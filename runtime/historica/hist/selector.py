"""选景器：按日期分组 → 足迹并集 → 五项打分（覆盖/云量/太阳高度/日期离散/季节一致）→ 候选排名。"""
from datetime import date

from shapely.geometry import shape
from shapely.ops import unary_union

from . import season as hseason
from . import stacq
from .years import year_window, date_distance_score


def _sun_score(sun, target):
    if sun is None:
        return 0.5
    return max(0.0, 1.0 - abs(float(sun) - float(target)) / 90.0)


def _scene_meta(it):
    """落到 plan.json 里的最小场景元数据（不依赖 pystac 对象，便于二次运行）。"""
    from shapely.geometry import mapping
    m = {"id": it.id, "date": it.datetime.strftime("%Y-%m-%d"),
         "cloud": float(it.properties.get("eo:cloud_cover", 100)),
         "sun": it.properties.get("view:sun_elevation"),
         "red_href": None,
         "footprint": mapping(shape(it.geometry)) if it.geometry else None,
         "file_size": None}
    try:
        m["red_href"] = stacq.asset_href(it, "red")
        a = it.assets.get("red")
        sz = getattr(a, "extra_fields", {}).get("file:size") or (a.get("file:size") if isinstance(a, dict) else None)
        m["file_size"] = int(sz) if sz else None
    except Exception:
        pass
    try:
        from . import stacq as _s
        g = _s.proj_grid(it)
        m.update({"epsg": g["epsg"], "width": g["width"], "height": g["height"],
                  "transform": g["transform"]})
    except Exception:
        m.update({"epsg": None, "width": None, "height": None, "transform": None})
    return m


def _score(cov, cloud, sun, date_score, w, sun_target, season_score=None):
    """加权综合分；权重和归一化，新增 season 项不会整体抬高/压低分数。

    season_score 为 None（未启用季节约束）时不参与归一化分母。
    """
    num = (float(w.get("coverage", 0.0)) * cov
           + float(w.get("cloud", 0.0)) * (1.0 - min(100.0, cloud) / 100.0)
           + float(w.get("sun", 0.0)) * _sun_score(sun, sun_target)
           + float(w.get("date", 0.0)) * date_score)
    den = sum(float(w.get(k, 0.0)) for k in ("coverage", "cloud", "sun", "date"))
    sw = float(w.get("season", 0.0) or 0.0)
    if sw > 0 and season_score is not None:
        num += sw * season_score
        den += sw
    return num / max(den, 1e-9)


def _item_doy(it):
    return date.fromisoformat(it.datetime.strftime("%Y-%m-%d")).timetuple().tm_yday


def group_by_date(items, target_geom):
    """同一天（可能有相邻轨 1 天内）的场景合并，计算足迹并集覆盖率。"""
    groups = {}
    for it in items:
        d = it.datetime.strftime("%Y-%m-%d")
        g = groups.setdefault(d, {"date": d, "items": []})
        g["items"].append(it)
    out = []
    for d, g in groups.items():
        fps = [shape(i.geometry) for i in g["items"] if i.geometry]
        cov = 0.0
        if fps:
            u = unary_union(fps)
            inter = target_geom.intersection(u)
            if not inter.is_empty:
                cov = inter.area / target_geom.area
        suns = [i.properties.get("view:sun_elevation") for i in g["items"]]
        suns = [float(s) for s in suns if s is not None]
        clouds = [float(i.properties.get("eo:cloud_cover", 100)) for i in g["items"]]
        out.append({
            "date": d,
            "date_obj": date.fromisoformat(d),
            "scenes": [i.id for i in g["items"]],
            "scenes_meta": [_scene_meta(i) for i in g["items"]],
            "items": g["items"],
            "coverage": cov,
            "cloud": max(clouds) if clouds else 100.0,
            "cloud_mean": sum(clouds) / len(clouds) if clouds else 100.0,
            "sun": max(suns) if suns else None,
            "n_scenes": len(g["items"]),
        })
    out.sort(key=lambda x: x["date"])
    return out


def _mixed_option(groups, target_geom, max_window_days, max_scenes=4):
    """跨日期补缺：在允许的时间窗内补齐覆盖率，并记录日期跨度代价。"""
    best = None
    n = len(groups)
    for i in range(n):
        union = None
        scenes_meta, scene_ids = [], []
        for j in range(i, n):
            span = (groups[j]["date_obj"] - groups[i]["date_obj"]).days
            if span > max_window_days:
                break
            if len(scenes_meta) + len(groups[j]["scenes_meta"]) > max_scenes:
                break
            scenes_meta += groups[j]["scenes_meta"]
            scene_ids += groups[j]["scenes"]
            union = unary_union([shape(x.geometry) for x in groups[j]["items"]]) if union is None else \
                unary_union([union] + [shape(x.geometry) for x in groups[j]["items"]])
            cov = target_geom.intersection(union).area / target_geom.area
            if best is None or cov > best["coverage"]:
                best = {
                    "date": groups[i]["date"] + " ~ " + groups[j]["date"],
                    "date_from": groups[i]["date"],
                    "date_to": groups[j]["date"],
                    "span_days": span,
                    "scenes": list(scene_ids),
                    "scenes_meta": list(scenes_meta),
                    "items": [s for g in groups[i:j + 1] for s in g["items"]],
                    "coverage": cov,
                    "cloud": max(g["cloud"] for g in groups[i:j + 1]),
                    "sun": min(g["sun"] for g in groups[i:j + 1] if g["sun"] is not None) if any(g["sun"] is not None for g in groups[i:j + 1]) else None,
                }
    if best:
        best["mixed"] = True
    return best


def select_epoch(target_year, items, target_geom, cfg, window_half=1, prefer_date=None,
                 season=None):
    """单期选景。season = {"center": doy, "half": days} 时只在该窗口内挑，并加季节一致性项。"""
    sel = cfg["selection"]
    w = sel["weights"]
    all_groups = group_by_date(items, target_geom)
    groups = all_groups
    if season and season.get("center") is not None and float(season.get("half") or 0) > 0:
        groups = [g for g in all_groups
                  if hseason.in_window(hseason.doy(g["date_obj"]),
                                       season["center"], season["half"])]
    if not groups:
        return {"target_year": target_year,
                "status": "no-data" if not all_groups else "no-season-candidate",
                "chosen": None, "alternatives": [],
                "n_in_window": 0, "n_total_groups": len(all_groups)}

    ranked = []
    for g in groups:
        ds = date_distance_score(g["date_obj"].year, target_year, window_half)
        gd = hseason.doy(g["date_obj"])
        sc = _score(g["coverage"], g["cloud"], g["sun"], ds, w, sel["sun_target_deg"],
                    season_score=hseason.season_score(gd, (season or {}).get("center"),
                                                      (season or {}).get("half"))
                    if season else None)
        if g["coverage"] < sel["min_coverage"]:
            sc *= 0.5
        ranked.append({**g, "date_score": ds, "doy": gd, "score": sc})
    ranked.sort(key=lambda x: -x["score"])

    ranked_simple = [{k: v for k, v in r.items() if k != "items" and k != "date_obj"} for r in ranked]

    chosen = None
    if prefer_date:
        for r in ranked:
            if r["date"] == prefer_date:
                chosen = r
                break
    if chosen is None:
        chosen = ranked[0]

    mixed = _mixed_option(groups, target_geom, sel["mixed_max_window_days"],
                          int(sel.get("max_scenes_per_epoch", 4)))
    if mixed:
        ds = min(date_distance_score(g["date_obj"].year, target_year, window_half) for g in groups
                 if g["date"] in (mixed["date_from"], mixed["date_to"]))
        span_penalty = max(0.0, 1.0 - mixed["span_days"] / max(1, sel["mixed_max_window_days"]))
        mid = (hseason.doy(date.fromisoformat(mixed["date_from"]))
               + hseason.doy(date.fromisoformat(mixed["date_to"]))) / 2.0
        mixed["score"] = _score(mixed["coverage"], mixed["cloud"], mixed["sun"], ds * span_penalty,
                                w, sel["sun_target_deg"],
                                season_score=hseason.season_score(mid, season.get("center"),
                                                                  season.get("half"))
                                if season else None)
        mixed["date_score"] = ds
        mixed["doy"] = int(round(mid))
        mixed["n_scenes"] = len(mixed["scenes"])
        ranked_simple.append({**{k: v for k, v in mixed.items() if k not in ("items", "date_obj")}, "mixed": True})

    # 默认策略：单日覆盖不足且跨日期能补齐 ≥5 个百分点时，选跨日期方案
    if (mixed and not prefer_date
            and chosen["coverage"] < sel["target_coverage"]
            and mixed["coverage"] > chosen["coverage"] + 0.05
            and sel.get("allow_mixed_by_default", True)):
        single_alt = {**{k: v for k, v in chosen.items() if k not in ("items", "date_obj")}, "mixed": False}
        chosen = mixed
        ranked_simple.insert(0, single_alt)

    return {
        "target_year": target_year,
        "status": "ok" if chosen["coverage"] >= sel["min_coverage"] else "low-coverage",
        "chosen": {k: v for k, v in chosen.items() if k not in ("items", "date_obj")},
        "mixed_option": {k: v for k, v in mixed.items() if k not in ("items", "date_obj")} if mixed else None,
        "alternatives": ranked_simple[:6],
        "n_in_window": len(groups),
        "n_total_groups": len(all_groups),
    }


def _chosen_date(ch):
    """选中项的代表日期（跨日期补缺取起始日）。"""
    if not ch:
        return None
    return ch.get("date_from") or ch.get("date")


def _chosen_doy(ch):
    d = _chosen_date(ch)
    return hseason.doy(date.fromisoformat(d)) if d else None


def _readable_est(ch):
    """下载前可读覆盖的近似值：几何覆盖 × (1 − 景云量)。【推断】不是 QA 实测。"""
    if not ch:
        return None
    return ch["coverage"] * max(0.0, 1.0 - min(100.0, float(ch["cloud"])) / 100.0)


def select_plan(aoi, targets, cfg, window_half=1, prefer_dates=None, season_window=None,
                log=print):
    """对每个目标年选景，返回 plan dict。

    v1.1 默认启用同季约束（selection.season_window_days，默认 ±45 天）：
    1) 先做一遍不加约束的初选，用各期日期圆均值确定共同窗口中心（不依赖外部气候分区）；
    2) 再在窗口内重选；窗口内没有可用低云景或覆盖低于下限时，不静默放开，
       而是回退并在 plan 里记 cross_season / 跨季天数 / 放开窗口能换回多少可读覆盖（推断值）。
    `season_window=0` 关闭约束，回到 v1.0 行为。
    """
    sel = cfg["selection"]
    sw = int(sel.get("season_window_days", 45) if season_window is None else season_window)
    fallback_min = float(sel.get("season_fallback_min_coverage", sel["min_coverage"]))
    cloud_max = sel["cloud_max"]
    collection = cfg["data"]["provider"]
    plan = {"aoi": {k: aoi[k] for k in ("source", "source_detail", "precision", "area_km2",
                                       "name_matched", "bounds", "grid")},
            "epochs": []}
    from shapely.geometry import mapping
    plan["aoi"]["boundary_geojson"] = mapping(aoi["geometry"])

    # 每个目标年只检索一次，窗口内/窗口外候选都来自同一批结果
    pools, prelim, qwin = {}, {}, {}
    for yr in targets:
        y0, y1 = year_window(yr, window_half)
        qwin[yr] = [y0, y1]
        pools[yr] = stacq.search_items(cfg["data"]["stac_url"], collection,
                                       aoi["bounds"], f"{y0}-01-01/{y1}-12-31", cloud_max)
        prelim[yr] = select_epoch(yr, pools[yr], aoi["geometry"], cfg, window_half,
                                  prefer_dates.get(yr) if prefer_dates else None)

    center = None
    if sw > 0:
        anchors = [_chosen_doy(prelim[yr]["chosen"]) for yr in targets
                   if prelim[yr].get("chosen")]
        anchors = [a for a in anchors if a is not None]
        if anchors:
            center = hseason.circ_mean(anchors)
            log(f"同季约束：窗口 ±{sw} 天，中心日序 {center}（{hseason.mmdd(center)}），"
                f"取自初选 {len(anchors)} 期日期的圆均值")

    for yr in targets:
        pref = prefer_dates.get(yr) if prefer_dates else None
        res_pre = prelim[yr]
        season_meta = {"enforced": sw > 0, "window_days": sw,
                       "center_doy": center, "center_label": hseason.mmdd(center)}
        res = res_pre
        if sw > 0 and center is not None:
            in_items = [it for it in pools[yr]
                        if hseason.in_window(_item_doy(it), center, sw)]
            res_in = select_epoch(yr, in_items, aoi["geometry"], cfg, window_half, pref,
                                  season={"center": center, "half": sw})
            ok_in = (res_in.get("chosen") is not None
                     and res_in["chosen"]["coverage"] >= fallback_min)
            pref_out = (pref and _chosen_date(res_pre["chosen"]) == pref
                        and not hseason.in_window(_chosen_doy(res_pre["chosen"]), center, sw))
            if ok_in and not pref_out:
                res = res_in
                season_meta.update({"in_window": True, "cross_season": False,
                                    "n_in_window_groups": res_in.get("n_in_window", 0),
                                    "n_total_groups": res_in.get("n_total_groups", 0)})
            else:
                res = res_pre
                ch = res.get("chosen")
                off = hseason.circ_dist(_chosen_doy(ch), center) if ch else None
                reason = ("用户 --prefer-date 指定窗口外日期" if pref_out else
                          ("窗口内无候选（候选 %d 景均不在 ±%d 天内）" % (len(pools[yr]), sw)
                           if res_in.get("n_in_window", 0) == 0 else
                           "窗口内最优覆盖 %.1f%% 低于下限 %.0f%%"
                           % ((res_in["chosen"]["coverage"] * 100
                               if res_in.get("chosen") else 0.0), fallback_min * 100)))
                best_in = res_in.get("chosen")
                gain = None
                if ch and best_in:
                    gain = round((_readable_est(ch) - _readable_est(best_in)) * 100, 1)
                elif ch:
                    gain = round(_readable_est(ch) * 100, 1)
                season_meta.update({
                    "in_window": False, "cross_season": True,
                    "cross_season_days": None if off is None else round(off, 1),
                    "fallback_reason": reason,
                    "n_in_window_groups": res_in.get("n_in_window", 0),
                    "n_total_groups": res_in.get("n_total_groups", 0),
                    "in_window_best": ({"date": best_in["date"],
                                        "coverage_pct": round(best_in["coverage"] * 100, 1),
                                        "cloud_pct": round(best_in["cloud"], 1),
                                        "readable_est_pct": round(_readable_est(best_in) * 100, 1)}
                                       if best_in else None),
                    "open_window": ({"date": _chosen_date(ch),
                                     "coverage_pct": round(ch["coverage"] * 100, 1),
                                     "cloud_pct": round(ch["cloud"], 1),
                                     "readable_est_pct": round(_readable_est(ch) * 100, 1),
                                     "gain_readable_est_pp": gain}
                                    if ch else None),
                })
            # 即使留在窗口内，也给出「放开窗口能换回多少」，便于看清同季约束的代价
            ch = res.get("chosen")
            alt = res_pre.get("chosen")
            if (ch and season_meta.get("in_window") and alt
                    and _chosen_date(alt) != _chosen_date(ch)
                    and (_readable_est(alt) - _readable_est(ch)) * 100 > 0.5):
                gain = round((_readable_est(alt) - _readable_est(ch)) * 100, 1)
                season_meta["open_window"] = {
                    "date": _chosen_date(alt),
                    "coverage_pct": round(alt["coverage"] * 100, 1),
                    "cloud_pct": round(alt["cloud"], 1),
                    "readable_est_pct": round(_readable_est(alt) * 100, 1),
                    "gain_readable_est_pp": gain,
                    "out_of_window_days": round(hseason.circ_dist(_chosen_doy(alt), center), 1),
                }
        else:
            season_meta.update({"in_window": None, "cross_season": False,
                                "note": "季节约束已关闭（--season-window 0 或配置为 0）"})
        if res.get("chosen"):
            season_meta["doy"] = _chosen_doy(res["chosen"])
            season_meta["doy_label"] = hseason.mmdd(season_meta["doy"])
            season_meta["offset_days"] = (round(hseason.offset_days(season_meta["doy"], center), 1)
                                          if center is not None else None)
        res["season"] = season_meta
        res["query_window"] = qwin[yr]
        res["n_candidates"] = len(pools[yr])
        plan["epochs"].append(res)
        if res["chosen"]:
            flag = ""
            if season_meta.get("cross_season"):
                flag = f"  [跨季 {season_meta.get('cross_season_days')} 天]"
            log(f"{yr}: {res['status']}  {res['chosen']['date']}  cov={res['chosen']['coverage']*100:.1f}% "
                f"cloud={res['chosen']['cloud']:.1f}%  scenes={res['chosen']['n_scenes']}{flag}")
        else:
            log(f"{yr}: 无可用候选")

    pre_dates = [date.fromisoformat(_chosen_date(prelim[yr]["chosen"]))
                 for yr in targets if prelim[yr].get("chosen")]
    post_dates = [date.fromisoformat(_chosen_date(e["chosen"]))
                  for e in plan["epochs"] if e.get("chosen")]
    plan["season"] = {
        "enabled": sw > 0, "window_days": sw,
        "center_doy": center, "center_label": hseason.mmdd(center),
        "center_source": "初选各期日期的圆均值（不引入外部气候分区，任何地区可复现）",
        "prelock": hseason.describe(pre_dates),
        "postlock": hseason.describe(post_dates),
        "cross_season_epochs": [e["target_year"] for e in plan["epochs"]
                                if (e.get("season") or {}).get("cross_season")],
        "note": ("默认同季：四期在同一个 ±%d 天窗口内选景；窗口内无可用低云景时回退并标注，"
                 "不静默放开。" % sw) if sw > 0 else "季节约束已关闭，可能跨季。",
    }
    return plan
