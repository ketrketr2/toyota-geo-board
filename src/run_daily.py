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
from common import (days_ago, demo_mode, load, load_prompts,  # noqa: E402
                    snapshot_path, today, write_json)


def _expected_calls(tier: str = "core") -> int:
    """その日に返ってくるはずの回答数。live実行の健全性チェックに使う。"""
    from common import load, load_prompts
    cfg = load("settings")
    n = min(len(load_prompts(tier)), cfg["sampling"]["tier_schedule"][tier]["max_prompts"])
    base = cfg["sampling"]["runs_per_prompt"] if tier == "core" else 1
    return sum(n * (s.get("runs", base) if tier == "core" else 1)
               for s in cfg["surfaces"] if s.get("enabled"))


def run_one(day: str, quiet: bool = False) -> dict:
    if not quiet:
        print(f"[{day}] collecting…")
    responses = llm.collect(day, tier="core")

    # ---- live実行の安全弁 ----
    # 認証ミスやモデル名の誤りで取りこぼすと、実力が落ちたわけでもないのに
    # スコアが下がった日が履歴に残り、移動中央値と±2σを永久に汚す。
    # 「全体の取得率」と「面ごとの取得」の両方を見る。片面だけ全滅しても止める。
    if not demo_mode():
        exp = _expected_calls("core")
        got = {s["id"]: 0 for s in load("settings")["surfaces"] if s.get("enabled")}
        for r in responses:
            if r["surface"] in got:
                got[r["surface"]] += 1
        cfg = load("settings")
        base = cfg["sampling"]["runs_per_prompt"]
        npr = min(len(load_prompts("core")), cfg["sampling"]["tier_schedule"]["core"]["max_prompts"])
        want = {s["id"]: npr * s.get("runs", base)
                for s in cfg["surfaces"] if s.get("enabled")}
        dead = [s for s, n in got.items() if n < want[s] * 0.5]
        if len(responses) < exp * 0.7 or dead:
            # 無人で走るので、失敗理由をリポジトリに残す（ログは後から読めない）
            from common import ROOT
            (ROOT / "data").mkdir(exist_ok=True)
            (ROOT / "data" / "last_error.txt").write_text(
                f"{day} live実行が失敗しました\n"
                f"期待{exp}件 / 取得{len(responses)}件 / 面別 {got}\n"
                f"実費 {llm.spent()}\n\n"
                + "\n".join(llm.errors()), encoding="utf-8")
            sys.exit(f"live実行が異常です: 期待{exp}件に対し{len(responses)}件。"
                     f"\n面ごとの取得数: {got}"
                     + (f"\n取得できていない面: {', '.join(dead)}" if dead else "")
                     + "\n認証情報・モデル名・残高を確認してください。"
                     "\nスナップショットは書いていないので、履歴は汚れていません。")
    import harvest
    nf = harvest.save_fanout(day, responses)      # AIが内部で投げた派生クエリを回収
    if nf and not quiet:
        print(f"  fan-out {nf}種を保存")
    sig = signals.collect(day)
    snap = analyze.aggregate(day, responses, sig)
    # その日いくら使ったかを必ず残す。予算切れは静かに起きて、静かに全部止まるため。
    snap["api_cost"] = llm.spent()
    write_json(snapshot_path(day), snap, compact=True)
    if not quiet and snap["api_cost"]["calls"]:
        print(f"  DataForSEO 実費 ${snap['api_cost']['usd']:.4f} / {snap['api_cost']['calls']}回")
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


def probe() -> None:
    """本番の1日分を回す前に、最小限の実測を2〜4本だけ投げて疎通を確かめる。

    認証・エンドポイント・モデル名のどれかが違うと全滅するが、
    それを720本投げてから知るのは遅い。数円で先に分かるようにしておく。
    """
    from common import load, load_prompts
    from collect.llm import fetch_llm_response, fetch_serp_ai, spent
    if demo_mode():
        sys.exit("probe は live 専用です。GEO_BOARD_MODE=live と DATAFORSEO_LOGIN を確認してください。")
    cfg = load("settings")
    prompt = load_prompts("core")[0]["text"]
    ok = 0
    for s in [x for x in cfg["surfaces"] if x.get("enabled")]:
        fn = fetch_serp_ai if s["provider"] == "serp" else fetch_llm_response
        try:
            r = fn(prompt, s)
            ok += 1
            print(f"  ○ {s['label']}: 本文{len(r['text'])}文字 / 引用{len(r['citations'])}件")
            for c in r["citations"][:3]:
                print(f"      - {c['url']}")
        except Exception as e:
            print(f"  × {s['label']}: {e}")
    c = spent()
    print(f"\n疎通 {ok}/{len([x for x in cfg['surfaces'] if x.get('enabled')])} 面 "
          f"／ 実費 ${c['usd']:.4f}（{c['calls']}回）")
    if ok == 0:
        sys.exit("1面も取得できませんでした。本番実行はまだ行わないでください。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today())
    ap.add_argument("--backfill", type=int, default=0)
    ap.add_argument("--probe", action="store_true",
                    help="実測の疎通確認だけを行う（スナップショットは書かない）")
    a = ap.parse_args()

    if a.probe:
        probe()
        return

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
