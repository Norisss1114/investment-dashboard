"""
discovery_adoption.py (v25) — 改善案検証で良かった条件の「採用候補」保存。

⚠️ 本番スコア・本番ランキング・発掘ロジックには一切接続しない。
承認(approve)しても status を変えるだけで本番反映は行わない（人間の確認用の控え）。
保存先: user_data/discovery_adoption_candidates.json（壊れても落ちない）。
"""
import datetime as dt

import config
from modules import storage

PATH = config.DISCOVERY_ADOPTION_PATH


# ---------------- 保存・読込（壊れても安全） ----------------
def load():
    data = storage.load_json(PATH, [])
    return data if isinstance(data, list) else []


def save(records):
    return storage.save_json(PATH, records)


def list_all():
    return load()


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def filter_id(filter_key):
    """filter spec dict → 安定な id 文字列（例 'rs_max=10'）。"""
    if not isinstance(filter_key, dict):
        return str(filter_key)
    return "|".join(f"{k}={filter_key[k]}" for k in sorted(filter_key))


# ---------------- 追加（dedup＋source統合） ----------------
def _upsert(filter_label, filter_key, source, section_name, section):
    """source ごとに bt/wf セクションを追加/更新。同一filterは1レコードに統合。"""
    rid = filter_id(filter_key)
    recs = load()
    rec = next((r for r in recs if r.get("id") == rid), None)
    now = _now()
    if rec is None:
        rec = {"id": rid, "filter_label": filter_label, "filter_key": filter_key,
               "source": source, "created_at": now, "updated_at": now,
               "status": "candidate", "approved_at": None, "rejected_at": None,
               "notes": "", "bt": None, "wf": None}
        recs.append(rec)
    rec[section_name] = section
    rec["updated_at"] = now
    rec["filter_label"] = filter_label
    # source 統合（bt と wf の両方があれば「両方」）
    has_bt = rec.get("bt") is not None
    has_wf = rec.get("wf") is not None
    rec["source"] = "両方" if (has_bt and has_wf) else ("通常BT" if has_bt else "WF")
    save(recs)
    return True, f"{filter_label} を採用候補に保存しました（{rec['source']}）"


def add_from_bt(variant, baseline):
    """通常BT検証のバリアントを採用候補に保存（ゲート判定は呼び出し側）。"""
    section = {
        "baseline": {k: baseline.get(k) for k in
                     ("total_return", "win_rate", "avg_return", "sharpe", "max_drawdown",
                      "trade_count", "vs_spy", "vs_qqq", "vs_random")},
        "candidate": {k: variant.get(k) for k in
                      ("total_return", "win_rate", "avg_return", "sharpe", "max_drawdown",
                       "trade_count", "vs_spy", "vs_qqq", "vs_random")},
        "improvement": round((variant.get("total_return", 0) or 0) - (baseline.get("total_return", 0) or 0), 1),
        "verdict": variant.get("verdict"),
    }
    return _upsert(variant.get("name", ""), variant.get("filter", {}), "通常BT", "bt", section)


def add_from_wf(variant, baseline):
    """WF検証のバリアントを採用候補に保存（ゲート判定は呼び出し側）。"""
    bs = baseline.get("summary", {}) or {}
    vs = variant.get("summary", {}) or {}
    keys = ("avg_oos_return", "positive_folds_pct", "beat_spy_pct", "beat_qqq_pct",
            "beat_random_pct", "avg_sharpe", "avg_max_dd")
    section = {
        "baseline": {k: bs.get(k) for k in keys},
        "candidate": {k: vs.get(k) for k in keys},
        "improvement": round((vs.get("avg_oos_return", 0) or 0) - (bs.get("avg_oos_return", 0) or 0), 1),
        "verdict": variant.get("verdict"),
        "avg_trade_count": variant.get("avg_trade_count"),
        "valid_folds": variant.get("valid_folds"),
    }
    return _upsert(variant.get("name", ""), variant.get("filter", {}), "WF", "wf", section)


# ---------------- 状態変更（本番反映なし） ----------------
def _set_status(rid, status):
    recs = load()
    now = _now()
    for r in recs:
        if r.get("id") == rid:
            r["status"] = status
            r["updated_at"] = now
            if status == "approved":
                r["approved_at"] = now
            elif status == "rejected":
                r["rejected_at"] = now
            save(recs)
            return True
    return False


def approve(rid):
    return _set_status(rid, "approved")


def reject(rid):
    return _set_status(rid, "rejected")


def remove(rid):
    save([r for r in load() if r.get("id") != rid])
    return True


def clear():
    save([])
    return True


# ---------------- 保存ゲート（呼び出し側の判定補助） ----------------
def bt_savable(variant, baseline):
    if variant.get("verdict") != "改善":
        return False
    if (variant.get("trade_count", 0) or 0) < 10:
        return False
    imp = (variant.get("total_return", 0) or 0) - (baseline.get("total_return", 0) or 0)
    return imp >= 3


def wf_savable(variant, baseline):
    if variant.get("verdict") != "改善":
        return False
    if (variant.get("avg_trade_count", 0) or 0) < 10:
        return False
    av = (variant.get("summary", {}) or {}).get("avg_oos_return")
    bv = (baseline.get("summary", {}) or {}).get("avg_oos_return")
    if av is None or bv is None:
        return False
    return (av - bv) >= 3
