import json, os, subprocess, sys, tempfile, unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hist import years, geo, estimate as hest, fetch as hfetch, tone, align  # noqa: E402


class TestYears(unittest.TestCase):
    def test_list(self):
        self.assertEqual(years.expand_years("1993,2004,2013,2025"), [1993, 2004, 2013, 2025])

    def test_list_sorted_unique(self):
        self.assertEqual(years.expand_years("2025, 1993,2025"), [1993, 2025])

    def test_range_step(self):
        self.assertEqual(years.expand_range("1990-2025", 5),
                         [1990, 1995, 2000, 2005, 2010, 2015, 2020, 2025])

    def test_range_no_tail_duplicate(self):
        self.assertEqual(years.expand_range("1990-1995", 5), [1990, 1995])

    def test_bad(self):
        with self.assertRaises(years.YearSpecError):
            years.expand_years("1993", "1990-2025")
        with self.assertRaises(years.YearSpecError):
            years.parse_year_list("93")
        with self.assertRaises(years.YearSpecError):
            years.parse_range("2025-1990")

    def test_window_and_date_score(self):
        self.assertEqual(years.year_window(1993, 1), (1992, 1994))
        self.assertEqual(years.date_distance_score(1993, 1993, 1), 1.0)
        self.assertLess(years.date_distance_score(1994, 1993, 1), 1.0)


class TestGeo(unittest.TestCase):
    def test_bbox(self):
        b = geo.parse_bbox("113.94,22.45,114.29,22.64")
        self.assertEqual(tuple(round(v, 2) for v in b.bounds), (113.94, 22.45, 114.29, 22.64))

    def test_bbox_invalid(self):
        for s in ("1,2,3", "114,22,113,23", "1,2,3,4,5"):
            with self.assertRaises(geo.AOIError):
                geo.parse_bbox(s)

    def test_wkt_and_area(self):
        g = geo.parse_wkt("POLYGON((117.85 26.25,118.67 26.25,118.67 26.87,117.85 26.87,117.85 26.25))")
        self.assertGreater(geo._area_km2(g), 2000)
        with self.assertRaises(geo.AOIError):
            geo.parse_wkt("POINT(1 2)")

    def test_grid(self):
        g = geo.parse_bbox("117.8481,26.2434,118.6731,26.8664")
        grid = geo.build_grid(g, {"grid": {"crs": "auto-utm", "res": 30, "pad_m": 0}})
        self.assertIn("EPSG:32650", grid["crs"])
        self.assertGreater(grid["ncols"], 2000)
        self.assertEqual(grid["transform"][0], 30)
        self.assertEqual(grid["ncols"], int(round((grid["x1"] - grid["x0"]) / 30)))

    def test_coverage_frac(self):
        from shapely.geometry import box
        t = box(0, 0, 10, 10)
        self.assertAlmostEqual(geo.coverage_frac(t, [box(0, 0, 10, 5)]), 0.5, places=6)

    def test_clip_bounds_to_footprint(self):
        from shapely.geometry import box, mapping
        fp = mapping(box(117.8481, 26.2434, 118.30, 26.8664))  # 只覆盖西半
        nb = geo.clip_bounds_to_footprint((117.8481, 26.2434, 118.6731, 26.8664), fp)
        self.assertAlmostEqual(nb[2], 118.30, places=6)
        self.assertIsNone(geo.clip_bounds_to_footprint((120, 30, 121, 31), fp))

    def test_load_vector_geojson(self):
        from shapely.geometry import box, mapping
        fc = {"type": "FeatureCollection",
              "features": [{"type": "Feature", "properties": {},
                            "geometry": mapping(box(117.85, 26.25, 118.67, 26.87))}]}
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "aoi.geojson")
            json.dump(fc, open(p, "w", encoding="utf-8"))
            g = geo.load_vector(p)
            self.assertEqual(g.geom_type, "Polygon")
            self.assertGreater(geo._area_km2(g), 2000)
            with self.assertRaises(geo.AOIError):
                geo.load_vector(os.path.join(td, "nope.shp"))


def _synthetic_plan():
    from shapely.geometry import box, mapping
    g = box(117.8481, 26.2434, 118.6731, 26.8664)
    grid = geo.build_grid(g, {"grid": {"crs": "auto-utm", "res": 30, "pad_m": 0}})
    scene = {"id": "LC09_L2SP_120042_20251226_02_T1", "date": "2025-12-26", "cloud": 1.0, "sun": 30.0,
             "epsg": 32650, "width": 7701, "height": 7551,
             "transform": [30, 0, 450000, 0, -30, 3130000], "red_href": None}
    return {
        "aoi": {"source": "test", "source_detail": "synthetic", "precision": "none",
                "area_km2": 2652, "name_matched": "延平区", "bounds": list(g.bounds),
                "boundary_geojson": mapping(g), "grid": grid},
        "epochs": [{
            "target_year": 2025, "status": "ok", "query_window": [2024, 2026], "n_candidates": 3,
            "chosen": {"date": "2025-12-26", "coverage": 0.71, "cloud": 1.0, "sun": 30.0,
                       "n_scenes": 1, "scenes": [scene["id"]], "scenes_meta": [scene]},
            "alternatives": [],
        }],
    }


class TestEstimate(unittest.TestCase):
    def test_scene_window_math(self):
        from shapely.geometry import box
        meta = _synthetic_plan()["epochs"][0]["chosen"]["scenes_meta"][0]
        w = hest._scene_window(meta, (117.8481, 26.2434, 118.6731, 26.8664), 30)
        self.assertTrue(w["inside"])
        self.assertGreater(w["px"], 0)
        self.assertEqual(w["w_aligned"] % 256, 0)

    def test_estimate_plan_offline(self):
        est = hest.estimate_plan(_synthetic_plan(), _cfg(), speed=0.21, do_probe=False)
        self.assertEqual(est["speed_mbps"], 0.21)
        self.assertGreater(est["total"]["payload_mb"], 0)
        self.assertGreater(est["total"]["total_s"], est["total"]["net_s"])
        self.assertIn("年份", hest.fmt_table(est))

    def test_compare(self):
        est = hest.estimate_plan(_synthetic_plan(), _cfg(), speed=0.21, do_probe=False)
        rows = hest.compare(est, {"per_epoch": [{"target_year": 2025, "elapsed_s": est["per_epoch"][0]["total_s"] * 1.5}]})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["over_warn"])


def _cfg():
    from hist import config
    cfg = config.load()
    cfg["_cache_dir"] = os.path.join(tempfile.gettempdir(), "historica_test_cache")
    return cfg


class TestProbeWatchdogCirrus(unittest.TestCase):
    def test_pct(self):
        self.assertAlmostEqual(hest._pct([1, 2, 3, 4], 10), 1.3, places=6)
        self.assertAlmostEqual(hest._pct([1, 2, 3, 4], 50), 2.5, places=6)
        self.assertAlmostEqual(hest._pct([1, 2, 3, 4], 90), 3.7, places=6)
        self.assertIsNone(hest._pct([], 10))

    def test_watchdog_triggers_after_three_low_blocks(self):
        cfg = _cfg()
        cfg["fetch"]["watchdog"] = {"enabled": True, "consecutive_below": 3,
                                    "below_fraction": 0.5, "min_sample_mb": 0.1}
        wd = hfetch.Watchdog(cfg, 1.0, 0.5)
        self.assertTrue(wd.enabled)
        self.assertAlmostEqual(wd.threshold, 0.5)
        with self.assertRaises(hfetch.WatchdogPause):
            for _ in range(3):
                wd.note(1_000_000, 5.0, "low")   # 0.5 MB / 5 s = 0.1 MB/s < 0.5
        self.assertTrue(wd.summary()["triggered"])

    def test_watchdog_resets_on_fast_block(self):
        cfg = _cfg()
        cfg["fetch"]["watchdog"] = {"enabled": True, "consecutive_below": 3,
                                    "below_fraction": 0.5, "min_sample_mb": 0.1}
        wd = hfetch.Watchdog(cfg, 1.0, 0.5)
        wd.note(1_000_000, 5.0, "low")
        wd.note(1_000_000, 1.0, "fast")          # 0.5 MB/s >= 0.5 → 计数归零
        wd.note(1_000_000, 5.0, "low")
        wd.note(1_000_000, 5.0, "low")
        self.assertFalse(wd.paused)

    def test_watchdog_disabled_without_p10(self):
        wd = hfetch.Watchdog(_cfg(), None, 0.5)
        self.assertFalse(wd.enabled)
        wd.note(1_000_000, 100.0, "x")
        self.assertFalse(wd.paused)

    def test_thin_cirrus_mask(self):
        cfg = _cfg()
        refl = [np.full((2, 2), 0.6, "float32"),
                np.full((2, 2), 0.6, "float32"),
                np.full((2, 2), 0.7, "float32")]
        sw = np.full((2, 2), 0.6, "float32")      # NDSI≈0、三波段均亮
        valid = np.ones((2, 2), bool)
        m = hfetch.thin_cirrus_mask(refl, sw, valid, cfg)
        self.assertTrue(m.all())
        # 雪：绿高、SWIR 低 → NDSI 高，不应判薄卷云
        sw2 = np.full((2, 2), 0.1, "float32")
        self.assertFalse(hfetch.thin_cirrus_mask(refl, sw2, valid, cfg).any())
        # 暗地物：不应判薄卷云
        dark = [np.full((2, 2), 0.10, "float32")] * 3
        self.assertFalse(hfetch.thin_cirrus_mask(dark, sw, valid, cfg).any())
        # 无效像元（QA 云位/填充）不判
        self.assertFalse(hfetch.thin_cirrus_mask(refl, sw, np.zeros((2, 2), bool), cfg).any())

    def test_estimate_outputs_interval_fields(self):
        est = hest.estimate_plan(_synthetic_plan(), _cfg(), speed=0.21, do_probe=False)
        self.assertIn("speed_p10_mbps", est)
        self.assertIn("advisory", est)
        self.assertIn("仅用于提醒", est["advisory"]["purpose"])
        self.assertIn("区间", hest.fmt_table(est))


class TestCache(unittest.TestCase):
    def test_key_stable(self):
        a = hfetch._cache_file("/tmp/c", "ID1", "red", [1, 2, 3, 4], 32650)
        b = hfetch._cache_file("/tmp/c", "ID1", "red", [1, 2, 3, 4], 32650)
        c = hfetch._cache_file("/tmp/c", "ID1", "red", [1, 2, 3, 5], 32650)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_npz_roundtrip(self):
        p = hfetch._cache_file(tempfile.gettempdir(), "ID2", "green", [0, 0, 1, 1], 32650)
        arr = np.arange(12, dtype="uint16").reshape(3, 4)
        np.savez_compressed(p, arr=arr, x0=0, y0=1, res=30, epsg=32650)
        z = np.load(p)
        np.testing.assert_array_equal(z["arr"], arr)


class _FakeItem:
    def __init__(self, geom):
        from shapely.geometry import mapping
        self.geometry = mapping(geom)
        self.id = "fake"
        self.properties = {}

    def datetime_str(self):
        return ""


class TestSelector(unittest.TestCase):
    def test_mixed_cap_max_scenes(self):
        from datetime import date
        from shapely.geometry import box, mapping
        from hist import selector
        target = box(0, 0, 10, 10)
        g0 = [_FakeItem(box(0, 0, 10, 5)) for _ in range(3)]
        g1 = [_FakeItem(box(0, 5, 10, 10)) for _ in range(3)]
        groups = [
            {"date": "2000-01-01", "date_obj": date(2000, 1, 1), "items": g0,
             "scenes": ["a", "b", "c"], "scenes_meta": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
             "cloud": 5, "sun": 40},
            {"date": "2000-01-10", "date_obj": date(2000, 1, 10), "items": g1,
             "scenes": ["d", "e", "f"], "scenes_meta": [{"id": "d"}, {"id": "e"}, {"id": "f"}],
             "cloud": 5, "sun": 40},
        ]
        m = selector._mixed_option(groups, target, 32, max_scenes=3)
        self.assertLessEqual(len(m["scenes"]), 3)
        self.assertGreater(m["coverage"], 0.0)

    def test_ranking_has_alternatives(self):
        from hist import selector
        sel = {"weights": {"coverage": .4, "cloud": .25, "sun": .15, "date": .2},
               "sun_target_deg": 45, "min_coverage": .6, "target_coverage": .995,
               "mixed_max_window_days": 32, "max_scenes_per_epoch": 4,
               "allow_mixed_by_default": True}
        from shapely.geometry import box
        target = box(0, 0, 10, 10)
        items = [_FakeItem(box(0, 0, 10, 6)), _FakeItem(box(0, 4, 10, 10))]
        items[0].properties = {"eo:cloud_cover": 5, "view:sun_elevation": 40}
        items[1].properties = {"eo:cloud_cover": 30, "view:sun_elevation": 40}
        # 同一天：两块并集满覆盖
        items[1].datetime = items[0].datetime = __import__("datetime").datetime(2000, 1, 1)
        cfg = {"selection": sel}
        res = selector.select_epoch(2000, items, target, cfg, 1, None)
        self.assertTrue(res["alternatives"])
        self.assertIn("coverage", res["alternatives"][0])


class TestSeason(unittest.TestCase):
    def test_circular_distance_across_year(self):
        from hist import season
        self.assertEqual(season.circ_dist(1, 365), 1.0)
        self.assertEqual(season.circ_dist(350, 10), 25.0)  # 365 天制，非 360

    def test_circular_mean_near_year_end(self):
        from hist import season
        self.assertLessEqual(season.circ_dist(season.circ_mean([355, 5, 2]), 1), 5)

    def test_window_membership(self):
        from hist import season
        self.assertTrue(season.in_window(300, 281, 45))
        self.assertFalse(season.in_window(53, 281, 45))
        self.assertTrue(season.in_window(53, 281, 0))  # 0 = 关闭约束

    def test_describe_matches_v10_huaxi(self):
        from datetime import date
        from hist import season
        d = season.describe([date(1993, 10, 8), date(2003, 2, 22),
                             date(2013, 9, 29), date(2024, 10, 29)])
        self.assertEqual(d["std_days"], 101.2)   # 与 v1.0 报告同口径
        self.assertEqual(d["max_diff_days"], 250)
        self.assertEqual(d["months"], [2, 9, 10])

    def test_season_score_shape(self):
        from hist import season
        self.assertEqual(season.season_score(281, 281, 45), 1.0)
        self.assertAlmostEqual(season.season_score(326, 281, 45), 0.5)
        self.assertEqual(season.season_score(53, 281, 45), 0.0)

    def test_select_epoch_respects_window(self):
        from datetime import datetime
        from shapely.geometry import box
        from hist import selector
        cfg = {"selection": {
            "weights": {"coverage": .4, "cloud": .25, "sun": .15, "date": .2, "season": .15},
            "sun_target_deg": 45, "min_coverage": .6, "target_coverage": .995,
            "mixed_max_window_days": 32, "max_scenes_per_epoch": 4,
            "allow_mixed_by_default": True}}
        target = box(0, 0, 10, 10)
        a, b = _FakeItem(box(0, 0, 10, 10)), _FakeItem(box(0, 0, 10, 10))
        a.properties = {"eo:cloud_cover": 10, "view:sun_elevation": 40}
        b.properties = {"eo:cloud_cover": 0, "view:sun_elevation": 45}
        a.datetime = datetime(2000, 1, 1)    # 窗口内
        b.datetime = datetime(2000, 4, 25)   # 窗口外，云量更优
        res = selector.select_epoch(2000, [a, b], target, cfg, 1, None,
                                    season={"center": 1, "half": 45})
        self.assertEqual(res["chosen"]["date"], "2000-01-01")
        self.assertEqual(res["n_in_window"], 1)
        self.assertEqual(res["n_total_groups"], 2)


class TestToneAlign(unittest.TestCase):
    def test_tone_range(self):
        rgb = np.random.rand(40, 40, 3).astype("float32") * 0.3
        out = tone.apply_tone(rgb, np.ones((40, 40), bool), _cfg())
        self.assertLessEqual(out.max(), 1.0)
        self.assertGreaterEqual(out.min(), 0.0)

    def test_align_zero_shift(self):
        a = np.random.rand(120, 120).astype("float32")
        rgb = np.dstack([a, a, a])
        res = align.verify_pairs([(2000, rgb, np.ones((120, 120), bool)),
                                  (2001, rgb, np.ones((120, 120), bool))])
        self.assertTrue(all(r["dx"] == 0 and r["dy"] == 0 for r in res))


class TestCliDryRun(unittest.TestCase):
    def test_end_to_end_dryrun(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, "plan.json")
            json.dump(_synthetic_plan(), open(pf, "w", encoding="utf-8"), ensure_ascii=False)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "historica.py"), "estimate",
                                "--plan", pf, "--speed", "0.21", "--no-probe", "--out", td],
                               cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("合计", r.stdout)
            self.assertTrue(os.listdir(os.path.join(td, "estimate")))

    def test_case_defaults_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, "plan.json")
            json.dump(_synthetic_plan(), open(pf, "w", encoding="utf-8"), ensure_ascii=False)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "historica.py"), "--case", "prd_full",
                                "estimate", "--plan", pf, "--speed", "0.21", "--no-probe", "--out", td],
                               cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("合计", r.stdout)


if __name__ == "__main__":
    unittest.main()
