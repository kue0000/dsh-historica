"""STAC 查询与资产签名。优先 pystac-client + planetary-computer，缺库时走纯 HTTP。"""
import json, time, urllib.error, urllib.parse, urllib.request

UA = {"User-Agent": "historica/0.1 (+research)"}


class StacError(RuntimeError):
    pass


_LAST_REQ = [0.0]


def _open(req, timeout=90, retries=5):
    """全局限速 + 对 429/5xx 做指数退避重试；其余错误直接抛出。"""
    for i in range(retries):
        gap = time.time() - _LAST_REQ[0]
        if gap < 1.0:
            time.sleep(1.0 - gap)
        _LAST_REQ[0] = time.time()
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < retries - 1:
                time.sleep(min(30, 2 ** i))
                continue
            raise


def _post_json(url, payload, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={**UA, "Content-Type": "application/json"})
    with _open(req, timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    with _open(req, timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def search_items(stac_url, collection, bbox, datetime_range, cloud_max, limit=200):
    """返回 pystac Item 列表；优先 pystac-client。"""
    try:
        from pystac_client import Client
        api = Client.open(stac_url)
        s = api.search(collections=[collection], bbox=list(bbox), datetime=datetime_range,
                       query={"eo:cloud_cover": {"lt": float(cloud_max)}}, max_items=limit)
        return list(s.items())
    except Exception:
        pass

    body = {
        "collections": [collection],
        "bbox": list(bbox),
        "datetime": datetime_range,
        "limit": min(limit, 200),
        "query": {"eo:cloud_cover": {"lt": float(cloud_max)}},
    }
    url = stac_url.rstrip("/") + "/search"
    data = _post_json(url, body)
    return [_DictItem(f) for f in data.get("features", [])]


class _DictItem:
    """纯 HTTP 返回的 GeoJSON feature 的轻量包装，接口与 pystac Item 对齐。"""

    def __init__(self, feat):
        self._f = feat
        self.id = feat.get("id")
        self.geometry = feat.get("geometry")
        props = feat.get("properties", {}) or {}
        self.properties = props
        self.assets = {k: _Asset(v) for k, v in (feat.get("assets") or {}).items()}
        dt = props.get("datetime") or ""
        self.datetime = _DT(dt)

    def __repr__(self):
        return f"<Item {self.id}>"


class _Asset:
    def __init__(self, a):
        self.href = a.get("href")
        self.extra_fields = a


class _DT:
    def __init__(self, s):
        self._s = s

    def strftime(self, fmt="%Y-%m-%d"):
        return self._s[:10] if fmt == "%Y-%m-%d" else self._s

    @property
    def year(self):
        return int(self._s[:4])

    def date(self):
        import datetime as _d
        return _d.date(int(self._s[:4]), int(self._s[5:7]), int(self._s[8:10]))


def sign_item(it):
    """Planetary Computer SAS 签名。优先 planetary_computer 库，否则调用公开签名接口。"""
    try:
        import planetary_computer as pc
        return pc.sign(it)
    except Exception:
        pass
    signed_assets = {}
    for name, asset in it.assets.items():
        href = getattr(asset, "href", None)
        if not href:
            continue
        signed_assets[name] = _sas_sign(href)
    if hasattr(it, "assets"):
        try:
            it.assets = signed_assets
            return it
        except Exception:
            pass
    f = getattr(it, "_f", None)
    if f is None:
        raise StacError("该 Item 无法签名（既无 planetary-computer 也无原始 feature）")
    f = dict(f)
    f["assets"] = {k: dict(v) for k, v in (f.get("assets") or {}).items()}
    for k, v in f["assets"].items():
        v["href"] = _sas_sign(v["href"])
    return _DictItem(f)


_TOKENS = {}
_ACCOUNT_COLL = {"landsateuwest": "landsat-c2-l2",
                 "sentinel2euwest": "sentinel-2-l2a",
                 "sentinel2l2a": "sentinel-2-l2a"}


def _token_for(href):
    """按存储账户取集合级 SAS token（一次请求覆盖整桶），避免逐文件签名触发 429。"""
    host = href.split("/")[2] if "//" in href else ""
    account = host.split(".")[0]
    coll = _ACCOUNT_COLL.get(account)
    if not coll:
        return None
    if coll not in _TOKENS:
        d = _get_json(f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{coll}", timeout=60)
        _TOKENS[coll] = d.get("token")
    return _TOKENS[coll]


def _sas_sign(href):
    if "?" in href and ("sig=" in href or "se=" in href):
        return href
    tok = _token_for(href)
    if tok:
        return href + "?" + tok
    url = "https://planetarycomputer.microsoft.com/api/sas/v1/sign?" + urllib.parse.urlencode({"href": href})
    data = _get_json(url, timeout=60)
    return data.get("href", href)


def asset_href(it, band):
    a = it.assets.get(band)
    if a is None:
        raise StacError(f"{it.id} 缺少资产 {band}")
    return getattr(a, "href", None) or a["href"]


# ---------------------------------------------------------------- 投影元数据
def proj_grid(it):
    """从 STAC 属性读取原生栅格网格：返回 dict(epsg, shape(H,W), transform(6), res)。"""
    p = it.properties
    epsg = p.get("proj:epsg")
    if not epsg:
        code = str(p.get("proj:code") or "")
        if code.upper().startswith("EPSG:"):
            try:
                epsg = int(code.split(":", 1)[1])
            except ValueError:
                epsg = None
    shape_ = p.get("proj:shape")
    tr = p.get("proj:transform")
    if not (epsg and shape_ and tr):
        raise StacError(f"{it.id} 缺少 proj:epsg/proj:code/proj:shape/proj:transform，需降级到 COG 头读取")
    if len(tr) == 9:
        a, b, c, d, e, f, _, _, _ = tr
    else:
        a, b, c, d, e, f = tr
    return {"epsg": epsg, "height": int(shape_[0]), "width": int(shape_[1]),
            "transform": [a, b, c, d, e, f], "res": (abs(a), abs(e))}


def window_pixels(it, target_geom, target_crs="EPSG:4326", target_res=None):
    """目标面在场景原生网格上的读取窗口像素数（含块对齐前）。"""
    from pyproj import Transformer
    from shapely.geometry import box as sbox
    g = proj_grid(it)
    a, b, c, d, e, f = g["transform"]
    # 场景覆盖范围（按 4 角，忽略旋转）
    xs = [c, c + a * g["width"] + b * g["height"]]
    ys = [f, f + d * g["width"] + e * g["height"]]
    xmin, xmax = sorted(xs)
    ymin, ymax = sorted(ys)

    tr = Transformer.from_crs(target_crs, f"EPSG:{g['epsg']}", always_xy=True)
    w, s, ea, n = target_geom.bounds
    pts = [(x, y) for x in (w, ea) for y in (s, n)]
    tx, ty = [], []
    for x, y in pts:
        xx, yy = tr.transform(x, y)
        tx.append(xx); ty.append(yy)
    txmin, txmax = min(tx), max(tx)
    tymin, tymax = min(ty), max(ty)
    ix0, ix1 = max(txmin, xmin), min(txmax, xmax)
    iy0, iy1 = max(tymin, ymin), min(tymax, ymax)
    if ix1 <= ix0 or iy1 <= iy0:
        return {"px": 0, "w": 0, "h": 0, "epsg": g["epsg"], "res": g["res"], "inside": False}
    px_res = target_res or min(g["res"])
    wpx = max(1, int(round((ix1 - ix0) / px_res)))
    hpx = max(1, int(round((iy1 - iy0) / px_res)))
    return {"px": wpx * hpx, "w": wpx, "h": hpx, "epsg": g["epsg"], "res": g["res"], "inside": True}
