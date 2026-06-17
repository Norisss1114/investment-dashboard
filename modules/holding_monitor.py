"""
holding_monitor.py (v15) — 保有銘柄の Upcoming News/Events 自動監視と、
利確・損切り価格の自動提案。

対象は positions.json の全保有銘柄（固定銘柄ではなく、保有が増減すれば自動で変わる）。
APIキーが無くてもテクニカルだけで価格提案を出し、ニュース欄は「API未設定」と表示する。

⚠️ 投資助言ではありません。教育・分析用の判断補助です。最終発注は必ずMoomooで確認してください。
"""
import datetime as dt

import config
from modules.utils import safe_float
from modules import portfolio, holding_action, news as news_mod

# ニュース見出し → 8カテゴリ分類のキーワード
_CAT_KEYWORDS = [
    ("決算",        ["earnings", "eps", "quarter", "results", "決算", "四半期"]),
    ("ガイダンス",   ["guidance", "outlook", "forecast", "ガイダンス", "見通し", "業績予想"]),
    ("アナリスト評価", ["upgrade", "downgrade", "rating", "analyst", "initiated", "格上げ", "格下げ", "アナリスト", "投資判断"]),
    ("目標株価変更", ["price target", "pt ", "target raised", "target cut", "目標株価", "目標価格"]),
    ("規制/訴訟",    ["lawsuit", "sue", "probe", "investigation", "regulat", "antitrust", "fine", "sec ", "訴訟", "規制", "当局", "制裁", "提訴"]),
    ("業界ニュース",  ["sector", "industry", "peers", "rival", "competition", "supply chain", "業界", "競合", "供給"]),
    ("マクロ影響",   ["fed", "inflation", "cpi", "rate cut", "rate hike", "tariff", "jobs report", "fomc", "金利", "関税", "インフレ", "雇用統計"]),
]


# ============================================================ 価格レベル提案
def _atr(df, period=14):
    """ATR（平均的な値動きの幅）。df が無ければ None。"""
    try:
        if df is None or len(df) < 2:
            return None
        import pandas as pd
        h, l, c = df["High"], df["Low"], df["Close"]
        pc = c.shift(1)
        tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        v = float(tr.tail(period).mean())
        return v if v > 0 else None
    except Exception:
        return None


def _recent_high_low(df, window=20):
    try:
        if df is None or len(df) == 0:
            return None, None
        tail = df.tail(window)
        return float(tail["High"].max()), float(tail["Low"].min())
    except Exception:
        return None, None


def _pct(price, base):
    if not base:
        return "—"
    return f"{(price / base - 1) * 100:+.0f}%"


def _pctn(price, base):
    if not base:
        return 0
    return round((price / base - 1) * 100)


def price_levels(ev):
    """保有1件の利確/損切り等を理由つきで返す。
    返り値: stop/exit_all/tp1/tp2/tp3/half/trail/stop_limit と各 *_reason、atr。"""
    cur = safe_float(ev.get("current"), 0) or 0
    avg = safe_float(ev.get("avg_cost"), 0) or 0
    if cur <= 0:
        cur = avg or 1.0
    if avg <= 0:
        avg = cur
    snap = ev.get("snap") or {}
    df = (ev.get("analysis") or {}).get("df")
    a = _atr(df) or max(cur * 0.03, 0.01)
    sma50 = snap.get("sma50") or 0
    sma200 = snap.get("sma200") or 0
    rhigh, rlow = _recent_high_low(df)
    stop_pct = float(getattr(config, "DEFAULT_STOP_PCT", 8.0))

    # --- 推奨損切り（規律ライン：損失を限定）---
    cand = [(f"取得単価から約-{stop_pct:.0f}%", avg * (1 - stop_pct / 100))]
    if sma50:
        cand.append(("50日線の少し下", sma50 * 0.98))
    if rlow:
        cand.append(("直近20日安値の少し下", rlow * 0.99))
    cand.append(("現在値-2×ATR", cur - 2 * a))
    below = [(why, p) for why, p in cand if 0 < p < cur]
    if below:
        why, stop = max(below, key=lambda x: x[1])  # 現在値未満で最も高い=損失が最小
    else:
        why, stop = "現在値の-8%", cur * 0.92
    line_word = "50日線割れ想定" if sma50 else "直近安値割れ想定"
    stop_reason = f"取得単価から約{_pct(stop, avg)}、{line_word}（{why}）"

    # --- 全部撤退（最終防衛ライン：これを明確に割れたら全株撤退）---
    exit_all = min(avg * (1 - 2 * stop_pct / 100), stop * 0.97)
    if sma200:
        exit_all = min(exit_all, sma200 * 0.97)
    exit_reason = f"取得単価から約{_pct(exit_all, avg)}、長期線（200日）割れ目安。理由が崩れたら全株撤退"

    # --- 利確（段階的に）---
    tp1 = max(avg * 1.12, rhigh or 0)
    if rhigh and tp1 <= rhigh + 0.01:
        tp1_reason = f"取得単価から約+{_pctn(tp1, avg)}%、直近高値 ${round(rhigh, 2)} 付近"
    else:
        tp1_reason = f"取得単価から約+{_pctn(tp1, avg)}%（最初の利確ポイント）"
    tp2 = avg * 1.25
    tp2_reason = "取得単価から約+25%（伸びたら追加で利確）"
    tp3 = avg * 1.40
    tp3_reason = "取得単価から約+40%（大きく伸びた時の最終利確）"

    # --- 半分売る / トレーリング / 逆指値 ---
    half = tp1
    half_reason = "利確1で保有の半分を売り、残りは利益を伸ばす（利確しつつ上昇も取る）"
    trail = max(cur - 1.5 * a, 0)
    trail_reason = f"現在値-1.5×ATR(${round(a, 2)})。株価上昇に合わせて引き上げる損切り"
    if cur > avg:
        lock = max(avg * 1.005, cur - 2 * a)
        lock_reason = "建値（取得単価）より上に逆指値→最悪でも損しない位置まで確保"
    else:
        lock = stop
        lock_reason = "まだ含み損。初期損切りを死守する逆指値"

    return {
        "atr": round(a, 2),
        "stop": round(stop, 2), "stop_reason": stop_reason,
        "exit_all": round(exit_all, 2), "exit_all_reason": exit_reason,
        "tp1": round(tp1, 2), "tp1_reason": tp1_reason,
        "tp2": round(tp2, 2), "tp2_reason": tp2_reason,
        "tp3": round(tp3, 2), "tp3_reason": tp3_reason,
        "half": round(half, 2), "half_reason": half_reason,
        "trail": round(trail, 2), "trail_reason": trail_reason,
        "stop_limit": round(max(lock, 0), 2), "stop_limit_reason": lock_reason,
    }


# ============================================================ 影響予想
def _lab(score):
    return "ポジティブ" if score >= 1 else "ネガティブ" if score <= -1 else "中立"


def impact_forecast(ev):
    """短期(今日〜3日)/中期(1〜2週)/スイング(1〜3ヶ月) の影響予想。
    分析できない場合は『不明』。"""
    analysis = ev.get("analysis") or {}
    snap = ev.get("snap") or {}
    if not analysis.get("ok") or not snap:
        return {"短期": "不明", "中期": "不明", "スイング": "不明"}
    ns = analysis.get("news_sum", {}) or {}
    avg_impact = ns.get("avg_impact", 0) or 0
    rsi = snap.get("rsi", 50)
    macd_hist = snap.get("macd_hist", 0) or 0
    above50 = snap.get("above_sma50", False)
    above200 = snap.get("above_sma200", False)

    short = _lab(avg_impact + (1 if macd_hist > 0 else -1 if macd_hist < 0 else 0)
                 + (-1 if rsi > 72 else 0))
    mid = _lab((1 if above50 else -1) + (avg_impact / 2))
    swing = _lab((1 if above200 else -1) + (0.5 if above50 else -0.5))
    return {"短期": short, "中期": mid, "スイング": swing}


# ============================================================ ニュース分類
def classify_news_event(item):
    """ニュース見出しを (カテゴリ, 重要度高/中/低) に分類。"""
    h = (item.get("headline", "") or "")
    hl = h.lower()
    cat = "個別材料"
    for name, kws in _CAT_KEYWORDS:
        if any(k in hl or k in h for k in kws):
            cat = name
            break
    impact = int(item.get("impact", 0) or 0)
    if item.get("category") == "危険" or abs(impact) >= 4:
        imp = "高"
    elif abs(impact) >= 2:
        imp = "中"
    else:
        imp = "低"
    return cat, imp


def news_events_from_analysis(analysis, limit=5):
    """分析結果のニュース項目を分類して返す。ニュース未取得なら []。"""
    if not (analysis and analysis.get("ok")):
        return []
    items = (analysis.get("news") or {}).get("items", []) or []
    out = []
    for it in items[:limit]:
        cat, imp = classify_news_event(it)
        out.append({"headline": it.get("headline", ""), "source": it.get("source", ""),
                    "category": cat, "importance": imp,
                    "lean": it.get("lean", "neutral"), "impact": int(it.get("impact", 0) or 0)})
    # 重要度高→中→低
    order = {"高": 0, "中": 1, "低": 2}
    out.sort(key=lambda x: order.get(x["importance"], 3))
    return out


def news_importance(news_events):
    """銘柄全体のニュース重要度（最大）。"""
    if not news_events:
        return "—"
    for level in ("高", "中", "低"):
        if any(n["importance"] == level for n in news_events):
            return level
    return "—"


# ============================================================ 今日の対応
def daily_action(ev, hd, levels, forecast, earnings_days):
    """保有1件の『今日の対応』を1行で。返り値 (label, color)。"""
    cur = ev.get("current", 0)
    pl_pct = ev.get("pl_pct", 0)
    stop = levels["stop"]; tp1 = levels["tp1"]
    act = hd.get("action")

    if act == "損切り" or (stop and cur <= stop):
        return "損切りライン接近・撤退", "#dc2626"
    if earnings_days is not None and 0 <= earnings_days <= config.EARNINGS_SOON_DAYS:
        return f"決算まで{earnings_days}日。利確優先／買い増し禁止", "#f59e0b"
    if cur <= stop * 1.03:
        return "損切りライン接近。逆指値を確認", "#f97316"
    if cur >= tp1:
        return "利確1到達。半分利確を検討", "#0ea5e9"
    if forecast.get("短期") == "ネガティブ" or act in ("ニュース確認", "危険"):
        return "悪材料の可能性。ニュース待ち／買い増し禁止", "#a855f7"
    if pl_pct > 0:
        return "逆指値を建値以上へ引き上げ", "#16a34a"
    return "何もしない（継続保有）", "#64748b"


# ============================================================ 集約
def _name_of(pos):
    t = pos.get("ticker", "")
    return config.TICKER_NAMES.get(t) or (pos.get("memo") or t)


def monitor_position(pos, analysis, regime):
    """保有1件のモニタ情報をまとめて返す（追加のネットワーク取得はしない）。"""
    ev = portfolio.evaluate_position(pos, analysis)
    mode = regime.get("regime", "中立") if isinstance(regime, dict) else "中立"
    hd = holding_action.decide(ev, mode)
    levels = price_levels(ev)
    forecast = impact_forecast(ev)
    news_events = news_events_from_analysis(analysis)
    e_days = ev.get("earnings_days")
    today, today_color = daily_action(ev, hd, levels, forecast, e_days)
    has_news = bool(analysis and analysis.get("ok") and analysis.get("with_news")
                    and (analysis.get("news") or {}).get("source") != "skip")
    return {
        "ticker": pos.get("ticker", ""),
        "name": _name_of(pos),
        "shares": ev.get("shares"),
        "avg_cost": ev.get("avg_cost"),
        "current": ev.get("current"),
        "pl_pct": ev.get("pl_pct"),
        "earnings_days": e_days,
        "earnings_date": (analysis.get("plan", {}) or {}).get("earnings", "未定") if analysis and analysis.get("ok") else "未取得",
        "ev": ev, "hd": hd, "levels": levels, "forecast": forecast,
        "news_events": news_events,
        "news_importance": news_importance(news_events),
        "has_news": has_news,
        "today": today, "today_color": today_color,
    }


def monitor_all(positions, rmap, regime):
    """全保有銘柄のモニタ情報。positions が空なら []。"""
    out = []
    for pos in positions:
        t = str(pos.get("ticker", "")).upper()
        out.append(monitor_position(pos, (rmap or {}).get(t), regime))
    # 緊急度の高い順（損切り→決算接近→利確→その他）
    def _rank(m):
        a = m["hd"]["action"]
        if a == "損切り":
            return 0
        if m["earnings_days"] is not None and 0 <= m["earnings_days"] <= config.EARNINGS_SOON_DAYS:
            return 1
        if a in ("一部利確", "全利確"):
            return 2
        if a in ("危険", "ニュース確認", "買い増し禁止"):
            return 3
        return 5
    out.sort(key=_rank)
    return out


def home_todos(monitors, limit=5):
    """ホーム『今日やること』向けの短い文字列リスト（最大 limit 件）。"""
    todos = []
    for m in monitors:
        t = m["ticker"]; lv = m["levels"]; e = m["earnings_days"]
        act = m["hd"]["action"]
        if act == "損切り":
            todos.append((m["today_color"], f"{t}：損切りライン ${lv['stop']} 接近。逆指値で撤退を確認"))
        elif e is not None and 0 <= e <= config.EARNINGS_SOON_DAYS:
            todos.append((m["today_color"], f"{t}：決算まで{e}日。利確1 ${lv['tp1']} 到達なら一部利確"))
        elif act in ("一部利確", "全利確") or (m["current"] and m["current"] >= lv["tp1"]):
            todos.append((m["today_color"], f"{t}：利確1 ${lv['tp1']} 到達。半分利確を検討"))
        elif act in ("ニュース確認", "危険"):
            todos.append((m["today_color"], f"{t}：悪材料の可能性。買い増し禁止・逆指値 ${lv['stop_limit']} を確認"))
        elif m["pl_pct"] and m["pl_pct"] > 0:
            todos.append((m["today_color"], f"{t}：逆指値を ${lv['stop_limit']} に設定（利益を守る）"))
        else:
            todos.append((m["today_color"], f"{t}：継続保有。逆指値 ${lv['stop']} を維持"))
        if len(todos) >= limit:
            break
    return todos


def order_memo_lines(m):
    """発注メモ向け：Moomooで設定する注文の行リスト。"""
    lv = m["levels"]; t = m["ticker"]
    return [
        f"Limit（利確1）: ${lv['tp1']} — {lv['tp1_reason']}",
        f"Limit（利確2）: ${lv['tp2']} — {lv['tp2_reason']}",
        f"Stop（損切り）: ${lv['stop']} — {lv['stop_reason']}",
        f"逆指値（利益確保）: ${lv['stop_limit']} — {lv['stop_limit_reason']}",
        f"Trailing Stop候補: ${lv['trail']} — {lv['trail_reason']}",
        f"半分売る価格: ${lv['half']} — {lv['half_reason']}",
        f"全部撤退価格: ${lv['exit_all']} — {lv['exit_all_reason']}",
    ]


# ============================================================ Upcoming Events 取得（Finnhub）
def events_available():
    return bool(config.get_secret("FINNHUB_API_KEY"))


def fetch_events(ticker):
    """Finnhubから earnings calendar / analyst rating / price target を取得。
    取得できなくても落とさず note を返す。返り値の available=False ならAPI未設定。"""
    ticker = (ticker or "").strip().upper()
    result = {"ticker": ticker, "available": False, "earnings_date": None,
              "price_target": None, "recommendation": None, "errors": [], "note": ""}
    key = config.get_secret("FINNHUB_API_KEY")
    if not key:
        result["note"] = "API未設定（Finnhub）。テクニカルのみで提案します。"
        return result
    result["available"] = True
    try:
        import requests
    except Exception:
        result["note"] = "requests 未導入"
        return result

    base = "https://finnhub.io/api/v1"
    today = dt.date.today()

    # --- earnings calendar（今後の決算日）---
    try:
        r = requests.get(f"{base}/calendar/earnings",
                         params={"from": today.isoformat(),
                                 "to": (today + dt.timedelta(days=120)).isoformat(),
                                 "symbol": ticker, "token": key}, timeout=8)
        if r.status_code == 200:
            rows = (r.json() or {}).get("earningsCalendar", []) or []
            if rows:
                d = rows[0].get("date")
                result["earnings_date"] = d
                try:
                    result["earnings_days"] = (dt.date.fromisoformat(d) - today).days
                except Exception:
                    result["earnings_days"] = None
    except Exception as e:
        result["errors"].append(f"earnings: {type(e).__name__}")

    # --- price target（目標株価）---
    try:
        r = requests.get(f"{base}/stock/price-target",
                         params={"symbol": ticker, "token": key}, timeout=8)
        if r.status_code == 200:
            d = r.json() or {}
            if d.get("targetMean"):
                result["price_target"] = {"mean": d.get("targetMean"), "high": d.get("targetHigh"),
                                          "low": d.get("targetLow"), "median": d.get("targetMedian")}
    except Exception as e:
        result["errors"].append(f"price_target: {type(e).__name__}")

    # --- recommendation（アナリスト評価）---
    try:
        r = requests.get(f"{base}/stock/recommendation",
                         params={"symbol": ticker, "token": key}, timeout=8)
        if r.status_code == 200:
            arr = r.json() or []
            if arr:
                d = arr[0]
                result["recommendation"] = {"period": d.get("period"), "buy": d.get("buy", 0),
                                            "hold": d.get("hold", 0), "sell": d.get("sell", 0),
                                            "strongBuy": d.get("strongBuy", 0), "strongSell": d.get("strongSell", 0)}
    except Exception as e:
        result["errors"].append(f"recommendation: {type(e).__name__}")

    if not (result["earnings_date"] or result["price_target"] or result["recommendation"]):
        result["note"] = "イベント未取得（該当データなし、または一時的に取得できませんでした）。"
    return result
