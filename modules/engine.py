"""
engine.py — 1銘柄ぶんの分析を一気通貫で実行。
v7: with_news（ニュース取得の有無）と use_cache（Streamlitキャッシュ経由可否）を追加。
    並列スキャンでは use_cache=False、高速モードの一次パスでは with_news=False で呼ぶ。
"""
import config
from modules import (data_fetch, indicators, news as news_mod, scoring,
                     trade_plan, action as action_mod)

# ニュース未取得時の中立サマリ
NEUTRAL_NEWS = {"avg_impact": 0.0, "categories": {}, "score": 10.0,
                "bull": 0, "bear": 0, "neutral": 0, "reasons": ["ニュース未取得（高速モード）"]}


def analyze_ticker(ticker, market_score, market_mode="中立", fomc_days=None,
                   with_news=True, use_cache=True) -> dict:
    ticker = ticker.strip().upper()
    try:
        df, src = data_fetch.get_price_history(ticker, config.PRICE_PERIOD, config.PRICE_INTERVAL, use_cache=use_cache)
        df = indicators.add_all_indicators(df)
        snap = indicators.latest_snapshot(df)
        fund = data_fetch.get_fundamentals(ticker, use_cache=use_cache)
        fund["ticker"] = ticker
        if snap.get("price"):
            fund["price"] = snap["price"]

        if with_news:
            news_res = news_mod.get_news(ticker, use_cache=use_cache)
            news_sum = news_mod.summarize(news_res["items"])
        else:
            news_res = {"items": [], "source": "skip", "classifier": "none",
                        "key_missing": False, "error": None}
            news_sum = dict(NEUTRAL_NEWS)

        scores = scoring.compute_total(
            fund, snap, news_sum, market_score,
            market_reasons=[f"相場モード: {market_mode}（{market_score}/10）"])
        plan = trade_plan.build_plan(fund, snap, df, fomc_days=fomc_days)
        act = action_mod.decide_action(scores, snap, fund, plan, news_sum, market_mode)
        avoid = action_mod.avoid_reasons(scores, snap, fund, plan, news_sum)

        return {"ticker": ticker, "ok": True, "source": src, "df": df, "snap": snap,
                "fund": fund, "news": news_res, "news_sum": news_sum,
                "scores": scores, "plan": plan, "action": act, "avoid_reasons": avoid,
                "with_news": with_news}
    except Exception as e:
        return {"ticker": ticker, "ok": False, "error": str(e)}


def attach_news(result, market_score, market_mode="中立", use_cache=True) -> dict:
    """既存の分析結果に後からニュースを取得して反映（高速モードのTOP補完用）。"""
    if not (result and result.get("ok")):
        return result
    t = result["ticker"]
    news_res = news_mod.get_news(t, use_cache=use_cache)
    news_sum = news_mod.summarize(news_res["items"])
    scores = scoring.compute_total(result["fund"], result["snap"], news_sum, market_score,
                                   market_reasons=[f"相場モード: {market_mode}（{market_score}/10）"])
    result["news"] = news_res
    result["news_sum"] = news_sum
    result["scores"] = scores
    result["with_news"] = True
    return result


def analyze_watchlist(tickers, market_score, market_mode="中立", fomc_days=None) -> list:
    return [analyze_ticker(t, market_score, market_mode, fomc_days)
            for t in tickers if t.strip()]


def analyze_map(tickers, market_score, market_mode="中立", fomc_days=None) -> dict:
    uniq, seen = [], set()
    for t in tickers:
        u = (t or "").strip().upper()
        if u and u not in seen:
            seen.add(u); uniq.append(u)
    results = analyze_watchlist(uniq, market_score, market_mode, fomc_days)
    return {r["ticker"]: r for r in results}
