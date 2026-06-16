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

    trades = []
    rng = np.random.default_rng(42)
    random_trades = []

    for n_done, i in enumerate(test_idx, 1):
        # PITスコア計算
        rets = {t: (data[t]["RET60"].iloc[i]) for t in data}
        valid = [t for t in data if rets[t] == rets[t]]
        if not valid:
            continue
        rser = pd.Series({t: rets[t] for t in valid}).rank(pct=True)  # 0..1
        scored = []
        for t in valid:
            snap = _snap_at(data[t], i)
            # RSI除外
            if snap.get("rsi", 50) >= rsi_thr:
                continue
            # 出来高条件
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

        # 採用条件
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
        picks = [it for it in scored if passes(it)][:config.BACKTEST_MAX_PICKS]

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
            trades.append({
                "date": str(date.date()), "ticker": t, "score": sc, "verdict": vd, "rank": rank,
                "entry": round(e_price, 2), "exit": round(e_price * (1 + pnl / 100), 2),
                "pnl_pct": round(pnl, 2), "days": days, "reason": why,
                "sector": theme_fit[t][1], "theme": "/".join(theme_fit[t][2][:2]),
                "exit_date": str(common[ex_idx].date()), "entry_rsi": round(float(snap.get("rsi", 50)), 0),
            })
        # ランダム比較（同数）
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
                random_trades.append({"date": str(date.date()), "pnl_pct": round(pnl, 2), "days": days})
        if progress_cb:
            progress_cb("検証", n_done, len(test_idx), 0, str(date.date()))

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
    comparisons = {
        "発掘ルール": total_ret,
        "ランダム同数": rand_total,
        "S&P500(SPY)放置": _benchmark_return(bench.get("SPY"), common, period_bars),
        "NASDAQ100(QQQ)放置": _benchmark_return(bench.get("QQQ"), common, period_bars),
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
