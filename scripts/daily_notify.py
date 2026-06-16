#!/usr/bin/env python3
"""
daily_notify.py (v12) — 毎朝の通知をコマンドラインから実行する。
cron / launchd から呼ぶことで、アプリを開かなくても通知が届く。

実行内容:
  1) 市場環境の評価
  2) 発掘スキャン（キャッシュが古ければ軽量×高速で更新）
  3) 保有アクション＋ニュース精密解析
  4) ダイジェスト生成 → 通知送信
  5) 履歴保存

使い方（プロジェクト直下で）:
  python3 scripts/daily_notify.py            # 通知設定の通知先へ送信
  python3 scripts/daily_notify.py --dry-run  # 送信せず本文を表示
  MOCK_MODE=1 python3 scripts/daily_notify.py --dry-run  # APIキー無しで確認

通知先は user_data/notify_settings.json（アプリの「🔔 通知設定」で保存）
または環境変数 DISCORD_WEBHOOK_URL / TELEGRAM_TOKEN+TELEGRAM_CHAT を使用。
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from modules import (macro, engine, portfolio, discovery, notify, storage)  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="送信せず本文を表示")
    ap.add_argument("--scan", choices=["auto", "always", "never"], default="auto",
                    help="発掘スキャンの実行可否（既定auto=キャッシュが古ければ実行）")
    args = ap.parse_args()

    idx = macro.get_market_indices()
    regime = macro.assess_regime(idx)
    events = macro.upcoming_events()

    # 発掘キャッシュ（古ければ軽量×高速で更新）
    cache = discovery.load_cache()
    need_scan = args.scan == "always" or (args.scan == "auto" and not discovery.is_fresh(cache))
    if need_scan:
        print("発掘スキャンを実行（軽量×高速）…")
        watch = []
        try:
            settings = storage.load_settings()
            watch = settings.get("watchlist", []) if settings else []
        except Exception:
            pass
        discovery.scan("full", config.DEFAULT_SCAN_MODE, config.DEFAULT_ANALYSIS_MODE,
                       regime["score"], regime["regime"], regime["fomc_days"],
                       watchlist=tuple(watch))

    positions = portfolio.load_positions()
    watch = []
    try:
        settings = storage.load_settings()
        watch = settings.get("watchlist", []) if settings else []
    except Exception:
        pass

    def analyze(t):
        return engine.analyze_ticker(t, regime["score"], regime["regime"], regime["fomc_days"])

    digest = notify.build_digest(regime, events, positions, tuple(watch), analyze)

    if args.dry_run:
        print("\n" + digest["text"])
        print("\n[dry-run] 送信はしていません。")
        return

    nset = notify.load_settings()
    ok, err = notify.send(nset, digest["text"], digest.get("markdown"))
    notify.log_history(nset.get("target"), digest, ok, err)
    print(f"送信: {'成功' if ok else '失敗'}" + (f"（{err}）" if err else ""))


if __name__ == "__main__":
    main()
