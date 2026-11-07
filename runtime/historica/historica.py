#!/usr/bin/env python
"""historica：国内任意地区历史卫星影像下载与对比。

子命令：init / estimate / fetch / compose / report
"""
import argparse, json, os, sys, time

import numpy as np

__version__ = "1.1.0"

from hist import config as hcfg
from hist import geo, years, selector, estimate as hest, fetch as hfetch, tone as htone
from hist import align as halign, compose as hcomp, report as hrep


def _add_scope(p):
    g = p.add_argument_group("范围（四选一）")
    g.add_argument("--admin", help="行政区名称，如 广东省深圳市 / 南平市延平区")
    g.add_argument("--bbox", help="min_lon,min_lat,max_lon,max_lat")
    g.add_argument("--vector", help="GeoJSON/Shapefile(.shp|.zip)/KML 路径")
    g.add_argument("--wkt", help="WKT 面字符串")


def _add_years(p):
    g = p.add_argument_group("年份")
    g.add_argument("--year", "--years", dest="year", help="逗号分隔年份，如 1993,2004,2013,2025")
    g.add_argument("--range", dest="range", help="区间，如 1990-2025")
    g.add_argument("--step", type=int, default=5, help="区间步长（默认 5）")
    g.add_argument("--window", type=int, default=1, help="自动择优的年份窗口 ±N（默认 1）")
    g.add_argument("--prefer-date", action="append", default=[],
                   help="强制某年用某日期，如 1993-12-26；可重复")
    g.add_argument("--season-window", type=int, default=None,
                   help="同季窗口 ±N 天（默认取配置 45；0 = 关闭约束，允许跨季）")


def _add_common(p):
    p.add_argument("--config", help="配置文件路径（默认 config/default.json）")
    p.add_argument("--out", default="runs", help="运行目录（默认 runs）")
    p.add_argument("--res", type=float, help="输出分辨率 m（覆盖配置）")
    p.add_argument("--crs", help="输出 CRS，默认 auto-utm")
    p.add_argument("--provider", help="数据源，默认 landsat-c2-l2")


def _apply_overrides(cfg, a):
    if getattr(a, "res", None):
        cfg["grid"]["res"] = a.res
    if getattr(a, "crs", None):
        cfg["grid"]["crs"] = a.crs
    if getattr(a, "provider", None):
        cfg["data"]["provider"] = a.provider
    return cfg


def _prepare(a):
    cfg = _apply_overrides(hcfg.load(a.config), a)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    cfg["_cache_dir"] = os.path.join(out, "cache")
    return cfg, out


def _targets(a):
    return years.expand_years(a.year, a.range, a.step)


def _prefer_map(a):
    m = {}
    for s in a.prefer_date:
        if "=" in s:
            k, v = s.split("=", 1)
            m[int(k)] = v
        else:
            m[int(s[:4])] = s
    return m


def cmd_init(a):
    cfg, out = _prepare(a)
    hcfg.save_user_config(cfg, out)
    for sub in ("cache", "data", "out"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    print(f"已初始化运行目录: {out}")
    print("配置文件: config/default.json；修改后可用 --config 指定副本。")
    print("交互框选页: historica/scripts/picker.html（用浏览器打开，导出 bbox/GeoJSON）")
    return 0


def _make_plan(a, cfg, log=print):
    aoi = geo.resolve_aoi(a, cfg)
    tg = _targets(a)
    log(f"范围: {aoi['source']}  面积 {aoi['area_km2']:,.2f} km²  边界匹配 {aoi.get('name_matched') or '-'}")
    log(f"格网: {aoi['grid']['crs']}  {aoi['grid']['ncols']}×{aoi['grid']['nrows']} @ {aoi['grid']['res']} m")
    log(f"目标年: {tg}")
    plan = selector.select_plan(aoi, tg, cfg, getattr(a, "window", 1), _prefer_map(a),
                                season_window=getattr(a, "season_window", None), log=log)
    return plan


def cmd_estimate(a):
    cfg, out = _prepare(a)
    plan = _load_plan(a.plan) if getattr(a, "plan", None) else _make_plan(a, cfg)
    run = os.path.join(out, "estimate", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run, exist_ok=True)
    speed = getattr(a, "speed", None)
    est = hest.estimate_plan(plan, cfg, speed=speed, do_probe=not getattr(a, "no_probe", False))
    print()
    print(hest.fmt_table(est))
    json.dump(plan, open(os.path.join(run, "plan.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump(est, open(os.path.join(run, "estimate.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    hcfg.save_user_config(cfg, run)
    print(f"\n已写入: {run}\\plan.json / estimate.json")
    if not getattr(a, "dry_run", False):
        print("未下载（estimate 不会下载）。确认预估后执行 fetch。")
    return 0


def _load_plan(path):
    return json.load(open(path, encoding="utf-8"))


def cmd_fetch(a):
    cfg, out = _prepare(a)
    if a.plan:
        plan = _load_plan(a.plan)
    else:
        plan = _make_plan(a, cfg)
        run = os.path.join(out, "fetch", time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(run, exist_ok=True)
        json.dump(plan, open(os.path.join(run, "plan.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        a.plan = os.path.join(run, "plan.json")
    run = os.path.dirname(os.path.abspath(a.plan))
    grid = plan["aoi"]["grid"]
    os.makedirs(os.path.join(run, "data"), exist_ok=True)
    os.makedirs(os.path.join(run, "out"), exist_ok=True)
    est = None
    est_path = os.path.join(run, "estimate.json")
    if os.path.exists(est_path):
        est = _load_plan(est_path)
        print(hest.fmt_table(est))
        print()

    # 运行期看门狗只看实测：连续 3 块低于预估区间低端（p5）的 1/2 → 暂停、落盘、可续跑
    lo_speed = (est or {}).get("speed_lo_mbps")
    comp = ((est or {}).get("assumptions") or {}).get("compression_ratio", 0.5)
    wd = hfetch.Watchdog(cfg, lo_speed, comp)

    t_all = time.time()
    actual = {"per_epoch": [], "paused": False}
    only = set()
    if getattr(a, "only_year", None):
        only = {int(x) for x in str(a.only_year).replace("，", ",").split(",") if x.strip()}
    completed = []
    paused_reason = None

    def _flush(reason=None):
        actual["total_s"] = round(time.time() - t_all, 1)
        actual["watchdog"] = wd.summary()
        if reason:
            actual["paused"] = True
            actual["pause_reason"] = reason
        json.dump(actual, open(os.path.join(run, "actual.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        json.dump({"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "resume": bool(cfg["fetch"].get("resume", True)),
                   "completed_years": completed, "paused": bool(reason), "reason": reason,
                   "watchdog": wd.summary()},
                  open(os.path.join(run, "fetch_progress.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        hrep.write_metadata_csv(plan, os.path.join(run, "out", "metadata.csv"),
                                years=completed, actual=actual)

    for ep in plan["epochs"]:
        if not ep.get("chosen"):
            continue
        if only and int(ep["target_year"]) not in only:
            continue
        print(f"[{ep['target_year']}] {ep['chosen']['date']}")
        ep0 = time.time()
        try:
            res = hfetch.build_epoch(ep, grid, cfg, os.path.join(run, "cache"), watchdog=wd)
        except hfetch.WatchdogPause as exc:
            paused_reason = str(exc)
            print(f"\n!! 看门狗暂停: {paused_reason}")
            _flush(paused_reason)
            print(f"进度已落盘: {os.path.join(run, 'fetch_progress.json')}；缓存保留，可直接重跑 fetch 续传。")
            return 3
        except Exception as exc:
            print(f"  失败: {exc}")
            actual["per_epoch"].append({"target_year": ep["target_year"], "elapsed_s": time.time() - ep0,
                                        "error": str(exc)})
            continue
        yr = int(ep["target_year"])
        np.savez_compressed(os.path.join(run, "data", f"epoch_{yr}.npz"),
                            rgb=res["rgb"].astype("float32"), mask=res["mask"],
                            valid_frac=res["valid_frac"], cirrus=res["cirrus"])
        meta_one = {
            "target_year": yr, "date": ep["chosen"]["date"],
            "epoch_coverage_pct": round(ep["chosen"]["coverage"] * 100, 2),
            "scene_ids": [m["id"] for m in ep["chosen"]["scenes_meta"]],
            "readable_frac_grid": round(res["valid_frac"], 4),
            "cloud_removed": round(res["cloud_removed"], 4),
            "cirrus_removed_px": int(res["cirrus"].sum()),
            "cirrus_removed_frac_grid": round(res["cirrus_frac"], 4),
            "cirrus_by_scene": res["cirrus_by_scene"],
            "elapsed_s": round(time.time() - ep0, 1),
        }
        json.dump(meta_one, open(os.path.join(run, "data", f"epoch_{yr}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        actual["per_epoch"].append({
            "target_year": yr, "elapsed_s": round(time.time() - ep0, 1),
            "valid_frac": res["valid_frac"], "cloud_removed": res["cloud_removed"],
            "cirrus_removed_px": int(res["cirrus"].sum()),
            "cirrus_removed_frac": res["cirrus_frac"],
            "cirrus_by_scene": res["cirrus_by_scene"]})
        completed.append(yr)
        _flush()   # 每期完成即落盘 metadata + 进度
    actual["notes"] = ("误差主要来自网络吞吐波动与 COG 块级放大（块 256–512 px，窗口不对齐时会多读）；"
                       "若实测远高于预估，先看测速时段是否与下载时段不同。")
    _flush()
    print(f"\n完成，总耗时 {actual['total_s']:.0f}s；metadata.csv 已按每期增量落盘；actual.json 已写入 {run}")
    return 0


def _epoch_files(run):
    d = os.path.join(run, "data")
    return sorted([os.path.join(d, f) for f in os.listdir(d)
                   if f.startswith("epoch_") and f.endswith(".npz")])


def cmd_compose(a):
    cfg, _ = _prepare(a)
    run = os.path.abspath(a.run)
    plan = _load_plan(os.path.join(run, "plan.json"))
    if not plan["aoi"].get("name_matched") and getattr(a, "case_meta", None):
        plan["aoi"]["name_matched"] = (a.case_meta.get("name") or "").split("（")[0]
    grid = plan["aoi"]["grid"]
    from shapely.geometry import shape as shp_shape
    inside2 = hcomp._inside_mask(hcomp._geom_in_grid(shp_shape(plan["aoi"]["boundary_geojson"]), grid), grid)
    epochs = []
    for f in _epoch_files(run):
        z = __import__("numpy").load(f)
        yr = int(os.path.basename(f).split("_")[1].split(".")[0])
        ep_plan = next((e for e in plan["epochs"] if e["target_year"] == yr), None)
        h, w = z["rgb"].shape[:2]
        rep = np.repeat(np.repeat(inside2, 2, 0), 2, 1)
        inside = np.zeros((h, w), bool)
        hh, ww = min(h, rep.shape[0]), min(w, rep.shape[1])
        inside[:hh, :ww] = rep[:hh, :ww]
        m = z["mask"] & inside          # 色调统计/配准只看 AOI 内有效像元，避免格网角上的 0 拉低均值
        disp = htone.apply_tone(z["rgb"], m, cfg)
        disp[~inside] = 0
        cir = z["cirrus"] if "cirrus" in z.files else None
        if cir is not None:
            cir = cir & inside
        epochs.append({"year": yr, "date": ep_plan["chosen"]["date"], "rgb": disp,
                       "mask": m, "coverage": ep_plan["chosen"]["coverage"],
                       "cirrus": cir,
                       "valid_frac": float(z["valid_frac"])})
    if not epochs:
        print("run 目录下没有 data/epoch_*.npz，请先 fetch")
        return 1
    os.makedirs(os.path.join(run, "out"), exist_ok=True)
    al = halign.summarize(halign.verify_pairs([(e["year"], e["rgb"], e["mask"]) for e in epochs], cfg))
    json.dump(al, open(os.path.join(run, "align.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    p_all = hcomp.compose_series(epochs, plan, cfg, os.path.join(run, "out", "series.jpg"),
                                 align_summary=al)
    for ep in epochs:
        hcomp.compose_single(ep, plan, cfg, os.path.join(run, "out", f"single_{ep['year']}.jpg"))
    print(f"已出图: {p_all}")
    print(f"配准: {al['pairs']} 窗口，锁定 {al.get('locked', 0)} / 未锁定 {al.get('unlocked', 0)}，"
          f"零位移 {al['zero_shift']}，最小置信比 {al['min_conf']}")
    return 0


def cmd_report(a):
    cfg, _ = _prepare(a)
    run = os.path.abspath(a.run)
    plan = _load_plan(os.path.join(run, "plan.json"))
    if not plan["aoi"].get("name_matched") and getattr(a, "case_meta", None):
        plan["aoi"]["name_matched"] = (a.case_meta.get("name") or "").split("（")[0]
    est = _load_plan(os.path.join(run, "estimate.json")) if os.path.exists(os.path.join(run, "estimate.json")) else None
    actual = _load_plan(os.path.join(run, "actual.json")) if os.path.exists(os.path.join(run, "actual.json")) else None
    al = _load_plan(os.path.join(run, "align.json")) if os.path.exists(os.path.join(run, "align.json")) else None
    if est and actual and est.get("total") and actual.get("total_s"):
        est_s = float(est["total"]["total_s"])
        tr_mb = float(est["total"]["transfer_mb"])
        lo_s = float(est["total"].get("total_lo_s") or 0)
        hi_s = float(est["total"].get("total_hi_s") or 0)
        open_s = sum(float(r.get("open_s") or 0) for r in est.get("per_epoch", []))
        proc_s = sum(float(r.get("proc_s") or 0) for r in est.get("per_epoch", []))
        net_actual = max(1e-9, float(actual["total_s"]) - open_s - proc_s)
        actual["estimate_total_s"] = round(est_s, 1)
        actual["estimate_total_lo_s"] = round(lo_s, 1)
        actual["estimate_total_hi_s"] = round(hi_s, 1)
        actual["error_ratio"] = round(actual["total_s"] / est_s - 1.0, 4) if est_s else None
        actual["interval_contains_actual"] = bool(lo_s <= actual["total_s"] <= hi_s) if lo_s and hi_s else None
        # 校准表存“净传输等效吞吐”（扣除打开/处理），下次预估再加回固定开销，避免重复计
        actual["effective_speed_mbps"] = round(tr_mb / max(actual["total_s"], 1e-9), 3)
        actual["net_speed_mbps"] = round(tr_mb / net_actual, 3)
        if actual.get("cold_equivalent_s"):
            actual["net_speed_cold_equivalent_mbps"] = round(
                tr_mb / max(float(actual["cold_equivalent_s"]) - open_s - proc_s, 1e-9), 3)
        from hist import calib
        case = os.path.basename(os.path.dirname(run))
        if case in ("estimate", "fetch"):
            case = os.path.basename(os.path.dirname(os.path.dirname(run)))
        sb = est.get("speed_basis") or {}
        calib.record(cfg, {"case": case, "run": run,
                           "est_s": round(est_s, 1), "est_lo_s": round(lo_s, 1), "est_hi_s": round(hi_s, 1),
                           "actual_s": round(actual["total_s"], 1),
                           "error_ratio": actual["error_ratio"],
                           "interval_contains_actual": actual["interval_contains_actual"],
                           "speed_mbps": actual["net_speed_mbps"],
                           "actual_concurrent_mbps": actual["net_speed_mbps"],
                           "probe_concurrent_mbps": sb.get("probe_median_mbps"),
                           "probe_rounds": sb.get("probe_rounds"),
                           "probe_workers": sb.get("probe_workers"),
                           "block_speeds_mbps": ((actual.get("watchdog") or {})
                                                 .get("throughput_samples_mbps") or []),
                           "transfer_mb": round(tr_mb, 1), "mode": "net(扣除打开与处理)"})
        sp, n = calib.median_speed(cfg)
        actual["calibration_median_mbps"] = sp
        actual["calibration_n"] = n
        actual["calibration_window_speeds"] = calib.recent_speeds(cfg)
        if sp:
            net_cal = tr_mb / sp
            total_cal = net_cal + open_s + proc_s
            lo, hi = cfg["estimate"]["time_range"]
            actual["calibrated_estimate"] = {
                "speed_mbps": round(sp, 3), "point_s": round(total_cal, 1),
                "lo_s": round(total_cal * lo, 1), "hi_s": round(total_cal * hi, 1),
                "error_ratio": round(actual["total_s"] / total_cal - 1.0, 4),
                "point_error_ratio": actual["error_ratio"]}
        json.dump(actual, open(os.path.join(run, "actual.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"误差 {actual['error_ratio']*100:+.1f}%；区间覆盖实测: "
              f"{actual['interval_contains_actual']}；校准表中位吞吐 {sp:.3f} MB/s（{n} 次）")
    hrep.write_metadata_csv(plan, os.path.join(run, "out", "metadata.csv"), actual=actual)
    md = hrep.build_markdown(plan, est, actual, al, cfg, os.path.join(run, "out"),
                             case_id=(getattr(a, "case_meta", None) or {}).get("_id"))
    p = hrep.save(md, os.path.join(run, "out"))
    print(f"报告: {p}")
    return 0


def _load_case(name):
    """读取 config/cases/<name>.json；文件里的 defaults 作为 argparse 默认值。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "cases", f"{name}.json")
    if not os.path.exists(p):
        raise SystemExit(f"未找到案例配置: {p}")
    obj = json.load(open(p, encoding="utf-8"))
    obj["_id"] = name
    return obj


def _peek_case(argv):
    for i, a in enumerate(argv):
        if a == "--case" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--case="):
            return a.split("=", 1)[1]
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="historica", description="历史卫星影像下载与对比")
    ap.add_argument("--version", action="version", version=f"historica {__version__}")
    ap.add_argument("--case", help="案例配置名（config/cases/<name>.json），其 defaults 作为默认参数")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="初始化运行目录与配置")
    _add_common(p); p.set_defaults(func=cmd_init)

    p = sub.add_parser("estimate", help="选景 + 实测吞吐 + 预估（不下载）")
    _add_scope(p); _add_years(p); _add_common(p)
    p.add_argument("--dry-run", action="store_true", help="只出预估（本子命令默认就是 dry-run）")
    p.add_argument("--speed", type=float, help="手动指定吞吐 MB/s，跳过测速")
    p.add_argument("--no-probe", action="store_true", help="不测速，用配置兜底值")
    p.add_argument("--plan", help="已有 plan.json（跳过 STAC 查询，便于离线 dry-run）")
    p.set_defaults(func=cmd_estimate)

    p = sub.add_parser("fetch", help="按计划窗口下载并拼接")
    _add_scope(p); _add_years(p); _add_common(p)
    p.add_argument("--plan", help="已有 plan.json（来自 estimate）")
    p.add_argument("--only-year", dest="only_year", help="只重建指定年份（可逗号分隔），用于局部重跑")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("compose", help="色调 + 配准校验 + 出图")
    _add_common(p); p.add_argument("--run", required=True, help="fetch 的 run 目录")
    p.set_defaults(func=cmd_compose)

    p = sub.add_parser("report", help="生成元数据 CSV 与技术报告")
    _add_common(p); p.add_argument("--run", required=True, help="fetch 的 run 目录")
    p.set_defaults(func=cmd_report)

    case = _load_case(_peek_case(argv)) if _peek_case(argv) else None
    if case:
        for sp in sub.choices.values():
            sp.set_defaults(**case.get("defaults", {}), case_meta=case)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except (geo.AOIError, years.YearSpecError) as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
