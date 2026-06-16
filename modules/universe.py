"""
universe.py (v6) — 発掘ユニバースの構築。
複数ソース（Dow30 / NASDAQ100 / S&P500 / Russell1000 / 主要ETF構成 / ユーザーCSV / ウォッチ）から
銘柄を集め、重複を除去しつつ「発見元(provenance)」を記録する。
ライブ時は Wikipedia 等から取得を試み、失敗・モック時は config の静的リストへフォールバック。
"""
import config
from modules import data_fetch, storage


def _mock() -> bool:
    try:
        return data_fetch.is_mock_mode()
    except Exception:
        return True


def _fetch_wikipedia_tickers(url: str, symbol_cols=("Symbol", "Ticker symbol", "Ticker")):
    """Wikipediaの表からティッカー列を抽出（ライブ時のみ）。失敗時は None。"""
    if _mock() or not config.UNIVERSE_ALLOW_NETWORK:
        return None
    try:
        import requests
        import pandas as pd
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8).text
        tables = pd.read_html(html)
        for tb in tables:
            for col in symbol_cols:
                if col in tb.columns:
                    syms = [str(s).strip().upper().replace(".", "-") for s in tb[col].tolist()]
                    syms = [s for s in syms if s and s.isascii() and 1 <= len(s) <= 6]
                    if len(syms) >= 20:
                        return syms
    except Exception:
        return None
    return None


def get_sp500():
    live = _fetch_wikipedia_tickers("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    if live:
        return live
    # フォールバック: NASDAQ100 + 追加大型株（重複は後で除去）
    return list(dict.fromkeys(config.NASDAQ100_FALLBACK + config.SP500_EXTRA_FALLBACK))


def get_nasdaq100():
    live = _fetch_wikipedia_tickers("https://en.wikipedia.org/wiki/Nasdaq-100")
    return live or config.NASDAQ100_FALLBACK


def get_dow30():
    live = _fetch_wikipedia_tickers("https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average")
    return live or config.DOW30_FALLBACK


def get_russell1000():
    live = _fetch_wikipedia_tickers("https://en.wikipedia.org/wiki/Russell_1000_Index")
    if live:
        return live
    # フォールバック: S&P相当 + 中型成長株
    return list(dict.fromkeys(get_sp500() + config.RUSSELL1000_EXTRA_FALLBACK))


def load_user_csv():
    out = []
    try:
        for r in storage.load_csv(config.DISCOVERY_UNIVERSE_CSV, ["ticker"]):
            t = (r.get("ticker", "") or "").strip().upper()
            if t:
                out.append(t)
    except Exception:
        pass
    return out


def build_universe(mode: str, watchlist=()):
    """
    返り値: (tickers:list, sources:dict[ticker->list[str]], meta:dict)
    """
    sources = {}

    def add(tickers, label):
        for t in tickers:
            t = (t or "").strip().upper()
            if not t:
                continue
            sources.setdefault(t, set()).add(label)

    custom_only = mode.startswith("カスタムCSV")

    if custom_only:
        add(load_user_csv(), "CSV")
        add(list(watchlist), "ウォッチ")
    else:
        # ベース（軽量に含む）
        add(get_dow30(), "Dow30")
        add(get_nasdaq100(), "NASDAQ100")
        for etf, holds in config.ETF_HOLDINGS.items():
            add(holds, etf)
        # 標準以上
        if "標準" in mode or "広範囲" in mode:
            add(get_sp500(), "S&P500")
        if "広範囲" in mode:
            add(get_russell1000(), "Russell1000")
        # 常に含める
        add(load_user_csv(), "CSV")
        add(list(watchlist), "ウォッチ")

    # 発見元の数が多い順（複数指数採用＝主要）→ アルファベット で安定ソート
    tickers = sorted(sources.keys(), key=lambda t: (-len(sources[t]), t))

    cap = config.SCAN_MODES.get(mode, 150)
    capped = bool(cap) and len(tickers) > cap
    if capped:
        tickers = tickers[:cap]

    src_out = {t: sorted(sources[t]) for t in tickers}
    meta = {"mode": mode, "total_found": len(sources), "scanned": len(tickers), "capped": capped, "cap": cap}
    return tickers, src_out, meta
