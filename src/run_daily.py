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


def run_one(day: str, quiet: bool = False) -> dict:
    if not quiet:
        print(f"[{day}] collecting…")
    responses = llm.collect(day, tier="core")
    sig = signals.collect(day)
    snap = analyze.aggregate(day, responses, sig)
    write_json(snapshot_path(day), snap)
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
    write_json(snapshot_path(day), {**snap, "diff": diffs, "comment": cmt})
    build_site(day)
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
