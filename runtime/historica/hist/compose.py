"""出图：单期图 + N×M 多期对比图（红虚线边界、统一比例尺与指北针、缺测斜线标注）。"""
import os

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path
from matplotlib.patches import Rectangle

from pyproj import Transformer
from shapely.geometry import Polygon


def _hex(c):
    return tuple(int(c.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _setup_font(cfg):
    name = (cfg.get("compose") or {}).get("font") or "Microsoft YaHei"
    plt.rcParams["font.sans-serif"] = [name, "Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["hatch.color"] = "#9aa0a6"
    plt.rcParams["hatch.linewidth"] = 0.6


def _geom_in_grid(geom, grid):
    tr = Transformer.from_crs("EPSG:4326", grid["crs"], always_xy=True)

    def f(x, y, z=None):
        return tr.transform(x, y)
    from shapely.ops import transform as shp_t
    return shp_t(f, geom)


def _inside_mask(geom_grid, grid, step=2):
    """把边界多边形栅格化成边界内掩膜（按 step 抽稀，够画图用）。"""
    a = grid["transform"][0]
    xs = grid["x0"] + (np.arange(0, grid["ncols"], step) + 0.5) * a
    ys = grid["y1"] - (np.arange(0, grid["nrows"], step) + 0.5) * a
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    polys = geom_grid.geoms if geom_grid.geom_type == "MultiPolygon" else [geom_grid]
    out = np.zeros(len(pts), dtype=bool)
    for p in polys:
        out |= Path(np.asarray(p.exterior.coords)).contains_points(pts)
    return out.reshape(gx.shape)


def _panel(ax, ep, grid, boundary_grid, inside, cfg, show_missing=True):
    ext = (grid["x0"], grid["x1"], grid["y0"], grid["y1"])
    ax.imshow(ep["rgb"], interpolation="bilinear", extent=ext, origin="upper")
    ax.set_xlim(grid["x0"], grid["x1"]); ax.set_ylim(grid["y0"], grid["y1"])
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor("#d9d4c9"); s.set_linewidth(0.8)

    # 边界：红虚线
    polys = boundary_grid.geoms if boundary_grid.geom_type == "MultiPolygon" else [boundary_grid]
    for p in polys:
        x, y = p.exterior.xy
        ax.plot(x, y, color=cfg["compose"]["boundary_color"], lw=1.1,
                ls=(0, (6, 3)), zorder=5)
        for hole in p.interiors:
            hx, hy = hole.xy
            ax.plot(hx, hy, color=cfg["compose"]["boundary_color"], lw=0.8,
                    ls=(0, (4, 3)), alpha=0.8, zorder=5)

    # 缺测：斜线 + 面积（区分「区内无影像」与「云/阴影/暗像元」）
    miss_km2 = 0.0
    if show_missing:
        valid = np.zeros(inside.shape, dtype=bool)
        step = 2
        valid = ep["mask"][::step, ::step][:inside.shape[0], :inside.shape[1]]
        miss = inside & ~valid
        if miss.any():
            px_km2 = (grid["res"] * step / 1000.0) ** 2
            miss_km2 = float(miss.sum()) * px_km2
            inside_km2 = float(inside.sum()) * px_km2
            gap_km2 = max(0.0, (1.0 - float(ep.get("coverage", 1.0))) * inside_km2)
            other_km2 = max(0.0, miss_km2 - gap_km2)
            hgt = np.where(inside, ~valid, False)
            xs = grid["x0"] + (np.arange(hgt.shape[1]) + 0.5) * grid["res"] * step
            ys = grid["y1"] - (np.arange(hgt.shape[0]) + 0.5) * grid["res"] * step
            ax.contourf(xs, ys, hgt.astype(float),
                        levels=[0.5, 1.5], colors="none", hatches=["////"],
                        zorder=6)
            ax.text(0.02, 0.02,
                    f"不可读 {miss_km2:,.0f} km²（无影像 {gap_km2:,.0f} / 云·阴影·暗像元 {other_km2:,.0f}）",
                    transform=ax.transAxes, fontsize=6.0,
                    color="#111", zorder=9,
                    bbox=dict(fc="#ffffffcc", ec="none", pad=1.6))

    # 薄卷云（QA 未判）：单独颜色细网点，与云/阴影斜线区分
    cir_km2 = 0.0
    if show_missing and ep.get("cirrus") is not None:
        step = 2
        cir = ep["cirrus"][::step, ::step][:inside.shape[0], :inside.shape[1]]
        cir = inside & cir
        if cir.any():
            px_km2 = (grid["res"] * step / 1000.0) ** 2
            cir_km2 = float(cir.sum()) * px_km2
            xs = grid["x0"] + (np.arange(cir.shape[1]) + 0.5) * grid["res"] * step
            ys = grid["y1"] - (np.arange(cir.shape[0]) + 0.5) * grid["res"] * step
            cs = ax.contourf(xs, ys, cir.astype(float), levels=[0.5, 1.5],
                             colors="none", hatches=["...."], zorder=7)
            try:
                cs.set_edgecolor("#c2185b")
            except Exception:
                pass
            ax.text(0.02, 0.085,
                    f"薄卷云 {cir_km2:,.0f} km²（已从可读扣除）",
                    transform=ax.transAxes, fontsize=6.0, color="#c2185b", zorder=10,
                    bbox=dict(fc="#ffffffd9", ec="none", pad=1.6))

    # 比例尺（自适应：条长不超过面板宽度的 1/3）
    cands = [1, 2, 5, 10, 20, 50, 100]
    km = float(cands[0])
    for c in cands:
        if c * 1000 / grid["res"] / grid["ncols"] <= 0.33:
            km = float(c)
        else:
            break
    bar_px = km * 1000 / grid["res"]
    x0, y0 = 0.06, 0.07
    ax.plot([x0, x0 + bar_px / grid["ncols"]], [y0, y0], transform=ax.transAxes,
            color="white", lw=1.8, solid_capstyle="butt", zorder=7)
    ax.plot([x0, x0 + bar_px / grid["ncols"]], [y0, y0], transform=ax.transAxes,
            color="#222", lw=0.8, zorder=7)
    ax.text(x0, y0 + 0.012, f"0        {km:.0f} km", transform=ax.transAxes, fontsize=6,
            color="white", zorder=7)
    ax.text(x0, y0 + 0.012, f"0        {km:.0f} km", transform=ax.transAxes, fontsize=6,
            color="#222", zorder=8, alpha=0.95)

    # 指北针
    ax.annotate("N", xy=(0.93, 0.90), xytext=(0.93, 0.78), xycoords="axes fraction",
                ha="center", va="center", fontsize=6.5, color="white", zorder=8,
                arrowprops=dict(arrowstyle="-|>", color="white", lw=1.0))
    ax.annotate("N", xy=(0.93, 0.90), xytext=(0.93, 0.78), xycoords="axes fraction",
                ha="center", va="center", fontsize=6, color="#222", zorder=9,
                arrowprops=dict(arrowstyle="-|>", color="#222", lw=0.7))

    vf = ep.get("valid_frac")
    vf_txt = f"  ·  可读 {vf*100:.1f}%" if vf is not None else ""
    ax.set_title(f"{ep['year']}  {ep['date']}  ·  区界覆盖 {ep['coverage']*100:.1f}%{vf_txt}",
                 fontsize=8.6, color=cfg["compose"]["ink"], pad=5)


def _layout(n, max_cols):
    cols = min(max_cols, n)
    rows = int(np.ceil(n / cols))
    return rows, cols


def compose_series(epochs, plan, cfg, out_path, title=None, align_summary=None):
    _setup_font(cfg)
    aoi = plan["aoi"]
    grid = aoi["grid"]
    from shapely.geometry import shape as shp_shape
    geom = shp_shape(aoi["boundary_geojson"])
    boundary_grid = _geom_in_grid(geom, grid)
    inside = _inside_mask(boundary_grid, grid)

    n = len(epochs)
    rows, cols = _layout(n, cfg["compose"]["max_cols"])
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 6.4, rows * 4.2), dpi=cfg["compose"]["dpi"])
    axes = np.atleast_1d(axes).ravel()
    for ax, ep in zip(axes, epochs):
        _panel(ax, ep, grid, boundary_grid, inside, cfg)
    for ax in axes[n:]:
        ax.axis("off")

    ttl = title or f"{aoi.get('name_matched') or '研究区'} 历史卫星影像对比"
    fig.suptitle(ttl, fontsize=12.5, y=0.995, color=cfg["compose"]["ink"])
    fig.patch.set_facecolor(cfg["compose"]["background"])
    footer = (f"数据：Landsat Collection 2 L2 / Planetary Computer  ·  投影 {grid['crs']}  "
              f"·  {grid['res']:.0f} m  ·  边界：{aoi['source']}（红虚线）  ·  "
              "色调仅用于展示，不可用于变化检测")
    if align_summary:
        n = align_summary.get("pairs", 0)
        locked = align_summary.get("locked", 0)
        footer += (f"\n配准：{n} 窗口/期对，锁定 {locked} / 未锁定 {n - locked}"
                   "；未锁定不计位移，不用于几何校正")
    fig.text(0.5, 0.004, footer, ha="center", va="bottom", fontsize=6.0,
             color="#4a4a4a", linespacing=1.35)
    fig.tight_layout(rect=[0, 0.05, 1, 0.965])
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


def compose_single(ep, plan, cfg, out_path):
    _setup_font(cfg)
    aoi = plan["aoi"]; grid = aoi["grid"]
    from shapely.geometry import shape as shp_shape
    geom = shp_shape(aoi["boundary_geojson"])
    boundary_grid = _geom_in_grid(geom, grid)
    inside = _inside_mask(boundary_grid, grid)
    fig, ax = plt.subplots(figsize=(8.2, 7.2), dpi=cfg["compose"]["dpi"])
    _panel(ax, ep, grid, boundary_grid, inside, cfg)
    fig.patch.set_facecolor(cfg["compose"]["background"])
    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path
