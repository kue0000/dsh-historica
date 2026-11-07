"""配准校验：梯度 NCC、±12 px、多窗口 × 多时序、带 NCC 置信比值（移植自 v1 align_verify2.py）。"""
import numpy as np


def _gray(rgb):
    return (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype("float32")


def _grad(g):
    gy, gx = np.gradient(g)
    return np.sqrt(gx * gx + gy * gy)


def _windows(shape):
    h, w = shape
    win = int(min(420, h // 3, w // 3))
    cy = h // 2
    return [(max(0, w // 4 - win // 2), max(0, cy - win // 2), win, win),
            (max(0, w // 2 - win // 2), max(0, cy - win // 2), win, win),
            (max(0, 3 * w // 4 - win // 2), max(0, cy - win // 2), win, win)]


def _xcorr(a, b):
    """零填充 FFT 互相关，返回 (C, cx, cy) 峰值中心坐标。"""
    h, w = a.shape
    H, W = 2 * h, 2 * w
    A = np.fft.rfft2(a, (H, W))
    B = np.fft.rfft2(b, (H, W))
    C = np.fft.irfft2(A * np.conj(B), (H, W))
    C = np.fft.fftshift(C)
    cy, cx = np.unravel_index(np.argmax(C), C.shape)
    return C, cy, cx, C.shape[0] // 2, C.shape[1] // 2


def best_shift(a, b, max_shift=12):
    """±max_shift 内找互相关峰；返回 (dx, dy, 峰NCC, 置信比值)。"""
    a = a - a.mean()
    b = b - b.mean()
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 0, 0, 0.0, 0.0
    a = a / a.std()
    b = b / b.std()
    C, py, px, cy0, cx0 = _xcorr(a, b)
    # 限制在搜索窗内
    r0, r1 = cy0 - max_shift, cy0 + max_shift + 1
    c0, c1 = cx0 - max_shift, cx0 + max_shift + 1
    sub = C[r0:r1, c0:c1]
    iy, ix = np.unravel_index(np.argmax(sub), sub.shape)
    peak = float(sub[iy, ix] / a.size)
    dx = int(ix + c0 - cx0)
    dy = int(iy + r0 - cy0)
    p90 = float(np.percentile(sub, 90) / a.size)
    ratio = (peak - p90) / (abs(peak) + 1e-6)
    return dx, dy, peak, ratio


def verify_pairs(epochs, cfg=None, max_shift=12):
    """epochs: [(year, rgb, mask)]，按年份排序；返回逐对结果。

    峰值落在 ±max_shift 边界、或 NCC 低于阈值时判「未锁定」：不给位移值，
    避免把噪声/搜索边界当成真实位移。
    """
    ncc_min = float((cfg or {}).get("align", {}).get("ncc_min", 0.2))
    out = []
    for (ya, ga, _ma), (yb, gb, _mb) in zip(epochs, epochs[1:]):
        G1, G2 = _grad(_gray(ga)), _grad(_gray(gb))
        for (x, y, ww, hh) in _windows(ga.shape[:2]):
            if y + hh > ga.shape[0] or x + ww > ga.shape[1]:
                continue
            dx, dy, peak, ratio = best_shift(G1[y:y + hh, x:x + ww], G2[y:y + hh, x:x + ww], max_shift)
            at_edge = abs(dx) >= max_shift or abs(dy) >= max_shift
            locked = (peak >= ncc_min) and not at_edge
            rec = {"pair": f"{ya}->{yb}", "window": [x, y, ww, hh],
                   "ncc": round(peak, 4), "confidence_ratio": round(ratio, 3),
                   "locked": bool(locked), "status": "ok" if locked else "未锁定"}
            if locked:
                rec.update({"dx": int(dx), "dy": int(dy),
                            "pass": abs(dx) <= 1 and abs(dy) <= 1})
            else:
                rec.update({"dx": None, "dy": None, "pass": False,
                            "reason": "峰值在搜索边界" if at_edge else f"NCC<{ncc_min:g}"})
            out.append(rec)
    return out


def summarize(results):
    n = len(results)
    if n == 0:
        return {"pairs": 0, "locked": 0, "unlocked": 0, "zero_shift": 0,
                "pass_rate": 0.0, "min_conf": None, "results": []}
    locked = [r for r in results if r.get("locked")]
    zero = sum(1 for r in locked if r["dx"] == 0 and r["dy"] == 0)
    return {"pairs": n, "locked": len(locked), "unlocked": n - len(locked),
            "zero_shift": zero, "pass_rate": zero / max(1, len(locked)),
            "min_conf": min((r["confidence_ratio"] for r in locked), default=None),
            "ncc_min": min((r["ncc"] for r in results), default=None),
            "results": results}
