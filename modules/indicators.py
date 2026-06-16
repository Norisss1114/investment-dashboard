"""
indicators.py — テクニカル指標の計算。pandas/numpy だけで動きます。
"""
import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """単純移動平均（Simple Moving Average）。"""
    return series.rolling(window=window, min_periods=1).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI（相対力指数）。0〜100。
    70以上=買われすぎ、30以下=売られすぎ、の目安。
    Wilder の平滑化を使用。
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """
    MACD。返り値: (macd_line, signal_line, histogram)
    macd_line が signal_line を上抜け=強気、下抜け=弱気の目安。
    """
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    OHLCV の DataFrame に各指標を列として追加して返す。
    必要列: Open, High, Low, Close, Volume
    """
    df = df.copy()
    close = df["Close"]
    df["SMA20"] = sma(close, 20)
    df["SMA50"] = sma(close, 50)
    df["SMA200"] = sma(close, 200)
    df["RSI"] = rsi(close, 14)
    macd_line, signal_line, hist = macd(close)
    df["MACD"] = macd_line
    df["MACD_SIGNAL"] = signal_line
    df["MACD_HIST"] = hist
    df["VOL_SMA20"] = sma(df["Volume"], 20)
    return df


def latest_snapshot(df: pd.DataFrame) -> dict:
    """最新行のテクニカル状態を辞書で返す（スコアリング・UIで使用）。"""
    if df is None or len(df) == 0:
        return {}
    last = df.iloc[-1]
    price = float(last["Close"])
    vol = float(last.get("Volume", 0) or 0)
    vol_avg = float(last.get("VOL_SMA20", vol) or vol)
    return {
        "price": price,
        "sma20": _f(last.get("SMA20")),
        "sma50": _f(last.get("SMA50")),
        "sma200": _f(last.get("SMA200")),
        "rsi": _f(last.get("RSI"), 50.0),
        "macd": _f(last.get("MACD")),
        "macd_signal": _f(last.get("MACD_SIGNAL")),
        "macd_hist": _f(last.get("MACD_HIST")),
        "volume": vol,
        "vol_avg": vol_avg,
        "vol_ratio": (vol / vol_avg) if vol_avg else 1.0,
        "above_sma200": bool(price > _f(last.get("SMA200"), price)),
        "above_sma50": bool(price > _f(last.get("SMA50"), price)),
        "above_sma20": bool(price > _f(last.get("SMA20"), price)),
    }


def _f(v, default=0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return float(default)
        return float(v)
    except Exception:
        return float(default)
