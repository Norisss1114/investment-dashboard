"""
sec_edgar.py (v26) — SEC EDGAR 公式ファンダの取得・表示用（参考表示のみ）。

⚠️ スコア計算・本番発掘ランキング・個別分析スコアには一切使用しない（表示専用）。
取得失敗・CIK変換失敗・companyfacts欠損・SECアクセス失敗・User-Agent未設定でも落ちない。
User-Agent は config.get_secret("SEC_USER_AGENT")（st.secrets→os.getenv→.env）から取得。
メールアドレスはハードコードしない。
"""
import datetime as dt

import config

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Streamlit があればキャッシュ（無い環境では素通し）
try:
    import streamlit as st
    _cache_long = st.cache_data(ttl=86400, show_spinner=False)   # ticker→CIK は1日
    _cache_facts = st.cache_data(ttl=21600, show_spinner=False)  # companyfacts は6時間
except Exception:  # pragma: no cover
    def _cache_long(f):
        return f

    def _cache_facts(f):
        return f


def user_agent():
    """(ua, is_set)。SEC_USER_AGENT 未設定なら既定文字列＋is_set=False。"""
    ua = config.get_secret("SEC_USER_AGENT")
    if ua:
        return ua, True
    return getattr(config, "SEC_USER_AGENT_DEFAULT", "investment-dashboard educational research"), False


def _headers():
    return {"User-Agent": user_agent()[0], "Accept-Encoding": "gzip, deflate"}


@_cache_long
def _ticker_map():
    """ticker(大文字) → CIK(10桁ゼロ埋め) の辞書。失敗時は空辞書。"""
    try:
        import requests
        r = requests.get(SEC_TICKERS_URL, headers=_headers(), timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json() or {}
        m = {}
        for v in (data.values() if isinstance(data, dict) else data):
            t = str(v.get("ticker", "")).strip().upper()
            cik = v.get("cik_str")
            if t and cik is not None:
                m[t] = str(int(cik)).zfill(10)
        return m
    except Exception:
        return {}


def ticker_to_cik(ticker):
    """ticker → CIK(10桁)。未知/失敗は None。"""
    t = str(ticker or "").strip().upper()
    if not t:
        return None
    try:
        return _ticker_map().get(t)
    except Exception:
        return None


@_cache_facts
def fetch_companyfacts(cik):
    """SEC companyfacts JSON を取得。失敗は None。"""
    if not cik:
        return None
    try:
        import requests
        r = requests.get(SEC_FACTS_URL.format(cik=cik), headers=_headers(), timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


def _annual_series(usgaap, tags, unit="USD"):
    """年次(10-K/FY優先)の昇順リストと採用タグを返す。"""
    for tag in tags:
        node = usgaap.get(tag)
        if not node:
            continue
        units = node.get("units", {}) or {}
        arr = units.get(unit) or (next(iter(units.values()), []) if units else [])
        ann = [x for x in arr if x.get("form") == "10-K" and x.get("fp") == "FY" and x.get("val") is not None]
        if not ann:
            ann = [x for x in arr if x.get("fp") == "FY" and x.get("val") is not None]
        if ann:
            ann.sort(key=lambda x: (x.get("fy") or 0, x.get("end") or ""))
            return ann, tag
    return [], None


def parse_recent_fundamentals(facts):
    """companyfacts から最新年次のファンダ＋成長率/比率/鮮度を抽出。欠損でも落ちない。"""
    try:
        usg = (facts or {}).get("facts", {}).get("us-gaap", {}) or {}
    except Exception:
        usg = {}
    if not usg:
        return {"ok": False, "reason": "us-gaap データなし"}

    def latest(tags, unit="USD"):
        ann, _tag = _annual_series(usg, tags, unit)
        if not ann:
            return None, None
        return ann[-1], (ann[-2] if len(ann) >= 2 else None)

    def val(x):
        return x.get("val") if x else None

    rev_c, rev_p = latest(["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"])
    ni_c, ni_p = latest(["NetIncomeLoss"])
    eps_c, eps_p = latest(["EarningsPerShareDiluted", "EarningsPerShareBasic"], "USD/shares")
    gp_c, _ = latest(["GrossProfit"])
    oi_c, _ = latest(["OperatingIncomeLoss"])
    as_c, _ = latest(["Assets"])
    li_c, _ = latest(["Liabilities"])
    eq_c, _ = latest(["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"])
    ocf_c, _ = latest(["NetCashProvidedByUsedInOperatingActivities",
                       "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"])

    revenue, net_income, eps = val(rev_c), val(ni_c), val(eps_c)
    gross, op = val(gp_c), val(oi_c)
    assets, liab, equity, ocf = val(as_c), val(li_c), val(eq_c), val(ocf_c)

    rev_g = (revenue / val(rev_p) - 1) if (revenue and val(rev_p)) else None
    eps_g = (eps / val(eps_p) - 1) if (eps and val(eps_p) and val(eps_p) > 0) else None
    margin = (net_income / revenue) if (net_income is not None and revenue) else None
    roe = (net_income / equity) if (net_income is not None and equity) else None
    debt_ratio = (liab / assets) if (liab is not None and assets) else None

    meta = rev_c or ni_c or as_c or eps_c or {}
    filed = meta.get("filed")
    freshness = None
    if filed:
        try:
            freshness = (dt.date.today() - dt.date.fromisoformat(filed)).days
        except Exception:
            freshness = None

    return {
        "ok": (revenue is not None) or (net_income is not None),
        "revenue": revenue, "net_income": net_income, "eps": eps,
        "gross_profit": gross, "operating_income": op,
        "assets": assets, "liabilities": liab, "equity": equity, "operating_cash_flow": ocf,
        "rev_growth": (round(rev_g, 4) if rev_g is not None else None),
        "eps_growth": (round(eps_g, 4) if eps_g is not None else None),
        "margin": (round(margin, 4) if margin is not None else None),
        "roe": (round(roe, 4) if roe is not None else None),
        "debt_ratio": (round(debt_ratio, 4) if debt_ratio is not None else None),
        "filing_date": filed, "fiscal_year": meta.get("fy"), "fiscal_period": meta.get("fp"),
        "period_end": meta.get("end"), "freshness_days": freshness,
    }


def get_fundamentals(ticker):
    """ticker → SEC公式ファンダ。失敗は {ok:False, reason}。落ちない。"""
    cik = ticker_to_cik(ticker)
    if not cik:
        return {"ok": False, "reason": "ticker→CIK 変換不可"}
    facts = fetch_companyfacts(cik)
    if not facts:
        return {"ok": False, "reason": "companyfacts 取得失敗"}
    res = parse_recent_fundamentals(facts)
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason", "ファンダ抽出不可")}
    res["cik"] = cik
    return res


def compare_with_yfinance(fund, edgar):
    """yfinance fund と EDGAR の margin / rev_g / eps_g を比較（差分はポイント%）。"""
    fund = fund or {}
    edgar = edgar or {}
    out = {}
    for ykey, ekey, label in [("margin", "margin", "利益率"), ("rev_g", "rev_growth", "売上成長率"),
                              ("eps_g", "eps_growth", "EPS成長率")]:
        yv = fund.get(ykey)
        ev = edgar.get(ekey)
        out[ykey] = {"label": label, "yf": yv, "edgar": ev,
                     "diff_pp": (round((ev - yv) * 100, 1) if (yv is not None and ev is not None) else None)}
    return out


# v26.1: ファンダ品質チェック（表示のみ・スコア非使用）
_MAIN_KEYS = ["revenue", "net_income", "eps", "assets", "liabilities", "equity",
              "gross_profit", "operating_income", "operating_cash_flow"]
# (注意しきい, 大乖離しきい) ポイント%
_DIFF_THRESH = {"margin": (5, 15), "rev_g": (10, 25), "eps_g": (20, 50)}


def _diff_level(diff_pp, thr):
    if diff_pp is None:
        return "判定不可"
    a = abs(diff_pp)
    if a < thr[0]:
        return "OK"
    if a < thr[1]:
        return "注意"
    return "大きな乖離"


def quality_check(edgar, fund):
    """EDGAR×yfinance のファンダ品質を判定（表示のみ）。
    返り値: quality/confidence(高中低), reasons, warnings, missing_count, freshness_days, diff_checks。
    取得失敗・None・欠損でも落ちない。"""
    edgar = edgar or {}
    if not edgar.get("ok"):
        return {"quality": "低", "confidence": "低", "reasons": ["EDGAR取得失敗"],
                "warnings": ["SECデータ未取得"], "missing_count": None,
                "freshness_days": None, "diff_checks": {}}

    fresh = edgar.get("freshness_days")
    missing = sum(1 for k in _MAIN_KEYS if edgar.get(k) is None)
    present = len(_MAIN_KEYS) - missing

    cmp = compare_with_yfinance(fund, edgar)
    diff_checks = {}
    for key in ("margin", "rev_g", "eps_g"):
        d = dict(cmp.get(key, {}))
        d["level"] = _diff_level(d.get("diff_pp"), _DIFF_THRESH[key])
        diff_checks[key] = d
    big = any(d["level"] == "大きな乖離" for d in diff_checks.values())

    reasons, warnings = [], []
    if fresh is None:
        reasons.append("filing date 不明")
    elif fresh <= 180:
        reasons.append(f"filing {fresh}日前 → 鮮度良好")
    elif fresh <= 365:
        reasons.append(f"filing {fresh}日前 → 許容")
    else:
        reasons.append(f"filing {fresh}日前 → 古い")
        warnings.append(f"データが古い（{fresh}日前）")
    reasons.append(f"主要項目 {present}/{len(_MAIN_KEYS)} 取得（欠損 {missing}）")
    if present < 2:
        warnings.append("主要項目が少なすぎる")
    for key in ("margin", "rev_g", "eps_g"):
        d = diff_checks[key]; dp = d.get("diff_pp"); lab = d.get("label", key)
        if dp is None:
            reasons.append(f"{lab}差: 判定不可（片方欠損）")
        elif d["level"] == "OK":
            reasons.append(f"{lab}差 {abs(dp):.1f}pt → 問題なし")
        else:
            tail = "要注意" if d["level"] == "注意" else "大きな乖離 → 要確認"
            reasons.append(f"{lab}差 {abs(dp):.1f}pt → {tail}")
            warnings.append(f"{lab}差 {dp:+.1f}pt（{d['level']}）")

    too_old = (fresh is not None and fresh > 365)
    if too_old or present < 2 or big:
        quality = "低"
    elif (fresh is not None and fresh <= 180) and present >= 3 and not big:
        quality = "高"
    else:
        quality = "中"

    return {"quality": quality, "confidence": quality, "reasons": reasons, "warnings": warnings,
            "missing_count": missing, "freshness_days": fresh, "diff_checks": diff_checks}
