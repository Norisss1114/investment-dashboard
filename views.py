"""
views.py (v2) — 各画面の描画。app.py から呼ばれる。
"""
import time
import pandas as pd
import streamlit as st

import config
from modules import risk as risk_mod
from modules import backtest as bt_mod
from modules import ui_helpers as ui
from modules import news as news_mod
from modules import holding_monitor as hm_mod
from modules import data_fetch as data_fetch_mod
from modules import goal_plan as gp_mod
from modules import scenario as scen_mod
from modules import sec_edgar as sec_mod
from modules import news_calibration as ncal_mod
from modules import news_adoption as nadopt_mod


def _action_chip(act: dict) -> str:
    return (f'<span style="background:{act["color"]}22;border:1px solid {act["color"]};'
            f'color:{act["color"]};padding:3px 10px;border-radius:999px;font-weight:700;">'
            f'{act["emoji"]} {act["action"]}</span>')


# ============================================================ v15.1: 分析対象（ウォッチ∪保有）
def analysis_targets(rmap, watch=None, positions=None):
    """ウォッチ∪保有を分析対象として整理する共通ヘルパー。
    返り値 dict:
      tickers : 重複統合済みの対象（ウォッチ→保有の順）
      tag     : {ticker: '💼 保有' / '⭐ ウォッチ' / '💼⭐ 保有+ウォッチ'}
      valid   : ok=True の結果リスト（順序維持）
      failed  : [(ticker, 失敗理由), ...]
      mocks   : モックにフォールバックした ticker
      empty   : 対象が0件（保有もウォッチも無い）か"""
    rmap = rmap or {}
    watch = [str(t).upper() for t in (watch or [])]
    pos = [p["ticker"].upper() for p in (positions or [])]
    wset, pset = set(watch), set(pos)
    tickers = list(dict.fromkeys(watch + pos))
    tag = {}
    for t in tickers:
        if t in wset and t in pset:
            tag[t] = "💼⭐ 保有+ウォッチ"
        elif t in pset:
            tag[t] = "💼 保有"
        else:
            tag[t] = "⭐ ウォッチ"
    valid, failed, mocks = [], [], []
    for t in tickers:
        r = rmap.get(t)
        if r and r.get("ok"):
            valid.append(r)
            if r.get("source") == "mock" or (r.get("fund") or {}).get("is_sample"):
                mocks.append(t)
        else:
            failed.append((t, (r or {}).get("error") or "解析結果なし（rmap未登録・対象外）"))
    return {"tickers": tickers, "tag": tag, "valid": valid,
            "failed": failed, "mocks": mocks, "empty": not tickers}


def analysis_gate(rmap, watch, positions):
    """空/取得失敗の共通メッセージを表示し、続行可能なら targets を返す（不可なら None）。
    要件: 完全に空→案内(8) / 一部失敗→理由表示(5) / 全滅→『有効なデータがありません』(7) /
          1件でもokなら続行(6)。"""
    t = analysis_targets(rmap, watch, positions)
    if t["empty"]:
        st.info("保有もウォッチも未登録です。📷 **Moomoo同期**で保有を登録するか、"
                "🔎 **銘柄検索**でウォッチに追加してください。")
        return None
    if t["failed"]:
        st.warning("⚠️ 次の銘柄はデータ取得に失敗しました（yfinanceの一時的なレート制限・"
                   "シンボル相違などの可能性）：\n\n"
                   + "\n\n".join(f"・{tk}（{t['tag'][tk]}）: {why}" for tk, why in t["failed"]))
    if not t["valid"]:
        st.error("有効なデータがありません（対象 " + ", ".join(t["tickers"]) +
                 " のすべてで取得に失敗）。サイドバーの『🔄 データ再取得』を時間をおいてお試しください。")
        return None
    if t["mocks"]:
        st.caption("ℹ️ サンプル(モック)データ表示中: " + ", ".join(t["mocks"]) + "（実データ取得に失敗した銘柄）")
    return t


# ============================================================ 市場環境
def render_market(macro_data, regime, events):
    st.header("🌍 市場環境")
    mode_color = {"強気": "#16a34a", "中立": "#eab308", "弱気": "#f97316", "危険": "#dc2626"}.get(regime["regime"], "#64748b")

    st.markdown(
        f'<div style="padding:16px 20px;border-radius:14px;background:{mode_color}1a;border:1px solid {mode_color};">'
        f'<span style="font-size:1.6rem;font-weight:800;color:{mode_color};">市場モード: {regime["regime"]}</span>'
        f'<span style="margin-left:12px;font-size:1.1rem;color:#e2e8f0;">→ 今は「{regime["stance"]}」</span><br>'
        f'<span style="color:#cbd5e1;">{regime["reason"]}</span></div>',
        unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("🟢 新規買いに使ってよい比率", f'{regime["new_buy_pct"]}%')
    c2.metric("💵 現金で持つべき比率", f'{regime["cash_pct"]}%')
    c3.metric("スタンス", regime["stance"])
    st.caption("地合いが悪いほど現金比率を上げ、新規買いを減らすのが基本です。")

    s1, s2 = st.columns(2)
    s1.success("✅ 今、有利なセクター: " + " / ".join(regime["good_sectors"]))
    s2.error("⚠️ 今、不利なセクター: " + " / ".join(regime["bad_sectors"]))

    st.subheader("主要指数")
    cols = st.columns(len(macro_data) if macro_data else 1)
    for col, (name, d) in zip(cols, macro_data.items()):
        col.metric(name, f'{d["value"]:,}', f'{d["change_pct"]:+.2f}%')
    st.caption("💡 " + ui.explain("VIX"))

    wk, mo = events
    st.subheader("🗓️ 重要イベント")
    e1, e2 = st.columns(2)
    with e1:
        st.markdown("**今週（7日以内）**")
        if wk:
            for ev in wk:
                st.warning(f'あと{ev["days"]}日 — {ev["date"]} {ev["title"]}\n\n{ev.get("note","")}')
        else:
            st.caption("今週の登録イベントはありません。")
    with e2:
        st.markdown("**今月（31日以内）**")
        for ev in mo:
            st.write(f'・{ev["date"]}（あと{ev["days"]}日） {ev["title"]}')
        if not mo:
            st.caption("今月の登録イベントはありません。")

    st.subheader("マクロ指標（手入力）")
    snap = config.MACRO_SNAPSHOT
    m = st.columns(4)
    m[0].metric("政策金利", snap["fed_funds_rate"])
    m[1].metric("CPI(前年比)", snap["last_cpi_yoy"])
    m[2].metric("失業率", snap["last_unemployment"])
    m[3].metric("次回FOMC", snap["next_fomc"])
    st.info(snap["updated_note"] + "　イベントは config.py の EVENTS で編集できます。")


# ============================================================ 今日のアクション（ランキング）
def render_ranking(results, regime, positions=None, rmap=None):
    st.header("🎯 今日のアクション（コックピット）")
    st.caption("毎朝ここを見て『買う/待つ/逃げる』を判断。各行の1行コメントが“今日やること”です。")
    try:
        from modules import strategy as _strat
        _sr = _strat.resolved()
        if _sr["active"]:
            st.info("🧪 " + _strat.label_line() + "。発掘の重み・RSI除外・出来高条件・TOP件数に反映されています。")
        else:
            st.caption("🧪 採用ルール: なし（デフォルト設定）。『🧪 ルール最適化』で採用すると発掘に反映されます。")
    except Exception:
        pass

    # ===== 保有銘柄の今日のアクション（毎朝・スクショ不要） =====
    positions = positions or []
    rmap = rmap or {}
    if positions:
        st.subheader("Ⓐ 保有銘柄へのアクション（売る/継続/利確/損切り/逆指値更新）")
        st.caption("毎日スクショは不要。保存済みの保有に最新株価・ニュース・AI分析を当てて提案します。")
        for pos in positions:
            ev = pf_mod.evaluate_position(pf_mod._normalize(pos), rmap.get(pos["ticker"].upper()))
            hd = ha_mod.decide(ev, regime["regime"])
            todo = ha_mod.moomoo_todo(ev, hd)
            an = ev.get("analysis") or {}
            ni = an.get("news_sum", {}).get("avg_impact", 0) if an.get("ok") else 0
            snap = ev.get("snap", {}) or {}
            tech = (f"RSI {snap.get('rsi',50):.0f}・"
                    + ("200日線↑" if snap.get("above_sma200") else "200日線↓"))
            ed = ev.get("earnings_days")
            erisk = (f"決算まで{ed}日" if (ed is not None and 0 <= ed <= 14) else "—")
            pl_color = "#16a34a" if ev["pl"] >= 0 else "#dc2626"
            with st.container(border=True):
                st.markdown(f'### {ev["ticker"]}　' + _hold_chip(hd), unsafe_allow_html=True)
                m = st.columns(6)
                m[0].metric("株数", f'{ev["shares"]:.0f}')
                m[1].metric("取得単価", f'${ev["avg_cost"]}')
                m[2].metric("現在値", f'${ev["current"]}')
                m[3].markdown(f'<span style="color:{pl_color};font-weight:700;">含み損益<br>'
                              f'${ev["pl"]:,.0f}<br>({ev["pl_pct"]:+.1f}%)</span>', unsafe_allow_html=True)
                m[4].metric("ニュース影響", f'{ni:+.1f}')
                m[5].metric("決算リスク", erisk)
                st.write(f'**テクニカル**: {tech}　|　**推奨アクション**: {hd["action"]}')
                st.info("📱 Moomooで今日やること: " + todo)
                # v15: 保有ニュース監視からの価格提案・影響予想・今日の対応
                mo = hm_mod.monitor_position(pf_mod._normalize(pos), rmap.get(pos["ticker"].upper()), regime)
                lv = mo["levels"]; fc = mo["forecast"]
                st.markdown(f'🗓 **今日の対応**: {mo["today"]}　|　**影響予想** '
                            f'短期:{fc["短期"]} / 中期:{fc["中期"]} / スイング:{fc["スイング"]}'
                            f'　|　**ニュース重要度** {mo["news_importance"]}')
                pcols = st.columns(4)
                pcols[0].caption(f"損切り ${lv['stop']}")
                pcols[1].caption(f"利確1 ${lv['tp1']}")
                pcols[2].caption(f"半分売る ${lv['half']}")
                pcols[3].caption(f"全部撤退 ${lv['exit_all']}")
        st.caption("💡 詳しい価格の理由・今後のイベントは「🗓 保有ニュース監視」ページへ。")
        st.divider()

    valid = [r for r in results if r.get("ok")]
    if not valid:
        if positions:
            st.subheader("Ⓑ ウォッチ銘柄へのアクション（買う/待つ/見送り）")
            st.caption("ウォッチリストは未登録です。サイドバーの🔎銘柄検索や🔍銘柄発掘の『☆ウォッチ追加』で買い候補を登録できます。")
        else:
            st.warning("有効なデータがありません。サイドバーでウォッチに追加するか、Moomoo同期で保有を登録してください。")
        return
    st.subheader("Ⓑ ウォッチ銘柄へのアクション（買う/待つ/見送り）")

    order = {"今すぐ買い": 0, "押し目待ち": 1, "ニュース待ち": 2, "決算後まで待ち": 3, "買わない": 4, "危険": 5}
    valid_sorted = sorted(valid, key=lambda r: (order.get(r["action"]["action"], 9), -r["scores"]["total"]))
    for r in valid_sorted:
        a = r["action"]
        st.markdown(
            f'<div style="padding:10px 14px;border-left:5px solid {a["color"]};background:{a["color"]}12;'
            f'border-radius:8px;margin-bottom:6px;">{_action_chip(a)}'
            f'<span style="margin-left:10px;font-weight:700;">{r["ticker"]}</span>'
            f'<span style="margin-left:6px;color:#64748b;">({r["scores"]["total"]:.0f}点 / {r["scores"]["verdict"]})</span><br>'
            f'<span style="color:#334155;">{a["today"]}</span></div>',
            unsafe_allow_html=True)

    st.divider()
    st.subheader("スコア一覧")
    rows = []
    for r in valid:
        s = r["scores"]; f = r["fund"]
        upside = (f.get("target", f["price"]) / f["price"] - 1) * 100 if f.get("price") else 0
        rows.append({"ティッカー": r["ticker"], "アクション": r["action"]["action"], "判定": s["verdict"],
                     "総合": s["total"], "上値%": round(upside, 1), "現在値": f["price"],
                     "ファンダ": s["fundamental"], "テク": s["technical"], "ニュース": s["news"],
                     "市場": s["market"], "需給": s["supply_demand"]})
    df = pd.DataFrame(rows).sort_values("総合", ascending=False).reset_index(drop=True)
    df.index += 1
    styled = (df.style
              .map(lambda v: f'background-color:{ui.verdict_color(v)};color:white;font-weight:700;', subset=["判定"])
              .map(lambda v: f'background-color:{config.ACTIONS.get(v,{}).get("color","#64748b")};color:white;', subset=["アクション"])
              .format({"現在値": "${:,.2f}", "総合": "{:.1f}", "上値%": "{:+.1f}%",
                       "ファンダ": "{:.0f}", "テク": "{:.0f}", "ニュース": "{:.0f}", "市場": "{:.0f}", "需給": "{:.0f}"})
              .background_gradient(subset=["総合"], cmap="RdYlGn", vmin=40, vmax=90))
    st.dataframe(styled, width='stretch', height=430)
    st.caption("※スコア・アクションは公開情報に基づく機械的な目安で、投資助言ではありません。")


# ============================================================ v17: 未来シナリオ予測
_SCEN_META = {"bull": ("強気", "#16a34a"), "neutral": ("中立", "#9ca3af"), "bear": ("弱気", "#dc2626")}


_JD_COLOR = {"強気": "#15803d", "やや強気": "#16a34a", "中立": "#9ca3af",
             "やや弱気": "#f97316", "弱気": "#dc2626"}


def _scen_chips(items):
    """(label, value, color) のリストを、スマホで折り返すチップ列のHTMLに。"""
    html = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 6px;">'
    for label, value, color in items:
        html += (f'<div style="flex:1 1 96px;min-width:96px;background:#f7f7fa;border-radius:12px;'
                 f'padding:8px 10px;border-left:4px solid {color};">'
                 f'<div style="font-size:0.7rem;color:#8a8a8e;">{label}</div>'
                 f'<div style="font-size:1.05rem;font-weight:800;color:{color};line-height:1.2;">{value}</div></div>')
    return html + '</div>'


def _render_scenario_forecast(r, ticker, f, psrc, positions=None):
    """個別銘柄分析の既存チャート下に『未来シナリオ予測（アナリスト風）』を追加。
    v17.3: 表示のみ結論ファーストに再構成（計算は不変）。"""
    st.markdown("#### 🔮 未来シナリオ予測")
    price = f.get("price")
    # 現在値が取れない/仮値のときは予測しない
    if not price or price <= 0 or psrc in ("unavailable", "fallback_avg_cost"):
        st.warning("現在値が取得できない/仮値のため予測しません。")
        return

    days = st.selectbox("予測期間", [20, 40, 60], index=0,
                        format_func=lambda d: f"{d}営業日", key=f"scen_days_{ticker}")
    # 計算（v17.2 のまま・変更なし）
    ascore = scen_mod.analyst_score(r)
    net_impact = (r.get("news_sum") or {}).get("avg_impact", 0) or 0
    scen = scen_mod.compute_scenarios(price, r.get("df"), r.get("snap") or {}, net_impact, days,
                                      tilt=ascore["tilt"])
    held = any(p["ticker"].upper() == ticker.upper() for p in (positions or []))
    summary = scen_mod.analyst_summary(scen, ascore, held=held)
    q = scen_mod.quality_score(r.get("df"), r.get("snap") or {}, r.get("news") or {},
                               fund=f, e_days=ascore.get("earnings_days"), vol_risk=ascore.get("vol_risk"))
    vol = ascore.get("vol")
    jd = summary["judgment"]; jc = _JD_COLOR.get(jd, "#64748b")

    note = []
    if psrc == "mock" or f.get("is_sample"):
        note.append("サンプルデータによる参考シナリオ")
    if scen.get("data_insufficient"):
        note.append("データ不足のため中立寄り・概算")

    # ===== 1. 🧠 総評カード（結論ファースト・主確率を特大） =====
    with st.container(border=True):
        st.markdown(f'🧠 **{ticker} アナリスト風総評**')
        st.markdown(
            f'<div style="text-align:center;padding:2px 0 6px;">'
            f'<div style="font-size:1.05rem;font-weight:800;color:{jc};">{jd}</div>'
            f'<div style="font-size:2.6rem;font-weight:900;color:{jc};line-height:1.05;">{summary["main_prob"]}%</div>'
            f'<div style="font-size:0.74rem;color:#8a8a8e;">主シナリオ {summary["main_label"]}　・　予測信頼度 '
            f'<b style="color:#1d1d1f;">{q["score"]}</b>/100</div></div>',
            unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.86rem;">📈 <span style="color:#16a34a;">${summary["up_level"]} 上抜けで強気継続</span>'
            f'　／　📉 <span style="color:#dc2626;">${summary["down_level"]} 割れで弱気転換</span></div>'
            f'<div style="font-size:0.92rem;margin-top:4px;">🎯 投資行動：<b>{summary["action"]}</b></div>',
            unsafe_allow_html=True)
        if note:
            st.caption("※ " + " ・ ".join(note))

    # ===== 2. 🔮 シナリオチャート（既存・不変） =====
    fig = scen_mod.build_scenario_chart(r.get("df"), ticker, scen, days)
    if fig is not None:
        st.plotly_chart(fig, width='stretch')

    # ===== 3. 強気/中立/弱気カード =====
    for key in ("bull", "neutral", "bear"):
        s = scen[key]; _, color = _SCEN_META[key]
        lo, hi = s["range"]
        st.markdown(
            f'<div style="border-left:4px solid {color};padding:4px 0 4px 10px;margin-bottom:4px;">'
            f'<b style="color:{color};font-size:1.0rem;">{s["label"]} {s["prob"]}%</b>'
            f'　<span style="font-size:0.84rem;">想定レンジ ${lo}〜${hi}</span><br>'
            f'<span style="font-size:0.8rem;color:#8a8a8e;">{s["desc"]}</span></div>',
            unsafe_allow_html=True)

    # ===== 4. 🎯 目標株価到達確率（target がある時だけ・チップ） =====
    target = f.get("target")
    if target and target > 0 and price > 0:
        up_room = (target / price - 1) * 100
        st.markdown("**🎯 目標株価到達確率**")
        st.markdown(_scen_chips([
            ("現在値", f"${price:,.2f}", "#1d1d1f"),
            ("目標株価", f"${target:,.2f}", "#0ea5e9"),
            ("上昇余地", f"{up_room:+.1f}%", "#16a34a" if up_room >= 0 else "#dc2626"),
        ]), unsafe_allow_html=True)
        up = target >= price
        chips = []
        for d in (20, 40, 60):
            p_t = scen_mod.prob_reach(price, target, vol, d, ascore["tilt"], up=up)
            chips.append((f"{d}営業日 到達目安", f"{p_t}%" if p_t is not None else "—", "#0ea5e9"))
        st.markdown(_scen_chips(chips), unsafe_allow_html=True)

    # ===== 5. 💰 利確/損切り到達確率（保有のみ・色付きチップ） =====
    if held:
        try:
            pos = next(p for p in positions if p["ticker"].upper() == ticker.upper())
            ev = pf_mod.evaluate_position(pf_mod._normalize(pos), r)
            lv = hm_mod.price_levels(ev)
            st.markdown("**💰 利確 / 損切り 到達確率**")
            chips = []
            for lab, key, up, color in [("利確1", "tp1", True, "#16a34a"), ("利確2", "tp2", True, "#16a34a"),
                                        ("利確3", "tp3", True, "#16a34a"), ("損切り", "stop", False, "#dc2626")]:
                lvl = lv.get(key)
                p = scen_mod.prob_reach(price, lvl, vol, days, ascore["tilt"], up=up)
                suffix = "到達目安" if up else "下落リスク"
                chips.append((f"{lab} ${lvl}", f"{p}% {suffix}" if p is not None else "—", color))
            st.markdown(_scen_chips(chips), unsafe_allow_html=True)
        except Exception:
            pass

    # ===== 6. 🧮 スコア分解（折りたたみ） =====
    with st.expander("🧮 アナリスト風スコア分解（0〜100）", expanded=False):
        sub = ascore["subscores"]
        labels = [("トレンド", "trend"), ("モメンタム", "momentum"), ("出来高", "volume"),
                  ("ニュース", "news"), ("ファンダメンタル", "fundamental"),
                  ("決算リスク", "earnings_risk"), ("ボラリスク", "volatility_risk"), ("目標株価余地", "target_room")]
        gc = st.columns(2)
        for i, (lab, k) in enumerate(labels):
            gc[i % 2].markdown(f'- {lab}：**{sub[k]}**')
        st.caption(f"合成傾きスコア：{ascore['tilt']:+.1f}（+で強気寄り / −で弱気寄り、±35が上限）")

    # ===== 7. 📊 根拠（折りたたみ） =====
    with st.expander("📊 シナリオの根拠（使用した指標）", expanded=False):
        st.caption("信頼度の理由：" + " ・ ".join(q["reasons"]))
        for line in scen.get("factors", []):
            st.markdown(f"- {line}")

    # ===== 8. 免責（小さく） =====
    st.caption("⚠️ 断定ではなく、現在のボラ・テクニカル・ニュースから作る概算シナリオです。"
               "到達確率はタッチ確率でなく終端確率ベースの簡易推定。投資助言ではありません。発注前にMoomooで確認を。")


def _fmt_big(v):
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def _fmt_pct(v):
    return f"{v*100:+.1f}%" if isinstance(v, (int, float)) else "—"


def _render_sec_edgar(ticker, fund):
    """v26: SEC EDGAR 公式ファンダ（参考表示のみ・ボタン実行・スコア非使用）。"""
    with st.expander("🏛 SEC公式ファンダ（SEC EDGAR・参考表示／スコア未使用）", expanded=False):
        ua, ua_set = sec_mod.user_agent()
        if not ua_set:
            st.warning("SEC_USER_AGENT未設定です。Streamlit Secrets / 環境変数 / .env に連絡先付きで設定してください"
                       "（既定UAで動作中・SECにブロックされる場合があります）。")
        key = f"sec_{ticker}"
        if st.button("🏛 SEC公式ファンダを取得", key=f"sec_btn_{ticker}", width='stretch'):
            with st.spinner("SEC EDGAR から取得中…"):
                st.session_state[key] = sec_mod.get_fundamentals(ticker)
        e = st.session_state.get(key)
        if e is None:
            st.caption("ボタンを押すと SEC 公式データを取得します（自動取得しません）。")
            return
        if not e.get("ok"):
            st.info("SECデータ未取得（" + e.get("reason", "") + "）。")
            _render_sec_quality(e, fund)
            return
        c = st.columns(3)
        c[0].metric("最新売上", _fmt_big(e.get("revenue")))
        c[1].metric("純利益", _fmt_big(e.get("net_income")))
        c[2].metric("EPS", f'{e["eps"]:.2f}' if isinstance(e.get("eps"), (int, float)) else "—")
        c2 = st.columns(3)
        c2[0].metric("粗利益", _fmt_big(e.get("gross_profit")))
        c2[1].metric("営業利益", _fmt_big(e.get("operating_income")))
        c2[2].metric("営業CF", _fmt_big(e.get("operating_cash_flow")))
        c3 = st.columns(3)
        c3[0].metric("総資産", _fmt_big(e.get("assets")))
        c3[1].metric("負債", _fmt_big(e.get("liabilities")))
        c3[2].metric("株主資本", _fmt_big(e.get("equity")))
        c4 = st.columns(5)
        c4[0].metric("売上成長", _fmt_pct(e.get("rev_growth")))
        c4[1].metric("EPS成長", _fmt_pct(e.get("eps_growth")))
        c4[2].metric("利益率", _fmt_pct(e.get("margin")))
        c4[3].metric("ROE", _fmt_pct(e.get("roe")))
        c4[4].metric("負債比率", _fmt_pct(e.get("debt_ratio")))
        fresh = e.get("freshness_days")
        st.caption(f"📅 filing date: {e.get('filing_date','—')}（FY{e.get('fiscal_year','—')} {e.get('fiscal_period','—')}）"
                   + (f"・データ鮮度 {fresh}日前" if isinstance(fresh, int) else "") + f"・CIK {e.get('cik','—')}")

        # yfinance との差分
        cmp = sec_mod.compare_with_yfinance(fund, e)
        st.markdown("**yfinance との差分（ポイント%）**")
        rows = [{"指標": v["label"],
                 "yfinance": (f'{v["yf"]*100:+.1f}%' if isinstance(v["yf"], (int, float)) else "—"),
                 "SEC": (f'{v["edgar"]*100:+.1f}%' if isinstance(v["edgar"], (int, float)) else "—"),
                 "差(pp)": (v["diff_pp"] if v["diff_pp"] is not None else "—")} for v in cmp.values()]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        st.caption("※SEC EDGAR 公式（直近年次）。**スコア計算には使用していません（参考表示）。** 値の単位・期は会社により異なります。")

        _render_sec_quality(e, fund)


_QUALITY_COLOR = {"高": "#16a34a", "中": "#eab308", "低": "#dc2626"}


def _render_sec_quality(edgar, fund):
    """v26.1: ファンダ品質チェック（表示のみ・スコア非使用）。"""
    q = sec_mod.quality_check(edgar, fund)
    qc = _QUALITY_COLOR.get(q["quality"], "#64748b")
    cc = _QUALITY_COLOR.get(q["confidence"], "#64748b")
    st.markdown("**🧪 ファンダ品質チェック**")
    st.markdown(
        f'<div style="font-size:0.95rem;">総合品質：<b style="color:{qc};">{q["quality"]}</b>　'
        f'｜SEC取得信頼度：<b style="color:{cc};">{q["confidence"]}</b>　'
        f'｜データ鮮度：{(str(q["freshness_days"]) + "日前") if isinstance(q["freshness_days"], int) else "—"}　'
        f'｜欠損項目：{q["missing_count"] if q["missing_count"] is not None else "—"}</div>',
        unsafe_allow_html=True)
    if q["warnings"]:
        st.warning("⚠️ yfinanceとの差分/注意点: " + " ／ ".join(q["warnings"]))
    with st.expander("判定理由", expanded=False):
        for r in q["reasons"]:
            st.markdown(f"- {r}")
    st.caption("※品質チェックは参考表示です。**スコア・発掘ランキングには一切反映していません。**")


def _render_news_calibration(ticker):
    """v27: ニュース較正データ（実日付付きニュース＋後続リターンの保存/更新・表示のみ）。
    ⚠️ 本番ニューススコア・発掘スコア・ランキングには一切影響しない。ボタン実行のみ。"""
    with st.expander("🧪 ニュース較正データ（実日付ニュース＋後続リターン／本番スコア未使用）", expanded=False):
        st.caption("ニュース（実日付付きのみ）と、その後の株価リターンを保存して将来の較正に使います。"
                   "**スコア・発掘ランキングには一切反映しません。** ボタン実行のみ（自動取得しません）。")
        if not config.get_secret("FINNHUB_API_KEY"):
            st.warning("FINNHUB_API_KEY未設定です。実日付付きニュースを取得できないため保存できません"
                       "（Streamlit Secrets / 環境変数 / .env に設定してください）。")

        c = st.columns(2)
        if c[0].button("📥 この銘柄のニュースを較正データに保存", key=f"ncal_save_{ticker}", width='stretch'):
            with st.spinner("Finnhub からニュース取得中…"):
                ok, msg = ncal_mod.add_for_ticker(ticker)
            (st.success if ok else st.warning)(msg)
        if c[1].button("🔄 較正リターンを更新", key=f"ncal_upd_{ticker}", width='stretch'):
            with st.spinner("価格履歴からリターンを計算中…"):
                n = ncal_mod.update_returns()
            st.success(f"{n} 件のリターンを更新しました（mock/失敗は unavailable）。")

        recs = ncal_mod.list_all(ticker)
        total = len(ncal_mod.list_all())
        st.caption(f"保存件数：全体 {total} 件 ／ {ticker} {len(recs)} 件")
        if not recs:
            st.info("この銘柄の較正データはまだありません。上のボタンで保存してください。")
            return
        recs = sorted(recs, key=lambda r: r.get("news_date", ""), reverse=True)

        def _f(v, suf="%"):
            return (f"{v:+.1f}{suf}" if isinstance(v, (int, float)) else "—")

        rows = [{
            "日付": r.get("news_date", "—"),
            "見出し": (r.get("headline", "") or "")[:50],
            "impact": (r.get("impact_score") if r.get("impact_score") is not None else "—"),
            "sentiment": r.get("sentiment", "—"),
            "ret_7": _f(r.get("ret_7")),
            "ret_30": _f(r.get("ret_30")),
            "vs_spy_30": _f(r.get("vs_spy_30")),
            "status": r.get("status", "—"),
        } for r in recs]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        st.caption("※実日付付き（Finnhub datetime）のニュースのみ保存。**スコア・ランキングには未使用（較正用データ）。**")


def _ncal_bd_df(rows, label_name):
    """analytics の breakdown 行を表示用 DataFrame に。"""
    def _f(v):
        return (f"{v:+.1f}%" if isinstance(v, (int, float)) else "—")
    return pd.DataFrame([{
        label_name: r["label"], "件数": r["n"],
        "平均ret7": _f(r.get("avg_ret7")), "平均ret30": _f(r.get("avg_ret30")),
        "平均vsSPY30": _f(r.get("avg_vs_spy30")),
        "勝率%": (r["win_rate"] if r.get("win_rate") is not None else "—"),
    } for r in rows])


def _render_news_calibration_analytics():
    """v27.1: ニュース較正データの集計分析（全銘柄横断・表示のみ・スコア非反映）。"""
    with st.expander("📊 ニュース較正分析（全銘柄横断の集計／本番スコア未反映）", expanded=False):
        a = ncal_mod.analytics()
        ov = a["overall"]
        if not ov["saved"]:
            st.info("較正データがまだありません。上の「🧪 ニュース較正データ」で保存してください。")
            return
        if not ov["updated"]:
            st.info("更新済みデータがありません（「🔄 較正リターンを更新」でリターンを計算してください）。")
            st.caption(f"保存 {ov['saved']} 件 ／ 更新済み 0 件")
            return

        if (ov.get("ret30_count") or 0) < 5:
            st.warning("サンプルが少ないため参考値です。まだニューススコアには反映しないでください。")

        def _f(v):
            return (f"{v:+.1f}%" if isinstance(v, (int, float)) else "—")

        m = st.columns(4)
        m[0].metric("保存件数", ov["saved"])
        m[1].metric("更新済み", ov["updated"])
        m[2].metric("ret7件数", ov["ret7_count"])
        m[3].metric("ret30件数", ov["ret30_count"])
        m2 = st.columns(4)
        m2[0].metric("平均ret7", _f(ov["avg_ret7"]))
        m2[1].metric("平均ret30", _f(ov["avg_ret30"]))
        m2[2].metric("平均vsSPY30", _f(ov["avg_vs_spy30"]))
        m2[3].metric("ret30勝率", f'{ov["win_rate_30"]}%' if ov["win_rate_30"] is not None else "—")

        blocks = [("impact_score別（-5〜+5実値）", a["by_impact"], "impact"),
                  ("sentiment別", a["by_sentiment"], "sentiment"),
                  ("category別（出現値ごと）", a["by_category"], "category")]
        for title, rows, label_name in blocks:
            st.markdown(f"**{title}**")
            if rows:
                st.dataframe(_ncal_bd_df(rows, label_name), width='stretch', hide_index=True)
            else:
                st.caption("該当データなし（更新後に集計されます）。")

        # 🧠 較正提案
        st.divider()
        st.subheader("🧠 較正提案（impact_score の妥当性チェック）")
        rec = ncal_mod.recommendation()
        if rec.get("data_insufficient"):
            st.info("判定対象（ret_30 計算済み）がまだありません。")
        else:
            st.caption(f"信頼度：{rec['confidence']}（判定対象 n={rec['judged']}）"
                       + ("　｜impact単調性：" + ("OK" if rec.get("monotonic_ok") else "逆転あり")
                          if rec.get("monotonic_ok") is not None else ""))
            for sug in rec.get("suggestions", []):
                st.markdown(f"- {sug}")
            if rec.get("low_sample"):
                with st.expander(f"サンプル不足（n<3・判断に未使用）{len(rec['low_sample'])}件", expanded=False):
                    for r in rec["low_sample"]:
                        st.caption(f"・{r['category']}「{r['label']}」 n={r['n']}")
            if rec.get("next_steps"):
                st.markdown("**次にやること**")
                for ns in rec["next_steps"]:
                    st.markdown(f"- {ns}")
        st.caption("※集計・提案は参考表示です。**ニューススコア・発掘スコア・ランキングには一切反映していません。**")

        # 🧭 v28: ニューススコア補正案（表示のみ・本番非反映）
        _render_news_score_corrections()

        # 📌 v27.2: 検証候補として保存（本番非反映）
        _render_news_adoption()

        # 🔐 v42: 本番反映ステータス（候補有無に依らず常に表示・apply 関数なし・本番非接続）
        _render_production_overrides_status()


def _render_news_score_corrections():
    """v28: ニューススコア補正案ジェネレーター（表示のみ・本番スコア非反映）。"""
    st.divider()
    st.subheader("🧭 ニューススコア補正案")
    cp = ncal_mod.correction_proposals()
    if cp.get("data_insufficient"):
        st.info("補正案を出せる更新済みデータ（ret_30 計算済み）がまだありません。")
        return

    def _f(v):
        return (f"{v:+.1f}%" if isinstance(v, (int, float)) else "—")

    if cp["confidence"] != "高":
        st.warning("サンプルが少ないため参考値です。まだニューススコアには反映しないでください。")
    st.caption(f"信頼度：{cp['confidence']}（補正対象は n≥5 のみ）")

    # 1) impact_score 補正案
    st.markdown("**1. impact_score 補正案**")
    if cp["impact"]:
        rows = [{
            "現在score": p["current_score"], "推奨score": p["recommended_score"],
            "delta": f'{p["delta"]:+d}', "n": p["n"],
            "avg_ret30": _f(p["avg_ret30"]), "avg_vsSPY30": _f(p["avg_vs_spy30"]),
            "win_rate%": (p["win_rate"] if p["win_rate"] is not None else "—"),
            "根拠": p["reason"],
        } for p in cp["impact"]]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("補正対象なし（delta=0 または n<5）。")

    # 2) sentiment 評価
    st.markdown("**2. sentiment 評価（定性のみ・数値補正なし）**")
    if cp["sentiment"]:
        rows = [{
            "sentiment": p["label"], "評価": p["evaluation"], "n": p["n"],
            "avg_ret30": _f(p["avg_ret30"]), "avg_vsSPY30": _f(p["avg_vs_spy30"]),
            "win_rate%": (p["win_rate"] if p["win_rate"] is not None else "—"),
            "根拠": p["reason"],
        } for p in cp["sentiment"]]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("評価対象なし（n<5）。")

    # 3) category 補正案
    st.markdown("**3. category 補正案（+1 / -1）**")
    if cp["category"]:
        rows = [{
            "category": p["label"], "補正候補": f'{p["correction"]:+d}', "n": p["n"],
            "avg_ret30": _f(p["avg_ret30"]), "avg_vsSPY30": _f(p["avg_vs_spy30"]),
            "win_rate%": (p["win_rate"] if p["win_rate"] is not None else "—"),
            "根拠": p["reason"],
        } for p in cp["category"]]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("補正対象なし（n<5 または中間）。")

    # 4) サンプル不足
    if cp["insufficient"]:
        with st.expander(f"サンプル不足（n<5・補正案に未使用）{len(cp['insufficient'])}件", expanded=False):
            for r in cp["insufficient"]:
                st.caption(f"・[{r['block']}]「{r['label']}」 n={r['n']}")

    st.info("これは表示のみで、本番ニューススコアには反映していません。"
            "サンプルが少ない場合は採用しないでください。")

    # 📌 v29: 補正案を検証候補として保存（本番非反映）
    if st.button("📌 補正案を検証候補に保存", key="nscore_save", width='stretch'):
        total, created, updated = nadopt_mod.save_score_corrections()
        if total == 0:
            st.info("保存できる補正案（n≥5・delta≠0・示唆あり）がありませんでした。")
        else:
            st.success(f"補正案 {total} 件を検証候補に保存しました（新規 {created} / 更新 {updated}）。"
                       "※本番スコアには反映しません。")
    st.caption("保存した補正案は下の「📋 ニュース採用候補」に score_correction として表示されます。")

    # 🧪 v30: 承認済み補正案のシミュレーション検証（表示のみ・本番非反映）
    _render_news_correction_simulation()


_SIM_VERDICT_COLOR = {"改善": "#16a34a", "変化なし": "#64748b", "悪化": "#dc2626", "サンプル不足": "#eab308"}


def _render_news_correction_simulation():
    """v30: 承認済み score_correction を過去較正データに仮適用して検証（表示のみ・本番非反映）。"""
    st.divider()
    st.subheader("🧪 ニュース補正シミュレーション")
    # approved な score_correction のみ news_adoption から抽出して注入（循環import回避）
    cands = [c for c in nadopt_mod.list_all()
             if c.get("type") == "score_correction" and c.get("status") == "approved"]
    sim = ncal_mod.simulate_corrections(candidates=cands)

    def _f(v):
        return (f"{v:+.1f}%" if isinstance(v, (int, float)) else "—")

    def _c(v):
        return (f"{v:+.3f}" if isinstance(v, (int, float)) else "—")

    vc = _SIM_VERDICT_COLOR.get(sim["verdict"], "#64748b")
    m = st.columns(4)
    m[0].metric("承認済み補正案", sim["approved_count"])
    m[1].metric("適用対象ニュース", sim["applicable_news"])
    m[2].metric("対象n(eligible)", sim["eligible_n"])
    m[3].metric("sentiment未適用", sim["sentiment_approved"])
    st.markdown(f'判定：<b style="color:{vc};">{sim["verdict"]}</b>', unsafe_allow_html=True)
    if sim["sentiment_approved"]:
        st.caption(f"承認済み sentiment 評価 {sim['sentiment_approved']}件（数値補正なしのため未適用）")

    if sim["verdict"] == "サンプル不足":
        st.warning("サンプルが少ないため判定できません（参考値）。まだニューススコアには反映しないでください。")

    b, a = sim["before"], sim["after"]
    comp = pd.DataFrame([
        {"指標": "ret30相関", "before": _c(b["corr_ret30"]), "after": _c(a["corr_ret30"])},
        {"指標": "vsSPY30相関", "before": _c(b["corr_vs_spy30"]), "after": _c(a["corr_vs_spy30"])},
        {"指標": "単調性違反数", "before": b["mono_violations"], "after": a["mono_violations"]},
        {"指標": "high impact n", "before": b["high"]["n"], "after": a["high"]["n"]},
        {"指標": "high avg_ret30", "before": _f(b["high"]["avg_ret30"]), "after": _f(a["high"]["avg_ret30"])},
        {"指標": "high avg_vsSPY30", "before": _f(b["high"]["avg_vs_spy30"]), "after": _f(a["high"]["avg_vs_spy30"])},
        {"指標": "low impact n", "before": b["low"]["n"], "after": a["low"]["n"]},
        {"指標": "low avg_ret30", "before": _f(b["low"]["avg_ret30"]), "after": _f(a["low"]["avg_ret30"])},
        {"指標": "low avg_vsSPY30", "before": _f(b["low"]["avg_vs_spy30"]), "after": _f(a["low"]["avg_vs_spy30"])},
    ])
    st.markdown("**補正前 / 補正後 比較**")
    st.dataframe(comp, width='stretch', hide_index=True)

    if sim["table"]:
        st.markdown("**score別 before / after**")
        rows = [{
            "score": t["score"],
            "before_n": t["before_n"], "before_ret30": _f(t["before_avg_ret30"]),
            "before_vsSPY30": _f(t["before_avg_vs_spy30"]),
            "after_n": t["after_n"], "after_ret30": _f(t["after_avg_ret30"]),
            "after_vsSPY30": _f(t["after_avg_vs_spy30"]),
        } for t in sim["table"]]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

    if sim["reasons"]:
        for r in sim["reasons"]:
            st.caption("・" + r)
    st.info("これはシミュレーション表示のみで、本番ニューススコアには反映していません。")

    # 📌 v31: 改善時のみ「本番反映候補」として保存（本番非反映・status控えのみ）
    can_save = (sim["verdict"] == "改善" and sim["approved_count"] > 0
                and sim["applicable_news"] >= 3 and sim["eligible_n"] >= 10)
    if can_save:
        if st.button("📌 シミュレーション結果を本番反映候補に保存", key="nsim_save", width='stretch'):
            ok, msg = nadopt_mod.save_simulation_result(sim)
            (st.success if ok else st.warning)(msg + "（※本番スコアには反映しません）")
        st.caption("保存結果は下の「📋 ニュース採用候補」に simulation_result として表示されます。")

    # 🛡 v32: 本番反映前の最終ガード（表示のみ・本番非反映）
    _render_production_guard()


_GUARD_DECISION_COLOR = {"反映準備OK": "#16a34a", "まだ反映しない": "#64748b",
                         "データ不足": "#eab308", "悪化リスクあり": "#dc2626"}


def _render_production_guard():
    """v32: 本番反映前の最終ガード（判定・チェックリスト表示のみ・本番非反映）。"""
    st.divider()
    st.subheader("🛡 本番反映前チェック")
    g = nadopt_mod.production_guard()
    dc = _GUARD_DECISION_COLOR.get(g["decision"], "#64748b")
    st.markdown(f'判定：<b style="color:{dc};font-size:1.05rem;">{g["decision"]}</b>', unsafe_allow_html=True)

    st.markdown("**チェックリスト**")
    for c in g["checks"]:
        mark = "✅" if c["ok"] else "❌"
        st.markdown(f'{mark} {c["label"]}：{c["value"]}')

    if g["reasons"]:
        st.markdown("**理由**")
        for r in g["reasons"]:
            st.caption("・" + r)

    if g["next_actions"]:
        st.markdown("**次のアクション**")
        for a in g["next_actions"]:
            st.markdown(f"- {a}")

    st.info("これは最終ガード表示のみで、本番ニューススコアには反映していません。")

    # 📝 v33: 反映準備OK 時のみ設定ファイルを生成（生成のみ・news.py 未読込・本番非反映）
    _render_overrides_generator()


def _render_overrides_generator():
    """v33: 承認済み補正案から news_score_overrides.json を生成（生成のみ・本番非反映）。"""
    st.divider()
    st.subheader("📝 ニューススコア設定ファイル生成")
    cands = nadopt_mod.list_all()
    guard = nadopt_mod.production_guard(cands)
    approved_sc = [c for c in cands if c.get("type") == "score_correction" and c.get("status") == "approved"]
    has_sim = any(c.get("type") == "simulation_result" and c.get("status") == "approved" for c in cands)
    ok, reason = nadopt_mod.can_generate_overrides(cands)

    st.markdown(f'- guard 判定：**{guard["decision"]}**')
    st.markdown(f'- 承認済み score_correction：**{len(approved_sc)}** 件')
    st.markdown(f'- 承認済み simulation_result：**{"あり" if has_sim else "なし"}**')
    st.markdown(f'- 生成可否：**{"✅ 生成可能" if ok else "❌ 生成不可"}**（{reason}）')

    if ok:
        if st.button("📝 news_score_overrides.json を生成", key="nover_gen", width='stretch'):
            done, msg, cfg = nadopt_mod.generate_overrides_config(cands)
            if done:
                st.success(msg)
                st.markdown(f'- 保存先：`{nadopt_mod.OVERRIDES_PATH}`')
                st.markdown(f'- enabled：**{str(cfg["enabled"]).lower()}**（本番反映しません）')
                st.markdown(f'- impact_overrides：**{len(cfg["impact_overrides"])}** 件')
                st.markdown(f'- category_adjustments：**{len(cfg["category_adjustments"])}** 件')
                st.markdown(f'- sentiment_notes：**{len(cfg["sentiment_notes"])}** 件')
            else:
                st.warning(msg)
    else:
        st.caption("生成条件（反映準備OK＋承認済み score_correction 1件以上＋承認済み simulation_result(改善)）を満たすと生成ボタンが表示されます。")

    existing = nadopt_mod.load_overrides_config()
    if existing:
        st.caption(f'生成済み：{nadopt_mod.OVERRIDES_PATH}（generated_at {existing.get("generated_at","—")} ／ '
                   f'enabled={str(existing.get("enabled")).lower()} ／ impact {len(existing.get("impact_overrides",{}))} ／ '
                   f'category {len(existing.get("category_adjustments",{}))} ／ sentiment {len(existing.get("sentiment_notes",{}))}）')
    st.info("これは設定ファイルの生成のみで、news.py には読み込ませていません。"
            "**本番ニューススコア・発掘スコア・ランキングには一切反映していません（enabled=false）。**")

    # 🧾 v34: 生成済み overrides の安全プレビュー（読み取り表示のみ・本番非反映）
    _render_overrides_preview()
    # 🧩 v35: overrides 読み込み状態（読むだけ・未適用・本番非反映・状態に依らず常に表示）
    _render_overrides_status()


def _render_overrides_preview():
    """v34: news_score_overrides.json の安全プレビュー（読み取り表示のみ・ファイル非改変・本番非反映）。"""
    st.divider()
    st.subheader("🧾 ニューススコア設定プレビュー")
    p = nadopt_mod.preview_overrides_config()

    if p["status"] == "未生成":
        st.info("まだ news_score_overrides.json は生成されていません。")
        return
    if p["status"] == "読み込み不可":
        st.error("news_score_overrides.json を読み込めません（壊れている可能性があります）。")
        return

    # 基本情報
    st.markdown(f'- ファイル：**あり**（`{nadopt_mod.OVERRIDES_PATH}`）')
    st.markdown(f'- generated_at：{p["generated_at"] or "—"}')
    st.markdown(f'- enabled：**{str(p["enabled"]).lower()}**')
    st.markdown(f'- source：{p["source"] or "—"}')
    st.markdown(f'- guard_decision：{p["guard_decision"] or "—"}')

    # 安全ステータス
    if p["safe"]:
        st.success("✅ 未適用・安全。本番には反映されていません。")
    else:
        st.warning("⚠️ 注意：enabled=true です。ただし現時点で news.py は読んでいないため、まだ実反映はありません。")

    def _f(v):
        return (f"{v:+.3f}" if isinstance(v, float) else (f"{v:+d}" if isinstance(v, int) and not isinstance(v, bool) else "—"))

    # impact_overrides
    st.markdown(f'**impact_overrides（{p["counts"]["impact"]}件）**')
    if p["impact_rows"]:
        rows = [{"元score": r["from"], "新score": r["to"],
                 "delta": (f'{r["delta"]:+d}' if isinstance(r["delta"], int) else "—")} for r in p["impact_rows"]]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("なし")

    # category_adjustments
    st.markdown(f'**category_adjustments（{p["counts"]["category"]}件）**')
    if p["category_rows"]:
        rows = [{"category": r["category"],
                 "adjustment": (f'{r["adjustment"]:+d}' if isinstance(r["adjustment"], int) else r["adjustment"])}
                for r in p["category_rows"]]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("なし")

    # sentiment_notes
    st.markdown(f'**sentiment_notes（{p["counts"]["sentiment"]}件）**')
    if p["sentiment_rows"]:
        st.dataframe(pd.DataFrame([{"sentiment": r["sentiment"], "note": r["note"]} for r in p["sentiment_rows"]]),
                     width='stretch', hide_index=True)
    else:
        st.caption("なし")

    # simulation_summary
    ss = p["simulation_summary"] or {}
    st.markdown("**simulation_summary**")
    st.caption(
        f'eligible_n {ss.get("eligible_n","—")} / applicable_news {ss.get("applicable_news","—")} / '
        f'approved_correction_count {ss.get("approved_correction_count","—")} ｜ '
        f'corr_ret30Δ {_f(ss.get("corr_ret30_delta"))} / mono違反Δ {_f(ss.get("mono_violations_delta"))} / '
        f'high vsSPY30Δ {_f(ss.get("high_vs_spy30_delta"))}')

    st.info("この画面はプレビューのみです。本番ニューススコアには反映していません。")


def _render_overrides_status():
    """v35: news.py が overrides を読み込めるが未適用であることを表示（safety mode）。"""
    st.divider()
    st.subheader("🧩 News Overrides Status")
    # news.py 経由で「読むだけ」（適用しない）。スコアには一切影響しない。
    status = news_mod.load_overrides_safe()
    cfg = status.get("config") or {}
    imp_n = len(cfg.get("impact_overrides") or {})
    cat_n = len(cfg.get("category_adjustments") or {})
    sen_n = len(cfg.get("sentiment_notes") or {})

    st.markdown(f'- file exists：**{status.get("exists")}**')
    st.markdown(f'- enabled：**{str(status.get("enabled")).lower()}**')
    st.markdown(f'- status：**{status.get("status")}**')
    st.markdown(f'- impact override count：**{imp_n}**')
    st.markdown(f'- category adjustment count：**{cat_n}**')
    st.markdown(f'- sentiment note count：**{sen_n}**')

    s = status.get("status")
    if s == "missing":
        st.info("overridesファイルは存在しません。")
    elif s == "disabled":
        st.success("enabled=false のため未適用です。")
    else:  # enabled
        st.warning("enabled=true ですが v35 Safety Mode により未適用です。")

    st.info("v35 は overrides を読み込むだけです。**本番ニューススコア・発掘スコア・ランキングには一切反映していません。**")

    # 🧪 v36: overrides 仮適用の差分プレビュー（表示計算のみ・本番非反映）
    _render_overrides_apply_preview()


def _render_overrides_apply_preview():
    """v36: overrides を較正ニュースへ仮適用した差分プレビュー（表示のみ・JSON非改変・本番非反映）。"""
    st.divider()
    st.subheader("🧪 overrides適用差分プレビュー")
    p = nadopt_mod.preview_apply_overrides()

    if not p["available"]:
        st.info("設定ファイルが無いためプレビューできません。先に news_score_overrides.json を生成してください。")
        return
    if p["total"] == 0:
        st.info("impact_score を持つ較正ニュースがありません。")
        return

    # enabled 状態の案内
    if p["enabled"]:
        st.warning("enabled=true ですが、v36ではプレビューのみです。本番には反映されていません。")
    else:
        st.success("enabled=false ですが、プレビューでは仮適用結果を表示しています。本番には反映されていません。")

    def _f(v):
        return (f"{v:+.2f}" if isinstance(v, (int, float)) else "—")

    m = st.columns(5)
    m[0].metric("対象ニュース", p["total"])
    m[1].metric("変更される件数", p["changed"])
    m[2].metric("平均impact before", (f'{p["avg_before"]:.2f}' if p["avg_before"] is not None else "—"))
    m[3].metric("平均impact after", (f'{p["avg_after"]:.2f}' if p["avg_after"] is not None else "—"))
    m[4].metric("平均delta", _f(p["avg_delta"]))

    # 銘柄別 summary
    st.markdown("**銘柄別 summary**")
    st.dataframe(pd.DataFrame([{
        "ticker": b["ticker"], "news_count": b["news_count"], "changed": b["changed_count"],
        "avg_before": (f'{b["avg_before"]:.2f}' if b["avg_before"] is not None else "—"),
        "avg_after": (f'{b["avg_after"]:.2f}' if b["avg_after"] is not None else "—"),
        "avg_delta": _f(b["avg_delta"]),
    } for b in p["by_ticker"]]), width='stretch', hide_index=True)

    # ニュース別テーブル
    st.markdown("**ニュース別差分**")
    st.dataframe(pd.DataFrame([{
        "ticker": r["ticker"], "news_date": r["news_date"],
        "headline": (r["headline"] or "")[:40], "sentiment": r["sentiment"],
        "categories": "/".join(r["categories"] or []),
        "before": r["impact_before"], "after": r["impact_after"],
        "delta": f'{r["delta"]:+d}',
        "impact適用": ("✅" if r["impact_override_applied"] else ""),
        "category適用": ("✅" if r["category_adjustment_applied"] else ""),
        "sentiment_note": (r["sentiment_note"] or ""),
    } for r in p["rows"]]), width='stretch', hide_index=True)

    st.info("これは差分プレビューのみで、本番ニューススコアには反映していません。")

    # 🧪 v37: 実験モードでのみ overrides を仮適用した比較（表示のみ・本番非接続）
    _render_overrides_experiment()


def _render_overrides_experiment():
    """v37: 実験モード時のみ overrides を仮適用して original/experiment を比較（表示のみ・本番非接続）。"""
    st.divider()
    st.subheader("🧪 News Overrides Experiment")
    exp_on = st.checkbox("🧪 実験モードを有効化（このセッションのみ・本番非反映）", value=False, key="nover_exp_toggle")
    e = nadopt_mod.experiment_overrides_preview(experiment_enabled=exp_on)

    def _f(v):
        return (f"{v:+.2f}" if isinstance(v, (int, float)) else "—")

    def _a(v):
        return (f"{v:.2f}" if isinstance(v, (int, float)) else "—")

    st.markdown(f'- overrides file exists：**{e["exists"]}**')
    st.markdown(f'- overrides enabled：**{str(e["enabled"]).lower() if e["enabled"] is not None else "—"}**')
    st.markdown(f'- experiment flag：**{str(e["experiment_flag"]).lower()}**')
    st.markdown(f'- experiment applied：**{e["experiment_applied"]}**（{e["reason"]}）')

    if e["experiment_applied"]:
        st.warning("⚠️ 実験適用中：較正データ上での比較のみです。**通常の発掘スコア・ランキングには反映していません。**")
    else:
        st.info(f"未適用（{e['reason']}）。enabled=true かつ実験モードONの両方が必要です。")

    if e["total"] == 0:
        st.caption("impact_score を持つ較正ニュースがありません。")
        st.info("これは実験モード表示のみです。通常の発掘スコア・ランキングには反映していません。")
        return

    m = st.columns(5)
    m[0].metric("target news", e["total"])
    m[1].metric("changed", e["changed"])
    m[2].metric("avg original", _a(e["avg_original"]))
    m[3].metric("avg experiment", _a(e["avg_experiment"]))
    m[4].metric("avg delta", _f(e["avg_delta"]))

    st.markdown("**銘柄別**")
    st.dataframe(pd.DataFrame([{
        "ticker": b["ticker"], "original_avg": _a(b["original_avg"]),
        "experiment_avg": _a(b["experiment_avg"]), "delta": _f(b["delta"]),
    } for b in e["by_ticker"]]), width='stretch', hide_index=True)

    st.markdown("**ニュース別**")
    st.dataframe(pd.DataFrame([{
        "ticker": r["ticker"], "news_date": r["news_date"],
        "headline": (r["headline"] or "")[:40],
        "original": r["original_score"], "experiment": r["experiment_score"],
        "delta": f'{r["delta"]:+d}', "applied_reason": r["applied_reason"],
    } for r in e["rows"]]), width='stretch', hide_index=True)

    st.info("これは実験モード表示のみです。通常の発掘スコア・ランキングには反映していません。")


_NADOPT_STATUS_COLOR = {"candidate": "#64748b", "approved": "#16a34a", "rejected": "#dc2626"}


def _render_news_adoption():
    """v27.2: ニュース較正提案を検証候補として保存・承認・却下・削除（本番非反映）。"""
    st.divider()
    if st.button("📌 較正提案を検証候補に保存", key="nadopt_save", width='stretch'):
        total, created, updated = nadopt_mod.save_candidates()
        if total == 0:
            st.info("保存できる有効候補（n≥3）がありませんでした。")
        else:
            st.success(f"検証候補 {total} 件を保存しました（新規 {created} / 更新 {updated}）。"
                       "※本番スコアには反映しません。")

    cands = nadopt_mod.list_all()
    with st.expander(f"📋 ニュース採用候補（{len(cands)}件・検証用の控え／本番非反映）", expanded=bool(cands)):
        if not cands:
            st.caption("まだ候補はありません。上の「📌 較正提案を検証候補に保存」で保存できます。")
            return

        def _f(v):
            return (f"{v:+.1f}%" if isinstance(v, (int, float)) else "—")

        order = {"candidate": 0, "approved": 1, "rejected": 2}
        for c in sorted(cands, key=lambda x: (order.get(x.get("status"), 9), x.get("type", ""), x.get("label", ""))):
            ev = c.get("evidence", {}) or {}
            color = _NADOPT_STATUS_COLOR.get(c.get("status"), "#64748b")
            n_val = ev.get("n", ev.get("sample_size"))  # v29=n / v27.2=sample_size
            if c.get("type") == "production_apply_candidate":
                # v41: 本番反映候補（発掘ランキング実験結果の控え）
                summ = c.get("summary", {}) or {}
                st.markdown(
                    f'<div style="font-size:0.95rem;">'
                    f'<b>[production_apply_candidate]</b> {c.get("source")}'
                    f'　<b style="color:{color};">{c.get("status")}</b></div>',
                    unsafe_allow_html=True)
                st.caption(
                    f'対象 {c.get("target_count")} / score変化 {c.get("score_changed")} / '
                    f'順位変化 {c.get("rank_changed")} / IN {c.get("in_count")} / OUT {c.get("out_count")} ｜ '
                    f'guard {c.get("guard_decision")} / overrides_enabled {str(c.get("overrides_enabled")).lower()} ｜ '
                    f'top10変化 {summ.get("top10_changed")} / avgΔ {summ.get("avg_score_delta")} / '
                    f'最大上昇 {summ.get("max_rank_up")} / 最大下落 {summ.get("max_rank_down")} ｜ '
                    f'作成 {c.get("created_at","—")} ／ 更新 {c.get("updated_at","—")}')
            elif c.get("type") == "simulation_result":
                # v31: シミュレーション結果行（verdict/improvement/規模を表示）
                imp = c.get("improvement", {}) or {}

                def _d(v, suf=""):
                    return (f"{v:+.3f}" if isinstance(v, float) else (f"{v:+d}" if isinstance(v, int) else "—")) + (suf if v is not None and not isinstance(v, bool) else "")

                st.markdown(
                    f'<div style="font-size:0.95rem;">'
                    f'<b>[simulation_result]</b> {c.get("label")} — 判定 {c.get("verdict")}'
                    f'　<b style="color:{color};">{c.get("status")}</b></div>',
                    unsafe_allow_html=True)
                st.caption(
                    f'承認補正 {c.get("approved_correction_count")} / 適用 {c.get("applicable_news")} / '
                    f'eligible {c.get("eligible_n")} ｜ '
                    f'相関Δ {_d(imp.get("corr_ret30_delta"))} / 単調性違反Δ {_d(imp.get("mono_violations_delta"))} / '
                    f'high vsSPY30Δ {_d(imp.get("high_vs_spy30_delta"),"pt")} ｜ '
                    f'作成 {c.get("created_at","—")} ／ 更新 {c.get("updated_at","—")}')
                for rsn in (c.get("reasons") or [])[:6]:
                    st.caption("・" + rsn)
            elif c.get("type") == "score_correction":
                # v29: score_correction 行（target/推奨score/delta を表示）
                cs = c.get("current_score")
                rs = c.get("recommended_score")
                delta = c.get("delta")
                head = f'<b>[score_correction/{c.get("target")}]</b> {c.get("label")} — {c.get("suggestion")}'
                if cs is not None and rs is not None:
                    head += f'（{cs} → {rs}'
                    head += (f'・delta {delta:+d}）' if isinstance(delta, int) else '）')
                elif isinstance(delta, int):
                    head += f'（補正 {delta:+d}）'
                st.markdown(
                    f'<div style="font-size:0.95rem;">{head}'
                    f'　<b style="color:{color};">{c.get("status")}</b></div>',
                    unsafe_allow_html=True)
                if c.get("reason"):
                    st.caption("根拠: " + c["reason"])
                st.caption(
                    f'ret30 {_f(ev.get("avg_ret30"))} / vsSPY30 {_f(ev.get("avg_vs_spy30"))} / '
                    f'勝率 {ev.get("win_rate") if ev.get("win_rate") is not None else "—"}% / '
                    f'n={n_val} ｜ 信頼度 {c.get("confidence","—")} ｜ '
                    f'作成 {c.get("created_at","—")} ／ 更新 {c.get("updated_at","—")}')
            else:
                # v27.2: 既存候補（impact_adjustment / sentiment / category）
                cs = c.get("current_score")
                st.markdown(
                    f'<div style="font-size:0.95rem;">'
                    f'<b>[{c.get("type")}]</b> {c.get("label")} '
                    f'— {c.get("suggestion")}'
                    + (f'（現score {cs}）' if cs is not None else "")
                    + f'　<b style="color:{color};">{c.get("status")}</b></div>',
                    unsafe_allow_html=True)
                st.caption(
                    f'ret7 {_f(ev.get("avg_ret7"))} / ret30 {_f(ev.get("avg_ret30"))} / '
                    f'vsSPY30 {_f(ev.get("avg_vs_spy30"))} / '
                    f'勝率 {ev.get("win_rate") if ev.get("win_rate") is not None else "—"}% / '
                    f'n={n_val} ｜ 信頼度 {c.get("confidence","—")} ｜ '
                    f'作成 {c.get("created_at","—")} ／ 更新 {c.get("updated_at","—")}')
            b = st.columns(3)
            if b[0].button("✅ 承認", key=f'nadopt_app_{c["id"]}', width='stretch'):
                nadopt_mod.approve(c["id"]); st.rerun()
            if b[1].button("🚫 却下", key=f'nadopt_rej_{c["id"]}', width='stretch'):
                nadopt_mod.reject(c["id"]); st.rerun()
            if b[2].button("🗑 削除", key=f'nadopt_del_{c["id"]}', width='stretch'):
                nadopt_mod.remove(c["id"]); st.rerun()
            st.markdown("---")
        st.caption("※承認/却下は status を変えるだけの控えです。"
                   "**ニューススコア・発掘スコア・ランキングには一切反映しません。**")


def _render_production_overrides_status():
    """v42: 本番反映の全ゲートの状態を表示（判定のみ・apply 関数なし・本番非反映）。"""
    st.divider()
    st.subheader("🔐 本番反映ステータス")
    s = nadopt_mod.get_production_overrides_status()

    allow = s["allow"]
    color = "#16a34a" if allow else "#dc2626"
    st.markdown(f'allow：<b style="color:{color};font-size:1.05rem;">{allow}</b>', unsafe_allow_html=True)

    st.markdown("**ゲート判定**")
    for g in s["gates"]:
        mark = "✅" if g["ok"] else "❌"
        st.markdown(f'{mark} {g["label"]}：{g["value"]}')

    # 🔑 v43: fingerprint 整合性
    def _short(fp):
        return (fp[:12] if isinstance(fp, str) and fp else "—")
    cur = s.get("current_fingerprint")
    cand = s.get("candidate_fingerprint")
    match = s.get("fingerprint_match")
    st.markdown("**fingerprint 整合性**")
    st.markdown(f'- current overrides fingerprint：`{_short(cur)}`')
    st.markdown(f'- candidate fingerprint：`{_short(cand)}`')
    st.markdown(f'- fingerprint match：**{match}**')
    st.markdown(f'- consistency gate：**{"✅ OK" if s["summary"]["consistency_ok"] else "❌ NG"}**')
    if not cand:
        st.caption("※承認済み候補に fingerprint が未保存です。v43以降に候補を再保存してください。")

    if s["reasons"]:
        st.markdown("**ブロック理由**")
        for r in s["reasons"]:
            st.caption("・" + r)

    st.info("これは状態表示のみです。"
            "**本番ニューススコア・発掘スコア・ランキングには一切反映していません。**")

    # 🧯 v44: Apply関数の安全テスト（サンプルのみ・本番値は使わない・絶対未適用）
    _render_apply_safety_test()


def _render_apply_safety_test():
    """v44: apply_news_overrides_if_allowed() の安全挙動をサンプルで検証表示（本番非接続）。"""
    st.divider()
    st.subheader("🧯 Apply関数安全テスト")
    # 表示用サンプル（本番 news_sum は使わない）。ネスト(categories/reasons)を含める
    sample = {"avg_impact": 2.0, "score": 14.0,
              "categories": {"チャンス": 2, "危険": 1},
              "reasons": ["平均インパクト +2.0（-5〜+5）"]}
    res = nadopt_mod.apply_news_overrides_if_allowed(sample)
    out = res["news_sum"]

    returned_same_as_input = (out == sample)
    returned_is_deepcopy = (out is not sample
                            and out.get("categories") is not sample["categories"]
                            and out.get("reasons") is not sample["reasons"])

    st.markdown(f'- apply関数あり：**{hasattr(nadopt_mod, "apply_news_overrides_if_allowed")}**')
    st.markdown(f'- status.allow：**{res["status"]["allow"]}**')
    st.markdown(f'- applied：**{res["applied"]}**')
    st.markdown(f'- reason：{res["reason"]}')
    st.markdown(f'- returned_same_as_input：**{returned_same_as_input}**')
    st.markdown(f'- returned_is_deepcopy：**{returned_is_deepcopy}**')
    st.info("v44では apply 関数は足場のみで、applied は常に False です。"
            "**本番ニューススコア・発掘スコア・ランキングには一切反映していません。**")


# ============================================================ 個別銘柄分析
def render_stock(rmap, watch=None, positions=None):
    st.header("🔎 個別銘柄分析")
    # v15.1: ウォッチ∪保有を対象に。失敗理由・空案内は共通ゲートで一元化
    t = analysis_gate(rmap, watch, positions)
    if t is None:
        return
    valid, tag = t["valid"], t["tag"]
    sel = st.selectbox("銘柄を選択", [r["ticker"] for r in valid],
                       format_func=lambda tk: f"{tk}　{tag.get(tk, '')}")
    r = next(x for x in valid if x["ticker"] == sel)
    f, s, snap, p, a = r["fund"], r["scores"], r["snap"], r["plan"], r["action"]

    st.markdown(f"### {f.get('name', sel)} ({sel})")
    st.markdown(_action_chip(a) + f"　{ui.verdict_badge(s['verdict'])}　総合 **{s['total']}/100**", unsafe_allow_html=True)
    watchlist_toggle_button(sel, key_prefix="stockpage")
    st.info("📌 今日やること: " + a["today"])

    top = st.columns(4)
    top[0].metric("現在値", ui.fmt_money(f["price"]))
    top[1].metric("目標株価", ui.fmt_money(f.get("target")))
    up = (f.get("target", f["price"]) / f["price"] - 1) * 100 if f.get("price") else 0
    top[2].metric("上値余地", f"{up:+.1f}%")
    top[3].metric("決算日", f.get("earnings", "未定"))

    # v15.2: 価格ソースの明示
    psrc = r.get("price_source")
    st.caption("価格ソース：" + data_fetch_mod.price_source_label(psrc or "—"))
    if psrc == "unavailable" or not f.get("price"):
        st.warning("⚠️ 現在値を取得できませんでした（yfinance / Finnhub / Stooq すべて失敗）。"
                   + (f"詳細: {r.get('price_error')}" if r.get("price_error") else "")
                   + " 表示中の数値は参考値です。")

    if r.get("source") == "mock" or f.get("is_sample"):
        st.caption("ℹ️ サンプル(モック)データ表示中。yfinance取得時は実データになります。")

    fig = ui.build_price_chart(r["df"], sel)
    if fig is not None:
        st.plotly_chart(fig, width='stretch')
    else:
        st.line_chart(r["df"]["Close"]); st.caption("（plotly未導入のため簡易表示）")

    _render_scenario_forecast(r, sel, f, psrc, positions)
    _render_sec_edgar(sel, f)
    _render_news_calibration(sel)
    _render_news_calibration_analytics()

    st.subheader("🧮 スコアの理由（なぜこの点数か）")
    W = config.SCORE_WEIGHTS
    labels = [("ファンダメンタル", "fundamental"), ("テクニカル", "technical"),
              ("ニュース", "news"), ("市場環境", "market"), ("需給", "supply_demand")]
    for name, key in labels:
        with st.expander(f"{name} {s[key]:.0f}/{W[key]}", expanded=(key in ("fundamental", "technical", "news"))):
            for reason in s["reasons"].get(key, []):
                st.write("・" + reason)

    st.subheader("📊 ファンダメンタル")
    fc = st.columns(4)
    fc[0].metric("時価総額", ui.fmt_big(f.get("mcap")))
    fc[1].metric("PER", f'{f.get("pe"):.1f}' if f.get("pe") else "—")
    fc[2].metric("PSR", f'{f.get("ps"):.1f}' if f.get("ps") else "—")
    fc[3].metric("利益率", ui.fmt_pct(f.get("margin")))
    fc[0].metric("EPS成長", ui.fmt_pct(f.get("eps_g")))
    fc[1].metric("売上成長", ui.fmt_pct(f.get("rev_g")))
    rsi = snap.get("rsi", 50)
    fc[2].metric("RSI", f"{rsi:.0f}"); fc[2].caption(ui.rsi_comment(rsi))
    fc[3].metric("200日線", "上 ✅" if snap.get("above_sma200") else "下 ⚠️")
    if r["avoid_reasons"]:
        st.warning("注意点: " + " / ".join(r["avoid_reasons"]))


# ============================================================ ニュース分析
def render_news(rmap, watch=None, positions=None):
    st.header("📰 ニュース分析（実戦向け分類）")
    status = news_mod.news_status()
    if status["fully_ready"]:
        st.success(f"✅ フル機能: Finnhub（実ニュース） + AI分類（{'OpenAI' if status['openai'] else 'Anthropic'}）")
    else:
        miss = "・".join(status["missing"]) or "APIキー"
        st.warning(f"⚠️ v4はニュース/AIを重視します。未設定: **{miss}**。"
                   "今は" + ("実ニュース" if status["finnhub"] else "サンプル見出し")
                   + "＋" + ("AI分類" if status["ai_ready"] else "簡易キーワード分類")
                   + "で代替中。`.env`にキーを設定すると精度とスコアの信頼性が上がります。")

    # v15.1: ウォッチ∪保有を対象に
    t = analysis_gate(rmap, watch, positions)
    if t is None:
        return
    valid, tag = t["valid"], t["tag"]
    sel = st.selectbox("銘柄を選択", [r["ticker"] for r in valid],
                       format_func=lambda tk: f"{tk}　{tag.get(tk, '')}")
    r = next(x for x in valid if x["ticker"] == sel)
    news, summ = r["news"], r["news_sum"]

    c = st.columns(5)
    cats = summ.get("categories", {})
    c[0].metric("⚡ すぐ効く", cats.get("すぐ効く", 0))
    c[1].metric("🕒 1〜3ヶ月", cats.get("1〜3ヶ月後", 0))
    c[2].metric("🔍 未織込み", cats.get("未織り込み", 0))
    c[3].metric("✅ チャンス", cats.get("チャンス", 0))
    c[4].metric("🚨 危険", cats.get("危険", 0))
    st.metric("ニュース総合インパクト（-5〜+5の平均）", f'{summ.get("avg_impact",0):+.1f}', f'スコア {summ["score"]}/20')

    # 🔐 v45: 本番反映ゲート接続済み（switch OFF のため未適用＝スコア不変）の明示。news_sum 非依存で導出。
    _prod_allow = nadopt_mod.get_production_overrides_status()["allow"]
    st.caption(f"Production override applied: {_prod_allow}（switch OFF・本番ニューススコアは未変更）")

    if news.get("key_missing"):
        st.caption("※下記はサンプル見出しです（APIキー未設定）。")

    cat_color = {"すぐ効く": "#0ea5e9", "1〜3ヶ月後": "#8b5cf6", "未織り込み": "#f59e0b",
                 "チャンス": "#16a34a", "危険": "#dc2626", "中立": "#64748b"}
    lean_icon = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}
    for it in news["items"]:
        cat = it.get("category", "中立"); col = cat_color.get(cat, "#64748b")
        imp = it.get("impact", 0)
        with st.container(border=True):
            st.markdown(
                f'<span style="background:{col};color:white;padding:2px 8px;border-radius:6px;font-size:0.8rem;">{cat}</span>'
                f'　<b>影響度 {imp:+d}</b>　{lean_icon.get(it.get("lean"),"⚪")}　{it.get("headline","")}',
                unsafe_allow_html=True)
            meta = [x for x in [it.get("short_impact"), it.get("mid_impact"),
                                (f'出所: {it["source"]}' if it.get("source") else "")] if x]
            if meta:
                st.caption("　/　".join(meta))

    # 🧪 v38: 実験モードでのみ overrides を仮適用（この銘柄のニュースだけ・本番非反映）
    _render_news_experiment(sel, news, r)


def _render_news_experiment(ticker, news, r=None):
    """v38: 個別銘柄のライブニュースに実験モードで overrides を仮適用（表示のみ・本番非接続）。
    通常の news_sum / avg_impact / verdict / score は変更しない。"""
    st.divider()
    st.subheader("🧪 ニュース実験モード分析")
    exp_on = st.checkbox("🧪 実験モードを有効化（このセッションのみ・本番非反映）",
                         value=False, key=f"news_exp_{ticker}")
    items = (news or {}).get("items", []) or []
    e = nadopt_mod.experiment_news_items(items, experiment_enabled=exp_on)

    def _a(v):
        return (f"{v:.2f}" if isinstance(v, (int, float)) else "—")

    def _f(v):
        return (f"{v:+.2f}" if isinstance(v, (int, float)) else "—")

    st.markdown(f'- overrides enabled：**{str(e["enabled"]).lower() if e["enabled"] is not None else "—"}**'
                f'　／　experiment flag：**{str(e["experiment_flag"]).lower()}**'
                f'　／　experiment applied：**{e["experiment_applied"]}**（{e["reason"]}）')

    if e["experiment_applied"]:
        st.warning("⚠️ 実験適用中：このページのニュース比較のみです。**通常の発掘スコア・ランキングには反映していません。**")
    else:
        st.info(f"未適用（{e['reason']}）。overrides enabled=true かつ実験モードONの両方が必要です。")

    if e["total"] == 0:
        st.caption("impact を持つニュースがありません。")
        st.info("これは個別銘柄ニュース分析の実験表示のみです。発掘スコア・ランキングには反映していません。")
        return

    m = st.columns(4)
    m[0].metric("対象ニュース数", e["total"])
    m[1].metric("補正された件数", e["changed"])
    m[2].metric("original avg impact", _a(e["avg_original"]))
    m[3].metric("experiment avg impact", _a(e["avg_experiment"]))
    st.caption(f'平均delta {_f(e["avg_delta"])} ｜ '
               f'sentiment（note のみ・スコア不変）original={e["original_sentiment"]} / experiment={e["experiment_sentiment"]}')

    st.markdown("**ニュース別**")
    st.dataframe(pd.DataFrame([{
        "headline": (r["headline"] or "")[:44], "source": r["source"],
        "original": r["original_impact"], "experiment": r["experiment_impact"],
        "delta": f'{r["delta"]:+d}',
        "category適用": ("✅" if r["category_adjustment_applied"] else ""),
        "impact適用": ("✅" if r["impact_override_applied"] else ""),
        "sentiment_note": (r["sentiment_note"] or ""),
    } for r in e["rows"]]), width='stretch', hide_index=True)

    st.info("これは個別銘柄ニュース分析の実験表示のみです。発掘スコア・ランキングには反映していません。")

    # 🧪 v39: 実験ニュース影響を個別銘柄スコアに仮反映して比較（表示のみ・本番非接続）
    _render_news_experiment_score(e, r)


def _render_news_experiment_score(exp, r):
    """v39: experiment avg_impact を news component に仮反映した total/verdict/action 比較（表示のみ）。"""
    st.divider()
    st.subheader("🧪 実験ニュース反映スコア比較")
    if not exp.get("experiment_applied") or exp.get("total", 0) == 0 or r is None:
        if not exp.get("experiment_applied"):
            st.info("overrides未適用のため未計算（実験モードONかつ overrides enabled=true が必要です）。")
        else:
            st.info("対象ニュースが無いため未計算です。")
        return

    s = nadopt_mod.experiment_individual_score(
        r.get("scores", {}), r.get("news_sum", {}), r.get("snap", {}),
        r.get("fund", {}), r.get("plan", {}), exp.get("avg_experiment"), market_mode="中立")

    m = st.columns(3)
    m[0].metric("total score", f'{s["experiment_total"]:.1f}', f'{s["score_delta"]:+.1f}（元 {s["original_total"]:.1f}）')
    m[1].metric("verdict", s["experiment_verdict"],
                (f'← {s["original_verdict"]}' if s["verdict_change"] else "変化なし"), delta_color="off")
    m[2].metric("action", s["experiment_action"] or "—",
                (f'← {s["original_action"]}' if s["action_change"] else "変化なし"), delta_color="off")

    st.markdown(
        f'- news component：**{s["news_before"]:.1f} → {s["news_after"]:.1f}**（delta {s["news_delta"]:+.1f}）\n'
        f'- verdict change：**{s["verdict_change"]}**（{s["original_verdict"]} → {s["experiment_verdict"]}）\n'
        f'- action change：**{s["action_change"]}**（{s["original_action"]} → {s["experiment_action"]}）')
    st.caption("ℹ️ " + s["note"] + "（fundamental / technical / market / supply_demand は同一）")
    st.info("これはスコア仮反映の表示のみです。本番スコア・発掘スコア・ランキングには反映していません。")


# ============================================================ 売買プラン
def render_plan(rmap, watch=None, positions=None):
    st.header("📋 売買プラン（3段エントリー）")
    st.error("⛔ 損切り(逆指値)なしの買いは禁止。利確1で半分売り、残りを伸ばすのが基本です。")
    # v15.1: ウォッチ∪保有を対象に
    t = analysis_gate(rmap, watch, positions)
    if t is None:
        return
    valid, tag = t["valid"], t["tag"]
    for r in valid:
        f, p, s, a = r["fund"], r["plan"], r["scores"], r["action"]
        if "error" in p:
            continue
        header = f'{r["ticker"]} {tag.get(r["ticker"], "")} — {a["emoji"]}{a["action"]}・{s["verdict"]}・{s["total"]}点'
        with st.expander(header, expanded=False):
            st.info("📌 " + a["today"])
            st.markdown("**エントリー（3分割）**")
            e = st.columns(4)
            e[0].metric("第1（打診）", f'${p["entry1"]}')
            e[1].metric("第2（-5%）", f'${p["entry2"]}')
            e[2].metric("第3（深押し）", f'${p["entry3"]}')
            e[3].metric("平均取得目安", f'${p["avg_entry"]}')
            st.markdown("**手仕舞い**")
            x = st.columns(5)
            x[0].metric("損切り", f'${p["stop"]}', f'-{p["stop_pct"]}%')
            x[1].metric("利確1(半分売り)", f'${p["tp1"]}')
            x[2].metric("利確2", f'${p["tp2"]}')
            x[3].metric("利確3(全部売り)", f'${p["tp3"]}')
            x[4].metric("全撤退ライン", f'${p["full_exit"]}')
            st.write(f'**半分売る所**: 利確1 ${p["half_sell"]}　|　**全撤退**: 損切り ${p["full_exit"]} 到達　|　**保有**: {p["hold"]}')
            warn = []
            if p["earnings_reduce"]:
                warn.append(f'決算前にポジション **{p["earnings_reduce"]}%** 減らす（決算 {p["earnings"]}）')
            if p["fomc_reduce"]:
                warn.append(f'FOMC前にポジション **{p["fomc_reduce"]}%** 減らす')
            if warn:
                st.warning("⚠️ イベント対応: " + " ／ ".join(warn))
            if r["avoid_reasons"]:
                st.caption("注意点: " + " / ".join(r["avoid_reasons"]))
            st.caption("👶 " + p["comment"])


# ============================================================ リスク管理
def render_risk(rmap, watch, positions, regime, capital, risk_pct, max_pos):
    st.header("🛡️ リスク管理（資金配分）")
    st.caption("総資金から、ルールに沿った現金比率・株数・損益を計算します。")

    rec_cash = capital * regime["cash_pct"] / 100
    head = st.columns(4)
    head[0].metric("総資金", f"${capital:,.0f}")
    head[1].metric("推奨現金比率", f'{regime["cash_pct"]}%', f'${rec_cash:,.0f}')
    head[2].metric("1銘柄 最大投入", f'${capital*max_pos/100:,.0f}', f'{max_pos:.0f}%')
    head[3].metric("1銘柄 最大損失", f'${capital*risk_pct/100:,.0f}', f'{risk_pct:.1f}%')
    st.caption(f"相場モード「{regime['regime']}」では新規買い {regime['new_buy_pct']}% / 現金 {regime['cash_pct']}% が目安。"
               "損切りなしの買いは禁止、決算/FOMC前はサイズを落とす。")

    # v15.1: ウォッチ∪保有を対象に
    t = analysis_gate(rmap, watch, positions)
    if t is None:
        return
    valid, tag = t["valid"], t["tag"]
    rows, sized = [], []
    for r in valid:
        p = r["plan"]
        if "error" in p:
            continue
        sz = risk_mod.position_sizing(capital, p["entry1"], p["stop"], p["tp1"], p["tp2"], risk_pct, max_pos)
        sized.append(sz)
        rows.append({
            "ティッカー": r["ticker"], "区分": tag.get(r["ticker"], ""),
            "アクション": r["action"]["action"],
            "買える株数": sz.get("shares", 0),
            "1回目": sz.get("shares_1", 0), "2回目": sz.get("shares_2", 0), "3回目": sz.get("shares_3", 0),
            "投入額": sz.get("position_value", 0), "比率%": sz.get("position_pct", 0),
            "損切り時 損失": -sz.get("max_loss", 0) if sz.get("max_loss") else 0,
            "利確1 利益": sz.get("profit_tp1", 0) or 0,
            "利確2 利益": sz.get("profit_tp2", 0) or 0,
        })
    if not rows:
        st.warning("プラン計算可能なデータがありません（プランエラー）。"); return

    df = pd.DataFrame(rows)
    st.dataframe(
        df.style
        .map(lambda v: f'background-color:{config.ACTIONS.get(v,{}).get("color","#64748b")};color:white;', subset=["アクション"])
        .format({"投入額": "${:,.0f}", "比率%": "{:.1f}%", "損切り時 損失": "${:,.0f}",
                 "利確1 利益": "${:,.0f}", "利確2 利益": "${:,.0f}"}),
        width='stretch', height=430)

    port = risk_mod.portfolio_plan(capital, regime, sized)
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("合計投入額", f'${port["total_invest"]:,.0f}')
    p2.metric("推奨投資枠", f'${port["invest_budget"]:,.0f}', f'現金{regime["cash_pct"]}%控除後')
    p3.metric("合計の最大損失", f'${port["total_max_loss"]:,.0f}')
    p4.metric("残し現金(目安)", f'${max(0, capital-port["total_invest"]):,.0f}')
    if port["over_budget"]:
        st.error("⚠️ 推奨投資枠を超えています。全部は買わず、アクションが『今すぐ買い』の上位に絞ってください。")
    st.success("👶 一度に全額入れない。上位2〜3銘柄に絞り、各銘柄も3回に分割。決算/FOMC前は半分以下に。")


# ============================================================ バックテスト
def render_backtest(rmap, watch=None, positions=None):
    st.header("🧪 簡易バックテスト")
    st.caption("過去約1年で『200日線より上＋RSI押し目→反発』で買い、損切り-8%/利確+20%/50日線割れで売った場合の概算。")
    st.info("ルール検証の目安です。手数料・スリッページ未考慮、過去成績は将来を保証しません。")

    # v15.1: ウォッチ∪保有を対象に
    t = analysis_gate(rmap, watch, positions)
    if t is None:
        return
    valid, tag = t["valid"], t["tag"]
    bts = bt_mod.backtest_watchlist(valid)
    rows = [b for b in bts if b.get("ok")]
    if not rows:
        st.warning("バックテスト可能なデータがありません（期間不足）。"); return

    df = pd.DataFrame([{
        "ティッカー": b["ticker"], "区分": tag.get(b["ticker"], ""),
        "取引回数": b["trades"], "勝率%": b["win_rate"],
        "平均利益%": b["avg_win"], "平均損失%": b["avg_loss"], "最大DD%": b["max_dd"],
        "ルール戦略%": b["strategy_return"], "買い放置%": b["buyhold_return"],
        "差(戦略-放置)": round(b["strategy_return"] - b["buyhold_return"], 1),
    } for b in rows])

    st.dataframe(
        df.style.format({"勝率%": "{:.0f}%", "平均利益%": "{:+.1f}%", "平均損失%": "{:+.1f}%",
                         "最大DD%": "{:.1f}%", "ルール戦略%": "{:+.1f}%", "買い放置%": "{:+.1f}%",
                         "差(戦略-放置)": "{:+.1f}%"})
        .background_gradient(subset=["差(戦略-放置)"], cmap="RdYlGn", vmin=-30, vmax=30),
        width='stretch', height=420)

    avg_win = df["勝率%"].mean()
    beat = (df["差(戦略-放置)"] > 0).sum()
    m = st.columns(3)
    m[0].metric("平均勝率", f"{avg_win:.0f}%")
    m[1].metric("放置に勝った銘柄数", f'{beat}/{len(df)}')
    m[2].metric("戦略の平均リターン", f'{df["ルール戦略%"].mean():+.1f}%')
    st.caption("💡 『損切りルール』は大きく下げた銘柄で“放置”より傷を浅くできることが多い、という点に注目。")


# ================================================================== v3
from modules import portfolio as pf_mod
from modules import holding_action as ha_mod
from modules import alerts as alerts_mod
from modules import order_memo as memo_mod
from modules import history as hist_mod
from modules import reflection as refl_mod


def _hold_chip(hd: dict) -> str:
    return (f'<span style="background:{hd["color"]}22;border:1px solid {hd["color"]};'
            f'color:{hd["color"]};padding:3px 10px;border-radius:999px;font-weight:700;">'
            f'{hd["emoji"]} {hd["action"]}</span>')


# ---------------------------------------------------------------- ポジション台帳
def render_positions(results_by_ticker, positions, regime, capital):
    st.header("📒 ポジション台帳（保有管理）")
    st.caption("保有銘柄を入力・保存。現在値/含み損益/保有日数/今日やること を自動表示。新規ティッカーは保存→再取得で分析されます。")

    base = pd.DataFrame(positions) if positions else pd.DataFrame(columns=pf_mod.POSITION_FIELDS)
    for c in pf_mod.POSITION_FIELDS:
        if c not in base.columns:
            base[c] = None
    base = base[pf_mod.POSITION_FIELDS]

    st.markdown("**保有を編集（行の追加・削除OK）**")
    edited = st.data_editor(
        base, num_rows="dynamic", width='stretch', key="pos_editor",
        column_config={
            "ticker": st.column_config.TextColumn("ティッカー"),
            "avg_cost": st.column_config.NumberColumn("平均取得単価", format="%.2f"),
            "shares": st.column_config.NumberColumn("株数", format="%.0f"),
            "buy_date": st.column_config.TextColumn("購入日(YYYY-MM-DD)"),
            "memo": st.column_config.TextColumn("メモ"),
            "purpose": st.column_config.TextColumn("投資目的"),
            "init_stop": st.column_config.NumberColumn("初期損切り", format="%.2f"),
            "init_tp1": st.column_config.NumberColumn("利確1", format="%.2f"),
            "init_tp2": st.column_config.NumberColumn("利確2", format="%.2f"),
        })
    if st.button("💾 ポジションを保存", width='stretch'):
        ok = pf_mod.save_positions(edited.to_dict("records"))
        if ok:
            st.success("保存しました（次回起動時に自動読込）。")
            st.rerun()
        else:
            st.error("保存に失敗しました。")

    recs = [r for r in edited.to_dict("records") if str(r.get("ticker", "") or "").strip()]
    if not recs:
        st.info("上の表に保有銘柄を入力してください。"); return

    st.divider()
    st.subheader("保有の評価 ＆ 今日やること")
    evals = []
    for r in recs:
        p = pf_mod._normalize(r)
        ev = pf_mod.evaluate_position(p, results_by_ticker.get(p["ticker"]))
        evals.append(ev)
        hd = ha_mod.decide(ev, regime["regime"])
        ts = ha_mod.trailing_stop_suggestions(ev)
        pl_color = "#16a34a" if ev["pl"] >= 0 else "#dc2626"
        with st.container(border=True):
            st.markdown(f'### {ev["ticker"]}　' + _hold_chip(hd), unsafe_allow_html=True)
            m = st.columns(5)
            m[0].metric("現在値", f'${ev["current"]:.2f}')
            m[1].metric("評価額", f'${ev["value"]:,.0f}')
            m[2].markdown(f'<span style="color:{pl_color};font-weight:700;font-size:1.1rem;">'
                          f'含み損益<br>${ev["pl"]:,.0f} ({ev["pl_pct"]:+.1f}%)</span>', unsafe_allow_html=True)
            m[3].metric("保有日数", f'{ev["days_held"]}日' if ev["days_held"] != "—" else "—")
            verdict = ev["analysis"]["scores"]["verdict"] if ev["analysis"] and ev["analysis"].get("ok") else "—"
            m[4].metric("分析判定", verdict)
            st.info("📌 今日やること: " + hd["reason"])
            if ts.get("recommended"):
                st.write(f'**次のアクション**: 逆指値を **${ts["recommended"]["price"]}**（{ts["recommended"]["label"]}）へ引き上げ検討。{ts["note"]}')
            if ev["themes"]:
                st.caption("テーマ: " + " / ".join(ev["themes"])
                           + (f'　|　メモ: {ev["memo"]}' if ev.get("memo") else ""))
            if not ev["has_stop"]:
                st.warning("⛔ 初期損切りが未設定です。必ず損切り価格を決めてください。")


# ---------------------------------------------------------------- ポートフォリオリスク
def render_portfolio_risk(results_by_ticker, positions, regime, capital):
    st.header("🛡️ ポートフォリオ全体リスク")
    if not positions:
        st.info("ポジション台帳に保有を登録すると、全体リスクを表示します。"); return

    evals = [pf_mod.evaluate_position(pf_mod._normalize(p), results_by_ticker.get(p["ticker"].upper()))
             for p in positions]
    summ = pf_mod.portfolio_summary(evals, capital)

    pl_color = "#16a34a" if summ["total_pl"] >= 0 else "#dc2626"
    c = st.columns(4)
    c[0].metric("総評価額", f'${summ["total_value"]:,.0f}')
    c[1].markdown(f'<span style="color:{pl_color};font-weight:700;">総含み損益<br>'
                  f'${summ["total_pl"]:,.0f} ({summ["total_pl_pct"]:+.1f}%)</span>', unsafe_allow_html=True)
    c[2].metric("現金比率", f'{summ["cash_pct"]:.0f}%', f'${summ["cash"]:,.0f}')
    c[3].metric("最大集中銘柄", f'{summ["top_ticker"] or "—"}', f'{summ["top_pct"]:.0f}%')

    # 危険判定
    st.subheader("⚠️ 危険判定")
    if summ["warnings"]:
        for w in summ["warnings"]:
            (st.error if w["level"] == "danger" else st.warning)(w["msg"])
    else:
        st.success("大きな偏りは見られません。バランスは概ね良好です。")

    # テーマ比率
    st.subheader("テーマ別比率（保有評価額ベース・重複あり）")
    tp = summ["theme_pct"]
    tcols = st.columns(len(tp) if tp else 1)
    for col, (name, v) in zip(tcols, tp.items()):
        col.metric(name, f'{v:.0f}%')
    st.bar_chart(pd.DataFrame({"比率%": tp}))
    st.caption("※1銘柄が複数テーマに属するため合計は100%を超えることがあります。")

    # セクター比率
    st.subheader("セクター別比率（代表テーマ）")
    st.bar_chart(pd.DataFrame({"比率%": summ["sector_pct"]}))

    st.caption("判定基準: 1銘柄≥25%=集中 / AI・半導体≥60%=テーマ集中 / 決算14日内が複数=イベント / 現金<10%=逃げ場なし / 損切り未設定=要対応")


# ---------------------------------------------------------------- アラート
def render_alerts(results_by_ticker, positions, regime):
    st.header("🔔 アラート")
    st.caption("価格・イベント・ニュースの注意点を自動抽出。🔴危険 → 🟠警告 → 🔵情報 の順。")
    al = alerts_mod.collect_all(results_by_ticker, positions, regime)
    if not al:
        st.success("現在、目立ったアラートはありません。"); return

    danger = [a for a in al if a["level"] == "danger"]
    warn = [a for a in al if a["level"] == "warn"]
    info = [a for a in al if a["level"] == "info"]
    cc = st.columns(3)
    cc[0].metric("🔴 危険", len(danger)); cc[1].metric("🟠 警告", len(warn)); cc[2].metric("🔵 情報", len(info))

    typ = {"price": "💹価格", "event": "🗓️イベント", "news": "📰ニュース"}
    for a in danger:
        st.error(f'[{typ.get(a["type"],a["type"])}] {a["msg"]}')
    for a in warn:
        st.warning(f'[{typ.get(a["type"],a["type"])}] {a["msg"]}')
    with st.expander(f"🔵 情報アラート（{len(info)}件）", expanded=False):
        for a in info:
            st.write(f'・[{typ.get(a["type"],a["type"])}] {a["msg"]}')


# ---------------------------------------------------------------- 発注メモ
def _render_goal_orders():
    """v16: 目標達成プランで保存した買い候補（保有/ウォッチが無くても表示）。"""
    orders = (gp_mod.load_goal() or {}).get("orders") or []
    if orders:
        st.subheader("🎯 目標達成プランの買い候補（保存済み）")
        st.caption("『🎯 目標達成プラン』で保存したバランス案の買い候補・株数・損切り・利確です。最終発注はMoomooで確認。")
        lines = [f"{o['ticker']}（{o['role']}）: {o['shares']}株 @ ${o['entry']} / "
                 f"損切り ${o['stop']} / 利確1 ${o['tp1']} / 利確2 ${o['tp2']}" for o in orders]
        st.code("\n".join(lines), language="text")


def render_order_memo(results_by_ticker, positions, regime):
    st.header("📝 Moomoo 手動発注メモ")
    st.caption("コピーしてMoomooの発注時メモに。買い注文メモと、保有中の売り/管理メモを生成します。")
    _render_goal_orders()  # 目標プラン由来の発注メモ（銘柄未登録でも表示）
    tickers = sorted(set(list(results_by_ticker.keys()) + [p["ticker"].upper() for p in positions]))
    if not tickers:
        st.info("ウォッチリストか保有を登録すると、個別の買い/保有メモも表示します。"); return
    sel = st.selectbox("銘柄を選択", tickers)
    a = results_by_ticker.get(sel)

    st.subheader("🛒 新規買いメモ")
    st.code(memo_mod.buy_memo(sel, a, regime), language="text")

    held = [p for p in positions if p["ticker"].upper() == sel]
    if held:
        st.subheader("📦 保有中メモ")
        ev = pf_mod.evaluate_position(pf_mod._normalize(held[0]), a)
        hd = ha_mod.decide(ev, regime["regime"])
        ts = ha_mod.trailing_stop_suggestions(ev)
        todo = ha_mod.moomoo_todo(ev, hd)
        st.code(memo_mod.hold_memo(ev, hd, ts, todo), language="text")
    else:
        st.caption("この銘柄は保有登録がありません（保有中メモは台帳に登録すると表示）。")

    # ===== v15: 保有ニュース監視からの自動注文メモ =====
    if held:
        m = hm_mod.monitor_position(pf_mod._normalize(held[0]), a, regime)
        st.subheader("🗓 自動提案（Moomooで設定する注文）")
        st.caption("保有ニュース監視（v15）が現在値・取得単価・テクニカルから算出した目安です。最終発注はMoomooで確認。")
        st.code("\n".join(hm_mod.order_memo_lines(m)), language="text")


# ---------------------------------------------------------------- v15: 保有ニュース監視
@st.cache_data(ttl=1800, show_spinner=False)
def _cached_events(ticker):
    """Finnhubの決算/目標株価/アナリスト評価を取得（30分キャッシュ・未設定でも落ちない）。"""
    return hm_mod.fetch_events(ticker)


def _fc_color(label):
    return {"ポジティブ": "#16a34a", "中立": "#64748b", "ネガティブ": "#dc2626"}.get(label, "#94a3b8")


def _imp_color(level):
    return {"高": "#dc2626", "中": "#f59e0b", "低": "#64748b"}.get(level, "#94a3b8")


def render_holding_monitor(rmap, positions, regime):
    st.header("🗓 保有ニュース監視")
    st.caption("保有銘柄すべての『今後の重要ニュース・決算』と『利確・損切り価格』を毎日自動で提案します。"
               "保有が増減すれば対象も自動で変わります。⚠️ 投資助言ではありません。最終発注は必ずMoomooで確認してください。")

    if not positions:
        st.info("保有銘柄が未登録です。『📷 Moomoo同期』で登録すると、自動で監視対象になります。")
        return

    monitors = hm_mod.monitor_all(positions, rmap, regime)
    if not hm_mod.events_available():
        st.warning("🔑 Finnhub APIキー未設定：ニュース/決算カレンダーは取得できません。"
                   "テクニカルだけで利確/損切りを提案します（アプリは落ちません）。")

    # v15.2: 現在値が取得単価の仮表示になっている銘柄を明示（誤表示防止）
    fb = [m["ticker"] for m in monitors if m.get("price_is_fallback")]
    if fb:
        st.warning("⚠️ 次の銘柄は現在値を取得できなかったため、**取得単価を仮表示**しています"
                   "（本物の現在値ではありません）: " + ", ".join(fb)
                   + "。Finnhub APIキー設定で安定します。")

    # ---------- サマリ表 ----------
    rows = []
    for m in monitors:
        rows.append({
            "ティッカー": m["ticker"], "銘柄名": m["name"], "株数": m["shares"],
            "取得単価": m["avg_cost"],
            "現在値": m["current"], "価格ソース": data_fetch_mod.price_source_label(m.get("price_source") or "—"),
            "含み損益%": m["pl_pct"],
            "次回決算": m["earnings_date"] or "—",
            "ニュース重要度": m["news_importance"],
            "短期影響": m["forecast"]["短期"], "今日の対応": m["today"],
        })
    sdf = pd.DataFrame(rows)
    st.dataframe(sdf.style.format({"取得単価": "${:.2f}", "現在値": "${:.2f}",
                                   "含み損益%": "{:+.1f}%", "株数": "{:.4g}"}),
                 width='stretch', hide_index=True)
    st.divider()

    # ---------- 銘柄ごとの詳細 ----------
    for m in monitors:
        lv = m["levels"]; fc = m["forecast"]
        pl = m["pl_pct"]; pl_col = "#16a34a" if (pl or 0) >= 0 else "#dc2626"
        with st.container(border=True):
            st.markdown(f'### {m["ticker"]} <span style="font-size:0.9rem;color:#8a8a8e;">{m["name"]}</span>',
                        unsafe_allow_html=True)
            c = st.columns(6)
            c[0].metric("株数", f'{(m["shares"] or 0):.4g}')
            c[1].metric("取得単価", f'${m["avg_cost"]}')
            c[2].metric("現在値", f'${m["current"]}')
            c[3].metric("含み損益", f'{pl:+.1f}%' if pl is not None else "—")
            c[4].metric("次回決算", m["earnings_date"] or "—")
            c[5].metric("ニュース重要度", m["news_importance"])

            # v15.2: 価格ソースの明示／仮値の警告
            st.caption("価格ソース：" + data_fetch_mod.price_source_label(m.get("price_source") or "—"))
            if m.get("price_is_fallback"):
                st.warning("⚠️ 現在値を取得できなかったため、取得単価を仮表示しています"
                           "（本物の現在値ではありません）。含み損益・利確/損切りも仮計算です。")

            # --- 株価影響予想 ---
            st.markdown('**📈 株価影響予想**')
            fcc = st.columns(3)
            for col, (k, sub) in zip(fcc, [("短期", "今日〜3日"), ("中期", "1〜2週間"), ("スイング", "1〜3ヶ月")]):
                v = fc[k]
                col.markdown(f'<div style="background:#f7f7fa;border-radius:10px;padding:8px 10px;">'
                             f'<div style="font-size:0.72rem;color:#8a8a8e;">{k}（{sub}）</div>'
                             f'<div style="font-weight:800;color:{_fc_color(v)};">{v}</div></div>',
                             unsafe_allow_html=True)

            # --- Upcoming Events（Finnhub追加取得・このページのみ）---
            with st.expander("🗓 今後のイベント / アナリスト / 目標株価", expanded=False):
                fe = _cached_events(m["ticker"])
                if not fe["available"]:
                    st.caption("API未設定：イベントは取得できません（テクニカル提案のみ）。")
                else:
                    shown = False
                    if fe.get("earnings_date"):
                        ed = fe.get("earnings_days")
                        st.write(f"- 📅 次回決算: **{fe['earnings_date']}**" + (f"（あと{ed}日）" if ed is not None else ""))
                        shown = True
                    pt = fe.get("price_target")
                    if pt and pt.get("mean"):
                        up = ((pt["mean"] / m["current"] - 1) * 100) if m["current"] else 0
                        st.write(f"- 🎯 目標株価（平均）: **${pt['mean']}**（現在比 {up:+.0f}%）"
                                 f" / 高 ${pt.get('high','—')}・低 ${pt.get('low','—')}")
                        shown = True
                    rc = fe.get("recommendation")
                    if rc:
                        st.write(f"- 🧮 アナリスト評価（{rc.get('period','')}）: "
                                 f"強気買い {rc.get('strongBuy',0)}・買い {rc.get('buy',0)}・"
                                 f"中立 {rc.get('hold',0)}・売り {rc.get('sell',0)}・強気売り {rc.get('strongSell',0)}")
                        shown = True
                    if not shown:
                        st.caption(fe.get("note") or "イベント未取得。")

            # --- ニュース重要度 ---
            st.markdown('**📰 直近の重要ニュース**')
            if m["news_events"]:
                for ne in m["news_events"][:5]:
                    st.markdown(
                        f'<div style="font-size:0.85rem;padding:2px 0;">'
                        f'<span style="background:{_imp_color(ne["importance"])};color:white;border-radius:6px;'
                        f'padding:1px 7px;font-size:0.72rem;">{ne["importance"]}</span> '
                        f'<span style="color:#64748b;">[{ne["category"]}]</span> {ne["headline"][:60]}</div>',
                        unsafe_allow_html=True)
            elif not m["has_news"]:
                st.caption("API未設定、またはニュース未取得。")
            else:
                st.caption("直近の重要ニュースはありません。")

            # --- 利確・損切り自動提案 ---
            st.markdown('**🎯 利確・損切りの自動提案（理由つき）**')
            gc = st.columns(2)
            with gc[0]:
                st.markdown('🛡 **守り（損失を限定）**')
                for lab, key in [("推奨損切り", "stop"), ("逆指値（利益確保）", "stop_limit"),
                                 ("トレーリングストップ", "trail"), ("全部撤退", "exit_all")]:
                    st.markdown(f'<div style="font-size:0.86rem;padding:2px 0;">'
                                f'<b>{lab}: ${lv[key]}</b><br>'
                                f'<span style="color:#8a8a8e;font-size:0.8rem;">理由: {lv[key+"_reason"]}</span></div>',
                                unsafe_allow_html=True)
            with gc[1]:
                st.markdown('💰 **攻め（利益を伸ばす）**')
                for lab, key in [("利確1", "tp1"), ("利確2", "tp2"), ("利確3", "tp3"), ("半分売る価格", "half")]:
                    st.markdown(f'<div style="font-size:0.86rem;padding:2px 0;">'
                                f'<b>{lab}: ${lv[key]}</b><br>'
                                f'<span style="color:#8a8a8e;font-size:0.8rem;">理由: {lv[key+"_reason"]}</span></div>',
                                unsafe_allow_html=True)

            # --- 今日の対応 ---
            st.markdown(f'<div style="margin-top:6px;padding:8px 12px;border-radius:10px;'
                        f'background:{m["today_color"]}15;border-left:4px solid {m["today_color"]};">'
                        f'<b>今日の対応:</b> {m["today"]}</div>', unsafe_allow_html=True)

    st.caption("※価格・影響予想は公開情報とテクニカルに基づく機械的な目安です。ニュース影響予想は外れることがあります。"
               "投資助言ではありません。最終発注は必ずMoomooでご自身が確認してください。")


# ---------------------------------------------------------------- v16: 目標達成プラン
_FEAS_COLOR = {"現実的": "#16a34a", "やや難しい": "#eab308", "かなり難しい": "#f97316", "非現実的": "#dc2626"}
_ROLE_COLOR = {"主力": "#15803d", "成長枠": "#0ea5e9", "守り枠": "#64748b", "短期狙い": "#f59e0b", "見送り": "#dc2626"}
# v16.1: 保有/ウォッチの役割語彙の色
_ROLE_COLOR2 = {
    "継続": "#16a34a", "利確候補": "#0ea5e9", "損切り候補": "#dc2626", "見直し": "#f59e0b",
    "新規買い候補": "#15803d", "押し目待ち": "#eab308", "見送り": "#94a3b8",
}


def render_goal_plan(rmap, positions, regime, capital):
    st.header("🎯 目標達成プラン")
    st.caption("現在資産・目標金額・期限・毎月追加から必要利回りと現実性を計算し、"
               "保有/ウォッチ/発掘/おすすめ銘柄で目標向けプランを提案します。"
               "⚠️ 投資助言ではありません。無理な目標は正直に表示します。最終発注はMoomooで確認してください。")

    saved = gp_mod.load_goal() or {}

    # v16.1: 保有評価額を自動算出（既存 evaluate_position を再利用。価格ロジックは変更しない）
    held_evals = []  # (ev, hd)
    holdings_value = 0.0
    for p in positions:
        ev = pf_mod.evaluate_position(pf_mod._normalize(p), rmap.get(p["ticker"].upper()))
        hd = ha_mod.decide(ev, regime.get("regime", "中立"))
        held_evals.append((ev, hd))
        holdings_value += ev.get("value", 0) or 0
    holdings_value = round(holdings_value, 2)

    # ---------- 入力（カード型） ----------
    with st.container(border=True):
        st.markdown("##### 📝 目標を入力")
        # --- 現在資産：保有評価額(自動) ＋ 現金残高(手入力) ---
        a = st.columns(2)
        a[0].metric("保有評価額（自動）", f"${holdings_value:,.0f}")
        cash_balance = a[1].number_input("現金残高 ($)", min_value=0.0, step=100.0,
                                         value=float(saved.get("cash_balance", 0.0)), key="gp_cash")
        auto_total = round(holdings_value + cash_balance, 2)
        override = st.checkbox("現在資産を手動で上書きする", value=bool(saved.get("override_assets", False)),
                               key="gp_override",
                               help="Moomooと実際の口座残高がズレる場合にON。OFFなら『保有評価額＋現金』を自動使用します。")
        if override:
            current_assets = st.number_input("現在資産 ($)（手動上書き）", min_value=0.0, step=500.0,
                                             value=float(saved.get("current_assets", auto_total) or auto_total),
                                             key="gp_current")
        else:
            current_assets = auto_total
            st.metric("現在資産合計（自動）", f"${current_assets:,.0f}")
        st.caption(f"内訳：保有評価額 ${holdings_value:,.0f} ＋ 現金 ${cash_balance:,.0f} = ${auto_total:,.0f}"
                   + ("　/　現在は手動上書き中" if override else ""))

        c1 = st.columns(2)
        goal_amount = c1[0].number_input("目標金額 ($)", min_value=0.0, step=500.0,
                                         value=float(saved.get("goal_amount", 10000.0)), key="gp_goal")
        hz_opts = list(gp_mod.HORIZONS.keys())
        horizon = c1[1].selectbox("期限", hz_opts,
                                  index=hz_opts.index(saved.get("horizon", "1年")) if saved.get("horizon") in hz_opts else 2,
                                  key="gp_horizon")
        c2 = st.columns(2)
        monthly_add = c2[0].number_input("毎月追加資金 ($)", min_value=0.0, step=50.0,
                                         value=float(saved.get("monthly_add", 0.0)), key="gp_monthly")
        risk_tol = c2[1].selectbox("リスク許容度", gp_mod.RISK_TOLERANCES,
                                   index=gp_mod.RISK_TOLERANCES.index(saved.get("risk_tolerance", "中"))
                                   if saved.get("risk_tolerance") in gp_mod.RISK_TOLERANCES else 1, key="gp_risk")
        c3 = st.columns(2)
        ml_opts = [f"{x}%" for x in gp_mod.MAX_LOSS_CHOICES]
        ml_default = f'{int(saved.get("max_loss_pct", 10))}%'
        max_loss = c3[0].selectbox("最大許容損失", ml_opts,
                                   index=ml_opts.index(ml_default) if ml_default in ml_opts else 1, key="gp_maxloss")
        style = c3[1].selectbox("投資スタイル", gp_mod.STYLES,
                                index=gp_mod.STYLES.index(saved.get("style", "バランス"))
                                if saved.get("style") in gp_mod.STYLES else 1, key="gp_style")

    inputs = {"current_assets": current_assets, "goal_amount": goal_amount, "horizon": horizon,
              "monthly_add": monthly_add, "risk_tolerance": risk_tol,
              "max_loss_pct": int(max_loss.rstrip("%")), "style": style}
    comp = gp_mod.compute(inputs)

    # ---------- 結論 ----------
    fcol = _FEAS_COLOR.get(comp["feasibility"], "#64748b")
    with st.container(border=True):
        st.markdown("##### 🧭 結論")
        chips = [
            ("目標達成スコア", f'{comp["score"]}', "/100", fcol),
            ("達成可能性", comp["feasibility"], "", fcol),
            ("必要年率", f'{comp["req_annual"]}%', "", "#1d1d1f"),
            ("必要月率", f'{comp["req_monthly"]}%', "", "#1d1d1f"),
            ("不足額", f'${comp["shortfall"]:,.0f}', "", "#1d1d1f"),
            ("必要運用益", f'${comp["need_gain"]:,.0f}', "積立後", "#1d1d1f"),
        ]
        html = '<div style="display:flex;flex-wrap:wrap;gap:8px;">'
        for label, val, sub, color in chips:
            html += (f'<div style="flex:1 1 100px;min-width:100px;background:#f7f7fa;border-radius:14px;padding:8px 11px;">'
                     f'<div style="font-size:0.7rem;color:#8a8a8e;">{label}</div>'
                     f'<div style="font-size:1.15rem;font-weight:800;color:{color};line-height:1.25;">{val}'
                     f'<span style="font-size:0.66rem;color:#aaa;font-weight:600;"> {sub}</span></div></div>')
        html += '</div>'
        st.markdown(html, unsafe_allow_html=True)

    # ---------- 現実性・正直な助言 ----------
    with st.container(border=True):
        st.markdown("##### ✅ 現実性")
        lead = comp["advice"][0] if comp["advice"] else ""
        if comp["feasibility"] in ("非現実的", "かなり難しい"):
            st.warning("⚠️ " + lead)
        else:
            st.success("👍 " + lead)
        for a in comp["advice"][1:]:
            st.caption("・" + a)
        st.caption(f"必要リスク水準: **{comp['risk_level']}**")

    # ---------- 推奨戦略 ----------
    with st.container(border=True):
        st.markdown("##### 📐 推奨戦略")
        m = st.columns(4)
        m[0].metric("推奨現金比率", f'{comp["cash_pct"]}%')
        m[1].metric("最大ポジション", f'{comp["max_pos_pct"]}%')
        m[2].metric("銘柄数", f'{comp["n_positions"]}')
        m[3].metric("分割購入", f'{comp["splits"]}回')
        m2 = st.columns(2)
        m2[0].metric("投資に回す額", f'${comp["invest_budget"]:,.0f}')
        m2[1].metric("1銘柄あたり最大損失", f'${comp["per_stock_loss"]:,.0f}')
        st.caption(f"利確ルール: {comp['tp_rule']}　/　損切りルール: {comp['stop_rule']}")

    # ---------- 候補銘柄 ----------
    candidates = gp_mod.build_candidates(rmap, positions, regime)
    variants = gp_mod.portfolio_variants(candidates, comp)

    # ---------- 3プラン ----------
    st.markdown("##### 🗂 ポートフォリオ案（3パターン）")
    if not candidates:
        st.info("候補銘柄がありません。ウォッチ追加・Moomoo同期・銘柄発掘スキャンを行うと提案が出ます。")
    else:
        vt = st.tabs([f"🛡 {variants[0]['name']}", f"⚖️ {variants[1]['name']}", f"🔥 {variants[2]['name']}"])
        for tab, v in zip(vt, variants):
            with tab:
                st.caption(v["note"])
                vm = st.columns(4)
                vm[0].metric("現金比率", f'{v["cash_pct"]}%')
                vm[1].metric("投資額", f'${v["invested"]:,.0f}')
                vm[2].metric("現金", f'${v["cash_amount"]:,.0f}')
                vm[3].metric("最大損失", f'${v["max_loss"]:,.0f}', f'-{v["max_loss_pct"]}%')
                if v["allocations"]:
                    adf = pd.DataFrame([{
                        "銘柄": a["ticker"], "役割": a["role"], "投入額": a["amount"], "株数": a["shares"],
                        "エントリー": a["entry"], "損切り": a["stop"], "利確1": a["tp1"], "利確2": a["tp2"],
                    } for a in v["allocations"]])
                    st.dataframe(adf.style.format({"投入額": "${:,.0f}", "エントリー": "${:.2f}",
                                                   "損切り": "${:.2f}", "利確1": "${:.2f}", "利確2": "${:.2f}"}),
                                 width='stretch', hide_index=True)
                else:
                    st.caption("このプランに合う候補がありませんでした。")

    # ---------- 銘柄候補（保有とウォッチを分けて表示） ----------
    # 💼 保有銘柄：継続/利確候補/損切り候補/見直し ＋ 目標達成への貢献度
    if held_evals:
        st.markdown("##### 💼 保有銘柄（役割・目標への貢献度）")
        for ev, hd in held_evals:
            role = gp_mod.holding_role(hd.get("action"))
            value = ev.get("value", 0) or 0
            pl_pct = ev.get("pl_pct")
            contrib = round(value / goal_amount * 100, 1) if goal_amount else 0.0
            with st.container(border=True):
                cc = st.columns([1.3, 1, 2.2])
                cc[0].markdown(f'<b>{ev["ticker"]}</b> <span style="color:#8a8a8e;font-size:0.78rem;">'
                               f'${ev.get("current")}</span>', unsafe_allow_html=True)
                cc[1].markdown(f'<span style="background:{_ROLE_COLOR2.get(role,"#64748b")};color:white;'
                               f'border-radius:8px;padding:2px 8px;font-size:0.78rem;font-weight:700;">{role}</span>',
                               unsafe_allow_html=True)
                pls = (f'{pl_pct:+.1f}%' if pl_pct is not None else "—")
                cc[2].markdown(f'<span style="font-size:0.82rem;">評価額 ${value:,.0f}・含み損益 {pls}'
                               f'・目標貢献度 {contrib}%</span>', unsafe_allow_html=True)
                st.caption(f'{hd.get("action","")}：{hd.get("reason","")}')

    # ⭐ ウォッチ銘柄：新規買い候補/押し目待ち/見送り
    watch_cands = [c for c in candidates if not c.get("held")]
    if watch_cands:
        st.markdown("##### ⭐ ウォッチ・候補銘柄（新規買い候補/押し目待ち/見送り）")
        for c in watch_cands[:12]:
            wrole = gp_mod.watch_role(c.get("verdict"), c.get("score"))
            with st.container(border=True):
                cc = st.columns([1.3, 1, 2.2])
                cc[0].markdown(f'<b>{c["ticker"]}</b> <span style="color:#8a8a8e;font-size:0.78rem;">'
                               f'${c["price"]}</span>', unsafe_allow_html=True)
                cc[1].markdown(f'<span style="background:{_ROLE_COLOR2.get(wrole,"#64748b")};color:white;'
                               f'border-radius:8px;padding:2px 8px;font-size:0.78rem;font-weight:700;">{wrole}</span>',
                               unsafe_allow_html=True)
                cc[2].markdown(f'<span style="font-size:0.82rem;">スコア {c["score"]:.0f}・'
                               f'エントリー ${c["entry"]}・損切り ${c["stop"]}・利確1 ${c["tp1"]}</span>',
                               unsafe_allow_html=True)
                if c.get("note"):
                    st.caption(f'⚠️ {c["note"]}')
    if not held_evals and not watch_cands:
        st.caption("候補銘柄がありません。ウォッチ追加・Moomoo同期・銘柄発掘スキャンで提案が増えます。")

    # ---------- 注意点 ----------
    with st.container(border=True):
        st.markdown("##### ⚠️ 注意点")
        st.markdown("- 必要リターンが高いほど**大きなリスク**を取ることになります。損切りを必ず守ってください。\n"
                    "- 価格・スコア・配分は機械的な目安で、将来の成果を保証しません。\n"
                    "- 1銘柄に集中せず、分割エントリー・分散を徹底してください。\n"
                    "- 最終的な発注（株数・価格・注文種別）は必ずMoomooでご自身が確認してください。")

    # ---------- 保存 ----------
    if st.button("💾 この目標プランを保存", width='stretch', key="gp_save"):
        payload = dict(inputs)
        # v16.1: 現在資産の内訳も保存（後方互換：読込側は .get で既定値）
        payload["cash_balance"] = cash_balance
        payload["holdings_value"] = holdings_value
        payload["override_assets"] = bool(override)
        payload["summary"] = {
            "goal_amount": comp["goal_amount"], "req_monthly": comp["req_monthly"],
            "req_annual": comp["req_annual"], "feasibility": comp["feasibility"], "score": comp["score"],
        }
        payload["orders"] = gp_mod.orders_for_memo(variants, prefer="バランスプラン")
        if gp_mod.save_goal(payload):
            st.success("保存しました。ホームの『今日やること』と発注メモにも反映されます。")
        else:
            st.error("保存に失敗しました。")


# ---------------------------------------------------------------- 売買履歴
def render_history():
    st.header("🧾 売買履歴")
    st.caption("売買を記録すると、実現損益・勝率・失敗パターンを自動集計します。")
    rows = hist_mod.load_history()
    base = pd.DataFrame(rows) if rows else pd.DataFrame(columns=hist_mod.HISTORY_FIELDS)
    for c in hist_mod.HISTORY_FIELDS:
        if c not in base.columns:
            base[c] = None
    base = base[hist_mod.HISTORY_FIELDS]

    edited = st.data_editor(
        base, num_rows="dynamic", width='stretch', key="hist_editor",
        column_config={
            "date": st.column_config.TextColumn("日付(YYYY-MM-DD)"),
            "ticker": st.column_config.TextColumn("ティッカー"),
            "side": st.column_config.SelectboxColumn("区分", options=["買い", "売り"]),
            "price": st.column_config.NumberColumn("価格", format="%.2f"),
            "shares": st.column_config.NumberColumn("株数", format="%.0f"),
            "reason": st.column_config.TextColumn("理由"),
            "result": st.column_config.TextColumn("結果メモ"),
        })
    if st.button("💾 履歴を保存", width='stretch'):
        if hist_mod.save_history(edited.to_dict("records")):
            st.success("保存しました。"); st.rerun()
        else:
            st.error("保存に失敗しました。")

    norm = hist_mod.normalize_rows(edited.to_dict("records"))
    stt = hist_mod.stats(norm)
    if stt["n"] == 0:
        st.info("買い→売りが揃うと実現損益を集計します。"); return

    m = st.columns(5)
    m[0].metric("確定取引", f'{stt["n"]}件')
    m[1].metric("勝率", f'{stt["win_rate"]:.0f}%')
    m[2].metric("平均利益", f'{stt["avg_win_pct"]:+.1f}%')
    m[3].metric("平均損失", f'{stt["avg_loss_pct"]:+.1f}%')
    pf = stt["profit_factor"]
    m[4].metric("実現損益", f'${stt["total_pnl"]:,.0f}', f'PF {pf if pf!=float("inf") else "∞"}')

    st.subheader("確定した取引")
    cdf = pd.DataFrame(stt["closed"])
    if not cdf.empty:
        st.dataframe(cdf[["date", "ticker", "buy", "sell", "shares", "pnl", "pnl_pct", "reason"]]
                     .style.format({"buy": "${:.2f}", "sell": "${:.2f}", "pnl": "${:,.0f}", "pnl_pct": "{:+.1f}%"}),
                     width='stretch', height=300)

    st.subheader("よくある失敗パターン / ルール遵守")
    for p in hist_mod.failure_patterns(norm, stt):
        st.write("・" + p)
    adh = hist_mod.rule_adherence(norm, stt)
    (st.success if adh["good"] else st.warning)(
        f'ルール遵守: 記録率 {adh["reason_rate"]:.0f}% / -12%超の損失 {adh["deep_losses"]}件')


# ---------------------------------------------------------------- 反省AI
def render_reflection():
    st.header("🪞 反省AI")
    st.caption("売買履歴から、改善点をやさしくコメント。APIキーがあればAI、なければルールベース。")
    norm = hist_mod.normalize_rows([dict(r) for r in hist_mod.load_history()])
    if not norm:
        st.info("売買履歴を入力・保存すると、反省コメントが表示されます。"); return

    res = refl_mod.reflect(norm)
    st.caption("生成方式: " + ("🤖 AI" if res["source"] == "ai" else "📐 ルールベース"))
    for c in res["comments"]:
        line = c if c.strip().startswith(("・", "✅", "⚠️", "📘", "📕", "🔴")) else "・" + c
        st.write(line)
    st.caption("※コメントは過去履歴に基づく一般的な気づきで、投資助言ではありません。")


# ================================================================== v7: 銘柄発掘（高速・安定）
from modules import discovery as disc_mod
from modules import discovery_tracking as track_mod
from modules import discovery_adoption as adopt_mod
from modules import storage as storage_mod


def _disc_color(verdict):
    return {"強いBUY": "#15803d", "BUY": "#16a34a", "WATCH": "#eab308", "AVOID": "#dc2626"}.get(verdict, "#64748b")


def _conf_style(v):
    cc = {"高": "#16a34a", "中": "#eab308", "低": "#dc2626"}.get(v, "#64748b")
    return f'background-color:{cc};color:white;font-weight:700;'


def _run_scan_with_progress(scope, universe_mode, analysis_mode, regime, watchlist, prev):
    prog = st.progress(0.0)
    status = st.empty()
    state = {"start": time.time()}

    def cb(done, total, fail, cur):
        frac = (done / total) if total else 1.0
        try:
            prog.progress(min(1.0, max(0.0, frac)))
        except Exception:
            pass
        el = time.time() - state["start"]
        eta = (el / done * (total - done)) if done else 0
        status.write(f"⏳ 処理 {done}/{total}｜失敗 {fail}｜残り {total-done}｜推定残り {eta:.0f}秒｜現在: {cur}")

    res = disc_mod.scan(scope, universe_mode, analysis_mode, regime["score"], regime["regime"],
                        regime["fomc_days"], watchlist=watchlist, progress_cb=cb, prev=prev)
    prog.progress(1.0)
    status.write(f"✅ 完了：{res['stats']['screened']}銘柄が条件通過 / {res['stats']['fail']}件失敗 / {res['duration_sec']}秒")
    return res


def render_discovery(regime, watchlist):
    st.header("🔍 銘柄発掘（高速・安定版）")
    tab1, tab2 = st.tabs(["🔍 発掘", "📈 発掘追跡"])
    with tab1:
        _render_discovery_scan(regime, watchlist)
    with tab2:
        _render_discovery_tracking()


def _render_discovery_scan(regime, watchlist):
    c1, c2 = st.columns(2)
    universe_mode = c1.selectbox("ユニバース規模", list(config.SCAN_MODES),
                                 index=list(config.SCAN_MODES).index(config.DEFAULT_SCAN_MODE), key="scan_mode")
    analysis_mode = c2.selectbox("解析モード", config.ANALYSIS_MODES,
                                 index=config.ANALYSIS_MODES.index(config.DEFAULT_ANALYSIS_MODE), key="analysis_mode")
    if "広範囲" in universe_mode:
        st.warning("⏳ 広範囲モードは銘柄数が多く時間がかかります（初回は数十秒〜）。以降はキャッシュ表示。")

    cache = disc_mod.load_cache()
    fresh = disc_mod.is_fresh(cache) if cache else False

    # ---------- 操作ボタン ----------
    st.markdown("**スキャン / 更新**")
    b = st.columns(5)
    scope = None
    if b[0].button("🔁 全銘柄再スキャン", width='stretch'):
        scope = "full"
    if b[1].button("📊 TOP20再評価", width='stretch', disabled=not cache):
        scope = "top20"
    if b[2].button("⭐ ウォッチだけ", width='stretch'):
        scope = "watchlist"
    if b[3].button("♻️ 失敗だけ再試行", width='stretch', disabled=not (cache and cache.get("failed"))):
        scope = "failed"
    if b[4].button("📰 ニュースだけ更新", width='stretch', disabled=not cache):
        scope = "news"

    used_cache = True
    if scope:
        cache = _run_scan_with_progress(scope, universe_mode, analysis_mode, regime, watchlist, prev=cache)
        used_cache = False

    if not cache:
        st.info("まだスキャンしていません。『🔁 全銘柄再スキャン』を押して市場をスキャンしてください。"
                "（軽量モードなら数秒〜十数秒）")
        return

    # 採用ルールの反映状況
    if cache.get("strategy_active"):
        st.info(f"🧪 採用ルール反映中: {cache.get('strategy_label','')}（過去検証に基づく参考設定・将来非保証）")

    # ---------- ヘッダー（鮮度・モード・API・キャッシュ） ----------
    age = disc_mod.cache_age_hours(cache)
    fresh = disc_mod.is_fresh(cache)
    h = st.columns(5)
    h[0].metric("前回スキャン", cache.get("timestamp", "—")[5:16].replace("T", " "))
    h[1].metric("データ鮮度", "🟢新鮮" if fresh else "🟠古い", f'{age:.1f}時間前' if age is not None else "")
    h[2].metric("利用モード", ("高速" if "高速" in cache.get("analysis_mode", "") else "精密"))
    h[3].metric("キャッシュ", "利用中" if used_cache else "今スキャン")
    api = cache.get("api", {})
    h[4].metric("ニュースAPI", f'{api.get("news_calls",0)}回', f'エラー{api.get("news_errors",0)}')
    if not fresh:
        st.warning("⚠️ 表示中のデータは24時間より古いです。『🔁 全銘柄再スキャン』で更新を推奨します。")
    st.caption("💡 高速モードは価格/出来高/テクニカル中心＋ニュースAIは上位のみ（毎朝用）。"
               "精密モードはニュース/AIを多めに使用（週末の深掘り・APIコスト増に注意）。"
               "⚠️ 機械的な発掘で投資助言ではありません。発注前にMoomooで最新値を確認。")
    if api.get("errors"):
        with st.expander(f"⚠️ APIエラー（{api.get('news_errors',0)}件）", expanded=False):
            for e in api["errors"]:
                st.write("・" + str(e))

    _render_discovery_result(cache, regime)

    # ---------- スキャンログ ----------
    log = disc_mod.load_run_log()
    if log:
        with st.expander(f"🧾 スキャンログ（直近{min(len(log),10)}件）", expanded=False):
            st.dataframe(pd.DataFrame(log[-10:][::-1]), width='stretch', height=240)


_RESULT_COLOR = {"成功": "#16a34a", "様子見": "#eab308", "失敗": "#dc2626", "追跡中": "#64748b", "未取得": "#94a3b8"}


def _track_cell(rec, n):
    """7/30/60/90日セルの表示：未到達/未取得/値。"""
    import datetime as _dt
    try:
        d0 = _dt.date.fromisoformat(rec["discovered_at"])
    except Exception:
        return "—"
    if _dt.date.today() < d0 + _dt.timedelta(days=n):
        return "未到達"
    v = rec.get(f"ret_{n}")
    return f"{v:+.1f}%" if v is not None else "未取得"


def _render_discovery_tracking():
    st.caption("発掘カードの「📌 追跡に追加」で候補を保存し、後日「🔄 実績を更新」で7/30/60/90日リターンや"
               "SPY/QQQ比較を計算します。⚠️ 表示は教育・分析用。投資助言ではありません。")
    recs = track_mod.load()
    if not recs:
        st.info("まだ追跡候補がありません。『🔍 発掘』タブのカードで「📌 追跡に追加」を押してください。")
        return

    cc = st.columns([1, 1, 2])
    if cc[0].button("🔄 実績を更新", width='stretch', key="track_update"):
        with st.spinner("実績を更新中…（価格取得）"):
            n = track_mod.update()
        st.success(f"{n}件を更新しました。")
        recs = track_mod.load()
    if cc[1].button("🗑 全クリア", width='stretch', key="track_clear"):
        track_mod.clear()
        st.success("追跡をクリアしました。")
        recs = track_mod.load()
    if not recs:
        return

    # 集計
    s = track_mod.summary(recs)
    m = st.columns(5)
    m[0].metric("成功", s.get("成功", 0))
    m[1].metric("様子見", s.get("様子見", 0))
    m[2].metric("失敗", s.get("失敗", 0))
    m[3].metric("追跡中", s.get("追跡中", 0))
    m[4].metric("未取得", s.get("未取得", 0))

    import datetime as _dt
    today = _dt.date.today()
    rows = []
    for r in recs:
        try:
            elapsed = (today - _dt.date.fromisoformat(r["discovered_at"])).days
        except Exception:
            elapsed = "—"
        rows.append({
            "銘柄": r["ticker"], "発掘日": r["discovered_at"],
            "発掘価格": r.get("discovery_price"), "現在価格": (r.get("current_price") if r.get("current_price") is not None else "—"),
            "経過日数": elapsed,
            "現在%": (f'{r["ret_current"]:+.1f}%' if r.get("ret_current") is not None else "—"),
            "7日": _track_cell(r, 7), "30日": _track_cell(r, 30),
            "60日": _track_cell(r, 60), "90日": _track_cell(r, 90),
            "vsSPY": (f'{r["vs_spy_30"]:+.1f}%' if r.get("vs_spy_30") is not None else "—"),
            "vsQQQ": (f'{r["vs_qqq_30"]:+.1f}%' if r.get("vs_qqq_30") is not None else "—"),
            "結果": r.get("result", "追跡中"),
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.map(lambda v: f'background-color:{_RESULT_COLOR.get(v,"#64748b")};color:white;font-weight:700;',
                     subset=["結果"]),
        width='stretch', hide_index=True, height=min(560, 80 + 34 * len(rows)))
    st.caption("判定（30日主）: 成功=30日+5%以上 or vsSPY+3%以上 ／ 失敗=−5%以下 or vsSPY−3%以下 ／ "
               "その間=様子見 ／ 30日未到達=追跡中 ／ 取得失敗=未取得。"
               f"取引コスト未考慮。data_fetch read-only・mock時は未取得。")

    # ---------- v22.1: 📊 発掘成績（集計・表示のみ） ----------
    _render_tracking_analytics(recs)
    # ---------- v23: 🧠 改善提案（ルールベース・表示のみ） ----------
    _render_tracking_recommendation(recs)


def _track_bd_df(rows, label_name):
    """analytics の breakdown 行を表示用 DataFrame に。"""
    return pd.DataFrame([{
        label_name: r["label"], "件数": r["total"], "判定済み": r["n"],
        "成功率%": (r["success_rate"] if r["success_rate"] is not None else "—"),
        "平均ret30%": (r["avg_ret30"] if r["avg_ret30"] is not None else "—"),
        "平均vsSPY%": (r["avg_vs_spy30"] if r["avg_vs_spy30"] is not None else "—"),
    } for r in rows])


def _render_tracking_analytics(recs):
    st.divider()
    st.subheader("📊 発掘成績")
    a = track_mod.analytics(recs)
    ov = a["overall"]
    if not ov["n"]:
        st.info("まだ判定済みデータがありません（「🔄 実績を更新」で30日経過分が集計されます）。")
        return
    if ov["n"] < 5:
        st.caption("⚠️ サンプルが少ないため参考値です。")
    m = st.columns(6)
    m[0].metric("総追跡数", ov["total"])
    m[1].metric("判定済み", ov["n"])
    m[2].metric("成功率", f'{ov["success_rate"]}%' if ov["success_rate"] is not None else "—")
    m[3].metric("平均ret30", f'{ov["avg_ret30"]:+.1f}%' if ov["avg_ret30"] is not None else "—")
    m[4].metric("平均vsSPY", f'{ov["avg_vs_spy30"]:+.1f}%' if ov["avg_vs_spy30"] is not None else "—")
    m[5].metric("ret30プラス率", f'{ov["win_rate"]}%' if ov["win_rate"] is not None else "—")

    blocks = [("スコア帯別", a["by_score"], "スコア帯"), ("セクター別", a["by_sector"], "セクター"),
              ("発掘理由別", a["by_reason"], "理由"), ("判定別(verdict)", a["by_verdict"], "判定"),
              ("リーダー別", a["by_leader"], "リーダー"), ("相対強度帯別", a["by_rs"], "相対強度")]
    for title, rows, label_name in blocks:
        st.markdown(f"**{title}**")
        if rows:
            st.dataframe(_track_bd_df(rows, label_name), width='stretch', hide_index=True)
        else:
            st.caption("該当データなし（実績更新後・新規追跡分で集計されます）。")


def _reco_line(r):
    parts = []
    if r.get("success_rate") is not None:
        parts.append(f'成功率{r["success_rate"]}%')
    if r.get("avg_ret30") is not None:
        parts.append(f'ret30 {r["avg_ret30"]:+.1f}%')
    if r.get("avg_vs_spy30") is not None:
        parts.append(f'vsSPY {r["avg_vs_spy30"]:+.1f}%')
    return f'{r["category"]}「{r["label"]}」：' + " / ".join(parts) + f'（n={r["n"]}）'


def _render_tracking_recommendation(recs):
    st.divider()
    st.subheader("🧠 改善提案")
    st.caption("⚠️ 追跡実績からの参考提案です。今回はスコア/重みには反映しません（表示のみ）。")
    rec = track_mod.recommendation(recs)
    if rec.get("data_insufficient"):
        st.info("判定済みデータがありません（「🔄 実績を更新」で30日経過分が判定され、提案が表示されます）。")
        return

    conf = rec["confidence"]
    cc = {"高": "#16a34a", "中": "#eab308", "低": "#dc2626"}.get(conf, "#64748b")
    st.markdown(f'信頼度：<b style="color:{cc};">{conf}</b>'
                f'（判定済み {rec["judged"]}件・全体成功率 '
                f'{rec["overall_success"] if rec["overall_success"] is not None else "—"}%）',
                unsafe_allow_html=True)
    if conf != "高":
        st.warning("サンプルが少ないため参考値です。まだ重み変更はしないでください。")

    st.markdown("**✅ 強く効いている条件**")
    if rec["strong"]:
        for r in rec["strong"][:8]:
            st.markdown(f'<div style="border-left:4px solid #16a34a;padding:2px 0 2px 10px;margin-bottom:3px;">'
                        f'{_reco_line(r)}</div>', unsafe_allow_html=True)
    else:
        st.caption("まだ明確に強い条件は見つかっていません")

    st.markdown("**⚠️ 注意すべき条件**")
    if rec["weak"]:
        for r in rec["weak"][:8]:
            st.markdown(f'<div style="border-left:4px solid #dc2626;padding:2px 0 2px 10px;margin-bottom:3px;">'
                        f'{_reco_line(r)}</div>', unsafe_allow_html=True)
    else:
        st.caption("まだ明確に弱い条件は見つかっていません")

    st.markdown("**📌 改善提案**")
    if rec["suggestions"]:
        for s in rec["suggestions"]:
            st.write("・" + s)
    else:
        st.caption("提案はまだありません（データ蓄積中）。")

    st.markdown("**🔭 次に見るべきこと**")
    for s in rec["next_steps"]:
        st.write("・" + s)

    if rec["low_sample"]:
        with st.expander(f"サンプル不足の条件（n<3・{len(rec['low_sample'])}件）", expanded=False):
            st.caption(" / ".join(f'{x["category"]}:{x["label"]}({x["n"]})' for x in rec["low_sample"][:40]))


_LEADER_COLOR = {"リーダー": "#15803d", "フォロワー": "#0ea5e9", "弱い": "#dc2626"}


def _leader_badge(it):
    """v19: リーダー/フォロワー/弱い のバッジ（無ければ空文字・旧キャッシュ安全）。"""
    ld = it.get("leader")
    if not ld:
        return ""
    c = _LEADER_COLOR.get(ld, "#64748b")
    return (f'<span style="background:{c};color:white;border-radius:6px;padding:1px 7px;'
            f'font-size:0.72rem;font-weight:700;">{ld}</span>')


def _vs_line(it):
    """v19.1/19.2: vs SPY / vs QQQ / vs セクターETF（60日超過リターン）の色付きHTML（あるものだけ）。"""
    parts = []
    pairs = [("vs SPY", it.get("vs_spy")), ("vs QQQ", it.get("vs_qqq"))]
    if it.get("vs_etf") is not None and it.get("etf"):
        pairs.append((f'vs {it["etf"]}', it.get("vs_etf")))
    for label, v in pairs:
        if v is None:
            continue
        c = "#16a34a" if v >= 0 else "#dc2626"
        parts.append(f'<span style="color:{c};font-weight:700;">{label} {v:+.1f}%</span>')
    return "　".join(parts)


def _disc_rank_line(it):
    """v19: セクター順位・市場上位%・相対強度% を1行に（あるものだけ）。"""
    parts = []
    if it.get("sector_rank") and it.get("sector_count"):
        parts.append(f'{it["sector"]} {it["sector_rank"]}位/{it["sector_count"]}')
    if it.get("market_pct") is not None:
        parts.append(f'市場上位{it["market_pct"]}%')
    if it.get("rs_pct") is not None:
        parts.append(f'相対強度 上位{it["rs_pct"]}%')
    return "　".join(parts)


def _chip_html(text, color="#374151", bg="#f1f5f9", bold=False):
    fw = "700" if bold else "600"
    return (f'<span style="display:inline-block;background:{bg};color:{color};border-radius:999px;'
            f'padding:2px 9px;margin:2px 2px 0 0;font-size:0.76rem;font-weight:{fw};">{text}</span>')


def _wrap_chips(chips):
    if not chips:
        return ""
    return '<div style="display:flex;flex-wrap:wrap;gap:2px;margin:3px 0;">' + "".join(chips) + '</div>'


def _rank_chips(it):
    """v19.3: 市場上位% / セクター順位 / 相対強度 をチップ化（あるものだけ）。"""
    chips = []
    if it.get("market_pct") is not None:
        chips.append(_chip_html(f'市場上位{it["market_pct"]}%', "#1d4ed8", "#dbeafe"))
    if it.get("sector_rank") and it.get("sector_count"):
        chips.append(_chip_html(f'{it["sector"]} {it["sector_rank"]}位/{it["sector_count"]}', "#7c3aed", "#ede9fe"))
    if it.get("rs_pct") is not None:
        chips.append(_chip_html(f'相対強度 上位{it["rs_pct"]}%', "#0e7490", "#cffafe"))
    return _wrap_chips(chips)


def _vs_chips(it):
    """v19.3: vs SPY / vs QQQ / vs セクターETF をチップ化（緑＝優位・赤＝劣後）。"""
    chips = []
    pairs = [("vs SPY", it.get("vs_spy")), ("vs QQQ", it.get("vs_qqq"))]
    if it.get("vs_etf") is not None and it.get("etf"):
        pairs.append((f'vs {it["etf"]}', it.get("vs_etf")))
    for label, v in pairs:
        if v is None:
            continue
        color, bg = ("#15803d", "#dcfce7") if v >= 0 else ("#b91c1c", "#fee2e2")
        chips.append(_chip_html(f'{label} {v:+.1f}%', color, bg, bold=True))
    return _wrap_chips(chips)


def _reason_chips(reasons, limit=6):
    """v19.3: 発掘理由をチップ化（最大 limit 個）。"""
    chips = [_chip_html(r, "#374151", "#f1f5f9") for r in (reasons or [])[:limit]]
    return _wrap_chips(chips)


def _sector_neutral_chips(it):
    """v21: セクター平均 / 中立スコア / Z をチップ化（あるものだけ・表示のみ）。"""
    chips = []
    if it.get("sector_avg") is not None:
        chips.append(_chip_html(f'セクター平均 {it["sector_avg"]}', "#475569", "#f1f5f9"))
    sn = it.get("sector_neutral")
    if sn is not None:
        color, bg = ("#15803d", "#dcfce7") if sn >= 0 else ("#b91c1c", "#fee2e2")
        chips.append(_chip_html(f'中立 {sn:+.1f}', color, bg, bold=True))
    if it.get("sector_z") is not None:
        chips.append(_chip_html(f'Z {it["sector_z"]:+.2f}', "#7c3aed", "#ede9fe"))
    return _wrap_chips(chips)


def _render_discovery_result(disc, regime):
    stt = disc["stats"]
    s = st.columns(5)
    s[0].metric("読込銘柄数", stt.get("loaded", 0))
    s[1].metric("一次通過", stt.get("screened", 0))
    s[2].metric("除外", stt.get("excluded", 0))
    s[3].metric("取得失敗", stt.get("fail", 0))
    s[4].metric("最終TOP", min(20, len(disc["top20"])))

    if disc.get("rotation"):
        st.subheader("📊 セクターローテーション（今強い順）")
        rcols = st.columns(min(6, len(disc["rotation"])))
        for col, (i, r) in zip(rcols, enumerate(disc["rotation"][:6], 1)):
            col.metric(f'{i}位 {r["sector"]}', f'{r["strength"]:.0f}', f'{r["count"]}銘柄')

    top3, top20 = disc["top3"], disc["top20"]
    if top3:
        st.subheader("🏅 本日の注目 TOP3")
        cols = st.columns(len(top3))
        for col, it in zip(cols, top3):
            c = _disc_color(it["verdict"])
            with col:
                # 1-2. 結論ファースト：ティッカー・会社名・リーダー / 総合スコア・判定
                st.markdown(
                    f'<div style="padding:12px;border-radius:14px;border:2px solid {c};background:{c}12;">'
                    f'<div style="font-size:1.3rem;font-weight:800;color:{c};">{it["ticker"]} {_leader_badge(it)}</div>'
                    f'<div style="color:#475569;font-size:0.82rem;">{it["name"]}｜{it["sector"]}</div>'
                    f'<div style="margin-top:4px;"><span style="font-size:1.7rem;font-weight:900;color:#1d1d1f;">{it["score"]}</span>'
                    f'<span style="font-size:0.85rem;color:{c};font-weight:700;"> {it["verdict"]}・信頼{it["confidence"]}</span></div>'
                    f'</div>', unsafe_allow_html=True)
                # 3. ランキングチップ / 4. 比較チップ / 5. 発掘理由チップ（v21: セクター中立も）
                for html in (_rank_chips(it), _sector_neutral_chips(it), _vs_chips(it),
                             _reason_chips(it.get("reasons"), limit=6)):
                    if html:
                        st.markdown(html, unsafe_allow_html=True)
                # 6. ウォッチ追加ボタン / 追跡に追加
                if wl_has(it["ticker"]):
                    st.caption("✅ ウォッチ登録済み")
                elif st.button("☆ ウォッチ追加", key=f"disc_top_wl_{it['ticker']}", width='stretch'):
                    wl_add(it["ticker"])
                if st.button("📌 追跡に追加", key=f"track_add_top_{it['ticker']}", width='stretch'):
                    ok, msg = track_mod.add(it)
                    (st.success if ok else st.info)(msg)
                # 7. 詳細は expander
                with st.expander("詳細を見る", expanded=False):
                    nd = it.get("news_driver")
                    if nd:
                        st.write(f'**効くニュース**: {nd["headline"]}（影響{nd["impact"]:+d}）')
                    st.write(f'**買い**: ${it["entry"]}　**損切り**: ${it["stop"]}　**期間**: {it["hold"]}')
                    st.info("📌 " + disc_mod.today_line(it))
                    if it["risk_points"] != ["特になし"]:
                        st.caption("リスク: " + " / ".join(it["risk_points"]))
                    if len(it.get("reasons") or []) > 6:
                        st.caption("その他の理由: " + " / ".join(it["reasons"][6:]))
                    st.caption("発見元: " + ", ".join(it["sources"][:4]))

    if top20:
        st.subheader("📋 発掘ランキング")
        # カード中心（上位をカード表示）
        show_n = disc.get("strategy_top_n") or 6
        for it in top20[:show_n]:
            vc = _disc_color(it["verdict"])
            with st.container(border=True):
                cc = st.columns([2.2, 1])
                with cc[0]:
                    st.markdown(f'<div style="font-size:1.2rem;font-weight:800;">{it["ticker"]} '
                                f'<span style="font-size:0.8rem;color:{vc};">{it["verdict"]}・信頼{it["confidence"]}</span> '
                                f'{_leader_badge(it)}</div>'
                                f'<div style="color:#8a8a8e;font-size:0.8rem;">{it.get("name","")}｜{it["sector"]}｜{_stars(it["score"])}</div>',
                                unsafe_allow_html=True)
                    for html in (_rank_chips(it), _sector_neutral_chips(it), _vs_chips(it),
                                 _reason_chips(it.get("reasons"), limit=5)):
                        if html:
                            st.markdown(html, unsafe_allow_html=True)
                    rp = it.get("risk_points") or []
                    if rp and rp != ["特になし"]:
                        st.markdown(f'<span style="font-size:0.78rem;color:#f97316;">リスク: {" / ".join(rp[:2])}</span>', unsafe_allow_html=True)
                with cc[1]:
                    st.markdown(f'<div style="text-align:right;"><span style="font-size:1.8rem;font-weight:800;">{it["score"]}</span>'
                                f'<div style="font-size:0.78rem;color:#16a34a;font-weight:700;">{it.get("action","")}</div>'
                                f'<div style="font-size:0.78rem;color:#8a8a8e;">買${it.get("entry")} / 損${it.get("stop")} / 利${it.get("tp1")}</div></div>',
                                unsafe_allow_html=True)
                    if wl_has(it["ticker"]):
                        st.caption("✅ 登録済み")
                    elif st.button("☆ ウォッチ追加", key=f"disc_wl_{it['ticker']}", width='stretch'):
                        wl_add(it["ticker"])
                    if st.button("📌 追跡", key=f"track_add_{it['ticker']}", width='stretch'):
                        ok, msg = track_mod.add(it)
                        (st.success if ok else st.info)(msg)
        if len(top20) > show_n:
            st.caption(f"上位{show_n}件をカード表示。下の『もっと見る』で全{len(top20)}件の詳細テーブル。")

        with st.expander("📊 もっと見る（全TOP20 詳細テーブル）", expanded=False):
            rows = [{
                "順位": i, "ティッカー": it["ticker"], "企業": it["name"], "セクター": it["sector"],
                "スコア": it["score"], "判定": it["verdict"], "アクション": it["action"],
                "市場上位%": (it.get("market_pct") if it.get("market_pct") is not None else "—"),
                "セクター順位": (f'{it["sector_rank"]}/{it["sector_count"]}'
                              if it.get("sector_rank") and it.get("sector_count") else "—"),
                "セクター平均": (it.get("sector_avg") if it.get("sector_avg") is not None else "—"),
                "中立スコア": (it.get("sector_neutral") if it.get("sector_neutral") is not None else "—"),
                "Z": (it.get("sector_z") if it.get("sector_z") is not None else "—"),
                "相対強度%": (it.get("rs_pct") if it.get("rs_pct") is not None else "—"),
                "vsSPY": (it.get("vs_spy") if it.get("vs_spy") is not None else "—"),
                "vsQQQ": (it.get("vs_qqq") if it.get("vs_qqq") is not None else "—"),
                "vsETF": (f'{it["etf"]} {it["vs_etf"]:+.1f}%'
                          if it.get("vs_etf") is not None and it.get("etf") else "—"),
                "エントリー": it["entry"], "損切り": it["stop"], "利確1": it["tp1"],
            } for i, it in enumerate(top20, 1)]
            df = pd.DataFrame(rows)
            st.dataframe(
                df.style
                .map(lambda v: f'background-color:{_disc_color(v)};color:white;font-weight:700;', subset=["判定"])
                .format({"スコア": "{:.1f}", "エントリー": "${:,.2f}", "損切り": "${:,.2f}", "利確1": "${:,.2f}"})
                .background_gradient(subset=["スコア"], cmap="RdYlGn", vmin=50, vmax=85),
                width='stretch', height=540)
        st.caption("信頼度『低』はデータ不足のためBUYに昇格させず最大WATCHに制限。")

        st.subheader("➕ ウォッチリストに追加")
        picks = st.multiselect("追加する銘柄を選択", [it["ticker"] for it in top20])
        if st.button("選択した銘柄をウォッチリストに追加", width='stretch'):
            if picks:
                wl_add(picks)  # v15.3: 追加は共通の set_watchlist 経由に統一
            else:
                st.info("銘柄を選択してください。")

    if disc.get("excluded"):
        with st.expander(f"🚫 除外された銘柄と理由（{len(disc['excluded'])}件）", expanded=False):
            edf = pd.DataFrame([{
                "ティッカー": it["ticker"], "スコア": it["score"], "セクター": it["sector"],
                "除外理由": " / ".join(it.get("exclude_reasons", [])),
            } for it in disc["excluded"]])
            st.dataframe(edf.style.format({"スコア": "{:.1f}"}), width='stretch', height=280)

    # 🧪 v40: 発掘ランキング実験プレビュー（近似・表示のみ・本番非反映）
    _render_discovery_ranking_experiment(disc.get("top20", []))


def _render_discovery_ranking_experiment(top20):
    """v40: top20 の news を実験ニュースで近似補正し、順位への影響を比較（表示のみ・本番非接続）。"""
    st.divider()
    st.subheader("🧪 発掘ランキング実験プレビュー")
    exp_on = st.checkbox("🧪 発掘ランキング実験を表示（このセッションのみ・本番非反映）",
                         value=False, key="disc_rank_exp")
    p = nadopt_mod.preview_discovery_ranking_experiment(top20, experiment_enabled=exp_on)

    if not p["experiment_applied"]:
        st.info(f"未適用（{p['reason']}）。overrides enabled=true かつ実験チェックON の両方が必要です。")
        return
    if p["total"] == 0:
        st.info("対象の発掘銘柄がありません。")
        return

    m = st.columns(5)
    m[0].metric("対象銘柄数", p["total"])
    m[1].metric("score変化", p["score_changed"])
    m[2].metric("順位変化", p["rank_changed"])
    m[3].metric("新規IN(top10)", p["in_count"])
    m[4].metric("OUT(top10)", p["out_count"])

    st.dataframe(pd.DataFrame([{
        "ticker": r["ticker"], "company": (r["name"] or "")[:24],
        "original_rank": r["original_rank"], "experiment_rank": r["experiment_rank"],
        "rank_delta": f'{r["rank_delta"]:+d}',
        "original_score": r["original_score"], "experiment_score": r["experiment_score"],
        "score_delta": f'{r["score_delta"]:+.1f}',
        "original_verdict": r["original_verdict"], "experiment_verdict": r["experiment_verdict"],
        "status": r["status"],
    } for r in p["rows"]]), width='stretch', hide_index=True)

    st.info("これは発掘ランキングの実験プレビューのみです。本番ランキングには反映していません。")
    st.caption("近似：impact_overrides のみ反映。category補正とaction再計算は未反映です。")

    # 📌 v41: 条件成立時のみ「本番反映候補」として保存（本番非反映・status控えのみ）
    ok, why = nadopt_mod.production_apply_savable(p)
    if ok:
        if st.button("📌 実験ランキング結果を本番反映候補に保存", key="prod_apply_save", width='stretch'):
            done, msg = nadopt_mod.save_production_apply_candidate(p)
            (st.success if done else st.warning)(msg + "（※本番ランキングには反映しません）")
        st.caption("保存結果は下の「📋 ニュース採用候補」に production_apply_candidate として表示されます。")
    else:
        st.caption(f"保存条件未達のため保存ボタンは非表示：{why}")


# ================================================================== v8.5: Moomoo同期
from modules import moomoo_import as mm_mod
from modules import holding_action as ha_mod2


def _sync_input_tabs():
    """v14.5: 3つの入力方法（画像読取 / コピペ同期 / 手入力）をタブで提供。
    読み取り結果は st.session_state['moomoo_rows'] に格納し、下の確認テーブルへ流す。"""
    tab_img, tab_paste, tab_manual = st.tabs(["📷 画像読取", "📋 コピペ同期", "✏️ 手入力"])

    with tab_img:
        vision = mm_mod.vision_available()
        if vision:
            st.success("🟢 Anthropic Vision 利用可（画像から自動読み取り）")
        else:
            st.info("ANTHROPIC_API_KEY 未設定のため画像解析は使えません。『コピペ同期』か『手入力』をご利用ください。")
        up = st.file_uploader("保有画面のスクショ (png / jpg / jpeg)", type=["png", "jpg", "jpeg"], key="mm_upload")
        if up is not None:
            st.image(up, caption="アップロード画像", width='stretch')
        if st.button("🔍 画像を解析して読み取る", disabled=(up is None or not vision),
                     width='stretch', key="mm_btn_img"):
            rows, err = mm_mod.parse_screenshot(up.getvalue(), up.type or "image/png")
            if err:
                st.error(err)
            if rows:
                st.session_state["moomoo_rows"] = rows
                st.session_state.pop("moomoo_editor", None)
                st.rerun()
        st.caption("画像解析は失敗することがあります。うまくいかない時は『コピペ同期』が確実です。")

    with tab_paste:
        st.caption("Moomooの保有画面からコピーしたテキスト、またはCSV風テキストを貼り付けてください。")
        with st.expander("貼り付け例（タップで表示）", expanded=False):
            st.markdown("**Moomooコピー形式（銘柄名→ティッカー→数値）**")
            st.code("Kura Sushi\nKRUS\n30.8639\n48.60\n1553.69\n\n"
                    "Truist Financial\nTFC\n20.077\n49.31\n973.33")
            st.markdown("**CSV形式（ヘッダー行あり）**")
            st.code("ticker,name,qty,avg_cost,current_price,market_value\n"
                    "KRUS,Kura Sushi,30.8639,48.60,50.34,1553.69\n"
                    "TFC,Truist Financial,20.077,49.31,48.48,973.33")
        txt = st.text_area("貼り付けテキスト", key="mm_paste_text", height=170,
                           placeholder="ここにMoomooの保有テキスト/CSVを貼り付け…")
        if st.button("📋 テキストを読み取る", disabled=not (txt or "").strip(),
                     width='stretch', key="mm_btn_paste"):
            rows, err = mm_mod.parse_pasted_text(txt)
            if err:
                st.error(err)
            if rows:
                st.session_state["moomoo_rows"] = rows
                st.session_state.pop("moomoo_editor", None)
                st.success(f"{len(rows)} 件を読み取りました。下の表で確認・修正してください。")
                st.rerun()

    with tab_manual:
        st.caption("空の表に手入力します。下の確認テーブルで直接編集・行追加できます。")
        if st.button("✏️ 空の表を用意する", width='stretch', key="mm_btn_manual"):
            st.session_state["moomoo_rows"] = [mm_mod.empty_row()]
            st.session_state.pop("moomoo_editor", None)
            st.rerun()


def _sync_editor_table():
    """読み取り結果（どのタブ由来でも）を確認・修正し (clean, errors, warns) を返す。"""
    rows = st.session_state.get("moomoo_rows", [mm_mod.empty_row()])
    base = pd.DataFrame(rows)
    for col in mm_mod.MOOMOO_FIELDS:
        if col not in base.columns:
            base[col] = ("" if col in ("ticker", "name") else 0)
    base = base[mm_mod.MOOMOO_FIELDS]
    # コピペ/CSV由来の文字列を数値・文字列に正規化（NumberColumn対策）
    for col in ("quantity", "average_cost", "current_price", "market_value"):
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0.0)
    for col in ("ticker", "name"):
        base[col] = base[col].astype(str).replace("nan", "")

    st.markdown("**読み取り結果（確認・修正してください）**")
    edited = st.data_editor(
        base, num_rows="dynamic", width='stretch', key="moomoo_editor",
        column_config={
            "ticker": st.column_config.TextColumn("ティッカー"),
            "name": st.column_config.TextColumn("銘柄名"),
            "quantity": st.column_config.NumberColumn("株数", format="%.4f"),
            "average_cost": st.column_config.NumberColumn("平均取得単価", format="%.2f"),
            "current_price": st.column_config.NumberColumn("現在値", format="%.2f"),
            "market_value": st.column_config.NumberColumn("評価額", format="%.2f"),
        })
    clean, errors, warns = mm_mod.validate_rows(edited.to_dict("records"))
    for e in errors:
        st.error("⛔ " + e)
    for w in warns:
        st.warning("⚠️ " + w)
    return clean, errors, warns


def render_moomoo_sync():
    st.header("📷 Moomoo 同期")
    st.caption("保有を読み取って positions.json と差分比較し、同期保存します。"
               "毎日は不要 — 初回登録/新規買い/買い増し/一部売却/全売却のときだけ実行してください。")

    _sync_input_tabs()
    clean, errors, warns = _sync_editor_table()
    st.caption(f"読み取り {len(clean)} 件" + ("（エラーがあるため同期できません）" if errors else ""))

    # ---------- 差分比較 ----------
    st.subheader("🔍 既存ポジションとの差分")
    diff = mm_mod.diff_positions(clean)
    cc = st.columns(6)
    labels = [("新規追加", "added"), ("株数増加", "qty_up"), ("株数減少", "qty_down"),
              ("単価変更", "cost_changed"), ("変更なし", "unchanged"), ("スクショに無い", "missing")]
    for col, (lab, key) in zip(cc, labels):
        col.metric(lab, len(diff[key]))

    def _show(title, key, cols):
        if diff[key]:
            st.markdown(f"**{title}**")
            st.dataframe(pd.DataFrame(diff[key])[cols], width='stretch', hide_index=True)

    _show("🆕 新規追加", "added", ["ticker", "new_shares", "new_avg"])
    _show("⬆️ 株数増加（買い増し）", "qty_up", ["ticker", "old_shares", "new_shares", "old_avg", "new_avg"])
    _show("⬇️ 株数減少（一部売却）", "qty_down", ["ticker", "old_shares", "new_shares"])
    _show("✏️ 平均取得単価の変更", "cost_changed", ["ticker", "old_avg", "new_avg"])

    # ---------- スクショに無い既存ポジション（勝手に消さない） ----------
    delete_missing = []
    if diff["missing"]:
        st.markdown("**🟠 スクショに存在しない既存ポジション（全売却済みの可能性）**")
        st.dataframe(pd.DataFrame(diff["missing"])[["ticker", "old_shares", "old_avg"]],
                     width='stretch', hide_index=True)
        st.caption("これらは自動削除しません。全売却した銘柄だけを選んで削除できます。")
        delete_missing = st.multiselect("全売却として削除する銘柄（任意・未選択なら残す）",
                                        [m["ticker"] for m in diff["missing"]])

    # ---------- 同期プレビュー ----------
    n_add = len(diff["added"]); n_upd = len(diff["qty_up"]) + len(diff["qty_down"]) + len(diff["cost_changed"])
    st.subheader("🔁 同期プレビュー")
    st.write(f"追加 **{n_add}件** / 更新 **{n_upd}件** / 削除 **{len(delete_missing)}件** "
             f"（変更なし {len(diff['unchanged'])}件、残す既存 {len(diff['missing'])-len(delete_missing)}件）")

    # ---------- 同期して保存 ----------
    also_watch = st.checkbox("保有銘柄をウォッチリストにも追加する", value=False,
                             help="OFFのままなら保有とウォッチは分離されます（推奨）。ONにすると同期した銘柄をウォッチにも入れます。")
    if st.button("💾 同期して保存", disabled=(bool(errors) or len(clean) == 0), width='stretch'):
        ok, summ = mm_mod.apply_sync(clean, delete_missing=tuple(delete_missing))
        if ok:
            st.success(f"✅ 同期しました。追加 {len(summ['added'])} / 更新 {len(summ['updated'])} / 削除 {len(summ['deleted'])} 件。")
            st.markdown("**以下に反映されました：**")
            for line in ["📒 ポジション台帳（保有一覧・含み損益）",
                         "🎯 今日のアクション（保有銘柄の推奨アクション＋Moomooで今日やること）",
                         "🛡️ ポートフォリオリスク（評価額・集中度・セクター比率）",
                         "🔔 アラート（損切り接近・利確到達・決算・RSI過熱など）",
                         "📝 発注メモ（手動注文用メモ）"]:
                st.write("・" + line)
            st.session_state.pop("moomoo_rows", None)
            st.session_state.pop("moomoo_editor", None)
            if also_watch:
                wl_add([r["ticker"] for r in clean])  # ウォッチにも追加（rerunされる）
        else:
            st.error("保存に失敗しました。")
    st.caption("⚠️ 読み取りは自動のため誤りが残ることがあります。保存前に必ず数値を確認してください。"
               "これは自動発注ではなく、Moomooで手動注文するための補助です。投資助言ではありません。")


# ================================================================== v9: 発掘バックテスト
from modules import discovery_backtest as bt_mod2
import plotly.graph_objects as _go


def _bt_run_with_progress(params):
    prog = st.progress(0.0); status = st.empty(); state = {"start": time.time()}

    def cb(phase, done, total, fail, cur):
        frac = (done / total) if total else 1.0
        try:
            prog.progress(min(1.0, max(0.0, frac)))
        except Exception:
            pass
        el = time.time() - state["start"]
        eta = (el / done * (total - done)) if done else 0
        status.write(f"⏳ {phase} {done}/{total}｜失敗 {fail}｜推定残り {eta:.0f}秒｜{cur}")

    res = bt_mod2.run_backtest(params, progress_cb=cb)
    prog.progress(1.0)
    if res.get("ok"):
        status.write(f"✅ 完了：{res['metrics']['trades']}トレード / {res.get('duration_sec','?')}秒")
    else:
        status.write("⚠️ " + res.get("reason", "失敗"))
    return res


def render_discovery_backtest(watchlist):
    st.header("📈 発掘バックテスト")
    st.caption("発掘ルール（その時点の価格/出来高/移動平均/RSI/相対強度/テーマ）で過去に選んでいたら、"
               "数週間〜数ヶ月でどうだったかを検証します。ニュース/現在ファンダは未来情報のため除外。")
    st.warning("⚠️ これは**過去検証**であり、将来の利益を保証しません。過去に有効でも今後有効とは限りません。")

    tab1, tab2, tab3 = st.tabs(["📊 通常バックテスト", "🚶 ウォークフォワード検証", "🧪 改善案検証"])
    with tab1:
        _render_bt_single(watchlist)
    with tab2:
        _render_bt_walkforward(watchlist)
    with tab3:
        _render_bt_improvement(watchlist)


def _render_bt_single(watchlist):
    # ---------- 設定 ----------
    with st.form("bt_form"):
        c = st.columns(3)
        universe_mode = c[0].selectbox("対象ユニバース", list(config.SCAN_MODES),
                                       index=list(config.SCAN_MODES).index(config.DEFAULT_SCAN_MODE))
        period = c[1].selectbox("期間", list(config.BACKTEST_PERIODS), index=2)
        hold = c[2].selectbox("保有期間", list(config.BACKTEST_HOLD), index=1)
        c2 = st.columns(3)
        entry = c2[0].selectbox("エントリー方式", config.BACKTEST_ENTRY)
        take = c2[1].selectbox("利確ルール", config.BACKTEST_TAKE)
        stop = c2[2].selectbox("損切りルール", config.BACKTEST_STOP)
        condition = st.selectbox("スコア条件", config.BACKTEST_CONDITION, index=3)
        submitted = st.form_submit_button("▶️ バックテスト実行", width='stretch')

    params = {"universe_mode": universe_mode, "period": period, "hold": hold, "entry": entry,
              "take": take, "stop": stop, "condition": condition, "watchlist": tuple(watchlist)}

    cache = bt_mod2.load_cache()
    result = None
    if submitted:
        result = _bt_run_with_progress(params)
        if not result.get("ok"):
            st.error("バックテストを生成できませんでした: " + result.get("reason", ""))
            return
        cache = None  # 直近結果を優先表示
    if result is None:
        if not cache:
            st.info("設定を選んで「▶️ バックテスト実行」を押してください（軽量×1年で数秒〜十数秒）。")
            return
        st.caption(f"前回結果を表示中（{cache.get('timestamp','')[:16].replace('T',' ')}）。"
                   "設定を変えて再実行できます。")
        result = cache
        result["trades"] = cache.get("trades_sample", [])

    _render_bt_result(result)


def _wf_run_with_progress(params, n_folds, test_months):
    prog = st.progress(0.0); status = st.empty(); state = {"start": time.time()}

    def cb(phase, done, total, fail, cur):
        try:
            prog.progress(min(1.0, max(0.0, (done / total) if total else 1.0)))
        except Exception:
            pass
        el = time.time() - state["start"]
        eta = (el / done * (total - done)) if done else 0
        status.write(f"⏳ {phase} {done}/{total}｜失敗 {fail}｜推定残り {eta:.0f}秒｜{cur}")

    res = bt_mod2.walk_forward(params, n_folds=n_folds, test_months=test_months, progress_cb=cb)
    prog.progress(1.0)
    status.write("✅ 完了" if res.get("ok") else "⚠️ " + res.get("reason", "失敗"))
    return res


def _render_bt_walkforward(watchlist):
    st.caption("重み固定のまま、履歴を連続する複数の期間（フォールド）に分け、各期間の"
               "アウトオブサンプル(OOS)成績を見ます。**これは最適化ではありません**（重みは再フィットしません）。")
    with st.form("wf_form"):
        c = st.columns(3)
        universe_mode = c[0].selectbox("対象ユニバース", list(config.SCAN_MODES),
                                       index=list(config.SCAN_MODES).index(config.DEFAULT_SCAN_MODE), key="wf_uni")
        hold = c[1].selectbox("保有期間", list(config.BACKTEST_HOLD), index=1, key="wf_hold")
        condition = c[2].selectbox("スコア条件", config.BACKTEST_CONDITION, index=3, key="wf_cond")
        c2 = st.columns(3)
        entry = c2[0].selectbox("エントリー方式", config.BACKTEST_ENTRY, key="wf_entry")
        take = c2[1].selectbox("利確ルール", config.BACKTEST_TAKE, key="wf_take")
        stop = c2[2].selectbox("損切りルール", config.BACKTEST_STOP, key="wf_stop")
        c3 = st.columns(2)
        n_folds = c3[0].selectbox("フォールド数", [3, 4, 5, 6],
                                  index=[3, 4, 5, 6].index(getattr(config, "WF_DEFAULT_FOLDS", 4)), key="wf_folds")
        test_months = c3[1].selectbox("各フォールドの検証期間(月)", [2, 3, 6],
                                      index=[2, 3, 6].index(getattr(config, "WF_DEFAULT_TEST_MONTHS", 3)), key="wf_tm")
        submitted = st.form_submit_button("▶️ ウォークフォワード実行", width='stretch')

    if not submitted:
        st.info("設定を選んで「▶️ ウォークフォワード実行」を押してください（重み固定の連続OOS評価・少し時間がかかります）。")
        return

    params = {"universe_mode": universe_mode, "period": list(config.BACKTEST_PERIODS)[0], "hold": hold,
              "entry": entry, "take": take, "stop": stop, "condition": condition,
              "weights": "現在設定", "watchlist": tuple(watchlist)}
    r = _wf_run_with_progress(params, n_folds, test_months)
    if not r.get("ok"):
        st.error("ウォークフォワードを生成できませんでした: " + r.get("reason", ""))
        return

    s = r["summary"]; folds = r["folds"]

    def _pm(v, suf="%"):
        return (f"{v:+.1f}{suf}" if isinstance(v, (int, float)) else "—")

    st.subheader("📋 集計（全フォールド）")
    m = st.columns(4)
    m[0].metric("平均OOSリターン", _pm(s.get("avg_oos_return")))
    m[1].metric("平均勝率", f'{s.get("avg_win_rate","—")}%')
    m[2].metric("平均Sharpe", f'{s.get("avg_sharpe","—")}')
    m[3].metric("最悪DD", _pm(s.get("worst_max_dd")))
    m2 = st.columns(4)
    m2[0].metric("SPYに勝った率", f'{s.get("beat_spy_pct","—")}%')
    m2[1].metric("QQQに勝った率", f'{s.get("beat_qqq_pct","—")}%')
    m2[2].metric("ランダムに勝った率", f'{s.get("beat_random_pct","—")}%')
    m2[3].metric("プラス収益フォールド率", f'{s.get("positive_folds_pct","—")}%')

    st.subheader("🚶 フォールド別結果（連続OOS）")
    rows = [{
        "フォールド": f["fold"], "期間": f'{f["start"]}〜{f["end"]}', "トレード": f["trades"],
        "OOSリターン%": f["oos_return"], "勝率%": f["win_rate"], "Sharpe": f["sharpe"],
        "最大DD%": f["max_dd"],
        "vsSPY": (f["vs_spy"] if f["vs_spy"] is not None else "—"),
        "vsQQQ": (f["vs_qqq"] if f["vs_qqq"] is not None else "—"),
        "vsランダム": f["vs_random"],
    } for f in folds]
    df = pd.DataFrame(rows)
    st.dataframe(df.style.format({"OOSリターン%": "{:+.1f}", "勝率%": "{:.0f}", "Sharpe": "{:.2f}",
                                  "最大DD%": "{:.1f}"}),
                 width='stretch', hide_index=True)
    if r.get("fail"):
        st.caption(f"取得失敗でスキップ: {len(r['fail'])}銘柄。")
    _rt = 2 * (getattr(config, "BACKTEST_FEE_PCT", 0.0) + getattr(config, "BACKTEST_SLIPPAGE_PCT", 0.0))
    st.caption(f"⚠️ これは**重み固定のアウトオブサンプル(OOS)検証であり、最適化ではありません**。"
               f"取引コスト（往復 約{_rt:.2f}%）控除済み。各フォールドは連続する未来期間。"
               "フォールドあたりトレードが少ないと結果はぶれます。将来を保証しません。")


def _wf_progress():
    prog = st.progress(0.0); status = st.empty(); state = {"start": time.time()}

    def cb(phase, done, total, fail, cur):
        try:
            prog.progress(min(1.0, max(0.0, (done / total) if total else 1.0)))
        except Exception:
            pass
        status.write(f"⏳ {phase} {done}/{total}｜{cur}")
    return cb, prog, status


_VERDICT_COLOR = {"改善": "#16a34a", "変化なし": "#64748b", "悪化": "#dc2626", "サンプル不足": "#94a3b8"}


def _render_bt_improvement(watchlist):
    st.caption("v23の改善提案（相対強度/スコア/市場上位/セクター中立/リーダー）を**仮フィルター**として比較します。"
               "**検証専用で、本番スコア・発掘ランキングには反映しません。**")
    mode = st.radio("検証モード", ["通常BT比較", "ウォークフォワード比較"], horizontal=True, key="imp_mode")
    if mode == "通常BT比較":
        _render_bt_improvement_single(watchlist)
    else:
        _render_bt_improvement_wf(watchlist)
    # 採用候補（モード共通・常に表示）
    _render_adoption_candidates()


def _render_bt_improvement_single(watchlist):
    with st.form("improve_form"):
        c = st.columns(3)
        universe_mode = c[0].selectbox("対象ユニバース", list(config.SCAN_MODES),
                                       index=list(config.SCAN_MODES).index(config.DEFAULT_SCAN_MODE), key="imp_uni")
        period = c[1].selectbox("期間", list(config.BACKTEST_PERIODS), index=2, key="imp_period")
        hold = c[2].selectbox("保有期間", list(config.BACKTEST_HOLD), index=1, key="imp_hold")
        c2 = st.columns(3)
        entry = c2[0].selectbox("エントリー方式", config.BACKTEST_ENTRY, key="imp_entry")
        take = c2[1].selectbox("利確ルール", config.BACKTEST_TAKE, key="imp_take")
        stop = c2[2].selectbox("損切りルール", config.BACKTEST_STOP, key="imp_stop")
        condition = st.selectbox("ベースのスコア条件", config.BACKTEST_CONDITION, index=4, key="imp_cond")
        names = [n for n, _ in bt_mod2.IMPROVEMENT_FILTERS]
        chosen = st.multiselect("検証する改善フィルター", names, default=names, key="imp_filters")
        submitted = st.form_submit_button("🧪 改善案を検証", width='stretch')

    if submitted:
        params = {"universe_mode": universe_mode, "period": period, "hold": hold, "entry": entry,
                  "take": take, "stop": stop, "condition": condition, "weights": "現在設定",
                  "watchlist": tuple(watchlist)}
        filters = [(n, spec) for n, spec in bt_mod2.IMPROVEMENT_FILTERS if n in chosen] or bt_mod2.IMPROVEMENT_FILTERS
        cb, prog, status = _wf_progress()
        r = bt_mod2.compare_improvement(params, filters=filters, progress_cb=cb)
        prog.progress(1.0)
        if not r.get("ok"):
            status.write("⚠️ " + r.get("reason", "失敗"))
            st.session_state.pop("imp_bt_result", None)
            st.error("検証を生成できませんでした: " + r.get("reason", ""))
            return
        status.write("✅ 完了")
        st.session_state["imp_bt_result"] = r

    r = st.session_state.get("imp_bt_result")
    if not r:
        st.info("フィルターを選んで「🧪 改善案を検証」を押してください（検証専用・ボタン実行）。")
        return

    b = r["baseline"]
    st.subheader("📊 通常バックテスト（ベースライン）")
    mm = st.columns(4)
    mm[0].metric("total_return", f'{b["total_return"]:+.1f}%')
    mm[1].metric("勝率", f'{b["win_rate"]:.0f}%')
    mm[2].metric("avg_return", f'{b["avg_return"]:+.2f}%')
    mm[3].metric("トレード数", b["trade_count"])

    st.subheader("🧪 改善案との比較")
    rows = [{
        "フィルター": "（ベースライン）", "判定": "—", "total_return%": b["total_return"], "勝率%": b["win_rate"],
        "avg_return%": b["avg_return"], "Sharpe": b["sharpe"], "最大DD%": b["max_drawdown"],
        "トレード数": b["trade_count"],
        "vsSPY": (b["vs_spy"] if b["vs_spy"] is not None else "—"),
        "vsQQQ": (b["vs_qqq"] if b["vs_qqq"] is not None else "—"), "vsランダム": b["vs_random"],
    }]
    for v in r["variants"]:
        rows.append({
            "フィルター": v["name"], "判定": v["verdict"], "total_return%": v["total_return"], "勝率%": v["win_rate"],
            "avg_return%": v["avg_return"], "Sharpe": v["sharpe"], "最大DD%": v["max_drawdown"],
            "トレード数": v["trade_count"],
            "vsSPY": (v["vs_spy"] if v["vs_spy"] is not None else "—"),
            "vsQQQ": (v["vs_qqq"] if v["vs_qqq"] is not None else "—"), "vsランダム": v["vs_random"],
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style
        .map(lambda v: f'background-color:{_VERDICT_COLOR.get(v,"#64748b")};color:white;font-weight:700;'
             if v in _VERDICT_COLOR else "", subset=["判定"])
        .format({"total_return%": "{:+.1f}", "勝率%": "{:.0f}", "avg_return%": "{:+.2f}",
                 "Sharpe": "{:.2f}", "最大DD%": "{:.1f}"}),
        width='stretch', hide_index=True)
    if r.get("fail"):
        st.caption(f"取得失敗でスキップ: {len(r['fail'])}銘柄。")
    st.caption("判定: total_returnがベースライン+3%以上かつトレード数がベースラインの30%以上→改善 ／ −3%以上→悪化 ／ "
               "その他→変化なし ／ トレード数<10→サンプル不足。PIT（当日情報のみ）で検証。"
               "⚠️ **これは検証専用であり、本番スコアには反映していません。**")

    # 採用候補に保存（ゲート通過分のみ）
    savable = [v for v in r["variants"] if adopt_mod.bt_savable(v, b)]
    if savable:
        st.markdown("**📌 採用候補に保存（改善＆条件クリア分のみ）**")
        for v in savable:
            cc = st.columns([3, 1])
            cc[0].caption(f'{v["name"]}：total_return {v["total_return"]:+.1f}%（ベース比 '
                          f'{v["total_return"]-b["total_return"]:+.1f}）・trade {v["trade_count"]}')
            if cc[1].button("📌 保存", key=f"save_bt_{v['name']}", width='stretch'):
                ok, msg = adopt_mod.add_from_bt(v, b)
                (st.success if ok else st.info)(msg)


def _render_bt_improvement_wf(watchlist):
    st.caption("改善フィルターを**ウォークフォワード（連続OOS）**でも検証します（通常WF vs フィルターWF）。"
               "**検証専用で、本番スコア・発掘ランキングには反映しません。**")
    with st.form("improve_wf_form"):
        c = st.columns(3)
        universe_mode = c[0].selectbox("対象ユニバース", list(config.SCAN_MODES),
                                       index=list(config.SCAN_MODES).index(config.DEFAULT_SCAN_MODE), key="impwf_uni")
        hold = c[1].selectbox("保有期間", list(config.BACKTEST_HOLD), index=1, key="impwf_hold")
        condition = c[2].selectbox("ベースのスコア条件", config.BACKTEST_CONDITION, index=4, key="impwf_cond")
        c2 = st.columns(4)
        entry = c2[0].selectbox("エントリー方式", config.BACKTEST_ENTRY, key="impwf_entry")
        take = c2[1].selectbox("利確ルール", config.BACKTEST_TAKE, key="impwf_take")
        stop = c2[2].selectbox("損切りルール", config.BACKTEST_STOP, key="impwf_stop")
        c3 = st.columns(2)
        n_folds = c3[0].selectbox("フォールド数", [3, 4, 5, 6],
                                  index=[3, 4, 5, 6].index(getattr(config, "WF_DEFAULT_FOLDS", 4)), key="impwf_folds")
        test_months = c3[1].selectbox("各フォールドの検証期間(月)", [2, 3, 6],
                                      index=[2, 3, 6].index(getattr(config, "WF_DEFAULT_TEST_MONTHS", 3)), key="impwf_tm")
        names = [n for n, _ in bt_mod2.IMPROVEMENT_FILTERS]
        chosen = st.multiselect("検証する改善フィルター", names, default=names, key="impwf_filters")
        submitted = st.form_submit_button("🚶 ウォークフォワードで検証", width='stretch')

    if submitted:
        params = {"universe_mode": universe_mode, "period": list(config.BACKTEST_PERIODS)[0], "hold": hold,
                  "entry": entry, "take": take, "stop": stop, "condition": condition, "weights": "現在設定",
                  "watchlist": tuple(watchlist)}
        filters = [(n, spec) for n, spec in bt_mod2.IMPROVEMENT_FILTERS if n in chosen] or bt_mod2.IMPROVEMENT_FILTERS
        cb, prog, status = _wf_progress()
        r = bt_mod2.compare_improvement_walk_forward(params, filters=filters, n_folds=n_folds,
                                                     test_months=test_months, progress_cb=cb)
        prog.progress(1.0)
        if not r.get("ok"):
            status.write("⚠️ " + r.get("reason", "失敗"))
            st.session_state.pop("imp_wf_result", None)
            st.error("検証を生成できませんでした: " + r.get("reason", ""))
            return
        status.write("✅ 完了")
        st.session_state["imp_wf_result"] = r

    r = st.session_state.get("imp_wf_result")
    if not r:
        st.info("フィルターを選んで「🚶 ウォークフォワードで検証」を押してください（検証専用・ボタン実行）。")
        return

    bs = r["baseline"]["summary"]
    st.subheader("📊 通常ウォークフォワード（ベースライン）")
    mm = st.columns(4)
    mm[0].metric("平均OOSリターン", f'{bs["avg_oos_return"]:+.1f}%' if bs["avg_oos_return"] is not None else "—")
    mm[1].metric("プラス収益F率", f'{bs["positive_folds_pct"]}%')
    mm[2].metric("SPYに勝った率", f'{bs["beat_spy_pct"]}%' if bs["beat_spy_pct"] is not None else "—")
    mm[3].metric("平均trade", f'{r["baseline"]["avg_trade_count"]:.0f}')

    st.subheader("🚶 改善案 × ウォークフォワード比較")
    rows = [{
        "フィルター": "（ベースライン）", "判定": "—",
        "平均OOS%": bs["avg_oos_return"], "プラスF%": bs["positive_folds_pct"],
        "SPY勝率%": bs["beat_spy_pct"], "QQQ勝率%": bs["beat_qqq_pct"], "ランダム勝率%": bs["beat_random_pct"],
        "平均Sharpe": bs["avg_sharpe"], "平均maxDD%": bs["avg_max_dd"], "平均trade": r["baseline"]["avg_trade_count"],
    }]
    for v in r["variants"]:
        s = v["summary"]
        rows.append({
            "フィルター": v["name"], "判定": v["verdict"],
            "平均OOS%": s["avg_oos_return"], "プラスF%": s["positive_folds_pct"],
            "SPY勝率%": s["beat_spy_pct"], "QQQ勝率%": s["beat_qqq_pct"], "ランダム勝率%": s["beat_random_pct"],
            "平均Sharpe": s["avg_sharpe"], "平均maxDD%": s["avg_max_dd"], "平均trade": v["avg_trade_count"],
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.map(lambda v: f'background-color:{_VERDICT_COLOR.get(v,"#64748b")};color:white;font-weight:700;'
                     if v in _VERDICT_COLOR else "", subset=["判定"]),
        width='stretch', hide_index=True)
    if r.get("fail"):
        st.caption(f"取得失敗でスキップ: {len(r['fail'])}銘柄。")
    st.caption("判定: 平均OOSがベースライン+3%以上かつプラスFolds率が同等以上かつ平均trade≥30%→改善 ／ "
               "平均OOS−3%以上 or SPY/QQQ勝率が25pt以上低下→悪化 ／ 平均trade<10 or 有効フォールド<半数→サンプル不足 ／ "
               "その他→変化なし。PIT（各フォールド当日情報のみ）。⚠️ **検証専用・本番スコア未反映。**")

    # 採用候補に保存（ゲート通過分のみ）
    base = r["baseline"]
    savable = [v for v in r["variants"] if adopt_mod.wf_savable(v, base)]
    if savable:
        st.markdown("**📌 採用候補に保存（改善＆条件クリア分のみ）**")
        for v in savable:
            s = v["summary"]
            cc = st.columns([3, 1])
            cc[0].caption(f'{v["name"]}：平均OOS {s["avg_oos_return"]:+.1f}%（ベース比 '
                          f'{s["avg_oos_return"]-base["summary"]["avg_oos_return"]:+.1f}）・平均trade {v["avg_trade_count"]:.0f}')
            if cc[1].button("📌 保存", key=f"save_wf_{v['name']}", width='stretch'):
                ok, msg = adopt_mod.add_from_wf(v, base)
                (st.success if ok else st.info)(msg)


_ADOPT_STATUS_COLOR = {"candidate": "#0ea5e9", "approved": "#16a34a", "rejected": "#dc2626"}


def _render_adoption_candidates():
    st.divider()
    st.subheader("📋 採用候補")
    st.caption("⚠️ ここは人間の確認用の控えです。承認しても**本番スコア・発掘ランキングには反映しません**（statusが変わるだけ）。")
    recs = adopt_mod.list_all()
    if not recs:
        st.info("採用候補はまだありません。上の検証結果で「📌 採用候補に保存」を押すと追加されます。")
        return
    if st.button("🗑 採用候補を全クリア", key="adopt_clear"):
        adopt_mod.clear()
        st.success("採用候補をクリアしました。")
        recs = adopt_mod.list_all()
    for r in recs:
        sc = _ADOPT_STATUS_COLOR.get(r.get("status"), "#64748b")
        bt_imp = (r.get("bt") or {}).get("improvement")
        wf_imp = (r.get("wf") or {}).get("improvement")
        with st.container(border=True):
            st.markdown(
                f'<b>{r["filter_label"]}</b>　<span style="font-size:0.78rem;color:#64748b;">{r["id"]}</span>　'
                f'<span style="background:#eef;border-radius:6px;padding:1px 7px;font-size:0.72rem;">{r["source"]}</span>　'
                f'<span style="background:{sc};color:white;border-radius:6px;padding:1px 7px;font-size:0.72rem;">{r["status"]}</span>',
                unsafe_allow_html=True)
            bits = []
            if bt_imp is not None:
                bits.append(f'BT改善 {bt_imp:+.1f}%・trade {(r["bt"].get("candidate") or {}).get("trade_count","—")}')
            if wf_imp is not None:
                bits.append(f'WF改善 {wf_imp:+.1f}%・平均trade {r["wf"].get("avg_trade_count","—")}')
            st.caption("　/　".join(bits) + f'　｜作成 {r["created_at"][:16].replace("T"," ")}・更新 {r["updated_at"][:16].replace("T"," ")}')
            a = st.columns(3)
            if a[0].button("✅ 承認", key=f"adopt_ap_{r['id']}", width='stretch'):
                adopt_mod.approve(r["id"]); st.rerun()
            if a[1].button("🚫 却下", key=f"adopt_rj_{r['id']}", width='stretch'):
                adopt_mod.reject(r["id"]); st.rerun()
            if a[2].button("🗑 削除", key=f"adopt_rm_{r['id']}", width='stretch'):
                adopt_mod.remove(r["id"]); st.rerun()


def _mini_metrics(m, title):
    st.markdown(f"**{title}**")
    if not m or not m.get("trades"):
        st.caption("該当トレードなし"); return
    cc = st.columns(4)
    cc[0].metric("トレード", m["trades"]); cc[1].metric("勝率", f'{m["win_rate"]:.0f}%')
    cc[2].metric("期待値", f'{m["expectancy"]:+.2f}%'); cc[3].metric("PF", m["profit_factor"])


def _render_bt_result(r):
    m = r["metrics"]
    st.subheader("📊 結果サマリー")
    a = st.columns(4)
    a[0].metric("総トレード数", m["trades"])
    a[1].metric("勝率", f'{m["win_rate"]:.0f}%')
    a[2].metric("平均利益", f'{m["avg_win"]:+.1f}%')
    a[3].metric("平均損失", f'{m["avg_loss"]:+.1f}%')
    b = st.columns(4)
    b[0].metric("期待値/トレード", f'{m["expectancy"]:+.2f}%')
    b[1].metric("最大ドローダウン", f'{m["max_dd"]:.1f}%')
    b[2].metric("プロフィットファクター", m["profit_factor"])
    b[3].metric("平均保有日数", f'{m["avg_days"]:.1f}日')
    st.caption(f"総リターン(概算) {m['total_return']:+.1f}%／最大利益 {m['max_win']:+.1f}%／最大損失 {m['max_loss']:+.1f}%")

    # ---------- 比較 ----------
    st.subheader("⚖️ 比較（買いっぱなし・ランダムとの対決）")
    comp = r["comparisons"]
    cols = st.columns(len(comp))
    base = comp.get("発掘ルール", 0) or 0
    for col, (k, v) in zip(cols, comp.items()):
        delta = None if k == "発掘ルール" else (f'{base-(v or 0):+.1f}pt' if v is not None else None)
        col.metric(k, f'{v:+.1f}%' if v is not None else "—", delta)
    st.caption("『発掘ルール』が指数・ランダムに勝っていれば、ルールに優位性があった可能性（過去において）。")

    # ---------- グラフ ----------
    st.subheader("📈 グラフ")
    eq = r.get("equity_curve", []); rc = r.get("random_curve", [])
    if eq:
        fig = _go.Figure()
        fig.add_trace(_go.Scatter(x=[c["date"] for c in eq], y=[c["equity"] for c in eq],
                                  name="発掘ルール", line=dict(color="#16a34a", width=2)))
        if rc:
            fig.add_trace(_go.Scatter(x=[c["date"] for c in rc], y=[c["equity"] for c in rc],
                                      name="ランダム", line=dict(color="#94a3b8", width=1, dash="dot")))
        fig.update_layout(title="累積損益（資産倍率）", height=300, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width='stretch')
    dd = r.get("dd_curve", [])
    if dd:
        fig = _go.Figure()
        fig.add_trace(_go.Scatter(x=[c["date"] for c in dd], y=[c["dd"] for c in dd],
                                  fill="tozeroy", line=dict(color="#dc2626"), name="DD"))
        fig.update_layout(title="ドローダウン (%)", height=240, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width='stretch')

    g = st.columns(2)
    bm = r.get("by_month", {})
    if bm:
        fig = _go.Figure([_go.Bar(x=list(bm.keys()), y=list(bm.values()),
                                  marker_color=["#16a34a" if v >= 0 else "#dc2626" for v in bm.values()])])
        fig.update_layout(title="月別 平均リターン (%)", height=260, margin=dict(l=10, r=10, t=40, b=10))
        g[0].plotly_chart(fig, width='stretch')
    bb = r.get("by_bucket", {})
    if bb:
        order = ["<55", "55-70", "70-80", "80+"]
        ks = [k for k in order if k in bb]
        fig = _go.Figure([_go.Bar(x=ks, y=[bb[k]["expectancy"] for k in ks], marker_color="#0ea5e9")])
        fig.update_layout(title="スコア帯別 期待値 (%)", height=260, margin=dict(l=10, r=10, t=40, b=10))
        g[1].plotly_chart(fig, width='stretch')

    bs = r.get("by_sector", {})
    if bs:
        items = sorted(bs.items(), key=lambda x: -x[1].get("expectancy", 0))[:12]
        fig = _go.Figure([_go.Bar(x=[k for k, _ in items], y=[v["expectancy"] for _, v in items],
                                  marker_color=["#16a34a" if v["expectancy"] >= 0 else "#dc2626" for _, v in items])])
        fig.update_layout(title="セクター別 期待値 (%)", height=280, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width='stretch')

    # TOP5/10/20 比較
    tc = r.get("top_cmp", {})
    if tc:
        st.markdown("**TOP5 / TOP10 / TOP20 比較**")
        tdf = pd.DataFrame([{"選定": k, "期待値%": v["expectancy"], "勝率%": v["win_rate"],
                             "平均%": v["avg"], "トレード数": v["trades"]} for k, v in tc.items()])
        st.dataframe(tdf, width='stretch', hide_index=True)

    # 判定別
    st.subheader("🔎 判定別 / スコア帯別")
    cols = st.columns(2)
    with cols[0]:
        _mini_metrics(r.get("by_verdict", {}).get("BUY", {}), "BUY判定")
    with cols[1]:
        _mini_metrics(r.get("by_verdict", {}).get("WATCH", {}), "WATCH判定")

    # ---------- トレード一覧 + CSV ----------
    st.subheader("🧾 トレード一覧")
    trades = r.get("trades", [])
    if trades:
        cols_order = ["date", "ticker", "score", "verdict", "entry", "exit", "pnl_pct",
                      "days", "reason", "sector", "theme"]
        tdf = pd.DataFrame(trades)
        show = tdf[[c for c in cols_order if c in tdf.columns]].rename(columns={
            "date": "検証日", "ticker": "ティッカー", "score": "スコア", "verdict": "判定",
            "entry": "エントリー", "exit": "エグジット", "pnl_pct": "損益%", "days": "保有日数",
            "reason": "終了理由", "sector": "セクター", "theme": "テーマ"})
        st.dataframe(show.style.format({"スコア": "{:.1f}", "エントリー": "${:.2f}", "エグジット": "${:.2f}",
                                        "損益%": "{:+.1f}%"}), width='stretch', height=360)
        csv_text = bt_mod2.load_trades_csv_text() or show.to_csv(index=False)
        st.download_button("⬇️ トレード一覧をCSVダウンロード", data=csv_text,
                           file_name="backtest_trades.csv", mime="text/csv", width='stretch')
        if r.get("n_trades") and r["n_trades"] > len(trades):
            st.caption(f"※表示は先頭{len(trades)}件。全{r['n_trades']}件はCSVに含まれます。")

    # ---------- 改善提案 ----------
    st.subheader("💡 ルール改善提案")
    ai = bt_mod2.ai_suggestions(r) if any(__import__("os").getenv(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")) else None
    st.caption("生成方式: " + ("🤖 AI" if ai else "📐 ルールベース"))
    for s in (ai or r.get("suggestions", [])):
        line = s if s.strip().startswith(("・", "-", "✅", "⚠️")) else "・" + s
        st.write(line)
    if r.get("fail"):
        st.caption(f"取得失敗でスキップ: {len(r['fail'])}銘柄（落ちずに継続）。")
    _rt = 2 * (getattr(config, "BACKTEST_FEE_PCT", 0.0) + getattr(config, "BACKTEST_SLIPPAGE_PCT", 0.0))
    st.caption(f"⚠️ 取引コスト（往復 約{_rt:.2f}%＝手数料+スリッページ）を控除済み（v18）。"
               "発掘・ランダムは毎トレード、SPY/QQQは1往復ぶん。流動性・当時のニュースは未反映で将来を保証しません。")


# ================================================================== v10: ルール最適化
from modules import optimizer as opt_mod


def _opt_run_with_progress(selected, base):
    prog = st.progress(0.0); status = st.empty(); state = {"start": time.time()}

    def cb(done, total, label):
        frac = (done / total) if total else 1.0
        try:
            prog.progress(min(1.0, max(0.0, frac)))
        except Exception:
            pass
        el = time.time() - state["start"]
        eta = (el / done * (total - done)) if done else 0
        status.write(f"⏳ 組み合わせ {done}/{total}｜推定残り {eta:.0f}秒｜{label}")

    rows = opt_mod.grid_search(selected, base, progress_cb=cb)
    prog.progress(1.0)
    status.write(f"✅ 完了：{len(rows)}通りを検証しました。")
    return rows


def render_rule_optimization(watchlist):
    st.header("🧪 ルール最適化（グリッドサーチ）")
    st.caption("複数の設定を一括バックテストし、期待値が高く“壊れにくい”設定を探します。"
               "感覚ではなく過去検証で選ぶための道具です。")
    st.warning("⚠️ 過去データに合わせすぎる（過学習）と将来は機能しません。**シンプルな設定**ほど信頼できます。"
               "結果は将来を保証しません。まずは軽量モードで。")

    # ---------- 最適化対象 ----------
    cc = st.columns(2)
    universe_mode = cc[0].selectbox("対象ユニバース", list(config.SCAN_MODES),
                                    index=list(config.SCAN_MODES).index(config.DEFAULT_SCAN_MODE))
    period = cc[1].selectbox("検証期間", list(config.BACKTEST_PERIODS), index=1)

    st.markdown("**探索する候補（複数選ぶほど組み合わせが増えます）**")
    defaults = {"hold": ["20営業日"], "entry": ["翌営業日始値"],
                "condition": ["スコア上位TOP10", "スコア上位TOP20"], "stop": ["初期損切り"],
                "take": ["利確1で半分→利確2で残り"], "rsi_exclude": ["なし"],
                "volume_cond": ["なし"], "weights": ["現在設定"]}
    selected = {}
    g = st.columns(2)
    for n, (key, label, options) in enumerate(opt_mod.DIMENSIONS):
        col = g[n % 2]
        selected[key] = col.multiselect(label, options, default=defaults.get(key, [options[0]]), key=f"opt_{key}")
        if not selected[key]:
            selected[key] = [defaults.get(key, [options[0]])[0]]

    base = {"universe_mode": universe_mode, "period": period, "watchlist": tuple(watchlist)}
    for key, _, options in opt_mod.DIMENSIONS:
        base[key] = (selected[key][0] if selected.get(key) else options[0])

    combos = opt_mod.count_combos(selected)
    capped = combos > config.OPT_MAX_COMBOS
    st.info(f"組み合わせ数: **{combos}**" + (f" → 上限{config.OPT_MAX_COMBOS}まで実行します（コスト対策）。" if capped else "")
            + f"　1通り約2〜3秒（軽量）。目安 {min(combos, config.OPT_MAX_COMBOS)*3} 秒前後。")

    run = st.button("🧪 最適化を実行", width='stretch')

    rows = None
    if run:
        rows = _opt_run_with_progress(selected, base)
        if not rows:
            st.error("有効な結果が出ませんでした（条件が厳しすぎる可能性）。候補を増やしてください。")
            return
    else:
        cache = opt_mod.load_cache()
        if cache and cache.get("rows"):
            st.caption(f"前回の最適化結果を表示中（{cache.get('timestamp','')[:16].replace('T',' ')}）。再実行で更新。")
            rows = cache["rows"]
        else:
            st.info("候補を選び「🧪 最適化を実行」を押してください。")
            return

    _render_optimization_result(rows)


def _render_optimization_result(rows):
    # ---------- ランキング基準 ----------
    crit = st.selectbox("ランキング基準", ["総合スコア", "期待値", "PF", "最大DD低め", "総リターン", "安定性"])
    keyf = {
        "総合スコア": lambda r: -r["composite"], "期待値": lambda r: -r["expectancy"],
        "PF": lambda r: -r["profit_factor"], "最大DD低め": lambda r: -(r["max_dd"]),
        "総リターン": lambda r: -r["total_return"],
        "安定性": lambda r: (not r.get("stable"), -r["composite"]),
    }[crit]
    ranked = sorted(rows, key=keyf)

    # ---------- ランキング表 ----------
    st.subheader("🏁 最適化ランキング")
    tbl = pd.DataFrame([{
        "総合": r["composite"], "設定": r["label"], "トレード": r["trades"], "勝率%": r["win_rate"],
        "期待値%": r["expectancy"], "PF": r["profit_factor"], "最大DD%": r["max_dd"],
        "総リターン%": r["total_return"], "シャープ": r["sharpe"], "vsランダム": r["vs_random"],
        "vsSPY": r["vs_spy"], "前半": r.get("wf_first"), "後半": r.get("wf_second"),
        "安定": "✅" if r.get("stable") else ("⚠️過学習" if r.get("overfit") else "—"),
    } for r in ranked])
    st.dataframe(tbl.style.format({"期待値%": "{:+.2f}", "PF": "{:.2f}", "最大DD%": "{:.1f}",
                                   "総リターン%": "{:+.1f}", "シャープ": "{:.2f}", "vsランダム": "{:+.1f}",
                                   "vsSPY": "{:+.1f}"})
                 .background_gradient(subset=["総合"], cmap="RdYlGn"), width='stretch', height=320)

    # CSVダウンロード
    csv_text = opt_mod.load_results_csv_text()
    if csv_text:
        st.download_button("⬇️ 最適化結果をCSVダウンロード", data=csv_text,
                           file_name="optimization_results.csv", mime="text/csv", width='stretch')

    # ---------- ベスト3カード ----------
    st.subheader("🥇 ベスト設定 TOP3")
    top3 = ranked[:3]
    cols = st.columns(len(top3))
    for i, (col, r) in enumerate(zip(cols, top3)):
        d = opt_mod.describe(r)
        with col:
            with st.container(border=True):
                st.markdown(f"### #{i+1}　総合 {r['composite']}")
                st.caption(r["label"])
                st.write("**設定**: " + " / ".join(f"{r[k]}" for k in ("condition", "hold", "stop", "take", "entry", "rsi_exclude", "volume_cond", "weights")))
                st.write("**なぜ良いか**")
                for x in d["good"]:
                    st.write("・" + x)
                st.write("**弱点**")
                for x in d["weak"]:
                    st.write("・" + x)
                st.write("**向く相場**: " + " / ".join(d["suited"]))
                # ウォークフォワード
                wf_label = ("🟢 信頼度高（前半後半とも黒字）" if r.get("stable")
                            else "🔴 過学習疑い（前半のみ良い）" if r.get("overfit") else "🟡 中立")
                st.write(f"**前半/後半**: {r.get('wf_first')} / {r.get('wf_second')}　{wf_label}")
                for x in d["notes"]:
                    st.caption("⚠️ " + x)
                if st.button("✅ この設定を採用する", key=f"adopt_{i}", width='stretch'):
                    if opt_mod.adopt(r["params"]):
                        st.success("strategy_settings.json に保存しました。"
                                   "『🔍 銘柄発掘』で再スキャンすると発掘・今日のアクション・通知に反映されます"
                                   "（採用ルールは過去検証に基づく参考設定・将来非保証）。")
                    else:
                        st.error("保存に失敗しました。")

    # ---------- 過学習チェック（上位の警告集約） ----------
    st.subheader("🧯 過学習チェック")
    any_flag = False
    for r in ranked[:5]:
        if r.get("flags"):
            any_flag = True
            st.markdown(f"**{r['label']}**")
            for f in r["flags"]:
                st.write("・⚠️ " + f)
    if not any_flag:
        st.success("上位設定に目立った過学習サインはありません（ただし将来は保証されません）。")

    _render_optimization_charts(ranked)
    st.caption("『過学習』とは、過去データに合わせすぎて将来通用しなくなること。"
               "トレード数が十分・前半後半とも安定・ランダムに勝つ・設定がシンプル、を満たすほど信頼できます。")


def _render_optimization_charts(ranked):
    st.subheader("📈 グラフ")
    g1 = st.columns(2)
    # ランキング棒
    topn = ranked[:10]
    fig = _go.Figure([_go.Bar(x=[r["label"][:18] for r in topn], y=[r["composite"] for r in topn],
                              marker_color="#0ea5e9")])
    fig.update_layout(title="総合スコア ランキング", height=300, margin=dict(l=10, r=10, t=40, b=80))
    g1[0].plotly_chart(fig, width='stretch')
    # 期待値 vs 最大DD 散布
    fig = _go.Figure([_go.Scatter(x=[r["max_dd"] for r in ranked], y=[r["expectancy"] for r in ranked],
                                  mode="markers+text", text=[r["label"][:8] for r in ranked],
                                  textposition="top center", marker=dict(size=10, color=[r["composite"] for r in ranked],
                                  colorscale="RdYlGn", showscale=True))])
    fig.update_layout(title="期待値 vs 最大DD（右上=DD浅く期待値高）", height=300,
                      xaxis_title="最大DD%", yaxis_title="期待値%", margin=dict(l=10, r=10, t=40, b=10))
    g1[1].plotly_chart(fig, width='stretch')

    g2 = st.columns(2)
    # PF vs トレード数
    fig = _go.Figure([_go.Scatter(x=[r["trades"] for r in ranked], y=[r["profit_factor"] for r in ranked],
                                  mode="markers", marker=dict(size=10, color="#16a34a"))])
    fig.update_layout(title="PF vs トレード数（多い×PF高が理想）", height=280,
                      xaxis_title="トレード数", yaxis_title="PF", margin=dict(l=10, r=10, t=40, b=10))
    g2[0].plotly_chart(fig, width='stretch')
    # 前半/後半比較
    topw = ranked[:8]
    fig = _go.Figure()
    fig.add_trace(_go.Bar(name="前半", x=[r["label"][:10] for r in topw], y=[r.get("wf_first") or 0 for r in topw], marker_color="#3b82f6"))
    fig.add_trace(_go.Bar(name="後半", x=[r["label"][:10] for r in topw], y=[r.get("wf_second") or 0 for r in topw], marker_color="#f59e0b"))
    fig.update_layout(title="前半 / 後半 平均リターン", barmode="group", height=280, margin=dict(l=10, r=10, t=40, b=70))
    g2[1].plotly_chart(fig, width='stretch')

    # 設定別 累積損益カーブ（上位3）
    curves = [r for r in ranked[:3] if r.get("equity_curve")]
    if curves:
        fig = _go.Figure()
        for r in curves:
            ec = r["equity_curve"]
            fig.add_trace(_go.Scatter(x=[c["date"] for c in ec], y=[c["equity"] for c in ec], name=r["label"][:16]))
        fig.update_layout(title="設定別 累積損益（上位3）", height=300, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width='stretch')

    # スコア条件(TOP5/10/20/BUY/WATCH)別 平均総合スコア
    from collections import defaultdict
    agg = defaultdict(list)
    for r in ranked:
        agg[r["condition"]].append(r["composite"])
    if agg:
        labels = list(agg.keys()); vals = [round(sum(v) / len(v), 1) for v in agg.values()]
        fig = _go.Figure([_go.Bar(x=labels, y=vals, marker_color="#8b5cf6")])
        fig.update_layout(title="スコア条件別 平均総合スコア（TOP5/10/20比較）", height=280, margin=dict(l=10, r=10, t=40, b=40))
        st.plotly_chart(fig, width='stretch')


# ================================================================== v11: ニュース精密解析
from modules import news_analysis as na_mod
from modules import engine as engine_mod2


def _na_scope_tickers(scope, positions, watchlist):
    if scope.startswith("保有"):
        return [p["ticker"].upper() for p in positions]
    if scope.startswith("ウォッチ"):
        return [t.upper() for t in watchlist]
    if scope.startswith("発掘"):
        c = disc_mod.load_cache()
        return [it["ticker"] for it in (c.get("top20", []) if c else [])][:12]
    # 今日のアクション = 保有 + ウォッチ
    return list(dict.fromkeys([p["ticker"].upper() for p in positions] + [t.upper() for t in watchlist]))


def _scn_color(k):
    return {"bull": "#16a34a", "neutral": "#eab308", "bear": "#dc2626"}[k]


def render_news_analysis(rmap, positions, watchlist, regime):
    st.header("🧠 ニュース精密解析")
    st.caption("ニュースが『今の保有/これから買う株』にどう効くかを、短期/中期/長期でシナリオ分析します。"
               "未来の断定ではなく“シナリオ予測”です。投資助言ではありません。")

    c = st.columns(2)
    scope = c[0].selectbox("対象", ["保有銘柄", "ウォッチリスト", "銘柄発掘TOP20", "今日のアクション（保有＋ウォッチ）"])
    window_label = c[1].selectbox("ニュース期間", ["直近24時間", "直近7日", "直近30日"], index=1)
    window_days = {"直近24時間": 1, "直近7日": 7, "直近30日": 30}[window_label]

    # マクロ現況（config の as-of 値を使用）
    macro_snap = dict(config.MACRO_FACTORS)

    tickers = _na_scope_tickers(scope, positions, watchlist)
    if not tickers:
        st.info("対象の銘柄がありません。保有登録・ウォッチ追加・発掘スキャンを行ってください。")
        return
    pos_by_t = {p["ticker"].upper(): p for p in positions}

    # rmapに無い銘柄（発掘TOP20など）はその場で分析（件数制限）
    local = dict(rmap)
    need = [t for t in tickers if t not in local]
    if need:
        with st.spinner(f"{len(need)}銘柄を解析中…"):
            for t in need[:12]:
                local[t] = engine_mod2.analyze_ticker(t, regime["score"], regime["regime"], regime["fomc_days"])

    results = na_mod.analyze_scope(tickers, local, pos_by_t, regime, window_days, macro_snap, cap=12)
    ok_results = [r for r in results if r.get("ok")]
    st.caption(f"対象 {len(tickers)}銘柄中 {len(ok_results)}銘柄を解析（最大12）。マクロ前提: "
               f"金利{macro_snap['米10年債利回り']['value']}・原油{macro_snap['原油(WTI)']['value']}・VIX{macro_snap['VIX']['value']}・FOMC {macro_snap['FOMC']['value']}。")

    # マクロ概況
    with st.expander("🌐 マクロ概況（CPI / FOMC / 金利 / 原油 / ドル / 地政学 / VIX）", expanded=False):
        st.dataframe(pd.DataFrame([{"要因": k, "現況": v["value"], "傾向": v["bias"], "メモ": v["note"]}
                                   for k, v in macro_snap.items()]), width='stretch', hide_index=True)

    if not ok_results:
        st.warning("解析可能なデータがありませんでした。"); return

    for r in ok_results:
        head = f"{'📦' if r['held'] else '🛒'} {r['ticker']}　${r['price']}　ニュース純影響 {r['net_impact']:+.1f}（{r['tone']}）"
        with st.expander(head, expanded=(r is ok_results[0])):
            st.markdown("**🧩 今日の結論**")
            st.info(r["conclusion"])

            cc = st.columns([1, 1])
            # ニュース要約
            with cc[0]:
                st.markdown("**📰 ニュース要約**")
                if r["categories"]:
                    st.caption("カテゴリ: " + " / ".join(f"{k}×{v}" for k, v in r["categories"].items()))
                if r["news"]:
                    for it in r["news"][:6]:
                        sign = "🟢" if it["impact"] > 0 else "🔴" if it["impact"] < 0 else "⚪"
                        st.write(f"{sign} [{it['fine_cat']}] {it['headline']}")
                        st.caption(f"　影響{it['impact']:+d}・信頼{it['confidence']}・{it['days_ago']}日前｜"
                                   f"短期{it['horizons']['今日〜3日']} / 中期{it['horizons']['1〜2週間']} / 長期{it['horizons']['1〜3ヶ月']}")
                else:
                    st.caption("該当期間のニュースなし（期間を広げてください）。")
            # 株価影響（織り込み）
            with cc[1]:
                st.markdown("**📊 株価影響 / 織り込み**")
                pe = r["priced_in"]
                st.write(f"織り込み判定: **{pe['label']}**")
                st.caption(f"直近の値動き {pe['move']:+.1f}%・ギャップ {pe['gap']:+.1f}%・出来高 {pe['vol_ratio']}x"
                           + ("・直近は戻り（過熱調整）" if pe["pullback"] else ""))
                st.caption("想定変動幅(±): " + " / ".join(f"{k} {v}%" for k, v in r["scenarios"]["expected_move_pct"].items()))

            # シナリオ
            st.markdown("**🔮 シナリオ予測（株価レンジ＋確率の目安）**")
            scn = r["scenarios"]
            sc = st.columns(3)
            for col, key in zip(sc, ["bull", "neutral", "bear"]):
                s = scn[key]; color = _scn_color(key)
                col.markdown(
                    f'<div style="border-left:5px solid {color};padding:6px 10px;background:{color}10;border-radius:6px;">'
                    f'<b style="color:{color};">{s["label"]} {s["prob"]}%</b><br>'
                    f'${s["range"][0]} 〜 ${s["range"][1]}<br>'
                    f'<span style="font-size:0.8rem;color:#64748b;">{s["desc"]}</span></div>', unsafe_allow_html=True)
            fig = _go.Figure([_go.Bar(x=["強気", "中立", "弱気"],
                                      y=[scn["bull"]["prob"], scn["neutral"]["prob"], scn["bear"]["prob"]],
                                      marker_color=["#16a34a", "#eab308", "#dc2626"])])
            fig.update_layout(height=180, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="確率%")
            st.plotly_chart(fig, width='stretch', key=f"scn_{r['ticker']}")

            # マクロ連動
            if r["macro"]:
                st.markdown("**🌐 この銘柄に効くマクロ**")
                st.caption(" ｜ ".join(f"{m['factor']}({m['value']}・{m['bias']})" for m in r["macro"]))

            # 判断 + Moomoo
            rec = r["recommendation"]
            badge = "#16a34a" if rec["action"] in ("継続保有", "今買ってよい", "買い増し検討") else \
                    "#dc2626" if rec["action"] in ("損切り", "買わない", "全利確", "危険") else "#eab308"
            st.markdown(f'**🧭 判断**：<span style="background:{badge};color:white;padding:2px 10px;border-radius:6px;">'
                        f'{rec["action"]}</span>　<span style="color:#64748b;">{rec["reason"]}</span>', unsafe_allow_html=True)
            st.success("📱 Moomooで今日やること: " + rec["todo"])

    st.caption("⚠️ 確率・レンジは過去の値動きとニュース傾向からの“目安”で、未来を保証しません。"
               "ニュース日付がない場合は便宜的に配分しています。最終判断はご自身で。")


# ================================================================== v12: 通知設定
from modules import notify as notify_mod


def render_notifications(regime, events, positions, watchlist):
    st.header("🔔 通知設定")
    st.caption("毎朝の発掘TOP3・保有アクション・重要ニュース・注文メモを1通にまとめて受け取れます。"
               "自動売買ではありません。発注はMoomooで手動。通知は判断補助です。")

    s = notify_mod.load_settings()

    # ---------- 設定フォーム ----------
    with st.form("notify_form"):
        c = st.columns(3)
        enabled = c[0].checkbox("通知ON", value=s.get("enabled", False))
        time_str = c[1].text_input("通知時刻 (HH:MM)", value=s.get("time", config.NOTIFY_DEFAULT_TIME))
        target = c[2].selectbox("通知先", config.NOTIFY_TARGETS,
                                index=config.NOTIFY_TARGETS.index(s.get("target", "未設定"))
                                if s.get("target") in config.NOTIFY_TARGETS else 0)
        st.caption("Discord Webhook が最も簡単です（サーバー設定→連携サービス→ウェブフックでURL作成）。")
        discord = st.text_input("Discord Webhook URL", value=s.get("discord_webhook", ""), type="password")
        tg = st.columns(2)
        tg_token = tg[0].text_input("Telegram Bot Token", value=s.get("telegram_token", ""), type="password")
        tg_chat = tg[1].text_input("Telegram Chat ID", value=s.get("telegram_chat", ""))
        em = st.columns(2)
        email_to = em[0].text_input("メール送信先", value=s.get("email_to", ""))
        smtp_host = em[1].text_input("SMTPホスト", value=s.get("smtp_host", ""))
        em2 = st.columns(3)
        smtp_port = em2[0].text_input("SMTPポート", value=str(s.get("smtp_port", 587)))
        smtp_user = em2[1].text_input("SMTPユーザー", value=s.get("smtp_user", ""))
        smtp_pass = em2[2].text_input("SMTPパスワード", value=s.get("smtp_pass", ""), type="password")
        saved = st.form_submit_button("💾 設定を保存", width='stretch')

    if saved:
        s = {"enabled": enabled, "time": time_str, "target": target, "discord_webhook": discord,
             "telegram_token": tg_token, "telegram_chat": tg_chat, "email_to": email_to,
             "smtp_host": smtp_host, "smtp_port": int(smtp_port) if str(smtp_port).isdigit() else 587,
             "smtp_user": smtp_user, "smtp_pass": smtp_pass}
        notify_mod.save_settings(s)
        st.success("設定を保存しました。")

    ready = notify_mod.target_ready(s)
    if ready:
        st.success(f"🟢 通知先『{s['target']}』は送信可能です。")
    else:
        st.warning("🟡 通知先が未設定です。上で通知先と接続情報を設定して保存してください（未設定でもアプリは動作します）。")

    # ---------- 通知作成 / 送信 ----------
    st.subheader("📨 通知の作成・送信")
    b = st.columns(3)
    make = b[0].button("📝 今すぐ通知を作成", width='stretch')
    test = b[1].button("✉️ テスト送信", width='stretch', disabled=not ready)
    send_now = b[2].button("🚀 この内容を送信", width='stretch', disabled=not ready)

    if make:
        with st.spinner("通知本文を作成中…（発掘キャッシュ＋保有を解析）"):
            analyze = lambda t: engine_mod2.analyze_ticker(t, regime["score"], regime["regime"], regime["fomc_days"])
            digest = notify_mod.build_digest(regime, events, positions, tuple(watchlist), analyze)
            st.session_state["_notify_digest"] = digest
        st.success("作成しました。下にプレビューを表示します。")

    if test:
        ok, err = notify_mod.send_test(s)
        notify_mod.log_history(s.get("target"), st.session_state.get("_notify_digest"), ok, err)
        (st.success if ok else st.error)(f"テスト送信: {'成功' if ok else '失敗'}" + (f"（{err}）" if err else ""))

    if send_now:
        digest = st.session_state.get("_notify_digest")
        if not digest:
            analyze = lambda t: engine_mod2.analyze_ticker(t, regime["score"], regime["regime"], regime["fomc_days"])
            digest = notify_mod.build_digest(regime, events, positions, tuple(watchlist), analyze)
            st.session_state["_notify_digest"] = digest
        ok, err = notify_mod.send(s, digest["text"], digest.get("markdown"))
        notify_mod.log_history(s.get("target"), digest, ok, err)
        (st.success if ok else st.error)(f"送信: {'成功' if ok else '失敗'}" + (f"（{err}）" if err else ""))

    # ---------- プレビュー ----------
    digest = st.session_state.get("_notify_digest")
    if digest:
        st.subheader("👀 プレビュー")
        st.code(digest["text"], language="text")

    # ---------- 履歴 ----------
    st.subheader("🧾 通知履歴")
    hist = notify_mod.load_history()
    if hist:
        st.dataframe(pd.DataFrame(hist[-20:][::-1]), width='stretch', height=280, hide_index=True)
    else:
        st.caption("まだ通知履歴はありません。")
    st.caption("⚠️ 通知は投資助言ではなく分析補助です。発注はMoomooでご自身が手動で行ってください。"
               "毎朝の自動送信は scripts/daily_notify.py を cron/launchd で実行（READMEに手順）。")


# ================================================================== v14: ホーム & カードUI
from modules import discovery as disc_home
from modules import portfolio as pf_home
from modules import holding_action as ha_home
from modules import strategy as strat_home


def _grade(score100):
    return ("A", "#16a34a") if score100 >= 80 else ("B", "#22c55e") if score100 >= 65 \
        else ("C", "#eab308") if score100 >= 45 else ("D", "#f97316")


def _mode_color(mode):
    return {"強気": "#16a34a", "やや強気": "#22c55e", "中立": "#64748b",
            "やや弱気": "#f97316", "弱気": "#dc2626"}.get(mode, "#64748b")


def _stars(score):
    n = 5 if score >= 85 else 4 if score >= 76 else 3 if score >= 68 else 2 if score >= 58 else 1
    return "★" * n + "☆" * (5 - n)


# v14.5: 保有アクション → スマホ向けの短いラベル（売る/継続/注意/利確/損切り）
_HOLD_LABEL = {
    "継続保有":     ("継続",   "#16a34a"),
    "一部利確":     ("利確",   "#0ea5e9"),
    "全利確":       ("売る",   "#06b6d4"),
    "損切り":       ("損切り", "#dc2626"),
    "危険":         ("注意",   "#b91c1c"),
    "買い増し禁止": ("注意",   "#f97316"),
    "ニュース確認": ("注意",   "#a855f7"),
}
# v14.5: ウォッチ判定 → 買う/待つ/見送り
_WATCH_LABEL = {"強いBUY": ("買う", "#15803d"), "BUY": ("買う", "#16a34a"),
                "WATCH": ("待つ", "#eab308"), "AVOID": ("見送り", "#dc2626")}


def _hold_label(action):
    return _HOLD_LABEL.get(action, ("継続", "#16a34a"))


def _watch_label(verdict):
    return _WATCH_LABEL.get(verdict, ("待つ", "#64748b"))


def _hold_overview(positions, rmap, regime):
    """全保有銘柄について {ticker, pl_pct, action, label, color, todo, attention} を返す。"""
    out = []
    for pos in positions:
        t = pos["ticker"].upper()
        ev = pf_home.evaluate_position(pf_home._normalize(pos), rmap.get(t))
        hd = ha_home.decide(ev, regime["regime"])
        label, color = _hold_label(hd["action"])
        ed = ev.get("earnings_days")
        attention = hd["action"] != "継続保有" or (ed is not None and 0 <= ed <= 14)
        out.append({"ticker": t, "pl_pct": ev.get("pl_pct"), "action": hd["action"],
                    "label": label, "color": color, "todo": ha_home.moomoo_todo(ev, hd),
                    "earnings_days": ed, "attention": attention})
    # 損切り→注意/利確/売る→継続 の順に上へ
    pri = {"損切り": 0, "注意": 1, "利確": 2, "売る": 3, "継続": 4}
    out.sort(key=lambda x: pri.get(x["label"], 9))
    return out


def render_home(regime, events, positions, rmap):
    score100 = int(round(regime.get("score", 5) * 10))
    grade, gcolor = _grade(score100)
    mode = regime.get("regime", "中立"); mcolor = _mode_color(mode)
    attack = mode in ("強気", "やや強気")
    cur_wl = wl_current()

    # ===== データ準備 =====
    holds = _hold_overview(positions, rmap, regime) if positions else []
    monitors = hm_mod.monitor_all(positions, rmap, regime) if positions else []  # v15: 保有ニュース監視
    week = events[0] if isinstance(events, tuple) else (events or [])
    week = [e for e in week if 0 <= e.get("days", 99) <= 7]
    cache = disc_home.load_cache()
    top3 = (cache.get("top20", [])[:3] if cache else [])

    # ===== 1. 今日やること（最上部・最重要） =====
    st.markdown("### ✅ 今日やること")
    todos = []  # (color, html)
    # v16: 目標達成プランの1行（保存済みの目標があれば）
    _goal_line = gp_mod.home_line(gp_mod.load_goal())
    if _goal_line:
        _gc = "#dc2626" if "非現実的" in _goal_line else "#0ea5e9"
        todos.append((_gc, _goal_line))
    # v15: 保有ニュース監視からの提案（利確/損切り価格つき・最大4件）
    for color, line in hm_mod.home_todos(monitors, limit=4):
        todos.append((color, line))
    if top3 and top3[0].get("verdict") in ("強いBUY", "BUY"):
        t0 = top3[0]
        todos.append(("#16a34a", f'<b>新規買い</b>：{t0["ticker"]} を ${t0.get("entry")} で指値（損切り ${t0.get("stop")}）'))
    elif attack:
        todos.append(("#0ea5e9", "<b>新規買い</b>：押し目なら買い検討（市場は強気）"))
    else:
        todos.append(("#64748b", "<b>新規買い</b>：今日は押し目待ち（無理に買わない）"))
    for e in week[:2]:
        todos.append(("#f97316", f'<b>{e["title"]}</b> を確認（あと{e["days"]}日）'))
    if not positions:
        todos.append(("#64748b", "📷 <b>Moomoo同期</b>で保有銘柄を登録してください"))
    if not cur_wl:
        todos.append(("#64748b", "🔎 <b>銘柄検索</b>または<b>おすすめ監視銘柄</b>からウォッチ追加してください"))
    with st.container(border=True):
        for i, (color, td) in enumerate(todos[:6], 1):
            st.markdown(f'<div style="border-left:4px solid {color};padding:3px 0 3px 10px;margin-bottom:4px;">'
                        f'<b>{i}.</b> {td}</div>', unsafe_allow_html=True)

    # ===== 2. 市場モード（コンパクトカード／スマホでも折り返して1画面に収まる） =====
    kpis = [
        ("投資スコア", str(score100), "/100", "#1d1d1f"),
        ("市場モード", f'{"🟢" if attack else "🟡" if mode=="中立" else "🔴"}{mode}', "", mcolor),
        ("総合評価", grade, "", gcolor),
        ("新規買い", f'{regime.get("new_buy_pct","—")}%', "上限", "#1d1d1f"),
        ("現金比率", f'{regime.get("cash_pct","—")}%', "推奨", "#1d1d1f"),
    ]
    chips = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 6px;">'
    for label, val, sub, color in kpis:
        chips += (f'<div style="flex:1 1 92px;min-width:92px;background:#f7f7fa;border-radius:14px;padding:8px 11px;">'
                  f'<div style="font-size:0.7rem;color:#8a8a8e;">{label}</div>'
                  f'<div style="font-size:1.2rem;font-weight:800;color:{color};line-height:1.25;">{val}'
                  f'<span style="font-size:0.68rem;color:#aaa;font-weight:600;"> {sub}</span></div></div>')
    chips += '</div>'
    st.markdown(chips, unsafe_allow_html=True)
    st.caption(("今日は攻めてよい日（押し目は買い検討）。" if attack else "守り気味の日。全力買いは控えて押し目待ち。"))
    sr = strat_home.resolved()
    if sr["active"]:
        st.caption("🧪 " + strat_home.label_line())

    # ===== 空状態（保有もウォッチも無い） =====
    if not positions and not cur_wl:
        with st.container(border=True):
            st.markdown("##### 💡 おすすめ監視銘柄から始める")
            render_suggested(key_prefix="homesug0", title="")
        with st.expander("ℹ️ このホームの見方・免責", expanded=False):
            _home_help()
        return

    # ===== 3. 保有中（売る/継続/注意/利確/損切り） =====
    if positions:
        st.markdown("##### 💼 保有中")
        for h in holds:
            with st.container(border=True):
                cc = st.columns([1.1, 1, 2.7])
                pl = h["pl_pct"]
                cc[0].markdown(f'<b style="font-size:1.05rem;">{h["ticker"]}</b>', unsafe_allow_html=True)
                cc[1].markdown((f'<span style="color:{"#16a34a" if (pl or 0)>=0 else "#dc2626"};font-weight:700;">{pl:+.1f}%</span>'
                                if pl is not None else "—"), unsafe_allow_html=True)
                cc[2].markdown(f'<span style="background:{h["color"]};color:white;padding:2px 9px;border-radius:8px;'
                               f'font-size:0.82rem;font-weight:700;">{h["label"]}</span> '
                               f'<span style="font-size:0.82rem;color:#555;">{h["todo"]}</span>', unsafe_allow_html=True)
    else:
        st.info("📷 保有銘柄が未登録です。『Moomoo同期』で取り込めます。")

    # ===== 4. 買い候補（ウォッチ：買う/待つ/見送り） =====
    if cur_wl:
        st.markdown("##### ⭐ 買い候補（ウォッチ）")
        chips = '<div style="display:flex;flex-wrap:wrap;gap:6px;">'
        for t in cur_wl:
            a = rmap.get(t)
            vd = (a.get("scores", {}).get("verdict") if a and a.get("ok") else None)
            label, color = _watch_label(vd)
            chips += (f'<span style="background:{color};color:white;border-radius:10px;padding:4px 11px;'
                      f'font-size:0.9rem;font-weight:700;">{t}・{label}</span>')
        chips += '</div>'
        st.markdown(chips, unsafe_allow_html=True)
    else:
        st.info("🔎 買い候補（ウォッチ）が未登録です。下のおすすめか、サイドバーの銘柄検索から追加できます。")

    # ===== 5. おすすめ監視銘柄（ウォッチ未登録なら） =====
    if not cur_wl:
        with st.container(border=True):
            render_suggested(key_prefix="homesug", title="💡 おすすめ監視銘柄（未登録・タップで追加）")

    # ===== 今日の買い候補 TOP3 =====
    st.markdown("##### 🛒 今日の買い候補 TOP3")
    medals = ["🥇", "🥈", "🥉"]
    if top3:
        cols = st.columns(len(top3))
        for col, it, md in zip(cols, top3, medals):
            vc = _disc_color(it["verdict"])
            with col:
                with st.container(border=True):
                    st.markdown(f'<div style="font-size:1.15rem;font-weight:800;">{md} {it["ticker"]}'
                                f'<span style="font-size:0.8rem;color:{vc};"> {it["verdict"]}</span></div>'
                                f'<div style="color:#8a8a8e;font-size:0.78rem;">{it.get("name","")}｜スコア {it["score"]}</div>',
                                unsafe_allow_html=True)
                    st.markdown(f'<div style="margin-top:4px;font-size:0.82rem;color:#16a34a;font-weight:700;">'
                                f'買い ${it.get("entry")}</div>', unsafe_allow_html=True)
    else:
        st.caption("発掘データなし。『🔍 銘柄発掘』でスキャンするとTOP3が出ます。")

    with st.expander("ℹ️ このホームの見方・免責", expanded=False):
        _home_help()


def _home_help():
    st.markdown("- **✅ 今日やること** … 最優先のアクション（上から順に対応）\n"
                "- **💼 保有中** … positions.json の銘柄。判定は 売る／継続／注意／利確／損切り\n"
                "- **⭐ 買い候補（ウォッチ）** … 自分で追加した監視銘柄。判定は 買う／待つ／見送り\n"
                "- **💡 おすすめ** … 未登録の提案銘柄（タップでウォッチ追加）")
    st.caption("⚠️ 教育・分析目的のツールです。投資助言ではありません。発注はMoomooでご自身が手動で。")


# ================================================================== v14.1: ウォッチリスト管理
from modules import search as search_mod
from modules import engine as engine_wl


# v15.3: ウォッチリストの永続化を一元化。
#   唯一の正  = settings.json（init_state で st.session_state["watchlist"] にロード）
#   session  = 表示・一時状態のみ（保存元にはしない）
#   追加/削除/手動編集/検索/発掘/おすすめ/個別分析 すべて set_watchlist 経由で保存。
def clean_tickers(items):
    """大文字化・重複統合・空文字除去した銘柄リストを返す。"""
    out = []
    for t in (items or []):
        u = str(t).strip().upper()
        if u and u not in out:
            out.append(u)
    return out


def wl_current():
    """ウォッチリストの唯一の正（settings.json 由来の session_state['watchlist']）。"""
    return list(st.session_state.get("watchlist", []))


def set_watchlist(tickers, msg=None, rerun=True):
    """ウォッチリスト更新の唯一の入口。session_state と settings.json を同時に更新する。"""
    merged = clean_tickers(tickers)
    st.session_state["watchlist"] = merged
    storage_mod.save_settings(merged, st.session_state.get("capital", 10000.0),
                              st.session_state.get("risk_pct", 2.0),
                              st.session_state.get("max_pos_pct", 20.0))
    if msg:
        st.session_state["_added_msg"] = msg
    if rerun:
        st.rerun()


def wl_add(tickers, label=None):
    cur = wl_current()
    add = tickers if isinstance(tickers, (list, tuple)) else [tickers]
    merged = clean_tickers(cur + list(add))
    if merged != cur:
        set_watchlist(merged, ("ok", clean_tickers(add)))


def wl_remove(ticker):
    cur = wl_current()
    u = str(ticker).strip().upper()
    merged = [t for t in cur if t != u]
    if merged != cur:
        set_watchlist(merged)


def wl_has(ticker):
    return str(ticker).strip().upper() in wl_current()


def render_suggested(key_prefix="sug", title="💡 おすすめ監視銘柄"):
    """おすすめ監視銘柄をカテゴリ別のチップ（ボタン）で表示。押した銘柄だけウォッチ追加。"""
    cur = set(wl_current())
    if title:
        st.markdown(f"**{title}**")
    st.caption("カテゴリ別の提案。自動登録はしません。タップした銘柄だけウォッチに入ります。")
    for group, tickers in config.SUGGESTED_WATCHLIST.items():
        st.markdown(f'<div style="font-size:0.8rem;color:#8a8a8e;margin:6px 0 2px;">{group}</div>',
                    unsafe_allow_html=True)
        ncol = min(4, max(1, len(tickers)))
        cols = st.columns(ncol)
        for i, t in enumerate(tickers):
            col = cols[i % ncol]
            name = config.TICKER_NAMES.get(t, t)
            if t in cur:
                col.button(f"✓ {t}", key=f"{key_prefix}_{t}", disabled=True,
                           width='stretch', help=f"{name}（登録済み）")
            elif col.button(f"＋ {t}", key=f"{key_prefix}_{t}",
                            width='stretch', help=f"{name} をウォッチに追加"):
                wl_add(t)


def render_sidebar_watchlist(positions, regime):
    """サイドバー：保有銘柄 / ウォッチリスト / おすすめ監視銘柄 を分けて表示。"""
    cur = wl_current()
    pos_tickers = [p["ticker"].upper() for p in positions]

    # --- 保有銘柄 ---
    st.subheader("💼 保有銘柄")
    if pos_tickers:
        st.markdown(" ".join(f'<span style="background:#e7f5ee;border-radius:8px;padding:2px 8px;margin:2px;">{t}</span>'
                             for t in pos_tickers), unsafe_allow_html=True)
        st.caption("「📷 Moomoo同期」で登録/更新します。")
    else:
        st.caption("未登録。「📷 Moomoo同期」で取り込めます。")

    st.divider()
    # --- ウォッチリスト ---
    st.subheader("⭐ ウォッチリスト")
    q = st.text_input("🔎 銘柄検索（ティッカー/企業名）", key="wl_search", placeholder="例: NVDA / nvidia")
    if q:
        results = search_mod.search(q, limit=6)
        if not results:
            st.caption("該当なし。『手動編集』でティッカーを直接追加できます。")
        for r in results:
            t = r["ticker"]
            with st.container(border=True):
                price = score = None
                try:
                    a = engine_wl.analyze_ticker(t, regime["score"], regime["regime"], regime["fomc_days"], with_news=False)
                    if a.get("ok"):
                        price = a["fund"].get("price"); score = a["scores"].get("total")
                except Exception:
                    pass
                st.markdown(f'**{t}** · {r["name"]}')
                meta = r["sector"] + (f' ・ ${price:.2f}' if price else "") + (f' ・ スコア{score:.0f}' if score is not None else "")
                st.caption(meta)
                if t in cur:
                    st.caption("✅ 登録済み")
                elif st.button("＋ ウォッチに追加", key=f"wladd_{t}", width='stretch'):
                    wl_add(t)

    st.caption(f"ウォッチ {len(cur)} / 保有 {len(pos_tickers)} 銘柄")
    if cur:
        for t in cur:
            cc = st.columns([3, 1])
            cc[0].markdown(f'<span style="background:#fff4e5;border-radius:8px;padding:3px 10px;">{t}</span>', unsafe_allow_html=True)
            if cc[1].button("×", key=f"wldel_{t}"):
                wl_remove(t)
    else:
        st.caption("まだ買い候補がありません。検索かおすすめから追加できます。")

    with st.expander("✏️ 手動編集（カンマ区切り）", expanded=False):
        # v15.3: 保存元(settings.json)は『更新』押下時のみ書き換える。
        # キーを現在のウォッチ内容に紐付け、常に最新を表示しつつ編集中の値は保持。
        seed = ", ".join(cur)
        edited = st.text_area("ティッカー（カンマ区切り）", value=seed, key=f"wl_edit_{seed}",
                              height=80, help="上級者向け。カンマ/改行区切りで直接編集→『この内容で更新』。")
        if st.button("この内容で更新", key="wl_manual_apply", width='stretch'):
            parsed = clean_tickers((edited or "").replace("\n", ",").split(","))
            if not parsed:
                # 要件4: 空のときは既存ウォッチを上書きしない
                st.info("空のため、既存のウォッチリストを保持しました（削除は × ボタンから）。")
            else:
                set_watchlist(parsed)

    st.divider()
    with st.expander("💡 おすすめ監視銘柄", expanded=not cur):
        render_suggested(key_prefix="sidesug", title="おすすめ監視銘柄")


def watchlist_toggle_button(ticker, key_prefix="wl"):
    """個別銘柄ページ用：表示中銘柄の追加/削除トグル。"""
    if wl_has(ticker):
        if st.button("★ ウォッチ登録済み（削除）", key=f"{key_prefix}_rm_{ticker}", width='stretch'):
            wl_remove(ticker)
    else:
        if st.button("☆ ウォッチリストに追加", key=f"{key_prefix}_add_{ticker}", width='stretch'):
            wl_add(ticker)


# ================================================================== v14.3: カード型ナビ
NAV_GROUPS = [
    ("⭐ よく使う", True, [
        ("🏠", "ホーム", "🏠 ホーム"), ("🎯", "目標達成プラン", "🎯 目標達成プラン"),
        ("🔎", "銘柄発掘", "🔍 銘柄発掘"),
        ("📋", "今日のアクション", "今日のアクション"), ("🗓", "保有ニュース監視", "🗓 保有ニュース監視"),
        ("📷", "Moomoo同期", "📷 Moomoo同期"), ("🔔", "通知設定", "🔔 通知設定"),
    ]),
    ("🎯 買う前", True, [
        ("🎯", "目標達成プラン", "🎯 目標達成プラン"),
        ("📊", "市場環境", "市場環境"), ("🔍", "銘柄発掘", "🔍 銘柄発掘"),
        ("🔬", "個別銘柄分析", "個別銘柄分析"), ("🧠", "ニュース精密解析", "🧠 ニュース精密解析"),
        ("📰", "ニュース分析", "ニュース分析"), ("🧭", "売買プラン", "売買プラン"),
    ]),
    ("💼 保有後", True, [
        ("📋", "今日のアクション", "今日のアクション"), ("🗓", "保有ニュース監視", "🗓 保有ニュース監視"),
        ("📒", "ポジション台帳", "ポジション台帳"),
        ("📷", "Moomoo同期", "📷 Moomoo同期"), ("🛡️", "ポートフォリオリスク", "ポートフォリオリスク"),
        ("🔔", "アラート", "アラート"), ("📝", "発注メモ", "発注メモ"),
        ("🗂️", "売買履歴", "売買履歴"), ("🪞", "反省AI", "反省AI"),
    ]),
    ("📈 検証", False, [
        ("📈", "発掘バックテスト", "📈 発掘バックテスト"), ("🧪", "ルール最適化", "🧪 ルール最適化"),
        ("⏱️", "バックテスト", "バックテスト"), ("⚖️", "リスク管理", "リスク管理"),
    ]),
    ("⚙️ 設定", False, [
        ("🔔", "通知設定", "🔔 通知設定"),
    ]),
]


def _nav_button(slot, icon, label, page_key):
    active = st.session_state.get("page", "🏠 ホーム") == page_key
    if st.button(f"{icon}　{label}", key=f"nav_{slot}_{page_key}", width='stretch',
                 type=("primary" if active else "secondary")):
        if not active:
            st.session_state["page"] = page_key
            st.rerun()


def render_nav():
    """スマホ向けカード型ナビ。st.session_state['page'] を更新。"""
    st.markdown('<div class="nav-wrap">', unsafe_allow_html=True)
    for gi, (title, expanded, items) in enumerate(NAV_GROUPS):
        with st.expander(title, expanded=expanded):
            for icon, label, page_key in items:
                _nav_button(gi, icon, label, page_key)
    st.markdown('</div>', unsafe_allow_html=True)
