"""
ui_helpers.py — 表示まわりの共通部品（色、バッジ、初心者向け用語説明、チャート作成）。
"""
import config


def verdict_color(verdict: str) -> str:
    return config.COLORS.get(verdict, "#64748b")


def verdict_badge(verdict: str) -> str:
    """HTMLバッジ（緑/黄/赤）。"""
    color = verdict_color(verdict)
    return (f'<span style="background:{color};color:white;padding:4px 12px;'
            f'border-radius:999px;font-weight:700;font-size:0.9rem;">{verdict}</span>')


def fmt_money(v) -> str:
    try:
        return f"${v:,.2f}"
    except Exception:
        return "—"


def fmt_big(v) -> str:
    """時価総額などを 兆/億 表記に。"""
    try:
        v = float(v)
    except Exception:
        return "—"
    if v >= 1e12:
        return f"${v/1e12:.2f}兆"
    if v >= 1e8:
        return f"${v/1e8:.0f}億"
    if v >= 1e6:
        return f"${v/1e6:.0f}百万"
    return f"${v:,.0f}"


def fmt_pct(v) -> str:
    try:
        return f"{float(v)*100:.1f}%"
    except Exception:
        return "—"


# 初心者向け用語説明（ツールチップ/キャプションで使用）
GLOSSARY = {
    "RSI": "買われすぎ/売られすぎの目安。70以上=買われすぎ(短期下落注意)、30以下=売られすぎ。",
    "MACD": "勢いの指標。線がシグナルを上抜け=強気サイン、下抜け=弱気サイン。",
    "SMA200": "200日移動平均線。これより株価が上なら長期トレンドは強い。",
    "SMA50": "50日移動平均線。中期トレンドの目安。",
    "出来高": "売買された株数。急増は大口投資家の参加サインのことがある。",
    "PER": "株価÷1株利益。高いほど割高(=期待が大きい)、業種で適正水準は異なる。",
    "PSR": "株価÷1株売上。赤字でも使える割高/割安の目安。",
    "アナリスト目標株価": "証券会社アナリストの予想株価。あくまで予想で外れることも多い。",
    "VIX": "恐怖指数。20前後が平常、25超で警戒、30超で市場が怖がっている状態。",
    "損切り": "想定外に下がったとき、損失を限定するために売る価格。必ず決めておく。",
}


def explain(term: str) -> str:
    return GLOSSARY.get(term, "")


def rsi_comment(rsi: float) -> str:
    if rsi >= 70:
        return "買われすぎ → 短期的に下がる可能性。押し目を待つのが無難。"
    if rsi <= 30:
        return "売られすぎ → 反発の可能性もあるが、下落継続にも注意。"
    if 50 <= rsi < 70:
        return "やや強気ゾーン。トレンドは上向き気味。"
    return "中立〜やや弱気ゾーン。"


def build_price_chart(df, ticker: str):
    """
    ローソク足 + 移動平均 + 出来高 + RSI + MACD のサブプロット。
    plotly が無い環境では None を返す（呼び出し側でガード）。
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception:
        return None

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True,
        row_heights=[0.5, 0.16, 0.17, 0.17], vertical_spacing=0.03,
        subplot_titles=(f"{ticker} 株価 + 移動平均", "出来高", "RSI", "MACD"),
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name="ローソク足", increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
    ), row=1, col=1)
    for col, color, name in [("SMA20", "#3b82f6", "20日線"),
                             ("SMA50", "#f59e0b", "50日線"),
                             ("SMA200", "#a855f7", "200日線")]:
        if col in df:
            fig.add_trace(go.Scatter(x=df.index, y=df[col], name=name,
                                     line=dict(width=1.3, color=color)), row=1, col=1)

    colors = ["#16a34a" if c >= o else "#dc2626" for o, c in zip(df["Open"], df["Close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="出来高",
                         marker_color=colors, opacity=0.6), row=2, col=1)

    if "RSI" in df:
        fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], name="RSI",
                                 line=dict(color="#0ea5e9", width=1.3)), row=3, col=1)
        fig.add_hline(y=70, line=dict(color="#dc2626", dash="dash", width=1), row=3, col=1)
        fig.add_hline(y=30, line=dict(color="#16a34a", dash="dash", width=1), row=3, col=1)

    if "MACD" in df:
        fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], name="MACD",
                                 line=dict(color="#3b82f6", width=1.2)), row=4, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MACD_SIGNAL"], name="シグナル",
                                 line=dict(color="#f59e0b", width=1.2)), row=4, col=1)
        hist_colors = ["#16a34a" if v >= 0 else "#dc2626" for v in df["MACD_HIST"]]
        fig.add_trace(go.Bar(x=df.index, y=df["MACD_HIST"], name="ヒスト",
                             marker_color=hist_colors, opacity=0.5), row=4, col=1)

    fig.update_layout(
        height=760, template="plotly_dark", showlegend=True,
        xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig
