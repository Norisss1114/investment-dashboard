"""
trade_plan.py — 売買プランの自動生成（v2: 3段エントリー＋多段利確＋イベント減らし）。
※助言ではなく機械的なたたき台です。
"""
import config
from modules.utils import days_until


def build_plan(fund, snap, df=None, fomc_days=None) -> dict:
    price = float(fund.get("price") or (snap.get("price") if snap else 0) or 0)
    if price <= 0:
        return {"error": "価格が取得できませんでした"}
    target = float(fund.get("target") or price)
    sma50 = snap.get("sma50") if snap else None

    recent_low = None
    try:
        if df is not None and len(df) >= 20:
            recent_low = float(df["Low"].tail(20).min())
    except Exception:
        recent_low = None

    # ---- 3段エントリー ----
    entry1 = round(price * 0.985, 2)                 # 第1: 現値の少し下（打診）
    entry2 = round(price * 0.95, 2)                  # 第2: -5%押し目
    e3_base = max([c for c in [sma50, recent_low, price * 0.90] if c] or [price * 0.90])
    entry3 = round(min(e3_base, price * 0.90), 2)    # 第3: 50日線/20日安値/-10%の深押し
    avg_entry = round((entry1 + entry2 + entry3) / 3, 2)

    # ---- 損切り（第3より下 or デフォルト幅） ----
    stop = round(min(entry3 * 0.96, avg_entry * (1 - config.DEFAULT_STOP_PCT / 100)), 2)

    # ---- 多段利確 ----
    upside = max(0.0, target / price - 1)
    if upside < 0.15:
        tp1 = round(price * 1.08, 2); tp2 = round(price * 1.15, 2); tp3 = round(price * 1.25, 2)
    else:
        tp1 = round(price * (1 + upside * 0.4), 2)
        tp2 = round(price * (1 + upside * 0.7), 2)
        tp3 = round(target, 2)

    half_sell = tp1   # ここで半分利確
    full_exit = stop  # 損切り＝全撤退

    # ---- イベント前に減らす割合 ----
    e_days = days_until(fund.get("earnings"))
    earnings_reduce = config.REDUCE_BEFORE_EARNINGS_PCT if (e_days is not None and 0 <= e_days <= config.EARNINGS_SOON_DAYS) else 0
    fomc_reduce = config.REDUCE_BEFORE_FOMC_PCT if (fomc_days is not None and 0 <= fomc_days <= config.FOMC_SOON_DAYS) else 0

    return {
        "price": round(price, 2),
        "entry1": entry1, "entry2": entry2, "entry3": entry3, "avg_entry": avg_entry,
        "stop": stop, "stop_pct": round((1 - stop / avg_entry) * 100, 1),
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "half_sell": half_sell, "full_exit": full_exit,
        "earnings": fund.get("earnings", "未定"), "earnings_days": e_days,
        "earnings_reduce": earnings_reduce, "fomc_reduce": fomc_reduce,
        "hold": "数週間〜数ヶ月（中期）",
        "comment": _comment(fund, snap, e_days),
    }


def _comment(fund, snap, e_days) -> str:
    parts = []
    if snap and snap.get("above_sma200"):
        parts.append("長期トレンドは上向き。")
    else:
        parts.append("長期トレンドは弱め。慎重に。")
    rsi = snap.get("rsi", 50) if snap else 50
    if rsi > 70:
        parts.append("短期は買われすぎ→押し目を待つ。")
    elif rsi < 35:
        parts.append("売られすぎ→反発もあるが下落継続に注意。")
    if e_days is not None and 0 <= e_days <= config.EARNINGS_SOON_DAYS:
        parts.append(f"決算が約{e_days}日後→新規は小さく/決算後待ちが安全。")
    parts.append("必ず損切りを置き、利確1で半分売り残りを伸ばす。")
    return " ".join(parts)
