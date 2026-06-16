"""
news_analysis.py (v11) — ニュース精密解析エンジン。
ニュースを時間軸別・カテゴリ別に解析し、株価への影響（短期/中期/長期）、織り込み判定、
強気/中立/弱気シナリオ（株価レンジ＋確率の目安）、マクロ連動、買う/待つ/売るの判断を出す。
※すべて「シナリオ予測」。未来を断定しない。投資助言ではなく分析補助。
APIキーが無くても動作（ルールベース）。あればAIで自然文の結論を生成。
"""
import numpy as np
import config
from modules import themes as themes_mod

# ニュース細分類（キーワードベース・日本語/英語）
FINE_CATEGORIES = [
    ("決算", ["決算", "四半期", "EPS", "売上高", "earnings", "quarter", "results", "増収", "減益", "増益"]),
    ("ガイダンス", ["ガイダンス", "見通し", "通期", "上方修正", "下方修正", "guidance", "outlook", "forecast"]),
    ("アナリスト評価", ["格上げ", "格下げ", "目標株価", "レーティング", "投資判断", "アナリスト", "upgrade", "downgrade", "price target", "rating"]),
    ("製品/サービス", ["新製品", "発表", "提携", "受注", "契約", "ローンチ", "product", "launch", "partnership", "deal", "order"]),
    ("規制", ["規制", "当局", "認可", "承認", "輸出規制", "制裁", "regulat", "ban", "approval", "FDA", "antitrust"]),
    ("訴訟", ["訴訟", "提訴", "和解", "賠償", "lawsuit", "litigation", "settle", "court"]),
    ("金利", ["金利", "利上げ", "利下げ", "FRB", "FOMC", "利回り", "rate", "fed", "yield"]),
    ("原油", ["原油", "OPEC", "石油", "WTI", "oil", "crude"]),
    ("地政学", ["戦争", "紛争", "地政学", "中東", "イラン", "制裁", "war", "geopolit", "conflict", "sanction"]),
    ("競合", ["競合", "シェア", "ライバル", "競争", "competitor", "rival", "market share"]),
    ("業界全体", ["業界", "セクター", "市場全体", "需要", "供給", "industry", "sector", "demand"]),
]


def _days_ago(item, ticker, idx):
    """ニュースの経過日数。実データに日付があれば使用、無ければ決定的に割当。"""
    d = item.get("days_ago")
    if isinstance(d, (int, float)):
        return int(d)
    h = (abs(hash((ticker, item.get("headline", ""), idx))) % 30)
    return h


def fine_category(headline):
    h = (headline or "")
    for cat, kws in FINE_CATEGORIES:
        if any(k.lower() in h.lower() for k in kws):
            return cat
    return "個別材料"


def _confidence(news_res, item):
    src = news_res.get("source"); clf = news_res.get("classifier")
    if src == "finnhub" and clf == "ai":
        return "高"
    if src == "finnhub":
        return "中"
    if clf == "ai":
        return "中"
    return "低"


def priced_in_signal(df, snap):
    """直近の株価/出来高/ギャップから織り込み度を判定。"""
    try:
        c = df["Close"]; o = df["Open"]
        cur = float(c.iloc[-1]); prev = float(c.iloc[-2])
        wk = float(c.iloc[-6]) if len(c) >= 6 else float(c.iloc[0])
        move = cur / wk - 1
        gap = float(o.iloc[-1]) / prev - 1
        vr = snap.get("vol_ratio", 1.0)
        # その後の戻り
        pull = (c.iloc[-1] < c.iloc[-2] < c.iloc[-3]) if len(c) >= 3 else False
    except Exception:
        return {"label": "判定不可", "move": 0, "gap": 0, "vol_ratio": 1, "pullback": False, "score": 0.5}

    if abs(move) >= 0.05 and vr >= 1.3:
        label = "ほぼ織り込み済み（大きく動いた後）"
        score = 0.85
    elif abs(move) >= 0.03:
        label = "一部織り込み"
        score = 0.55
    else:
        label = "未織り込みの余地あり"
        score = 0.25
    return {"label": label, "move": round(move * 100, 1), "gap": round(gap * 100, 1),
            "vol_ratio": round(vr, 1), "pullback": bool(pull), "score": score}


def _horizon_impacts(item):
    """カテゴリと影響度から短期/中期/長期の効き方を推定。"""
    cat = item.get("fine_cat", "個別材料")
    imp = item.get("impact", 0)
    persist = {"決算": (1.0, 0.8, 0.5), "ガイダンス": (0.8, 1.0, 0.8), "アナリスト評価": (1.0, 0.5, 0.2),
               "製品/サービス": (0.7, 0.9, 0.7), "規制": (0.9, 0.9, 0.8), "訴訟": (0.8, 0.7, 0.6),
               "金利": (0.9, 0.9, 0.7), "原油": (0.9, 0.8, 0.6), "地政学": (1.0, 0.7, 0.4),
               "競合": (0.6, 0.8, 0.7), "業界全体": (0.6, 0.9, 0.9), "個別材料": (1.0, 0.5, 0.3)}
    s, m, l = persist.get(cat, (1.0, 0.5, 0.3))

    def lab(v):
        x = imp * v
        if x >= 2:
            return "↑↑ 強い追い風"
        if x >= 0.7:
            return "↑ 追い風"
        if x <= -2:
            return "↓↓ 強い逆風"
        if x <= -0.7:
            return "↓ 逆風"
        return "→ 中立"
    return {"今日〜3日": lab(s), "1〜2週間": lab(m), "1〜3ヶ月": lab(l)}


def analyze_news_items(news_res, ticker, df, snap):
    items = news_res.get("items", [])
    pe = priced_in_signal(df, snap)
    out = []
    for i, it in enumerate(items):
        d = _days_ago(it, ticker, i)
        cat = fine_category(it.get("headline", ""))
        imp = int(it.get("impact", 0))
        enriched = {
            "headline": it.get("headline", ""), "days_ago": d, "fine_cat": cat, "impact": imp,
            "confidence": _confidence(news_res, it),
            "priced_in": pe["label"],
            "downside": ("大きい" if imp <= -3 else "中" if imp < 0 else "小"),
            "upside": ("大きい" if imp >= 3 else "中" if imp > 0 else "小"),
        }
        enriched["horizons"] = _horizon_impacts(enriched)
        out.append(enriched)
    return out, pe


def _window_items(items, window_days):
    return [it for it in items if it["days_ago"] <= window_days]


def scenarios(price, df, snap, net_impact, window_days):
    """強気/中立/弱気シナリオ（株価レンジ＋確率の目安）。MID(約10営業日)を基準。"""
    try:
        vol = float(df["Close"].pct_change().tail(20).std()) or 0.02
    except Exception:
        vol = 0.02
    horizon = 10
    sigma = vol * (horizon ** 0.5)
    up = max(0.05, sigma * 2.0); down = max(0.04, sigma * 1.8)

    # 確率の傾き（ニュース純影響 + テクニカル）
    tech_tilt = 0
    if snap.get("above_sma200"):
        tech_tilt += 6
    if snap.get("macd_hist", 0) > 0:
        tech_tilt += 4
    rsi = snap.get("rsi", 50)
    if rsi >= 70:
        tech_tilt -= 6
    elif rsi <= 35:
        tech_tilt += 4
    bull_p = 33 + net_impact * 4 + tech_tilt
    bear_p = 33 - net_impact * 4 - tech_tilt
    bull_p = int(max(10, min(70, bull_p))); bear_p = int(max(10, min(70, bear_p)))
    neutral_p = max(5, 100 - bull_p - bear_p)
    tot = bull_p + bear_p + neutral_p
    bull_p = round(bull_p / tot * 100); bear_p = round(bear_p / tot * 100)
    neutral_p = 100 - bull_p - bear_p

    return {
        "bull": {"label": "強気", "range": [round(price * (1 + up * 0.5), 2), round(price * (1 + up), 2)],
                 "prob": bull_p, "desc": "好材料が効き、テクニカルも上向きを維持した場合"},
        "neutral": {"label": "中立", "range": [round(price * (1 - down * 0.3), 2), round(price * (1 + up * 0.3), 2)],
                    "prob": neutral_p, "desc": "材料が織り込まれ、レンジ内で推移する場合"},
        "bear": {"label": "弱気", "range": [round(price * (1 - down), 2), round(price * (1 - down * 0.5), 2)],
                 "prob": bear_p, "desc": "悪材料やマクロ逆風、過熱調整が出た場合"},
        "expected_move_pct": {"今日〜3日": round(vol * (3 ** 0.5) * 100, 1),
                              "1〜2週間": round(vol * (10 ** 0.5) * 100, 1),
                              "1〜3ヶ月": round(vol * (60 ** 0.5) * 100, 1)},
    }


def macro_linkage(themes, macro_snapshot=None):
    snap = macro_snapshot or config.MACRO_FACTORS
    factors = []
    for th in themes:
        for key, facs in config.THEME_MACRO.items():
            if key == th or key in th:
                factors += facs
    if not factors:
        factors = ["CPI", "FOMC", "VIX"]
    seen, out = set(), []
    for f in factors:
        if f in seen or f not in snap:
            continue
        seen.add(f)
        info = snap[f]
        out.append({"factor": f, "value": info.get("value"), "bias": info.get("bias"), "note": info.get("note")})
    return out[:5]


def recommend(held, analysis, regime, net_impact):
    """保有→管理判断 / 新規→購入判断。"""
    if held is not None:
        try:
            from modules import portfolio as pf, holding_action as ha
            ev = pf.evaluate_position(pf._normalize(held), analysis)
            hd = ha.decide(ev, regime.get("regime", "中立"))
            todo = ha.moomoo_todo(ev, hd)
            return {"kind": "保有", "action": hd["action"], "reason": hd["reason"], "todo": todo, "ev": ev}
        except Exception:
            pass
    # 新規候補
    act = (analysis.get("action") or {}) if analysis else {}
    a = act.get("action", "—")
    mapping = {"今すぐ買い": "今買ってよい", "押し目待ち": "押し目待ち", "ニュース待ち": "ニュース確認",
               "決算後まで待ち": "決算後まで待ち", "買わない": "買わない", "危険": "買わない"}
    label = mapping.get(a, a)
    plan = analysis.get("plan", {}) if analysis else {}
    entry = plan.get("entry1"); stop = plan.get("stop")
    if label == "今買ってよい":
        todo = f"第1エントリー ${entry} で打診買い（資金の1/3）。損切り ${stop} を必ず設定。"
    elif label == "押し目待ち":
        todo = f"${entry} 以下まで待ってから打診買い。"
    elif label == "ニュース確認":
        todo = "悪材料/好材料の内容を確認してから判断。"
    elif label == "決算後まで待ち":
        todo = "決算を通過してから改めて判断。"
    else:
        todo = "今は新規を見送り、ウォッチのみ。"
    return {"kind": "新規", "action": label, "reason": act.get("reason", ""), "todo": todo, "ev": None}


def analyze_one(ticker, analysis, held=None, regime=None, window_days=7, macro_snapshot=None):
    if not (analysis and analysis.get("ok")):
        return {"ticker": ticker, "ok": False}
    regime = regime or {"regime": "中立"}
    df = analysis["df"]; snap = analysis["snap"]; fund = analysis["fund"]
    price = fund.get("price", snap.get("price", 0))
    themes = themes_mod.classify(ticker, fund, analysis["news"]["items"])

    items_all, pe = analyze_news_items(analysis["news"], ticker, df, snap)
    win_items = _window_items(items_all, window_days)
    net = round(float(np.mean([it["impact"] for it in win_items])), 1) if win_items else 0.0

    scn = scenarios(price, df, snap, net, window_days)
    rec = recommend(held, analysis, regime, net)
    macros = macro_linkage(themes, macro_snapshot)

    # カテゴリ集計
    cats = {}
    for it in win_items:
        cats[it["fine_cat"]] = cats.get(it["fine_cat"], 0) + 1

    # 今日の結論（ルールベース）
    tone = "好材料優勢" if net >= 1 else "悪材料優勢" if net <= -1 else "中立"
    tech = ("上昇トレンド" if snap.get("above_sma200") else "下降基調") + f"・RSI{snap.get('rsi',50):.0f}"
    conclusion = (f"{ticker}は直近{window_days}日のニュース純影響 {net:+.1f}（{tone}・{pe['label']}）。"
                  f"テクニカルは{tech}。シナリオは強気{scn['bull']['prob']}% / 中立{scn['neutral']['prob']}% / "
                  f"弱気{scn['bear']['prob']}%。基本方針は『{rec['action']}』。")

    return {
        "ticker": ticker, "ok": True, "held": held is not None, "price": round(price, 2),
        "themes": themes[:3], "net_impact": net, "tone": tone, "priced_in": pe,
        "categories": cats, "news": win_items, "news_all_count": len(items_all),
        "scenarios": scn, "recommendation": rec, "macro": macros,
        "conclusion": conclusion, "tech": tech,
    }


def analyze_scope(tickers, rmap, positions_by_t, regime, window_days, macro_snapshot=None, cap=12):
    out = []
    for t in tickers[:cap]:
        t = t.upper()
        res = analyze_one(t, rmap.get(t), positions_by_t.get(t), regime, window_days, macro_snapshot)
        out.append(res)
    return out
