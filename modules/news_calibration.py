"""
news_calibration.py (v27) — ニュース影響度の「較正用データ」作成・保存・表示。

目的: ニュースイベント（見出し/実日付/分類/impact）と、その後の株価リターンを
保存して、将来の較正（ニューススコアの当たり外れ検証）に使うデータを貯める。

⚠️ 本番ニューススコア・発掘スコア・本番ランキングには一切接続しない（保存/表示のみ）。
⚠️ 学習モデルは作らない。集計分析は v27.1 以降。

最重要ルール:
  - 本物の日付付きニュースだけ保存する。Finnhub company-news の `datetime`(UNIX) を使う。
  - datetime が無い / 0 / 不明 のニュースは保存しない。
  - news_analysis._days_ago のようなハッシュ疑似日付は絶対に使わない。

ニュース取得: Finnhub company-news を read-only で直接取得（news.py は編集しない）。
分類: news._classify_keyword を read-only で利用（classifier="keyword"）。
リターン: data_fetch.get_price_history を read-only で利用（mock/失敗は unavailable）。
タイムゾーン差は discovery_tracking と同じ方針で吸収する。落ちない設計。
"""
import datetime as dt

import config
from modules import storage, data_fetch, news as news_mod

PATH = config.NEWS_CALIBRATION_PATH
FETCH_DAYS = 90    # 取得対象（過去何日分のニュースを探すか）
FETCH_LIMIT = 50   # 1回の取得上限
HORIZONS = (1, 3, 7, 30)  # 取引日ベースの経過日数


# ---------------- 保存・読込（壊れても落ちない） ----------------
def load():
    data = storage.load_json(PATH, [])
    return data if isinstance(data, list) else []


def save(records):
    return storage.save_json(PATH, records)


def list_all(ticker=None):
    recs = load()
    if ticker:
        t = str(ticker).strip().upper()
        recs = [r for r in recs if r.get("ticker") == t]
    return recs


def remove(rid):
    save([r for r in load() if r.get("id") != rid])
    return True


def clear():
    save([])
    return True


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _rec_id(ticker, news_date, headline):
    """ticker × 実日付 × 見出し で安定な id。dedup に使う。"""
    return f"{ticker}|{news_date}|{(headline or '')[:60]}"


# ---------------- Finnhub company-news 直接取得（実日付のみ） ----------------
def _fetch_news_with_dates(ticker, days=FETCH_DAYS, limit=FETCH_LIMIT):
    """Finnhub company-news を read-only 取得。`datetime`(UNIX) を実日付化して返す。
    datetime が無い/0/不明の要素は除外する（疑似日付は作らない）。
    キー未設定・失敗・該当なしは [] を返す（落ちない）。"""
    key = config.get_secret("FINNHUB_API_KEY")
    if not key:
        return []
    t = str(ticker).strip().upper()
    try:
        import requests
        today = dt.date.today()
        frm = (today - dt.timedelta(days=days)).isoformat()
        r = requests.get("https://finnhub.io/api/v1/company-news",
                         params={"symbol": t, "from": frm, "to": today.isoformat(), "token": key},
                         timeout=10)
        if r.status_code != 200:
            return []
        data = r.json() or []
    except Exception:
        return []

    out = []
    for d in data:
        if not isinstance(d, dict):
            continue
        ts = d.get("datetime")
        # 実日付チェック: 数値かつ正の値のみ採用。無い/0/不明は除外。
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            continue
        if ts <= 0:
            continue
        try:
            news_date = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            continue
        headline = (d.get("headline", "") or "").strip()
        if not headline:
            continue
        out.append({
            "headline": headline,
            "summary": (d.get("summary", "") or "").strip(),
            "source": d.get("source", "Finnhub") or "Finnhub",
            "url": d.get("url", "") or "",
            "news_date": news_date,
        })
        if len(out) >= limit:
            break
    return out


def _classify(item):
    """news._classify_keyword を read-only 利用して分類値を取り出す。"""
    try:
        c = news_mod._classify_keyword({"headline": item.get("headline", "")})
    except Exception:
        c = {}
    return {
        "impact_score": c.get("impact"),
        "categories": [c.get("category")] if c.get("category") else [],
        "sentiment": c.get("lean"),
    }


# ---------------- 追加（実日付のみ・dedup） ----------------
def add_for_ticker(ticker, days=FETCH_DAYS, limit=FETCH_LIMIT):
    """この銘柄の Finnhub ニュース（実日付付きのみ）を較正データに保存。
    返り値 (ok, msg)。同一 id は重複保存しない。"""
    t = str(ticker or "").strip().upper()
    if not t:
        return False, "ティッカーがありません"
    if not config.get_secret("FINNHUB_API_KEY"):
        return False, "FINNHUB_API_KEY未設定のため取得できません（保存なし）"

    fetched = _fetch_news_with_dates(t, days, limit)
    if not fetched:
        return False, f"{t}: 実日付付きのニュースが取得できませんでした（保存なし）"

    recs = load()
    existing = {r.get("id") for r in recs}
    now = _now()
    added = 0
    for it in fetched:
        rid = _rec_id(t, it["news_date"], it["headline"])
        if rid in existing:
            continue
        cls = _classify(it)
        recs.append({
            "id": rid, "ticker": t,
            "headline": it["headline"], "summary": it["summary"],
            "source": it["source"], "url": it["url"],
            "news_date": it["news_date"], "saved_at": now,
            "classifier": "keyword",
            "impact_score": cls["impact_score"],
            "categories": cls["categories"],
            "sentiment": cls["sentiment"],
            # 実績（未更新）
            "price_at_news": None, "spy_at_news": None, "qqq_at_news": None,
            "ret_1": None, "ret_3": None, "ret_7": None, "ret_30": None,
            "vs_spy_7": None, "vs_spy_30": None, "vs_qqq_7": None, "vs_qqq_30": None,
            "updated_at": None, "data_ok": None, "status": "pending",
        })
        existing.add(rid)
        added += 1
    save(recs)
    if added == 0:
        return True, f"{t}: 新規ニュースなし（保存済みと重複）。取得 {len(fetched)} 件はすべて既存です。"
    return True, f"{t}: 実日付付きニュース {added} 件を較正データに保存しました（取得 {len(fetched)} 件中）。"


# ---------------- リターン更新 ----------------
def _close_series(ticker):
    """終値Series（DatetimeIndex）を返す。mock/失敗は (None, False)。"""
    try:
        df, src = data_fetch.get_price_history(ticker, config.PRICE_PERIOD, config.PRICE_INTERVAL, use_cache=True)
        if src != "yfinance" or df is None or len(df) == 0:
            return None, False
        c = df["Close"].dropna()
        return (c, True) if len(c) else (None, False)
    except Exception:
        return None, False


def _pos_asof(close, date):
    """date 当日（取引日でなければ翌取引日）の位置。範囲外は None。tz有無に対応。"""
    try:
        import pandas as pd
        ts = pd.Timestamp(date)
        tz = getattr(close.index, "tz", None)
        if tz is not None:
            ts = ts.tz_localize(tz)
        pos = int(close.index.searchsorted(ts))  # ts 以上で最初の位置
        if 0 <= pos < len(close):
            return pos
    except Exception:
        pass
    return None


def _ret_n(close, pos0, n, anchor):
    """pos0 から n 取引日後のリターン(%)。未到達/データ不足は None。"""
    if pos0 is None or anchor is None or anchor <= 0:
        return None
    pos_n = pos0 + n
    if pos_n >= len(close):
        return None  # 未到達 または データ不足
    try:
        return round((float(close.iloc[pos_n]) / anchor - 1) * 100, 1)
    except Exception:
        return None


def update_returns(progress_cb=None):
    """全レコードの news_date 起点リターンを更新。返り値=処理件数。手動ボタンからのみ。
    mock/取得失敗は status='unavailable'、未到達 horizon は None。"""
    recs = load()
    if not recs:
        return 0
    spy_close, spy_ok = _close_series("SPY")
    qqq_close, qqq_ok = _close_series("QQQ")
    spy_pos_cache, qqq_pos_cache = {}, {}
    series_cache = {}
    now = _now()

    def _bench_ret(bclose, bok, cache, news_date, n):
        if not bok:
            return None
        if news_date not in cache:
            cache[news_date] = _pos_asof(bclose, news_date)
        bpos = cache[news_date]
        if bpos is None:
            return None
        try:
            anchor = float(bclose.iloc[bpos])
        except Exception:
            return None
        return _ret_n(bclose, bpos, n, anchor)

    for k, r in enumerate(recs, 1):
        t = r.get("ticker", "")
        nd = r.get("news_date")
        r["updated_at"] = now
        if t not in series_cache:
            series_cache[t] = _close_series(t)
        close, ok = series_cache[t]

        if not ok or not nd:
            r["data_ok"] = False
            r["status"] = "unavailable"
            if progress_cb:
                progress_cb(k, len(recs), t)
            continue

        pos0 = _pos_asof(close, nd)
        if pos0 is None:
            r["data_ok"] = False
            r["status"] = "unavailable"
            if progress_cb:
                progress_cb(k, len(recs), t)
            continue

        anchor = float(close.iloc[pos0])
        r["price_at_news"] = round(anchor, 2)
        r["data_ok"] = True
        r["status"] = "updated"
        for n in HORIZONS:
            r[f"ret_{n}"] = _ret_n(close, pos0, n, anchor)

        # ベンチ基準値（参考表示用）
        spy_b = _bench_ret(spy_close, spy_ok, spy_pos_cache, nd, 7)
        if spy_ok and nd in spy_pos_cache and spy_pos_cache[nd] is not None:
            try:
                r["spy_at_news"] = round(float(spy_close.iloc[spy_pos_cache[nd]]), 2)
            except Exception:
                r["spy_at_news"] = None
        if qqq_ok:
            _bench_ret(qqq_close, qqq_ok, qqq_pos_cache, nd, 7)  # populate cache
            if nd in qqq_pos_cache and qqq_pos_cache[nd] is not None:
                try:
                    r["qqq_at_news"] = round(float(qqq_close.iloc[qqq_pos_cache[nd]]), 2)
                except Exception:
                    r["qqq_at_news"] = None

        for n in (7, 30):
            sp = _bench_ret(spy_close, spy_ok, spy_pos_cache, nd, n)
            qq = _bench_ret(qqq_close, qqq_ok, qqq_pos_cache, nd, n)
            rn = r.get(f"ret_{n}")
            r[f"vs_spy_{n}"] = (round(rn - sp, 1) if (rn is not None and sp is not None) else None)
            r[f"vs_qqq_{n}"] = (round(rn - qq, 1) if (rn is not None and qq is not None) else None)

        if progress_cb:
            progress_cb(k, len(recs), t)

    save(recs)
    return len(recs)


# ---------------- v27.1: 集計分析（表示のみ・スコア非変更） ----------------
def _avg(vals):
    """None を除外した平均(1桁)。該当なしは None。"""
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _win_rate(rets):
    """ret_30>0 を勝ちとした勝率(%)。母数は ret_30 非Noneのみ。該当なしは None。"""
    rs = [v for v in rets if v is not None]
    if not rs:
        return None
    return round(sum(1 for v in rs if v > 0) / len(rs) * 100)


def _stats(recs):
    """更新済みレコード群の集計（件数/平均ret7,30/平均vs_spy30/勝率）。"""
    n = len(recs)
    return {
        "n": n,
        "avg_ret7": _avg([r.get("ret_7") for r in recs]),
        "avg_ret30": _avg([r.get("ret_30") for r in recs]),
        "avg_vs_spy30": _avg([r.get("vs_spy_30") for r in recs]),
        "win_rate": _win_rate([r.get("ret_30") for r in recs]),
    }


def _breakdown(recs, key_fn, multi=False):
    """key_fn(rec)->ラベル(またはラベルlist) でグルーピングし各グループ統計。"""
    groups = {}
    for r in recs:
        labs = key_fn(r)
        if labs is None:
            continue
        if not multi:
            labs = [labs]
        for lab in labs:
            if lab is None:
                continue
            groups.setdefault(lab, []).append(r)
    rows = []
    for lab, lst in groups.items():
        s = _stats(lst)
        s["label"] = lab
        rows.append(s)
    return rows


def analytics(records=None):
    """較正データを集計（表示のみ・スコア非変更）。空/未更新でも落ちない。
    集計対象は status=='updated'。各平均は None を除外。pending/unavailable は件数のみ。"""
    recs = records if records is not None else load()
    if not isinstance(recs, list):
        recs = []
    saved = len(recs)
    updated_recs = [r for r in recs if r.get("status") == "updated"]
    updated = len(updated_recs)
    ret7_count = sum(1 for r in updated_recs if r.get("ret_7") is not None)
    ret30_count = sum(1 for r in updated_recs if r.get("ret_30") is not None)

    overall = {
        "saved": saved, "updated": updated,
        "ret7_count": ret7_count, "ret30_count": ret30_count,
        "avg_ret7": _avg([r.get("ret_7") for r in updated_recs]),
        "avg_ret30": _avg([r.get("ret_30") for r in updated_recs]),
        "avg_vs_spy30": _avg([r.get("vs_spy_30") for r in updated_recs]),
        "win_rate_30": _win_rate([r.get("ret_30") for r in updated_recs]),
    }

    # impact_score 実値別（-5〜+5。None は除外、負値も保持）。impact 昇順で並べる。
    by_impact = _breakdown(updated_recs,
                           lambda r: (r.get("impact_score") if isinstance(r.get("impact_score"), int) else None))
    by_impact.sort(key=lambda x: x["label"])

    # sentiment 別（bull/neutral/bear の順で固定表示）
    by_sentiment = _breakdown(updated_recs,
                             lambda r: (r.get("sentiment") if r.get("sentiment") in ("bull", "neutral", "bear") else None))
    _sent_order = {"bull": 0, "neutral": 1, "bear": 2}
    by_sentiment.sort(key=lambda x: _sent_order.get(x["label"], 9))

    # category 別（categories 配列に出現した値ごと・データ駆動）
    by_category = _breakdown(updated_recs,
                            lambda r: ([c for c in (r.get("categories") or []) if c] or None),
                            multi=True)
    by_category.sort(key=lambda x: -x["n"])

    return {"overall": overall, "by_impact": by_impact,
            "by_sentiment": by_sentiment, "by_category": by_category}


# ---------------- v27.1: 較正提案（ルールベース・表示のみ） ----------------
def recommendation(records=None):
    """impact_score の妥当性検証＋強い/弱い条件抽出（表示のみ・スコア非変更）。
    判定対象 n<3 は low_sample に隔離し提案には使わない。"""
    a = analytics(records)
    ov = a["overall"]
    updated = ov.get("updated", 0)
    # 判定母数 = ret_30 が計算済みの更新レコード数
    judged = ov.get("ret30_count", 0) or 0

    if not judged:
        return {"confidence": "低", "judged": 0,
                "monotonic_ok": None, "impact_issues": [],
                "strong": [], "weak": [], "low_sample": [],
                "suggestions": [], "data_insufficient": True,
                "next_steps": ["「🔄 較正リターンを更新」で ret_30 が貯まるまで継続してください。"]}

    confidence = "高" if judged >= 10 else "中" if judged >= 5 else "低"

    # n>=3 を有効、n<3 を low_sample に隔離
    def split(rows, category):
        ok, low = [], []
        for r in rows:
            rec = {"category": category, "label": r["label"], "n": r["n"],
                   "avg_ret7": r.get("avg_ret7"), "avg_ret30": r.get("avg_ret30"),
                   "avg_vs_spy30": r.get("avg_vs_spy30"), "win_rate": r.get("win_rate")}
            (ok if r["n"] >= 3 else low).append(rec)
        return ok, low

    imp_ok, imp_low = split(a["by_impact"], "impact")
    sen_ok, sen_low = split(a["by_sentiment"], "sentiment")
    cat_ok, cat_low = split(a["by_category"], "category")
    low_sample = imp_low + sen_low + cat_low

    # impact 単調性チェック（impact が高いほど ret_30 平均が高い、が理想）
    impact_issues = []
    monotonic_ok = None
    pts = [(r["label"], r["avg_ret30"]) for r in imp_ok if r["avg_ret30"] is not None]
    pts.sort(key=lambda x: x[0])
    if len(pts) >= 2:
        monotonic_ok = True
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                imp_lo, ret_lo = pts[i]
                imp_hi, ret_hi = pts[j]
                # 高impactの平均ret30が低impactより明確に低い → 逆転
                if ret_hi + 3 < ret_lo:
                    monotonic_ok = False
                    impact_issues.append(
                        f"impact{imp_hi}（平均ret30 {ret_hi:+.1f}%）が impact{imp_lo}（{ret_lo:+.1f}%）より低い "
                        f"→ impact{imp_hi} は過大評価の可能性")
    # 高impactなのにマイナス / 低impactなのに高プラス の単発指摘
    for lab, ret in pts:
        if lab >= 4 and ret < 0:
            impact_issues.append(f"impact{lab} は強気想定だが平均ret30 {ret:+.1f}% → 過大評価の可能性")
        if lab <= 1 and ret >= 5:
            impact_issues.append(f"impact{lab} は弱め想定だが平均ret30 {ret:+.1f}% → 過小評価の可能性")

    # 強い/弱い条件抽出（全カテゴリ横断・n>=3）
    osr = ov.get("avg_ret30")
    strong, weak = [], []
    for rec in (imp_ok + sen_ok + cat_ok):
        ar = rec.get("avg_ret30")
        av = rec.get("avg_vs_spy30")
        wr = rec.get("win_rate")
        is_strong = ((ar is not None and ar >= 5) or (av is not None and av >= 3)
                     or (wr is not None and wr >= 70))
        is_weak = ((ar is not None and ar <= -5) or (av is not None and av <= -3)
                   or (wr is not None and wr <= 30))
        if is_strong and not is_weak:
            strong.append(rec)
        elif is_weak:
            weak.append(rec)
    strong.sort(key=lambda x: -(x["avg_ret30"] if x["avg_ret30"] is not None else -999))
    weak.sort(key=lambda x: (x["avg_ret30"] if x["avg_ret30"] is not None else 999))

    _cat_label = {"impact": "impact", "sentiment": "sentiment", "category": "category"}

    def line(r, tail):
        bits = []
        if r["avg_ret30"] is not None:
            bits.append(f'平均ret30 {r["avg_ret30"]:+.1f}%')
        if r["avg_vs_spy30"] is not None:
            bits.append(f'vsSPY {r["avg_vs_spy30"]:+.1f}%')
        if r["win_rate"] is not None:
            bits.append(f'勝率{r["win_rate"]}%')
        return f'{_cat_label[r["category"]]}「{r["label"]}」：' + " / ".join(bits) + f'（n={r["n"]}）→ {tail}'

    suggestions = list(impact_issues)
    for r in strong[:4]:
        suggestions.append(line(r, "当たりやすい傾向"))
    for r in weak[:4]:
        suggestions.append(line(r, "外しやすい/過信注意"))
    if confidence != "高":
        suggestions.append("サンプルが少ないため参考値です。まだニューススコアには反映しないでください。")

    next_steps = ["「🔄 較正リターンを更新」で ret_30 を増やし、判定対象 n≥10 を目指してください。"]
    if impact_issues:
        next_steps.append("impact_score の妥当性に疑問あり。逆転している impact 帯の中身を確認してください。")
    if strong:
        next_steps.append("当たりやすい条件は、まず手動で傾向を確認（スコア重み変更はまだしない）。")

    return {"confidence": confidence, "judged": judged,
            "monotonic_ok": monotonic_ok, "impact_issues": impact_issues,
            "strong": strong, "weak": weak, "low_sample": low_sample,
            "suggestions": suggestions, "data_insufficient": False,
            "next_steps": next_steps}


# ---------------- v28: ニューススコア補正案ジェネレーター（表示のみ・スコア非反映） ----------------
CORR_MIN_SAMPLE = 5  # n>=5 のみ補正案。n<5 は insufficient。


def _impact_delta(avg_ret30, avg_vs_spy30, win_rate):
    """実績指標から impact 補正段階(delta)と根拠ラベル群を返す。該当なしは (0, [])。
    優先順: 大きく強い/大きく弱い → 強い/弱い。同強度の強弱矛盾は |avg_vs_spy30| で優先。"""
    ar, av, wr = avg_ret30, avg_vs_spy30, win_rate
    sr = f"{ar:+.1f}%" if ar is not None else "—"
    sv = f"{av:+.1f}%" if av is not None else "—"
    sw = f"{wr}%" if wr is not None else "—"

    def hit(conds):
        return [c for c, ok in conds if ok]

    big_strong = hit([(f"avg_ret30 {sr}≥+10", ar is not None and ar >= 10),
                      (f"vsSPY30 {sv}≥+7", av is not None and av >= 7),
                      (f"勝率{sw}≥70", wr is not None and wr >= 70)])
    big_weak = hit([(f"avg_ret30 {sr}≤-7", ar is not None and ar <= -7),
                    (f"vsSPY30 {sv}≤-5", av is not None and av <= -5),
                    (f"勝率{sw}≤35", wr is not None and wr <= 35)])
    strong = hit([(f"avg_ret30 {sr}≥+5", ar is not None and ar >= 5),
                  (f"vsSPY30 {sv}≥+3", av is not None and av >= 3),
                  (f"勝率{sw}≥60", wr is not None and wr >= 60)])
    weak = hit([(f"avg_ret30 {sr}≤-3", ar is not None and ar <= -3),
                (f"vsSPY30 {sv}≤-3", av is not None and av <= -3),
                (f"勝率{sw}≤45", wr is not None and wr <= 45)])

    # 1) 大きく強い / 大きく弱い（同時該当は |avg_vs_spy30| 比較、無ければ +2 優先）
    if big_strong and big_weak:
        if av is not None and av < 0:
            return -2, big_weak
        return 2, big_strong
    if big_strong:
        return 2, big_strong
    if big_weak:
        return -2, big_weak
    # 2) 強い / 弱い（同時該当は |avg_vs_spy30| 比較、無ければ +1 優先）
    if strong and weak:
        if av is not None and av < 0:
            return -1, weak
        return 1, strong
    if strong:
        return 1, strong
    if weak:
        return -1, weak
    return 0, []


def _clamp_score(v):
    return max(-5, min(5, v))


def correction_proposals(records=None):
    """較正データの集計(analytics)から、impact/sentiment/category の補正案を生成（表示のみ）。
    ⚠️ 本番ニューススコア・発掘スコア・ランキングには一切反映しない。
    n>=5 のみ補正対象。n<5 は insufficient。空/未更新でも落ちない。"""
    a = analytics(records)
    ov = a["overall"]
    ret30_count = ov.get("ret30_count", 0) or 0
    confidence = "高" if ret30_count >= 10 else "中" if ret30_count >= 5 else "低"

    impact_props, sentiment_props, category_props, insufficient = [], [], [], []

    # ---- impact_score 補正 ----
    for r in a.get("by_impact", []):
        n = r.get("n") or 0
        if n < CORR_MIN_SAMPLE:
            insufficient.append({"block": "impact", "label": r["label"], "n": n})
            continue
        delta, why = _impact_delta(r.get("avg_ret30"), r.get("avg_vs_spy30"), r.get("win_rate"))
        if delta == 0:
            continue
        cur = r["label"]
        recommended = _clamp_score(cur + delta)
        if recommended == cur:  # 上限/下限で動かない場合は補正案にしない
            continue
        if delta > 0:
            reason = "中impactだが実績が強く、" + "・".join(why) + " → 過小評価の可能性"
            if cur >= 4:
                reason = "実績が強く " + "・".join(why) + " → さらに上方の可能性"
        else:
            reason = "高impactなのに実績が弱く、" + "・".join(why) + " → 過大評価の可能性"
            if cur <= 0:
                reason = "実績が弱く " + "・".join(why) + " → さらに下方の可能性"
        impact_props.append({
            "label": cur, "current_score": cur, "recommended_score": recommended, "delta": delta,
            "reason": reason, "n": n, "avg_ret30": r.get("avg_ret30"),
            "avg_vs_spy30": r.get("avg_vs_spy30"), "win_rate": r.get("win_rate")})
    impact_props.sort(key=lambda x: x["label"])

    # ---- sentiment 評価（定性のみ・数値補正なし） ----
    for r in a.get("by_sentiment", []):
        n = r.get("n") or 0
        if n < CORR_MIN_SAMPLE:
            insufficient.append({"block": "sentiment", "label": r["label"], "n": n})
            continue
        ar, av, wr = r.get("avg_ret30"), r.get("avg_vs_spy30"), r.get("win_rate")
        strong = ((ar is not None and ar >= 5) or (av is not None and av >= 3)
                  or (wr is not None and wr >= 60))
        weak_down = ((ar is not None and ar <= -3) or (av is not None and av <= -3))
        lab = r["label"]
        if lab == "bull":
            ev = "bull信頼度 高" if strong else "bull信頼度 低/要確認"
            reason = ("bull判定が実際にプラスを出している" if strong
                      else "bull判定だが実績が伴っていない")
        elif lab == "bear":
            ev = "bear信頼度 高" if weak_down else "bear信頼度 低/要確認"
            reason = ("bear判定が実際に下げている" if weak_down
                      else "bear判定だが実際は下げていない")
        else:  # neutral
            if strong:
                ev, reason = "neutral過小評価", "neutralでも実績が強い → 過小評価の可能性"
            elif weak_down:
                ev, reason = "neutral弱い", "neutralで実績も弱い"
            else:
                ev, reason = "neutral妥当", "neutralは概ね中立的な実績"
        sentiment_props.append({"label": lab, "evaluation": ev, "reason": reason,
                                "n": n, "avg_ret30": ar, "avg_vs_spy30": av, "win_rate": wr})

    # ---- category 補正（+1 / -1。0 は出さない） ----
    for r in a.get("by_category", []):
        n = r.get("n") or 0
        if n < CORR_MIN_SAMPLE:
            insufficient.append({"block": "category", "label": r["label"], "n": n})
            continue
        ar, av, wr = r.get("avg_ret30"), r.get("avg_vs_spy30"), r.get("win_rate")
        strong = (ar is not None and ar >= 5) or (av is not None and av >= 3)
        weak = (ar is not None and ar <= -3) or (av is not None and av <= -3)
        sr = f"{ar:+.1f}%" if ar is not None else "—"
        sv = f"{av:+.1f}%" if av is not None else "—"
        if strong and not weak:
            corr, reason = 1, f"強いカテゴリ（ret30 {sr} / vsSPY30 {sv}）→ +1 補正候補"
        elif weak:
            corr, reason = -1, f"弱いカテゴリ（ret30 {sr} / vsSPY30 {sv}）→ -1 補正候補"
        else:
            continue
        category_props.append({"label": r["label"], "correction": corr, "reason": reason,
                               "n": n, "avg_ret30": ar, "avg_vs_spy30": av, "win_rate": wr})

    data_insufficient = (ret30_count == 0)
    return {"confidence": confidence, "impact": impact_props, "sentiment": sentiment_props,
            "category": category_props, "insufficient": insufficient,
            "data_insufficient": data_insufficient}
