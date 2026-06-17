"""
news.py — ニュース取得＋実戦向け分類。
各ニュースを以下に分類し、影響度を -5〜+5 で数値化:
  category: すぐ効く / 1〜3ヶ月後 / 未織り込み / 危険 / チャンス / 中立
  impact: int (-5..+5)
優先: Finnhub(あれば) → モック。 分類: AI(あれば) → キーワード。
失敗してもアプリは止まらない。
"""
import os
import config
import datetime as dt
from modules import mock_data

try:
    import streamlit as st
    _cache = st.cache_data(ttl=900, show_spinner=False)
except Exception:  # pragma: no cover
    def _cache(func):
        return func

_BULL = ["上方", "引き上げ", "上回", "最高益", "増益", "増収", "受注", "買い", "強気", "提携",
         "好調", "上昇", "beat", "upgrade", "raise", "record", "surge", "buy", "partnership"]
_BEAR = ["下方", "引き下げ", "下回", "減益", "減収", "懸念", "調査", "訴訟", "売り", "弱気",
         "下落", "過熱", "miss", "downgrade", "cut", "probe", "lawsuit", "sell", "warn", "fall"]
_DANGER = ["調査", "訴訟", "不正", "リコール", "規制", "提訴", "probe", "lawsuit", "fraud", "recall", "ban", "halt"]
_OPP = ["提携", "受注", "新製品", "上方修正", "自社株買い", "partnership", "contract", "buyback", "launch"]
_UNPRICED = ["報道", "観測", "可能性", "検討", "計画", "交渉", "rumor", "report", "talks", "mulls", "could", "plans"]
_MID = ["決算", "ガイダンス", "受注", "新製品", "工場", "需要", "earnings", "guidance", "demand", "contract", "capacity"]
_SHORT = ["引き上げ", "引き下げ", "格上げ", "格下げ", "急騰", "急落", "upgrade", "downgrade", "surge", "plunge", "target"]


def news_status() -> dict:
    fh = bool(config.get_secret("FINNHUB_API_KEY"))
    oa = bool(config.get_secret("OPENAI_API_KEY"))
    an = bool(config.get_secret("ANTHROPIC_API_KEY"))
    missing = []
    if not fh:
        missing.append("FINNHUB_API_KEY")
    if not (oa or an):
        missing.append("AIキー(OpenAI または Anthropic)")
    return {"finnhub": fh, "openai": oa, "anthropic": an,
            # FinnhubとAI（OpenAI/Anthropicのいずれか）が揃えば「フル機能」
            "ai_ready": oa or an,
            "fully_ready": fh and (oa or an),
            "missing": missing}


@_cache
def _get_news_cached(ticker, days, limit):
    return _get_news_impl(ticker, days, limit)


def get_news(ticker: str, days: int = 30, limit: int = 8, use_cache: bool = True) -> dict:
    if use_cache:
        try:
            return _get_news_cached(ticker, days, limit)
        except Exception:
            pass
    return _get_news_impl(ticker, days, limit)


def _get_news_impl(ticker: str, days: int = 30, limit: int = 8) -> dict:
    ticker = ticker.strip().upper()
    items, src, err = _fetch_raw(ticker, days, limit)
    key_missing = src == "mock"

    classifier = "keyword"
    if config.get_secret("ANTHROPIC_API_KEY") or config.get_secret("OPENAI_API_KEY"):
        ai = _classify_with_ai(ticker, items)
        if ai:
            items, classifier = ai, "ai"
        else:
            err = err or "AI分類に失敗（キーワードへフォールバック）"
    if classifier != "ai":
        items = [_classify_keyword(it) for it in items]
    return {"items": items, "source": src, "classifier": classifier,
            "key_missing": key_missing, "error": err}


def summarize(items: list) -> dict:
    """件数・カテゴリ別・ニューススコア(0-20)・スコア理由を返す。"""
    bull = sum(1 for i in items if i.get("lean") == "bull")
    bear = sum(1 for i in items if i.get("lean") == "bear")
    neu = sum(1 for i in items if i.get("lean") == "neutral")
    total = max(1, bull + bear + neu)
    avg_impact = sum(i.get("impact", 0) for i in items) / max(1, len(items))
    # 0-20スコア: 平均インパクト(-5..+5)を 0..20 に線形変換（中心10）
    score = max(0.0, min(20.0, 10.0 + avg_impact * 2.0))

    cats = {}
    for i in items:
        cats[i.get("category", "中立")] = cats.get(i.get("category", "中立"), 0) + 1

    reasons = []
    if bull:
        reasons.append(f"強気材料 {bull}件")
    if bear:
        reasons.append(f"弱気材料 {bear}件")
    if cats.get("危険"):
        reasons.append(f"⚠️危険ニュース {cats['危険']}件")
    if cats.get("チャンス"):
        reasons.append(f"チャンス材料 {cats['チャンス']}件")
    if cats.get("未織り込み"):
        reasons.append(f"未織り込みの可能性 {cats['未織り込み']}件")
    reasons.append(f"平均インパクト {avg_impact:+.1f}（-5〜+5）")

    return {"bull": bull, "bear": bear, "neutral": neu, "score": round(score, 1),
            "avg_impact": round(avg_impact, 1), "categories": cats, "reasons": reasons}


# ---------------- 内部 ----------------
def _fetch_raw(ticker, days, limit):
    key = config.get_secret("FINNHUB_API_KEY")
    if key:
        try:
            import requests
            today = dt.date.today()
            frm = (today - dt.timedelta(days=days)).isoformat()
            r = requests.get("https://finnhub.io/api/v1/company-news",
                             params={"symbol": ticker, "from": frm, "to": today.isoformat(), "token": key},
                             timeout=10)
            if r.status_code == 200:
                data = r.json()
                items = [{"headline": d.get("headline", ""), "source": d.get("source", "Finnhub")} for d in data[:limit]]
                if items:
                    return items, "finnhub", None
                return mock_data.mock_news(ticker, n=min(limit, 8)), "mock", "Finnhubに該当ニュースなし"
            else:
                return mock_data.mock_news(ticker, n=min(limit, 8)), "mock", f"Finnhub HTTP {r.status_code}"
        except Exception as e:
            return mock_data.mock_news(ticker, n=min(limit, 8)), "mock", f"Finnhub取得失敗: {type(e).__name__}"
    return mock_data.mock_news(ticker, n=min(limit, 8)), "mock", None


def _classify_keyword(item: dict) -> dict:
    text = (item.get("headline", "") or "").lower()
    lean = item.get("lean")
    if lean not in ("bull", "bear", "neutral"):
        b = sum(1 for w in _BULL if w.lower() in text)
        s = sum(1 for w in _BEAR if w.lower() in text)
        lean = "bull" if b > s else "bear" if s > b else "neutral"

    danger = any(w.lower() in text for w in _DANGER)
    opp = any(w.lower() in text for w in _OPP)
    unpriced = any(w.lower() in text for w in _UNPRICED)
    mid = any(w.lower() in text for w in _MID)
    short = any(w.lower() in text for w in _SHORT)

    # カテゴリ決定（優先順位: 危険 > チャンス > 未織り込み > 時間軸）
    if danger and lean == "bear":
        category = "危険"
    elif opp and lean == "bull":
        category = "チャンス"
    elif unpriced:
        category = "未織り込み"
    elif short:
        category = "すぐ効く"
    elif mid:
        category = "1〜3ヶ月後"
    else:
        category = "中立"

    # インパクト -5..+5
    base = {"bull": 3, "bear": -3, "neutral": 0}[lean]
    if danger:
        base -= 2
    if opp:
        base += 1
    impact = max(-5, min(5, base))

    short_imp = {"bull": "短期: ↑ になりやすい", "bear": "短期: ↓ になりやすい", "neutral": "短期: 影響小"}[lean]
    mid_imp = {"bull": "中期: 追い風の可能性", "bear": "中期: 重しの可能性", "neutral": "中期: 中立"}[lean]
    return {**item, "lean": lean, "category": category, "impact": impact,
            "short_impact": short_imp, "mid_impact": mid_imp}


def _classify_with_ai(ticker, items):
    headlines = [it.get("headline", "") for it in items]
    if not headlines:
        return None
    prompt = (
        f"あなたは株式アナリストです。銘柄{ticker}の見出しを JSON 配列で分類。各要素:\n"
        '{"lean":"bull|bear|neutral",'
        '"category":"すぐ効く|1〜3ヶ月後|未織り込み|危険|チャンス|中立",'
        '"impact":整数(-5〜+5),"short_impact":"一言","mid_impact":"一言"}\n'
        "JSON配列のみ出力。\n見出し:\n" + "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    )
    raw = _call_ai(prompt)
    if not raw:
        return None
    try:
        import json, re
        m = re.search(r"\[.*\]", raw, re.S)
        arr = json.loads(m.group(0)) if m else json.loads(raw)
        out = []
        for it, c in zip(items, arr):
            lean = c.get("lean", "neutral")
            lean = lean if lean in ("bull", "bear", "neutral") else "neutral"
            out.append({"headline": it.get("headline", ""), "source": it.get("source", ""),
                        "lean": lean, "category": c.get("category", "中立"),
                        "impact": max(-5, min(5, int(c.get("impact", 0) or 0))),
                        "short_impact": c.get("short_impact", ""), "mid_impact": c.get("mid_impact", "")})
        return out or None
    except Exception:
        return None


def _call_ai(prompt):
    """config.AI_PROVIDER_ORDER の順に試す（v4はOpenAI優先）。"""
    # config はモジュール先頭で import 済み。関数内で再importすると config が
    # ローカル変数化し UnboundLocalError を起こすため、ここでは import しない。
    order = getattr(config, "AI_PROVIDER_ORDER", ["openai", "anthropic"])
    for provider in order:
        raw = _call_openai(prompt) if provider == "openai" else _call_anthropic(prompt)
        if raw:
            return raw
    return None


def _call_anthropic(prompt):
    key = config.get_secret("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic  # config はモジュール先頭の import を使う（再importしない）
        msg = anthropic.Anthropic(api_key=key).messages.create(
            model=getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-6"), max_tokens=1024,
            messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception:
        return None


def _call_openai(prompt):
    key = config.get_secret("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI  # config はモジュール先頭の import を使う（再importしない）
        resp = OpenAI(api_key=key).chat.completions.create(
            model=getattr(config, "OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}], max_tokens=1024)
        return resp.choices[0].message.content
    except Exception:
        return None
