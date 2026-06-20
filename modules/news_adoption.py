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
# 任意フィールド。存在する時だけ保存/更新する（持たない候補の shape は不変）。
#   score_correction(v29): target/recommended_score/delta/reason
#   simulation_result(v31): source/verdict/approved_correction_count/applicable_news/
#                           eligible_n/before/after/improvement/reasons
_OPTIONAL_FIELDS = ("target", "recommended_score", "delta", "reason",
                    "source", "verdict", "approved_correction_count", "applicable_news",
                    "eligible_n", "before", "after", "improvement", "reasons")


def _upsert(cand):
    """候補1件を upsert。既存(同一id)は evidence/confidence/suggestion(+任意field)/updated_at を更新、
    status(approved/rejected) は保持。返り値 (created:bool)。"""
    recs = load()
    now = _now()
    existing = next((r for r in recs if r.get("id") == cand["id"]), None)
    if existing is None:
        rec = {
            "id": cand["id"], "type": cand["type"], "label": cand["label"],
            "suggestion": cand["suggestion"], "current_score": cand.get("current_score"),
            "evidence": cand["evidence"], "confidence": cand.get("confidence"),
            "status": "candidate", "created_at": now, "updated_at": now,
            "approved_at": None, "rejected_at": None, "note": "",
        }
        for f in _OPTIONAL_FIELDS:
            if f in cand:
                rec[f] = cand[f]
        recs.append(rec)
        save(recs)
        return True
    # 既存は内容だけ更新。status と日時(created/approved/rejected)は保持。
    existing["suggestion"] = cand["suggestion"]
    existing["current_score"] = cand.get("current_score")
    existing["evidence"] = cand["evidence"]
    existing["confidence"] = cand.get("confidence")
    existing["updated_at"] = now
    for f in _OPTIONAL_FIELDS:
        if f in cand:
            existing[f] = cand[f]
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


# ---------------- v29: ニューススコア補正案の検証候補化（保存のみ・本番非反映） ----------------
# 保存する sentiment 評価（示唆あり）。neutral妥当 / 信頼度低 / 要確認は保存しない。
_SENTIMENT_SAVE = ("bull信頼度 高", "bear信頼度 高", "neutral過小評価", "neutral弱い")


def _score_cand_id(target, label):
    """score_correction の安定キー。例 'score_correction|impact|5'。"""
    return f"score_correction|{target}|{label}"


def _score_evidence(row):
    """v28 補正案の行から evidence を作る（キーは n で統一）。"""
    return {
        "n": row.get("n"),
        "avg_ret30": row.get("avg_ret30"),
        "avg_vs_spy30": row.get("avg_vs_spy30"),
        "win_rate": row.get("win_rate"),
    }


def build_score_corrections(records=None):
    """v28 correction_proposals() から score_correction 候補を組み立てる。
    n>=5・delta!=0 は v28 側で既にフィルタ済み。sentiment は示唆ありのみ保存。"""
    cp = ncal.correction_proposals(records)
    confidence = cp.get("confidence", "低")
    out = []

    def _mk(target, id_key, label, suggestion, reason, row,
            current_score=None, recommended_score=None, delta=None):
        out.append({
            "id": _score_cand_id(target, id_key), "type": "score_correction",
            "target": target, "label": label,
            "current_score": current_score, "recommended_score": recommended_score, "delta": delta,
            "suggestion": suggestion, "reason": reason,
            "evidence": _score_evidence(row), "confidence": confidence,
        })

    # impact 補正（v28: n>=5 & delta!=0 のみ含まれる）。id は score の実値、label は impactN。
    for p in cp.get("impact", []):
        cur = p.get("current_score")
        label = f"impact{cur}"
        suggestion = "過小評価の可能性" if p["delta"] > 0 else "過大評価の可能性"
        _mk("impact", cur, label, suggestion, p.get("reason", ""), p,
            current_score=cur, recommended_score=p.get("recommended_score"), delta=p.get("delta"))

    # sentiment 評価（示唆ありのみ。数値 score は None）。id/label は sentiment 名。
    for p in cp.get("sentiment", []):
        if p.get("evaluation") not in _SENTIMENT_SAVE:
            continue
        _mk("sentiment", p["label"], p["label"], p.get("evaluation", ""), p.get("reason", ""), p)

    # category 補正（v28: n>=5 & ±1 のみ。current/recommended は None、delta に ±1）。
    for p in cp.get("category", []):
        corr = p.get("correction")
        if corr not in (1, -1):
            continue
        suggestion = "+1 補正候補" if corr == 1 else "-1 補正候補"
        _mk("category", p["label"], p["label"], suggestion, p.get("reason", ""), p, delta=corr)

    return out


def save_score_corrections(records=None):
    """build_score_corrections の結果をまとめて保存（dedup）。返り値 (total, created, updated)。"""
    cands = build_score_corrections(records)
    created = updated = 0
    for c in cands:
        if _upsert(c):
            created += 1
        else:
            updated += 1
    return len(cands), created, updated


# ---------------- v31: シミュレーション結果を本番反映候補として保存（保存のみ・本番非反映） ----------------
def _simulation_cand_id():
    """simulation_result の固定id（1件に集約・最新で上書き）。"""
    return "simulation_result|news_score_corrections"


def _improvement(sim):
    """before/after から改善量を算出。None があれば None。"""
    b, a = sim.get("before", {}) or {}, sim.get("after", {}) or {}

    def _sub(x, y):
        return (round(x - y, 3) if (isinstance(x, (int, float)) and isinstance(y, (int, float))) else None)

    bh = (b.get("high") or {}).get("avg_vs_spy30")
    ah = (a.get("high") or {}).get("avg_vs_spy30")
    return {
        "corr_ret30_delta": _sub(a.get("corr_ret30"), b.get("corr_ret30")),
        "mono_violations_delta": _sub(a.get("mono_violations"), b.get("mono_violations")),
        "high_vs_spy30_delta": _sub(ah, bh),
    }


def save_simulation_result(sim):
    """v30 simulate_corrections() の結果を simulation_result 候補として保存（dedup・固定id）。
    保存条件を満たさなければ保存せず (False, 理由)。承認/却下 status は再保存でも保持。
    ⚠️ 承認しても本番ニューススコアには反映しない（status 変更のみ）。"""
    sim = sim or {}
    if sim.get("verdict") != "改善":
        return False, f"verdict が「改善」ではありません（{sim.get('verdict')}）"
    if (sim.get("approved_count") or 0) <= 0:
        return False, "承認済み補正案が0件です"
    if (sim.get("applicable_news") or 0) < 3:
        return False, "補正が変化を与えたニュースが3件未満です"
    if (sim.get("eligible_n") or 0) < 10:
        return False, "対象ニュースが10件未満です"

    cand = {
        "id": _simulation_cand_id(), "type": "simulation_result",
        "label": "news_score_corrections", "suggestion": sim.get("verdict", ""),
        "evidence": {}, "confidence": None,
        "source": "news_correction_simulation",
        "verdict": sim.get("verdict"),
        "approved_correction_count": sim.get("approved_count"),
        "applicable_news": sim.get("applicable_news"),
        "eligible_n": sim.get("eligible_n"),
        "before": sim.get("before"), "after": sim.get("after"),
        "improvement": _improvement(sim), "reasons": sim.get("reasons", []),
    }
    created = _upsert(cand)
    return True, ("保存しました（新規）" if created else "更新しました（既存candidate/承認状態は保持）")


# ---------------- v32: 本番反映前の最終ガード（表示のみ・本番非反映） ----------------
GUARD_MIN_ELIGIBLE = 30
GUARD_MIN_APPLICABLE = 10
GUARD_MIN_CORRECTIONS = 1


def production_guard(candidates=None):
    """approved な simulation_result を読み、本番反映してよい状態かを判定（表示のみ）。
    ⚠️ 本番ニューススコア・発掘スコア・ランキングには一切反映しない（判定の表示のみ）。
    candidates は引数注入可（None なら list_all()）。None値でも落ちない。
    判定優先順位: データ不足 → 悪化リスクあり → 反映準備OK → まだ反映しない。"""
    cands = candidates if candidates is not None else load()
    if not isinstance(cands, list):
        cands = []
    sim = next((c for c in cands
                if c.get("type") == "simulation_result" and c.get("status") == "approved"), None)
    has_approved_sim = sim is not None

    imp = (sim or {}).get("improvement", {}) or {}
    verdict = (sim or {}).get("verdict")
    eligible_n = (sim or {}).get("eligible_n")
    applicable = (sim or {}).get("applicable_news")
    corr_cnt = (sim or {}).get("approved_correction_count")
    d_corr = imp.get("corr_ret30_delta")
    d_mono = imp.get("mono_violations_delta")
    d_high = imp.get("high_vs_spy30_delta")

    def _num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    # 改善指標（None は不成立扱い）
    imp_corr = isinstance(d_corr, (int, float)) and d_corr >= 0.05
    imp_mono = isinstance(d_mono, (int, float)) and d_mono < 0
    imp_high = isinstance(d_high, (int, float)) and d_high >= 2
    has_improve = imp_corr or imp_mono or imp_high

    # 悪化指標（None は該当しない）
    wor_corr = isinstance(d_corr, (int, float)) and d_corr <= -0.05
    wor_mono = isinstance(d_mono, (int, float)) and d_mono > 0
    wor_high = isinstance(d_high, (int, float)) and d_high <= -2
    has_worsen = wor_corr or wor_mono or wor_high

    # データ不足条件
    insufficient = (not has_approved_sim or _num(eligible_n) < GUARD_MIN_ELIGIBLE
                    or _num(applicable) < GUARD_MIN_APPLICABLE or _num(corr_cnt) < GUARD_MIN_CORRECTIONS)

    # 反映準備OK条件
    ok_ready = (has_approved_sim and verdict == "改善"
                and _num(eligible_n) >= GUARD_MIN_ELIGIBLE
                and _num(applicable) >= GUARD_MIN_APPLICABLE
                and _num(corr_cnt) >= GUARD_MIN_CORRECTIONS
                and has_improve and not has_worsen)

    # 判定（優先順位: データ不足 → 悪化リスク → OK → まだ）
    if insufficient:
        decision = "データ不足"
    elif has_worsen:
        decision = "悪化リスクあり"
    elif ok_ready:
        decision = "反映準備OK"
    else:
        decision = "まだ反映しない"

    def _fmt(v):
        return v if v is not None else "—"

    checks = [
        {"label": "approved simulation_result", "ok": has_approved_sim, "value": ("あり" if has_approved_sim else "なし")},
        {"label": "verdict 改善", "ok": (verdict == "改善"), "value": _fmt(verdict)},
        {"label": f"eligible_n ≥ {GUARD_MIN_ELIGIBLE}", "ok": _num(eligible_n) >= GUARD_MIN_ELIGIBLE, "value": _fmt(eligible_n)},
        {"label": f"applicable_news ≥ {GUARD_MIN_APPLICABLE}", "ok": _num(applicable) >= GUARD_MIN_APPLICABLE, "value": _fmt(applicable)},
        {"label": f"approved_correction_count ≥ {GUARD_MIN_CORRECTIONS}", "ok": _num(corr_cnt) >= GUARD_MIN_CORRECTIONS, "value": _fmt(corr_cnt)},
        {"label": "corr_ret30_delta ≥ +0.05", "ok": imp_corr, "value": _fmt(d_corr)},
        {"label": "mono_violations_delta < 0", "ok": imp_mono, "value": _fmt(d_mono)},
        {"label": "high_vs_spy30_delta ≥ +2", "ok": imp_high, "value": _fmt(d_high)},
        {"label": "悪化指標なし", "ok": not has_worsen, "value": ("悪化あり" if has_worsen else "なし")},
    ]

    # 理由
    reasons = []
    if not has_approved_sim:
        reasons.append("承認済み simulation_result がありません")
    else:
        if _num(eligible_n) < GUARD_MIN_ELIGIBLE:
            reasons.append(f"eligible_n {eligible_n} < {GUARD_MIN_ELIGIBLE}")
        if _num(applicable) < GUARD_MIN_APPLICABLE:
            reasons.append(f"applicable_news {applicable} < {GUARD_MIN_APPLICABLE}")
        if _num(corr_cnt) < GUARD_MIN_CORRECTIONS:
            reasons.append(f"approved_correction_count {corr_cnt} < {GUARD_MIN_CORRECTIONS}")
        if verdict != "改善":
            reasons.append(f"verdict が「改善」でない（{verdict}）")
        if has_worsen:
            ws = []
            if wor_corr:
                ws.append(f"corr_ret30_delta {d_corr:+.3f} ≤ -0.05")
            if wor_mono:
                ws.append(f"mono_violations_delta {d_mono:+d} > 0")
            if wor_high:
                ws.append(f"high_vs_spy30_delta {d_high:+.1f} ≤ -2")
            reasons.append("悪化指標: " + " / ".join(ws))
        if not insufficient and not has_worsen and not has_improve:
            reasons.append("改善指標が1つも立っていない")
        if decision == "反映準備OK":
            reasons.append("十分なデータ・改善指標あり・悪化指標なし")

    # 次のアクション
    if decision == "データ不足":
        next_actions = ["ニュース較正データを増やす", "追加ニュースを保存する",
                        "ret_30 の更新を待つ（30営業日経過が必要）", "反映はまだしない"]
    elif decision == "悪化リスクあり":
        next_actions = ["補正案を見直す（承認を取り消す/調整）", "シミュレーションをやり直す", "反映はまだしない"]
    elif decision == "まだ反映しない":
        next_actions = ["明確な改善指標が出るまで継続", "データを増やして再シミュレーション", "反映はまだしない"]
    else:  # 反映準備OK
        next_actions = ["人手で最終確認する（自動反映はしない）",
                        "本番反映は別途・慎重に判断（このツールでは反映しません）"]

    return {"decision": decision, "has_approved_sim": has_approved_sim, "checks": checks,
            "improvement": (imp if has_approved_sim else None),
            "reasons": reasons, "next_actions": next_actions}
