"""
news_adoption.py (v27.2) — ニュース較正提案の「検証候補」保存。

v27.1 の news_calibration.analytics()/recommendation() が出す
  - impact_score の過大/過小評価候補（impact_adjustment）
  - sentiment の強い/弱い条件
  - category の強い/弱い条件
を「検証候補」として保存・承認・却下・削除する。

⚠️ 本番ニューススコア・発掘スコア・本番ランキングには一切接続しない。
   承認(approve)しても status を変えるだけで本番反映は行わない（人間の確認用の控え）。
   news.py / scoring.py / engine.py / discovery* / optimizer.py は import も呼び出しもしない。
保存先: user_data/news_adoption_candidates.json（壊れても落ちない）。
候補抽出は recommendation()/analytics() の構造化データから組み立てる（文字列パースしない）。
"""
import datetime as dt

from modules import storage, news_calibration as ncal

PATH = "user_data/news_adoption_candidates.json"
MIN_SAMPLE = 3  # n>=3 のみ候補化（low_sample は保存しない）


# ---------------- 保存・読込（壊れても落ちない） ----------------
def load():
    data = storage.load_json(PATH, [])
    return data if isinstance(data, list) else []


def save(records):
    return storage.save_json(PATH, records)


def list_all():
    return load()


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _cand_id(ctype, label):
    """type|label の安定キー（dedup用）。例 'impact_adjustment|impact5'。"""
    return f"{ctype}|{label}"


def _evidence(row):
    """analytics/recommendation の行から evidence dict を作る。"""
    return {
        "avg_ret7": row.get("avg_ret7"),
        "avg_ret30": row.get("avg_ret30"),
        "avg_vs_spy30": row.get("avg_vs_spy30"),
        "win_rate": row.get("win_rate"),
        "sample_size": row.get("n"),
    }


# ---------------- 候補抽出（構造化データから・文字列パースしない） ----------------
def build_candidates(records=None):
    """recommendation()/analytics() の構造化データから検証候補リストを組み立てる。
    n>=3 の有効候補のみ。重複（同一 type|label）は最初の1件に統合。"""
    a = ncal.analytics(records)
    rec = ncal.recommendation(records)
    confidence = rec.get("confidence", "低")
    out = {}

    def _add(ctype, label, suggestion, row, current_score=None):
        if (row.get("n") or 0) < MIN_SAMPLE:
            return
        cid = _cand_id(ctype, label)
        if cid in out:
            return
        out[cid] = {
            "id": cid, "type": ctype, "label": label, "suggestion": suggestion,
            "current_score": current_score, "evidence": _evidence(row),
            "confidence": confidence,
        }

    # 1) impact_adjustment（過大/過小評価）: analytics.by_impact から逆転・乖離を構造判定
    imp_rows = [r for r in a.get("by_impact", []) if (r.get("n") or 0) >= MIN_SAMPLE
                and r.get("avg_ret30") is not None]
    imp_rows.sort(key=lambda x: x["label"])
    for i, lo in enumerate(imp_rows):
        # 高impactなのに低impactより平均ret30が明確に低い → 過大評価
        for hi in imp_rows[i + 1:]:
            if hi["avg_ret30"] + 3 < lo["avg_ret30"]:
                _add("impact_adjustment", f"impact{hi['label']}", "過大評価の可能性",
                     hi, current_score=hi["label"])
        # 高impact(>=4)なのにマイナス → 過大評価
        if lo["label"] >= 4 and lo["avg_ret30"] < 0:
            _add("impact_adjustment", f"impact{lo['label']}", "過大評価の可能性",
                 lo, current_score=lo["label"])
        # 低impact(<=1)なのに高プラス → 過小評価
        if lo["label"] <= 1 and lo["avg_ret30"] >= 5:
            _add("impact_adjustment", f"impact{lo['label']}", "過小評価の可能性",
                 lo, current_score=lo["label"])

    # 2) sentiment / category: recommendation の strong/weak（既に n>=3 で構造化済み）
    for r in rec.get("strong", []):
        if r.get("category") in ("sentiment", "category"):
            _add(r["category"], r["label"], "強い条件", r)
    for r in rec.get("weak", []):
        if r.get("category") in ("sentiment", "category"):
            _add(r["category"], r["label"], "弱い条件", r)

    return list(out.values())


# ---------------- 追加（dedup・status保持） ----------------
def _upsert(cand):
    """候補1件を upsert。既存(同一id)は evidence/confidence/suggestion/updated_at を更新、
    status(approved/rejected) は保持。返り値 (created:bool)。"""
    recs = load()
    now = _now()
    existing = next((r for r in recs if r.get("id") == cand["id"]), None)
    if existing is None:
        recs.append({
            "id": cand["id"], "type": cand["type"], "label": cand["label"],
            "suggestion": cand["suggestion"], "current_score": cand.get("current_score"),
            "evidence": cand["evidence"], "confidence": cand.get("confidence"),
            "status": "candidate", "created_at": now, "updated_at": now,
            "approved_at": None, "rejected_at": None, "note": "",
        })
        save(recs)
        return True
    # 既存は内容だけ更新。status と日時(created/approved/rejected)は保持。
    existing["suggestion"] = cand["suggestion"]
    existing["current_score"] = cand.get("current_score")
    existing["evidence"] = cand["evidence"]
    existing["confidence"] = cand.get("confidence")
    existing["updated_at"] = now
    save(recs)
    return False


def save_candidates(records=None):
    """build_candidates の結果をまとめて保存（dedup）。返り値 (saved_total, created, updated)。"""
    cands = build_candidates(records)
    created = updated = 0
    for c in cands:
        if _upsert(c):
            created += 1
        else:
            updated += 1
    return len(cands), created, updated


# ---------------- 状態変更（本番反映なし） ----------------
def _set_status(cid, status):
    recs = load()
    now = _now()
    for r in recs:
        if r.get("id") == cid:
            r["status"] = status
            r["updated_at"] = now
            if status == "approved":
                r["approved_at"] = now
            elif status == "rejected":
                r["rejected_at"] = now
            save(recs)
            return True
    return False


def approve(cid):
    return _set_status(cid, "approved")


def reject(cid):
    return _set_status(cid, "rejected")


def remove(cid):
    save([r for r in load() if r.get("id") != cid])
    return True


def clear():
    save([])
    return True
