"""预估：先真实测吞吐（并发 Range GET），再按窗口像素 × 波段 × 2 字节外推。

吞吐口径（2026-11 起）：
- 并发探针与 `fetch.workers` 同数，每轮 N 路同时发 Range GET，按「整轮字节 / 整轮墙钟」聚合；
  多轮取中位。
- 单流探针保留，但只作 diagnostic 字段，不进估算。
- 样本 = 本轮并发轮次 + 最近 N 次校准净吞吐 + 这些运行在下载期看门狗记录的分块吞吐；
  点估计取中位，区间取 p5–p95（v1.1 起）。
- 预估只用于「是否提醒/是否分批」，不再决定任务是否继续；停止由运行期看门狗负责。
"""
import json, math, statistics, time, urllib.request

from pyproj import Transformer

from . import stacq

UA = {"User-Agent": "historica/0.1 (+research)"}


def probe_speed(url, nbytes=8388608, timeout=180):
    """单流 Range GET，返回实测 MB/s 与采样细节（仅作诊断字段）。"""
    nbytes = int(nbytes)
    t0 = time.time()
    req = urllib.request.Request(url, headers={**UA, "Range": f"bytes=0-{nbytes - 1}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        total = r.headers.get("Content-Range")
    dt = time.time() - t0
    speed = (len(data) / 1e6) / dt if dt > 0 else 0.0
    return {"bytes": len(data), "seconds": round(dt, 2), "mbps": round(speed, 3),
            "content_range": total, "url_host": url.split("/")[2] if "//" in url else ""}


def probe_speed_repeat(url, nbytes=8388608, samples=5, timeout=180, agg="median"):
    """同一 URL 连做 N 次单流 Range GET，取中位数（诊断用，不进估算）。"""
    rs = []
    for _ in range(max(1, int(samples))):
        try:
            rs.append(probe_speed(url, nbytes, timeout))
        except Exception as exc:
            rs.append({"mbps": None, "error": str(exc)})
    vals = sorted(r["mbps"] for r in rs if r.get("mbps"))
    if not vals:
        return {"samples": rs, "median_mbps": None, "agg": agg, "diagnostic": True}
    med = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    return {"samples": rs, "median_mbps": round(med, 3), "min_mbps": round(vals[0], 3),
            "max_mbps": round(vals[-1], 3), "agg": agg, "n": len(vals), "diagnostic": True}


def _range_get(url, start, nbytes, timeout):
    req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={int(start)}-{int(start) + int(nbytes) - 1}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        cr = r.headers.get("Content-Range")
    return {"start": int(start), "bytes": len(data), "seconds": round(time.time() - t0, 3),
            "content_range": cr}


def probe_speed_concurrent(url, nbytes=8388608, workers=4, rounds=5, timeout=180, log=None):
    """与下载同数的并发 Range GET：每轮 workers 路同时发，按整轮聚合吞吐计；多轮取中位。

    这是进入估算的探针。单流探针另算，只作诊断。
    """
    from concurrent.futures import ThreadPoolExecutor
    workers = max(1, int(workers))
    rounds = max(1, int(rounds))
    nbytes = max(1, int(nbytes))
    size = None
    try:
        size = _asset_size(url)
    except Exception:
        size = None
    if size and size < workers * nbytes:
        per = max(1, size // workers)
        starts = [i * per for i in range(workers)]
        sizes = [min(per, size - s) for s in starts]
    else:
        starts = [i * nbytes for i in range(workers)]
        sizes = [nbytes] * workers

    rs = []
    for k in range(rounds):
        t0 = time.time()
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(_range_get, url, s, n, timeout) for s, n in zip(starts, sizes)]
                got = [f.result() for f in futs]
            dt = time.time() - t0
            total = sum(g["bytes"] for g in got)
            if log:
                log(f"  探针 {k + 1}/{rounds} 轮：{total/1e6:.1f} MB / {dt:.1f}s = "
                    f"{(total / 1e6) / dt if dt > 0 else 0:.3f} MB/s（{workers} 路）")
            rs.append({"round": k + 1, "workers": workers, "bytes": total,
                       "seconds": round(dt, 2), "mbps": round((total / 1e6) / dt, 3) if dt > 0 else 0.0,
                       "streams": [{"start": g["start"], "mbps": round((g["bytes"] / 1e6) / max(g["seconds"], 1e-9), 3)}
                                   for g in got]})
        except Exception as exc:
            rs.append({"round": k + 1, "workers": workers, "mbps": None, "error": str(exc)})
    vals = sorted(r["mbps"] for r in rs if r.get("mbps"))
    if not vals:
        return {"rounds": rs, "median_mbps": None, "workers": workers, "n": 0, "concurrent": True}
    med = statistics.median(vals)
    return {"rounds": rs, "median_mbps": round(med, 3), "min_mbps": round(vals[0], 3),
            "max_mbps": round(vals[-1], 3), "workers": workers, "n": len(vals),
            "round_bytes": int(sum(sizes)), "concurrent": True}


def _pct(vals, p):
    """线性插值分位数（不引入 numpy 依赖）。"""
    v = sorted(float(x) for x in vals)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    k = (len(v) - 1) * float(p) / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return v[lo]
    return v[lo] + (v[hi] - v[lo]) * (k - lo)



def _align(n, block=256):
    return int(math.ceil(n / block) * block)


def _scene_window(meta, target_bounds, target_res):
    """目标 bbox（已按场景足迹收窄）在某场景原生网格上的读取窗口（含块对齐）。"""
    if not meta.get("epsg") or not meta.get("transform"):
        return None
    from .geo import clip_bounds_to_footprint
    wb = clip_bounds_to_footprint(target_bounds, meta.get("footprint"))
    if wb is None:
        return {"w": 0, "h": 0, "w_aligned": 0, "h_aligned": 0, "px": 0, "inside": False}
    a, b, c, d, e, f = meta["transform"]
    W, H = int(meta["width"]), int(meta["height"])
    xs = sorted([c, c + a * W + b * H])
    ys = sorted([f, f + d * W + e * H])
    xmin, xmax, ymin, ymax = xs[0], xs[1], ys[0], ys[1]

    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{meta['epsg']}", always_xy=True)
    w, s, ea, n = wb
    pts = [tr.transform(x, y) for x in (w, ea) for y in (s, n)]
    txmin, txmax = min(p[0] for p in pts), max(p[0] for p in pts)
    tymin, tymax = min(p[1] for p in pts), max(p[1] for p in pts)
    ix0, ix1 = max(txmin, xmin), min(txmax, xmax)
    iy0, iy1 = max(tymin, ymin), min(tymax, ymax)
    if ix1 <= ix0 or iy1 <= iy0:
        return {"w": 0, "h": 0, "w_aligned": 0, "h_aligned": 0, "px": 0, "inside": False}
    wpx = max(1, int(round((ix1 - ix0) / target_res)))
    hpx = max(1, int(round((iy1 - iy0) / target_res)))
    wa, ha = _align(wpx), _align(hpx)
    return {"w": wpx, "h": hpx, "w_aligned": wa, "h_aligned": ha,
            "px": wa * ha, "inside": True}


def _scene_px(meta, target_geom, grid, res):
    """按「目标 bbox 在场景原生 CRS 上的读取窗口」估算像素，与 read_band 的窗口口径一致。

    注意：读的是矩形窗口（含 AOI 外的角），因此不能按面要素面积折算，否则会低估计费量。
    """
    w = _scene_window(meta, list(target_geom.bounds), res)
    return (w or {}).get("px", 0)


def _asset_size(href):
    """用 Content-Range 取 COG 全文件字节数（不下载内容）。"""
    req = urllib.request.Request(href, headers={**UA, "Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        cr = r.headers.get("Content-Range") or ""
    if "/" not in cr:
        return None
    try:
        return int(cr.rsplit("/", 1)[1])
    except ValueError:
        return None


def _compression_ratio(plan):
    """实测 DEFLATE 压缩比：优先 file:size，缺失时用 Content-Range 全文件字节数。"""
    samples = []
    for ep in plan["epochs"]:
        for m in (ep.get("chosen") or {}).get("scenes_meta", []):
            if not (m.get("width") and m.get("height")):
                continue
            raw = m["width"] * m["height"] * 2
            sz = m.get("file_size")
            if not sz and m.get("red_href"):
                try:
                    sz = _asset_size(stacq._sas_sign(m["red_href"]))
                except Exception:
                    sz = None
            if sz:
                samples.append(max(0.2, min(1.0, sz / raw)))
            if len(samples) >= 2:
                break
        if len(samples) >= 2:
            break
    return sum(samples) / len(samples) if samples else None


def estimate_plan(plan, cfg, speed=None, do_probe=True, log=print):
    est = cfg["estimate"]
    bands = cfg["data"]["providers"][cfg["data"]["provider"]]["bands"]
    nbands = len(bands)
    qc = (cfg.get("qc") or {}).get("thin_cirrus") or {}
    n_screen = 1 if qc.get("enabled") else 0
    n_read = nbands + n_screen
    res = float(cfg["grid"]["res"])
    target_bounds = plan["aoi"]["bounds"]
    from shapely.geometry import shape as shp_shape
    target_geom = shp_shape(plan["aoi"]["boundary_geojson"])
    grid = plan["aoi"]["grid"]
    comp = _compression_ratio(plan) or float(est.get("compression_fallback", 0.6))

    lo_p, hi_p = (est.get("interval_percentiles") or [5, 95])[:2]
    min_n = int(est.get("min_samples_for_percentile", 4))
    probe = None
    diag = None
    probe_vals, cal_vals, block_vals = [], [], []
    probe_med = cal_med = block_med = None
    p10 = p90 = None
    interval_source = "配置 time_range 系数（样本不足）"
    speed_source = "用户指定"
    samples = []
    if speed is None:
        if do_probe:
            href = None
            for ep in plan["epochs"]:
                for m in (ep.get("chosen") or {}).get("scenes_meta", []):
                    if m.get("red_href"):
                        href = stacq._sas_sign(m["red_href"])
                        break
                if href:
                    break
            if href:
                workers = int(est.get("probe_workers") or cfg["fetch"]["workers"])
                try:
                    probe = probe_speed_concurrent(
                        href, est["probe_bytes"], workers,
                        int(est.get("probe_rounds", 5)), est["probe_timeout_s"], log=log)
                    probe_med = probe.get("median_mbps")
                    probe_vals = [r["mbps"] for r in probe.get("rounds", []) if r.get("mbps")]
                    log(f"并发探针（{workers} 路 × {len(probe_vals)} 轮，每轮 "
                        f"{probe.get('round_bytes', 0)/1e6:.0f} MB）：聚合吞吐中位 {probe_med} MB/s"
                        f"（{probe.get('min_mbps')}–{probe.get('max_mbps')}）")
                except Exception as exc:
                    log(f"并发测速失败: {exc}")
                try:
                    diag = probe_speed_repeat(
                        href, est["probe_bytes"], int(est.get("probe_diagnostic_samples", 1)),
                        est["probe_timeout_s"])
                    log(f"单流探针（诊断，不进估算）：{diag.get('median_mbps')} MB/s")
                except Exception as exc:
                    log(f"单流诊断测速失败: {exc}")
        from . import calib
        cal_vals = calib.recent_speeds(cfg)
        block_vals = calib.recent_block_speeds(cfg)
        cal_med = statistics.median(cal_vals) if cal_vals else None
        block_med = statistics.median(block_vals) if block_vals else None
        samples = [v for v in (probe_vals + cal_vals + block_vals) if v]
        if samples:
            speed = statistics.median(samples)
            speed_source = (f"本轮并发探针 {len(probe_vals)} 轮 + 最近 {len(cal_vals)} 次校准净吞吐"
                            f" + 下载期看门狗分块吞吐 {len(block_vals)} 个（等权中位）")
            if block_vals:
                log(f"下载期看门狗样本并入：{len(block_vals)} 个，中位 {block_med} MB/s")
            p10, p90 = _pct(samples, lo_p), _pct(samples, hi_p)
            if len(samples) >= min_n and p10 and p90:
                interval_source = f"样本 p{lo_p}–p{hi_p}（{len(samples)} 个样本）"
            else:
                p10 = p90 = None
            log(f"采用吞吐 {speed:.3f} MB/s（来源：{speed_source}）；"
                f"区间 {p10}–{p90} MB/s（{interval_source}）")
        else:
            speed = float(est["fallback_speed_mbps"])
            speed_source = "兜底常数（无测速/无校准）"
            log(f"使用兜底吞吐 {speed:.3f} MB/s")

    per_epoch = []
    total = {"scenes": 0, "px": 0, "mb": 0.0, "transfer_mb": 0.0, "net_s": 0.0, "proc_s": 0.0, "opens": 0}
    for ep in plan["epochs"]:
        chosen = ep.get("chosen")
        if not chosen:
            per_epoch.append({"target_year": ep["target_year"], "status": ep["status"], "scenes": 0})
            continue
        windows = []
        for m in chosen["scenes_meta"]:
            px = _scene_px(m, target_geom, grid, res)
            windows.append({"id": m["id"], "px": px, "inside": px > 0})
        px = sum(x["px"] for x in windows)
        if px == 0:
            px = int(grid["ncols"] * grid["nrows"] * 1.4)
            unknown = True
        else:
            unknown = False
        raw_mb = px * n_read * 2 * est["block_overhead"] / 1e6
        transfer_mb = raw_mb * comp
        net_s = transfer_mb / max(speed, 1e-9)
        opens = len(windows) * n_read
        open_s = opens * est["open_latency_s"]
        proc_s = (px * 3 / 1e6) * est["processing_s_per_mpx"]
        fixed_s = open_s + proc_s
        if p10 and p90:
            lo_s = transfer_mb / p90 * 1.0 + fixed_s
            hi_s = transfer_mb / p10 * 1.0 + fixed_s
        else:
            lo, hi = est["time_range"]
            lo_s, hi_s = (net_s + fixed_s) * lo, (net_s + fixed_s) * hi
        row = {
            "target_year": ep["target_year"],
            "status": ep["status"],
            "date": chosen["date"],
            "coverage": round(chosen["coverage"] * 100, 2),
            "scenes": len(windows),
            "scene_ids": [m["id"] for m in chosen["scenes_meta"]],
            "windows": windows,
            "window_px": px,
            "payload_mb": round(raw_mb, 1),
            "transfer_mb": round(transfer_mb, 1),
            "net_s": round(net_s, 1),
            "open_s": round(open_s, 1),
            "proc_s": round(proc_s, 1),
            "total_s": round(net_s + fixed_s, 1),
            "total_lo_s": round(lo_s, 1),
            "total_hi_s": round(hi_s, 1),
            "proj_meta": "estimated" if unknown else "exact",
        }
        per_epoch.append(row)
        total["scenes"] += len(windows)
        total["px"] += px
        total["mb"] += raw_mb
        total["transfer_mb"] += transfer_mb
        total["net_s"] += net_s
        total["proc_s"] += proc_s
        total["opens"] += opens

    fixed_total = total["proc_s"] + total["opens"] * est["open_latency_s"]
    base = total["net_s"] + fixed_total
    if p10 and p90:
        total_lo = total["transfer_mb"] / p90 + fixed_total
        total_hi = total["transfer_mb"] / p10 + fixed_total
    else:
        lo, hi = est["time_range"]
        total_lo, total_hi = base * lo, base * hi
    sens = []
    for s in est.get("sensitivity_speeds_mbps", [0.05, 0.1, 0.2, 0.5, 1.0]):
        sens.append({"speed_mbps": s,
                     "total_min": round((total["transfer_mb"] / max(s, 1e-9) + fixed_total) / 60.0, 1)})
    out = {
        "measured": probe,
        "single_stream_diagnostic": diag,
        "speed_mbps": round(speed, 3),
        "speed_p10_mbps": None if p10 is None else round(p10, 3),
        "speed_p90_mbps": None if p90 is None else round(p90, 3),
        "speed_lo_mbps": None if p10 is None else round(p10, 3),
        "speed_hi_mbps": None if p90 is None else round(p90, 3),
        "interval_percentiles": [lo_p, hi_p],
        "speed_samples": [round(float(v), 3) for v in samples],
        "speed_basis": {"source": speed_source, "probe_median_mbps": probe_med,
                        "probe_rounds": len(probe_vals),
                        "probe_workers": (probe or {}).get("workers"),
                        "single_stream_diagnostic_mbps": (diag or {}).get("median_mbps"),
                        "calibration_median_mbps": cal_med, "calibration_n": len(cal_vals),
                        "block_samples_n": len(block_vals), "block_median_mbps": block_med,
                        "interval_percentiles": [lo_p, hi_p],
                        "p10_mbps": None if p10 is None else round(p10, 3),
                        "p90_mbps": None if p90 is None else round(p90, 3),
                        "interval_source": interval_source},
        "assumptions": {
            "n_bands": nbands, "n_screen_bands": n_screen, "n_read_bands": n_read,
            "bytes_per_sample": 2,
            "block_overhead": est["block_overhead"],
            "compression_ratio": round(comp, 3),
            "compression_source": "实测（COG 资产体积/未压缩体积）" if _compression_ratio(plan) else "配置兜底",
            "open_latency_s": est["open_latency_s"],
            "processing_s_per_mpx": est["processing_s_per_mpx"],
            "speed_source": speed_source,
            "probe_rounds": int(est.get("probe_rounds", 1)),
            "probe_workers": int(est.get("probe_workers") or cfg["fetch"]["workers"]),
            "interval_source": interval_source,
            "interval_percentiles": [lo_p, hi_p],
            "time_range": list(est["time_range"]),
        },
        "per_epoch": per_epoch,
        "total": {
            "scenes": total["scenes"], "window_px": total["px"],
            "payload_mb": round(total["mb"], 1),
            "transfer_mb": round(total["transfer_mb"], 1),
            "net_s": round(total["net_s"], 1), "proc_s": round(total["proc_s"], 1),
            "total_s": round(base, 1),
            "total_lo_s": round(total_lo, 1), "total_hi_s": round(total_hi, 1),
        },
        "sensitivity": sens,
        "advisory": {
            "purpose": "仅用于提醒用户与是否分批；不决定任务是否继续",
            "over_threshold_min": float((cfg.get("gates") or {}).get("ask_if_estimate_over_min", 10)),
            "suggest_split": bool(base / 60.0 > float((cfg.get("gates") or {}).get("ask_if_estimate_over_min", 10))),
            "watchdog": (cfg.get("fetch") or {}).get("watchdog") or {},
        },
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return out


def fmt_table(est):
    lines = []
    head = (f"{'年份':<6}{'日期':<24}{'覆盖%':>7}{'景数':>5}{'窗口px':>12}{'原始MB':>8}"
            f"{'传输MB':>8}{'网络s':>7}{'处理s':>7}{'合计s':>14}")
    lines.append(head)
    lines.append("-" * 108)
    for r in est["per_epoch"]:
        if not r.get("scenes"):
            lines.append(f"{r['target_year']:<6}{'—':<24}{'—':>7}{0:>5}{'—':>12}{'—':>8}{'—':>8}{'—':>7}{'—':>7}{'无数据':>14}")
            continue
        lines.append(f"{r['target_year']:<6}{r['date']:<24}{r['coverage']:>6.1f} {r['scenes']:>4}"
                     f"{r['window_px']:>12,}{r['payload_mb']:>8.1f}{r['transfer_mb']:>8.1f}"
                     f"{r['net_s']:>7.0f}{r['proc_s']:>7.0f}{r['total_lo_s']:>7.0f}–{r['total_hi_s']:<6.0f}")
    t = est["total"]
    lines.append("-" * 108)
    lines.append(f"{'合计':<6}{'':<24}{'':>7}{t['scenes']:>5}{t['window_px']:>12,}{t['payload_mb']:>8.1f}"
                 f"{t['transfer_mb']:>8.1f}{t['net_s']:>7.0f}{t['proc_s']:>7.0f}"
                 f"{t['total_lo_s']:>7.0f}–{t['total_hi_s']:<6.0f}")
    sb = est.get("speed_basis") or {}
    lines.append(f"\n吞吐点估计 {est['speed_mbps']:.3f} MB/s（来源：{sb.get('source', '—')}；"
                 f"并发探针中位 {sb.get('probe_median_mbps')}（{sb.get('probe_rounds', 0)} 轮 / "
                 f"{sb.get('probe_workers')} 路），单流诊断 {sb.get('single_stream_diagnostic_mbps')}，"
                 f"校准中位 {sb.get('calibration_median_mbps')}（{sb.get('calibration_n', 0)} 次），"
                 f"看门狗分块中位 {sb.get('block_median_mbps')}（{sb.get('block_samples_n', 0)} 个））")
    p10, p90 = est.get("speed_p10_mbps"), est.get("speed_p90_mbps")
    lines.append(f"速度区间 {('—' if p10 is None else f'{p10:.3f}')}–"
                 f"{('—' if p90 is None else f'{p90:.3f}')} MB/s（{sb.get('interval_source', '—')}）；"
                 f"样本 {est.get('speed_samples')}")
    a = est["assumptions"]
    lines.append(f"体积口径：窗口px × {a['n_read_bands']} 波段"
                 f"（{a['n_bands']} 光谱 + {a['n_screen_bands']} 薄卷云筛查）× 2 字节 × 块开销 "
                 f"{a['block_overhead']}；传输量按实测压缩比 {a['compression_ratio']:.2f}（{a['compression_source']}）")
    lines.append(f"耗时 = 网络 {t['net_s']:.0f}s + 打开/元数据 {t['total_s']-t['net_s']-t['proc_s']:.0f}s + "
                 f"处理 {t['proc_s']:.0f}s")
    lo_p, hi_p = (est.get("interval_percentiles") or [5, 95])[:2]
    basis_txt = (f"区间由样本 p{lo_p}–p{hi_p} 换算，不取点估计的固定倍数"
                 if est.get("speed_p10_mbps") is not None
                 else "样本不足，区间回退到配置 time_range 系数")
    lines.append(f"点估计 {t['total_s']/60:.1f} min；区间 {t['total_lo_s']/60:.1f}–{t['total_hi_s']/60:.1f} min"
                 f"（{basis_txt}）")
    adv = est.get("advisory") or {}
    lines.append(f"用途：预测仅用于提醒/是否分批（点估计{'超过' if adv.get('suggest_split') else '未超过'} "
                 f"{adv.get('over_threshold_min', 10):g} min 提醒阈值）；**不再据此决定任务是否继续**。"
                 f"运行期看门狗：连续 3 个读取块低于区间低端（p{lo_p}）的 1/2 → 暂停、落盘、可续跑。")
    if est.get("sensitivity"):
        s = "，".join(f"{x['speed_mbps']:g} MB/s→{x['total_min']:.0f} min" for x in est["sensitivity"])
        lines.append(f"吞吐敏感性（同一传输量）：{s}")
    return "\n".join(lines)


def compare(est, actual):
    """预估 vs 实测误差；>30% 时返回解释文本。"""
    warn = 0.30
    rows = []
    for a in actual.get("per_epoch", []):
        for e in est.get("per_epoch", []):
            if e.get("target_year") == a.get("target_year") and e.get("scenes"):
                err = (a["elapsed_s"] - e["total_s"]) / max(e["total_s"], 1e-9)
                rows.append({"target_year": e["target_year"], "estimated_s": e["total_s"],
                             "actual_s": round(a["elapsed_s"], 1), "error_ratio": round(err, 3),
                             "over_warn": abs(err) > warn})
    return rows
