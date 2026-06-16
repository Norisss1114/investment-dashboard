"""
moomoo_import.py (v8.1) — Moomoo保有スクショの最小取り込み。
Anthropic Vision で画像から保有銘柄を抽出 → 検証 → positions.json へ保存。
キーが無ければ手入力補助モード（空の表を編集して保存）。
※差分比較・同期はv8.5以降。ここは「読み取り→表示→修正→保存」だけ。
"""
import os
import re
import json
import base64

import config
from modules import portfolio

MOOMOO_FIELDS = ["ticker", "name", "quantity", "average_cost", "current_price", "market_value"]


def vision_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def empty_row():
    return {k: ("" if k in ("ticker", "name") else 0) for k in MOOMOO_FIELDS}


def parse_screenshot(image_bytes, media_type="image/png"):
    """画像から保有を抽出。返り値 (rows, error)。失敗時 rows=[]。"""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return [], "ANTHROPIC_API_KEY未設定（手入力補助モード）"
    try:
        import anthropic
    except Exception:
        return [], "anthropicライブラリ未導入です（pip install anthropic）。手入力をご利用ください。"
    try:
        b64 = base64.b64encode(image_bytes).decode()
        client = anthropic.Anthropic(api_key=key)
        prompt = (
            "これはMoomooの保有銘柄(ポジション)画面のスクリーンショットです。"
            "保有している各銘柄を抽出し、JSON配列のみを出力してください。各要素は "
            '{"ticker":"","name":"","quantity":数値,"average_cost":数値,"current_price":数値,"market_value":数値}。'
            "通貨記号やカンマは除いた数値にし、読み取れない項目は0または空文字にしてください。"
            "前置き・説明・コードフェンスは禁止。JSON配列だけを返してください。")
        msg = client.messages.create(
            model=getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-6"), max_tokens=2000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": prompt}]}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\[.*\]", text, re.S)
        arr = json.loads(m.group(0)) if m else json.loads(text)
        rows = []
        for it in arr:
            if not isinstance(it, dict):
                continue
            rows.append({k: it.get(k, "" if k in ("ticker", "name") else 0) for k in MOOMOO_FIELDS})
        if not rows:
            return [], "保有銘柄を読み取れませんでした。手入力してください。"
        return rows, None
    except Exception as e:
        return [], f"画像解析に失敗しました: {type(e).__name__}. 手入力をご利用ください。"


def _num(v):
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("$", "").replace("¥", "").strip()
        return float(v) if v not in ("", None) else 0.0
    except Exception:
        return 0.0


def validate_rows(rows):
    """(clean, errors, warnings)。空行はスキップ、異常はエラー、軽微は警告。"""
    clean, errors, warns = [], [], []
    for idx, r in enumerate(rows, 1):
        t = str(r.get("ticker", "") or "").strip().upper()
        if not t and not any(_num(r.get(k)) for k in ("quantity", "average_cost")):
            continue  # 完全な空行はスキップ
        if not t:
            errors.append(f"{idx}行目: ティッカーが未入力")
            continue
        if not re.fullmatch(r"[A-Z0-9.\-]{1,6}", t):
            errors.append(f"{idx}行目: ティッカー '{t}' が不正な形式")
            continue
        q = _num(r.get("quantity")); ac = _num(r.get("average_cost"))
        cp = _num(r.get("current_price")); mv = _num(r.get("market_value"))
        if q <= 0:
            errors.append(f"{t}: 株数が0以下です")
            continue
        if ac <= 0:
            errors.append(f"{t}: 平均取得単価が0以下です")
            continue
        if q > 1e7 or ac > 1e6 or cp > 1e6:
            errors.append(f"{t}: 値が異常に大きいです（桁の確認を）")
            continue
        if cp > 0 and mv > 0 and abs(q * cp - mv) / mv > 0.30:
            warns.append(f"{t}: 評価額({mv:,.0f}) と 株数×現在値({q*cp:,.0f}) が30%以上ずれています（読取ミスの可能性）")
        clean.append({"ticker": t, "name": str(r.get("name", "") or ""), "quantity": q,
                      "average_cost": ac, "current_price": cp, "market_value": mv})
    return clean, errors, warns


def existing_tickers():
    return {p["ticker"] for p in portfolio.load_positions()}


def save_positions(clean_rows, overwrite=True):
    """positions.json に保存。返り値 (ok, added, updated)。
    既存銘柄は数量・取得単価のみ更新し、損切り/利確等は保持する。"""
    existing = portfolio.load_positions()
    by = {p["ticker"]: p for p in existing}
    added, updated = [], []
    for r in clean_rows:
        t = r["ticker"]
        if t in by:
            if overwrite:
                old = by[t]
                old["avg_cost"] = r["average_cost"]
                old["shares"] = r["quantity"]
                if r["name"] and not old.get("memo"):
                    old["memo"] = r["name"]
                updated.append(t)
            # overwrite=False の同一銘柄はスキップ
        else:
            by[t] = {"ticker": t, "avg_cost": r["average_cost"], "shares": r["quantity"],
                     "buy_date": "", "memo": (r["name"] or "Moomooインポート"),
                     "purpose": "Moomooインポート", "init_stop": 0, "init_tp1": 0, "init_tp2": 0}
            added.append(t)
    ok = portfolio.save_positions(list(by.values()))
    return ok, added, updated


# ===================== v8.5: 差分比較・同期 =====================
def diff_positions(clean_rows):
    """既存 positions.json と読み取り結果を比較。
    返り値: {added, qty_up, qty_down, cost_changed, unchanged, missing}。
    missing = 既存にあるがスクショに無い（=全売却の可能性。自動削除はしない）。"""
    existing = {p["ticker"]: p for p in portfolio.load_positions()}
    new_by = {r["ticker"]: r for r in clean_rows}
    added, qty_up, qty_down, cost_changed, unchanged = [], [], [], [], []

    for t, r in new_by.items():
        if t not in existing:
            added.append({"ticker": t, "shares": r["quantity"], "avg_cost": r["average_cost"]})
            continue
        old = existing[t]
        os_, oc = float(old.get("shares", 0)), float(old.get("avg_cost", 0))
        ns, nc = r["quantity"], r["average_cost"]
        rec = {"ticker": t, "old_shares": os_, "new_shares": ns, "old_cost": oc, "new_cost": nc}
        if abs(ns - os_) > 1e-9:
            (qty_up if ns > os_ else qty_down).append(rec)
        elif abs(nc - oc) / (oc or 1) > 0.005:
            cost_changed.append(rec)
        else:
            unchanged.append(rec)

    missing = [{"ticker": t, "shares": float(p.get("shares", 0)), "avg_cost": float(p.get("avg_cost", 0))}
               for t, p in existing.items() if t not in new_by]
    return {"added": added, "qty_up": qty_up, "qty_down": qty_down,
            "cost_changed": cost_changed, "unchanged": unchanged, "missing": missing}


def apply_sync(clean_rows, delete_missing=()):
    """読み取り結果を positions.json に同期。
    - 既存銘柄は株数・取得単価のみ更新（損切り/利確などは保持）
    - 新規は追加
    - delete_missing に含む既存銘柄のみ削除（明示選択時だけ）
    返り値: (ok, summary)。"""
    existing = portfolio.load_positions()
    by = {p["ticker"]: p for p in existing}
    new_by = {r["ticker"]: r for r in clean_rows}
    added, updated, deleted = [], [], []

    for t, r in new_by.items():
        if t in by:
            by[t]["avg_cost"] = r["average_cost"]
            by[t]["shares"] = r["quantity"]
            if r["name"] and not by[t].get("memo"):
                by[t]["memo"] = r["name"]
            updated.append(t)
        else:
            by[t] = {"ticker": t, "avg_cost": r["average_cost"], "shares": r["quantity"],
                     "buy_date": "", "memo": (r["name"] or "Moomooインポート"),
                     "purpose": "Moomooインポート", "init_stop": 0, "init_tp1": 0, "init_tp2": 0}
            added.append(t)

    for t in delete_missing:
        if t in by:
            del by[t]; deleted.append(t)

    ok = portfolio.save_positions(list(by.values()))
    return ok, {"added": added, "updated": updated, "deleted": deleted}


# ===================== v8.5: 差分比較 / 同期適用 =====================
def diff_positions(clean_rows):
    """positions.json と読み取りデータの差分を分類して返す。"""
    existing = portfolio.load_positions()
    by_old = {p["ticker"]: p for p in existing}
    new_by = {r["ticker"]: r for r in clean_rows}

    added, qty_up, qty_down, cost_changed, unchanged = [], [], [], [], []
    for t, r in new_by.items():
        ns, nc = r["quantity"], r["average_cost"]
        if t not in by_old:
            added.append({"ticker": t, "new_shares": ns, "new_avg": nc})
            continue
        old = by_old[t]; os_, oc = old["shares"], old["avg_cost"]
        d = {"ticker": t, "old_shares": os_, "new_shares": ns, "old_avg": oc, "new_avg": nc}
        if abs(ns - os_) > 1e-6:
            (qty_up if ns > os_ else qty_down).append(d)
        elif abs(nc - oc) > 1e-6:
            cost_changed.append(d)
        else:
            unchanged.append(d)
    missing = [{"ticker": t, "old_shares": p["shares"], "old_avg": p["avg_cost"]}
               for t, p in by_old.items() if t not in new_by]
    return {"added": added, "qty_up": qty_up, "qty_down": qty_down,
            "cost_changed": cost_changed, "unchanged": unchanged, "missing": missing}


def apply_sync(clean_rows, delete_missing=()):
    """読み取りデータでpositions.jsonを同期。
    既存は数量・取得単価のみ更新（損切り/利確は保持）。新規は追加。
    delete_missing に含まれる銘柄のみ削除（スクショに無い既存ポジションの全売却扱い）。
    返り値 (ok, {added, updated, deleted})。"""
    existing = portfolio.load_positions()
    by = {p["ticker"]: p for p in existing}
    new_by = {r["ticker"]: r for r in clean_rows}
    added, updated, deleted = [], [], []
    for t, r in new_by.items():
        if t in by:
            by[t]["avg_cost"] = r["average_cost"]
            by[t]["shares"] = r["quantity"]
            if r["name"] and not by[t].get("memo"):
                by[t]["memo"] = r["name"]
            updated.append(t)
        else:
            by[t] = {"ticker": t, "avg_cost": r["average_cost"], "shares": r["quantity"],
                     "buy_date": "", "memo": (r["name"] or "Moomoo同期"),
                     "purpose": "Moomoo同期", "init_stop": 0, "init_tp1": 0, "init_tp2": 0}
            added.append(t)
    for t in delete_missing:
        if t in by and t not in new_by:
            del by[t]; deleted.append(t)
    ok = portfolio.save_positions(list(by.values()))
    return ok, {"added": added, "updated": updated, "deleted": deleted}
