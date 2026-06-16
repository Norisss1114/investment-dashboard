"""
scoring.py (v4) — 各サブスコアを 0〜1 に正規化し、config.SCORE_WEIGHTS の配点を掛ける方式。
- ニュースの比重を引き上げ（配点25・avg_impact主導でAIシグナルを強く反映）。
- 部分点を寛容化し、AVOID過多を解消（しきい値も config で再調整済み）。
返り値は「重み付け後の点数＋理由」。
"""
import config


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# ---------------- 0〜1 正規化サブスコア ----------------
def _fundamental_raw(f: dict):
    eps_g = _g(f.get("eps_g")); rev_g = _g(f.get("rev_g")); margin = _g(f.get("margin"))
    pe = f.get("pe"); price = f.get("price") or 0
    target = f.get("target") or price
    upside = (target / price - 1) if price else 0
    reasons = []

    c_eps = _clamp(eps_g / 0.30) * 0.30
    c_rev = _clamp(rev_g / 0.30) * 0.20
    c_mar = _clamp(margin / 0.30) * 0.15
    c_up = _clamp(upside / 0.30) * 0.25
    if pe is None:
        c_pe = 0.05
    elif pe <= 25:
        c_pe = 0.10
    elif pe <= 45:
        c_pe = 0.07
    elif pe <= 80:
        c_pe = 0.04
    else:
        c_pe = 0.02
    raw = c_eps + c_rev + c_mar + c_up + c_pe

    reasons.append(f"EPS成長 {eps_g*100:.0f}%" + ("（高成長）" if eps_g > 0.25 else ""))
    reasons.append(f"売上成長 {rev_g*100:.0f}% / 利益率 {margin*100:.0f}%")
    reasons.append(f"アナリスト上値余地 {upside*100:+.0f}%")
    reasons.append(f"PER " + ("不明" if pe is None else f"{pe:.0f}"
                  + ("（割安め）" if pe <= 25 else "（割高・期待過大）" if pe > 80 else "")))
    return _clamp(raw), reasons


def _technical_raw(snap: dict):
    if not snap:
        return 0.55, ["データ不足のため中立寄り"]
    reasons = []
    trend = 0.0
    if snap.get("above_sma200"):
        trend += 0.25; reasons.append("200日線より上（長期トレンド強い）")
    else:
        reasons.append("200日線より下（長期は弱め）")
    if snap.get("above_sma50"):
        trend += 0.12; reasons.append("50日線より上")
    if snap.get("above_sma20"):
        trend += 0.08

    rsi = snap.get("rsi", 50)
    if 50 <= rsi <= 65:
        r = 0.25; reasons.append(f"RSI {rsi:.0f}（理想・過熱なし）")
    elif 45 <= rsi < 50 or 65 < rsi <= 70:
        r = 0.20; reasons.append(f"RSI {rsi:.0f}（概ね良好）")
    elif 40 <= rsi < 45 or 70 < rsi <= 72:
        r = 0.14; reasons.append(f"RSI {rsi:.0f}")
    elif 35 <= rsi < 40:
        r = 0.10; reasons.append(f"RSI {rsi:.0f}（やや弱い）")
    elif rsi > 72:
        r = 0.06; reasons.append(f"RSI {rsi:.0f}（買われすぎ）")
    else:
        r = 0.09; reasons.append(f"RSI {rsi:.0f}（売られすぎ・反発期待）")

    hist = snap.get("macd_hist", 0)
    if hist > 0 and snap.get("macd", 0) > 0:
        m = 0.30; reasons.append("MACDが上向き＆プラス圏（強気）")
    elif hist > 0:
        m = 0.22; reasons.append("MACDヒストがプラス転換中")
    elif hist > -0.5:
        m = 0.12; reasons.append("MACDは中立")
    else:
        m = 0.04; reasons.append("MACDが下向き（弱気）")
    return _clamp(trend + r + m), reasons


def _supply_raw(snap: dict):
    if not snap:
        return 0.5, ["データ不足のため中立"]
    reasons = []
    vr = snap.get("vol_ratio", 1.0)
    if vr >= 1.5:
        v = 0.55; reasons.append(f"出来高 平均の{vr:.1f}倍（大口参加の可能性）")
    elif vr >= 1.2:
        v = 0.42; reasons.append(f"出来高 増加中（{vr:.1f}倍）")
    elif vr >= 0.9:
        v = 0.32; reasons.append(f"出来高 平常（{vr:.1f}倍）")
    else:
        v = 0.18; reasons.append(f"出来高 細い（{vr:.1f}倍）")
    if snap.get("above_sma20") and snap.get("above_sma50"):
        mo = 0.40; reasons.append("短中期ともに上（モメンタム良好）")
    elif snap.get("above_sma50"):
        mo = 0.25; reasons.append("中期は上")
    else:
        mo = 0.10
    return _clamp(v + mo), reasons


def _news_raw(news_sum: dict):
    """ニュースは avg_impact(-5..+5) 主導。危険/チャンスで微調整。"""
    avg = _g(news_sum.get("avg_impact"))
    cats = news_sum.get("categories", {}) or {}
    raw = _clamp(0.5 + avg / 10.0)
    if cats.get("危険"):
        raw *= 0.85
    if cats.get("チャンス"):
        raw = _clamp(raw * 1.08)
    reasons = list(news_sum.get("reasons", [])) or [f"平均インパクト {avg:+.1f}"]
    return _clamp(raw), reasons


# ---------------- 合算 ----------------
def compute_total(fund, snap, news_sum, market_score, market_reasons=None) -> dict:
    W = config.SCORE_WEIGHTS
    f_raw, f_re = _fundamental_raw(fund)
    t_raw, t_re = _technical_raw(snap)
    s_raw, s_re = _supply_raw(snap)
    n_raw, n_re = _news_raw(news_sum or {})
    m_raw = _clamp(_g(market_score) / 10.0)

    fs = round(f_raw * W["fundamental"], 1)
    ts = round(t_raw * W["technical"], 1)
    ns = round(n_raw * W["news"], 1)
    ms = round(m_raw * W["market"], 1)
    ss = round(s_raw * W["supply_demand"], 1)
    total = round(fs + ts + ns + ms + ss, 1)

    verdict = ("BUY" if total >= config.BUY_THRESHOLD
               else "WATCH" if total >= config.WATCH_THRESHOLD else "AVOID")

    return {
        "fundamental": fs, "technical": ts, "news": ns, "market": ms, "supply_demand": ss,
        "total": total, "verdict": verdict,
        "raw": {"fundamental": round(f_raw, 2), "technical": round(t_raw, 2),
                "news": round(n_raw, 2), "market": round(m_raw, 2), "supply_demand": round(s_raw, 2)},
        "reasons": {
            "fundamental": f_re, "technical": t_re, "supply_demand": s_re,
            "news": n_re, "market": market_reasons or [f"相場環境 {ms}/{W['market']}"],
        },
    }


def _g(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default
