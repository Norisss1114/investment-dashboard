"""
backtest.py — 簡易バックテスト。
戦略(シンプル):
  - 買い: 株価が200日線より上 かつ RSIが35〜55から上抜け（押し目からの反発）
  - 売り: 損切り -8% / 利確 +20% / または株価が50日線を割れたら手仕舞い
  - 同時に1ポジションのみ
出力: 勝率 / 平均利益 / 平均損失 / 最大DD / 戦略リターン vs буy&hold
※ あくまで概算。手数料・スリッページ・約定ズレは未考慮。
"""
import numpy as np
import pandas as pd
from modules import indicators


def backtest_ticker(df: pd.DataFrame, stop_pct=8.0, take_pct=20.0) -> dict:
    if df is None or len(df) < 60:
        return {"ok": False, "reason": "データ不足"}
    d = indicators.add_all_indicators(df).dropna(subset=["Close"]).copy()
    close = d["Close"].values
    sma50 = d["SMA50"].values
    sma200 = d["SMA200"].values
    rsi = d["RSI"].values

    in_pos = False
    entry = 0.0
    trades = []          # 各トレードのリターン%
    equity = [1.0]       # 戦略の資産推移（1.0スタート）
    cash = 1.0
    shares = 0.0

    for i in range(1, len(d)):
        price = close[i]
        # 含み込みのエクイティ更新
        equity.append(cash + shares * price)

        if not in_pos:
            cross_up = rsi[i - 1] < 55 and rsi[i] >= 55 and rsi[i - 1] >= 30
            trend_ok = price > sma200[i] if not np.isnan(sma200[i]) else False
            if trend_ok and cross_up:
                in_pos = True
                entry = price
                shares = cash / price
                cash = 0.0
        else:
            stop_price = entry * (1 - stop_pct / 100)
            take_price = entry * (1 + take_pct / 100)
            exit_now = False
            if price <= stop_price or price >= take_price:
                exit_now = True
            elif not np.isnan(sma50[i]) and price < sma50[i]:
                exit_now = True
            if exit_now:
                ret = price / entry - 1
                trades.append(ret * 100)
                cash = shares * price
                shares = 0.0
                in_pos = False

    # 最後に持っていたら時価で清算（記録のみ）
    final_equity = cash + shares * close[-1]
    eq = pd.Series(equity)
    running_max = eq.cummax()
    dd = (eq / running_max - 1.0)
    max_dd = float(dd.min() * 100) if len(dd) else 0.0

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    n = len(trades)
    bh_return = (close[-1] / close[0] - 1) * 100  # buy&hold

    return {
        "ok": True, "trades": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "avg_win": round(float(np.mean(wins)), 1) if wins else 0.0,
        "avg_loss": round(float(np.mean(losses)), 1) if losses else 0.0,
        "max_dd": round(max_dd, 1),
        "strategy_return": round((final_equity - 1.0) * 100, 1),
        "buyhold_return": round(bh_return, 1),
    }


def backtest_watchlist(results) -> list:
    out = []
    for r in results:
        if not r.get("ok"):
            continue
        bt = backtest_ticker(r["df"])
        bt["ticker"] = r["ticker"]
        out.append(bt)
    return out
