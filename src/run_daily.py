#!/usr/bin/env python3
"""日次パイプライン本体。

  python src/run_daily.py                 # 今日ぶんを1回
  python src/run_daily.py --date 2026-07-30
  python src/run_daily.py --backfill 45   # 過去45日をまとめて生成（デモ用）

GitHub Actions からはこれを1日1回叩くだけ。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze  # noqa: E402
import comment  # noqa: E402
import diff  # noqa: E402
from build_site import build_site  # noqa: E402
from collect import llm, signals  # noqa: E402
from common import days_ago, demo_mode, snapshot_path, today, write_json  # noqa: E402


def _expected_calls(tier: str = "core") -> int:
    """その日に返ってくるはずの回答数。live実行の健全性チェックに使う。"""
    from common import load, load_prompts
    cfg = load("settings")
    n = min(len(load_prompts(tier)), cfg["sampling"]["tier_schedule"][tier]["max_prompts"])
    surfaces = len([s for s in cfg["surfaces"] if s.get("enabled")])
    runs = cfg["sampling"]["runs_per_prompt"] if tier == "core" else 1
    return n * surfaces * runs


def run_one(day: str, quiet: bool = False) -> dict:
    if not quiet:
        print(f"[{day}] collecting…")
    responses = llm.collect(day, tier="core")

    # ---- live実行の安全弁 ----
    # 認証ミスやモデル名の誤りで全滅すると、スコア0の日が履歴に残って
    # 移動中央値と±2σを永久に汚す。半分も取れなければ何も書かずに落とす。
    if not demo_mode():
        exp = _expected_calls("core")
        if len(responses) < exp * 0.5:
            sys.exit(f"live実行が異常です: 期待{exp}件に対し{len(responses)}件しか取得できませんでした。"
                     f"\n認証情報・モデル名・残高を確認してください。"
                     f"\nスナップショットは書いていないので、履歴は汚れていません。")
    import harvest
    nf = harvest.save_fanout(day, responses)      # AIが内部で投げた派生クエリを回収
    if nf and not quiet:
        print(f"  fan-out {nf}種を保存")
    sig = signals.collect(day)
    snap = analyze.aggregate(day, responses, sig)
    write_json(snapshot_path(day), snap, compact=True)
    if not quiet:
        print(f"[{day}] score={snap['score']} "
              f"presence={snap['factors']['presence']:.1f} "
              f"earned={snap['factors']['earned_citation']:.1f}")
    return snap


def finalize(day: str) -> None:
    """差分・コメント・サイトを作る。"""
    diffs = diff.build(day)
    cmt = comment.build(day, diffs)
    snap = __import__("common").read_json(snapshot_path(day))
    write_json(snapshot_path(day), {**snap, "diff": diffs, "comment": cmt}, compact=True)
    build_site(day)
    from common import prune_snapshots
    n = prune_snapshots()
    if n:
        print(f"  古いスナップショット {n}件から明細を削除しました")
    print("\n— 本日のコメント —")
    print(cmt["headline"])
    for p, lines in cmt["periods"].items():
        if lines:
            print(f"  [{p}] " + " / ".join(lines))
    print(f"  {cmt['alert_summary']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today())
    ap.add_argument("--backfill", type=int, default=0)
    a = ap.parse_args()

    if a.backfill:
        if not demo_mode():
            sys.exit("backfill はデモモード専用です（過去のAI回答は再現できないため）")
        for i in range(a.backfill, -1, -1):
            run_one(days_ago(a.date, i), quiet=True)
        print(f"backfilled {a.backfill + 1} days")

    if not a.backfill:
        run_one(a.date)
    finalize(a.date)


if __name__ == "__main__":
    main()
