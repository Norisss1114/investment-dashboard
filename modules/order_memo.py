"""
order_memo.py — Moomoo手動発注用のメモ文を自動生成。
買いメモ（新規）と保有中メモ（売り/管理）の2種類。
"""
import config


def buy_memo(ticker, analysis, regime) -> str:
    if not (analysis and analysis.get("ok")):
        return f"【{ticker} 発注メモ】データ取得できず。Moomooで最新値を確認してください。"
    p = analysis["plan"]; a = analysis["action"]
    lines = [f"【{ticker} 発注メモ（新規買い）】",
             f"現在値：${p['price']}",
             f"第1買い：${p['entry1']} で指値（打診・資金の1/3）",
             f"第2買い：${p['entry2']} で指値（押し目）",
             f"第3買い：${p['entry3']} で指値（深押し）",
             f"損切り：${p['stop']} で逆指値（必ず設定）",
             f"利確1：${p['tp1']} で半分売り",
             f"利確2：${p['tp2']} で残り売り（利確3 ${p['tp3']} まで伸ばす案も）"]
    notes = []
    if p.get("fomc_reduce"):
        notes.append(f"FOMC前なので予定株数の一部のみ（{100-p['fomc_reduce']}%程度）")
    if p.get("earnings_reduce"):
        notes.append(f"決算が近い（{p.get('earnings')}）。新規は小さく/決算後待ちが安全")
    notes.append(f"アクション判定：{a['action']}")
    lines.append("注意：" + " / ".join(notes))
    return "\n".join(lines)


def hold_memo(ev: dict, hold_decision: dict, trailing: dict, todo: str = "") -> str:
    avg = ev.get("avg_cost", 0)
    tp1 = ev.get("init_tp1", 0); tp2 = ev.get("init_tp2", 0); stop = ev.get("init_stop", 0)
    # 利確3はポジションに無いので analysis のプランを参考、無ければ利確2×1.12 を目安
    tp3 = 0
    a = ev.get("analysis") or {}
    if a.get("ok"):
        tp3 = a["plan"].get("tp3", 0)
    if not tp3 and tp2:
        tp3 = round(tp2 * 1.12, 2)

    lines = [f"【{ev['ticker']} 保有中メモ（Moomoo手動注文用）】",
             f"平均取得単価：${avg}",
             f"現在値：${ev['current']}",
             f"保有株数：{ev['shares']:.0f} 株",
             f"含み損益：{ev['pl_pct']:+.1f}%（${ev['pl']:,.0f}）",
             f"推奨損切り：${stop or '未設定（要設定）'}（逆指値）",
             f"利確1：${tp1 or '—'}　利確2：${tp2 or '—'}　利確3：${tp3 or '—'}",
             f"半分売る価格：${tp1 or '—'}（利確1で半分を指値売り）",
             f"全部撤退する価格：${stop or '—'}（損切り到達で全売り）"]
    rec = (trailing or {}).get("recommended")
    if rec:
        lines.append(f"トレーリングストップ候補：${rec['price']}（{rec['label']}）")
    lines.append(f"対応：{hold_decision['action']} — {hold_decision['reason']}")
    if todo:
        lines.append(f"今日やること：{todo}")
    lines.append("※これは自動発注ではありません。Moomooで手動で指値/逆指値を設定してください。")
    return "\n".join(lines)
