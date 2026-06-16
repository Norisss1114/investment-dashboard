"""
holding_action.py — 保有中の銘柄に対するアクション判定。
継続保有 / 一部利確 / 全利確 / 損切り / 買い増し禁止 / 買い増し検討 /
決算前に減らす / ニュース確認 / 危険。
"""
import config

ACTION_META = {
    "継続保有":     {"color": "#16a34a", "emoji": "🟢"},
    "買い増し検討": {"color": "#22c55e", "emoji": "➕"},
    "一部利確":     {"color": "#0ea5e9", "emoji": "💰"},
    "全利確":       {"color": "#06b6d4", "emoji": "🏁"},
    "決算前に減らす": {"color": "#f59e0b", "emoji": "🟠"},
    "買い増し禁止": {"color": "#eab308", "emoji": "⛔"},
    "ニュース確認": {"color": "#a855f7", "emoji": "🟣"},
    "損切り":       {"color": "#dc2626", "emoji": "✂️"},
    "危険":         {"color": "#b91c1c", "emoji": "🔴"},
}


def decide(ev: dict, market_mode: str = "中立") -> dict:
    cur = ev["current"]; avg = ev["avg_cost"]
    pl_pct = ev["pl_pct"]
    stop = ev.get("init_stop", 0); tp1 = ev.get("init_tp1", 0); tp2 = ev.get("init_tp2", 0)
    e_days = ev.get("earnings_days")
    snap = ev.get("snap", {}) or {}
    rsi = snap.get("rsi", 50)
    above200 = snap.get("above_sma200", False)
    analysis = ev.get("analysis") or {}
    news_sum = analysis.get("news_sum", {}) if analysis.get("ok") else {}
    danger_news = news_sum.get("categories", {}).get("危険", 0) > 0 or news_sum.get("avg_impact", 0) <= -2.5
    verdict = analysis.get("scores", {}).get("verdict") if analysis.get("ok") else None

    # ---- 優先順位つき ----
    if stop and cur <= stop:
        action = "損切り"
        reason = f"現在値 ${cur} が損切り ${stop} に到達。ルール通り撤退（損失を限定）。"
    elif market_mode == "危険" or (danger_news and not above200):
        action = "危険"
        reason = "相場/個別リスクが高い。利益が乗っていれば一部利確、含み損なら撤退も検討。"
    elif tp2 and cur >= tp2:
        action = "全利確"
        reason = f"現在値 ${cur} が利確2 ${tp2} 到達。目標達成。残りを利確して終了を検討。"
    elif tp1 and cur >= tp1:
        action = "一部利確"
        reason = f"含み益 +{pl_pct:.0f}% で利確1 ${tp1} に到達。半分売って利益を確保、残りは伸ばす。"
    elif e_days is not None and 0 <= e_days <= config.EARNINGS_SOON_DAYS:
        action = "決算前に減らす"
        reason = f"決算まで約{e_days}日。新規買い増しは禁止、保有株の{config.REDUCE_BEFORE_EARNINGS_PCT}%を減らす候補。"
    elif danger_news:
        action = "ニュース確認"
        reason = f"弱気/危険ニュースあり（平均{news_sum.get('avg_impact',0):+.1f}）。内容を確認し、悪材料なら縮小。"
    elif rsi > 72:
        action = "買い増し禁止"
        reason = f"RSI {rsi:.0f} で短期過熱。今は買い増ししない（押し目を待つ）。継続保有はOK。"
    elif verdict == "BUY" and cur < avg * 1.03 and above200 and rsi < 60:
        action = "買い増し検討"
        reason = f"評価が良好で取得単価付近。トレンドも上向き。資金管理の範囲で買い増し検討可。"
    else:
        action = "継続保有"
        reason = f"含み損益 {pl_pct:+.0f}%。判定はまだ保有継続。損切り ${stop or '未設定'} を必ず維持。"

    meta = ACTION_META.get(action, {"color": "#64748b", "emoji": "•"})
    today = f"{ev['ticker']}：{reason}"
    return {"action": action, "reason": reason, "today": today,
            "color": meta["color"], "emoji": meta["emoji"]}


def trailing_stop_suggestions(ev: dict) -> dict:
    """含み益が出ている銘柄の損切り引き上げ案。"""
    cur = ev["current"]; avg = ev["avg_cost"]; cur_stop = ev.get("init_stop", 0)
    snap = ev.get("snap", {}) or {}
    sma20 = snap.get("sma20")
    df = (ev.get("analysis") or {}).get("df")

    recent_low = None
    try:
        if df is not None and len(df) >= 20:
            recent_low = float(df["Low"].tail(20).min())
    except Exception:
        recent_low = None

    candidates = []
    candidates.append({"label": "現在値の-8%（基本）", "price": round(cur * 0.92, 2)})
    if sma20:
        candidates.append({"label": "20日移動平均の少し下", "price": round(sma20 * 0.98, 2)})
    if recent_low:
        candidates.append({"label": "直近20日安値の少し下", "price": round(recent_low * 0.99, 2)})
    candidates.append({"label": "取得単価（建値・最低限トントン）", "price": round(avg, 2)})

    # 推奨: 現在値より下 かつ 取得単価以上（利益確保）で最も高いもの
    valid = [c for c in candidates if c["price"] < cur]
    in_profit = cur > avg
    pick = None
    if in_profit:
        locked = [c for c in valid if c["price"] >= avg]
        pick = max(locked, key=lambda c: c["price"]) if locked else (max(valid, key=lambda c: c["price"]) if valid else None)
    else:
        pick = max(valid, key=lambda c: c["price"]) if valid else None

    note = ""
    if in_profit and pick and pick["price"] >= avg:
        note = "✅ この水準まで損切りを引き上げれば、最悪でも損はしない（利益を守れる）。"
    elif in_profit:
        note = "含み益あり。徐々に損切りを切り上げ、利益を守りましょう。"
    else:
        note = "まだ含み損。無理に切り上げず、初期損切りを厳守。"

    return {"candidates": candidates, "recommended": pick, "current_stop": cur_stop,
            "in_profit": in_profit, "note": note}


def moomoo_todo(ev: dict, hd: dict) -> str:
    """Moomooで今日やること（手動注文の1行）。自動発注ではない。"""
    t = ev["ticker"]; cur = ev["current"]
    stop = ev.get("init_stop", 0); tp1 = ev.get("init_tp1", 0); tp2 = ev.get("init_tp2", 0)
    action = hd["action"]
    if action == "損切り":
        return f"{t}: ${stop or cur} で成行/逆指値の売り（損切り実行）"
    if action == "全利確":
        return f"{t}: 残り全株を ${tp2 or cur} 付近で指値売り（利確完了）"
    if action == "一部利確":
        return f"{t}: ${tp1 or cur} で保有の半分を指値売り → 逆指値を建値以上へ引き上げ"
    if action == "決算前に減らす":
        return f"{t}: 決算前に保有の約{config.REDUCE_BEFORE_EARNINGS_PCT}%を成行/指値で減らす"
    if action == "買い増し検討":
        return f"{t}: ${round(cur*0.98,2)} 付近で買い増しの指値（資金管理の範囲で）＋逆指値 ${stop or '未設定'}"
    if action == "買い増し禁止":
        return f"{t}: 新規買い増しはしない。逆指値 ${stop or '未設定'} を維持"
    if action == "ニュース確認":
        return f"{t}: ニュース内容を確認。悪材料なら一部利確/逆指値 ${stop or '未設定'} を引き上げ"
    if action == "危険":
        return f"{t}: リスク高。逆指値 ${stop or cur} を必ず置き、利益が乗っていれば一部利確"
    return f"{t}: 逆指値 ${stop or '未設定'} を維持（無ければ設定）。継続保有"


def moomoo_todo(ev: dict, hd: dict) -> str:
    """『Moomooで今日やること』を1行で（手動発注の指示）。"""
    action = hd["action"]
    stop = ev.get("init_stop") or 0
    tp1 = ev.get("init_tp1") or 0
    tp2 = ev.get("init_tp2") or 0
    s = f"${stop}" if stop else "（未設定→要設定）"
    if action == "損切り":
        return f"逆指値 {s} で全株を売り（損切り）"
    if action == "一部利確":
        return f"利確1 ${tp1} で半分を指値売り、残りは逆指値 {s} を維持"
    if action == "全利確":
        return f"利確2 ${tp2} 付近で残り全株を指値売り"
    if action == "決算前に減らす":
        return f"決算前に保有の約{ __import__('config').REDUCE_BEFORE_EARNINGS_PCT }%を減らす（指値/成行）"
    if action == "買い増し禁止":
        return f"買い増ししない。逆指値 {s} を維持"
    if action == "買い増し検討":
        return f"取得単価 ${ev.get('avg_cost')} 付近で買い増し指値を検討（無理はしない）"
    if action == "ニュース確認":
        return f"悪材料を確認。内容次第で一部利確/縮小、逆指値 {s} を維持"
    if action == "危険":
        return f"新規は避ける。利益が乗っていれば一部利確、逆指値 {s} を引き上げ検討"
    return f"継続保有。逆指値 {s} を維持（利確1 ${tp1} 到達で半分売り）"
