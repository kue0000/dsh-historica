"""吞吐校准表：把每次运行的（预估, 实测, 误差）落盘，下次预估用最近 N 次实测中位数。"""
import json, os, statistics, time

from . import config


def path_of(cfg):
    cal = ((cfg.get("estimate") or {}).get("calibration") or {})
    rel = cal.get("path") or "cache/calibration.json"
    return rel if os.path.isabs(rel) else os.path.join(config.ROOT, rel)


def load(cfg):
    p = path_of(cfg)
    if not os.path.exists(p):
        return []
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return []


def record(cfg, entry):
    rows = load(cfg)
    entry = dict(entry, measured_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    rows = [r for r in rows if r.get("run") != entry.get("run")]   # 同一运行重跑只留最新一条
    rows.append(entry)
    rows = rows[-100:]
    p = path_of(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return p


def _window(cfg):
    return int(((cfg.get("estimate") or {}).get("calibration") or {}).get("window", 5) or 5)


def recent_speeds(cfg):
    """最近 N 次运行的整轮净吞吐（每次运行一个样本）。"""
    rows = [r for r in load(cfg) if r.get("speed_mbps")]
    return [float(r["speed_mbps"]) for r in rows[-_window(cfg):]]


def recent_block_speeds(cfg, max_per_run=10):
    """下载期看门狗记录的分块吞吐样本（v1.1 起并入预估样本池）。

    每次运行最多等距抽 `max_per_run` 个，避免一次长运行在样本池里把探针样本挤掉。
    """
    rows = [r for r in load(cfg) if r.get("block_speeds_mbps")]
    out = []
    for r in rows[-_window(cfg):]:
        vals = [float(v) for v in (r.get("block_speeds_mbps") or []) if v]
        if not vals:
            continue
        if len(vals) > max_per_run:
            idx = sorted({int(round(i * (len(vals) - 1) / (max_per_run - 1)))
                          for i in range(max_per_run)})
            vals = [vals[i] for i in idx]
        out.extend(vals)
    return out


def median_speed(cfg):
    vals = recent_speeds(cfg)
    if not vals:
        return None, 0
    return float(statistics.median(vals)), len(vals)
