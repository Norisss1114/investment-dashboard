"""
history.py — 売買履歴(CSV)の保存・読込と、実現損益・勝率・失敗パターン分析。
"""
import config
from collections import defaultdict, deque
from modules import storage
from modules.utils import safe_float

HISTORY_FIELDS = ["date", "ticker", "side", "price", "shares", "reason", "result"]
_CHASE_WORDS = ["高値", "追いかけ", "急騰", "fomo", "焦", "飛びつ"]
_PANIC_WORDS = ["怖", "パニック", "狼狽", "不安", "つられ"]


def load_history() -> list:
    rows = storage.load_csv(config.HISTORY_PATH, HISTORY_FIELDS)
    out = []
    for r in rows:
        out.append({
            "date": r.get("date", ""), "ticker": (r.get("ticker", "") or "").upper(),
            "side": r.get("side", ""), "price": safe_float(r.get("price")),
            "shares": safe_float(r.get("shares")), "reason": r.get("reason", ""),
            "result": r.get("result", ""),
        })
    return out


def save_history(rows: list) -> bool:
    return storage.save_csv(config.HISTORY_PATH, HISTORY_FIELDS, rows)


def _is_buy(side: str) -> bool:
    return ("買" in side) or (side.strip().lower() in ("buy", "b", "long"))


def realized_trades(history: list) -> list:
    """FIFOで買い→売りをマッチングし、確定した取引(損益%)のリストを返す。"""
    by_t = defaultdict(list)
    for h in sorted(history, key=lambda x: x.get("date", "")):
        by_t[h["ticker"]].append(h)

    closed = []
    for ticker, trades in by_t.items():
        lots = deque()  # (price, shares)
        for tr in trades:
            qty = tr["shares"]; price = tr["price"]
            if qty <= 0 or price <= 0:
                continue
            if _is_buy(tr["side"]):
                lots.append([price, qty])
            else:  # 売り
                remaining = qty
                while remaining > 0 and lots:
                    buy_price, buy_qty = lots[0]
                    matched = min(remaining, buy_qty)
                    pnl = (price - buy_price) * matched
                    pnl_pct = (price / buy_price - 1) * 100 if buy_price else 0
                    closed.append({"ticker": ticker, "buy": buy_price, "sell": price,
                                   "shares": matched, "pnl": round(pnl, 2),
                                   "pnl_pct": round(pnl_pct, 1), "date": tr["date"],
                                   "reason": tr["reason"], "result": tr["result"]})
                    remaining -= matched
                    lots[0][1] -= matched
                    if lots[0][1] <= 0:
                        lots.popleft()
    return closed


def stats(history: list) -> dict:
    closed = realized_trades(history)
    n = len(closed)
    wins = [c for c in closed if c["pnl"] > 0]
    losses = [c for c in closed if c["pnl"] <= 0]
    total_pnl = sum(c["pnl"] for c in closed)
    gross_win = sum(c["pnl"] for c in wins)
    gross_loss = -sum(c["pnl"] for c in losses)
    return {
        "closed": closed, "n": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "avg_win_pct": round(sum(c["pnl_pct"] for c in wins) / len(wins), 1) if wins else 0.0,
        "avg_loss_pct": round(sum(c["pnl_pct"] for c in losses) / len(losses), 1) if losses else 0.0,
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (float("inf") if gross_win else 0.0),
        "n_wins": len(wins), "n_losses": len(losses),
    }


def failure_patterns(history: list, st: dict) -> list:
    """よくある失敗パターンをルールベースで抽出。"""
    pats = []
    closed = st["closed"]
    if not closed:
        return ["まだ確定した取引がありません。"]

    # 損大利小
    if st["avg_loss_pct"] and st["avg_win_pct"] and abs(st["avg_loss_pct"]) > st["avg_win_pct"]:
        pats.append(f"損大利小: 平均利益 +{st['avg_win_pct']:.0f}% < 平均損失 {st['avg_loss_pct']:.0f}%（損切りが遅い可能性）")
    # 深い損切り（-15%超）の割合
    deep = [c for c in closed if c["pnl_pct"] <= -15]
    if len(deep) >= max(1, len(closed) // 4):
        pats.append(f"損切りを遅らせる傾向: -15%超の損失が {len(deep)}件")
    # 利確が早い（+8%未満で利確した勝ち）
    small_win = [c for c in closed if 0 < c["pnl_pct"] < 8]
    if len(small_win) >= max(1, st["n_wins"] // 2) and st["n_wins"]:
        pats.append(f"利確が早すぎる傾向: +8%未満での利確が {len(small_win)}件")
    # 高値追い・狼狽（理由テキストから）
    chase = [h for h in history if _is_buy(h["side"]) and any(w in (h["reason"] or "") for w in _CHASE_WORDS)]
    if chase:
        pats.append(f"高値追いが疑われる買い: {len(chase)}件（理由に『高値/急騰/焦り』等）")
    panic = [h for h in history if not _is_buy(h["side"]) and any(w in (h["reason"] or "") for w in _PANIC_WORDS)]
    if panic:
        pats.append(f"狼狽売りが疑われる売り: {len(panic)}件")

    if not pats:
        pats.append("大きな悪癖は見当たりません。ルールを守れています。")
    return pats


def rule_adherence(history: list, st: dict) -> dict:
    """ルール遵守の簡易評価。"""
    closed = st["closed"]
    deep = sum(1 for c in closed if c["pnl_pct"] <= -12)
    with_reason = sum(1 for h in history if (h.get("reason") or "").strip())
    total = max(1, len(history))
    return {
        "deep_losses": deep,
        "reason_rate": round(with_reason / total * 100, 0),
        "good": deep == 0 and (with_reason / total) >= 0.7,
    }


def normalize_rows(rows: list) -> list:
    """data_editor等の生の行を、stats が使える形に正規化。"""
    out = []
    for r in rows or []:
        t = str(r.get("ticker", "") or "").strip().upper()
        if not t:
            continue
        out.append({"date": str(r.get("date", "") or ""), "ticker": t,
                    "side": str(r.get("side", "") or ""), "price": safe_float(r.get("price")),
                    "shares": safe_float(r.get("shares")), "reason": str(r.get("reason", "") or ""),
                    "result": str(r.get("result", "") or "")})
    return out
