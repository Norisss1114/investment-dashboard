"""
data_fetch.py — 株価・ファンダ取得の入口。
yfinance が使えればそれを、ダメならモックデータに自動フォールバック。
v7: use_cache フラグを追加。並列スキャン(別スレッド)では use_cache=False で
    Streamlitキャッシュを経由せず直接取得する（スレッド安全）。
"""
import os
import pandas as pd

from modules import mock_data

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
