"""
storage.py — ウォッチリストと設定をローカルJSONに保存/読込。
壊れたファイルや権限エラーでも落ちない。
"""
import os
import json
import config

_DEFAULTS = {
    "watchlist": [],
    "capital": 10000.0,
    "risk_pct": config.MAX_RISK_PER_TRADE_PCT,
    "max_pos_pct": config.MAX_POSITION_PCT,
}


def load_settings() -> dict:
    path = config.SETTINGS_PATH
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = {**_DEFAULTS, **(data or {})}
            if not isinstance(merged.get("watchlist"), list):
                merged["watchlist"] = []
            return merged
    except Exception:
        pass
    return dict(_DEFAULTS)


def load_json(path, default):
    """汎用JSON読込。壊れていても default を返す。"""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if data is not None else default
    except Exception:
        pass
    return default


def save_json(path, data) -> bool:
    """汎用JSON保存。失敗しても False を返すだけ。"""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def save_settings(watchlist, capital, risk_pct, max_pos_pct) -> bool:
    path = config.SETTINGS_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "watchlist": [t.strip().upper() for t in watchlist if t.strip()],
            "capital": float(capital),
            "risk_pct": float(risk_pct),
            "max_pos_pct": float(max_pos_pct),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ===================== v3: 汎用 JSON/CSV 保存 =====================
import csv as _csv


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path, data) -> bool:
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_csv(path, fieldnames):
    rows = []
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", newline="") as f:
                for row in _csv.DictReader(f):
                    rows.append(row)
    except Exception:
        pass
    return rows


def save_csv(path, fieldnames, rows) -> bool:
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fieldnames})
        return True
    except Exception:
        return False
