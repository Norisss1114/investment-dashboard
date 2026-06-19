"""
discovery_backtest.py (v8) — 発掘ルールの過去検証（バックテスト）。
過去の各検証日に、その時点(PIT)のデータだけで発掘スコアを計算→候補選定→
その後の値動きでエントリー/損切り/利確をシミュレートし、成績を集計する。
※ニュース/現在ファンダは未来情報のためPITスコアから除外（v9以降で過去ニュース対応）。
※過去検証であり将来の利益を保証しません。
"""
import time
import datetime as dt
import numpy as np
import pandas as pd

import config
from modules import indicators, scoring, universe, storage, data_fetch, mock_data
from modules import themes as themes_mod

W = config.BACKTEST_WEIGHTS


# ---------------- データ取得（バックテスト用・十分な長さ） ----------------
def _history(ticker, total_bars):
    if data_fetch.is_mock_mode():
        return mock_data.mock_price_history(ticker, days=int(total_bars))
    try:
        import yfinance as yf
        years = max(1, int(np.ceil(total_bars / 252)) + 1)
        df = yf.Ticker(ticker).history(period=f"{years}y", interval="1d", auto_adjust=False)
        if df is not None and len(df) > 210:
            return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception:
        pass
    return mock_data.mock_price_history(ticker, days=int(total_bars))


def _prep(df):
    d = indicators.add_all_indicators(df).copy()
    d["RET60"] = d["Close"] / d["Close"].shift(60) - 1.0
    return d


def _snap_at(d, i):
    row = d.iloc[i]
    price = float(row["Close"])
    vol = float(row.get("Volume", 0) or 0)
    vsma = float(row.get("VOL_SMA20", vol) or vol)
    def f(x, dv):
        try:
            return float(x) if x == x else dv
        except Exception:
            return dv
    sma200 = f(row.get("SMA200"), price)
    sma50 = f(row.get("SMA50"), price)
    sma20 = f(row.get("SMA20"), price)
    return {"price": price, "sma20": sma20, "sma50": sma50, "sma200": sma200,
            "rsi": f(row.get("RSI"), 50.0), "macd": f(row.get("MACD"), 0.0),
            "macd_hist": f(row.get("MACD_HIST"), 0.0),
            "vol_ratio": (vol / vsma) if vsma else 1.0,
            "above_sma200": price > sma200, "above_sma50": price > sma50, "above_sma20": price > sma20}


# ---------------- v18: 取引コスト（往復・%） ----------------
def _round_trip_cost():
    """往復コスト(%) = 2 × (手数料 + スリッページ)。設定が無ければ0。"""
    fee = getattr(config, "BACKTEST_FEE_PCT", 0.0) or 0.0
    slip = getattr(config, "BACKTEST_SLIPPAGE_PCT", 0.0) or 0.0
    return 2.0 * (float(fee) + float(slip))


# ---------------- トレードのシミュレーション ----------------
def _simulate(d, entry_idx, hold, entry_price, take_rule, stop_rule):
    n = len(d)
    stop = entry_price * (1 - config.BACKTEST_STOP_PCT / 100)
    tp1 = entry_price * (1 + config.BACKTEST_TP1_PCT / 100)
    tp2 = entry_price * (1 + config.BACKTEST_TP2_PCT / 100)
    use_tp1 = take_rule.startswith("利確1")
    use_tp2 = "利確2" in take_rule or take_rule == "利確2で全売り"
    half_done = False
    highest = entry_price
    end_idx = min(entry_idx + hold, n - 1)

    for j in range(entry_idx + 1, end_idx + 1):
        hi = float(d["High"].iloc[j]); lo = float(d["Low"].iloc[j]); cl = float(d["Close"].iloc[j])
        highest = max(highest, hi)
        cur_stop = stop
        reason_stop = "損切り"
        if stop_rule == "トレーリングストップ":
            cur_stop = max(stop, highest * (1 - config.BACKTEST_TRAIL_PCT / 100))
            reason_stop = "トレーリング"
        # 損切り判定
        if lo <= cur_stop:
            ret = (0.5 * (tp1 / entry_price - 1) + 0.5 * (cur_stop / entry_price - 1)) if half_done \
                else (cur_stop / entry_price - 1)
            return ret * 100, j - entry_idx, reason_stop
        # 20日線割れ
        if stop_rule == "20日線割れ撤退":
            sma20 = d["SMA20"].iloc[j]
            if sma20 == sma20 and cl < float(sma20):
                ret = (0.5 * (tp1 / entry_price - 1) + 0.5 * (cl / entry_price - 1)) if half_done \
                    else (cl / entry_price - 1)
                return ret * 100, j - entry_idx, "20日線割れ"
        # 利確1（半分）
        if use_tp1 and not half_done and hi >= tp1:
            half_done = True
        # 利確2（全/残り）
        if (use_tp2 or use_tp1) and hi >= tp2:
            ret = (0.5 * (tp1 / entry_price - 1) + 0.5 * (tp2 / entry_price - 1)) if half_done \
                else (tp2 / entry_price - 1)
            return ret * 100, j - entry_idx, "利確2"
    # 期間終了
    cl = float(d["Close"].iloc[end_idx])
    ret = (0.5 * (tp1 / entry_price - 1) + 0.5 * (cl / entry_price - 1)) if half_done else (cl / entry_price - 1)
    return ret * 100, end_idx - entry_idx, "期間終了"


# ---------------- v24: 改善案検証（PIT派生フィルター・検証専用） ----------------
# プリセット（discovery_backtest 内に固定。本番スコア/ランキングには一切影響しない）
IMPROVEMENT_FILTERS = [
    ("相対強度 上位10%", {"rs_max": 10}),
    ("相対強度 上位25%", {"rs_max": 25}),
    ("スコア80以上", {"score_min": 80}),
    ("スコア85以上", {"score_min": 85}),
    ("スコア90以上", {"score_min": 90}),
    ("市場上位10%", {"market_pct_max": 10}),
    ("市場上位25%", {"market_pct_max": 25}),
    ("セクター中立 +0以上", {"sector_neutral_min": 0}),
    ("セクター中立 +5以上", {"sector_neutral_min": 5}),
    ("リーダーのみ", {"leader_only": True}),
]


def _pit_metrics(scored, rser, theme_fit):
    """その検証日の横断情報だけから market_pct / rs_pct / sector_neutral / leader を算出（PIT）。
    scored=[(t,sc,vd,snap,rank)...]。未来情報・ニュース・現在ファンダは一切使わない。"""
    total = len(scored) or 1
    sec_scores = {}
    for (t, sc, vd, snap, rank) in scored:
        sec_scores.setdefault(theme_fit[t][1], []).append((t, sc))
    sec_mean, sec_rank, sec_cnt = {}, {}, {}
    for s, lst in sec_scores.items():
        sec_mean[s] = sum(x[1] for x in lst) / len(lst)
        for r2, (tt, _s) in enumerate(sorted(lst, key=lambda x: -x[1]), 1):
            sec_rank[tt] = r2
        for (tt, _s) in lst:
            sec_cnt[tt] = len(lst)
    pit = {}
    for (t, sc, vd, snap, rank) in scored:
        sec = theme_fit[t][1]
        scnt = sec_cnt.get(t, 1) or 1
        sr = sec_rank.get(t, scnt)
        ratio = sr / scnt
        leader = "リーダー" if (sr == 1 or ratio <= 0.3) else ("弱い" if ratio >= 0.7 else "フォロワー")
        pit[t] = {
            "market_pct": max(1, round(rank / total * 100)),
            "rs_pct": max(1, round((1 - float(rser[t])) * 100)),
            "sector_neutral": round(sc - sec_mean.get(sec, sc), 1),
            "leader": leader,
        }
    return pit


def _window_metrics(trades, random_trades, bench, common, period_bars):
    """検証窓のトレード群から比較用メトリクスを算出（v18同様にコスト反映）。"""
    pnls = [t["pnl_pct"] for t in trades]
    m = _metrics(pnls)
    _, _, max_dd, total_ret = _equity_and_dd(sorted(trades, key=lambda x: x["date"])) if trades else ([], [], 0.0, 0.0)
    _, _, _, rand_total = _equity_and_dd(sorted(random_trades, key=lambda x: x["date"])) if random_trades else ([], [], 0.0, 0.0)
    sharpe = round(float(np.mean(pnls) / np.std(pnls)), 2) if len(pnls) >= 2 and np.std(pnls) else 0.0
    rt = _round_trip_cost()
    spy = _benchmark_return(bench.get("SPY"), common, period_bars)
    qqq = _benchmark_return(bench.get("QQQ"), common, period_bars)
    spy = round(spy - rt, 1) if spy is not None else None
    qqq = round(qqq - rt, 1) if qqq is not None else None
    return {
        "trade_count": m.get("trades", 0), "win_rate": m.get("win_rate", 0.0),
        "avg_return": m.get("expectancy", 0.0), "total_return": round(total_ret, 1),
        "sharpe": sharpe, "max_drawdown": round(max_dd, 1),
        "vs_spy": (round(total_ret - spy, 1) if spy is not None else None),
        "vs_qqq": (round(total_ret - qqq, 1) if qqq is not None else None),
        "vs_random": round(total_ret - rand_total, 1),
    }


def _improve_verdict(base, var):
    """改善/悪化/変化なし/サンプル不足。主判定=total_return、補助=trade_count。"""
    if var["trade_count"] < 10:
        return "サンプル不足"
    dr = var["total_return"] - base["total_return"]
    if dr >= 3 and var["trade_count"] >= max(1, base["trade_count"] * 0.3):
        return "改善"
    if dr <= -3:
        return "悪化"
    return "変化なし"


def compare_improvement(params, filters=None, progress_cb=None):
    """通常バックテスト vs 改善案（PITフィルター）バックテストを比較（検証専用・本番非変更）。
    データは1回だけ取得し、ベースライン＋各フィルターを同一データで評価する。"""
    filters = filters if filters is not None else IMPROVEMENT_FILTERS
    dv = _derive_params(params)
    hold = dv["hold"]
    period_m = config.BACKTEST_PERIODS[params["period"]]
    period_bars = period_m * 21
    total_bars = 210 + period_bars + hold + 10

    tickers, sources, meta = universe.build_universe(params["universe_mode"], params.get("watchlist", ()))
    tickers = tickers[:config.BACKTEST_MAX_TICKERS]
    data, fail = {}, []
    for t in tickers:
        try:
            d = _prep(_history(t, total_bars))
            if len(d) >= 230:
                data[t] = d
            else:
                fail.append(t)
        except Exception:
            fail.append(t)
    if not data:
        return {"ok": False, "reason": "有効データなし", "fail": fail}
    common = None
    for d in data.values():
        common = d.index if common is None else common.intersection(d.index)
    common = common.sort_values()
    for t in list(data.keys()):
        data[t] = data[t].reindex(common).dropna(subset=["Close"])
    L = len(common)
    if L < 230:
        return {"ok": False, "reason": "履歴期間が不足", "fail": fail}

    theme_fit = {}
    for t in data:
        th = themes_mod.classify(t)
        wt = 0.0
        for x in th:
            for key, val in config.CURRENT_THEMES.items():
                if key == x or key in x:
                    wt = max(wt, val)
        theme_fit[t] = (wt, th[0] if th else "未分類", th)

    bench = {}
    for b in ("SPY", "QQQ"):
        try:
            bench[b] = _history(b, total_bars).reindex(common).dropna(subset=["Close"])
        except Exception:
            bench[b] = None

    first = max(205, L - period_bars)
    last = L - hold - 2
    test_idx = list(range(first, last + 1, config.BACKTEST_SAMPLE_EVERY))

    bt, br = _eval_window(data, common, L, test_idx, theme_fit, dv, progress_cb, phase="ベースライン")
    base = _window_metrics(bt, br, bench, common, period_bars)

    variants = []
    for k, (name, spec) in enumerate(filters, 1):
        ft, fr = _eval_window(data, common, L, test_idx, theme_fit, dv, progress_cb,
                              phase=f"検証 {name}", post_filter=spec)
        vm = _window_metrics(ft, fr, bench, common, period_bars)
        vm["name"] = name
        vm["filter"] = spec
        vm["verdict"] = _improve_verdict(base, vm)
        variants.append(vm)

    return {"ok": True, "baseline": base, "variants": variants, "fail": fail, "params": params}


# ---------------- v20: 1検証窓の評価（run_backtest / walk_forward 共通） ----------------
def _eval_window(data, common, L, test_idx, theme_fit, dv, progress_cb=None, phase="検証", post_filter=None):
    """指定 test_idx（検証日インデックス群）でトレード＋ランダム比較を生成して返す。
    ロジックは従来 run_backtest の内側ループと同一。
    v24: post_filter（PIT派生のフィルター辞書）が指定された時だけ picks をサブセット化（検証専用）。
    post_filter=None なら従来と完全に同一の出力（回帰なし）。"""
    Wt = dv["Wt"]; rsi_thr = dv["rsi_thr"]; vol_cond = dv["vol_cond"]; cond = dv["cond"]
    entry_method = dv["entry_method"]; hold = dv["hold"]; take_rule = dv["take_rule"]
    stop_rule = dv["stop_rule"]; rt_cost = dv["rt_cost"]
    trades = []
    rng = np.random.default_rng(42)
    random_trades = []

    for n_done, i in enumerate(test_idx, 1):
        rets = {t: (data[t]["RET60"].iloc[i]) for t in data}
        valid = [t for t in data if rets[t] == rets[t]]
        if not valid:
            continue
        rser = pd.Series({t: rets[t] for t in valid}).rank(pct=True)
        scored = []
        for t in valid:
            snap = _snap_at(data[t], i)
            if snap.get("rsi", 50) >= rsi_thr:
                continue
            if vol_cond != "なし":
                vol_i = float(data[t]["Volume"].iloc[i] or 0)
                win = 20 if "20" in vol_cond else 50
                vavg = float(data[t]["Volume"].iloc[max(0, i - win + 1):i + 1].mean() or 0)
                if not (vavg > 0 and vol_i >= vavg):
                    continue
            tech = scoring._technical_raw(snap)[0]
            supply = scoring._supply_raw(snap)[0]
            rs = float(rser[t])
            tfit = theme_fit[t][0]
            score = tech * Wt["technical"] + rs * Wt["relative_strength"] + tfit * Wt["theme"] + supply * Wt["supply"]
            verdict = "BUY" if score >= 70 else "WATCH" if score >= 55 else "AVOID"
            scored.append((t, round(score, 1), verdict, snap))
        scored.sort(key=lambda x: -x[1])
        for rank, (t, sc, vd, snap) in enumerate(scored, 1):
            scored[rank - 1] = (t, sc, vd, snap, rank)

        def passes(item):
            t, sc, vd, snap, rank = item
            if cond == "BUY以上だけ":
                return vd == "BUY"
            if cond == "WATCH以上":
                return vd in ("BUY", "WATCH")
            if cond == "スコア上位TOP5":
                return rank <= 5
            if cond == "スコア上位TOP10":
                return rank <= 10
            return rank <= 20

        # v24: PIT派生メトリクスでの検証フィルター（post_filter指定時のみ）。当日横断情報だけ使用。
        pit = _pit_metrics(scored, rser, theme_fit) if post_filter else None

        def _post_ok(item):
            if not post_filter:
                return True
            t, sc, vd, snap, rank = item
            p = pit.get(t, {})
            if "rs_max" in post_filter and not (p.get("rs_pct") is not None and p["rs_pct"] <= post_filter["rs_max"]):
                return False
            if "score_min" in post_filter and not (sc >= post_filter["score_min"]):
                return False
            if "market_pct_max" in post_filter and not (p.get("market_pct") is not None and p["market_pct"] <= post_filter["market_pct_max"]):
                return False
            if "sector_neutral_min" in post_filter and not (p.get("sector_neutral") is not None and p["sector_neutral"] >= post_filter["sector_neutral_min"]):
                return False
            if post_filter.get("leader_only") and p.get("leader") != "リーダー":
                return False
            return True

        picks = [it for it in scored if passes(it) and _post_ok(it)][:config.BACKTEST_MAX_PICKS]

        date = common[i]
        for t, sc, vd, snap, rank in picks:
            d = data[t]
            if entry_method == "翌営業日始値":
                e_idx = i + 1; e_price = float(d["Open"].iloc[e_idx])
            elif entry_method == "翌営業日終値":
                e_idx = i + 1; e_price = float(d["Close"].iloc[e_idx])
            else:
                e_idx = i; e_price = float(d["Close"].iloc[i])
            if e_idx + 1 >= L or not (e_price > 0):
                continue
            pnl, days, why = _simulate(d, e_idx, hold, e_price, take_rule, stop_rule)
            ex_idx = min(e_idx + days, L - 1)
            pnl_net = pnl - rt_cost
            trades.append({
                "date": str(date.date()), "ticker": t, "score": sc, "verdict": vd, "rank": rank,
                "entry": round(e_price, 2), "exit": round(e_price * (1 + pnl / 100), 2),
                "pnl_gross": round(pnl, 2), "pnl_pct": round(pnl_net, 2), "days": days, "reason": why,
                "sector": theme_fit[t][1], "theme": "/".join(theme_fit[t][2][:2]),
                "exit_date": str(common[ex_idx].date()), "entry_rsi": round(float(snap.get("rsi", 50)), 0),
            })
        k = len(picks)
        if k and valid:
            rsel = rng.choice(valid, size=min(k, len(valid)), replace=False)
            for t in rsel:
                d = data[t]
                e_idx = i + 1 if entry_method != "スコア算出日の終値" else i
                if e_idx + 1 >= L:
                    continue
                e_price = float(d["Open"].iloc[e_idx]) if entry_method == "翌営業日始値" else float(d["Close"].iloc[e_idx])
                if e_price <= 0:
                    continue
                pnl, days, why = _simulate(d, e_idx, hold, e_price, take_rule, stop_rule)
                random_trades.append({"date": str(date.date()), "pnl_gross": round(pnl, 2),
                                      "pnl_pct": round(pnl - rt_cost, 2), "days": days})
        if progress_cb:
            progress_cb(phase, n_done, len(test_idx), 0, str(date.date()))
    return trades, random_trades


def _derive_params(params):
    """params から派生値（重み・しきい値・hold等）を1か所で算出。"""
    Wt = config.WEIGHT_PRESETS.get(params.get("weights", "現在設定"), config.BACKTEST_WEIGHTS)
    rsi_excl = params.get("rsi_exclude", "なし")
    rsi_thr = 999
    for thr in (70, 75, 80):
        if str(thr) in rsi_excl:
            rsi_thr = thr
    return {"Wt": Wt, "rsi_thr": rsi_thr, "vol_cond": params.get("volume_cond", "なし"),
            "cond": params["condition"], "entry_method": params["entry"],
            "hold": config.BACKTEST_HOLD[params["hold"]], "take_rule": params["take"],
            "stop_rule": params["stop"], "rt_cost": _round_trip_cost()}


# ---------------- メイン ----------------
def run_backtest(params, progress_cb=None):
    start_dt = dt.datetime.now(); t0 = time.time()
    universe_mode = params["universe_mode"]; period_m = config.BACKTEST_PERIODS[params["period"]]
    hold = config.BACKTEST_HOLD[params["hold"]]; entry_method = params["entry"]
    take_rule = params["take"]; stop_rule = params["stop"]; cond = params["condition"]
    rsi_excl = params.get("rsi_exclude", "なし")
    vol_cond = params.get("volume_cond", "なし")
    Wt = config.WEIGHT_PRESETS.get(params.get("weights", "現在設定"), config.BACKTEST_WEIGHTS)
    rsi_thr = 999
    for thr in (70, 75, 80):
        if str(thr) in rsi_excl:
            rsi_thr = thr

    period_bars = period_m * 21
    total_bars = 210 + period_bars + hold + 10

    tickers, sources, meta = universe.build_universe(universe_mode, params.get("watchlist", ()))
    tickers = tickers[:config.BACKTEST_MAX_TICKERS]

    # データ取得
    data = {}; fail = []
    for k, t in enumerate(tickers):
        try:
            d = _prep(_history(t, total_bars))
            if len(d) >= 230:
                data[t] = d
            else:
                fail.append(t)
        except Exception:
            fail.append(t)
        if progress_cb:
            progress_cb("データ取得", k + 1, len(tickers), len(fail), t)
    if not data:
        return {"ok": False, "reason": "有効データなし", "fail": fail}

    # 共通の営業日（intersection）で位置合わせ
    common = None
    for d in data.values():
        common = d.index if common is None else common.intersection(d.index)
    common = common.sort_values()
    for t in list(data.keys()):
        data[t] = data[t].reindex(common).dropna(subset=["Close"])
    L = len(common)
    if L < 230:
        return {"ok": False, "reason": "履歴期間が不足", "fail": fail}

    # テーマ（静的）
    theme_fit = {}
    for t in data:
        th = themes_mod.classify(t)
        wt = 0.0
        for x in th:
            for key, val in config.CURRENT_THEMES.items():
                if key == x or key in x:
                    wt = max(wt, val)
        theme_fit[t] = (wt, th[0] if th else "未分類", th)

    # 検証日インデックス
    first = max(205, L - period_bars)
    last = L - hold - 2
    test_idx = list(range(first, last + 1, config.BACKTEST_SAMPLE_EVERY))

    # 比較用: SPY/QQQ
    bench = {}
    for b in ("SPY", "QQQ"):
        try:
            bd = _history(b, total_bars).reindex(common).dropna(subset=["Close"])
            bench[b] = bd
        except Exception:
            bench[b] = None

    # v20: 内側ループは _eval_window に集約（出力は従来と同一）
    dv = {"Wt": Wt, "rsi_thr": rsi_thr, "vol_cond": vol_cond, "cond": cond,
          "entry_method": entry_method, "hold": hold, "take_rule": take_rule,
          "stop_rule": stop_rule, "rt_cost": _round_trip_cost()}
    trades, random_trades = _eval_window(data, common, L, test_idx, theme_fit, dv, progress_cb)

    if not trades:
        return {"ok": False, "reason": "トレードが生成されませんでした（条件が厳しすぎる可能性）", "fail": fail}

    result = _aggregate(trades, random_trades, bench, common, period_bars, params)
    result["fail"] = fail
    result["timestamp"] = start_dt.isoformat(timespec="seconds")
    result["duration_sec"] = round(time.time() - t0, 1)
    result["params"] = params
    result["ok"] = True
    save_cache(result)
    storage.save_csv(config.BACKTEST_TRADES_PATH, list(trades[0].keys()), trades)
    return result


# ---------------- v20: ウォークフォワード検証（重み固定の連続OOS評価） ----------------
def _bench_window_return(bclose, i0, i1, rt):
    """ベンチ（買い持ち）の窓内リターン%（1往復コスト控除）。取れなければ None。"""
    try:
        a = float(bclose.iloc[i0]); b = float(bclose.iloc[i1])
        if a > 0 and b > 0:
            return round((b / a - 1) * 100 - rt, 1)
    except Exception:
        pass
    return None


def _wf_prepare(params, n_folds=None, test_months=None, progress_cb=None):
    """walk_forward / compare_improvement_walk_forward 共通の準備：
    データ取得・整列・theme_fit・bench・フォールド分割。返り値 dict（ok=False で理由）。"""
    n_folds = int(n_folds or getattr(config, "WF_DEFAULT_FOLDS", 4))
    test_months = int(test_months or getattr(config, "WF_DEFAULT_TEST_MONTHS", 3))
    dv = _derive_params(params)
    hold = dv["hold"]; rt = dv["rt_cost"]
    test_bars = test_months * 21
    total_bars = 210 + n_folds * test_bars + hold + 12

    tickers, sources, meta = universe.build_universe(params["universe_mode"], params.get("watchlist", ()))
    tickers = tickers[:config.BACKTEST_MAX_TICKERS]
    data, fail = {}, []
    for k, t in enumerate(tickers):
        try:
            d = _prep(_history(t, total_bars))
            if len(d) >= 230:
                data[t] = d
            else:
                fail.append(t)
        except Exception:
            fail.append(t)
        if progress_cb:
            progress_cb("データ取得", k + 1, len(tickers), len(fail), t)
    if not data:
        return {"ok": False, "reason": "有効データなし", "fail": fail}

    common = None
    for d in data.values():
        common = d.index if common is None else common.intersection(d.index)
    common = common.sort_values()
    for t in list(data.keys()):
        data[t] = data[t].reindex(common).dropna(subset=["Close"])
    L = len(common)
    if L < 230:
        return {"ok": False, "reason": "履歴期間が不足（フォールド数や期間を減らしてください）", "fail": fail}

    theme_fit = {}
    for t in data:
        th = themes_mod.classify(t)
        wt = 0.0
        for x in th:
            for key, val in config.CURRENT_THEMES.items():
                if key == x or key in x:
                    wt = max(wt, val)
        theme_fit[t] = (wt, th[0] if th else "未分類", th)

    bench = {}
    for b in ("SPY", "QQQ"):
        try:
            bench[b] = _history(b, total_bars).reindex(common)["Close"].ffill()
        except Exception:
            bench[b] = None

    last = L - hold - 2
    first = max(210, last - n_folds * test_bars + 1)
    span = last - first + 1
    if span < n_folds * 5:
        return {"ok": False, "reason": "履歴が短くフォールドを作れません", "fail": fail}
    fold_size = span // n_folds
    fold_ranges = []
    for kf in range(n_folds):
        seg0 = first + kf * fold_size
        seg1 = (first + (kf + 1) * fold_size - 1) if kf < n_folds - 1 else last
        fold_ranges.append((seg0, seg1))

    return {"ok": True, "data": data, "common": common, "L": L, "theme_fit": theme_fit,
            "bench": bench, "dv": dv, "rt": rt, "n_folds": n_folds, "test_months": test_months,
            "fold_ranges": fold_ranges, "fail": fail}


def _wf_evaluate(prep, post_filter=None, progress_cb=None):
    """準備済みデータ上で各フォールドを評価。post_filter=None なら従来と同一出力。"""
    data = prep["data"]; common = prep["common"]; L = prep["L"]; theme_fit = prep["theme_fit"]
    bench = prep["bench"]; dv = prep["dv"]; rt = prep["rt"]; n_folds = prep["n_folds"]
    folds = []
    for kf, (seg0, seg1) in enumerate(prep["fold_ranges"]):
        test_idx = list(range(seg0, seg1 + 1, config.BACKTEST_SAMPLE_EVERY))
        trades, random_trades = _eval_window(data, common, L, test_idx, theme_fit, dv,
                                             progress_cb, phase=f"フォールド{kf+1}/{n_folds}",
                                             post_filter=post_filter)
        pnls = [t["pnl_pct"] for t in trades]
        m = _metrics(pnls)
        _, _, max_dd, total_ret = _equity_and_dd(sorted(trades, key=lambda x: x["date"])) if trades else ([], [], 0.0, 0.0)
        _, _, _, rand_total = _equity_and_dd(sorted(random_trades, key=lambda x: x["date"])) if random_trades else ([], [], 0.0, 0.0)
        sharpe = round(float(np.mean(pnls) / np.std(pnls)), 2) if len(pnls) >= 2 and np.std(pnls) else 0.0
        spy_ret = _bench_window_return(bench.get("SPY"), seg0, seg1, rt) if bench.get("SPY") is not None else None
        qqq_ret = _bench_window_return(bench.get("QQQ"), seg0, seg1, rt) if bench.get("QQQ") is not None else None
        folds.append({
            "fold": kf + 1,
            "start": str(common[seg0].date()), "end": str(common[seg1].date()),
            "trades": m.get("trades", 0), "oos_return": round(total_ret, 1),
            "win_rate": m.get("win_rate", 0.0), "sharpe": sharpe, "max_dd": round(max_dd, 1),
            "vs_spy": (round(total_ret - spy_ret, 1) if spy_ret is not None else None),
            "vs_qqq": (round(total_ret - qqq_ret, 1) if qqq_ret is not None else None),
            "vs_random": round(total_ret - rand_total, 1),
            "spy": spy_ret, "qqq": qqq_ret, "random": round(rand_total, 1),
        })

    def _avg(key):
        vals = [f[key] for f in folds if f.get(key) is not None]
        return round(float(np.mean(vals)), 1) if vals else None

    def _beat_rate(key):
        vals = [f[key] for f in folds if f.get(key) is not None]
        return (round(sum(1 for v in vals if v > 0) / len(vals) * 100)) if vals else None

    summary = {
        "n_folds": n_folds, "test_months": prep["test_months"],
        "avg_oos_return": _avg("oos_return"), "avg_win_rate": _avg("win_rate"),
        "avg_sharpe": _avg("sharpe"), "avg_max_dd": _avg("max_dd"),
        "worst_max_dd": round(min((f["max_dd"] for f in folds), default=0.0), 1),
        "total_trades": sum(f["trades"] for f in folds),
        "beat_spy_pct": _beat_rate("vs_spy"), "beat_qqq_pct": _beat_rate("vs_qqq"),
        "beat_random_pct": _beat_rate("vs_random"),
        "positive_folds_pct": round(sum(1 for f in folds if f["oos_return"] > 0) / len(folds) * 100) if folds else 0,
    }
    return {"folds": folds, "summary": summary}


def walk_forward(params, n_folds=None, test_months=None, progress_cb=None):
    """重み固定のまま履歴を連続する n_folds 個のOOS窓に分割し、各窓の成績を出す。
    ※学習(重み再フィット)は行わない＝ローリングのアウトオブサンプル評価。"""
    prep = _wf_prepare(params, n_folds, test_months, progress_cb)
    if not prep.get("ok"):
        return prep
    ev = _wf_evaluate(prep, post_filter=None, progress_cb=progress_cb)
    return {"ok": True, "folds": ev["folds"], "summary": ev["summary"],
            "fail": prep["fail"], "params": params}


def _wf_avg_trades(folds):
    return round(sum(f["trades"] for f in folds) / len(folds), 1) if folds else 0.0


def _wf_valid_folds(folds):
    return sum(1 for f in folds if f["trades"] > 0)


def _wf_verdict(base_sum, base_tc, var_sum, var_tc, var_valid, n_folds):
    """WF改善/悪化/変化なし/サンプル不足。"""
    if var_tc < 10 or var_valid < n_folds / 2.0:
        return "サンプル不足"
    ab, av = base_sum.get("avg_oos_return"), var_sum.get("avg_oos_return")
    if ab is None or av is None:
        return "サンプル不足"
    dr = av - ab
    pf_ok = (var_sum.get("positive_folds_pct", 0) or 0) >= (base_sum.get("positive_folds_pct", 0) or 0)
    if dr >= 3 and pf_ok and var_tc >= max(1.0, base_tc * 0.3):
        return "改善"
    bs_drop = (base_sum.get("beat_spy_pct") is not None and var_sum.get("beat_spy_pct") is not None
               and (base_sum["beat_spy_pct"] - var_sum["beat_spy_pct"]) >= 25)
    bq_drop = (base_sum.get("beat_qqq_pct") is not None and var_sum.get("beat_qqq_pct") is not None
               and (base_sum["beat_qqq_pct"] - var_sum["beat_qqq_pct"]) >= 25)
    if dr <= -3 or bs_drop or bq_drop:
        return "悪化"
    return "変化なし"


def compare_improvement_walk_forward(params, filters=None, n_folds=None, test_months=None, progress_cb=None):
    """通常WF vs フィルター適用WF を同一データ・同一フォールドで比較（検証専用・本番非変更）。"""
    filters = filters if filters is not None else IMPROVEMENT_FILTERS
    prep = _wf_prepare(params, n_folds, test_months, progress_cb)
    if not prep.get("ok"):
        return prep
    base_ev = _wf_evaluate(prep, post_filter=None, progress_cb=progress_cb)
    base_sum = base_ev["summary"]
    base_tc = _wf_avg_trades(base_ev["folds"])

    variants = []
    for name, spec in filters:
        ev = _wf_evaluate(prep, post_filter=spec, progress_cb=progress_cb)
        sm = ev["summary"]
        avg_tc = _wf_avg_trades(ev["folds"])
        valid = _wf_valid_folds(ev["folds"])
        variants.append({
            "name": name, "filter": spec, "summary": sm,
            "avg_trade_count": avg_tc, "valid_folds": valid,
            "verdict": _wf_verdict(base_sum, base_tc, sm, avg_tc, valid, prep["n_folds"]),
        })

    return {"ok": True, "baseline": {"summary": base_sum, "avg_trade_count": base_tc,
                                     "valid_folds": _wf_valid_folds(base_ev["folds"])},
            "variants": variants, "n_folds": prep["n_folds"], "fail": prep["fail"], "params": params}


# ---------------- 集計・比較・提案 ----------------
def _metrics(pnls):
    n = len(pnls)
    if n == 0:
        return {}
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    wr = len(wins) / n * 100
    aw = float(np.mean(wins)) if wins else 0.0
    al = float(np.mean(losses)) if losses else 0.0
    gw = sum(wins); gl = -sum(losses)
    pf = (gw / gl) if gl else (float("inf") if gw else 0.0)
    expectancy = wr / 100 * aw + (1 - wr / 100) * al
    return {"trades": n, "win_rate": round(wr, 1), "avg_win": round(aw, 1), "avg_loss": round(al, 1),
            "expectancy": round(expectancy, 2), "profit_factor": (round(pf, 2) if pf != float("inf") else 999.0),
            "max_win": round(max(pnls), 1), "max_loss": round(min(pnls), 1)}


def _equity_and_dd(trades_sorted):
    """exit順に等金額で複利。各検証日の本数で正規化して概算の累積カーブを作る。"""
    by_date = {}
    for tr in trades_sorted:
        by_date.setdefault(tr["date"], []).append(tr)
    eq = 1.0; curve = []
    for date in sorted(by_date):
        day = by_date[date]
        w = 1.0 / len(day)
        r = sum((t["pnl_pct"] / 100) * w for t in day)
        eq *= (1 + r)
        curve.append({"date": date, "equity": round(eq, 4)})
    # ドローダウン
    peak = -1e9; dd = []
    for c in curve:
        peak = max(peak, c["equity"])
        dd.append({"date": c["date"], "dd": round((c["equity"] / peak - 1) * 100, 2)})
    max_dd = min((x["dd"] for x in dd), default=0.0)
    total_ret = round((eq - 1) * 100, 1)
    return curve, dd, max_dd, total_ret


def _aggregate(trades, random_trades, bench, common, period_bars, params):
    pnls = [t["pnl_pct"] for t in trades]
    base = _metrics(pnls)
    base["avg_days"] = round(float(np.mean([t["days"] for t in trades])), 1)
    curve, dd, max_dd, total_ret = _equity_and_dd(sorted(trades, key=lambda x: x["date"]))
    base["max_dd"] = round(max_dd, 1)
    base["total_return"] = total_ret

    # 判定別
    by_verdict = {v: _metrics([t["pnl_pct"] for t in trades if t["verdict"] == v]) for v in ("BUY", "WATCH")}
    # スコア帯別
    def bucket(s):
        return "80+" if s >= 80 else "70-80" if s >= 70 else "55-70" if s >= 55 else "<55"
    buckets = {}
    for t in trades:
        buckets.setdefault(bucket(t["score"]), []).append(t["pnl_pct"])
    by_bucket = {b: _metrics(v) for b, v in buckets.items()}
    # セクター別/テーマ別
    sec = {}; th = {}
    for t in trades:
        sec.setdefault(t["sector"], []).append(t["pnl_pct"])
        th.setdefault(t["theme"], []).append(t["pnl_pct"])
    by_sector = {s: _metrics(v) for s, v in sec.items() if len(v) >= 3}
    by_theme = {s: _metrics(v) for s, v in th.items() if len(v) >= 3}
    # TOP5/10/20比較
    top_cmp = {}
    for k in (5, 10, 20):
        sub = [t["pnl_pct"] for t in trades if t["rank"] <= k]
        m = _metrics(sub)
        top_cmp[f"TOP{k}"] = {"expectancy": m.get("expectancy", 0), "win_rate": m.get("win_rate", 0),
                              "avg": round(float(np.mean(sub)), 1) if sub else 0, "trades": len(sub)}
    # 月別
    monthly = {}
    for t in trades:
        m = t["date"][:7]
        monthly.setdefault(m, []).append(t["pnl_pct"])
    by_month = {m: round(float(np.mean(v)), 1) for m, v in sorted(monthly.items())}

    # 比較対象
    rand_pnls = [t["pnl_pct"] for t in random_trades]
    rcurve, _, _, rand_total = _equity_and_dd(sorted(random_trades, key=lambda x: x["date"])) if random_trades else ([], [], 0, 0)
    # v18: ベンチ（買い持ち）は1往復分のコストを控除
    rt = _round_trip_cost()

    def _net_bench(v):
        return round(v - rt, 1) if v is not None else None

    comparisons = {
        "発掘ルール": total_ret,
        "ランダム同数": rand_total,
        "S&P500(SPY)放置": _net_bench(_benchmark_return(bench.get("SPY"), common, period_bars)),
        "NASDAQ100(QQQ)放置": _net_bench(_benchmark_return(bench.get("QQQ"), common, period_bars)),
    }

    suggestions = _suggestions(trades, by_verdict, by_bucket, by_sector, top_cmp, base)

    return {
        "metrics": base, "by_verdict": by_verdict, "by_bucket": by_bucket,
        "by_sector": by_sector, "by_theme": by_theme, "top_cmp": top_cmp, "by_month": by_month,
        "equity_curve": curve, "dd_curve": dd, "random_curve": rcurve,
        "comparisons": comparisons, "trades": trades, "suggestions": suggestions,
    }


def _benchmark_return(bdf, common, period_bars):
    if bdf is None or len(bdf) < period_bars + 5:
        return None
    try:
        c = bdf["Close"].dropna()
        start = c.iloc[max(0, len(c) - period_bars - 1)]
        return float(round((c.iloc[-1] / start - 1) * 100, 1))
    except Exception:
        return None


def _suggestions(trades, by_verdict, by_bucket, by_sector, top_cmp, base):
    out = []
    # RSI過熱（entry_rsi>=70）の成績比較
    hot = [t["pnl_pct"] for t in trades if t.get("entry_rsi", 0) >= 70]
    cool = [t["pnl_pct"] for t in trades if t.get("entry_rsi", 0) < 70]
    if len(hot) >= 5 and cool:
        hot_e = _metrics(hot).get("expectancy", 0); cool_e = _metrics(cool).get("expectancy", 0)
        if hot_e < cool_e:
            out.append(f"RSI70以上のエントリーは期待値({hot_e:+})が低い（RSI70未満は{cool_e:+}）→ RSI過熱の除外が有効な可能性。")
        else:
            out.append("RSI70以上でも成績は悪化していない→過度なRSI除外は不要かも。")
    # RSI過熱の影響（rank/score基準は別途、ここはRSIをトレードに持っていないので簡易にスコア帯で代替）
    if by_bucket.get("80+") and by_bucket.get("55-70"):
        if by_bucket["80+"].get("expectancy", 0) > by_bucket["55-70"].get("expectancy", 0):
            out.append("スコアが高いほど期待値が高い傾向。低スコア(55-70)の採用を絞ると改善の可能性。")
    # TOP5 vs TOP20
    if top_cmp.get("TOP5") and top_cmp.get("TOP20"):
        if top_cmp["TOP5"]["expectancy"] > top_cmp["TOP20"]["expectancy"]:
            out.append(f"TOP5の期待値({top_cmp['TOP5']['expectancy']:+})がTOP20({top_cmp['TOP20']['expectancy']:+})より高い→選定を絞る方が有利かも。")
        else:
            out.append("TOP20でもTOP5に劣らない→分散を広げても良い。")
    # BUY vs WATCH
    bv = by_verdict.get("BUY", {}); wv = by_verdict.get("WATCH", {})
    if bv.get("trades") and wv.get("trades"):
        if bv.get("expectancy", 0) > wv.get("expectancy", 0):
            out.append("BUYの期待値がWATCHを上回る→WATCHは見送り、BUY中心が無難。")
    # 損切り幅
    if base.get("avg_loss", 0) <= -config.BACKTEST_STOP_PCT * 0.95:
        out.append(f"平均損失が損切り幅(-{config.BACKTEST_STOP_PCT:.0f}%)付近に張り付き→損切りが浅すぎる可能性（揺さぶられ）。")
    # セクター
    if by_sector:
        best = max(by_sector.items(), key=lambda x: x[1].get("expectancy", -9))
        worst = min(by_sector.items(), key=lambda x: x[1].get("expectancy", 9))
        out.append(f"セクター別: 「{best[0]}」が期待値最高、「{worst[0]}」は低調。強いセクターに寄せると改善の可能性。")
    # PF/総評
    pf = base.get("profit_factor", 0)
    if pf and pf < 1:
        out.append("プロフィットファクター<1（損失>利益）。利確を伸ばす/損切りを早める等のルール見直しを。")
    elif pf >= 1.3:
        out.append(f"プロフィットファクター{pf}と良好。現行ルールは過去では機能。ただし将来を保証しません。")
    if not out:
        out.append("明確な改善点は検出されませんでした。サンプルを増やして再検証してください。")
    return out


def ai_suggestions(result):
    """AIがあれば自然文コメント。なければ None。"""
    try:
        from modules import news as news_mod
        m = result["metrics"]; cmp = result["comparisons"]
        summary = (f"トレード{m['trades']} 勝率{m['win_rate']}% 期待値{m['expectancy']} PF{m['profit_factor']} "
                   f"最大DD{m['max_dd']}% 総リターン{m['total_return']}% vs SPY{cmp.get('S&P500(SPY)放置')}")
        prompt = ("あなたは投資のバックテスト分析家です。以下の発掘ルールの過去検証結果から、"
                  "改善案を3〜5個、初心者向けにやさしく箇条書き(各行『・』)で。日本語。将来を保証しない旨も一言。\n" + summary)
        raw = news_mod._call_ai(prompt)
        if not raw:
            return None
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]
    except Exception:
        return None


# ---------------- キャッシュ ----------------
def save_cache(result):
    slim = {k: result.get(k) for k in ("metrics", "by_verdict", "by_bucket", "by_sector", "by_theme",
            "top_cmp", "by_month", "equity_curve", "dd_curve", "random_curve", "comparisons",
            "suggestions", "timestamp", "duration_sec", "params", "fail")}
    slim["trades_sample"] = result.get("trades", [])[:200]
    slim["n_trades"] = len(result.get("trades", []))
    storage.save_json(config.BACKTEST_CACHE_PATH, slim)


def load_cache():
    return storage.load_json(config.BACKTEST_CACHE_PATH, None)


def load_trades_csv_text():
    import os
    p = config.BACKTEST_TRADES_PATH
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""
