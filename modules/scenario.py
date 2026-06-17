"""
scenario.py (v17) — 個別銘柄分析の「未来シナリオ予測」。
現在のテクニカル・ニュース影響から、強気/中立/弱気の3シナリオ（確率・想定レンジ・予測パス）を作る。

⚠️ 将来価格の断定ではありません。教育・分析用のシナリオ分岐であり、保証ではありません。
※確率の傾き計算は news_analysis.scenarios の方針を移植し、予測期間(days)に対応させたもの。
   news_analysis.py 本体は変更していません。価格取得・API等にも依存しません。
"""
import math


def _path(price, end, days):
    """現在値→endpoint までを days 営業日で線形補間（day1..dayN の値リスト）。"""
    days = max(1, int(days))
    return [round(price + (end - price) * i / days, 2) for i in range(1, days + 1)]


def _band(price, end, half, days):
    """中央値ラインを軸に、末端ほど広がるコーン状の上下限（day1..dayN）。"""
    days = max(1, int(days))
    upper, lower = [], []
    for i in range(1, days + 1):
        v = price + (end - price) * i / days
        w = half * math.sqrt(i / days)  # day0で0 → dayNで half
        upper.append(round(v + w, 2))
        lower.append(round(v - w, 2))
    return upper, lower


def _factor_lines(snap, ni):
    """シナリオ根拠：実際に使ったテクニカル/ニュース指標の箇条書き（C）。"""
    lines = []
    lines.append("SMA200 " + ("上 ✅" if snap.get("above_sma200") else "下 ⚠️"))
    a20, a50 = snap.get("above_sma20"), snap.get("above_sma50")
    if a20 and a50:
        lines.append("20日線・50日線の上（上昇基調）")
    elif (not a20) and (not a50):
        lines.append("20日線・50日線の下（下降基調）")
    else:
        lines.append("20日線/50日線が交錯（方向感薄い）")
    mh = snap.get("macd_hist", 0) or 0
    lines.append("MACD " + ("上向き ✅" if mh > 0 else "下向き ⚠️" if mh < 0 else "中立"))
    rsi = snap.get("rsi", 50) or 50
    lines.append(f"RSI {rsi:.0f}（" + ("過熱" if rsi >= 70 else "売られ過ぎ" if rsi <= 30 else "正常圏") + "）")
    vr = snap.get("vol_ratio", 1) or 1
    lines.append("出来高 " + ("増加" if vr >= 1.3 else "低下" if vr <= 0.7 else "安定"))
    lines.append(f"ニュース好感度 {ni:+.2f}" + ("（好材料）" if ni > 0.5 else "（悪材料）" if ni < -0.5 else "（中立）"))
    return lines


def quality_score(df, snap, news_res):
    """シナリオ品質スコア(0-100)＋理由（E）。AI解析が無くてもテクニカルで参考になる配点。"""
    snap = snap or {}
    news_res = news_res or {}
    score = 0
    reasons = []
    n = len(df) if df is not None else 0
    if n >= 200:
        score += 30; reasons.append("データ十分")
    elif n >= 60:
        score += 22; reasons.append("データやや十分")
    elif n >= 20:
        score += 12; reasons.append("データ少なめ")
    else:
        score += 4; reasons.append("データ不足")
    if (snap.get("vol_avg") or 0) > 0:
        score += 25; reasons.append("出来高十分")
    else:
        score += 8; reasons.append("出来高データ薄い")
    src = news_res.get("source")
    if src and src not in ("mock", "skip", None):
        score += 25; reasons.append("実ニュースあり")
    else:
        score += 8; reasons.append("ニュースはサンプル/無し")
    if news_res.get("classifier") == "ai":
        score += 20; reasons.append("AI解析あり")
    else:
        score += 10; reasons.append("テクニカル中心")
    return {"score": int(min(100, score)), "reasons": reasons}


def compute_scenarios(price, df, snap, net_impact=0.0, days=20):
    """強気/中立/弱気シナリオを計算して返す。
    返り値: {ok, price, days, vol, data_insufficient, bull/neutral/bear:{label,prob,range,mid,path,desc}}
    確率は必ず合計100%。"""
    price = float(price or 0)
    snap = snap or {}

    # --- ボラティリティ（直近20日リターンの標準偏差）---
    vol = None
    try:
        if df is not None and len(df) >= 10:
            v = float(df["Close"].pct_change().tail(20).std())
            if v and v == v and v > 0:  # NaN/0除外
                vol = v
    except Exception:
        vol = None
    data_insufficient = vol is None
    if vol is None:
        vol = 0.02

    sigma = vol * math.sqrt(max(1, int(days)))
    up = min(0.5, max(0.05, sigma * 2.0))
    down = min(0.5, max(0.04, sigma * 1.8))
    if data_insufficient:  # データ不足時は現在値±5〜15%に収める
        up = min(0.15, max(0.05, up))
        down = min(0.15, max(0.05, down))

    # --- 確率の傾き（テクニカル＋ニュース純影響）---
    tech_tilt = 0
    if snap.get("above_sma200"):
        tech_tilt += 6
    if (snap.get("macd_hist", 0) or 0) > 0:
        tech_tilt += 4
    rsi = snap.get("rsi", 50) or 50
    if rsi >= 70:
        tech_tilt -= 6
    elif rsi <= 35:
        tech_tilt += 4
    ni = float(net_impact or 0)

    # v17.1: ホライズン減衰。長期ほど確信(tilt/news)を弱め、不確実性(弱気)を少しだけ上げる。
    damp = math.sqrt(20.0 / max(1, int(days)))   # 20d=1.0 / 40d≈0.71 / 60d≈0.58
    unc = 1.0 - damp                              # 20d=0 / 40d≈0.29 / 60d≈0.42
    if data_insufficient:
        bull_p, bear_p = 25, 25  # 中立を高めに
    else:
        bull_p = 33 + (ni * 4 + tech_tilt) * damp - unc * 8   # 長期ほど強気の確信を弱める
        bear_p = 33 - (ni * 4 + tech_tilt) * damp + unc * 10  # 長期ほど弱気(不確実性)を少し上げる
    bull_p = int(max(10, min(70, bull_p)))
    bear_p = int(max(10, min(70, bear_p)))
    neutral_p = max(5, 100 - bull_p - bear_p)
    tot = bull_p + bear_p + neutral_p
    bull_p = round(bull_p / tot * 100)
    bear_p = round(bear_p / tot * 100)
    neutral_p = 100 - bull_p - bear_p  # 合計を必ず100%に

    # --- 想定レンジ ---
    bull_r = [round(price * (1 + up * 0.5), 2), round(price * (1 + up), 2)]
    neu_r = [round(price * (1 - down * 0.3), 2), round(price * (1 + up * 0.3), 2)]
    bear_r = [round(price * (1 - down), 2), round(price * (1 - down * 0.5), 2)]

    def _mid(r):
        return (r[0] + r[1]) / 2

    trend_up = bool(snap.get("above_sma200"))
    bull_desc = "上昇トレンド継続・出来高安定" + ("、好材料あり" if ni > 0.5 else "")
    neu_desc = "材料不足・現在値付近で推移"
    bear_bits = []
    if rsi >= 70:
        bear_bits.append("RSI過熱")
    if ni < -0.5:
        bear_bits.append("悪材料あり")
    if not trend_up:
        bear_bits.append("トレンド弱い（200日線下）")
    bear_desc = "調整リスク" + ("：" + "・".join(bear_bits) if bear_bits else "・直近安値/50日線割れ想定")

    def _scn(label, prob, r, desc):
        mid = round(_mid(r), 2)
        half = (r[1] - r[0]) / 2.0
        up_path, lo_path = _band(price, mid, half, days)
        return {"label": label, "prob": prob, "range": r, "mid": mid,
                "path": _path(price, mid, days), "band_upper": up_path, "band_lower": lo_path,
                "desc": desc}

    return {
        "ok": price > 0, "price": round(price, 2), "days": int(days),
        "vol": round(vol, 4), "data_insufficient": data_insufficient,
        "factors": _factor_lines(snap, ni),
        "bull": _scn("強気", bull_p, bull_r, bull_desc),
        "neutral": _scn("中立", neutral_p, neu_r, neu_desc),
        "bear": _scn("弱気", bear_p, bear_r, bear_desc),
    }


def build_scenario_chart(df, ticker, scen, days, past=60):
    """過去株価(実線)＋強気/中立/弱気(点線)のチャート。plotly無し/データ不正なら None。"""
    try:
        import plotly.graph_objects as go
        import pandas as pd
    except Exception:
        return None
    if df is None or len(df) == 0 or not scen.get("ok"):
        return None
    try:
        hist = df["Close"].dropna().tail(past)
        if len(hist) == 0:
            return None
        last = hist.index[-1]
        # 未来の x 軸（営業日）。DatetimeIndexでなければ数値インデックスにフォールバック
        if isinstance(last, pd.Timestamp):
            xhist = hist.index
            future = pd.bdate_range(start=last, periods=int(days) + 1)
        else:
            n = len(hist)
            xhist = list(range(n))
            future = list(range(n - 1, n - 1 + int(days) + 1))

        price = scen["price"]
        fx = list(future)
        fig = go.Figure()
        # v17.1: 半透明レンジ帯（lower→upper を fill）。凡例には出さず見やすく。
        for key, fill in [("bull", "rgba(22,163,74,0.12)"), ("neutral", "rgba(156,163,175,0.12)"),
                          ("bear", "rgba(220,38,38,0.12)")]:
            s = scen[key]
            if s.get("band_lower") and s.get("band_upper"):
                fig.add_trace(go.Scatter(x=fx, y=[price] + s["band_lower"], mode="lines",
                                         line=dict(width=0), hoverinfo="skip", showlegend=False))
                fig.add_trace(go.Scatter(x=fx, y=[price] + s["band_upper"], mode="lines",
                                         line=dict(width=0), fill="tonexty", fillcolor=fill,
                                         hoverinfo="skip", showlegend=False))
        # 過去株価（薄い実線）
        fig.add_trace(go.Scatter(x=list(xhist), y=list(hist.values), name="過去株価",
                                 line=dict(color="#e5e7eb", width=1.6)))
        # 中央値（点線・凡例に確率）
        for key, color in [("bull", "#16a34a"), ("neutral", "#9ca3af"), ("bear", "#dc2626")]:
            s = scen[key]
            y = [price] + s["path"]
            fig.add_trace(go.Scatter(x=fx, y=y, name=f'{s["label"]} {s["prob"]}%',
                                     line=dict(color=color, width=2, dash="dot")))
        fig.update_layout(
            height=380, template="plotly_dark", showlegend=True,
            xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=40, b=10),
            title=f"{ticker} 未来シナリオ（{int(days)}営業日）",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        return fig
    except Exception:
        return None
