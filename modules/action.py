"""
action.py — 「今日やること」を1行で決めるロジック（v2の中核）。
6種類のアクションに分類し、初心者向けの一言を作る。
  今すぐ買い / 押し目待ち / 決算後まで待ち / ニュース待ち / 買わない / 危険
"""
import config


def decide_action(scores, snap, fund, plan, news_sum, market_mode) -> dict:
    verdict = scores["verdict"]
    total = scores["total"]
    rsi = snap.get("rsi", 50) if snap else 50
    above200 = snap.get("above_sma200", False) if snap else False
    e_days = plan.get("earnings_days")
    danger_news = (news_sum.get("categories", {}).get("危険", 0) > 0) or news_sum.get("avg_impact", 0) <= -2.5
    entry1 = plan.get("entry1"); entry2 = plan.get("entry2"); stop = plan.get("stop")
    price = plan.get("price")

    # ---- 優先順位つき判定 ----
    # 1) 危険：相場が危険モード or 強い悪材料＋下落
    if market_mode == "危険" or (danger_news and not above200):
        action = "危険"
        today = f"相場/個別リスクが高い。新規は避け、保有は一部利益確定や縮小も検討。"
    # 2) 決算後まで待ち：決算が近い
    elif e_days is not None and 0 <= e_days <= config.EARNINGS_SOON_DAYS:
        action = "決算後まで待ち"
        today = f"決算まで約{e_days}日。新規買いは決算後まで待つ（決算は株価が大きく動く）。"
    # 3) ニュース待ち：悪材料で不透明
    elif danger_news:
        action = "ニュース待ち"
        today = f"弱気/危険ニュースで不透明（平均インパクト{news_sum.get('avg_impact',0):+.1f}）。落ち着くまで様子見。"
    # 4) 今すぐ買い：BUYかつ過熱しておらず長期トレンド良好
    elif verdict == "BUY" and rsi <= 68 and above200:
        action = "今すぐ買い"
        today = f"条件良好。第1エントリー ${entry1} 付近を資金の1/3だけ。損切り ${stop} を必ず置く。"
    # 5) 押し目待ち：素地は良いが高値圏 or WATCH
    elif (verdict in ("BUY", "WATCH")) and (rsi > 68 or not above200 or total >= config.WATCH_THRESHOLD):
        action = "押し目待ち"
        reason = "高値圏(RSI高い)" if rsi > 68 else ("長期トレンドが弱め" if not above200 else "もう一段の確認待ち")
        today = f"{reason}。${entry2} 以下まで待つ。買うなら資金の1/3から。"
    # 6) 買わない：スコア低い
    else:
        action = "買わない"
        today = f"総合{total:.0f}点と低め。今回は見送り。条件が整うまでウォッチのみ。"

    meta = config.ACTIONS.get(action, {"color": "#64748b", "emoji": "•"})
    return {"action": action, "today": f"{fund.get('ticker','')}：{today}".lstrip("："),
            "color": meta["color"], "emoji": meta["emoji"]}


def avoid_reasons(scores, snap, fund, plan, news_sum) -> list:
    """「今は買わない方がいい」理由を列挙（該当するものだけ）。"""
    rs = []
    rsi = snap.get("rsi", 50) if snap else 50
    price = plan.get("price", 0); avg = plan.get("avg_entry", price); stop = plan.get("stop", 0)
    tp2 = plan.get("tp2", price)
    if rsi > 72:
        rs.append(f"上がりすぎ（RSI {rsi:.0f}）")
    if plan.get("earnings_days") is not None and 0 <= plan["earnings_days"] <= config.EARNINGS_SOON_DAYS:
        rs.append(f"決算直前（約{plan['earnings_days']}日後）")
    if snap and snap.get("vol_ratio", 1) < 0.8:
        rs.append("出来高が弱い（関心薄）")
    if news_sum.get("categories", {}).get("危険", 0) > 0:
        rs.append("危険ニュースあり")
    if news_sum.get("avg_impact", 0) <= -2:
        rs.append(f"ニュースが悪い（平均{news_sum['avg_impact']:+.1f}）")
    if stop and avg and (avg - stop) / avg > 0.13:
        rs.append(f"損切り幅が広すぎる（-{(avg-stop)/avg*100:.0f}%）")
    # リスクリワード（利確2までの上昇 / 損切りまでの下落）
    if stop and avg and (avg - stop) > 0:
        rr = (tp2 - avg) / (avg - stop)
        if rr < 1.5:
            rs.append(f"リスクリワードが悪い（{rr:.1f}倍）")
    if scores["technical"] < config.SCORE_WEIGHTS["technical"] * 0.4:
        rs.append("テクニカルが弱い（下降トレンド）")
    return rs
