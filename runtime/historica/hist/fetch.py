"""下载与拼接：窗口化 COG 读取（rasterio 优先，纯 HTTP 兜底）、断点续传、羽化拼接。"""
import hashlib, json, os, time

import numpy as np

from . import stacq
from .geo import _area_km2  # noqa: F401  (保持包内一致性)


class WatchdogPause(RuntimeError):
    """运行期吞吐看门狗触发：暂停任务，保留缓存，可续跑。"""


class Watchdog:
    """连续 N 个读取块的实际吞吐低于预估区间低端（p5）的 1/2 → 暂停。

    监控单位是「单波段窗口读取」；rasterio 内部按 256 px 块发起请求，因此等价于连续块级观察。
    预测值本身不决定任务是否继续，这里只看实测。
    """

    def __init__(self, cfg, lo_mbps, compression_ratio=0.5, log=print):
        wd = (cfg.get("fetch") or {}).get("watchdog") or {}
        self.cfg = wd
        self.comp = float(compression_ratio or 0.5)
        self.log = log
        self.consecutive = int(wd.get("consecutive_below", 3))
        self.min_mb = float(wd.get("min_sample_mb", 2.0))
        self.enabled = bool(wd.get("enabled", True)) and bool(lo_mbps)
        self.lo = float(lo_mbps) if lo_mbps else None
        self.threshold = (float(lo_mbps) * float(wd.get("below_fraction", 0.5))
                          if lo_mbps else None)
        self.streak = 0
        self.samples = []
        self.paused = False
        if self.enabled:
            self.log(f"  看门狗启用：阈值 {self.threshold:.3f} MB/s（区间低端 {lo_mbps:.3f} × "
                     f"{wd.get('below_fraction', 0.5)}），连续 {self.consecutive} 块触发")

    def note(self, raw_bytes, seconds, label=""):
        if not self.enabled or seconds <= 0.05:
            return
        mb = float(raw_bytes) / 1e6 * self.comp
        if mb < self.min_mb:
            return
        mbps = mb / float(seconds)
        self.samples.append({"label": label, "effective_mb": round(mb, 2),
                             "seconds": round(float(seconds), 2), "mbps": round(mbps, 3)})
        self.samples = self.samples[-40:]
        if mbps < self.threshold:
            self.streak += 1
            self.log(f"    看门狗：{label} 等效 {mbps:.3f} MB/s < 阈值 {self.threshold:.3f}"
                     f"（连续 {self.streak}/{self.consecutive}）")
            if self.streak >= self.consecutive:
                self.paused = True
                raise WatchdogPause(
                    f"连续 {self.streak} 个读取块的实际吞吐低于预估区间低端（p5 {self.lo:.3f} MB/s）"
                    f"的 1/2；最后一块 {label} 为 {mbps:.3f} MB/s。已暂停，缓存进度保留，可续跑。")
        else:
            self.streak = 0

    def summary(self):
        vals = [s["mbps"] for s in self.samples]
        return {"enabled": self.enabled, "threshold_mbps": None if self.threshold is None else round(self.threshold, 3),
                "consecutive_below": self.consecutive, "triggered": self.paused,
                "samples": len(vals),
                "min_mbps": round(min(vals), 3) if vals else None,
                "median_mbps": round(float(np.median(vals)), 3) if vals else None,
                "max_mbps": round(max(vals), 3) if vals else None,
                "throughput_samples_mbps": [round(v, 3) for v in vals],
                "last": self.samples[-5:]}


def _hash_key(*parts):
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]
    return h


def _cache_file(cache_dir, item_id, band, bounds, epsg, halo=0):
    d = os.path.join(cache_dir, "raw", item_id)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{band}_{_hash_key(*bounds, epsg, halo)}.npz")


def _native_bounds(epsg, wgs_bounds):
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    w, s, e, n = wgs_bounds
    xs, ys = [], []
    for x in (w, e):
        for y in (s, n):
            xx, yy = tr.transform(x, y)
            xs.append(xx); ys.append(yy)
    return [min(xs), min(ys), max(xs), max(ys)]


# ---------------------------------------------------------------- 单波段读取
def read_band(meta, band, href, grid, cfg, cache_dir, log=print, watchdog=None):
    """返回 (DN array uint16, x0, y0, res, epsg)，带本地缓存。"""
    epsg = meta.get("epsg")
    if epsg is None:
        raise RuntimeError(f"{meta['id']} 缺少 proj:epsg，无法定位窗口")
    from .geo import clip_bounds_to_footprint
    wb = clip_bounds_to_footprint(grid.get("bbox_wgs84"), meta.get("footprint"))
    if wb is None:
        raise RuntimeError(f"{meta['id']} 与目标范围无交集")
    nb = _native_bounds(epsg, wb)
    win_cfg = cfg.get("window", {})
    halo_px = int(win_cfg.get("block_px", 256)) * int(win_cfg.get("halo_blocks", 0))
    cf = _cache_file(cache_dir, meta["id"], band, nb, epsg, halo_px)
    if cfg["fetch"].get("resume", True) and os.path.exists(cf):
        z = np.load(cf)
        return z["arr"], float(z["x0"]), float(z["y0"]), float(z["res"]), int(z["epsg"])

    t0 = time.time()
    if _rasterio_ok():
        import rasterio
        from rasterio.windows import from_bounds, Window
        with rasterio.open(href) as r:
            w = from_bounds(*nb, transform=r.transform)
            if halo_px > 0:
                # 向外多取 halo 块：避免重采样在窗口边缘采到填充 0，形成色带/接缝
                w = Window(w.col_off - halo_px, w.row_off - halo_px,
                           w.width + 2 * halo_px, w.height + 2 * halo_px)
            # 只读与影像范围真正相交的部分，避免为 AOI 外的角分配/读取空块
            w = w.intersection(Window(0, 0, r.width, r.height))
            if w.width <= 0 or w.height <= 0:
                raise RuntimeError(f"{meta['id']} 与目标范围无交集")
            arr = r.read(1, window=w, boundless=True, fill_value=0)
            res = abs(r.transform.a)
            crs_epsg = r.crs.to_epsg() if r.crs else epsg
            x0 = r.transform.c + w.col_off * r.transform.a
            y0 = r.transform.f + w.row_off * r.transform.e
    else:
        from .coghttp import Cogtif
        ct = Cogtif(href)
        arr, x0, y0, res = ct.read_geo_gt(nb, target_res=None)
        crs_epsg = epsg
    dt = time.time() - t0
    np.savez_compressed(cf, arr=arr.astype(np.uint16), x0=x0, y0=y0, res=res, epsg=crs_epsg)
    log(f"    {band:<9} {arr.shape[1]}x{arr.shape[0]}  {dt:.1f}s  ({(arr>0).mean()*100:.0f}% 有值)")
    if watchdog is not None:
        # 先落盘再判定：即使触发暂停，这一块也已缓存，续跑不会重下
        watchdog.note(arr.nbytes, dt, f"{meta['id']}/{band}")
    return arr, x0, y0, res, crs_epsg


def _rasterio_ok():
    try:
        import rasterio  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 重采样到统一格网
def _reproject(arr, x0, y0, res, epsg, grid):
    if _rasterio_ok():
        import rasterio
        from rasterio.warp import reproject, Resampling
        dst = np.zeros((grid["nrows"], grid["ncols"]), dtype="float32")
        src = arr.astype("float32")
        reproject(source=src, destination=dst,
                  src_transform=rasterio.Affine(res, 0.0, x0, 0.0, -res, y0),
                  src_crs=f"EPSG:{epsg}",
                  dst_transform=rasterio.Affine(*grid["transform"]),
                  dst_crs=grid["crs"], resampling=Resampling.bilinear,
                  src_nodata=0, dst_nodata=0)
        return dst
    return _resample_numpy(arr, x0, y0, res, epsg, grid)


def _resample_numpy(arr, x0, y0, res, epsg, grid):
    """无 rasterio 时的双线性重采样（逐行，内存可控）。"""
    from pyproj import Transformer

    g = grid["transform"]
    tr = Transformer.from_crs(grid["crs"], f"EPSG:{epsg}", always_xy=True)
    out = np.zeros((grid["nrows"], grid["ncols"]), dtype="float32")
    src = arr.astype("float32")
    h, w = src.shape
    for j in range(grid["nrows"]):
        xs = g[2] + (np.arange(grid["ncols"]) + 0.5) * g[0]
        ys = np.full(grid["ncols"], g[5] + (j + 0.5) * g[4])
        xx, yy = tr.transform(xs, ys)
        fi = (xx - x0) / res - 0.5
        fj = (y0 - yy) / res - 0.5
        i0 = np.floor(fi).astype(int); j0 = np.floor(fj).astype(int)
        ok = (i0 >= 0) & (i0 < w - 1) & (j0 >= 0) & (j0 < h - 1)
        if not ok.any():
            continue
        di, dj = fi - i0, fj - j0
        out[j, ok] = (src[j0[ok], i0[ok]] * (1 - di[ok]) * (1 - dj[ok])
                      + src[j0[ok], i0[ok] + 1] * di[ok] * (1 - dj[ok])
                      + src[j0[ok] + 1, i0[ok]] * (1 - di[ok]) * dj[ok]
                      + src[j0[ok] + 1, i0[ok] + 1] * di[ok] * dj[ok])
    return out


# ---------------------------------------------------------------- 羽化
def _edge_weight(mask, max_dist):
    """到有效区边缘的曼哈顿距离，线性衰减成 0..1 权重（仅 mask 内非零）。"""
    if not mask.any():
        return mask.astype("float32")
    h, w = mask.shape
    left = np.zeros((h, w), dtype=np.int32)
    cnt = np.zeros(h, dtype=np.int32)
    for i in range(w):
        cnt = np.where(mask[:, i], cnt + 1, 0)
        left[:, i] = cnt
    right = np.zeros((h, w), dtype=np.int32)
    cnt = np.zeros(h, dtype=np.int32)
    for i in range(w - 1, -1, -1):
        cnt = np.where(mask[:, i], cnt + 1, 0)
        right[:, i] = cnt
    top = np.zeros((h, w), dtype=np.int32)
    cnt = np.zeros(w, dtype=np.int32)
    for j in range(h):
        cnt = np.where(mask[j], cnt + 1, 0)
        top[j] = cnt
    bot = np.zeros((h, w), dtype=np.int32)
    cnt = np.zeros(w, dtype=np.int32)
    for j in range(h - 1, -1, -1):
        cnt = np.where(mask[j], cnt + 1, 0)
        bot[j] = cnt
    d = np.minimum(np.minimum(left, right), np.minimum(top, bot))
    return (np.clip(d / max(1, max_dist), 0, 1) * mask).astype("float32")


# ---------------------------------------------------------------- 薄卷云筛查
def thin_cirrus_mask(refl, swrefl, valid, cfg):
    """像元同时满足「各波段亮度都高」「NDSI≈0」「QA 云位=0（已含在 valid 中）」→ 疑似薄卷云。

    refl: [red, green, blue] 反射率数组；swrefl: SWIR1 反射率；valid: 通过填充与 QA 云位检查的像元。
    阈值来自延平 2003 三片斑块统计，见 config/default.json → qc.thin_cirrus.source。
    """
    tc = (cfg.get("qc") or {}).get("thin_cirrus") or {}
    mn = np.minimum(np.minimum(refl[0], refl[1]), refl[2])
    denom = refl[1] + swrefl
    ndsi = np.where(denom > 0, (refl[1] - swrefl) / np.maximum(denom, 1e-6), 9.0)
    return (valid & (swrefl > 0) & (denom > 0)
            & (mn >= float(tc.get("brightness_min", 0.40)))
            & (np.abs(ndsi) <= float(tc.get("ndsi_abs_max", 0.25))))


# ---------------------------------------------------------------- 一期拼接
def build_epoch(epoch, grid, cfg, cache_dir, log=print, watchdog=None):
    prov = cfg["data"]["providers"][cfg["data"]["provider"]]
    scale = prov.get("scale", 2.75e-05)
    offset = prov.get("offset", -0.2)
    bands = prov["bands"]
    chosen = epoch["chosen"]
    tc = (cfg.get("qc") or {}).get("thin_cirrus") or {}
    t0 = time.time()
    acc = np.zeros((grid["nrows"], grid["ncols"], 3), dtype="float32")
    wsum = np.zeros((grid["nrows"], grid["ncols"]), dtype="float32")
    covered = np.zeros((grid["nrows"], grid["ncols"]), dtype="bool")
    cirrus_hit = np.zeros((grid["nrows"], grid["ncols"]), dtype="bool")
    cloud_frac = []
    cirrus_by_scene = []

    for m in chosen["scenes_meta"]:
        it = m
        refl, vals = [], []
        for b in bands[:3]:
            href = _href_for(it, b, cfg)
            arr, x0, y0, res, epsg = read_band(it, b, href, grid, cfg, cache_dir, log=log,
                                               watchdog=watchdog)
            r = _reproject(arr, x0, y0, res, epsg, grid)
            vals.append(r > 0)            # 有效性看原始 DN（0=填充）；不能用拉伸后的反射率判断
            refl.append(np.clip(r * scale + offset, 0, None))
        mask = vals[0]
        for v in vals[1:]:
            mask &= v
        # QA 云/阴影掩膜（Landsat C2: bit3=cloud, bit4=shadow；S2 SCL: 4/6=cloud/shadow）
        if bands[3] in ("qa_pixel", "SCL"):
            try:
                qhref = _href_for(it, bands[3], cfg)
                qa, qx, qy, qr, qeps = read_band(it, bands[3], qhref, grid, cfg, cache_dir, log=log,
                                                 watchdog=watchdog)
                qa = _reproject(qa, qx, qy, qr, qeps, grid).astype("uint16")
                if bands[3] == "qa_pixel":
                    # bit1 膨胀云 / bit2 卷云 / bit3 云 / bit4 云影：C2 官方 clear 掩膜口径
                    bad = ((qa & 0b11110) != 0).astype(bool)
                else:
                    bad = np.isin(qa, [4, 5, 6, 9])
                nd = bad & mask
                if nd.any():
                    cloud_frac.append(float(nd.sum()) / max(1, int(mask.sum())))
                    mask &= ~nd
            except Exception as exc:
                log(f"    QA 跳过: {exc}")

        # 薄卷云筛查（QA 之外的第四道）：各波段亮度都高 + NDSI≈0 + QA 云位=0
        if tc.get("enabled") and mask.any():
            try:
                asset = tc.get("swir_asset", "swir16")
                shref = _href_for(it, asset, cfg)
                sw, sx, sy, sr, seps = read_band(it, asset, shref, grid, cfg, cache_dir, log=log,
                                                  watchdog=watchdog)
                swr = _reproject(sw, sx, sy, sr, seps, grid)
                swrefl = np.clip(swr * scale + offset, 0, None)
                pre = int(mask.sum())
                cand = thin_cirrus_mask(refl, swrefl, mask, cfg)
                n_cand = int(cand.sum())
                if n_cand:
                    cirrus_hit |= cand
                    mask &= ~cand
                cirrus_by_scene.append({"id": it["id"], "candidate_px": n_cand,
                                        "candidate_frac": round(n_cand / max(1, pre), 4)})
                log(f"    薄卷云筛查：{n_cand:,} px 疑似（占该景 QA-clear {n_cand/max(1,pre)*100:.1f}%），已从可读扣除")
            except Exception as exc:
                log(f"    薄卷云筛查跳过（{tc.get('swir_asset', 'swir16')} 不可用）: {exc}")

        if mask.sum() == 0:
            log(f"    {it['id']} 在目标范围内无有效像元，跳过")
            continue
        wt = _edge_weight(mask, int(cfg["fetch"]["feather_km"] * 1000 / grid["res"]))
        wt = np.where(wt > 0, wt, 0)
        wsum += wt
        for k in range(3):
            acc[:, :, k] += refl[k] * wt
        covered |= mask

    if wsum.max() <= 0:
        raise RuntimeError(f"{epoch['target_year']} 拼接失败：无有效像元")
    out = acc / np.maximum(wsum, 1e-6)[:, :, None]
    out[~covered] = 0.0
    cirrus = cirrus_hit & ~covered      # 最终未被其它景覆盖、且因薄卷云被扣除的像元
    valid_frac = float(covered.mean())
    elapsed = time.time() - t0
    log(f"  {epoch['target_year']} 拼接完成：{np.sum(covered):,} px 有效（{valid_frac*100:.1f}%），"
        f"云/阴影剔除 {np.mean(cloud_frac)*100 if cloud_frac else 0:.1f}%，"
        f"薄卷云扣除 {cirrus.sum()*grid['res']**2/1e6:.1f} km²，{elapsed:.1f}s")
    return {"rgb": out.astype("float32"), "mask": covered, "valid_frac": valid_frac,
            "elapsed_s": elapsed, "cloud_removed": float(np.mean(cloud_frac)) if cloud_frac else 0.0,
            "cirrus": cirrus, "cirrus_frac": float(cirrus.mean()),
            "cirrus_by_scene": cirrus_by_scene}


def _href_for(meta, band, cfg):
    """优先用 plan 里存的 red_href；其余波段按 STAC 资产名现查并签名。"""
    if band == "red" and meta.get("red_href"):
        return stacq._sas_sign(meta["red_href"])
    it = _reload(meta["id"], cfg)
    return stacq._sas_sign(stacq.asset_href(it, band))


_ITEM_CACHE = {}


def _reload(item_id, cfg):
    if item_id in _ITEM_CACHE:
        return _ITEM_CACHE[item_id]
    coll = cfg["data"]["provider"]
    url = cfg["data"]["stac_url"].rstrip("/") + "/search"
    body = {"collections": [coll], "ids": [item_id], "limit": 1}
    from .stacq import _post_json
    feats = _post_json(url, body).get("features", [])
    if not feats:
        raise RuntimeError(f"STAC 回查失败: {item_id}")
    it = stacq._DictItem(feats[0])
    _ITEM_CACHE[item_id] = it
    return it
