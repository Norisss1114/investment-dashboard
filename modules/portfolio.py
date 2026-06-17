"""
portfolio.py — ポジション台帳（保有銘柄）の保存・読込と、
1銘柄ごとの評価・ポートフォリオ全体のリスク集計。
"""
import datetime as dt
import config
from modules import storage, themes as themes_mod
from modules.utils import days_until, safe_float

POSITION_FIELDS = ["ticker", "avg_cost", "shares", "buy_date", "memo", "purpose",
                   "init_stop", "init_tp1", "init_tp2"]


def load_positions() -> list:
    data = storage.load_json(config.POSITIONS_PATH, [])
    out = []
    if isinstance(data, list):
        for p in data:
            try:
                out.append(_normalize(p))
            except Exception:
                continue
    return out


def save_positions(positions: list) -> bool:
    clean = []
    for p in positions:
        try:
            clean.append(_normalize(p))
        except Exception:
            continue
    return storage.save_json(config.POSITIONS_PATH, clean)


def _normalize(p: dict) -> dict:
    return {
        "ticker": str(p.get("ticker", "")).strip().upper(),
        "avg_cost": safe_float(p.get("avg_cost")),
        "shares": safe_float(p.get("shares")),
        "buy_date": str(p.get("buy_date", "")),
        "memo": str(p.get("memo", "")),
        "purpose": str(p.get("purpose", "")),
        "init_stop": safe_float(p.get("init_stop")),
        "init_tp1": safe_float(p.get("init_tp1")),
        "init_tp2": safe_float(p.get("init_tp2")),
    }


def evaluate_position(pos: dict, analysis: dict) -> dict:
    """保有1件を評価。analysis は engine.analyze_ticker の結果（無ければ None 可）。
    v15.2: 現在値は get_quote の多段フォールバックで解決し、取得できなければ
    取得単価を仮値として使う（price_is_fallback=True で明示）。"""
    cur = None
    snap = {}
    earnings_days = None
    price_source = None
    price_error = None
    if analysis and analysis.get("ok"):
        cur = analysis["fund"].get("price")
        snap = analysis.get("snap", {})
        earnings_days = analysis["plan"].get("earnings_days")
        price_source = analysis.get("price_source")
        price_error = analysis.get("price_error")
    elif analysis:  # ok=False でも現在値だけは拾えることがある
        cur = (analysis.get("fund") or {}).get("price")
        price_source = analysis.get("price_source")
        price_error = analysis.get("price_error")

    # 現在値が無ければ、保有銘柄でも確実に最新値を引きにいく（Cloud対策）
    if not cur or safe_float(cur, 0) <= 0:
        from modules import data_fetch
        try:
            q = data_fetch.get_quote(pos["ticker"])
        except Exception as e:
            q = {"price": None, "source": "unavailable", "error": str(e)}
        if q.get("price"):
            cur = q["price"]; price_source = q["source"]; price_error = q.get("error")
        else:
            price_error = q.get("error") or price_error
            price_source = price_source if price_source not in (None, "mock", "unavailable") else "unavailable"

    # それでも無ければ取得単価を仮値（誤表示しないようフラグを立てる）
    price_is_fallback = False
    if not cur or safe_float(cur, 0) <= 0:
        cur = pos["avg_cost"]
        price_source = "fallback_avg_cost"
        price_is_fallback = True
    cur = safe_float(cur, pos["avg_cost"])

    shares = pos["shares"]
    avg = pos["avg_cost"]
    value = cur * shares
    cost = avg * shares
    pl = value - cost
    pl_pct = (cur / avg - 1) * 100 if avg else 0.0

    days_held = None
    d = days_until(pos.get("buy_date"))
    if d is not None:
        days_held = -d if d <= 0 else 0  # 過去日なら経過日数

    return {
        **pos,
        "current": round(cur, 2),
        "value": round(value, 2),
        "cost": round(cost, 2),
        "pl": round(pl, 2),
        "pl_pct": round(pl_pct, 1),
        "days_held": days_held if days_held is not None else "—",
        "earnings_days": earnings_days,
        "themes": themes_mod.classify(pos["ticker"], analysis["fund"] if analysis and analysis.get("ok") else None,
                                      analysis["news"]["items"] if analysis and analysis.get("ok") else None),
        "has_stop": pos["init_stop"] > 0,
        "snap": snap,
        "analysis": analysis,
        "price_source": price_source,
        "price_error": price_error,
        "price_is_fallback": price_is_fallback,
    }


def portfolio_summary(evals: list, total_capital: float) -> dict:
    """ポートフォリオ全体の集計＋危険判定。"""
    total_value = sum(e["value"] for e in evals)
    total_cost = sum(e["cost"] for e in evals)
    total_pl = total_value - total_cost
    cash = max(0.0, total_capital - total_value)
    cash_pct = (cash / total_capital * 100) if total_capital else 0.0

    # テーマ別比率（評価額ウェイト）
    bucket_value = {b: 0.0 for b in config.THEME_BUCKETS}
    sector_value = {}
    for e in evals:
        for b in themes_mod.buckets_for(e["themes"]):
            bucket_value[b] += e["value"]
        # 代表セクター = 先頭テーマ
        sec = e["themes"][0] if e["themes"] else "未分類"
        sector_value[sec] = sector_value.get(sec, 0.0) + e["value"]

    denom = total_value if total_value else 1.0
    theme_pct = {b: round(v / denom * 100, 1) for b, v in bucket_value.items()}
    sector_pct = {s: round(v / denom * 100, 1) for s, v in sorted(sector_value.items(), key=lambda x: -x[1])}

    # 1銘柄集中度（対総資金）
    top_ticker, top_pct = None, 0.0
    for e in evals:
        pct = (e["value"] / total_capital * 100) if total_capital else 0.0
        if pct > top_pct:
            top_pct, top_ticker = pct, e["ticker"]

    earnings_soon = [e["ticker"] for e in evals
                     if e.get("earnings_days") is not None and 0 <= e["earnings_days"] <= config.EARNINGS_RISK_DAYS]
    no_stop = [e["ticker"] for e in evals if not e["has_stop"]]

    # ---- 危険判定 ----
    warnings = []
    if top_ticker and top_pct >= config.CONCENTRATION_PCT:
        warnings.append({"level": "warn", "msg": f"集中しすぎ: {top_ticker} が総資金の {top_pct:.0f}%（{config.CONCENTRATION_PCT:.0f}%以上）"})
    ai_semi = max(theme_pct.get("AI関連", 0), theme_pct.get("半導体", 0))
    if ai_semi >= config.THEME_CONCENTRATION_PCT:
        warnings.append({"level": "warn", "msg": f"テーマ集中: AI/半導体が保有の {ai_semi:.0f}%（{config.THEME_CONCENTRATION_PCT:.0f}%以上）"})
    if len(earnings_soon) >= 2:
        warnings.append({"level": "warn", "msg": f"イベント警告: 決算{config.EARNINGS_RISK_DAYS}日以内が {len(earnings_soon)}銘柄（{', '.join(earnings_soon)}）"})
    if total_capital and cash_pct < config.MIN_CASH_PCT:
        warnings.append({"level": "danger", "msg": f"逃げ場がない: 現金比率 {cash_pct:.0f}%（{config.MIN_CASH_PCT:.0f}%未満）。下落時に動けません"})
    if no_stop:
        warnings.append({"level": "danger", "msg": f"⛔ 損切り未設定: {', '.join(no_stop)}。必ず損切りを決めてください"})

    return {
        "total_value": round(total_value, 2), "total_cost": round(total_cost, 2),
        "total_pl": round(total_pl, 2),
        "total_pl_pct": round((total_pl / total_cost * 100) if total_cost else 0.0, 1),
        "cash": round(cash, 2), "cash_pct": round(cash_pct, 1),
        "theme_pct": theme_pct, "sector_pct": sector_pct,
        "top_ticker": top_ticker, "top_pct": round(top_pct, 1),
        "earnings_soon": earnings_soon, "no_stop": no_stop,
        "warnings": warnings,
    }
