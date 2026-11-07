"""色调管线：从 v1 render4.py 原样移植（DOS → 白平衡 → 固定拉伸 → gamma → 锚定 → 对比度 → 饱和 → 均值）。"""
import numpy as np


def _lum(rgb):
    return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]


def apply_tone(rgb, mask, cfg):
    """输入 reflectance float32 (H,W,3)，返回 display float32 0..1。"""
    t = cfg["tone"]
    x = np.clip(rgb.astype("float32"), 0, None)
    m = mask
    if m.sum() == 0:
        m = np.ones_like(m, dtype=bool)

    # 1) DOS：逐波段减去暗像元分位数
    p = t["dos_percentile"]
    for k in range(3):
        dosv = np.nanpercentile(x[:, :, k][m], p)
        x[:, :, k] -= dosv
    x = np.clip(x, 0, None)

    # 2) 白平衡
    wb = np.asarray(t["wb_target"], dtype="float32")
    x *= wb[None, None, :]

    # 3) 固定拉伸 + gamma
    lo, hi = t["stretch"]
    x = np.clip((x - lo) / (hi - lo), 0, 1) ** t["gamma"]

    # 4) 锚定：亮度 p10/p90 对齐到固定锚点（跨期一致性的关键）
    a_lo, a_hi = t["anchor"]
    L = _lum(x)[m]
    p10, p90 = np.percentile(L, 10), np.percentile(L, 90)
    if p90 > p10:
        x = (x - p10) * ((a_hi - a_lo) / (p90 - p10)) + a_lo

    # 5) 对比度（围绕 0.5）
    x = (x - 0.5) * t["contrast"] + 0.5

    # 6) 饱和度
    L = _lum(x)[:, :, None]
    x = L + (x - L) * t["saturation"]

    # 7) 均值归一
    mean = float(_lum(x)[m].mean())
    if mean > 1e-6:
        x = x * (t["target_mean"] / mean)
    return np.clip(x, 0, 1).astype("float32")
