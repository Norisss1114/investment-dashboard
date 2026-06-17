"""
data_fetch.py — 株価・ファンダ取得の入口。
yfinance が使えればそれを、ダメならモックデータに自動フォールバック。
v7: use_cache フラグを追加。並列スキャン(別スレッド)では use_cache=False で
    Streamlitキャッシュを経由せず直接取得する（スレッド安全）。
"""
import os
import pandas as pd

import config
from modules import mock_data

# v15.2: 価格ソースの表示ラベル
PRICE_SOURCE_LABEL = {
    "yfinance": "yfinance",
    "yfinance_download": "yfinance(download)",
    "finnhub_quote": "Finnhub",
    "stooq": "Stooq",
    "fallback_avg_cost": "取得単価(仮)",
    "mock": "サンプル",
    "unavailable": "取得不可",
}


def price_source_label(src: str) -> str:
    return PRICE_SOURCE_LABEL.get(src, src or "—")

try:
    import streamlit as st
    _cache = st.cache_data(ttl=900, show_spinner=False)
except Exception:  # pragma: no cover
    def _cache(func):
        return func


def is_mock_mode() -> bool:
    val = os.getenv("MOCK_MODE", "").strip().lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    try:
        import yfinance  # noqa: F401
        return False
    except Exception:
        return True


# ---------------- 実体（キャッシュなし） ----------------
def _price_impl(ticker: str, period: str, interval: str):
    ticker = ticker.strip().upper()
    if not is_mock_mode():
        try:
            import yfinance as yf
            df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
            if df is not None and len(df) > 30:
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                return df, "yfinance"
        except Exception:
            pass
    return mock_data.mock_price_history(ticker), "mock"


def _fund_impl(ticker: str) -> dict:
    ticker = ticker.strip().upper()
    if not is_mock_mode():
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if price:
                return {
                    "ticker": ticker, "name": info.get("shortName") or ticker,
                    "price": float(price), "mcap": _num(info.get("marketCap")),
                    "pe": _num(info.get("trailingPE")), "ps": _num(info.get("priceToSalesTrailing12Months")),
                    "eps_g": _num(info.get("earningsGrowth")), "rev_g": _num(info.get("revenueGrowth")),
                    "margin": _num(info.get("profitMargins")), "target": _num(info.get("targetMeanPrice")) or float(price),
                    "earnings": _earnings_date(info), "is_sample": False,
                }
        except Exception:
            pass
    return mock_data.mock_fundamentals(ticker)


# ---------------- キャッシュ付きラッパ ----------------
@_cache
def _price_cached(ticker, period, interval):
    return _price_impl(ticker, period, interval)


@_cache
def _fund_cached(ticker):
    return _fund_impl(ticker)


# ---------------- 公開API ----------------
def get_price_history(ticker, period="1y", interval="1d", use_cache=True):
    if use_cache:
        try:
            return _price_cached(ticker, period, interval)
        except Exception:
            pass
    return _price_impl(ticker, period, interval)


def get_fundamentals(ticker, use_cache=True):
    if use_cache:
        try:
            return _fund_cached(ticker)
        except Exception:
            pass
    return _fund_impl(ticker)


# ---------------- v15.2: 現在値の多段フォールバック取得 ----------------
def _quote_impl(ticker: str) -> dict:
    """現在値を複数ソースから順に取得。返り値 {price, source, error}。
    優先: yfinance(fast_info→history→download) → Finnhub quote → Stooq。
    すべて失敗したら price=None, source='unavailable'。
    ※取得単価の仮値はここでは入れない（呼び出し側で明示的に行う）。"""
    ticker = (ticker or "").strip().upper()
    errs = []

    if not is_mock_mode():
        # A-1. yfinance fast_info
        try:
            import yfinance as yf
            fi = yf.Ticker(ticker).fast_info
            p = None
            for k in ("last_price", "lastPrice"):
                try:
                    p = fi[k] if k in fi else None
                except Exception:
                    p = getattr(fi, "last_price", None)
                if p:
                    break
            if p and float(p) > 0:
                return {"price": float(p), "source": "yfinance", "error": None}
        except Exception as e:
            errs.append(f"yfinance fast_info: {type(e).__name__}")
        # A-2. yfinance history
        try:
            import yfinance as yf
            h = yf.Ticker(ticker).history(period="5d")
            if h is not None and len(h) > 0:
                c = h["Close"].dropna()
                if len(c) > 0 and float(c.iloc[-1]) > 0:
                    return {"price": float(c.iloc[-1]), "source": "yfinance", "error": None}
        except Exception as e:
            errs.append(f"yfinance history: {type(e).__name__}")
        # B. yfinance download
        try:
            import yfinance as yf
            d = yf.download(ticker, period="5d", progress=False, auto_adjust=False, threads=False)
            if d is not None and len(d) > 0 and "Close" in d:
                c = d["Close"]
                series = c.iloc[:, 0] if hasattr(c, "columns") else c
                series = series.dropna()
                if len(series) > 0 and float(series.iloc[-1]) > 0:
                    return {"price": float(series.iloc[-1]), "source": "yfinance_download", "error": None}
        except Exception as e:
            errs.append(f"yfinance download: {type(e).__name__}")

    # C. Finnhub quote（CloudでyfinanceがダメでもここでカバーD）
    key = config.get_secret("FINNHUB_API_KEY")
    if key:
        try:
            import requests
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": ticker, "token": key}, timeout=8)
            if r.status_code == 200:
                c = (r.json() or {}).get("c")
                if c and float(c) > 0:
                    return {"price": float(c), "source": "finnhub_quote", "error": None}
                errs.append("finnhub quote: 価格0/該当なし")
            else:
                errs.append(f"finnhub quote HTTP {r.status_code}")
        except Exception as e:
            errs.append(f"finnhub quote: {type(e).__name__}")
    else:
        errs.append("Finnhub未設定")

    # D. Stooq（無料・キー不要のCSV）
    try:
        import requests
        r = requests.get("https://stooq.com/q/l/",
                         params={"s": f"{ticker.lower()}.us", "f": "sd2t2ohlcv", "h": "", "e": "csv"},
                         timeout=8)
        if r.status_code == 200 and r.text:
            lines = r.text.strip().splitlines()
            if len(lines) >= 2:
                row = dict(zip(lines[0].split(","), lines[1].split(",")))
                c = row.get("Close")
                if c and c not in ("N/D", "", "0") and float(c) > 0:
                    return {"price": float(c), "source": "stooq", "error": None}
        errs.append("stooq: 取得不可")
    except Exception as e:
        errs.append(f"stooq: {type(e).__name__}")

    return {"price": None, "source": "unavailable", "error": "; ".join(errs) or "全ソース失敗"}


@_cache
def _quote_cached(ticker):
    return _quote_impl(ticker)


def get_quote(ticker, use_cache=True) -> dict:
    """現在値を多段フォールバックで取得。全ページ・全銘柄で共通利用する。"""
    if use_cache:
        try:
            return _quote_cached(ticker)
        except Exception:
            pass
    return _quote_impl(ticker)


def _num(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _earnings_date(info: dict) -> str:
    for key in ("earningsTimestampStart", "earningsTimestamp"):
        ts = info.get(key)
        if ts:
            try:
                return pd.to_datetime(ts, unit="s").strftime("%Y-%m-%d")
            except Exception:
                pass
    return "未定"
