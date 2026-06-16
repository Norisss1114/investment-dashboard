"""
themes.py — 銘柄のテーマ/セクター分類。
既知マップ → ファンダ/ニュースから推測 → 不明なら「未分類」。
"""
import config

_AI_WORDS = ["ai", "人工知能", "データセンター", "gpu", "accelerat"]
_SEMI_WORDS = ["半導体", "semiconductor", "chip", "メモリ", "memory"]
_ENERGY_WORDS = ["energy", "oil", "原油", "power", "電力", "uranium", "ウラン", "nuclear", "原子力", "gas"]
_DEFENSE_WORDS = ["defense", "防衛", "軍", "aerospace"]
_SOFT_WORDS = ["software", "ソフト", "cloud", "platform"]


def classify(ticker: str, fund: dict = None, news_items=None) -> list:
    t = (ticker or "").upper()
    if t in config.TICKER_THEMES:
        return list(config.TICKER_THEMES[t])

    themes = []
    name = (fund or {}).get("name", "") if fund else ""
    text = (name + " " + " ".join(i.get("headline", "") for i in (news_items or []))).lower()

    if any(w in text for w in _AI_WORDS):
        themes.append("AI")
    if any(w in text for w in _SEMI_WORDS):
        themes.append("半導体")
    if any(w in text for w in _ENERGY_WORDS):
        themes.append("エネルギー")
    if any(w in text for w in _DEFENSE_WORDS):
        themes.append("防衛")
    if any(w in text for w in _SOFT_WORDS):
        themes.append("ソフトウェア")

    if fund:
        pe = fund.get("pe")
        rev_g = fund.get("rev_g") or 0
        try:
            if (pe and float(pe) > 50) or (rev_g and float(rev_g) > 0.3):
                themes.append("高PERグロース")
        except Exception:
            pass

    return themes or ["未分類"]


def buckets_for(themes: list) -> set:
    """テーマ群が属する集計バケツ名の集合を返す。"""
    out = set()
    for bucket, keys in config.THEME_BUCKETS.items():
        if any(th in keys for th in themes):
            out.add(bucket)
    return out
