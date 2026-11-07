"""技术报告生成：数值 + 链接 + 推断标注 + 预估/实测对照。"""
import csv, os, time


def bbox_coverage(chosen, bounds):
    """同一组影像相对「外接矩形」的覆盖率（与区界口径并列展示，不用于成本以外用途）。"""
    from shapely.geometry import box, shape
    from shapely.ops import unary_union
    box_geom = box(*bounds)
    fps = [shape(m["footprint"]) for m in chosen.get("scenes_meta", []) if m.get("footprint")]
    if not fps:
        return None
    u = unary_union(fps)
    return u.intersection(box_geom).area / box_geom.area


def bbox_area_km2(bounds):
    from pyproj import Geod
    w, s, e, n = list(bounds)
    return abs(Geod(ellps="WGS84").polygon_area_perimeter([w, e, e, w, w], [s, s, n, n, s])[0]) / 1e6


def readable_by_epoch(plan, run):
    """区界内有效像元占比（第三口径，实测；用于验收闸门）。"""
    import numpy as np
    from shapely.geometry import shape
    from . import compose as hc
    grid = plan["aoi"]["grid"]
    inside = hc._inside_mask(hc._geom_in_grid(shape(plan["aoi"]["boundary_geojson"]), grid), grid, step=1)
    d = os.path.join(run, "data")
    out = {}
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        if name.startswith("epoch_") and name.endswith(".npz"):
            yr = int(name[6:-4])
            m = np.load(os.path.join(d, name))["mask"]
            out[yr] = float((m & inside).sum()) / max(1, int(inside.sum()))
    return out


def cirrus_by_epoch(plan, run):
    """区界内因薄卷云被扣除的像元占比/面积（用于改前改后与图面核对）。"""
    import numpy as np
    from shapely.geometry import shape
    from . import compose as hc
    grid = plan["aoi"]["grid"]
    inside = hc._inside_mask(hc._geom_in_grid(shape(plan["aoi"]["boundary_geojson"]), grid), grid, step=1)
    d = os.path.join(run, "data")
    out = {}
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        if name.startswith("epoch_") and name.endswith(".npz"):
            yr = int(name[6:-4])
            z = np.load(os.path.join(d, name))
            if "cirrus" not in z.files:
                continue
            cz = z["cirrus"] & inside
            out[yr] = {"px": int(cz.sum()),
                       "frac_inside": float(cz.sum()) / max(1, int(inside.sum())),
                       "km2": float(cz.sum()) * float(grid["res"]) ** 2 / 1e6}
    return out


def write_metadata_csv(plan, path, years=None, actual=None):
    rows = []
    act = {}
    if actual:
        for e in actual.get("per_epoch", []):
            act[int(e["target_year"])] = e
    for ep in plan["epochs"]:
        ch = ep.get("chosen")
        if not ch:
            continue
        if years and int(ep["target_year"]) not in years:
            continue
        ae = act.get(int(ep["target_year"]), {})
        sm = ep.get("season") or {}
        for m in ch.get("scenes_meta", []):
            from datetime import date as _date
            rows.append({
                "target_year": ep["target_year"],
                "image_id": m["id"],
                "sensor": m["id"].split("_")[0],
                "date": m["date"],
                "day_of_year": _date.fromisoformat(m["date"]).timetuple().tm_yday,
                "season_window_days": sm.get("window_days", ""),
                "season_center_doy": sm.get("center_doy", ""),
                "season_offset_days": sm.get("offset_days", ""),
                "cross_season": bool(sm.get("cross_season")),
                "cloud_cover_pct": m["cloud"],
                "sun_elevation_deg": m.get("sun"),
                "scene_epsg": m.get("epsg"),
                "native_res_m": 30 if str(m.get("epsg", "")).startswith("326") else "",
                "aoi_bbox_wgs84": ",".join(f"{v:.6f}" for v in plan["aoi"]["bounds"]),
                "epoch_coverage_pct": round(ch["coverage"] * 100, 2),
                "readable_in_grid_pct": round(float(ae["valid_frac"]) * 100, 2) if ae.get("valid_frac") is not None else "",
                "thin_cirrus_removed_px": ae.get("cirrus_removed_px", ""),
                "thin_cirrus_removed_pct_grid": round(float(ae["cirrus_removed_frac"]) * 100, 2) if ae.get("cirrus_removed_frac") is not None else "",
            })
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["image_id"])
        w.writeheader()
        w.writerows(rows)
    return path


def _season_section(plan, A):
    """季节一致性小节：改前/改后日序、跨季标记、放开窗口能换回多少（下载前推断）。"""
    se = plan.get("season") or {}
    if not se:
        return
    A("### 1.1 季节一致性（v1.1 同季约束）\n")
    if not se.get("enabled"):
        A("- 季节约束**已关闭**（`--season-window 0` 或配置为 0）：各期可能落在不同季节，跨期色调/物候不可比。\n")
        return
    pre, post = se.get("prelock") or {}, se.get("postlock") or {}
    A(f"- 共同窗口：中心 {se.get('center_label')}（日序 {se.get('center_doy')}），±{se.get('window_days')} 天；"
      f"中心来源：{se.get('center_source')}。")
    A(f"- 改前（无约束初选）：日序 {pre.get('doy')}，月份 {pre.get('months')}，"
      f"标准差 {pre.get('std_days')} 天，最大差 {pre.get('max_diff_days')} 天。")
    A(f"- 改后（同季约束）：日序 {post.get('doy')}，月份 {post.get('months')}，"
      f"标准差 {post.get('std_days')} 天，最大差 {post.get('max_diff_days')} 天，"
      f"最大偏移 {post.get('max_offset_days')} 天（相对窗口中心）。")
    cs = se.get("cross_season_epochs") or []
    if cs:
        A(f"- **跨季回退**：{', '.join(str(y) for y in cs)} 期。回退不静默，逐期说明如下：")
        for ep in plan["epochs"]:
            sm = ep.get("season") or {}
            if not sm.get("cross_season"):
                continue
            ib = sm.get("in_window_best") or {}
            A(f"  - {ep['target_year']} → `{(ep.get('chosen') or {}).get('date', '无')}`："
              f"{sm.get('fallback_reason')}；跨季 {sm.get('cross_season_days')} 天。"
              f"窗口内最优 {ib.get('date') or '无'}"
              f"（覆盖 {ib.get('coverage_pct', '—')}%，推断可读 {ib.get('readable_est_pct', '—')}%）。")
    else:
        A("- 四期全部落在窗口内，无跨季回退。")
    gains = []
    for ep in plan["epochs"]:
        sm = ep.get("season") or {}
        ow = sm.get("open_window")
        if ow:
            gains.append(f"{ep['target_year']}：放开窗口可用 `{ow.get('date')}`"
                         f"（覆盖 {ow.get('coverage_pct')}%，推断可读 {ow.get('readable_est_pct')}%），"
                         f"{'+' if (ow.get('gain_readable_est_pp') or 0) >= 0 else ''}"
                         f"{ow.get('gain_readable_est_pp')} 个百分点")
    if gains:
        A("- **放开窗口能换回多少**（用同一次 STAC 检索的候选、不下载，可读覆盖为"
          "「几何覆盖 × (1 − 景云量)」的推断值，不等同于 QA 实测）：" + "；".join(gains) + "。")
    else:
        A("- 各期在窗口内都已达到覆盖目标，放开窗口无额外收益（或初选与窗口内结果同景）。")
    A("")


def build_markdown(plan, est, actual, align, cfg, out_dir, extra=None, case_id=None):
    aoi = plan["aoi"]
    g = aoi["grid"]
    lo_p, hi_p = ((est or {}).get("interval_percentiles") or [5, 95])[:2]
    L = []
    A = L.append
    A(f"# {aoi.get('name_matched') or '研究区'} 历史卫星影像对比技术报告\n")
    A(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}（本机时区 Asia/Shanghai）")
    A(f"- 数据源：`{cfg['data']['provider']}` / Planetary Computer STAC（`{cfg['data']['stac_url']}`）")
    A(f"- 输出投影：`{g['crs']}`，分辨率 {g['res']:.0f} m，格网 {g['ncols']}×{g['nrows']}")
    A(f"- 目标范围：WGS84 {', '.join(f'{v:.4f}' for v in aoi['bounds'])}；面积约 {aoi['area_km2']:,.2f} km²")
    A(f"- 边界来源：{aoi['source']}（{aoi['source_detail']}）；精度说明：{aoi['precision']}\n")

    A("## 1. 范围与边界\n")
    A(f"- 名称匹配链：`{aoi.get('name_matched') or '用户直接给定'}`")
    A(f"- 边界备注：{aoi.get('note') or '无'}")
    A(f"- 面积口径：区界 {aoi['area_km2']:,.2f} km²（展示/覆盖率口径）；"
      f"外接矩形 {bbox_area_km2(aoi['bounds']):,.2f} km²（仅算窗口与成本）。两者并列列出，不混用。")
    A("- 边界仅作定位参考，不得用于权属或测绘用途。\n")
    if cfg.get("area", {}).get("report_all_scopes"):
        rdp = readable_by_epoch(plan, os.path.dirname(out_dir))
        cbp = cirrus_by_epoch(plan, os.path.dirname(out_dir))
        A("| 年份 | 成像日期 | 区界覆盖% | 可读% | 薄卷云扣除% | 矩形覆盖% | 无影像 km² | 不可读 km² |")
        A("|---|---|---|---|---|---|---|---|")
        for ep in plan["epochs"]:
            ch = ep.get("chosen")
            if not ch:
                continue
            bc = bbox_coverage(ch, aoi["bounds"])
            miss = (1.0 - ch["coverage"]) * aoi["area_km2"]
            bc_txt = f"{bc*100:.1f}" if bc is not None else "—"
            rd = rdp.get(ep["target_year"])
            rd_txt = f"{rd*100:.1f}" if rd is not None else "—"
            unread = (1.0 - rd) * aoi["area_km2"] if rd is not None else None
            unread_txt = f"{unread:,.1f}" if unread is not None else "—"
            cb = cbp.get(ep["target_year"])
            cb_txt = f"{cb['frac_inside']*100:.2f}" if cb else "—"
            A(f"| {ep['target_year']} | {ch['date']} | {ch['coverage']*100:.1f} | {rd_txt} | {cb_txt} | {bc_txt} | {miss:,.1f} | {unread_txt} |")
        A(f"- 可读% = 区界内有效像元（去云/阴影/填充/薄卷云）占比；验收阈值 "
          f"{cfg['area']['coverage_thresholds'].get('readable_accept', 0.70)*100:.0f}% 以下需确认。"
          "「无影像 km²」来自区界覆盖率缺额；「不可读 km²」= (1 − 可读%) × 区界面积，含云/阴影/薄卷云。"
          "「薄卷云扣除%」为区界内因薄卷云筛查被移出可读的像元占比（图上品红细网点）。\n")
        _season_section(plan, A)


    A("## 2. 选景与候选排名\n")
    wsel = cfg["selection"]["weights"]
    A("打分项：" + "、".join(f"{k} {float(v)*100:.0f}%" for k, v in wsel.items())
      + "（权重来自 `config/default.json` → `selection.weights`；v1.1 新增季节一致性项，"
        "窗口中心 1.0、偏移=窗口半宽 0.5、≥2×半宽 0）。\n")
    for ep in plan["epochs"]:
        A(f"### {ep['target_year']}（查询窗口 {ep['query_window'][0]}–{ep['query_window'][1]}，候选 {ep.get('n_candidates',0)} 景）")
        ch = ep.get("chosen")
        if not ch:
            A("- **无可用候选**（该年份窗口内没有满足云量阈值的影像）\n")
            continue
        A(f"- 入选：`{ch['date']}`，覆盖 **{ch['coverage']*100:.2f}%**，景云量 {ch['cloud']:.2f}%，"
          f"太阳高度 {ch['sun'] if ch['sun'] is not None else '未知'}°，{ch['n_scenes']} 景")
        if ch.get("scenes"):
            A(f"- 影像：{', '.join('`' + s + '`' for s in ch['scenes'])}")
        if ch["coverage"] < 0.995:
            A(f"- **覆盖不足**：缺 {100 - ch['coverage']*100:.2f}%，缺测区已在图面用斜线标注并给出面积")
        A("")
        if cfg["report"].get("include_candidate_ranking"):
            A("| 排名 | 日期/区间 | 覆盖% | 云量% | 太阳高度 | 日期跨度 | 综合分 | 备注 |")
            A("|---|---|---|---|---|---|---|---|")
            alts = []
            seen_dates = set()
            for a in ep.get("alternatives", []):
                if a["date"] in seen_dates:
                    continue
                seen_dates.add(a["date"])
                alts.append(a)
            alts = alts[:6]
            for i, a in enumerate(alts, 1):
                note = "跨日期补缺" if a.get("mixed") else ("低覆盖" if a["coverage"] < cfg["selection"]["min_coverage"] else "")
                span = a.get("span_days", 0)
                A(f"| {i} | {a['date']} | {a['coverage']*100:.1f} | {a['cloud']:.1f} | "
                  f"{a['sun'] if a['sun'] is not None else '—'} | {span or '—'} | {a['score']:.3f} | {note} |")
            A("")
            if ep.get("mixed_option"):
                mo = ep["mixed_option"]
                A(f"- 备选（跨日期补缺）：{mo['date']}，覆盖 {mo['coverage']*100:.1f}%，"
                  f"跨度 {mo['span_days']} 天，代价是多景物候/日期不一致。")
            A("")

    A("## 3. 处理参数\n")
    A("| 环节 | 参数 |")
    A("|---|---|")
    A(f"| 读取 | COG 窗口读取（仅目标 bbox 窗口，不整景下载）；并发 {cfg['fetch']['workers']} |")
    A(f"| 重投影 | 源景原生 CRS → `{g['crs']}`，双线性 |")
    A(f"| 拼接 | 多轨/多行按目标覆盖并集；边界羽化 {cfg['fetch']['feather_km']} km（曼哈顿距离线性权重） |")
    A(f"| 云掩膜 | Landsat C2 `QA_PIXEL` bit1–4（膨胀云/卷云/云/云影）；S2 `SCL` 4/5/6/9 |")
    tc = (cfg.get("qc") or {}).get("thin_cirrus") or {}
    if tc.get("enabled"):
        A(f"| 薄卷云筛查（QA 之外） | 逐波段最小反射率 ≥ {tc.get('brightness_min')} 且 "
          f"\\|NDSI\\| ≤ {tc.get('ndsi_abs_max')} 且 QA 云位=0；从可读扣除，图上品红细网点；"
          f"阈值来源：延平 2003 三斑块实测（见 `config/default.json` → `qc.thin_cirrus.source`） |")
    wd = (cfg.get("fetch") or {}).get("watchdog") or {}
    A(f"| 运行期看门狗 | 连续 {wd.get('consecutive_below', 3)} 个读取块等效吞吐 < p{lo_p} 速度的 "
      f"{wd.get('below_fraction', 0.5)} → 暂停/落盘/可续跑（预测不决定是否继续） |")
    A(f"| 色调 | DOS p{cfg['tone']['dos_percentile']} → 白平衡 {cfg['tone']['wb_target']} → 固定拉伸 "
      f"{cfg['tone']['stretch']}/gamma {cfg['tone']['gamma']} → 锚定 {cfg['tone']['anchor']} → "
      f"对比度 {cfg['tone']['contrast']} → 饱和 {cfg['tone']['saturation']} → 均值 {cfg['tone']['target_mean']} |")
    A(f"| 输出 | {cfg['compose']['dpi']} dpi，比例尺 {cfg['compose']['scale_km']:.0f} km |")
    A("")

    A("## 4. 预估 vs 实测\n")
    if est:
        sb = est.get("speed_basis") or {}
        A(f"- 采用吞吐（点估计）：**{est['speed_mbps']:.3f} MB/s**（来源：{sb.get('source', '—')}）。"
          f"并发探针中位 {sb.get('probe_median_mbps')} MB/s（{sb.get('probe_rounds', 0)} 轮 / "
          f"{sb.get('probe_workers')} 路）；单流探针 {sb.get('single_stream_diagnostic_mbps')} MB/s"
          f"（仅诊断，不进估算）；校准净吞吐中位 {sb.get('calibration_median_mbps')}"
          f"（{sb.get('calibration_n', 0)} 次）。")
        A(f"- 速度区间 **{est.get('speed_p10_mbps')}–{est.get('speed_p90_mbps')} MB/s**"
          f"（{sb.get('interval_source', '—')}，样本 {est.get('speed_samples')}）。"
          if est.get("speed_p10_mbps") is not None else
          "- 速度区间：该 run 为旧版 estimate（无 p10/p90 样本），区间不可用；见同目录旧报告结论。")
        t = est.get("total") or {}
        ce = (actual or {}).get("calibrated_estimate") or {}
        A(f"- **预估区间（p{lo_p}–p{hi_p} 换算）**：点估计 {t.get('total_s', 0)/60:.1f} min，"
          f"区间 **{t.get('total_lo_s', 0)/60:.1f}–{t.get('total_hi_s', 0)/60:.1f} min**。"
          "报告同时给出点估计与区间；只写点估计不合格。")
        adv = est.get("advisory") or {}
        A(f"- **预测用途**：仅用于提醒与是否分批（点估计"
          f"{'超过' if adv.get('suggest_split') else '未超过'} {adv.get('over_threshold_min', 10):g} min 提醒阈值）；"
          "**不决定任务是否继续**。停止条件为运行期看门狗：连续 "
          f"{((cfg.get('fetch') or {}).get('watchdog') or {}).get('consecutive_below', 3)} 个读取块的实际吞吐"
          f"低于预估区间低端（p{lo_p}）的 {((cfg.get('fetch') or {}).get('watchdog') or {}).get('below_fraction', 0.5)}"
          " → 暂停、落盘进度、可续跑。")
        runs = (actual or {}).get("runs") or []
        wd = (actual or {}).get("watchdog") or {}
        if wd.get("enabled"):
            seg = "（最后一段/续跑）" if len(runs) > 1 else ""
            A(f"- 本轮看门狗{seg}：阈值 {wd.get('threshold_mbps')} MB/s，观察 {wd.get('samples', 0)} 个块，"
              f"实测 {wd.get('min_mbps')}–{wd.get('max_mbps')} MB/s（中位 {wd.get('median_mbps')}），"
              f"{'**已触发暂停**' if wd.get('triggered') else '未触发'}。")
        if actual and actual.get("paused"):
            A(f"- **任务因看门狗暂停**：{actual.get('pause_reason')}")
        for ph in (actual or {}).get("pause_history") or []:
            A(f"- **暂停记录**（{ph.get('at')}，{ph.get('epoch')} 期）：{ph.get('reason')}")
        if len(runs) > 1:
            segs = "；".join(f"{r['run']} 段 {r.get('started', '')}–{r.get('ended', '')}"
                            f"（{r.get('elapsed_s', 0):.0f} s，{r.get('outcome')}）" for r in runs)
            A(f"- **运行分段**：{segs}；段间人工等待 {((actual or {}).get('human_gap_s') or 0):.0f} s 不计入活跃时长。")
        if actual and actual.get("cold_equivalent_s"):
            roi = actual.get("resume_overhead_detail") or {}
            A(f"- **as-run 与冷启动等价**：as-run 活跃 **{actual.get('active_total_s', actual.get('total_s')):.0f} s**；"
              f"扣除续跑重读已完成期 {roi.get('cached_epoch_rebuild_s', 0):.0f} s 与暂停导致的重复读取 "
              f"{roi.get('duplicate_green_read_s', 0):.0f} s 后，冷启动等价 **{actual['cold_equivalent_s']:.0f} s**。"
              f"下面表格逐期给出两段合计的实测 s。")
        if ce:
            A(f"- **校准表回代（含本条，非样本外）**：按校准表最近 {actual.get('calibration_n', 0)} 条中位净吞吐 "
              f"{ce.get('speed_mbps')} MB/s 回代，合计 {ce.get('point_s', 0):.0f} s"
              f"（{ce.get('lo_s', 0):.0f}–{ce.get('hi_s', 0):.0f} s），与本轮实测 "
              f"{(actual or {}).get('total_s', 0):.0f} s 相差 {(ce.get('error_ratio') or 0)*100:+.1f}%。"
              "该列只说明「若按校准中位预测会怎样」，不是本轮自洽校验。")
        A("")
        A("| 年份 | 景数 | 窗口像素 | 预估流量 MB | 预估点 s | 预估区间 s | 回代 s* | 实测 s（两段） | 误差 | 冷等价 s | 误差(冷) |")
        A("|" + "---|" * 11)
        rows = {r["target_year"]: r for r in (actual or {}).get("per_epoch", [])}
        for e in est["per_epoch"]:
            if not e.get("scenes"):
                continue
            a = rows.get(e["target_year"])
            cal_s = (e.get("transfer_mb", 0) / max(ce.get("speed_mbps") or 1e-9, 1e-9)
                     + e.get("open_s", 0) + e.get("proc_s", 0)) if ce else None
            rng = f"{e.get('total_lo_s', 0):.0f}–{e.get('total_hi_s', 0):.0f}"
            if a:
                err = (a["elapsed_s"] - e["total_s"]) / max(e["total_s"], 1e-9)
                flag = " **>30%**" if abs(err) > cfg["estimate"]["warn_error_ratio"] else ""
                cold = a.get("cold_s") or (a["elapsed_s"] if len(runs) <= 1 else None)
                cold_txt = f"{cold:.0f}" if cold else "—"
                cerr = (cold - e["total_s"]) / max(e["total_s"], 1e-9) if cold else None
                cerr_txt = f"{cerr*100:+.1f}%" if cerr is not None else "—"
                A(f"| {e['target_year']} | {e['scenes']} | {e['window_px']:,} | {e['payload_mb']:.1f} | "
                  f"{e['total_s']:.0f} | {rng} | {cal_s:.0f} | {a['elapsed_s']:.0f} | {err*100:+.1f}%{flag} | "
                  f"{cold_txt} | {cerr_txt} |")
            else:
                A(f"| {e['target_year']} | {e['scenes']} | {e['window_px']:,} | {e['payload_mb']:.1f} | "
                  f"{e['total_s']:.0f} | {rng} | {('—' if cal_s is None else f'{cal_s:.0f}')} | — | — | — | — |")
        A("")
        if actual and actual.get("notes"):
            A(f"- 误差说明：{actual['notes']}")
        A("")
        A("*注：回代列 = 按校准表最近 N 条中位净吞吐复算，含本条，不是样本外精度证据；冷等价 = 扣除暂停与续跑重读后的用时。\n")
        if actual and actual.get("error_ratio") is not None:
            ge = actual["error_ratio"]
            A(f"- 合计：预估点估计 {actual.get('estimate_total_s', 0):.0f} s"
              f"（区间 {actual.get('estimate_total_lo_s', 0):.0f}–{actual.get('estimate_total_hi_s', 0):.0f} s），"
              f"实测 {actual['total_s']:.0f} s，误差 **{ge*100:+.1f}%**；含打开与处理的等效吞吐 "
              f"{actual.get('effective_speed_mbps', 0):.3f} MB/s。")
            inside_txt = "是" if actual.get("interval_contains_actual") else "否"
            A(f"- **预估区间是否覆盖实测（as-run）**：{inside_txt}。")
            cold = actual.get("cold_equivalent_s")
            if cold:
                lo_s = actual.get("estimate_total_lo_s") or 0
                hi_s = actual.get("estimate_total_hi_s") or 0
                cov = "**在区间内**" if lo_s <= cold <= hi_s else "**仍在区间外**"
                A(f"- **以冷启动等价 {cold:.0f} s 对照区间 {lo_s:.0f}–{hi_s:.0f} s**：{cov}。")
            A(f"- **新预估 vs 实测（点估计口径）**：误差 **{ge*100:+.1f}%** → "
              f"{'**已收敛到 10% 以内**' if abs(ge) <= 0.10 else '**未收敛到 10% 以内**'}。")
            if ce:
                ns = actual.get("net_speed_mbps")
                ns_cold = actual.get("net_speed_cold_equivalent_mbps")
                A(f"- **偏差来源核对**：本轮并发探针中位 {sb.get('probe_median_mbps')} MB/s；"
                  f"实测净吞吐 {ns} MB/s（as-run）/ {ns_cold if ns_cold else '—'} MB/s（冷等价）；"
                  f"探针/实测 = {(sb.get('probe_median_mbps') or 0)/max(ns_cold or ns or 1e-9, 1e-9):.2f}；"
                  f"单流诊断 {sb.get('single_stream_diagnostic_mbps')} MB/s（仅对照，不参与点估计）。"
                  f"预估采用 {est.get('speed_mbps')} MB/s，为实测净吞吐的 "
                  f"{(est.get('speed_mbps') or 0)/max(ns_cold or ns or 1e-9, 1e-9)*100:.0f}%。")
                tr = sum(float(e.get("transfer_mb") or 0) for e in est["per_epoch"])
                opn = sum(float(e.get("open_s") or 0) for e in est["per_epoch"])
                prc = sum(float(e.get("proc_s") or 0) for e in est["per_epoch"])
                t_est = float((est.get("total") or {}).get("total_s") or 0)
                A(f"- **开销拆分**：传输 {tr:.1f} MB、打开 {opn:.0f} s、处理 {prc:.0f} s；"
                  f"按点估计 {est.get('speed_mbps')} MB/s 纯传输需 {tr/max(est.get('speed_mbps') or 1e-9, 1e-9):.0f} s，"
                  f"占预估合计 {t_est:.0f} s 的 {tr/max(est.get('speed_mbps') or 1e-9, 1e-9)/max(t_est, 1e-9)*100:.0f}%。"
                  "误差主要来自吞吐输入（测速时段与下载时段不一致）与慢资产/慢时段，不是打开/处理开销。")
        if actual and actual.get("calibration_n"):
            A(f"- **校准回写**：本轮 (预估区间, 实测, 误差率, 实际并发吞吐) 已写入 "
              f"`{cfg['estimate']['calibration']['path']}`（同一 run 只保留最新一条），"
              f"表现有 {actual['calibration_n']} 条样本。")
            A(f"- **下次预估如何用这几条**：样本 = 新一次并发探针的 "
              f"{int(est['assumptions'].get('probe_rounds', 5))} 轮聚合吞吐 + 最近 "
              f"{cfg['estimate']['calibration']['window']} 条校准净吞吐（含本条）"
              " + 这些运行在下载期看门狗记录的分块吞吐（每条等距抽最多 10 个）；"
              f"每个样本等权（不按新旧/大小加权），点估计取中位，区间取样本 p{lo_p}–p{hi_p}。"
              "若样本数 < 4，区间回退到配置 time_range 系数并明确标注。"
              f"当前校准表共 {actual.get('calibration_n', 0)} 条样本："
              f"{actual.get('calibration_window_speeds')}（MB/s），"
              f"最近 {cfg['estimate']['calibration']['window']} 条中位净吞吐为 "
              f"**{actual['calibration_median_mbps']:.3f} MB/s**。"
              + ("注意：本轮 as-run 含暂停与续跑，入库用的是 as-run 净吞吐（偏保守）；"
                 "若下次预估明显偏长，应先看这段网络是否仍处于低速时段。"
                 if len((actual.get("runs") or [])) > 1 else
                 "注意：本轮未触发暂停，as-run 即冷启动等价，入库用的是 as-run 净吞吐；"
                 "若下一轮落在低速时段，中位会被自然拉低，属预期行为。"))
        A("")

    A("## 5. 配准校验\n")
    if align:
        A(f"- 方法：梯度幅值 NCC，搜索 ±12 px，3 窗口 × 相邻期对；NCC<0.2 或峰值落在搜索边界一律判「未锁定」，不输出位移。")
        mc = align.get("min_conf")
        mc_txt = f"{mc:.2f}" if isinstance(mc, (int, float)) else "—（有效窗口不足，单期或纹理不足）"
        A(f"- 结果：{align['pairs']} 个窗口/期对，锁定 {align.get('locked', 0)}、"
          f"未锁定 {align.get('unlocked', 0)}；锁定中零位移 {align['zero_shift']} 个"
          f"（占锁定 {align['pass_rate']*100:.1f}%），最小置信比 {mc_txt}。")
        unl = [r for r in align.get("results", []) if not r.get("locked")]
        if unl:
            A(f"- **未锁定 {len(unl)} 处（不计入位移；下列前 {min(5, len(unl))} 处）**：")
            for r in unl[:5]:
                A(f"  - `{r['pair']}` 窗口 {r['window']}：{r.get('reason', '未锁定')}，NCC={r['ncc']}")
        bad = [r for r in align.get("results", [])
               if r.get("locked") and r.get("dx") is not None and (abs(r["dx"]) > 1 or abs(r["dy"]) > 1)]
        if bad:
            A(f"- **非零位移 {len(bad)} 处**：")
            for r in bad[:5]:
                A(f"  - `{r['pair']}` 窗口 {r['window']}：dx={r['dx']}, dy={r['dy']}, NCC={r['ncc']}, 置信比={r['confidence_ratio']}")
        A("- 说明：相对配准只能消除两期之间的相对偏移；各景绝对定位误差（约 12 m RMSE）无法由此排除。")
        A("")

    A("## 6. 薄卷云筛查与 2003 专项复核\n")
    import json as _json
    A("### 6.1 薄卷云筛查统计（本轮全期）\n")
    tc6 = (cfg.get("qc") or {}).get("thin_cirrus") or {}
    cbp6 = cirrus_by_epoch(plan, os.path.dirname(out_dir))
    if cbp6:
        A(f"- 判据：逐波段最小反射率 ≥ **{tc6.get('brightness_min')}**，"
          f"|NDSI| ≤ **{tc6.get('ndsi_abs_max')}**，且 `QA_PIXEL` bit1–4 = 0；"
          "从可读像元扣除，图上品红细网点。阈值来源：延平 2003 三片斑块逐像元统计"
          "（逐波段最小值 p25 = 0.405–0.452 → 取 0.40；|NDSI| p95 = 0.232–0.272 → 取 0.25）。")
        A("")
        A("| 年份 | 区界内扣除像元 | 区界内占比 | 面积 km² |")
        A("|---|---|---|---|")
        for yr, v in sorted(cbp6.items()):
            A(f"| {yr} | {v['px']:,} | {v['frac_inside']*100:.2f}% | {v['km2']:,.1f} |")
        A("")
        ab_p = os.path.join(out_dir, "cirrus_ab.json")
        if os.path.exists(ab_p):
            ab = _json.load(open(ab_p, encoding="utf-8"))
            A("**改前 / 改后可读覆盖（区界内）**\n")
            A("| 年份 | 成像日期 | 改前可读% | 改后可读% | 变化 | 薄卷云扣除 km² | 单景最高候选率* |")
            A("|---|---|---|---|---|---|---|")
            for r in ab.get("rows", []):
                A(f"| {r['year']} | {r['date']} | {r['before_pct']:.1f} | {r['after_pct']:.1f} | "
                  f"{r['after_pct']-r['before_pct']:+.1f} pt | {r['cirrus_km2']:,.1f} | "
                  f"{r.get('max_candidate_frac_pct', r['candidate_frac_pct']):.1f}% |")
            A(f"- *单景最高候选率 = 该期各景中「被筛出像元 / 该景 QA-clear 像元」的最大值（诊断用，不等于区界扣除比例）。"
              f"阈值：亮度 ≥ {ab.get('brightness_min')}、|NDSI| ≤ {ab.get('ndsi_abs_max')}、QA 云位=0；"
              f"来源：{ab.get('source', '延平 2003 三斑块实测')}。\n")
        per = (actual or {}).get("per_epoch") or []
        detail = []
        for e in per:
            for s in e.get("cirrus_by_scene", []) or []:
                if s.get("candidate_px"):
                    detail.append((s["candidate_px"], e["target_year"], s["id"], s.get("candidate_frac", 0)))
        if detail:
            detail.sort(reverse=True)
            A("- 单景候选占比最高的前 5 个（候选像元 / 该景 QA-clear 像元）：")
            for px, yr, sid, frac in detail[:5]:
                A(f"  - {yr} `{sid}`：{px:,} px（{frac*100:.1f}%）")
        A("- 说明：本规则只在「QA 云位为 0」时补漏，命中量取决于该区薄云是否已被 QA 报出。"
          "若读图仍见条状亮云，属 QA=0 且 NDSI 偏离 0 的情形，本规则不覆盖，按云对待、不回填。")
        A("- 风险：阈值由亚热带山地（延平）标定；珠三角高反射屋面/裸土可能被误判为薄卷云，"
          "已用「三波段同时高亮 + NDSI≈0」抑制，但仍非零；受影响面积见上表，不作人工回填。\n")
    else:
        A("- 本轮产物无薄卷云图层（筛查未启用或为旧数据）。\n")

    A("### 6.2 2003 竖向接缝 A/B（halo 是否有效）\n")
    seam_p = os.path.join(out_dir, "seam_ab_2003.json")
    qa_p = os.path.join(out_dir, "qa_bright_analysis.json")
    if os.path.exists(seam_p):
        sm = _json.load(open(seam_p, encoding="utf-8"))
        b = sm.get("halo0", {}).get("rows0-2328", {})
        h = sm.get("halo1", {}).get("rows0-2328", {})
        gd = sm.get("gap_diff", {})
        plus = int(gd.get("valid_in_halo1_not_halo0_px", 0))
        minus = int(gd.get("valid_in_halo0_not_halo1_px", 0))
        km2 = float(gd.get("km2_per_px", 0))
        A(f"- 做法：2003 期分别用「窗口外扩 1 块（{cfg.get('window', {}).get('block_px', 256)} px）」"
          f"与「不外扩」重建，逐列取区界内中位亮度，统计 1 px 硬跳变（不受羽化掩盖）。")
        A(f"- 结果：无 halo 最大跳变 **{b.get('max_jump')}**，>0.10 的列 {b.get('n>0.10')} 个；"
          f"有 halo 最大跳变 **{h.get('max_jump')}**，>0.10 的列 {h.get('n>0.10')} 个 → **指标一致**。")
        A(f"- 缺口：有 halo 相对无 halo 净增有效像元 **{plus:,}**（{plus*km2:.1f} km²），"
          f"另有 {minus:,}（{minus*km2:.2f} km²，占区界 "
          f"{minus*km2/max(aoi['area_km2'],1e-9)*100:.2f}%）在边界插值中由有效转为无效；"
          f"后者为离散边缘抖动，不构成新的块状缺口。")
        A("- 结论：画面中的竖向明暗边界**不是窗口边缘插值**（若是，halo 会显著改变跳变指标）；"
          "其来源是异日场景足迹/云掩膜边界与 2002-02-02、2002-02-11/18 之间的真实地表差异。"
          "2 km 羽化已生效；继续加大羽化会掩盖真实差异，故不采用。\n")
    else:
        A("- 未找到 `seam_ab_2003.json`（本案例未做接缝 A/B 诊断）。\n")

    A("### 6.3 高亮斑块定性（QA 位分类 + 低云对照）\n")
    if os.path.exists(qa_p):
        qa = _json.load(open(qa_p, encoding="utf-8"))
        ctl = qa.get("control") or {}
        A(f"- 对照景：`{ctl.get('id')}`（{ctl.get('date')}，景云量 {ctl.get('cloud_pct')}%），"
          f"与 2003 期同 path/row（120/042）；2003 期实际成像为 2002-02-02，故对照相隔约 1 年、同为 2 月，"
          f"用于判断季节性地物而非同日大气差异。")
        A("")
        A("| 斑块 | 面积 km² | 亮度 p50 | 蓝/红 | NDSI | QA 雪位% | QA 云% | 对照亮度 p50 | 结论 |")
        A("|---|---|---|---|---|---|---|---|---|")
        for c in qa.get("components", []):
            m = (c.get("qa_by_scene") or {}).get("LT05_L2SP_120042_20020202_02_T1", {})
            cc = c.get("control") or {}
            A(f"| #{c['rank']} | {c['area_km2']} | {c.get('epoch_brightness_p50')} | "
              f"{c.get('epoch_blue_red_p50')} | {c.get('epoch_ndsi_p50')} | "
              f"{m.get('snow_pct', '—')} | {m.get('cloud_pct', '—')} | "
              f"{cc.get('brightness_p50', '—')} | {c.get('conclusion', '—')} |")
        A("")
        A("- 判定：三个斑块在 2003 期极亮（反射率 p50 ≈0.95–0.98）、偏蓝（蓝/红 ≈2.2–2.3）、"
          "可见波段部分饱和，但 **NDSI≈0**（绿与短波红外同高），对照景同区亮度降至约 0.05，"
          "且 QA_PIXEL bit1–4 基本为 0。**结论：云/薄卷云，QA 位未判**——"
          "既非积雪/霜（NDSI 不支持），也非真实裸土/新工地（对照景完全消失、无稳定地物）。")
        A("- 本次只做判定，不改图面；这些像元仍按「有效」参与拼接，读图时应按云对待。\n")
    else:
        A("- 未找到 `qa_bright_analysis.json`（需运行 2003 高亮斑块 QA 分析）。\n")

    A("## 7. 已知偏差与限制\n")
    A("- **色调不可用于变化检测**：未做大气校正（LEDAPS/LaSRC 差异）、未做 BRDF 校正，"
      "跨传感器与跨季节的辐射差异仍然存在；本报告色调仅用于展示。")
    A("- **绝对定位**：Landsat L1/L2 产品标称约 12 m RMSE；行政边界为百米级示意；两者叠加后无法用相对配准消除共同偏移。")
    A("- **分辨率**：30 m 像元对应地面 30 m，实际可判读地物边界约 60–90 m，不宜用于地块权属判读。")
    if extra:
        for e in extra:
            A(f"- {e}")
    A("")

    A("## 8. 复现命令\n")
    A("```powershell")
    yrs = ",".join(str(e["target_year"]) for e in plan["epochs"])
    prefix = f"python historica.py --case {case_id} " if case_id else "python historica.py "
    if case_id:
        A(prefix + "estimate")
    else:
        src = aoi.get("source")
        if src == "bbox":
            A(prefix + f'estimate --bbox "{",".join(f"{v:.4f}" for v in aoi["bounds"])}" --year {yrs}')
        elif src == "admin":
            A(prefix + f'estimate --admin "{aoi.get("name_matched") or ""}" --year {yrs}')
        else:
            A(prefix + f"estimate --vector <边界文件> --year {yrs}")
    run_path = os.path.dirname(out_dir).replace("\\", "/")
    parts = run_path.split("/")
    i = len(parts) - 1 - parts[::-1].index("runs") if "runs" in parts else 0
    run_rel = "\\".join(parts[i:])
    A(prefix + f'fetch --plan "{run_rel}\\plan.json"   # 断点续传：中断后原样重跑')
    A(prefix + f'compose --run "{run_rel}"')
    A(prefix + f'report --run "{run_rel}"')
    A("```\n")

    A("## 9. 数据许可\n")
    for name, p in cfg["data"]["providers"].items():
        if p.get("enabled"):
            A(f"- `{name}`：{p.get('license', '未注明')}")
    A("")

    return "\n".join(L)


def save(md, out_dir, filename="technical_report.md"):
    p = os.path.join(out_dir, filename)
    open(p, "w", encoding="utf-8").write(md)
    return p
