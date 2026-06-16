"""
notify.py (v12) — 毎朝の通知（判断補助）。
発掘TOP3・保有アクション・ニュース要約・注文メモを1通にまとめ、
Discord Webhook（優先）/ Telegram / メール(SMTP) へ送信。
※自動売買ではありません。発注はユーザーがMoomooで手動。通知は分析補助です。
APIキー/Webhook未設定でも落ちません（「通知先未設定」を返す）。
"""
import os
import json
import datetime as dt

import config
from modules import storage
from modules import discovery as disc_mod
from modules import news_analysis as na_mod


# ---------------- 設定 ----------------
def load_settings():
    s = storage.load_json(config.NOTIFY_SETTINGS_PATH, None)
    if not s:
        s = {"enabled": False, "time": config.NOTIFY_DEFAULT_TIME, "target": "未設定",
             "discord_webhook": "", "telegram_token": "", "telegram_chat": "",
             "email_to": "", "smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_pass": ""}
    return s


def save_settings(s):
    return storage.save_json(config.NOTIFY_SETTINGS_PATH, s)


def _webhook(settings):
    return (settings.get("discord_webhook") or config.get_secret("DISCORD_WEBHOOK_URL") or "").strip()


def target_ready(settings):
    t = settings.get("target")
    if t == "Discord Webhook":
        return bool(_webhook(settings))
    if t == "Telegram Bot":
        return bool((settings.get("telegram_token") or config.get_secret("TELEGRAM_TOKEN")) and
                    (settings.get("telegram_chat") or config.get_secret("TELEGRAM_CHAT")))
    if t == "メール(SMTP)":
        return bool(settings.get("smtp_host") and settings.get("email_to"))
    return False


# ---------------- ダイジェスト生成 ----------------
def build_digest(regime, events, positions, watchlist, analyze, top3_news=True):
    """returns dict with structured parts + text + markdown."""
    today = dt.date.today().isoformat()

    # A. 市場環境
    week_events = events[0] if isinstance(events, tuple) else (events or [])
    mode = regime.get("regime", "中立")
    attack = mode in ("強気", "やや強気")
    try:
        from modules import strategy as strat_mod
        strat_line = strat_mod.label_line()
    except Exception:
        strat_line = "採用ルール: なし"
    market = {
        "mode": mode, "stance": ("攻める日（押し目は買い検討）" if attack else "守り気味（全力買いは控える）"),
        "cash_pct": regime.get("cash_pct"), "new_buy_pct": regime.get("new_buy_pct"),
        "strategy": strat_line,
        "events": [f"{e['title']}（あと{e['days']}日）" for e in week_events if 0 <= e.get("days", 99) <= 7],
    }

    # B. 発掘TOP3（キャッシュ利用＝コスト対策）
    cache = disc_mod.load_cache()
    top3 = []
    for it in (cache.get("top20", [])[:3] if cache else []):
        top3.append({
            "ticker": it["ticker"], "name": it.get("name", it["ticker"]), "score": it.get("score"),
            "verdict": it.get("verdict"), "reasons": it.get("reasons", [])[:2], "action": it.get("action"),
            "entry": it.get("entry"), "stop": it.get("stop"), "tp1": it.get("tp1"),
            "risk": (it.get("risk_points") or ["—"])[:1],
        })

    # C. 保有アクション
    holdings = []
    pos_by_t = {p["ticker"].upper(): p for p in positions}
    rmap = {}
    targets = list(pos_by_t.keys()) + [t["ticker"] for t in top3]
    for t in list(dict.fromkeys(targets))[:config.NOTIFY_MAX_HOLDINGS]:
        try:
            rmap[t] = analyze(t)
        except Exception:
            rmap[t] = {"ticker": t, "ok": False}
    try:
        from modules import portfolio as pf, holding_action as ha
        for t, pos in pos_by_t.items():
            ev = pf.evaluate_position(pf._normalize(pos), rmap.get(t))
            hd = ha.decide(ev, mode)
            holdings.append({"ticker": t, "action": hd["action"], "reason": hd["reason"],
                             "todo": ha.moomoo_todo(ev, hd), "stop": ev.get("init_stop"),
                             "tp1": ev.get("init_tp1"), "pl_pct": ev.get("pl_pct")})
    except Exception:
        pass

    # D. ニュース精密解析まとめ（TOP3＋保有のみ＝コスト対策）
    news = {"good": [], "bad": [], "unpriced": [], "caution": []}
    if top3_news:
        na_tickers = list(dict.fromkeys([h["ticker"] for h in holdings] + [t["ticker"] for t in top3]))
        for t in na_tickers[:config.NOTIFY_MAX_HOLDINGS]:
            a = rmap.get(t)
            if not (a and a.get("ok")):
                continue
            r = na_mod.analyze_one(t, a, pos_by_t.get(t), regime, window_days=7)
            if not r.get("ok"):
                continue
            if r["net_impact"] >= 1.5:
                news["good"].append(f"{t}: 好材料優勢（{r['net_impact']:+.1f}）")
            if r["net_impact"] <= -1.5:
                news["bad"].append(f"{t}: 悪材料優勢（{r['net_impact']:+.1f}）")
            if r["priced_in"]["score"] < 0.4 and r["net_impact"] >= 1:
                news["unpriced"].append(f"{t}: 未織り込みの余地（上昇余地）")
            for it in r["news"][:3]:
                if it["impact"] <= -3 or it["fine_cat"] in ("規制", "訴訟", "地政学"):
                    news["caution"].append(f"{t}: [{it['fine_cat']}] {it['headline'][:30]}")

    # E. Moomoo注文メモ
    memos = {"set": [], "watch": [], "nothing": []}
    for h in holdings:
        if h["action"] in ("損切り", "危険"):
            memos["set"].append(f"{h['ticker']}: 逆指値 ${h['stop']} を確認/設定（{h['action']}）")
        elif h["action"] in ("一部利確", "全利確"):
            memos["set"].append(f"{h['ticker']}: 利確 ${h['tp1']} で指値（{h['action']}）")
        elif h["action"] in ("買い増し禁止", "ニュース確認"):
            memos["watch"].append(f"{h['ticker']}: {h['action']}（逆指値 ${h['stop']} 維持）")
        else:
            memos["nothing"].append(f"{h['ticker']}: 継続保有（${h['stop']} 維持）")
    for t in top3:
        if t["verdict"] in ("強いBUY", "BUY"):
            memos["set"].append(f"{t['ticker']}: 第1 ${t['entry']} 指値・損切り ${t['stop']}（新規）")

    digest = {"date": today, "market": market, "top3": top3, "holdings": holdings,
              "news": news, "memos": memos}
    digest["text"] = _render_text(digest)
    digest["markdown"] = _render_markdown(digest)
    return digest


def _render_text(d):
    L = []
    m = d["market"]
    L.append("【今日の投資コックピット】" + f"  {d['date']}")
    L.append("")
    L.append(f"市場：{m['mode']}（{m['stance']}）")
    if m.get("strategy"):
        L.append(m["strategy"])
    if m.get("cash_pct") is not None:
        L.append(f"現金比率の目安：{m['cash_pct']}%")
    if m["events"]:
        L.append("注意イベント：" + " / ".join(m["events"]))
    L.append("")
    L.append("■ 発掘TOP3")
    if d["top3"]:
        for i, t in enumerate(d["top3"], 1):
            L.append(f"{i}. {t['ticker']}（{t['name']}）  判定:{t['verdict']} スコア{t['score']}")
            if t["reasons"]:
                L.append(f"   理由：{' / '.join(t['reasons'])}")
            L.append(f"   買い:${t['entry']} 損切り:${t['stop']} 利確1:${t['tp1']}  今日:{t['action']}")
    else:
        L.append("（発掘キャッシュがありません。発掘ページでスキャンしてください）")
    L.append("")
    L.append("■ 保有アクション")
    if d["holdings"]:
        for h in d["holdings"]:
            pl = f"（{h['pl_pct']:+.1f}%）" if h.get("pl_pct") is not None else ""
            L.append(f"・{h['ticker']}{pl}：{h['action']} — {h['todo']}")
    else:
        L.append("（保有銘柄なし。Moomoo同期で登録できます）")
    L.append("")
    if any(d["news"].values()):
        L.append("■ 重要ニュース")
        for k, lab in [("good", "好材料"), ("bad", "悪材料"), ("unpriced", "未織り込み"), ("caution", "注意")]:
            for x in d["news"][k][:3]:
                L.append(f"・[{lab}] {x}")
        L.append("")
    L.append("■ Moomoo注文メモ")
    for x in d["memos"]["set"][:8]:
        L.append("・設定: " + x)
    for x in d["memos"]["watch"][:6]:
        L.append("・監視: " + x)
    if d["memos"]["nothing"]:
        L.append("・何もしない: " + ", ".join(h.split(":")[0] for h in d["memos"]["nothing"]))
    L.append("")
    L.append("※これは投資助言ではなく分析補助です。発注はMoomooでご自身が手動で行ってください。")
    return "\n".join(L)


def _render_markdown(d):
    # Discord向け（テキストベースで十分見やすい）
    return "```\n" + _render_text(d) + "\n```"


# ---------------- 送信 ----------------
def send(settings, content_text, content_markdown=None):
    """設定された通知先へ送信。返り値 (ok, error)。"""
    target = settings.get("target", "未設定")
    if target == "未設定" or not target_ready(settings):
        return False, "通知先が未設定です"
    try:
        if target == "Discord Webhook":
            return _send_discord(_webhook(settings), content_markdown or content_text)
        if target == "Telegram Bot":
            token = settings.get("telegram_token") or config.get_secret("TELEGRAM_TOKEN")
            chat = settings.get("telegram_chat") or config.get_secret("TELEGRAM_CHAT")
            return _send_telegram(token, chat, content_text)
        if target == "メール(SMTP)":
            return _send_email(settings, content_text)
    except Exception as e:
        return False, f"送信失敗: {type(e).__name__}: {e}"
    return False, "未対応の通知先"


def _send_discord(webhook, content):
    import requests
    # Discordは2000文字制限。分割送信。
    chunks = _chunk(content, 1900)
    for ch in chunks:
        r = requests.post(webhook, json={"content": ch}, timeout=10)
        if r.status_code not in (200, 204):
            return False, f"Discord HTTP {r.status_code}: {r.text[:120]}"
    return True, None


def _send_telegram(token, chat, content):
    import requests
    for ch in _chunk(content, 3900):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": ch}, timeout=10)
        if r.status_code != 200:
            return False, f"Telegram HTTP {r.status_code}: {r.text[:120]}"
    return True, None


def _send_email(settings, content):
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = "今日の投資コックピット"
    msg["From"] = settings.get("smtp_user") or settings.get("email_to")
    msg["To"] = settings.get("email_to")
    with smtplib.SMTP(settings["smtp_host"], int(settings.get("smtp_port", 587)), timeout=15) as s:
        s.starttls()
        if settings.get("smtp_user"):
            s.login(settings["smtp_user"], settings.get("smtp_pass", ""))
        s.sendmail(msg["From"], [settings["email_to"]], msg.as_string())
    return True, None


def _chunk(text, n):
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def send_test(settings):
    return send(settings, "✅ テスト通知：投資コックピットの通知設定は正常です（これは分析補助です）。")


# ---------------- 履歴 ----------------
HIST_FIELDS = ["datetime", "target", "market_mode", "top3", "holdings_actions", "success", "error"]


def log_history(target, digest, ok, error):
    rows = storage.load_csv(config.NOTIFY_HISTORY_PATH, HIST_FIELDS)
    rows.append({
        "datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "target": target,
        "market_mode": digest.get("market", {}).get("mode", "") if digest else "",
        "top3": "/".join(t["ticker"] for t in digest.get("top3", [])) if digest else "",
        "holdings_actions": len(digest.get("holdings", [])) if digest else 0,
        "success": "成功" if ok else "失敗", "error": (error or "")[:120],
    })
    storage.save_csv(config.NOTIFY_HISTORY_PATH, HIST_FIELDS, rows[-100:])


def load_history():
    return storage.load_csv(config.NOTIFY_HISTORY_PATH, HIST_FIELDS)
