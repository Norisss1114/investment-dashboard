"""
reflection.py — 売買履歴から反省コメントを生成。
APIキーがあればAI、なければルールベース。
"""
from modules import history as hist_mod
from modules import news as news_mod  # AI呼び出しを再利用


def rule_based(history: list) -> list:
    st = hist_mod.stats(history)
    if st["n"] == 0:
        return ["まだ確定取引がありません。売買履歴を入力すると反省コメントが出ます。"]

    comments = []
    pats = hist_mod.failure_patterns(history, st)
    comments += pats

    # 総評
    pf = st["profit_factor"]
    if st["win_rate"] < 50 and pf != float("inf") and pf >= 1.2:
        comments.append("✅ 勝率は50%未満ですが、損小利大（プロフィットファクター>1.2）なので問題ありません。この形を維持しましょう。")
    elif st["win_rate"] >= 60 and pf < 1:
        comments.append("⚠️ 勝率は高いのに収支がマイナス気味です。1回の負けが大きすぎないか確認を（損切りを早く）。")
    if pf == float("inf"):
        comments.append("負け取引がまだ無いか損失ゼロです。サンプルが増えると傾向が見えます。")

    adh = hist_mod.rule_adherence(history, st)
    if adh["good"]:
        comments.append("📘 記録もしっかり、深い損切りも無し。ルール遵守できています。")
    else:
        if adh["deep_losses"]:
            comments.append(f"📕 -12%超の損失が{adh['deep_losses']}件。損切りラインを先に決めて必ず守りましょう。")
        if adh["reason_rate"] < 70:
            comments.append("📕 売買理由の記録が少なめです。理由を書くと反省が効きます。")
    return comments


def ai_based(history: list):
    """AIに履歴の要約を渡して反省コメントを得る。失敗時 None。"""
    st = hist_mod.stats(history)
    if st["n"] == 0:
        return None
    summary = (f"確定{st['n']}件 勝率{st['win_rate']}% 平均利益{st['avg_win_pct']}% "
               f"平均損失{st['avg_loss_pct']}% PF{st['profit_factor']} 合計損益{st['total_pnl']}")
    sample = "; ".join(f"{c['ticker']} {c['pnl_pct']:+.0f}%({c.get('reason','')})" for c in st["closed"][:15])
    prompt = ("あなたは投資コーチです。以下の売買統計とサンプルから、初心者向けに"
              "改善点を3〜5個、やさしく具体的に箇条書きで。日本語、各行『・』始まり、説教臭くなく。\n"
              f"統計: {summary}\nサンプル: {sample}")
    raw = news_mod._call_ai(prompt)
    if not raw:
        return None
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return lines or None


def reflect(history: list) -> dict:
    ai = ai_based(history)
    if ai:
        return {"source": "ai", "comments": ai}
    return {"source": "rule", "comments": rule_based(history)}
