"""年份展开：具体年份 / 区间+步长 / 自动择优窗口。纯函数，便于单测。"""
import re


class YearSpecError(ValueError):
    pass


def parse_year_list(spec):
    """'1993,2004,2013,2025' -> [1993, 2004, 2013, 2025]"""
    if not spec:
        return []
    parts = [p.strip() for p in re.split(r"[,，;；\s]+", spec) if p.strip()]
    out = []
    for p in parts:
        if not re.fullmatch(r"\d{4}", p):
            raise YearSpecError(f"非法年份: {p!r}（必须是四位数字年份）")
        out.append(int(p))
    return sorted(set(out))


def parse_range(spec):
    """'1990-2025' -> (1990, 2025)"""
    m = re.fullmatch(r"(\d{4})\s*[-–~]\s*(\d{4})", spec.strip())
    if not m:
        raise YearSpecError(f"非法区间: {spec!r}（应形如 1990-2025）")
    a, b = int(m.group(1)), int(m.group(2))
    if a > b:
        raise YearSpecError(f"区间起点大于终点: {spec}")
    return a, b


def expand_range(spec, step=5, inclusive=True):
    """'1990-2025' + step 5 -> [1990,1995,2000,2005,2010,2015,2020,2025]"""
    a, b = parse_range(spec)
    if step <= 0:
        raise YearSpecError("step 必须大于 0")
    years = list(range(a, b + 1, step))
    if inclusive and years[-1] != b:
        years.append(b)
    return years


def expand_years(year_list=None, range_spec=None, step=None, start=None, end=None):
    """三种输入方式的统一入口。

    1) 具体年份列表
    2) 区间 + 步长
    3) 区间（用 start/end 给出）→ 交给自动择优窗口在查询时处理
    """
    if year_list and range_spec:
        raise YearSpecError("--year 与 --range 不能同时使用")
    if year_list:
        return parse_year_list(year_list)
    if range_spec:
        return expand_range(range_spec, step or 5)
    if start and end:
        return expand_range(f"{start}-{end}", step or 1)
    raise YearSpecError("必须提供 --year 或 --range")


def year_window(target, half_width=1):
    """自动择优的查询窗口（含端点）。"""
    if half_width < 0:
        raise YearSpecError("窗口半宽不能为负")
    return target - half_width, target + half_width


def date_distance_score(date_year, target_year, half_width):
    """日期离散度得分：落在目标年得 1，偏离 1 年按窗口衰减。"""
    if half_width <= 0:
        return 1.0 if date_year == target_year else 0.0
    d = abs(date_year - target_year)
    return max(0.0, 1.0 - d / (half_width + 1.0))
