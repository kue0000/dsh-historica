"""范围输入：行政区名称 / 经纬度 bbox / 矢量文件 / WKT，统一产出 WGS84 几何 + 输出格网。"""
import json, math, os, re, urllib.request, zipfile
from xml.etree import ElementTree as ET

from shapely.geometry import shape, box, MultiPolygon, Polygon
from shapely.ops import unary_union

UA = {"User-Agent": "historica/0.1 (+research)"}


class AOIError(ValueError):
    pass


# ---------------------------------------------------------------- bbox / wkt
def parse_bbox(text):
    """'113.94,22.45,114.29,22.64' -> shapely box；顺序 W,S,E,N。"""
    parts = [p.strip() for p in re.split(r"[,，\s]+", text.strip()) if p.strip()]
    if len(parts) != 4:
        raise AOIError("bbox 需要 4 个数：min_lon,min_lat,max_lon,max_lat")
    try:
        w, s, e, n = [float(p) for p in parts]
    except ValueError as exc:
        raise AOIError(f"bbox 含非数字: {text}") from exc
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90):
        raise AOIError("bbox 超出经纬度范围")
    if w >= e or s >= n:
        raise AOIError("bbox 需要 min<max（W<E, S<N）")
    return box(w, s, e, n)


def parse_wkt(text):
    from shapely import wkt
    try:
        g = wkt.loads(text)
    except Exception as exc:
        raise AOIError(f"WKT 解析失败: {exc}") from exc
    return _ensure_polygonal(g)


def _ensure_polygonal(g):
    if g.geom_type not in ("Polygon", "MultiPolygon"):
        raise AOIError(f"只接受面要素，收到 {g.geom_type}")
    if not g.is_valid:
        g = g.buffer(0)
    return g


def load_vector(path):
    """GeoJSON / KML / Shapefile(.shp|.zip)。"""
    low = path.lower()
    if low.endswith((".geojson", ".json")):
        obj = json.load(open(path, encoding="utf-8"))
        if obj.get("type") == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in obj["features"] if f.get("geometry")]
        elif obj.get("type") == "Feature":
            geoms = [shape(obj["geometry"])]
        else:
            geoms = [shape(obj)]
        return _ensure_polygonal(unary_union(geoms))
    if low.endswith(".kml"):
        return _load_kml(path)
    if low.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".shp")]
            if not names:
                raise AOIError("zip 中没有 .shp")
            tmp = os.path.join(os.path.dirname(path), "__shp_tmp__")
            os.makedirs(tmp, exist_ok=True)
            z.extractall(tmp)
            return _load_shp(os.path.join(tmp, names[0]))
    if low.endswith(".shp"):
        return _load_shp(path)
    raise AOIError(f"不支持的矢量格式: {path}")


def _load_shp(path):
    try:
        import shapefile  # pyshp
    except ImportError as exc:
        raise AOIError("缺少 pyshp，无法读 Shapefile；执行 `uv pip install pyshp` 或先转成 GeoJSON") from exc
    from shapely.geometry import Polygon as SPoly
    try:
        r = shapefile.Reader(path)
    except Exception as exc:
        raise AOIError(f"无法读取 Shapefile: {path}（{exc}）") from exc
    polys = []
    for sh in r.shapes():
        pts = sh.points
        parts = list(sh.parts) + [len(pts)]
        rings = [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
        polys.append(SPoly(rings[0], rings[1:]))
    return _ensure_polygonal(unary_union(polys))


def _load_kml(path):
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.parse(path).getroot()
    polys = []
    for poly in root.iter("{http://www.opengis.net/kml/2.2}Polygon"):
        outer = poly.find(".//k:outerBoundaryIs/k:LinearRing/k:coordinates", ns)
        if outer is None or not outer.text:
            continue
        pts = []
        for tok in outer.text.replace("\n", " ").split():
            lon, lat, *_ = tok.split(",")
            pts.append((float(lon), float(lat)))
        if len(pts) >= 4:
            polys.append(Polygon(pts))
    if not polys:
        raise AOIError("KML 中未找到面要素")
    return _ensure_polygonal(unary_union(polys))


# ---------------------------------------------------------------- GCJ-02 -> WGS84
def gcj02_to_wgs84(lon, lat):
    a = 6378245.0
    ee = 0.00669342162296594323

    def transform_lat(x, y):
        ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
        return ret

    def transform_lon(x, y):
        ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
        return ret

    dlat = transform_lat(lon - 105.0, lat - 35.0)
    dlon = transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    return lon * 2 - (lon + dlon), lat * 2 - (lat + dlat)


def _reproject_gcj(geom):
    from shapely.ops import transform as shp_t
    return _ensure_polygonal(shp_t(lambda x, y, z=None: gcj02_to_wgs84(x, y), geom))


# ---------------------------------------------------------------- admin lookup
def _fetch_json(url, cache_dir=None):
    cached = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        key = re.sub(r"[^0-9A-Za-z._-]", "_", url.split("/")[-1])
        cached = os.path.join(cache_dir, key)
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            return json.load(open(cached, encoding="utf-8"))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    if cached:
        json.dump(data, open(cached, "w", encoding="utf-8"), ensure_ascii=False)
    return data


def _name_tokens(name):
    name = name.strip()
    prov = re.match(r"^(.+?(?:省|自治区|特别行政区|市))", name)
    toks = []
    rest = name
    if prov:
        toks.append(prov.group(1))
        rest = name[len(prov.group(1)):]
    m = re.match(r"^(.+?(?:市|自治州|地区|盟))", rest)
    if m:
        toks.append(m.group(1))
        rest = rest[len(m.group(1)):]
    if rest:
        toks.append(rest)
    return toks


def admin_lookup(name, cfg):
    """返回 dict(geometry, source, precision, area_km2, note)。DataV 优先，GADM 兜底。"""
    cache = os.path.join(cfg.get("_cache_dir", "cache"), "boundary")
    toks = _name_tokens(name)
    if not toks:
        raise AOIError("行政区名称解析失败")
    errors = []

    if "datav" in cfg["boundary"]["sources"]:
        try:
            return _admin_datav(toks, cfg, cache)
        except Exception as exc:  # 兜底到 GADM
            errors.append(f"DataV: {exc}")

    if "gadm" in cfg["boundary"]["sources"]:
        try:
            return _admin_gadm(toks, cfg, cache)
        except Exception as exc:
            errors.append(f"GADM: {exc}")

    raise AOIError("行政区边界获取失败：" + " | ".join(errors))


def _admin_datav(toks, cfg, cache):
    """用 DataV 全量索引 all.json 解析「省 > 市 > 区县」，再取单面边界。"""
    idx = _fetch_json(cfg["boundary"]["datav_all"], cache)
    entries = idx if isinstance(idx, list) else idx.get("features", [])
    norm = lambda s: re.sub(r"(省|市|自治区|自治州|地区|盟|特别行政区|县|区)$", "", str(s))

    prev = None
    used = []
    for tok in toks:
        cands = [e for e in entries if str(e.get("name", "")) == tok]
        if not cands:
            cands = [e for e in entries if norm(e.get("name")) == norm(tok)]
        if prev is not None:
            cands = [e for e in cands if e.get("parent") == prev["adcode"]]
        if not cands:
            raise AOIError(f"DataV 索引中未找到 {tok}" + (f"（{used[-1]} 下）" if used else ""))
        # 多个同名时取层级最细的
        cands.sort(key=lambda e: {"province": 1, "city": 2, "district": 3}.get(e.get("level"), 4), reverse=True)
        prev = cands[0]
        used.append(prev["name"])
    node = prev
    data = _fetch_json(cfg["boundary"]["datav_bound_single"].format(adcode=node["adcode"]), cache)
    feats = data.get("features", [])
    if not feats:
        raise AOIError(f"DataV 边界为空: {node['adcode']}")
    geom = unary_union([shape(f["geometry"]) for f in feats])
    geom = _reproject_gcj(_ensure_polygonal(geom))
    return {
        "geometry": geom,
        "source": "DataV.GeoAtlas",
        "source_detail": f"阿里云 DataV areas_v3 all.json 索引 + {node['adcode']}.json（GCJ-02→WGS84）",
        "precision": "GCJ-02→WGS84 反算残余约 1–2 m；边界本身为示意，非测绘成果【推断】",
        "area_km2": _area_km2(geom),
        "name_matched": " > ".join(used),
        "note": "DataV 为高德系示意边界，与 GADM 叠合率通常 95–97%（坐标系偏移为主）",
    }


def _admin_gadm(toks, cfg, cache):
    name = toks[-1]
    best = None
    url = None
    for lv in (3, 2, 1):
        url = cfg["boundary"]["gadm_base"] + f"gadm41_CHN_{lv}.json"
        try:
            data = _fetch_json(url, cache)
        except Exception:
            continue
        for f in data["features"]:
            p = f["properties"]
            cand = {str(p.get(f"NL_NAME_{lv}", "")), str(p.get(f"NAME_{lv}", ""))}
            if name in cand or any(name.endswith(c) for c in cand if c not in ("", "NA")):
                best = f
                break
        if best:
            break
    if not best:
        raise AOIError(f"GADM 未找到 {name}（GADM 4.1 区县建制可能滞后，如延平区被标为 Nanping）")
    geom = _ensure_polygonal(shape(best["geometry"]))
    return {
        "geometry": geom,
        "source": "GADM 4.1",
        "source_detail": url,
        "precision": "GADM 未公布量化精度，通常百米级；不得作为权属/测绘依据【推断】",
        "area_km2": _area_km2(geom),
        "name_matched": name,
        "note": "GADM 中文名可能缺失（NA），已按英文名/后缀匹配",
    }


def _area_km2(geom):
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    return sum(abs(geod.geometry_area_perimeter(p)[0]) / 1e6 for p in polys)


# ---------------------------------------------------------------- output grid
def utm_crs_for(geom):
    c = geom.centroid
    zone = int((c.x + 180.0) / 6.0) + 1
    return f"EPSG:{32600 + zone if c.y >= 0 else 32700 + zone}"


def build_grid(geom, cfg):
    """把 WGS84 几何转成输出格网参数（原点对齐 res 的整数倍）。"""
    from pyproj import Transformer
    from rasterio.warp import transform_bounds

    res = float(cfg["grid"]["res"])
    crs = cfg["grid"]["crs"]
    if crs in (None, "", "auto-utm"):
        crs = utm_crs_for(geom)
    w, s, e, n = geom.bounds
    b = transform_bounds("EPSG:4326", crs, w, s, e, n, densify_pts=21)
    pad = float(cfg["grid"].get("pad_m", 0.0))
    x0 = math.floor((b[0] - pad) / res) * res
    y0 = math.floor((b[1] - pad) / res) * res
    x1 = math.ceil((b[2] + pad) / res) * res
    y1 = math.ceil((b[3] + pad) / res) * res
    ncols = int(round((x1 - x0) / res))
    nrows = int(round((y1 - y0) / res))
    return {
        "crs": crs,
        "res": res,
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "ncols": ncols, "nrows": nrows,
        "transform": [res, 0.0, x0, 0.0, -res, y1],
        "bbox_wgs84": [w, s, e, n],
        "area_km2": (x1 - x0) * (y1 - y0) / 1e6,
    }


def resolve_aoi(args, cfg):
    """CLI 四种范围输入的统一入口。"""
    provided = [bool(args.admin), bool(args.bbox), bool(args.vector), bool(args.wkt)]
    if sum(provided) != 1:
        raise AOIError("范围参数必须且只能给一种：--admin / --bbox / --vector / --wkt")
    if args.admin:
        info = admin_lookup(args.admin, cfg)
    elif args.bbox:
        info = {"geometry": parse_bbox(args.bbox), "source": "bbox", "source_detail": "用户输入",
                "precision": "用户给定，无额外误差", "area_km2": _area_km2(parse_bbox(args.bbox)),
                "name_matched": "", "note": ""}
    elif args.vector:
        g = load_vector(args.vector)
        info = {"geometry": g, "source": "矢量文件", "source_detail": args.vector,
                "precision": "取决于源文件坐标系；插件假定为 WGS84", "area_km2": _area_km2(g),
                "name_matched": "", "note": ""}
    else:
        g = parse_wkt(args.wkt)
        info = {"geometry": g, "source": "WKT", "source_detail": "用户输入",
                "precision": "用户给定", "area_km2": _area_km2(g), "name_matched": "", "note": ""}
    info["grid"] = build_grid(info["geometry"], cfg)
    info["bounds"] = list(info["geometry"].bounds)
    return info


def coverage_frac(target_geom, footprints):
    """footprint 并集与目标面的相交比例（按 deg² 计，纬度 30° 内足够）。"""
    if not footprints:
        return 0.0
    u = unary_union(footprints)
    inter = target_geom.intersection(u)
    return 0.0 if inter.is_empty else inter.area / target_geom.area


def clip_bounds_to_footprint(bounds, footprint_geojson):
    """把目标 bbox 收窄到「场景足迹 ∩ 目标」的 bbox，减少读取窗口外的空块。

    只影响读取窗口大小，不改变输出格网；估算与实际下载共用这一口径。
    """
    if not footprint_geojson:
        return list(bounds)
    try:
        from shapely.geometry import box as sbox
        g = shape(footprint_geojson)
        inter = g.intersection(sbox(*bounds))
        if inter.is_empty:
            return None
        return list(inter.bounds)
    except Exception:
        return list(bounds)
