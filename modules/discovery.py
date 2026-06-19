"""
discovery.py (v7) — 高速・安定の銘柄発掘エンジン。
並列取得 / 進捗コールバック / 差分キャッシュ / 部分更新 / 高速・精密モード /
スキャンログ / API制限対策（ニュースは一次通過の上位のみAI分析）。
"""
import time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from modules import engine, scoring, universe, storage
from modules import themes as themes_mod
from modules import data_fetch  # v19.1: SPY/QQQ取得（read-only呼び出しのみ・data_fetchは未編集）

W = config.DISCOVERY_WEIGHTS


# ============================ スコア部品（v6から継承） ============================
def _theme_fit(themes):
    weights = config.CURRENT_THEMES
    best, matched = 0.0, []
    for th in themes:
        for key, w in weights.items():
            if key == th or key in th:
                matched.append(key); best = max(best, w)
    return best, sorted(set(matched))


def _is_etf(themes):
    return any("ETF" in th for th in themes)


def _risk_subscore(r, df, plan):
    risk, pts = 1.0, []
    snap = r["snap"]; rsi = snap.get("rsi", 50)
    if rsi >= 75:
        risk -= 0.4; pts.append(f"RSI過熱({rsi:.0f})")
    if snap.get("vol_ratio", 1) < 0.5:
        risk -= 0.3; pts.append("出来高急減")
    try:
        c = df["Close"]; ret20 = c.iloc[-1] / c.iloc[-21] - 1
        vol = c.pct_change().tail(20).std()
        if ret20 > 0.40:
            risk -= 0.3; pts.append(f"短期急騰(+{ret20*100:.0f}%)")
        if vol and vol > 0.06:
            risk -= 0.2; pts.append("ボラ異常")
    except Exception:
        pass
    entry = plan.get("entry1"); stop = plan.get("stop"); avg = plan.get("avg_entry"); tp2 = plan.get("tp2")
    if entry and stop and (entry - stop) / entry > 0.13:
        risk -= 0.2; pts.append(f"損切り幅広い(-{(entry-stop)/entry*100:.0f}%)")
    if entry and stop and avg and (avg - stop) > 0 and tp2 and (tp2 - avg) / (avg - stop) < 1.5:
        risk -= 0.15; pts.append("リスクリワード低")
    return max(0.0, min(1.0, risk)), pts


def _hit_conditions(r, df):
    snap = r["snap"]; hits = []
    sma20 = snap.get("sma20"); sma50 = snap.get("sma50"); sma200 = snap.get("sma200")
    rsi = snap.get("rsi", 50); near_high = False; ret_recent = 0.0
    try:
        c = df["Close"]; cur = c.iloc[-1]
        near_high = cur >= df["High"].tail(252).max() * 0.92
        ret_recent = cur / df["High"].tail(20).max() - 1
    except Exception:
        pass
    if sma20 and sma50 and sma200 and sma20 > sma50 > sma200 and snap.get("vol_ratio", 1) >= 1.0:
        hits.append("モメンタム(20>50>200・出来高増)")
    if near_high:
        hits.append("52週高値圏")
    if snap.get("above_sma200") and -0.15 <= ret_recent <= -0.05 and 40 <= rsi <= 60:
        hits.append("押し目(一時的調整)")
    return hits


def _confidence(r):
    fund = r["fund"]; snap = r["snap"]; news = r["news"]
    has_price = (snap.get("price") or 0) > 0
    has_vol = (snap.get("vol_avg") or 0) > 0
    has_fund = (fund.get("pe") is not None) or (fund.get("eps_g") is not None)
    has_news = len(news.get("items", [])) > 0
    n = sum([has_price, has_vol, has_fund, has_news])
    real = (not fund.get("is_sample", False)) and (news.get("source") == "finnhub")
    notes = []
    if n >= 4 and real:
        return "高", notes
    if n >= 4:
        return "中", ["サンプル/簡易ニュースを含む"]
    if n == 3:
        return "中", notes
    if not has_fund:
        notes.append("ファンダ不足")
    if not has_news:
        notes.append("ニュース未取得")
    return "低", notes


def _news_driver(r):
    items = r["news"]["items"]
    if not items:
        return None
    top = max(items, key=lambda x: abs(x.get("impact", 0)))
    return {"headline": top.get("headline", ""), "impact": top.get("impact", 0),
            "category": top.get("category", "中立")}


def _tech_state(snap):
    return " / ".join(["200日線↑" if snap.get("above_sma200") else "200日線↓",
                       "50日線↑" if snap.get("above_sma50") else "50日線↓",
                       f"RSI {snap.get('rsi',50):.0f}",
                       "MACD↑" if snap.get("macd_hist", 0) > 0 else "MACD↓",
                       f"出来高{snap.get('vol_ratio',1):.1f}x"])


def _action(verdict, snap):
    rsi = snap.get("rsi", 50); above200 = snap.get("above_sma200", False)
    if verdict in ("強いBUY", "BUY") and rsi <= 68 and above200:
        return "今すぐ買い"
    if verdict in ("強いBUY", "BUY", "WATCH"):
        return "押し目待ち"
    return "見送り"


# ============================ v19: 相対順位（外部取得なし） ============================
def _ret60(df):
    """過去60営業日リターン。データ不足(61本未満)なら None。"""
    try:
        c = df["Close"].dropna()
        if len(c) >= 61:
            return float(c.iloc[-1] / c.iloc[-61] - 1)
    except Exception:
        pass
    return None


def _top_pct(rank, total):
    """上位パーセンタイル（1始まり順位→上位X%）。"""
    if not total:
        return None
    return max(1, int(round(rank / total * 100)))


def _attach_rankings(items, excluded, pool):
    """スキャン母集団 pool から セクター順位 / 市場パーセンタイル / 相対強度 / リーダー判定 を
    各 item に付与し、発掘理由を拡充する（外部取得なし・後処理のみ）。
    pool: [{ticker, sector, score, ret60, eps_g, rev_g, vol_ratio, news_impact}]"""
    n = len(pool)
    # 市場パーセンタイル（score 降順）
    score_rank = {p["ticker"]: i + 1 for i, p in enumerate(sorted(pool, key=lambda x: -x["score"]))}
    # 相対強度（60日リターン降順・取得できたものだけ）
    rs_pool = [p for p in pool if p.get("ret60") is not None]
    rs_rank = {p["ticker"]: i + 1 for i, p in enumerate(sorted(rs_pool, key=lambda x: -x["ret60"]))}
    n_rs = len(rs_pool)
    # セクター順位
    sectors = {}
    for p in pool:
        sectors.setdefault(p["sector"], []).append(p)
    sec_rank, sec_count = {}, {}
    for sec, lst in sectors.items():
        for i, p in enumerate(sorted(lst, key=lambda x: -x["score"])):
            sec_rank[p["ticker"]] = i + 1
        for p in lst:
            sec_count[p["ticker"]] = len(lst)
    meta = {p["ticker"]: p for p in pool}

    def attach(it):
        t = it["ticker"]
        it["market_pct"] = _top_pct(score_rank.get(t, n), n)
        it["sector_rank"] = sec_rank.get(t)
        it["sector_count"] = sec_count.get(t)
        it["rs_pct"] = _top_pct(rs_rank[t], n_rs) if t in rs_rank else None
        # リーダー判定（セクター内の相対位置）
        sr, sc = it.get("sector_rank"), it.get("sector_count") or 1
        if sr:
            ratio = sr / sc
            if sr == 1 or ratio <= 0.3:
                it["leader"] = "リーダー"
            elif ratio >= 0.7 or it.get("verdict") == "AVOID":
                it["leader"] = "弱い"
            else:
                it["leader"] = "フォロワー"
        else:
            it["leader"] = None
        # 発掘理由の拡充（条件に合うものだけ・重複回避）
        m = meta.get(t, {})
        extra = []
        if it.get("sector_rank") and it["sector_rank"] <= 3:
            extra.append(f"セクター{it['sector_rank']}位")
        if it.get("market_pct") is not None and it["market_pct"] <= 10:
            extra.append(f"市場上位{it['market_pct']}%")
        if it.get("rs_pct") is not None and it["rs_pct"] <= 20:
            extra.append(f"相対強度 上位{it['rs_pct']}%")
        vr = m.get("vol_ratio") or 0
        if vr >= 1.5:
            extra.append(f"出来高急増(×{vr:.1f})")
        eg = m.get("eps_g")
        if eg is not None and eg >= 0.15:
            extra.append(f"EPS成長(+{eg*100:.0f}%)")
        rg = m.get("rev_g")
        if rg is not None and rg >= 0.15:
            extra.append(f"売上成長(+{rg*100:.0f}%)")
        ni = m.get("news_impact") or 0
        if ni >= 1.0 and not any(str(x).startswith("好材料") for x in it.get("reasons", [])):
            extra.append(f"ニュース好感度(+{ni:.1f})")
        reasons = it.setdefault("reasons", [])
        for e in extra:
            if e not in reasons:
                reasons.append(e)

    for it in list(items) + list(excluded):
        attach(it)


# ============================ v19.1: SPY/QQQ 実比較（表示のみ） ============================
def _bench_ret60(ticker):
    """SPY/QQQ の60日リターン。実データ(yfinance)以外（mock/失敗）は None で比較しない。"""
    try:
        df, src = data_fetch.get_price_history(ticker, config.PRICE_PERIOD, config.PRICE_INTERVAL, use_cache=True)
        if src != "yfinance":
            return None
        return _ret60(df)
    except Exception:
        return None


def _attach_vs_benchmarks(items, excluded):
    """各 item に vs SPY / vs QQQ（60日超過リターン, %ポイント）を付与。
    ベンチが取れない（mock/失敗）場合は付与しない＝view側で非表示。"""
    spy = _bench_ret60("SPY")
    qqq = _bench_ret60("QQQ")
    if spy is None and qqq is None:
        return
    for it in list(items) + list(excluded):
        r = it.get("ret60")
        if r is None:
            continue
        if spy is not None:
            it["vs_spy"] = round((r - spy) * 100, 1)
        if qqq is not None:
            it["vs_qqq"] = round((r - qqq) * 100, 1)


# v19.2: テーマ→代表セクターETF（具体的セクターを優先。先頭ルールほど優先）
_SECTOR_ETF_RULES = [
    (("半導体", "AI半導体", "通信半導体", "ファウンドリ", "製造装置", "メモリ"), "SMH"),
    (("原子力", "ウラン"), "URA"),
    (("防衛", "地政学ヘッジ"), "ITA"),
    (("エネルギー", "原油ヘッジ"), "XLE"),
    (("公益", "電力", "発電設備", "AI電力"), "XLU"),
    (("ヘルスケア",), "XLV"),
    (("金融", "銀行"), "XLF"),
    (("小型株ETF", "小型株"), "IWM"),
    (("AI", "ソフトウェア", "データセンター", "インフラ"), "QQQ"),
]


def _etf_for(themes):
    """テーマ群から代表セクターETFを決定（具体的セクター優先）。該当なしは None。"""
    ts = themes or []
    for keys, etf in _SECTOR_ETF_RULES:
        if any(t in keys for t in ts):
            return etf
    return None


def _attach_vs_sector_etf(items, excluded):
    """各 item に vs セクターETF（60日超過リターン, %ポイント）を付与。表示のみ。
    QQQ/SPY 割当は vs SPY/QQQ と重複するため出さない。mock/失敗/None は非表示。"""
    memo = {}

    def etf_ret(sym):
        if sym not in memo:
            memo[sym] = _bench_ret60(sym)
        return memo[sym]

    for it in list(items) + list(excluded):
        etf = _etf_for(it.get("themes"))
        if not etf or etf in ("QQQ", "SPY"):  # 重複回避
            continue
        r = it.get("ret60")
        if r is None:
            continue
        er = etf_ret(etf)
        if er is None:  # mock/失敗 → 非表示
            continue
        it["etf"] = etf
        it["vs_etf"] = round((r - er) * 100, 1)


def sector_rotation(pass1):
    agg = {}
    for p in pass1:
        if p["is_etf"] or p["sector"] in ("未分類", "指数ETF"):
            continue
        agg.setdefault(p["sector"], []).append(p["tech_raw"] * 0.6 + p["supply_raw"] * 0.4)
    ranked = [{"sector": s, "strength": round(sum(v) / len(v) * 100, 1), "count": len(v)}
              for s, v in agg.items() if len(v) >= 2]
    ranked.sort(key=lambda x: -x["strength"])
    return ranked


# ============================ 並列スキャン ============================
def _parallel_analyze(tickers, ms, mode, fomc, with_news, progress_cb=None):
    results, fail = {}, []
    total = len(tickers); done = 0

    def work(t):
        last = None
        for _ in range(config.DISCOVERY_RETRY_COUNT + 1):
            r = engine.analyze_ticker(t, ms, mode, fomc, with_news=with_news, use_cache=False)
            if r.get("ok"):
                return r
            last = r
        return last

    if total == 0:
        return results, fail
    with ThreadPoolExecutor(max_workers=config.DISCOVERY_MAX_WORKERS) as ex:
        futs = {ex.submit(work, t): t for t in tickers}
        for fu in as_completed(futs):
            t = futs[fu]; done += 1
            try:
                r = fu.result(timeout=config.DISCOVERY_TIMEOUT_SEC)
            except Exception:
                r = {"ticker": t, "ok": False, "error": "timeout"}
            if r and r.get("ok"):
                results[t] = r
            else:
                fail.append(t)
            if progress_cb:
                progress_cb(done, total, len(fail), t)
    return results, fail


def _topup_news(results, ms, mode, topn, progress_cb=None):
    """高速モード: 一次通過の上位だけ後からニュースAI分析。"""
    ranked = sorted(results.values(), key=lambda r: -r["scores"]["total"])[:topn]
    calls = errors = 0
    api_errors = []
    if not ranked:
        return calls, errors, api_errors

    def work(r):
        engine.attach_news(r, ms, mode, use_cache=False)
        return r
    with ThreadPoolExecutor(max_workers=config.DISCOVERY_MAX_WORKERS) as ex:
        futs = [ex.submit(work, r) for r in ranked]
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result(timeout=config.DISCOVERY_TIMEOUT_SEC)
                calls += 1
                if r["news"].get("error"):
                    errors += 1; api_errors.append(f"{r['ticker']}: {r['news']['error']}")
            except Exception as e:
                errors += 1; api_errors.append(f"news timeout: {type(e).__name__}")
            if progress_cb:
                progress_cb(i, len(ranked), errors, "ニュース更新中")
    return calls, errors, api_errors


# ============================ 結果の組み立て ============================
def _finalize(results, sources, strat=None):
    from modules import strategy as strat_mod
    strat = strat or strat_mod.resolved()
    Wt = strat.get("weights", W)
    rsi_thr = strat.get("rsi_thr", 75)
    vol_filter = strat.get("vol_filter", "なし")
    pass1 = []
    for t, r in results.items():
        fund = r["fund"]; snap = r["snap"]; df = r["df"]; plan = r["plan"]; ns = r["news_sum"]
        price = fund.get("price", 0) or 0; mcap = fund.get("mcap") or 0; avgvol = snap.get("vol_avg", 0) or 0
        themes = themes_mod.classify(t, fund, r["news"]["items"])
        tfit, matched = _theme_fit(themes)
        screen_fail = []
        if price < config.DISCOVERY_MIN_PRICE:
            screen_fail.append("株価$5未満")
        if mcap and mcap < config.DISCOVERY_MIN_MCAP:
            screen_fail.append("時価総額$1B未満")
        if avgvol and avgvol < config.DISCOVERY_MIN_AVGVOL:
            screen_fail.append("出来高50万株未満")
        excl = []
        e_days = plan.get("earnings_days")
        if e_days is not None and 0 <= e_days <= 7:
            excl.append(f"決算{e_days}日以内")
        if ns.get("categories", {}).get("危険", 0) > 0 or ns.get("avg_impact", 0) <= -2.5:
            excl.append("明確な悪材料")
        if snap.get("rsi", 50) >= rsi_thr:
            excl.append(f"RSI過熱({snap.get('rsi',50):.0f}≧{rsi_thr})")
        if snap.get("vol_ratio", 1) < 0.5:
            excl.append("出来高急減")
        # 採用ルールの出来高条件
        if vol_filter != "なし" and snap.get("vol_ratio", 1) < 1.0:
            excl.append("出来高条件未達(採用ルール)")
        risk_raw, risk_pts = _risk_subscore(r, df, plan)
        conf, conf_notes = _confidence(r)
        pass1.append({
            "ticker": t, "name": fund.get("name", t), "sector": themes[0] if themes else "未分類",
            "themes": themes, "is_etf": _is_etf(themes), "tfit": tfit, "matched": matched,
            "tech_raw": scoring._technical_raw(snap)[0], "news_raw": scoring._news_raw(ns)[0],
            "fund_raw": scoring._fundamental_raw(fund)[0], "supply_raw": scoring._supply_raw(snap)[0],
            "risk_raw": risk_raw, "risk_pts": risk_pts, "price": round(price, 2), "plan": plan,
            "snap": snap, "ns": ns, "hits": _hit_conditions(r, df), "news_driver": _news_driver(r),
            "tech_state": _tech_state(snap), "confidence": conf, "conf_notes": conf_notes,
            "sources": sources.get(t, []), "screen_fail": screen_fail, "excl": excl, "earnings_days": e_days,
            # v19: 相対順位用（外部取得なし）
            "ret60": _ret60(df), "eps_g": fund.get("eps_g"), "rev_g": fund.get("rev_g"),
            "vol_ratio": snap.get("vol_ratio"),
        })

    rotation = sector_rotation([p for p in pass1 if not (p["screen_fail"] or p["excl"])])
    top3_secs = [x["sector"] for x in rotation[:3]]
    top5_secs = [x["sector"] for x in rotation[:5]]

    items, excluded = [], []
    rank_pool = []  # v19: 順位計算の母集団（全pass1）
    for p in pass1:
        rot = 1.0 if p["sector"] in top3_secs else 0.6 if p["sector"] in top5_secs else 0.3
        theme_comp = round((0.7 * p["tfit"] + 0.3 * rot) * Wt["theme"], 1)
        tech = round(p["tech_raw"] * Wt["technical"], 1); news = round(p["news_raw"] * Wt["news"], 1)
        funda = round(p["fund_raw"] * Wt["fundamental"], 1); supply = round(p["supply_raw"] * Wt["supply"], 1)
        risk = round(p["risk_raw"] * Wt["risk"], 1)
        score = round(tech + news + funda + theme_comp + supply + risk, 1)
        rank_pool.append({"ticker": p["ticker"], "sector": p["sector"], "score": score,
                          "ret60": p.get("ret60"), "eps_g": p.get("eps_g"), "rev_g": p.get("rev_g"),
                          "vol_ratio": p.get("vol_ratio"), "news_impact": p["ns"].get("avg_impact", 0)})
        verdict = ("強いBUY" if score >= config.DISCOVERY_BUY_STRONG else "BUY" if score >= config.DISCOVERY_BUY
                   else "WATCH" if score >= config.DISCOVERY_WATCH else "AVOID")
        if p["confidence"] == "低" and verdict in ("強いBUY", "BUY"):
            verdict = "WATCH"
        reasons = list(p["hits"])
        if p["matched"]:
            reasons.append("テーマ適合: " + "/".join(p["matched"][:3]))
        if p["sector"] in top3_secs:
            reasons.append(f"強いセクター({p['sector']})")
        if p["ns"].get("avg_impact", 0) >= 1.5:
            reasons.append(f"好材料(影響{p['ns']['avg_impact']:+.1f})")
        if not reasons:
            reasons.append("総合バランス型")
        plan = p["plan"]
        item = {
            "ticker": p["ticker"], "name": p["name"], "sector": p["sector"], "themes": p["themes"],
            "sources": p["sources"], "confidence": p["confidence"], "price": p["price"],
            "score": score, "verdict": verdict, "action": _action(verdict, p["snap"]),
            "breakdown": {"technical": tech, "news": news, "fundamental": funda,
                          "theme": theme_comp, "supply": supply, "risk": risk},
            "reasons": reasons, "matched_themes": p["matched"], "news_impact": p["ns"].get("avg_impact", 0),
            "news_driver": p["news_driver"], "tech_state": p["tech_state"], "entry": plan.get("entry1"),
            "stop": plan.get("stop"), "tp1": plan.get("tp1"), "tp2": plan.get("tp2"),
            "hold": plan.get("hold", "数週間〜数ヶ月"),
            "risk_points": (p["excl"] + p["risk_pts"]) or ["特になし"],
            "ret60": p.get("ret60"),  # v19.1: vs SPY/QQQ 計算用
        }
        if p["screen_fail"] or p["excl"]:
            item["excluded"] = True; item["exclude_reasons"] = p["screen_fail"] + p["excl"]
            excluded.append(item)
        else:
            item["excluded"] = False; items.append(item)

    _attach_rankings(items, excluded, rank_pool)  # v19: 相対順位を付与＋理由拡充
    items.sort(key=lambda x: -x["score"]); excluded.sort(key=lambda x: -x["score"])
    return items[:20], excluded[:12], rotation


# ============================ スキャン本体 ============================
def _tickers_for_scope(scope, universe_mode, watchlist, prev):
    if scope == "watchlist":
        ts = [t.upper() for t in watchlist]
        return ts, {t: ["ウォッチ"] for t in ts}
    if scope == "top20" and prev:
        ts = [it["ticker"] for it in prev.get("top20", [])]
        return ts, {it["ticker"]: it.get("sources", []) for it in prev.get("top20", [])}
    if scope == "news" and prev:
        ts = [it["ticker"] for it in prev.get("top20", [])]
        return ts, {it["ticker"]: it.get("sources", []) for it in prev.get("top20", [])}
    if scope == "failed" and prev:
        base = list(prev.get("failed", [])) + [it["ticker"] for it in prev.get("top20", [])]
        ts = list(dict.fromkeys(base))
        src = {it["ticker"]: it.get("sources", []) for it in prev.get("top20", [])}
        return ts, src
    # full
    tickers, sources, meta = universe.build_universe(universe_mode, watchlist)
    return tickers, sources


def scan(scope, universe_mode, analysis_mode, market_score, market_mode="中立",
         fomc_days=None, watchlist=(), progress_cb=None, prev=None):
    start = dt.datetime.now()
    t0 = time.time()
    precise = "精密" in analysis_mode

    tickers, sources = _tickers_for_scope(scope, universe_mode, watchlist, prev)
    total = len(tickers)

    # ニュースの取り方: 精密 or 小規模 or news/top20スコープ → 全件with_news。高速&大規模 → 後でtop-up
    full_news = precise or scope in ("news", "top20", "watchlist") or total <= config.DISCOVERY_NEWS_TOPN
    results, fail = _parallel_analyze(tickers, market_score, market_mode, fomc_days,
                                      with_news=full_news, progress_cb=progress_cb)

    news_calls = sum(1 for r in results.values() if r.get("with_news")) if full_news else 0
    api_errors_list = [f"{t}: {r['news']['error']}" for t, r in results.items()
                       if r.get("with_news") and r["news"].get("error")]
    if not full_news:
        nc, ne, errs = _topup_news(results, market_score, market_mode, config.DISCOVERY_NEWS_TOPN, progress_cb)
        news_calls += nc; api_errors_list += errs

    from modules import strategy as strat_mod
    strat = strat_mod.resolved()
    top20, excluded, rotation = _finalize(results, sources, strat)
    _attach_vs_benchmarks(top20, excluded)  # v19.1: SPY/QQQ実比較（scan内で1回・表示のみ）
    _attach_vs_sector_etf(top20, excluded)  # v19.2: セクターETF実比較（表示のみ）
    end = dt.datetime.now(); dur = round(time.time() - t0, 1)

    stats = {"loaded": total, "found": total, "capped": False, "cap": config.SCAN_MODES.get(universe_mode, 0),
             "screened": len(top20) + max(0, len([1 for _ in results]) - len(top20) - len(excluded)) + len(top20),
             "screened_total": len(results) - len(excluded), "excluded": len(excluded),
             "fail": len(fail), "mode": universe_mode}
    # screened を分かりやすく再計算
    stats["screened"] = len(results) - len(excluded)

    api = {"news_calls": news_calls, "news_errors": len(api_errors_list), "errors": api_errors_list[:10]}
    result = {
        "top20": top20, "top3": top20[:3], "excluded": excluded, "rotation": rotation,
        "stats": stats, "failed": fail, "api": api,
        "timestamp": start.isoformat(timespec="seconds"),
        "universe_mode": universe_mode, "analysis_mode": analysis_mode, "scope": scope,
        "duration_sec": dur,
        "strategy_label": strat["label"], "strategy_active": strat["active"],
        "strategy_top_n": strat.get("top_n"),
    }
    save_cache(result)
    append_run_log({
        "start": start.strftime("%Y-%m-%d %H:%M:%S"), "end": end.strftime("%H:%M:%S"),
        "duration_sec": dur, "universe_mode": universe_mode, "analysis_mode": analysis_mode,
        "scope": scope, "target": total, "success": len(results), "fail": len(fail),
        "excluded": len(excluded), "top20": len(top20), "api_errors": len(api_errors_list),
    })
    return result


# ============================ キャッシュ / ログ ============================
def save_cache(result):
    slim = {k: result[k] for k in ("top20", "top3", "excluded", "rotation", "stats", "failed",
                                   "api", "timestamp", "universe_mode", "analysis_mode", "scope", "duration_sec",
                                   "strategy_label", "strategy_active", "strategy_top_n")}
    storage.save_json(config.DISCOVERY_CACHE_PATH, slim)


def load_cache():
    return storage.load_json(config.DISCOVERY_CACHE_PATH, None)


def cache_age_hours(cache):
    try:
        ts = dt.datetime.fromisoformat(cache["timestamp"])
        return (dt.datetime.now() - ts).total_seconds() / 3600.0
    except Exception:
        return None


def is_fresh(cache):
    age = cache_age_hours(cache) if cache else None
    return age is not None and age <= config.DISCOVERY_CACHE_FRESH_HOURS


RUN_FIELDS = ["start", "end", "duration_sec", "universe_mode", "analysis_mode", "scope",
              "target", "success", "fail", "excluded", "top20", "api_errors"]


def append_run_log(row):
    rows = storage.load_csv(config.DISCOVERY_RUNS_PATH, RUN_FIELDS)
    rows.append(row)
    storage.save_csv(config.DISCOVERY_RUNS_PATH, RUN_FIELDS, rows[-50:])


def load_run_log():
    return storage.load_csv(config.DISCOVERY_RUNS_PATH, RUN_FIELDS)


def today_line(item):
    v = item["verdict"]
    if v in ("強いBUY", "BUY"):
        return f"{item['ticker']}：第1エントリー ${item['entry']} 付近を資金の1/3だけ。損切り ${item['stop']} を必ず設定。"
    if v == "WATCH":
        return f"{item['ticker']}：${item['entry']} 以下まで待つ。条件が整えば打診買い。"
    return f"{item['ticker']}：今は見送り。ウォッチのみ。"
