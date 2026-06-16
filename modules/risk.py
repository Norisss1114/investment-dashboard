"""
risk.py — 資金管理（v2: 現金比率・3段別株数・損益シミュレーション）。
ルール: 1銘柄損失=総資金の1〜2% / 1銘柄上限=20% / 損切りなしは禁止。
"""
import math
import config


def position_sizing(total_capital, entry, stop, tp1=None, tp2=None,
                    risk_pct=None, max_pos_pct=None) -> dict:
    risk_pct = config.MAX_RISK_PER_TRADE_PCT if risk_pct is None else risk_pct
    max_pos_pct = config.MAX_POSITION_PCT if max_pos_pct is None else max_pos_pct
    res = {"ok": False, "warnings": [], "shares": 0}

    if total_capital <= 0:
        res["warnings"].append("総資金を入力してください。"); return res
    if entry <= 0:
        res["warnings"].append("エントリー価格が不正です。"); return res
    if stop <= 0 or stop >= entry:
        res["warnings"].append("⛔ 損切りが未設定/エントリー以上です。損切りなしの買いは禁止扱い。"); return res

    risk_amount = total_capital * (risk_pct / 100.0)
    per_share_risk = entry - stop
    shares_by_risk = math.floor(risk_amount / per_share_risk)
    max_position_value = total_capital * (max_pos_pct / 100.0)
    shares_by_pos = math.floor(max_position_value / entry)
    shares = max(0, min(shares_by_risk, shares_by_pos))

    position_value = shares * entry
    max_loss = shares * per_share_risk
    capped = "position" if (shares == shares_by_pos and shares_by_pos < shares_by_risk) else \
             ("risk" if shares == shares_by_risk else None)
    if shares == 0:
        res["warnings"].append("資金/損切り幅の都合で推奨0株。資金を増やすか値幅の小さい銘柄を。")

    # 3段別の株数（1/3ずつ）
    s1 = shares // 3; s2 = shares // 3; s3 = shares - s1 - s2

    # 損益シミュレーション
    profit_tp1 = round((tp1 - entry) * shares, 2) if tp1 else None
    profit_tp2 = round((tp2 - entry) * shares, 2) if tp2 else None
    # 利確1で半分売り、残り半分を利確2で売った場合
    half = shares // 2
    blended_profit = None
    if tp1 and tp2:
        blended_profit = round((tp1 - entry) * half + (tp2 - entry) * (shares - half), 2)

    res.update({
        "ok": shares > 0, "risk_pct": risk_pct, "max_pos_pct": max_pos_pct,
        "risk_amount": round(risk_amount, 2), "per_share_risk": round(per_share_risk, 2),
        "shares": shares, "shares_1": s1, "shares_2": s2, "shares_3": s3,
        "position_value": round(position_value, 2),
        "position_pct": round(position_value / total_capital * 100, 1) if total_capital else 0,
        "max_loss": round(max_loss, 2), "capped_by": capped,
        "profit_tp1": profit_tp1, "profit_tp2": profit_tp2, "blended_profit": blended_profit,
    })
    return res


def portfolio_plan(total_capital, market_regime, sized_rows) -> dict:
    """ポートフォリオ全体の推奨現金比率と配分サマリ。"""
    cash_pct = market_regime.get("cash_pct", 50)
    invest_budget = total_capital * (1 - cash_pct / 100)
    total_invest = sum(r.get("position_value", 0) for r in sized_rows)
    total_loss = sum(r.get("max_loss", 0) for r in sized_rows)
    return {
        "cash_pct": cash_pct,
        "recommended_cash": round(total_capital * cash_pct / 100, 2),
        "invest_budget": round(invest_budget, 2),
        "total_invest": round(total_invest, 2),
        "total_max_loss": round(total_loss, 2),
        "over_budget": total_invest > invest_budget + 1,
    }
