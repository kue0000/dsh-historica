"""季节一致性：年内日序（DOY）的圆均值、圆距离、共同窗口与离散度。纯函数，便于单测。

口径：
- 一年按 365 天计（闰年 2 月 29 日也落在 60 附近，误差 1 天以内，不影响 ±45 天窗口判断）。
- 「共同窗口」由各期初选日期的圆均值确定，不引入外部气候分区数据，因此任何地区都可复现。
- 圆距离 = min(|Δdoy|, 365 − |Δdoy|)，跨年边界（12 月/1 月）不会被误判为半年。
"""
import math


def doy(date_obj):
    """date → 年内日序（1–366）。"""
    return int(date_obj.timetuple().tm_yday)


def circ_dist(a, b):
    """两个日序之间的圆距离（天，0–182.5）。"""
    if a is None or b is None:
        return None
    d = abs(float(a) - float(b)) % 365.0
    return min(d, 365.0 - d)


def circ_mean(vals):
    """日序的圆均值。样本反向对消时退化为中位数，避免返回无意义的方向。"""
    v = [float(x) for x in vals if x is not None]
    if not v:
        return None
    ang = [2.0 * math.pi * (x - 1.0) / 365.0 for x in v]
    s = sum(math.sin(a) for a in ang)
    c = sum(math.cos(a) for a in ang)
    if math.hypot(s, c) / len(v) < 1e-6:
        return int(round(sorted(v)[len(v) // 2]))
    a = math.atan2(s, c) % (2.0 * math.pi)
    d = a / (2.0 * math.pi) * 365.0 + 1.0
    out = int(round(d)) % 365
    return out or 365


def in_window(d, center, half):
    """日序 d 是否落在以 center 为中心、±half 天的窗口内；half<=0 表示不约束。"""
    if center is None or not half or float(half) <= 0:
        return True
    return circ_dist(d, center) <= float(half)


def offset_days(d, center):
    """相对窗口中心的带符号偏移（取圆上较近的一侧，正=偏晚）。"""
    if d is None or center is None:
        return None
    raw = (float(d) - float(center)) % 365.0
    return raw - 365.0 if raw > 182.5 else raw


def season_score(d, center, half):
    """打分里的季节一致性项：窗口中心 1.0，偏移 = half 时 0.5，≥2×half 时 0。"""
    if center is None or not half or float(half) <= 0:
        return None
    return max(0.0, 1.0 - circ_dist(d, center) / (2.0 * float(half)))


def mmdd(doy_value):
    """日序 → 'MM-DD'（非闰年）。"""
    if doy_value is None:
        return "—"
    import datetime as _d
    base = _d.date(2023, 1, 1) + _d.timedelta(days=int(doy_value) - 1)
    return base.strftime("%m-%d")


def describe(dates):
    """一组 date 的离散度：日序、月份、均值/标准差、最大差。

    `std_days` / `max_diff_days` 按算术口径（与 v1.0 报告的 101 天同口径），便于改前改后直接对照；
    `circ_center` / `max_offset_days` 是圆口径，跨年边界时以此为准。
    """
    ds = [d for d in dates if d is not None]
    if not ds:
        return {"dates": [], "doy": [], "months": [], "std_days": None,
                "max_diff_days": None, "circ_center": None, "max_offset_days": None}
    doys = [doy(d) for d in ds]
    mean = sum(doys) / len(doys)
    std = math.sqrt(sum((x - mean) ** 2 for x in doys) / len(doys))
    center = circ_mean(doys)
    return {
        "dates": [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in ds],
        "doy": doys,
        "months": sorted({d.month for d in ds}),
        "std_days": round(std, 1),
        "max_diff_days": int(max(doys) - min(doys)),
        "circ_center": center,
        "circ_center_label": mmdd(center),
        "max_offset_days": round(max(circ_dist(x, center) for x in doys), 1),
    }
