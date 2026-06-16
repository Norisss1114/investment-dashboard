"""
search.py (v14.1) — ティッカー/企業名で銘柄を検索する。
config の TICKER_NAMES（企業名）と発掘ユニバース、TICKER_THEMES（セクター）を横断。
"""
import config


def _universe_tickers():
    base = list(config.TICKER_NAMES.keys())
    for lst in (getattr(config, "DISCOVERY_UNIVERSE", []),
                getattr(config, "NASDAQ100_FALLBACK", []),
                getattr(config, "DOW30_FALLBACK", []),
                getattr(config, "SP500_EXTRA_FALLBACK", []),
                getattr(config, "RUSSELL1000_EXTRA_FALLBACK", [])):
        base += list(lst)
    return list(dict.fromkeys(t.upper() for t in base))


def _name(t):
    return config.TICKER_NAMES.get(t, "")


def _sector(t):
    th = config.TICKER_THEMES.get(t, [])
    return th[0] if th else "—"


def search(query, limit=8):
    """ティッカー/企業名で前方一致・部分一致検索。返り値: list[{ticker,name,sector}]"""
    q = (query or "").strip().upper()
    if not q:
        return []
    tickers = _universe_tickers()
    starts, contains = [], []
    for t in tickers:
        name = _name(t)
        nu = name.upper()
        if t == q or t.startswith(q) or nu.startswith(q):
            starts.append(t)
        elif q in t or q in nu:
            contains.append(t)
    # ティッカー完全一致が無く、未知ティッカーっぽい入力ならそのまま候補に
    ordered = list(dict.fromkeys(starts + contains))
    if not ordered and q.isalnum() and 1 <= len(q) <= 6:
        ordered = [q]
    return [{"ticker": t, "name": _name(t) or t, "sector": _sector(t)} for t in ordered[:limit]]
