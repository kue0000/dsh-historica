"""配置加载与合并。所有可调参数都来自 config/default.json，代码里不写死。"""
import json, os, copy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _deep_merge(a, b):
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None, overrides=None):
    """默认配置 + 可选覆盖文件（可只写要改的键，与默认深度合并）。"""
    base_path = os.path.join(ROOT, "config", "default.json")
    cfg = json.load(open(base_path, encoding="utf-8"))
    if path:
        cfg = _deep_merge(cfg, json.load(open(path, encoding="utf-8")))
    if overrides:
        cfg = _deep_merge(cfg, overrides)
    return cfg


def save_user_config(cfg, out_dir):
    """把本次运行的有效配置落盘，保证可复现。"""
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "config_used.json")
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return p
