"""
goal_plan.py (v16) — 目標達成プランモード。
現在資産・目標金額・期限・毎月追加資金・リスク許容度・最大許容損失・投資スタイルから、
必要利回り／現実性を計算し、保有・ウォッチ・発掘・おすすめ銘柄で目標向けプランを提案する。

⚠️ 投資助言ではありません。教育・分析用の判断補助です。無理な目標は正直に「非現実的」と表示します。
"""
import math

import config
from modules import storage

GOAL_PATH = "user_data/goal_plan.json"

HORIZONS = {"3ヶ月": 3, "6ヶ月": 6, "1年": 12, "2年": 24, "3年": 36}
RISK_TOLERANCES = ["低", "中", "高"]
MAX_LOSS_CHOICES = [5, 10, 15, 20]
STYLES = ["守り", "バランス", "攻め"]


# ============================================================ 保存・読込
def load_goal():
    """保存済みの目標プラン（無ければ None）。壊れていても落ちない。"""
    d = storage.load_json(GOAL_PATH, None)
    return d if isinstance(d, dict) else None


def save_goal(d) -> bool:
    return storage.save_json(GOAL_PATH, d)


# ============================================================ 必要利回り計算
def _future_value(current, monthly, n_months, r):
    """月利 r で n_months 後の資産（毎月 monthly を月初積立）。"""
    if abs(r) < 1e-12:
        return current + monthly * n_months
    growth = (1 + r) ** n_months
    return current * growth + monthly * ((growth - 1) / r)


def required_monthly_rate(current, monthly, n_months, goal):
    """目標到達に必要な月利を二分探索で求める。0%で届くなら0、100%でも届かないなら~1.0。"""
    current = max(0.0, float(current)); monthly = max(0.0, float(monthly))
    goal = float(goal); n = max(1, int(n_months))
    if _future_value(current, monthly, n, 0.0) >= goal:
        return 0.0
    lo, hi = 0.0, 1.0  # 月利 0%〜100%
    if _future_value(current, monthly, n, hi) < goal:
        return hi  # 100%/月でも届かない＝非現実的
    for _ in range(120):
        mid = (lo + hi) / 2
        if _future_value(current, monthly, n, mid) < goal:
            lo = mid
        else:
            hi = mid
    return hi


def feasibility(req_annual):
    """必要年率から現実性ラベル。"""
    if req_annual <= 0.12:
        return "現実的"
    if req_annual <= 0.25:
        return "やや難しい"
    if req_annual <= 0.50:
        return "かなり難しい"
    return "非現実的"


def required_risk_level(req_annual):
    if req_annual <= 0.10:
        return "低"
    if req_annual <= 0.25:
        return "中"
    if req_annual <= 0.45:
        return "高"
    return "非常に高い"


def goal_score(req_annual):
    """目標達成スコア 0-100（必要年率が低いほど高い）。"""
    return int(max(0, min(100, round(100 * (1 - req_annual / 0.80)))))


# ============================================================ スタイル別パラメータ
def style_params(style, risk_tolerance):
    """スタイル＋リスク許容度 → 推奨現金比率・最大ポジション%・銘柄数・分割回数。"""
    base = {
        "守り":     {"cash": 40, "max_pos": 12, "n": 7, "splits": 3},
        "バランス": {"cash": 25, "max_pos": 18, "n": 5, "splits": 3},
        "攻め":     {"cash": 12, "max_pos": 26, "n": 4, "splits": 2},
    }.get(style, {"cash": 25, "max_pos": 18, "n": 5, "splits": 3})
    p = dict(base)
    # リスク許容度で微調整（高いほど現金を減らしポジションを大きく）
    adj = {"低": (+8, -3), "中": (0, 0), "高": (-6, +4)}.get(risk_tolerance, (0, 0))
    p["cash"] = int(max(5, min(70, p["cash"] + adj[0])))
    p["max_pos"] = int(max(5, min(35, p["max_pos"] + adj[1])))
    return p


# ============================================================ 中核計算
def compute(inputs):
    """数値計算をまとめて返す。inputs: current_assets, goal_amount, horizon(ラベル),
    monthly_add, risk_tolerance, max_loss_pct, style。"""
    cur = max(0.0, float(inputs.get("current_assets", 0) or 0))
    goal = max(0.0, float(inputs.get("goal_amount", 0) or 0))
    n = HORIZONS.get(inputs.get("horizon", "1年"), 12)
    monthly = max(0.0, float(inputs.get("monthly_add", 0) or 0))
    style = inputs.get("style", "バランス")
    risk_tol = inputs.get("risk_tolerance", "中")
    max_loss_pct = float(inputs.get("max_loss_pct", 10) or 10)

    shortfall = max(0.0, goal - cur)
    total_contrib = monthly * n
    # 毎月追加を含めても足りない運用益
    need_gain = max(0.0, goal - cur - total_contrib)

    r_m = required_monthly_rate(cur, monthly, n, goal)
    req_annual = (1 + r_m) ** 12 - 1
    feas = feasibility(req_annual)
    score = goal_score(req_annual)
    risk_level = required_risk_level(req_annual)
    sp = style_params(style, risk_tol)

    # 1銘柄あたり最大損失額（資産 × 最大許容損失% を銘柄数で分散）
    total_loss_budget = cur * max_loss_pct / 100.0
    per_stock_loss = total_loss_budget / max(1, sp["n"])
    invest_budget = cur * (1 - sp["cash"] / 100.0)

    # 正直な助言
    advice = []
    if goal <= cur:
        advice.append("すでに目標金額に到達しています。利益確定とリスク管理を優先しましょう。")
    elif feas == "非現実的":
        advice.append("この期限ではリスクが高すぎます（必要リターンが非現実的）。")
        advice.append("毎月の追加資金を増やすか、期限を延ばす方が現実的です。")
    elif feas == "かなり難しい":
        advice.append("達成にはかなり高いリターンが必要です。期限延長や追加入金の増額を検討してください。")
        advice.append("一発を狙うとリスク過大。損切りを厳守し、分散してください。")
    elif feas == "やや難しい":
        advice.append("やや高めのリターンが必要です。無理な集中投資は避け、押し目を待ちましょう。")
    else:
        advice.append("現実的なペースです。リスク管理を守りながら着実に進めましょう。")
    if monthly == 0 and shortfall > 0:
        advice.append("毎月の追加資金を入れると、必要リターンを大きく下げられます。")

    return {
        "current_assets": round(cur, 2), "goal_amount": round(goal, 2),
        "horizon": inputs.get("horizon", "1年"), "n_months": n,
        "monthly_add": round(monthly, 2), "style": style,
        "risk_tolerance": risk_tol, "max_loss_pct": max_loss_pct,
        "shortfall": round(shortfall, 2), "total_contrib": round(total_contrib, 2),
        "need_gain": round(need_gain, 2),
        "req_monthly": round(r_m * 100, 2), "req_annual": round(req_annual * 100, 1),
        "feasibility": feas, "score": score, "risk_level": risk_level,
        "cash_pct": sp["cash"], "max_pos_pct": sp["max_pos"],
        "n_positions": sp["n"], "splits": sp["splits"],
        "invest_budget": round(invest_budget, 2),
        "total_loss_budget": round(total_loss_budget, 2),
        "per_stock_loss": round(per_stock_loss, 2),
        "advice": advice,
        "tp_rule": f"利確1で半分→利確2で残り（損切り {max_loss_pct:.0f}% 厳守）",
        "stop_rule": f"取得から約-{max_loss_pct:.0f}% または直近安値割れで損切り",
    }


# ============================================================ 銘柄候補
def _rec_from_analysis(t, a, held):
    if not (a and a.get("ok")):
        price = ((a or {}).get("fund") or {}).get("price") if a else None
        if not price or price <= 0:
            return None
        return {"ticker": t, "held": held, "price": round(price, 2), "score": 0, "verdict": None,
                "entry": round(price, 2), "stop": round(price * 0.92, 2),
                "tp1": round(price * 1.12, 2), "tp2": round(price * 1.25, 2),
                "note": "分析データ不足", "name": t}
    f, s, p = a["fund"], a["scores"], a["plan"]
    price = f.get("price")
    if not price or price <= 0:
        return None
    return {"ticker": t, "held": held, "price": round(float(price), 2),
            "score": float(s.get("total", 0) or 0), "verdict": s.get("verdict"),
            "entry": p.get("entry1", price), "stop": p.get("stop"),
            "tp1": p.get("tp1"), "tp2": p.get("tp2"),
            "earnings_days": p.get("earnings_days"),
            "note": " / ".join((a.get("avoid_reasons") or [])[:1]),
            "name": f.get("name", t)}


# v16.1: 保有/ウォッチを分けた役割語彙（表示用）
_HOLDING_ROLE = {
    "損切り": "損切り候補",
    "危険": "見直し", "ニュース確認": "見直し", "買い増し禁止": "見直し", "決算前に減らす": "見直し",
    "一部利確": "利確候補", "全利確": "利確候補",
    "継続保有": "継続", "買い増し検討": "継続",
}


def holding_role(action):
    """保有アクション(holding_action.decide の action) → 継続/利確候補/損切り候補/見直し。"""
    return _HOLDING_ROLE.get(action, "継続")


def watch_role(verdict, score=0):
    """ウォッチ判定 → 新規買い候補/押し目待ち/見送り。"""
    if verdict in ("強いBUY", "BUY"):
        return "新規買い候補"
    if verdict == "AVOID" or (score or 0) < 48:
        return "見送り"
    return "押し目待ち"


def _rec_from_discovery(it, held):
    price = it.get("entry") or 0
    if not price or price <= 0:
        return None
    return {"ticker": it["ticker"].upper(), "held": held, "price": round(float(price), 2),
            "score": float(it.get("score", 0) or 0), "verdict": it.get("verdict"),
            "entry": it.get("entry"), "stop": it.get("stop"),
            "tp1": it.get("tp1"), "tp2": round(float(price) * 1.25, 2),
            "note": (it.get("risk_points") or [""])[0] if it.get("risk_points") else "",
            "name": it.get("name", it["ticker"])}


def _role(rec):
    s = rec.get("score", 0); v = rec.get("verdict")
    if v == "AVOID" or s < 48:
        return "見送り"
    if rec.get("held") and s >= 58:
        return "主力"
    if s >= 75:
        return "主力"
    if s >= 64:
        return "成長枠"
    if s >= 54:
        return "守り枠"
    return "短期狙い"


def build_candidates(rmap, positions, regime, analyze_missing=True):
    """保有・ウォッチ(rmap)・発掘キャッシュ・おすすめ から候補レコードを作成。"""
    recs = {}
    pos_set = {p["ticker"].upper() for p in (positions or [])}

    for t, a in (rmap or {}).items():
        rec = _rec_from_analysis(t.upper(), a, t.upper() in pos_set)
        if rec:
            recs[t.upper()] = rec

    try:
        from modules import discovery
        c = discovery.load_cache()
        for it in (c.get("top20", []) if c else [])[:12]:
            t = it["ticker"].upper()
            if t not in recs:
                rec = _rec_from_discovery(it, t in pos_set)
                if rec:
                    recs[t] = rec
    except Exception:
        pass

    if analyze_missing:
        try:
            from modules import engine
            sugg = [t for grp in config.SUGGESTED_WATCHLIST.values() for t in grp]
            for t in sugg:
                t = t.upper()
                if t in recs or len(recs) >= 16:
                    continue
                try:
                    a = engine.analyze_ticker(t, regime["score"], regime["regime"],
                                              regime.get("fomc_days"), with_news=False)
                    rec = _rec_from_analysis(t, a, t in pos_set)
                    if rec:
                        recs[t] = rec
                except Exception:
                    continue
        except Exception:
            pass

    out = list(recs.values())
    for r in out:
        r["role"] = _role(r)
    out.sort(key=lambda x: (-x["score"], x["ticker"]))
    return out


def _safe(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def candidate_rows(candidates, comp):
    """画面表示用：各候補の推奨投入額・株数・貢献度などを付与（バランス基準）。"""
    invest = comp["invest_budget"]; max_pos = comp["max_pos_pct"]; cur = comp["current_assets"]
    picks = [c for c in candidates if c["role"] != "見送り"][: comp["n_positions"]]
    skipped = [c for c in candidates if c["role"] == "見送り"]
    tot_score = sum(_safe(c["score"]) for c in picks) or 1.0
    rows = []
    for c in picks:
        w = _safe(c["score"]) / tot_score
        cap_amt = cur * max_pos / 100.0
        amount = min(invest * w, cap_amt)
        price = _safe(c["price"]) or 1.0
        shares = int(amount // price)
        actual = round(shares * price, 2)
        tp1 = _safe(c.get("tp1")) or round(price * 1.12, 2)
        gain = max(0.0, (tp1 - price) * shares)
        contrib = round(gain / comp["shortfall"] * 100, 1) if comp["shortfall"] > 0 else 0.0
        rows.append({**c, "amount": actual, "shares": shares,
                     "gain_tp1": round(gain, 2), "contribution": contrib})
    return rows, skipped


# ============================================================ 3プラン
_VARIANTS = [
    ("守りプラン",   {"cash": 40, "n": 7, "max_pos": 12, "roles": ["守り枠", "主力", "成長枠"],
                    "note": "現金多め・分散重視・低リスク。下落に強いが伸びは控えめ。"}),
    ("バランスプラン", {"cash": 25, "n": 5, "max_pos": 18, "roles": ["主力", "成長枠", "守り枠"],
                    "note": "成長株＋現金のバランス。中リスク・中リターン。"}),
    ("攻めプラン",   {"cash": 12, "n": 4, "max_pos": 26, "roles": ["主力", "成長枠", "短期狙い"],
                    "note": "成長株多め・高リターン狙い。ただし損切り厳守が前提。"}),
]


def portfolio_variants(candidates, comp):
    """守り/バランス/攻め の3案を生成。"""
    cur = comp["current_assets"]
    variants = []
    for name, cfg in _VARIANTS:
        pool = [c for c in candidates if c["role"] in cfg["roles"]]
        pool = sorted(pool, key=lambda x: -_safe(x["score"]))[: cfg["n"]]
        invest = cur * (1 - cfg["cash"] / 100.0)
        tot = sum(_safe(c["score"]) for c in pool) or 1.0
        allocs, invested, max_loss = [], 0.0, 0.0
        for c in pool:
            w = _safe(c["score"]) / tot
            amount = min(invest * w, cur * cfg["max_pos"] / 100.0)
            price = _safe(c["price"]) or 1.0
            shares = int(amount // price)
            if shares <= 0:
                continue
            actual = round(shares * price, 2)
            stop = _safe(c.get("stop")) or round(price * 0.92, 2)
            loss = max(0.0, (price - stop) * shares)
            invested += actual; max_loss += loss
            allocs.append({"ticker": c["ticker"], "role": c["role"], "amount": actual,
                           "shares": shares, "entry": round(price, 2), "stop": round(stop, 2),
                           "tp1": round(_safe(c.get("tp1")) or price * 1.12, 2),
                           "tp2": round(_safe(c.get("tp2")) or price * 1.25, 2)})
        variants.append({
            "name": name, "cash_pct": cfg["cash"], "note": cfg["note"],
            "n_positions": len(allocs), "allocations": allocs,
            "invested": round(invested, 2), "cash_amount": round(cur - invested, 2),
            "max_loss": round(max_loss, 2),
            "max_loss_pct": round(max_loss / cur * 100, 1) if cur > 0 else 0.0,
        })
    return variants


def orders_for_memo(variants, prefer="バランスプラン"):
    """発注メモ用：選択プラン（既定はバランス）の発注候補リスト。"""
    chosen = next((v for v in variants if v["name"] == prefer), variants[1] if len(variants) > 1 else (variants[0] if variants else None))
    if not chosen:
        return []
    return [{"ticker": a["ticker"], "role": a["role"], "shares": a["shares"],
             "entry": a["entry"], "stop": a["stop"], "tp1": a["tp1"], "tp2": a["tp2"]}
            for a in chosen["allocations"]]


def home_line(saved):
    """ホーム『今日やること』向けの1行（保存済み目標があれば）。"""
    if not saved or not saved.get("summary"):
        return None
    s = saved["summary"]
    goal = s.get("goal_amount") or saved.get("goal_amount")
    if not goal:
        return None
    req_m = s.get("req_monthly")
    feas = s.get("feasibility", "")
    if feas == "非現実的":
        return f"🎯 目標 ${goal:,.0f} は今の期限では非現実的。期限延長か毎月入金の増額を検討。"
    return f"🎯 目標 ${goal:,.0f} には月 +{req_m:.1f}% 必要（{feas}）。無理な新規買いより損切り/逆指値の確認を優先。"
