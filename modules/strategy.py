"""
strategy.py (v13) — 採用済みルール(strategy_settings.json)を実運用に反映する解決器。
v10のルール最適化で「採用」した設定を、発掘エンジンのスコア重み・RSI除外・出来高条件・
TOP件数・保有/損切り/利確の目安に翻訳する。未採用ならデフォルトで動作。
※採用中ルールは過去検証に基づく参考設定。将来を保証しない。
"""
import config
from modules import storage

# 重みプリセット → 発掘エンジンの6要素配点（合計100）
PRESET_TO_DISCOVERY_WEIGHTS = {
    "現在設定":       config.DISCOVERY_WEIGHTS,
    "テクニカル重視": {"technical": 35, "news": 20, "fundamental": 15, "theme": 15, "supply": 10, "risk": 5},
    "ニュース重視":   {"technical": 20, "news": 35, "fundamental": 15, "theme": 15, "supply": 10, "risk": 5},
    "ファンダ重視":   {"technical": 20, "news": 20, "fundamental": 30, "theme": 15, "supply": 10, "risk": 5},
    "テーマ重視":     {"technical": 20, "news": 20, "fundamental": 15, "theme": 30, "supply": 10, "risk": 5},
    "リスク低減重視": {"technical": 25, "news": 20, "fundamental": 15, "theme": 10, "supply": 15, "risk": 15},
}


def active():
    """採用済み設定(dict) or None。"""
    return storage.load_json(config.STRATEGY_SETTINGS_PATH, None)


def _top_n(condition):
    return {"スコア上位TOP5": 5, "スコア上位TOP10": 10, "スコア上位TOP20": 20}.get(condition)


def resolved(s=None):
    """採用設定を実運用パラメータへ翻訳。未採用ならデフォルト。"""
    s = s if s is not None else active()
    if not s:
        return {
            "active": False, "label": "デフォルト（未採用）",
            "weights": config.DISCOVERY_WEIGHTS, "rsi_thr": 75, "vol_filter": "なし",
            "top_n": None, "condition": None, "hold": None, "stop": None, "take": None,
            "adopted_at": None,
        }
    weights_name = s.get("weights", "現在設定")
    rsi_excl = s.get("rsi_exclude", "なし")
    rsi_thr = 75
    for thr in (70, 75, 80):
        if str(thr) in rsi_excl:
            rsi_thr = thr
    return {
        "active": True,
        "label": f"{s.get('condition','-')}/{s.get('hold','-')}/{s.get('stop','-')}/RSI:{rsi_excl}/{weights_name}",
        "weights": PRESET_TO_DISCOVERY_WEIGHTS.get(weights_name, config.DISCOVERY_WEIGHTS),
        "weights_name": weights_name,
        "rsi_thr": rsi_thr, "rsi_exclude": rsi_excl,
        "vol_filter": s.get("volume_cond", "なし"),
        "top_n": _top_n(s.get("condition")), "condition": s.get("condition"),
        "hold": s.get("hold"), "stop": s.get("stop"), "take": s.get("take"),
        "adopted_at": s.get("_adopted_at"),
    }


def label_line(strat=None):
    strat = strat or resolved()
    if not strat["active"]:
        return "採用ルール: なし（デフォルト設定で稼働中）"
    return f"採用ルール: {strat['label']}（過去検証に基づく参考設定・将来非保証）"
