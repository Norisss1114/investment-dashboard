"""
app.py — エントリーポイント。 起動: streamlit run app.py
v3: 買う前の分析(v2) ＋ 買った後の管理（ポジション/ポートフォリオ/アラート/発注メモ/履歴/反省）。
"""
import streamlit as st
from dotenv import load_dotenv
load_dotenv()

import config
import views
from modules import (engine, macro, data_fetch, storage, portfolio,
                     discovery, news as news_mod)

st.set_page_config(page_title=config.APP_TITLE, page_icon="📊", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
.block-container {padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1280px;}
[data-testid="stMetricValue"] {font-size: 1.18rem;}
section[data-testid="stSidebar"] {min-width: 320px;}
@media (max-width: 640px){ .block-container {padding-left:0.5rem; padding-right:0.5rem;} }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner="分析中…")
def run_analysis_map(tickers_tuple, market_score, market_mode, fomc_days):
    return engine.analyze_map(list(tickers_tuple), market_score, market_mode, fomc_days)


def init_state():
    if "loaded" not in st.session_state:
        s = storage.load_settings()
        st.session_state.watchlist_text = ", ".join(s["watchlist"])
        st.session_state.capital = float(s["capital"])
        st.session_state.risk_pct = float(s["risk_pct"])
        st.session_state.max_pos_pct = float(s["max_pos_pct"])
        st.session_state.loaded = True
    if "page" not in st.session_state:
        st.session_state["page"] = "🏠 ホーム"


def main():
    init_state()
    st.markdown("""
    <style>
    html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Helvetica Neue", "Hiragino Sans", "Yu Gothic UI", sans-serif; }
    .block-container { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1100px; }
    h1 { font-weight: 700; letter-spacing: -0.02em; }
    h2, h3 { font-weight: 650; letter-spacing: -0.01em; }
    /* カード（st.container(border=True)）をApple風に */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 18px !important; border: 1px solid #ececf1 !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04); background: #ffffff;
    }
    /* メトリクスを大きめ・落ち着いた色 */
    div[data-testid="stMetricValue"] { font-weight: 700; letter-spacing: -0.02em; }
    div[data-testid="stMetricLabel"] { color: #8a8a8e; }
    /* ボタンを丸く */
    .stButton > button { border-radius: 12px; font-weight: 600; border: 1px solid #e5e5ea; }
    .stDownloadButton > button { border-radius: 12px; }
    /* ラジオ（ナビ）を読みやすく */
    section[data-testid="stSidebar"] label p { font-size: 0.94rem; }
    /* 区切りを薄く */
    hr { border-color: #f0f0f3; }
    @media (max-width: 640px) {
      .block-container { padding-left: 0.6rem; padding-right: 0.6rem; }
    }
    /* v14.3: カード型ナビ（サイドバーのボタンを大きく押しやすく） */
    section[data-testid="stSidebar"] { min-width: 270px; }
    section[data-testid="stSidebar"] .stButton > button {
        min-height: 52px; width: 100%; justify-content: flex-start; text-align: left;
        font-size: 16px; font-weight: 600; border-radius: 14px; padding: 0 16px;
        margin-bottom: 4px; border: 1px solid #ececf1; background: #ffffff; color: #1d1d1f;
        transition: background .12s ease, transform .04s ease;
    }
    section[data-testid="stSidebar"] .stButton > button:hover { background: #f3f3f6; }
    section[data-testid="stSidebar"] .stButton > button:active { transform: scale(0.99); }
    /* 選択中ページ（primary）を濃色ハイライト */
    section[data-testid="stSidebar"] .stButton > button[kind="primary"],
    section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {
        background: #111114; color: #ffffff; border-color: #111114;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] { border: none; box-shadow: none; }
    section[data-testid="stSidebar"] [data-testid="stExpander"] summary { font-weight: 700; font-size: 0.95rem; }
    @media (max-width: 640px) {
      section[data-testid="stSidebar"] .stButton > button { min-height: 56px; font-size: 17px; }
    }
    </style>
    """, unsafe_allow_html=True)
    # 発掘ページからのウォッチリスト追加を、ウィジェット生成前に反映
    if "_pending_watchlist" in st.session_state:
        st.session_state.watchlist_text = ", ".join(st.session_state.pop("_pending_watchlist"))
    if "_added_msg" in st.session_state:
        kind, picks = st.session_state.pop("_added_msg")
        (st.success if kind == "ok" else st.warning)(
            ("✅ ウォッチリストに追加・保存しました: " if kind == "ok"
             else "⚠️ 追加しましたが保存に失敗: ") + ", ".join(picks))

    st.title("📊 " + config.APP_TITLE)
    st.caption("中期投資の『発注前の分析』＋『保有後の管理』を補助。⚠️ 投資助言ではありません。発注前にMoomooで最新値を確認。")

    # v4: ニュース/AIキーの状態バナー
    ks = news_mod.news_status()
    if not ks["fully_ready"]:
        miss = " ・ ".join(ks["missing"]) or "APIキー"
        st.info(f"🔑 未設定: **{miss}**。Streamlit Cloudでは **Secrets**、ローカルでは `.env`/環境変数に設定すると"
                "実ニュース＋AI分類でスコアの信頼性が上がります（未設定でも代替モードで動作します）。")
    else:
        ai_name = "OpenAI" if ks["openai"] else "Anthropic"
        st.success(f"🔑 フル機能稼働: Finnhub + {ai_name}（ニュース比重 {config.SCORE_WEIGHTS['news']}点）")

    positions = portfolio.load_positions()

    with st.sidebar:
        st.markdown("### 📊 メニュー")
        views.render_nav()
        page = st.session_state.get("page", "🏠 ホーム")
        if st.button("🔄 データ再取得", width='stretch', key="nav_refresh"):
            st.cache_data.clear(); st.rerun()

        st.divider()
        st.header("⚙️ 設定")
        if data_fetch.is_mock_mode():
            st.warning("🟡 **モックモード**（サンプルデータ）。yfinance導入で実データに。")
        else:
            st.success("🟢 ライブモード (yfinance)")

        # v14.1: 検索→ボタン追加・チップ削除・手動編集（textareaは中で生成）
        macro_data_for_wl = macro.get_market_indices()
        regime_for_wl = macro.assess_regime(macro_data_for_wl)
        views.render_sidebar_watchlist(positions, regime_for_wl)
        raw = st.session_state.get("watchlist_text", "")
        watch = [t.strip().upper() for t in raw.replace("\n", ",").split(",") if t.strip()]

        st.subheader("資金設定")
        st.number_input("総資金 ($)", min_value=0.0, step=500.0, key="capital")
        st.slider("1銘柄の最大損失 (%)", 0.5, 3.0, step=0.5, key="risk_pct")
        st.slider("1銘柄の上限 (%)", 5.0, 30.0, step=5.0, key="max_pos_pct")
        if st.button("💾 設定を保存", width='stretch'):
            ok = storage.save_settings(watch, st.session_state.capital,
                                       st.session_state.risk_pct, st.session_state.max_pos_pct)
            st.success("保存しました。") if ok else st.error("保存失敗。")

        st.subheader("APIキー状況（v4推奨）")
        s = news_mod.news_status()
        st.write(f"- Finnhub: {'✅' if s['finnhub'] else '❌ 未設定'}")
        st.write(f"- OpenAI: {'✅' if s['openai'] else '❌ 未設定'}")
        st.write(f"- Anthropic(任意): {'✅' if s['anthropic'] else '—'}")
        if s["fully_ready"]:
            st.success("フル機能で稼働中")
        else:
            st.caption("未設定でも代替モードで動作（精度は下がります）")

    # 市場 & 分析（ウォッチ＋保有の和集合）
    macro_data = macro.get_market_indices()
    regime = macro.assess_regime(macro_data)
    events = macro.upcoming_events()
    all_tickers = list(dict.fromkeys(watch + [p["ticker"] for p in positions]))
    rmap = run_analysis_map(tuple(all_tickers), regime["score"], regime["regime"], regime["fomc_days"]) if all_tickers else {}
    watch_results = [rmap[t] for t in watch if t in rmap]
    failed = [t for t in all_tickers if t in rmap and not rmap[t].get("ok")]
    if failed:
        st.warning(f"取得に失敗（スキップ）: {', '.join(failed)}")

    cap = st.session_state.capital
    risk_pct = st.session_state.risk_pct
    max_pos = st.session_state.max_pos_pct

    # ルーティング
    if page == "🏠 ホーム" or page.startswith("──"):
        views.render_home(regime, events, positions, rmap)
    elif page == "市場環境":
        views.render_market(macro_data, regime, events)
    elif page == "🔍 銘柄発掘":
        views.render_discovery(regime, tuple(watch))
    elif page == "📈 発掘バックテスト":
        views.render_discovery_backtest(tuple(watch))
    elif page == "🧪 ルール最適化":
        views.render_rule_optimization(tuple(watch))
    elif page == "🧠 ニュース精密解析":
        views.render_news_analysis(rmap, positions, tuple(watch), regime)
    elif page == "今日のアクション":
        views.render_ranking(watch_results, regime, positions, rmap)
    elif page == "個別銘柄分析":
        views.render_stock(watch_results)
    elif page == "ニュース分析":
        views.render_news(watch_results)
    elif page == "売買プラン":
        views.render_plan(watch_results)
    elif page == "リスク管理":
        views.render_risk(watch_results, regime, cap, risk_pct, max_pos)
    elif page == "バックテスト":
        views.render_backtest(watch_results)
    elif page == "ポジション台帳":
        views.render_positions(rmap, positions, regime, cap)
    elif page == "📷 Moomoo同期":
        views.render_moomoo_sync()
    elif page == "ポートフォリオリスク":
        views.render_portfolio_risk(rmap, positions, regime, cap)
    elif page == "アラート":
        views.render_alerts(rmap, positions, regime)
    elif page == "発注メモ":
        views.render_order_memo(rmap, positions, regime)
    elif page == "売買履歴":
        views.render_history()
    elif page == "反省AI":
        views.render_reflection()
    elif page == "🔔 通知設定":
        views.render_notifications(regime, events, positions, tuple(watch))

    st.divider()
    st.caption("⚠️ 教育・分析目的のツールです。将来の成果を保証しません。最終判断・責任はご自身で。"
               "損切り・分割・資金管理を徹底してください。")


if __name__ == "__main__":
    main()
