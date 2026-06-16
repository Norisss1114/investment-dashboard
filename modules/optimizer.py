"""
optimizer.py (v10) — 発掘ルールのパラメータ自動最適化（グリッドサーチ）。
各組み合わせを discovery_backtest.run_backtest で検証し、期待値/PF/最大DD/
ランダム・指数比較/ウォークフォワード(前半・後半)/総合スコア/過学習フラグを算出。
※過去検証であり将来を保証しません。過学習回避のためシンプルな設定を優先加点。
"""
import itertools
import numpy as np

import config
from modules import discovery_backtest as bt, storage

OPT_CACHE_PATH = "user_data/optimization_cache.json"

# (key, ラベル, 候補)
DIMENSIONS = [
    ("hold", "保有期間", list(config.BACKTEST_HOLD)),
    ("entry", "エントリー方式", config.BACKTEST_ENTRY),
    ("condition", "スコア条件", config.BACKTEST_CONDITION),
    ("stop", "損切り方式", config.BACKTEST_STOP),
    ("take", "利確方式", config.BACKTEST_TAKE),
    ("rsi_exclude", "RSI除外", config.BACKTEST_RSI_EXCLUDE),
    ("volume_cond", "出来高条件", config.BACKTEST_VOLUME),
    ("weights", "スコア重み", list(config.WEIGHT_PRESETS)),
]
_KEYS = [k for k, _, _ in DIMENSIONS]
_DEFAULTS = {"entry": "翌営業日始値", "stop": "初期損切り",
             "take": "利確1で半分→利確2で残り", "rsi_exclude": "なし",
             "volume_cond": "なし", "weights": "現在設定"}


def count_combos(selected) -> int:
    n = 1
    for k in _KEYS:
        n *= max(1, len(selected.get(k, []) or []))
    return n


def _complexity(p) -> int:
    return sum(1 for k, dv in _DEFAULTS.items() if p.get(k) != dv)


def _sharpe(trades):
    pn = [t["pnl_pct"] for t in trades]
    if len(pn) < 2:
        return 0.0
    s = float(np.std(pn))
    return round(float(np.mean(pn)) / s, 2) if s else 0.0


def _walk_forward(trades):
    if len(trades) < 6:
        return None
    ds = sorted(t["date"] for t in trades)
    mid = ds[len(ds) // 2]
    first = [t["pnl_pct"] for t in trades if t["date"] <= mid]
    second = [t["pnl_pct"] for t in trades if t["date"] > mid]

    def e(x):
        return round(float(np.mean(x)), 2) if x else 0.0
    fe, se = e(first), e(second)
    return {"first": fe, "second": se, "first_n": len(first), "second_n": len(second),
            "stable": fe > 0 and se > 0, "overfit": fe > 0 and se <= 0}


def _composite(m, comp, wf, complexity):
    exp = m.get("expectancy", 0); pf = m.get("profit_factor", 0); dd = m.get("max_dd", 0)
    n = m.get("trades", 0); wr = m.get("win_rate", 0)
    beat_rand = (comp.get("発掘ルール", 0) or 0) - (comp.get("ランダム同数", 0) or 0)
    beat_spy = (comp.get("発掘ルール", 0) or 0) - (comp.get("S&P500(SPY)放置") or 0)
    s = 30.0
    s += max(-20, min(30, exp * 15))
    s += max(0, min(20, (pf - 1) * 25))
    s += max(-20, min(15, (dd + 25) * 0.6))
    s += 10 if n >= 30 else n / 3
    s += max(-10, min(10, beat_rand * 0.5))
    s += max(-10, min(10, beat_spy * 0.5))
    if wf and wf["stable"]:
        s += 10
    if wf and wf["overfit"]:
        s -= 12
    s -= complexity * 2
    return round(max(0, min(100, s)), 1)


def _overfit_flags(m, comp, wf, trades):
    from collections import Counter
    flags = []
    n = m.get("trades", 0)
    if n < 20:
        flags.append("トレード数が少なすぎる（偶然の可能性）")
    cnt = Counter(t.get("sector") for t in trades)
    if cnt and n:
        top, topn = cnt.most_common(1)[0]
        if topn / n > 0.5:
            flags.append(f"特定セクター({top})に{topn/n*100:.0f}%偏り")
    if m.get("max_dd", 0) <= -30:
        flags.append("最大DDが大きすぎる（-30%超）")
    if m.get("profit_factor", 0) >= 1.3 and m.get("win_rate", 100) < 35:
        flags.append("PFは高いが勝率が低すぎる（少数の大勝ち依存）")
    if wf and wf["overfit"]:
        flags.append("前半だけ良く後半が悪い（過学習疑い）")
    beat_rand = (comp.get("発掘ルール", 0) or 0) - (comp.get("ランダム同数", 0) or 0)
    if abs(beat_rand) < 2:
        flags.append("ランダムとの差が小さい（優位性が不明瞭）")
    return flags


def _label(p):
    return f"{p.get('condition')}|{p.get('hold')}|{p.get('stop')}|RSI:{p.get('rsi_exclude','なし')}|{p.get('weights','現在設定')}"


def grid_search(selected, base, max_combos=None, progress_cb=None):
    max_combos = max_combos or config.OPT_MAX_COMBOS
    lists = [selected.get(k) or [base.get(k)] for k in _KEYS]
    combos = list(itertools.product(*lists))[:max_combos]
    total = len(combos)
    rows = []
    for idx, combo in enumerate(combos, 1):
        params = dict(base)
        for k, v in zip(_KEYS, combo):
            params[k] = v
        if progress_cb:
            progress_cb(idx - 1, total, _label(params))
        try:
            r = bt.run_backtest(params)
        except Exception:
            r = {"ok": False}
        if r.get("ok"):
            m = r["metrics"]; comp = r["comparisons"]; trades = r["trades"]
            wf = _walk_forward(trades); cx = _complexity(params)
            rows.append({
                "label": _label(params), **{k: params[k] for k in _KEYS},
                "trades": m["trades"], "win_rate": m["win_rate"], "avg_win": m["avg_win"],
                "avg_loss": m["avg_loss"], "expectancy": m["expectancy"],
                "profit_factor": m["profit_factor"], "max_dd": m["max_dd"],
                "total_return": m["total_return"], "sharpe": _sharpe(trades),
                "vs_random": round((comp.get("発掘ルール", 0) or 0) - (comp.get("ランダム同数", 0) or 0), 1),
                "vs_spy": round((comp.get("発掘ルール", 0) or 0) - (comp.get("S&P500(SPY)放置") or 0), 1),
                "vs_qqq": round((comp.get("発掘ルール", 0) or 0) - (comp.get("NASDAQ100(QQQ)放置") or 0), 1),
                "wf_first": (wf or {}).get("first"), "wf_second": (wf or {}).get("second"),
                "stable": bool((wf or {}).get("stable", False)), "overfit": bool((wf or {}).get("overfit", False)),
                "complexity": cx, "composite": _composite(m, comp, wf, cx),
                "flags": _overfit_flags(m, comp, wf, trades),
                "equity_curve": r.get("equity_curve", []),
                "params": {k: params[k] for k in _KEYS},
            })
        if progress_cb:
            progress_cb(idx, total, _label(params))

    rows.sort(key=lambda x: -x["composite"])
    save_results(rows)
    return rows


_CSV_FIELDS = ["label", "condition", "hold", "stop", "take", "entry", "rsi_exclude", "volume_cond",
               "weights", "trades", "win_rate", "expectancy", "profit_factor", "max_dd",
               "total_return", "sharpe", "vs_random", "vs_spy", "vs_qqq", "wf_first", "wf_second",
               "stable", "overfit", "complexity", "composite"]


def save_results(rows):
    storage.save_csv(config.OPT_RESULTS_PATH, _CSV_FIELDS, [{k: r.get(k) for k in _CSV_FIELDS} for r in rows])
    # 型つきで再表示できるようJSONも保存
    import datetime as dt
    slim = [{k: r.get(k) for k in (_CSV_FIELDS + ["flags", "params", "equity_curve"])} for r in rows]
    storage.save_json(OPT_CACHE_PATH, {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "rows": slim})


def load_cache():
    return storage.load_json(OPT_CACHE_PATH, None)


def load_results_csv_text():
    import os
    if os.path.exists(config.OPT_RESULTS_PATH):
        try:
            with open(config.OPT_RESULTS_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""


def adopt(params):
    import datetime as dt
    payload = dict(params)
    payload["_adopted_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return storage.save_json(config.STRATEGY_SETTINGS_PATH, payload)


def load_strategy():
    return storage.load_json(config.STRATEGY_SETTINGS_PATH, None)


def describe(row):
    """ベスト設定カード用の説明（なぜ良かったか/弱点/向く相場/注意点）。"""
    good, weak, suited, notes = [], [], [], []
    if row["expectancy"] > 0:
        good.append(f"期待値 {row['expectancy']:+.2f}%/トレード")
    if row["profit_factor"] >= 1.2:
        good.append(f"PF {row['profit_factor']}（利益>損失）")
    if row["vs_random"] > 0:
        good.append(f"ランダムに +{row['vs_random']}pt")
    if row["stable"]:
        good.append("前半・後半とも黒字（安定）")
    if row["max_dd"] <= -25:
        weak.append(f"最大DD {row['max_dd']}%（深め）")
    if row["win_rate"] < 40:
        weak.append(f"勝率 {row['win_rate']}%（低め＝損小利大型）")
    if row["overfit"]:
        weak.append("後半失速（過学習の疑い）")
    weak += row.get("flags", [])
    # 向いている相場
    if row.get("weights") in ("テクニカル重視", "現在設定"):
        suited.append("トレンド相場")
    if row.get("weights") == "テーマ重視":
        suited.append("テーマ物色相場")
    if row.get("stop") == "トレーリングストップ":
        suited.append("上昇継続局面（利を伸ばす）")
    if not suited:
        suited.append("中庸な相場")
    notes.append("過去検証であり将来を保証しません。実運用前に小さく試す。")
    if row.get("complexity", 0) >= 3:
        notes.append("設定が複雑め＝過剰最適化に注意。")
    return {"good": good or ["平均的"], "weak": weak or ["目立った弱点なし"], "suited": suited, "notes": notes}
