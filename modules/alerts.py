"""
alerts.py — アプリ内アラート生成（価格 / イベント / ニュース）。
level: info / warn / danger。
"""
import config
from modules.utils import days_until


def _recent_high(df, window=60):
    try:
        return float(df["High"].tail(window).max())
    except Exception:
        return None


def price_alerts(ticker, analysis, position=None) -> list:
    out = []
    if not (analysis and analysis.get("ok")):
        return out
    snap = analysis["snap"]; plan = analysis["plan"]; df = analysis["df"]
    cur = snap.get("price", 0)
    sma50 = snap.get("sma50"); sma200 = snap.get("sma200")

    # ポジションがあれば初期損切り/利確、無ければプランの値を使う
    stop = (position or {}).get("init_stop") or plan.get("stop")
    tp1 = (position or {}).get("init_tp1") or plan.get("tp1")
    tp2 = (position or {}).get("init_tp2") or plan.get("tp2")
    entry1 = plan.get("entry1")

    if entry1 and abs(cur - entry1) / entry1 < 0.01:
        out.append(("price", "info", f"{ticker}: 第1エントリー ${entry1} 付近"))
    if stop and cur <= stop * 1.03:
        lv = "danger" if cur <= stop else "warn"
        out.append(("price", lv, f"{ticker}: 損切り ${stop} に接近/到達（現在 ${cur:.2f}）"))
    if tp1 and cur >= tp1:
        out.append(("price", "info", f"{ticker}: 利確1 ${tp1} 到達 → 半分売り検討"))
    if tp2 and cur >= tp2:
        out.append(("price", "info", f"{ticker}: 利確2 ${tp2} 到達 → 残り利確検討"))
    rh = _recent_high(df)
    if rh and cur >= rh * 0.999:
        out.append(("price", "info", f"{ticker}: 直近高値 ${rh:.2f} を更新"))
    if sma50 and cur < sma50:
        out.append(("price", "warn", f"{ticker}: 50日線 ${sma50:.2f} を割れ（中期トレンド悪化）"))
    if sma200 and cur < sma200:
        out.append(("price", "danger", f"{ticker}: 200日線 ${sma200:.2f} を割れ（長期トレンド悪化）"))
    rsi = snap.get("rsi", 50)
    if rsi >= 72:
        out.append(("price", "warn", f"{ticker}: RSI {rsi:.0f} で過熱（短期は買い増し非推奨）"))
    return out


def event_alerts(ticker, analysis) -> list:
    out = []
    if analysis and analysis.get("ok"):
        e = analysis["plan"].get("earnings_days")
        if e is not None:
            if 0 <= e <= 7:
                out.append(("event", "warn", f"{ticker}: 決算まで約{e}日（7日前）→ サイズ縮小"))
            elif 8 <= e <= 14:
                out.append(("event", "info", f"{ticker}: 決算まで約{e}日（14日前）"))
    return out


def macro_alerts() -> list:
    """全体向けのイベント/マクロアラート（config.EVENTS から）。"""
    out = []
    for ev in config.EVENTS:
        d = days_until(ev["date"])
        if d is None or d < 0:
            continue
        if d <= config.FOMC_SOON_DAYS and "FOMC" in ev["title"]:
            out.append(("event", "warn", f"FOMCまで約{d}日 → 全体のサイズを落とす"))
        elif d <= 3 and ("CPI" in ev["title"] or "雇用" in ev["title"]):
            out.append(("event", "warn", f"{ev['title']}まで約{d}日"))
    return out


def news_alerts(ticker, analysis) -> list:
    out = []
    if not (analysis and analysis.get("ok")):
        return out
    ns = analysis["news_sum"]; cats = ns.get("categories", {})
    if cats.get("危険", 0) > 0:
        out.append(("news", "danger", f"{ticker}: 強い悪材料/危険ニュースあり"))
    if cats.get("チャンス", 0) > 0:
        out.append(("news", "info", f"{ticker}: 好材料（チャンス）ニュースあり"))
    if ns.get("avg_impact", 0) >= 2:
        out.append(("news", "info", f"{ticker}: ニュースが総じて強気（{ns['avg_impact']:+.1f}）"))
    elif ns.get("avg_impact", 0) <= -2:
        out.append(("news", "warn", f"{ticker}: ニュースが総じて弱気（{ns['avg_impact']:+.1f}）"))
    return out


def sector_alerts(regime) -> list:
    """地政学・金利・AI/半導体セクターのマクロ的注意。"""
    out = []
    if regime.get("vix", 20) >= 25:
        out.append(("news", "warn", f"地政学/変動リスク上昇（VIX {regime.get('vix')}）"))
    if "半導体" in " ".join(regime.get("bad_sectors", [])):
        out.append(("news", "warn", "半導体セクターが現在の相場で不利"))
    return out


def collect_all(results_by_ticker, positions, regime) -> list:
    """全アラートをまとめて返す。"""
    alerts = []
    pos_by_t = {p["ticker"]: p for p in positions}
    tickers = set(results_by_ticker.keys()) | set(pos_by_t.keys())
    for t in tickers:
        a = results_by_ticker.get(t)
        pos = pos_by_t.get(t)
        alerts += [(*x, t) for x in price_alerts(t, a, pos)]
        alerts += [(*x, t) for x in event_alerts(t, a)]
        alerts += [(*x, t) for x in news_alerts(t, a)]
    # 保有銘柄: 保有アクション由来のアラート（買い増し禁止/危険/損切り/決算前に減らす）
    alerts += [(*x, x_t) for x_t, x in _holding_alerts(pos_by_t, results_by_ticker, regime)]
    alerts += [(*x, "MARKET") for x in macro_alerts()]
    alerts += [(*x, "MARKET") for x in sector_alerts(regime)]
    rank = {"danger": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda x: rank.get(x[1], 3))
    # 重複除去（type,level,msg）
    seen = set(); out = []
    for a in alerts:
        key = (a[0], a[1], a[2])
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": a[0], "level": a[1], "msg": a[2], "ticker": a[3]})
    return out


def _holding_alerts(pos_by_t, results_by_ticker, regime):
    """保有アクション判定から派生するアラート。"""
    try:
        from modules import portfolio as pf_mod
        from modules import holding_action as ha_mod
    except Exception:
        return []
    out = []
    for t, pos in pos_by_t.items():
        a = results_by_ticker.get(t)
        # RSI過熱（買い増し禁止サイン）
        try:
            rsi = a["snap"].get("rsi", 50) if (a and a.get("ok")) else 50
            if rsi >= 72:
                out.append((t, ("price", "warn", f"{t}: RSI {rsi:.0f} で過熱（買い増し禁止・短期下落注意）")))
        except Exception:
            pass
        try:
            ev = pf_mod.evaluate_position(pf_mod._normalize(pos), a)
            hd = ha_mod.decide(ev, regime.get("regime", "中立"))
        except Exception:
            continue
        act = hd["action"]
        if act in ("損切り", "危険"):
            out.append((t, ("news", "danger", f"{t}: 保有判定『{act}』— {hd['reason']}")))
        elif act in ("買い増し禁止", "決算前に減らす", "一部利確", "全利確", "ニュース確認"):
            out.append((t, ("news", "warn", f"{t}: 保有判定『{act}』— {hd['reason']}")))
    return out
