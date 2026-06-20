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
import copy
import datetime as dt
import hashlib
import json
import os

from modules import storage, news_calibration as ncal

PATH = "user_data/news_adoption_candidates.json"
OVERRIDES_PATH = "user_data/news_score_overrides.json"  # v33: 設定ファイル（生成のみ・本番未読込）
MIN_SAMPLE = 3  # n>=3 のみ候補化（low_sample は保存しない）
NEWS_OVERRIDES_EXPERIMENT = False  # v37: 実験モードフラグ（デフォルト false=絶対未適用）
PRODUCTION_NEWS_OVERRIDES_ENABLED = False  # v42: 本番反映グローバルスイッチ（OFF固定・コード鍵）
PRODUCTION_FLAGS_PATH = "user_data/news_production_flags.json"  # v42: 運用確認フラグ（自動生成しない）
PRODUCTION_OVERRIDES_FRESH_HOURS = 72  # v42: overrides.generated_at の鮮度しきい値


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
#   production_apply_candidate(v41): experiment_applied/target_count/score_changed/rank_changed/
#                           in_count/out_count/guard_decision/overrides_enabled/summary/rows/note
_OPTIONAL_FIELDS = ("target", "recommended_score", "delta", "reason",
                    "source", "verdict", "approved_correction_count", "applicable_news",
                    "eligible_n", "before", "after", "improvement", "reasons",
                    "experiment_applied", "target_count", "score_changed", "rank_changed",
                    "in_count", "out_count", "guard_decision", "overrides_enabled",
                    "summary", "rows", "note",
                    # production_apply_candidate(v43): fingerprint 整合性
                    "overrides_fingerprint", "overrides_fingerprint_source")


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


# ---------------- v33: 本番反映用の設定ファイル生成（生成のみ・news.py 未読込・本番非反映） ----------------
def _approved_score_corrections(cands):
    return [c for c in cands if c.get("type") == "score_correction" and c.get("status") == "approved"]


def _approved_simulation(cands):
    return next((c for c in cands
                 if c.get("type") == "simulation_result" and c.get("status") == "approved"), None)


def build_overrides_config(candidates=None):
    """approved score_correction / simulation_result から overrides 設定 dict を組み立てる（保存しない）。
    ⚠️ enabled は必ず False（生成しても本番反映しないための安全ガード）。"""
    cands = candidates if candidates is not None else load()
    if not isinstance(cands, list):
        cands = []
    approved = _approved_score_corrections(cands)
    sim = _approved_simulation(cands)
    guard = production_guard(cands)

    impact_overrides, category_adjustments, sentiment_notes = {}, {}, {}
    for c in approved:
        tgt = c.get("target")
        if tgt == "impact":
            cur, rec = c.get("current_score"), c.get("recommended_score")
            if isinstance(cur, int) and isinstance(rec, int):
                impact_overrides[str(cur)] = rec
        elif tgt == "category":
            d = c.get("delta")
            lab = c.get("label")
            if isinstance(d, int) and lab:
                category_adjustments[lab] = category_adjustments.get(lab, 0) + d  # 防御的に合算
        elif tgt == "sentiment":
            lab = c.get("label")
            if lab:
                sentiment_notes[lab] = c.get("suggestion", "")

    imp = (sim or {}).get("improvement", {}) or {}
    simulation_summary = {
        "eligible_n": (sim or {}).get("eligible_n"),
        "applicable_news": (sim or {}).get("applicable_news"),
        "approved_correction_count": (sim or {}).get("approved_correction_count"),
        "corr_ret30_delta": imp.get("corr_ret30_delta"),
        "mono_violations_delta": imp.get("mono_violations_delta"),
        "high_vs_spy30_delta": imp.get("high_vs_spy30_delta"),
    }

    return {
        "version": 1,
        "generated_at": _now(),
        "enabled": False,  # 必ず False（本番反映しない）
        "source": "approved_news_score_corrections",
        "guard_decision": guard["decision"],
        "impact_overrides": impact_overrides,
        "category_adjustments": category_adjustments,
        "sentiment_notes": sentiment_notes,
        "simulation_summary": simulation_summary,
        "note": "Generated only. Not applied to production scoring.",
    }


def can_generate_overrides(candidates=None):
    """生成条件を満たすか判定。返り値 (ok:bool, reason:str)。"""
    cands = candidates if candidates is not None else load()
    if not isinstance(cands, list):
        cands = []
    if production_guard(cands)["decision"] != "反映準備OK":
        return False, "本番反映前チェックが「反映準備OK」ではありません"
    if not _approved_score_corrections(cands):
        return False, "承認済み score_correction がありません"
    sim = _approved_simulation(cands)
    if sim is None:
        return False, "承認済み simulation_result がありません"
    if sim.get("verdict") != "改善":
        return False, f"simulation_result の verdict が「改善」ではありません（{sim.get('verdict')}）"
    return True, "生成条件を満たしています"


def generate_overrides_config(candidates=None):
    """生成条件を満たす場合のみ news_score_overrides.json を生成（上書き）。
    満たさなければ保存せず (False, 理由, None)。満たせば (True, msg, config)。
    ⚠️ news.py には読み込ませない・本番ニューススコアには反映しない（enabled=False）。"""
    cands = candidates if candidates is not None else load()
    if not isinstance(cands, list):
        cands = []
    ok, reason = can_generate_overrides(cands)
    if not ok:
        return False, reason, None
    cfg = build_overrides_config(cands)
    storage.save_json(OVERRIDES_PATH, cfg)
    return True, f"{OVERRIDES_PATH} を生成しました（enabled=false・本番未反映）", cfg


def load_overrides_config():
    """生成済み overrides を読む（表示用）。無ければ None。"""
    cfg = storage.load_json(OVERRIDES_PATH, None)
    return cfg if isinstance(cfg, dict) else None


# ---------------- v43: overrides の内容 fingerprint（整合性チェック用） ----------------
def overrides_fingerprint(cfg):
    """overrides の「設定内容」のみを正規化して sha256 fingerprint を返す。
    対象は impact_overrides / category_adjustments / sentiment_notes の3キーのみ
    （generated_at / simulation_summary / enabled / source / guard_decision は含めない）。
    cfg が dict でなければ None。キー順序差は sort_keys で吸収される。"""
    if not isinstance(cfg, dict):
        return None
    payload = {
        "impact_overrides": cfg.get("impact_overrides") or {},
        "category_adjustments": cfg.get("category_adjustments") or {},
        "sentiment_notes": cfg.get("sentiment_notes") or {},
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------- v35: overrides の状態を返す（読むだけ・適用しない） ----------------
def load_overrides_status():
    """news_score_overrides.json の状態を返す（読み取りのみ・適用しない・非改変）。
    ⚠️ 本番ニューススコア・発掘スコア・ランキングには一切反映しない。
    status: missing(ファイル無/読込不可) / disabled(enabled=false) / enabled(enabled=true)。
    壊れたJSONでも落ちない。"""
    if not os.path.exists(OVERRIDES_PATH):
        return {"exists": False, "enabled": False, "status": "missing", "config": None}
    cfg = load_overrides_config()  # 壊れJSONは None
    if not isinstance(cfg, dict):
        # ファイルはあるが読めない → 安全側（未適用）に倒す
        return {"exists": True, "enabled": False, "status": "missing", "config": None}
    enabled = bool(cfg.get("enabled"))
    return {"exists": True, "enabled": enabled,
            "status": ("enabled" if enabled else "disabled"), "config": cfg}


# ---------------- v34: overrides 設定ファイルの安全プレビュー（読み取り表示のみ・本番非反映） ----------------
def preview_overrides_config():
    """news_score_overrides.json を読み、プレビュー用に正規化（読み取りのみ・非改変）。
    ⚠️ ファイルは書き換えない（enabled=true でも false に直さない）。
    ⚠️ news.py には読み込ませない・本番ニューススコアには反映しない。
    status: 未生成（ファイル無）/ 読み込み不可（dictで読めない）/ 正常。"""
    exists = os.path.exists(OVERRIDES_PATH)
    cfg = load_overrides_config() if exists else None

    if not exists:
        return {"status": "未生成", "exists": False, "enabled": None, "safe": False,
                "safety_label": "未生成", "generated_at": None, "source": None,
                "guard_decision": None, "impact_rows": [], "category_rows": [],
                "sentiment_rows": [], "counts": {"impact": 0, "category": 0, "sentiment": 0},
                "simulation_summary": None}
    if not isinstance(cfg, dict):
        return {"status": "読み込み不可", "exists": True, "enabled": None, "safe": False,
                "safety_label": "読み込み不可", "generated_at": None, "source": None,
                "guard_decision": None, "impact_rows": [], "category_rows": [],
                "sentiment_rows": [], "counts": {"impact": 0, "category": 0, "sentiment": 0},
                "simulation_summary": None}

    enabled = bool(cfg.get("enabled"))
    safe = not enabled
    safety_label = "未適用・安全" if not enabled else "注意：enabled=true"

    # impact_overrides: {"5": 3} -> {from:5, to:3, delta:-2}。key が int化できなくても落ちない。
    impact_rows = []
    for k, v in (cfg.get("impact_overrides") or {}).items():
        try:
            frm = int(k)
        except (TypeError, ValueError):
            frm = k  # 変換不可はそのまま表示（落とさない）
        to = v
        delta = (to - frm if isinstance(frm, int) and isinstance(to, int) else None)
        impact_rows.append({"from": frm, "to": to, "delta": delta})

    category_rows = [{"category": k, "adjustment": v}
                     for k, v in (cfg.get("category_adjustments") or {}).items()]
    sentiment_rows = [{"sentiment": k, "note": v}
                      for k, v in (cfg.get("sentiment_notes") or {}).items()]

    return {
        "status": "正常", "exists": True, "enabled": enabled, "safe": safe,
        "safety_label": safety_label,
        "generated_at": cfg.get("generated_at"), "source": cfg.get("source"),
        "guard_decision": cfg.get("guard_decision"),
        "impact_rows": impact_rows, "category_rows": category_rows, "sentiment_rows": sentiment_rows,
        "counts": {"impact": len(impact_rows), "category": len(category_rows), "sentiment": len(sentiment_rows)},
        "simulation_summary": cfg.get("simulation_summary"),
    }


# ---------------- v36: overrides を仮適用した差分プレビュー（表示計算のみ・JSON非改変・本番非反映） ----------------
def _clamp5(v):
    return max(-5, min(5, int(round(v))))


def _apply_overrides_to_score(impact_score, categories, impact_map, category_deltas):
    """1ニュースへ overrides を仮適用（本番非反映）。
    返り値 (after, impact_applied, category_applied)。
    impact置換 → category加算 → clamp(-5..+5)。news_calibration の private には依存しない。"""
    s = impact_score
    impact_applied = str(impact_score) in impact_map
    if impact_applied:
        s = impact_map[str(impact_score)]
    category_applied = False
    for c in (categories or []):
        if c in category_deltas:
            s += category_deltas[c]
            category_applied = True
    return _clamp5(s), impact_applied, category_applied


def _avg1(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def preview_apply_overrides(records=None, cfg=None):
    """較正ニュースへ overrides を仮適用した差分プレビュー（表示計算のみ・JSON非改変）。
    ⚠️ 本番ニューススコア・発掘スコア・ランキングには一切反映しない（enabled に依らず計算のみ）。
    records: None なら news_calibration.load()。cfg: None なら load_overrides_config()。"""
    cfg = cfg if cfg is not None else load_overrides_config()
    if not isinstance(cfg, dict):
        return {"available": False, "enabled": None,
                "reason": "overrides 設定ファイルがありません（先に生成してください）",
                "total": 0, "changed": 0, "avg_before": None, "avg_after": None, "avg_delta": None,
                "rows": [], "by_ticker": []}

    enabled = bool(cfg.get("enabled"))
    # overrides を仮適用用に正規化（impact キーは str のまま比較、category は label->delta）
    impact_map = {}
    for k, v in (cfg.get("impact_overrides") or {}).items():
        if isinstance(v, int):
            impact_map[str(k)] = v
    category_deltas = {}
    for k, v in (cfg.get("category_adjustments") or {}).items():
        if isinstance(v, int) and k:
            category_deltas[k] = v
    sentiment_notes = cfg.get("sentiment_notes") or {}

    recs = records if records is not None else ncal.load()
    if not isinstance(recs, list):
        recs = []
    # impact_score を持つニュースのみ対象
    targets = [r for r in recs if isinstance(r.get("impact_score"), int)]
    if not targets:
        return {"available": True, "enabled": enabled,
                "reason": "impact_score を持つ較正ニュースがありません",
                "total": 0, "changed": 0, "avg_before": None, "avg_after": None, "avg_delta": None,
                "rows": [], "by_ticker": []}

    rows = []
    for r in targets:
        before = r["impact_score"]
        cats = r.get("categories") or []
        after, imp_app, cat_app = _apply_overrides_to_score(before, cats, impact_map, category_deltas)
        sent = r.get("sentiment")
        note = sentiment_notes.get(sent) if sent in sentiment_notes else None
        rows.append({
            "ticker": r.get("ticker"), "news_date": r.get("news_date"),
            "headline": r.get("headline", ""), "sentiment": sent, "categories": cats,
            "impact_before": before, "impact_after": after, "delta": after - before,
            "impact_override_applied": imp_app, "category_adjustment_applied": cat_app,
            "sentiment_note": note})

    changed = sum(1 for r in rows if r["delta"] != 0)
    avg_before = _avg1([r["impact_before"] for r in rows])
    avg_after = _avg1([r["impact_after"] for r in rows])
    avg_delta = _avg1([r["delta"] for r in rows])

    # 銘柄別 summary
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    by_ticker = []
    for t, lst in by.items():
        by_ticker.append({
            "ticker": t, "news_count": len(lst),
            "changed_count": sum(1 for x in lst if x["delta"] != 0),
            "avg_before": _avg1([x["impact_before"] for x in lst]),
            "avg_after": _avg1([x["impact_after"] for x in lst]),
            "avg_delta": _avg1([x["delta"] for x in lst])})
    by_ticker.sort(key=lambda x: -x["changed_count"])

    return {"available": True, "enabled": enabled, "reason": "",
            "total": len(rows), "changed": changed,
            "avg_before": avg_before, "avg_after": avg_after, "avg_delta": avg_delta,
            "rows": rows, "by_ticker": by_ticker}


# ---------------- v37: 実験モードでのみ overrides を仮適用（表示のみ・本番非接続・JSON非改変） ----------------
def experiment_overrides_preview(records=None, cfg=None, experiment_enabled=None):
    """実験モード時のみ overrides を較正ニュースへ仮適用し original/experiment を比較（表示のみ）。
    ⚠️ 通常のニューススコア・発掘スコア・ランキングには一切反映しない（本番非接続）。
    実験適用は cfg が dict ＋ cfg.enabled is True ＋ experiment_enabled is True の3条件 AND のみ。
    それ以外は未適用（experiment_score=original, delta=0）。較正JSON・overrides JSON は非改変。"""
    if experiment_enabled is None:
        experiment_enabled = NEWS_OVERRIDES_EXPERIMENT
    experiment_flag = bool(experiment_enabled)

    cfg = cfg if cfg is not None else load_overrides_config()
    exists = isinstance(cfg, dict)
    enabled = bool(cfg.get("enabled")) if exists else None

    # 3段ゲート（すべて満たした時だけ実験適用）
    if not exists:
        applied, reason = False, "overridesなし"
    elif enabled is not True:
        applied, reason = False, "enabled=false"
    elif experiment_flag is not True:
        applied, reason = False, "experiment flag=false"
    else:
        applied, reason = True, "実験適用"

    # 仮適用マップ（実験適用時のみ使用）
    impact_map, category_deltas = {}, {}
    if applied:
        for k, v in (cfg.get("impact_overrides") or {}).items():
            if isinstance(v, int):
                impact_map[str(k)] = v
        for k, v in (cfg.get("category_adjustments") or {}).items():
            if isinstance(v, int) and k:
                category_deltas[k] = v

    recs = records if records is not None else ncal.load()
    if not isinstance(recs, list):
        recs = []
    targets = [r for r in recs if isinstance(r.get("impact_score"), int)]

    rows = []
    for r in targets:
        orig = r["impact_score"]
        if applied:
            exp, _imp_app, _cat_app = _apply_overrides_to_score(orig, r.get("categories") or [],
                                                                impact_map, category_deltas)
            row_reason = "実験適用"
        else:
            exp, row_reason = orig, reason
        rows.append({
            "ticker": r.get("ticker"), "news_date": r.get("news_date"),
            "headline": r.get("headline", ""),
            "original_score": orig, "experiment_score": exp, "delta": exp - orig,
            "applied_reason": row_reason})

    changed = sum(1 for r in rows if r["delta"] != 0)
    avg_original = _avg1([r["original_score"] for r in rows])
    avg_experiment = _avg1([r["experiment_score"] for r in rows])
    avg_delta = _avg1([r["delta"] for r in rows])

    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    by_ticker = []
    for t, lst in by.items():
        by_ticker.append({
            "ticker": t,
            "original_avg": _avg1([x["original_score"] for x in lst]),
            "experiment_avg": _avg1([x["experiment_score"] for x in lst]),
            "delta": _avg1([x["delta"] for x in lst])})
    by_ticker.sort(key=lambda x: -abs(x["delta"] if x["delta"] is not None else 0))

    return {
        "exists": exists, "enabled": enabled, "experiment_flag": experiment_flag,
        "experiment_applied": applied, "reason": reason,
        "total": len(rows), "changed": changed,
        "avg_original": avg_original, "avg_experiment": avg_experiment, "avg_delta": avg_delta,
        "rows": rows, "by_ticker": by_ticker}


# ---------------- v38: 個別銘柄ライブニュースへ実験モードで仮適用（表示のみ・本番非接続） ----------------
def experiment_news_items(items, cfg=None, experiment_enabled=None):
    """個別銘柄ページのライブニュース items に overrides を実験適用した比較（表示のみ）。
    ⚠️ 通常の news_sum / avg_impact / verdict / score・発掘スコア・ランキングには一切反映しない。
    ライブ item のフィールド（impact / category / lean / headline / source）を使う
    （較正データの impact_score / categories / sentiment とは名前が異なる）。
    実験適用は cfg dict ＋ cfg.enabled is True ＋ experiment_enabled is True の3条件 AND のみ。
    sentiment はスコア非変更（note 表示のみ）。較正JSON・overrides JSON は非改変。"""
    if experiment_enabled is None:
        experiment_enabled = NEWS_OVERRIDES_EXPERIMENT
    experiment_flag = bool(experiment_enabled)

    cfg = cfg if cfg is not None else load_overrides_config()
    exists = isinstance(cfg, dict)
    enabled = bool(cfg.get("enabled")) if exists else None

    # 3段ゲート（v37 と同一）
    if not exists:
        applied, reason = False, "overridesなし"
    elif enabled is not True:
        applied, reason = False, "enabled=false"
    elif experiment_flag is not True:
        applied, reason = False, "experiment flag=false"
    else:
        applied, reason = True, "実験適用"

    impact_map, category_deltas, sentiment_notes = {}, {}, {}
    if applied:
        for k, v in (cfg.get("impact_overrides") or {}).items():
            if isinstance(v, int):
                impact_map[str(k)] = v
        for k, v in (cfg.get("category_adjustments") or {}).items():
            if isinstance(v, int) and k:
                category_deltas[k] = v
        sentiment_notes = cfg.get("sentiment_notes") or {}

    items = items if isinstance(items, list) else []
    # impact が int のニュースのみ対象
    targets = [it for it in items if isinstance(it.get("impact"), int)]

    def _sent_counts():
        c = {"bull": 0, "bear": 0, "neutral": 0}
        for it in targets:
            lean = it.get("lean")
            if lean in c:
                c[lean] += 1
        return c

    rows = []
    for it in targets:
        orig = it["impact"]
        # category(単一str) -> [category] にマッピング、lean -> sentiment
        cats = [it["category"]] if it.get("category") else []
        sent = it.get("lean")
        if applied:
            exp, imp_app, cat_app = _apply_overrides_to_score(orig, cats, impact_map, category_deltas)
            note = sentiment_notes.get(sent) if sent in sentiment_notes else None
        else:
            exp, imp_app, cat_app, note = orig, False, False, None
        rows.append({
            "headline": it.get("headline", ""), "source": it.get("source", ""),
            "original_impact": orig, "experiment_impact": exp, "delta": exp - orig,
            "category_adjustment_applied": cat_app, "impact_override_applied": imp_app,
            "sentiment_note": note})

    changed = sum(1 for r in rows if r["delta"] != 0)
    avg_original = _avg1([r["original_impact"] for r in rows])
    avg_experiment = _avg1([r["experiment_impact"] for r in rows])
    avg_delta = _avg1([r["delta"] for r in rows])
    # sentiment はスコア非変更 → experiment は original と同じ
    original_sentiment = _sent_counts()
    experiment_sentiment = dict(original_sentiment)

    return {
        "exists": exists, "enabled": enabled, "experiment_flag": experiment_flag,
        "experiment_applied": applied, "reason": reason,
        "total": len(rows), "changed": changed,
        "avg_original": avg_original, "avg_experiment": avg_experiment, "avg_delta": avg_delta,
        "original_sentiment": original_sentiment, "experiment_sentiment": experiment_sentiment,
        "rows": rows}


# ---------------- v39: 実験ニュース影響を個別銘柄スコアに仮反映して比較（表示のみ・本番非接続） ----------------
def experiment_individual_score(scores, news_sum, snap, fund, plan, exp_avg_impact, market_mode="中立"):
    """experiment avg_impact を news component に仮反映した total/verdict/action を比較（表示のみ）。
    ⚠️ 本番ニューススコア・発掘スコア・ランキングには一切反映しない。
    news component だけ差し替え、他コンポーネント（fund/tech/market/supply）は scores の値を流用
    （＝「ニュース以外のスコアは同一」を構造保証）。scores/plan/action は原本非変更（コピーで計算）。
    scoring._news_raw / action.decide_action / config 閾値を read-only 利用（編集しない）。"""
    import config
    from modules import scoring, action as action_mod

    scores = scores or {}
    news_sum = news_sum or {}
    W = config.SCORE_WEIGHTS

    original_total = scores.get("total", 0)
    original_news_component = scores.get("news", 0)
    original_verdict = scores.get("verdict")

    # experiment news component（categories/reasons は元のまま、avg_impact だけ差し替え）
    exp_news_sum = {"avg_impact": exp_avg_impact,
                    "categories": news_sum.get("categories", {}) or {},
                    "reasons": news_sum.get("reasons", []) or []}
    exp_news_raw = scoring._news_raw(exp_news_sum)[0]
    experiment_news_component = round(exp_news_raw * W["news"], 1)

    experiment_total = round(original_total - original_news_component + experiment_news_component, 1)

    def _verdict(total):
        if total >= config.BUY_THRESHOLD:
            return "BUY"
        if total >= config.WATCH_THRESHOLD:
            return "WATCH"
        return "AVOID"

    experiment_verdict = _verdict(experiment_total)

    # action は original/experiment とも同一 market_mode で再計算（差は news のみ）。原本コピー。
    orig_scores_copy = dict(scores)
    exp_scores_copy = dict(scores)
    exp_scores_copy["news"] = experiment_news_component
    exp_scores_copy["total"] = experiment_total
    exp_scores_copy["verdict"] = experiment_verdict

    try:
        original_action = action_mod.decide_action(orig_scores_copy, snap, fund, plan, news_sum, market_mode)["action"]
    except Exception:
        original_action = None
    try:
        experiment_action = action_mod.decide_action(exp_scores_copy, snap, fund, plan, exp_news_sum, market_mode)["action"]
    except Exception:
        experiment_action = None

    return {
        "original_total": original_total, "experiment_total": experiment_total,
        "score_delta": round(experiment_total - original_total, 1),
        "original_verdict": original_verdict, "experiment_verdict": experiment_verdict,
        "verdict_change": (original_verdict != experiment_verdict),
        "original_action": original_action, "experiment_action": experiment_action,
        "action_change": (original_action != experiment_action),
        "news_before": original_news_component, "news_after": experiment_news_component,
        "news_delta": round(experiment_news_component - original_news_component, 1),
        "note": "ニュース以外のスコアは同一。実験actionは market_mode=中立 前提。",
    }


# ---------------- v40: 発掘ランキングへの影響プレビュー（近似・表示のみ・本番非接続） ----------------
def _discovery_verdict(score, confidence):
    """discovery の verdict 閾値を再現（強いBUY≥80/BUY≥70/WATCH≥55/else AVOID、confidence低でdowngrade）。"""
    import config
    v = ("強いBUY" if score >= config.DISCOVERY_BUY_STRONG
         else "BUY" if score >= config.DISCOVERY_BUY
         else "WATCH" if score >= config.DISCOVERY_WATCH else "AVOID")
    if confidence == "低" and v in ("強いBUY", "BUY"):
        v = "WATCH"
    return v


def preview_discovery_ranking_experiment(items, cfg=None, experiment_enabled=False, top_n=10):
    """発掘 top20 items の news component を実験ニュースで近似補正し、順位への影響を比較（表示のみ）。
    ⚠️ 本番発掘スコア・ランキングには一切反映しない（items はコピーで計算・原本非変更）。
    近似：item に個別ニュース/categories が無いため、impact_overrides を代表値 round(news_impact)
    に近似適用（category補正・action再計算は未反映）。scoring._news_raw は read-only 利用。"""
    from modules import scoring

    cfg = cfg if cfg is not None else load_overrides_config()
    exists = isinstance(cfg, dict)
    enabled = bool(cfg.get("enabled")) if exists else None
    experiment_flag = bool(experiment_enabled)

    # 3段ゲート
    if not exists:
        applied, reason = False, "overridesなし"
    elif enabled is not True:
        applied, reason = False, "enabled=false"
    elif experiment_flag is not True:
        applied, reason = False, "experiment flag=false"
    else:
        applied, reason = True, "実験適用"

    impact_map = {}
    if applied:
        for k, v in (cfg.get("impact_overrides") or {}).items():
            if isinstance(v, int):
                impact_map[str(k)] = v

    items = items if isinstance(items, list) else []
    n = len(items)

    def _clamp5f(v):
        return max(-5.0, min(5.0, v))

    # 1) original_rank（元の並び順）＋ experiment_score を算出（コピー計算・原本非変更）
    work = []
    for i, it in enumerate(items, 1):
        orig_score = it.get("score", 0) or 0
        orig_news = (it.get("breakdown") or {}).get("news", 0) or 0
        avg = it.get("news_impact", 0) or 0
        confidence = it.get("confidence")
        orig_verdict = it.get("verdict")

        exp_score = orig_score
        if applied:
            ri = int(round(avg))
            exp_avg = avg + (impact_map[str(ri)] - ri) if str(ri) in impact_map else avg
            exp_avg = _clamp5f(exp_avg)
            try:
                raw_orig = scoring._news_raw({"avg_impact": avg, "categories": {}})[0]
                raw_exp = scoring._news_raw({"avg_impact": exp_avg, "categories": {}})[0]
            except Exception:
                raw_orig, raw_exp = None, None
            if raw_orig and raw_orig > 0 and raw_exp is not None:
                weight = orig_news / raw_orig          # discovery news 重みを逆算
                exp_news_comp = round(raw_exp * weight, 1)
                exp_score = round(orig_score - orig_news + exp_news_comp, 1)
        work.append({
            "ticker": it.get("ticker"), "name": it.get("name", it.get("ticker")),
            "original_rank": i, "original_score": orig_score, "experiment_score": exp_score,
            "original_verdict": orig_verdict,
            "experiment_verdict": _discovery_verdict(exp_score, confidence),
        })

    # 2) experiment_rank（experiment_score 降順。同点は original_rank で安定）
    order = sorted(work, key=lambda x: (-x["experiment_score"], x["original_rank"]))
    for rank, w in enumerate(order, 1):
        w["experiment_rank"] = rank

    # 3) status / 集計
    rows, score_changed, rank_changed, in_count, out_count = [], 0, 0, 0, 0
    for w in work:
        rank_delta = w["original_rank"] - w["experiment_rank"]
        score_delta = round(w["experiment_score"] - w["original_score"], 1)
        if w["original_rank"] > top_n and w["experiment_rank"] <= top_n:
            status = "新規IN"
            in_count += 1
        elif w["original_rank"] <= top_n and w["experiment_rank"] > top_n:
            status = "OUT"
            out_count += 1
        elif rank_delta > 0:
            status = "上昇"
        elif rank_delta < 0:
            status = "下落"
        else:
            status = "変化なし"
        if score_delta != 0:
            score_changed += 1
        if rank_delta != 0:
            rank_changed += 1
        rows.append({
            "ticker": w["ticker"], "name": w["name"],
            "original_rank": w["original_rank"], "experiment_rank": w["experiment_rank"],
            "rank_delta": rank_delta,
            "original_score": w["original_score"], "experiment_score": w["experiment_score"],
            "score_delta": score_delta,
            "original_verdict": w["original_verdict"], "experiment_verdict": w["experiment_verdict"],
            "status": status})
    rows.sort(key=lambda x: x["experiment_rank"])

    return {
        "available": exists, "enabled": enabled, "experiment_flag": experiment_flag,
        "experiment_applied": applied, "reason": reason,
        "total": n, "score_changed": score_changed, "rank_changed": rank_changed,
        "in_count": in_count, "out_count": out_count, "rows": rows}


# ---------------- v41: 実験ランキング結果を本番反映候補として保存（保存のみ・本番非反映） ----------------
def _production_apply_cand_id():
    """production_apply_candidate の固定id（1件集約・最新で上書き）。"""
    return "production_apply_candidate|news_overrides"


def _production_apply_summary(rows):
    """v40 rows から summary を算出（rows 空でも落ちない）。"""
    rows = rows or []
    if not rows:
        return {"top10_changed": 0, "avg_score_delta": 0.0, "max_rank_up": 0, "max_rank_down": 0}
    top10_changed = sum(1 for r in rows
                        if ((r.get("original_rank") or 999) <= 10 or (r.get("experiment_rank") or 999) <= 10)
                        and r.get("status") != "変化なし")
    deltas = [r.get("score_delta", 0) or 0 for r in rows]
    avg_score_delta = round(sum(deltas) / len(deltas), 1) if deltas else 0.0
    rank_deltas = [r.get("rank_delta", 0) or 0 for r in rows]
    return {"top10_changed": top10_changed, "avg_score_delta": avg_score_delta,
            "max_rank_up": max(rank_deltas), "max_rank_down": min(rank_deltas)}


def production_apply_savable(exp_result, candidates=None):
    """保存条件の判定（views のボタン表示と save 内で共用）。返り値 (ok, reason)。"""
    e = exp_result or {}
    if not e.get("experiment_applied"):
        return False, "実験が未適用です（experiment_applied=false）"
    if e.get("enabled") is not True:
        return False, "overrides enabled=false です"
    cands = candidates if candidates is not None else load()
    if production_guard(cands)["decision"] != "反映準備OK":
        return False, "本番反映前チェックが「反映準備OK」ではありません"
    if (e.get("score_changed") or 0) < 1:
        return False, "score 変化が0件です"
    if (e.get("rank_changed") or 0) < 1:
        return False, "順位変化が0件です"
    if (e.get("out_count") or 0) > (e.get("in_count") or 0) + 2:
        return False, f"OUTが多すぎます（out {e.get('out_count')} > in {e.get('in_count')}+2）"
    return True, "保存条件を満たしています"


def save_production_apply_candidate(exp_result):
    """v40 preview_discovery_ranking_experiment() 結果を production_apply_candidate として保存（固定id）。
    保存条件を満たさなければ保存せず (False, 理由)。承認/却下 status は再保存でも保持。
    ⚠️ 承認しても本番ニューススコア・発掘スコア・ランキングには反映しない（status 変更のみ）。"""
    e = exp_result or {}
    ok, reason = production_apply_savable(e)
    if not ok:
        return False, reason

    rows = [{
        "ticker": r.get("ticker"), "original_rank": r.get("original_rank"),
        "experiment_rank": r.get("experiment_rank"), "rank_delta": r.get("rank_delta"),
        "original_score": r.get("original_score"), "experiment_score": r.get("experiment_score"),
        "score_delta": r.get("score_delta"), "status": r.get("status"),
    } for r in (e.get("rows") or [])]

    cand = {
        "id": _production_apply_cand_id(), "type": "production_apply_candidate",
        "label": "news_overrides", "suggestion": "production_apply_candidate",
        "evidence": {}, "confidence": None,
        "source": "discovery_ranking_experiment",
        "experiment_applied": bool(e.get("experiment_applied")),
        "target_count": e.get("total"),
        "score_changed": e.get("score_changed"), "rank_changed": e.get("rank_changed"),
        "in_count": e.get("in_count"), "out_count": e.get("out_count"),
        "guard_decision": production_guard(load())["decision"],
        "overrides_enabled": bool(e.get("enabled")),
        "summary": _production_apply_summary(rows),
        "rows": rows,
        "note": "Saved only. Not applied to production ranking.",
        # v43: 承認時点の overrides 設定内容の fingerprint（整合性チェック用）
        "overrides_fingerprint": overrides_fingerprint(load_overrides_config()),
        "overrides_fingerprint_source": "news_score_overrides.json",
    }
    created = _upsert(cand)
    return True, ("保存しました（新規）" if created else "更新しました（既存candidate/承認状態は保持）")


# ---------------- v42: 本番反映スイッチの状態評価（判定のみ・apply関数は無し・本番非接続） ----------------
def _production_apply_confirmed():
    """news_production_flags.json の production_apply_confirmed を読む。
    ファイル無し / 壊れJSON / キー無し は False（default-deny）。自動生成しない。"""
    flags = storage.load_json(PRODUCTION_FLAGS_PATH, None)
    if not isinstance(flags, dict):
        return False
    return flags.get("production_apply_confirmed") is True


def _overrides_freshness_ok(cfg):
    """overrides.generated_at が PRODUCTION_OVERRIDES_FRESH_HOURS 以内なら True。
    無し / parse不可 / 古い は False。"""
    if not isinstance(cfg, dict):
        return False
    ga = cfg.get("generated_at")
    if not ga:
        return False
    try:
        ts = dt.datetime.fromisoformat(ga)
    except (ValueError, TypeError):
        return False
    age_h = (dt.datetime.now() - ts).total_seconds() / 3600.0
    return 0 <= age_h <= PRODUCTION_OVERRIDES_FRESH_HOURS


def get_production_overrides_status():
    """本番反映の全ゲートを評価して allow/gates/reasons/summary を返す（判定のみ・副作用なし）。
    ⚠️ apply パスは存在しない（v43時点）。本番ニューススコア・発掘スコア・ランキングには一切反映しない。
    consistency(fingerprint) は v43 で実判定化（current overrides ↔ approved candidate の一致）。
    ただし PRODUCTION_NEWS_OVERRIDES_ENABLED が False（OFF固定）の限り allow は必ず False。"""
    cands = load()
    cfg = load_overrides_config()

    g_switch = (PRODUCTION_NEWS_OVERRIDES_ENABLED is True)
    g_confirm = _production_apply_confirmed()
    g_exists = isinstance(cfg, dict)
    g_enabled = bool(cfg.get("enabled")) if g_exists else False
    guard_decision = production_guard(cands)["decision"]
    g_guard = (guard_decision == "反映準備OK")
    g_approved = any(c.get("type") == "production_apply_candidate" and c.get("status") == "approved"
                     for c in cands)
    g_fresh = _overrides_freshness_ok(cfg) if g_exists else False

    # v43: fingerprint 整合性判定（current overrides ↔ approved candidate）
    approved_cand = next((c for c in cands if c.get("type") == "production_apply_candidate"
                          and c.get("status") == "approved"), None)
    current_fp = overrides_fingerprint(cfg)
    candidate_fp = approved_cand.get("overrides_fingerprint") if approved_cand else None
    consistency_reason = None
    if not approved_cand:
        g_consistency = False
        consistency_reason = "approved production_apply_candidate missing"
    elif not candidate_fp:
        g_consistency = False
        consistency_reason = "fingerprint未保存。v43以降に候補を再保存してください"
    elif current_fp is None:
        g_consistency = False
        consistency_reason = "overrides file missing"
    elif current_fp == candidate_fp:
        g_consistency = True
    else:
        g_consistency = False
        consistency_reason = "overrides の内容が承認時と不一致です"
    fingerprint_match = bool(current_fp and candidate_fp and current_fp == candidate_fp)

    gates = [
        {"label": "Global production switch", "ok": g_switch, "value": PRODUCTION_NEWS_OVERRIDES_ENABLED},
        {"label": "Operational confirmation", "ok": g_confirm, "value": g_confirm},
        {"label": "Overrides exists", "ok": g_exists, "value": g_exists},
        {"label": "Overrides enabled", "ok": g_enabled, "value": g_enabled},
        {"label": "Production guard ready", "ok": g_guard, "value": guard_decision},
        {"label": "Approved production_apply_candidate", "ok": g_approved, "value": g_approved},
        {"label": "Overrides freshness (<=72h)", "ok": g_fresh,
         "value": (cfg or {}).get("generated_at") if g_exists else None},
        {"label": "Fingerprint consistency", "ok": g_consistency, "value": fingerprint_match},
    ]

    reasons = []
    if not g_switch:
        reasons.append("PRODUCTION_NEWS_OVERRIDES_ENABLED is False")
    if not g_confirm:
        reasons.append("production_apply_confirmed is False")
    if not g_exists:
        reasons.append("overrides file missing")
    if g_exists and not g_enabled:
        reasons.append("overrides enabled is False")
    if not g_guard:
        reasons.append("production guard is not ready")
    if not g_approved:
        reasons.append("approved production_apply_candidate missing")
    if not g_fresh:
        reasons.append("overrides generated_at is stale")
    if not g_consistency and consistency_reason:
        reasons.append(consistency_reason)

    allow = all(g["ok"] for g in gates)  # switch が OFF固定のため v43 でも常に False

    summary = {
        "production_switch": PRODUCTION_NEWS_OVERRIDES_ENABLED,
        "production_apply_confirmed": g_confirm,
        "overrides_exists": g_exists,
        "overrides_enabled": g_enabled,
        "guard_decision": guard_decision,
        "approved_candidate_exists": g_approved,
        "freshness_ok": g_fresh,
        "consistency_ok": g_consistency,
        "current_fingerprint": current_fp,
        "candidate_fingerprint": candidate_fp,
        "fingerprint_match": fingerprint_match,
        "allow": allow,
    }
    return {"allow": allow, "gates": gates, "reasons": reasons, "summary": summary,
            "current_fingerprint": current_fp, "candidate_fingerprint": candidate_fp,
            "fingerprint_match": fingerprint_match}


# ---------------- v44: 本番適用関数の足場（switch OFF で絶対未適用・本番非接続） ----------------
def apply_news_overrides_if_allowed(news_sum):
    """news_sum に overrides を適用…する“足場”。⚠️ v44 では絶対に適用しない（applied=False 固定）。
    本番ニューススコア・発掘スコア・ランキングには一切反映しない。news.py からは未接続。
    必ず deep copy を返し、元の news_sum（categories/reasons のネスト含む）を破壊しない。
    None / 非dict でも落ちない。"""
    copied = copy.deepcopy(news_sum) if isinstance(news_sum, dict) else {}
    status = get_production_overrides_status()

    if not status.get("allow"):
        return {"news_sum": copied, "applied": False,
                "reason": "Production overrides not allowed", "status": status}

    # ⚠️ allow=True に到達しても v44 では意図的に未適用（足場のみ）。
    # TODO(v45+): allow=True かつ本番解禁時のみ、avg_impact を overrides で調整し
    #             reasons に「overrides適用」を追加、score を再計算する（今回は実装しない）。
    return {"news_sum": copied, "applied": False,
            "reason": "Production overrides application is intentionally disabled in v44 safety mode",
            "status": status}
