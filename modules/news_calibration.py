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
