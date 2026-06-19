"""
discovery_tracking.py (v22) — 発掘候補の結果追跡。
発掘カードから手動で候補を保存し、後日「実績を更新」で7/30/60/90日リターンや
SPY/QQQ比較を計算して、発掘エンジンの実力を測る。

⚠️ 表示のみ。価格は data_fetch.get_price_history を read-only で利用（data_fetch は未編集）。
mock/取得失敗時は「未取得」とし、誤った値は出さない。教育・分析用で投資助言ではない。
"""
import datetime as dt

import config
from modules import storage, data_fetch

PATH = config.DISCOVERY_TRACKING_PATH
HORIZONS = (7, 30, 60, 90)


# ---------------- 保存・読込（壊れても落ちない） ----------------
def load():
    data = storage.load_json(PATH, [])
    return data if isinstance(data, list) else []


def save(records):
    return storage.save_json(PATH, records)


def _rec_id(ticker, day):
    return f"{ticker}@{day}"


# ---------------- 追加（重複防止） ----------------
def add(item):
    """発掘 item を追跡に追加。同一 ticker×発掘日 は重複追加しない。返り値 (ok, msg)。"""
    t = str(item.get("ticker", "")).strip().upper()
    if not t:
        return False, "ティッカーがありません"
    price = item.get("price")
    if not price or float(price) <= 0:
        return False, f"{t}: 発掘価格が不明のため追加できません"
    recs = load()
    today = dt.date.today().isoformat()
    rid = _rec_id(t, today)
    if any(r.get("id") == rid for r in recs):
        return False, f"{t} は本日分を追加済みです"
    recs.append({
        "id": rid, "ticker": t, "discovered_at": today,
        "discovery_price": round(float(price), 2),
        "score": item.get("score"), "verdict": item.get("verdict"),
        "sector": item.get("sector"), "reasons": (item.get("reasons") or [])[:6],
        "market_pct": item.get("market_pct"), "sector_rank": item.get("sector_rank"),
        "sector_neutral": item.get("sector_neutral"),
        "leader": item.get("leader"), "rs_pct": item.get("rs_pct"),  # v22.1: 分析用
        "vs_spy": item.get("vs_spy"), "vs_qqq": item.get("vs_qqq"),
        "vs_etf": item.get("vs_etf"), "etf": item.get("etf"),
        # 実績（未更新）
        "updated_at": None, "current_price": None, "ret_current": None,
        "ret_7": None, "ret_30": None, "ret_60": None, "ret_90": None,
        "spy_30": None, "qqq_30": None, "vs_spy_30": None, "vs_qqq_30": None,
        "result": "追跡中", "data_ok": None,
    })
    save(recs)
    return True, f"{t} を追跡に追加しました"


def remove(rid):
    save([r for r in load() if r.get("id") != rid])
    return True


def clear():
    save([])
    return True


# ---------------- 実績更新 ----------------
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


def _price_asof(close, date):
    """date 以前で最も新しい終値（無ければ None）。tz有無の両方に対応。"""
    try:
        import pandas as pd
        ts = pd.Timestamp(date)
        tz = getattr(close.index, "tz", None)
        if tz is not None:
            ts = ts.tz_localize(tz)
        sub = close[close.index <= ts]
        if len(sub):
            return float(sub.iloc[-1])
    except Exception:
        pass
    return None


def _bench_ret(bclose, bok, d0, n_days):
    if not bok:
        return None
    p0 = _price_asof(bclose, d0)
    p1 = _price_asof(bclose, d0 + dt.timedelta(days=n_days))
    if p0 and p1 and p0 > 0:
        return round((p1 / p0 - 1) * 100, 1)
    return None


def update(progress_cb=None):
    """全レコードの実績を更新。返り値 = 更新件数。手動ボタンからのみ呼ぶ想定。"""
    recs = load()
    if not recs:
        return 0
    today = dt.date.today()
    spy_close, spy_ok = _close_series("SPY")
    qqq_close, qqq_ok = _close_series("QQQ")
    series_cache = {}
    for k, r in enumerate(recs, 1):
        t = r["ticker"]
        if t not in series_cache:
            series_cache[t] = _close_series(t)
        close, ok = series_cache[t]
        try:
            d0 = dt.date.fromisoformat(r["discovered_at"])
        except Exception:
            d0 = today
        anchor = r.get("discovery_price")
        r["updated_at"] = today.isoformat()
        if not ok or not anchor or anchor <= 0:
            r["data_ok"] = False
            r["result"] = "未取得"
            if progress_cb:
                progress_cb(k, len(recs), t)
            continue
        r["data_ok"] = True
        cur = float(close.iloc[-1])
        r["current_price"] = round(cur, 2)
        r["ret_current"] = round((cur / anchor - 1) * 100, 1)
        for n in HORIZONS:
            if today >= d0 + dt.timedelta(days=n):
                p = _price_asof(close, d0 + dt.timedelta(days=n))
                r[f"ret_{n}"] = round((p / anchor - 1) * 100, 1) if p else None
            else:
                r[f"ret_{n}"] = None
        if today >= d0 + dt.timedelta(days=30):
            r["spy_30"] = _bench_ret(spy_close, spy_ok, d0, 30)
            r["qqq_30"] = _bench_ret(qqq_close, qqq_ok, d0, 30)
            r["vs_spy_30"] = (round(r["ret_30"] - r["spy_30"], 1)
                              if (r.get("ret_30") is not None and r["spy_30"] is not None) else None)
            r["vs_qqq_30"] = (round(r["ret_30"] - r["qqq_30"], 1)
                              if (r.get("ret_30") is not None and r["qqq_30"] is not None) else None)
        r["result"] = judge(r)
        if progress_cb:
            progress_cb(k, len(recs), t)
    save(recs)
    return len(recs)


# ---------------- 判定 ----------------
def judge(r):
    """30日を主判定。成功/失敗/様子見/追跡中/未取得。"""
    if r.get("data_ok") is False:
        return "未取得"
    try:
        d0 = dt.date.fromisoformat(r["discovered_at"])
    except Exception:
        return "追跡中"
    if dt.date.today() < d0 + dt.timedelta(days=30):
        return "追跡中"
    ret30 = r.get("ret_30")
    vs = r.get("vs_spy_30")
    if ret30 is None:
        return "未取得"
    if ret30 >= 5 or (vs is not None and vs >= 3):
        return "成功"
    if ret30 <= -5 or (vs is not None and vs <= -3):
        return "失敗"
    return "様子見"


def summary(records=None):
    recs = records if records is not None else load()
    out = {"成功": 0, "様子見": 0, "失敗": 0, "追跡中": 0, "未取得": 0, "total": len(recs)}
    for r in recs:
        out[r.get("result", "追跡中")] = out.get(r.get("result", "追跡中"), 0) + 1
    return out


# ---------------- v22.1: 追跡結果の分析（表示のみ） ----------------
_JUDGED = ("成功", "様子見", "失敗")


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _group_stats(recs):
    """判定済みレコード群の統計。成功率/平均ret_30/平均vs_spy_30/件数/勝率。"""
    judged = [r for r in recs if r.get("result") in _JUDGED]
    n = len(judged)
    succ = sum(1 for r in judged if r.get("result") == "成功")
    ret30 = [r.get("ret_30") for r in judged]
    vsspy = [r.get("vs_spy_30") for r in judged]
    pos = [r.get("ret_30") for r in judged if r.get("ret_30") is not None]
    return {
        "n": n,
        "success_rate": round(succ / n * 100) if n else None,
        "avg_ret30": _avg(ret30),
        "avg_vs_spy30": _avg(vsspy),
        "win_rate": (round(sum(1 for v in pos if v > 0) / len(pos) * 100) if pos else None),
        "success": succ,
    }


def _score_band(score):
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s >= 90:
        return "90+"
    if s >= 80:
        return "80-89"
    if s >= 70:
        return "70-79"
    if s >= 60:
        return "60-69"
    return "<60"


def _rs_band(rs):
    if rs is None:
        return None
    try:
        v = float(rs)
    except (TypeError, ValueError):
        return None
    if v <= 10:
        return "上位10%"
    if v <= 25:
        return "上位25%"
    return "その他"


def _breakdown(recs, key_fn, order=None):
    """key_fn(rec)->ラベル(None=除外) でグルーピングし、各グループの統計を返す。"""
    groups = {}
    for r in recs:
        lab = key_fn(r)
        if lab is None:
            continue
        groups.setdefault(lab, []).append(r)
    rows = []
    for lab, lst in groups.items():
        st = _group_stats(lst)
        st["label"] = lab
        st["total"] = len(lst)  # 判定前含む総数
        rows.append(st)
    if order:
        rows.sort(key=lambda x: order.index(x["label"]) if x["label"] in order else 999)
    else:
        rows.sort(key=lambda x: -(x["success_rate"] if x["success_rate"] is not None else -1))
    return rows


def analytics(records=None):
    """追跡データを集計（表示のみ）。空/全追跡中でも落ちない。"""
    recs = records if records is not None else load()
    overall = _group_stats(recs)
    overall["total"] = len(recs)

    by_score = _breakdown(recs, lambda r: _score_band(r.get("score")),
                          order=["90+", "80-89", "70-79", "60-69", "<60"])
    by_sector = _breakdown(recs, lambda r: r.get("sector") or None)
    by_verdict = _breakdown(recs, lambda r: r.get("verdict") or None,
                            order=["強いBUY", "BUY", "WATCH", "AVOID"])
    # リーダー別・相対強度別は項目を持つ新規追跡分のみ
    by_leader = _breakdown([r for r in recs if r.get("leader")], lambda r: r.get("leader"),
                           order=["リーダー", "フォロワー", "弱い"])
    by_rs = _breakdown([r for r in recs if r.get("rs_pct") is not None],
                       lambda r: _rs_band(r.get("rs_pct")), order=["上位10%", "上位25%", "その他"])

    # 発掘理由別（1銘柄が複数理由ならそれぞれにカウント）
    reason_groups = {}
    for r in recs:
        for rsn in (r.get("reasons") or []):
            reason_groups.setdefault(rsn, []).append(r)
    by_reason = []
    for rsn, lst in reason_groups.items():
        st = _group_stats(lst)
        st["label"] = rsn
        st["total"] = len(lst)
        by_reason.append(st)
    by_reason.sort(key=lambda x: -(x["success_rate"] if x["success_rate"] is not None else -1))

    return {"overall": overall, "by_score": by_score, "by_sector": by_sector,
            "by_reason": by_reason, "by_verdict": by_verdict,
            "by_leader": by_leader, "by_rs": by_rs}
