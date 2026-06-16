"""
mock_data.py — APIキー無し / オフラインでも動かすためのサンプルデータ生成。
価格は「ティッカーから決まる乱数シード」で生成するので、毎回同じ形になります。
※ここの数値はあくまでデモ用のサンプルです（実データではありません）。
"""
import hashlib
import numpy as np
import pandas as pd

# デフォルト10銘柄の「サンプル」基礎情報（デモを現実的に見せるための目安値）
# price は近似の出発点。実際の値は yfinance か Moomoo で必ず確認してください。
_SAMPLE_FUND = {
    "NVDA": dict(name="NVIDIA",        price=205.0,  mcap=5.0e12, pe=42.0, ps=22.0,
                 eps_g=0.61, rev_g=0.65, margin=0.55, target=298.0, earnings="2026-08-27"),
    "AMD":  dict(name="AMD",           price=512.0,  mcap=8.3e11, pe=70.0, ps=18.0,
                 eps_g=0.55, rev_g=0.50, margin=0.20, target=472.0, earnings="2026-08-04"),
    "AVGO": dict(name="Broadcom",      price=383.0,  mcap=1.82e12, pe=63.0, ps=24.0,
                 eps_g=0.40, rev_g=0.28, margin=0.45, target=485.0, earnings="2026-09-04"),
    "PLTR": dict(name="Palantir",      price=128.0,  mcap=3.0e11, pe=190.0, ps=70.0,
                 eps_g=0.50, rev_g=0.85, margin=0.30, target=183.0, earnings="2026-08-10"),
    "VST":  dict(name="Vistra",        price=150.0,  mcap=5.0e10, pe=24.0, ps=4.0,
                 eps_g=0.20, rev_g=0.15, margin=0.18, target=209.0, earnings="2026-08-06"),
    "GEV":  dict(name="GE Vernova",    price=1010.0, mcap=2.7e11, pe=55.0, ps=7.0,
                 eps_g=0.30, rev_g=0.15, margin=0.10, target=1180.0, earnings="2026-07-22"),
    "CEG":  dict(name="Constellation", price=285.0,  mcap=9.0e10, pe=30.0, ps=4.0,
                 eps_g=0.22, rev_g=0.10, margin=0.20, target=340.0, earnings="2026-08-05"),
    "XOM":  dict(name="ExxonMobil",    price=147.0,  mcap=6.3e11, pe=17.0, ps=1.6,
                 eps_g=0.10, rev_g=0.05, margin=0.10, target=170.0, earnings="2026-07-24"),
    "CCJ":  dict(name="Cameco",        price=101.0,  mcap=4.4e10, pe=93.0, ps=12.0,
                 eps_g=0.25, rev_g=0.20, margin=0.18, target=125.0, earnings="2026-07-30"),
    "MRVL": dict(name="Marvell",       price=199.0,  mcap=2.45e11, pe=85.0, ps=20.0,
                 eps_g=0.45, rev_g=0.35, margin=0.30, target=233.0, earnings="2026-08-26"),
}

_GENERIC_HEADLINES = [
    ("{t}: アナリストが目標株価を引き上げ（格上げ）", "bull"),
    ("{t} の決算がガイダンスで需要拡大を示唆", "bull"),
    ("{t}、大型の新規受注で提携を発表", "bull"),
    ("{t} に強気の投資判断（買い）", "bull"),
    ("{t}、規制当局の調査を巡る報道で売られる", "bear"),
    ("{t} の競合が値下げ、シェア懸念で格下げ", "bear"),
    ("{t}、バリュエーション過熱との指摘で急落", "bear"),
    ("{t} は方向感に欠ける展開", "neutral"),
    ("{t}、四半期決算を控え様子見", "neutral"),
    ("{t} の出来高が平均を上回る", "neutral"),
    ("{t}、買収交渉の可能性が報道される", "bull"),
    ("{t} の新工場計画が需要拡大の観測", "bull"),
]


def _seed(ticker: str) -> int:
    return int(hashlib.md5(ticker.encode()).hexdigest(), 16) % (2**32)


def mock_price_history(ticker: str, days: int = 260) -> pd.DataFrame:
    """ティッカー固有のシードで、なめらかな疑似株価（ランダムウォーク+トレンド）を生成。"""
    rng = np.random.default_rng(_seed(ticker))
    info = _SAMPLE_FUND.get(ticker.upper())
    end_price = info["price"] if info else float(50 + (_seed(ticker) % 400))

    # 先に営業日インデックスを作り、その本数に全配列を揃える（長さズレ防止）
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    n = len(idx)

    # トレンドを少し持たせる（シードで上昇/横ばいが決まる）
    drift = (rng.random() - 0.35) * 0.0018
    vol = 0.015 + rng.random() * 0.02
    rets = rng.normal(drift, vol, n)
    # 終値が end_price になるように逆算
    prices = end_price * np.exp(np.cumsum(rets[::-1]))[::-1]
    prices = prices / prices[-1] * end_price

    close = prices
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    open_ = open_ * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    base_vol = 5e6 + (_seed(ticker) % 50) * 1e6
    volume = (base_vol * (1 + np.abs(rng.normal(0, 0.4, n)))).astype(np.int64)

    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )
    return df


def mock_fundamentals(ticker: str) -> dict:
    t = ticker.upper()
    info = _SAMPLE_FUND.get(t)
    if info:
        d = dict(info)
        d["ticker"] = t
        d["is_sample"] = True
        return d
    # 未知ティッカーは中庸なダミー
    rng = np.random.default_rng(_seed(t))
    price = float(50 + (_seed(t) % 400))
    return dict(
        ticker=t, name=t, price=price, mcap=float(1e9 * (1 + _seed(t) % 200)),
        pe=float(15 + rng.random() * 40), ps=float(2 + rng.random() * 15),
        eps_g=float(rng.random() * 0.4), rev_g=float(rng.random() * 0.4),
        margin=float(0.05 + rng.random() * 0.3), target=price * (1 + rng.random() * 0.4),
        earnings="未定", is_sample=True,
    )


def mock_news(ticker: str, n: int = 6) -> list:
    """ティッカー固有の疑似ニュース見出しを返す。"""
    rng = np.random.default_rng(_seed(ticker) + 7)
    picks = rng.choice(len(_GENERIC_HEADLINES), size=min(n, len(_GENERIC_HEADLINES)), replace=False)
    out = []
    for i in picks:
        title, lean = _GENERIC_HEADLINES[i]
        out.append({"headline": title.format(t=ticker.upper()), "source": "サンプル", "lean": lean})
    return out


def mock_macro() -> dict:
    """マクロ指数のサンプル値（オフライン時用）。"""
    return {
        "S&P500": {"value": 7405.0, "change_pct": 0.3, "trend": "上"},
        "NASDAQ": {"value": 25930.0, "change_pct": 0.9, "trend": "上"},
        "VIX (恐怖指数)": {"value": 20.5, "change_pct": -5.0, "trend": "やや高"},
        "米10年債利回り": {"value": 4.45, "change_pct": 0.0, "trend": "横ばい"},
        "原油 (WTI)": {"value": 86.0, "change_pct": 1.5, "trend": "高止まり"},
    }
