"""
macro.py — 市場全体の取得と相場判定（v2: 4モード＋セクター＋イベント＋推奨比率）。
"""
import datetime as dt
import config
from modules import data_fetch, mock_data
from modules.utils import days_until

try:
    import streamlit as st
    _cache = st.cache_data(ttl=900, show_spinner=False)
except Exception:  # pragma: no cover
    def _cache(func):
        return func


@_cache
def get_market_indices() -> dict:
    if data_fetch.is_mock_mode():
        return mock_data.mock_macro()
    out = {}
    try:
        import yfinance as yf
        for label, tk in config.MACRO_TICKERS.items():
            try:
                df = yf.Ticker(tk).history(period="6mo", interval="1d")
                close = df["Close"].dropna()
                value = float(close.iloc[-1]); prev = float(close.iloc[-2])
                change = (value / prev - 1) * 100 if prev else 0.0
                ma50 = float(close.rolling(50, min_periods=1).mean().iloc[-1])
                out[label] = {"value": round(value, 2), "change_pct": round(change, 2),
                              "trend": "上" if value > ma50 else "下"}
            except Exception:
                out[label] = mock_data.mock_macro().get(label, {"value": 0, "change_pct": 0, "trend": "—"})
        return out
    except Exception:
        return mock_data.mock_macro()


def assess_regime(indices: dict) -> dict:
    """4モード（強気/中立/弱気/危険）＋市場スコア(0-10)＋セクター＋推奨比率を返す。"""
    sp = indices.get("S&P500", {}); ndx = indices.get("NASDAQ", {})
    vix = indices.get("VIX (恐怖指数)", {}).get("value", 20)
    up = sum(1 for x in (sp, ndx) if x.get("trend") == "上")
    fomc_days = days_until(config.MACRO_SNAPSHOT.get("next_fomc"))

    # 危険: VIX急騰 or 主要指数が両方下 かつ VIX高い
    if vix >= 30 or (up == 0 and vix >= 25):
        mode, score = "危険", 2.0
    elif up == 2 and vix < 18:
        mode, score = "強気", 9.0
    elif up == 0 or vix > 26:
        mode, score = "弱気", 4.0
    else:
        mode, score = "中立", 6.0
    if vix > 22 and mode not in ("危険",):
        score = max(0.0, score - 1.0)

    play = config.REGIME_PLAYBOOK.get(mode, config.REGIME_PLAYBOOK["中立"])
    reasons = [
        f"主要指数の上昇トレンド: {up}/2",
        f"VIX {vix}（20前後=平常 / 25超=警戒 / 30超=恐怖）",
    ]
    if fomc_days is not None and 0 <= fomc_days <= config.FOMC_SOON_DAYS:
        reasons.append(f"⚠️ FOMCまで約{fomc_days}日（イベント前はサイズを落とす）")

    return {
        "regime": mode, "score": round(score, 1), "reason": " / ".join(reasons),
        "reasons": reasons, "stance": play["stance"],
        "new_buy_pct": play["new_buy_pct"], "cash_pct": play["cash_pct"],
        "good_sectors": play["good"], "bad_sectors": play["bad"],
        "fomc_days": fomc_days, "vix": vix,
    }


def upcoming_events():
    """今週・今月の重要イベントを返す。"""
    today = dt.date.today()
    week, month = [], []
    for ev in config.EVENTS:
        d = days_until(ev["date"])
        if d is None or d < 0:
            continue
        item = {**ev, "days": d}
        if d <= 7:
            week.append(item)
        if d <= 31:
            month.append(item)
    week.sort(key=lambda x: x["days"]); month.sort(key=lambda x: x["days"])
    return week, month


def snapshot() -> dict:
    return config.MACRO_SNAPSHOT
