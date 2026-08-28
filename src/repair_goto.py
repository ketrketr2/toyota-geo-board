#!/usr/bin/env python3
"""過去スナップショットの google.com/goto 汚染を修復する（1回限りの後始末）。

2026-08-25〜27 の AIによる概要／AIモードの引用が、Google の追跡リダイレクト
（https://google.com/goto?url=CAES…）で返るようになり、引用元が全部 google.com に
化けていた。トークンは今も解決できるので、実URLに直してから集計をやり直す。

スコアと因数は analyze._compute をそのまま使う。再計算のロジックを別に書くと
本番と食い違うため、絶対に自前で書かない。
"""
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "collect"))

from analyze import _compute, classify_url          # noqa: E402
from collect.llm import _is_goto, _resolve_goto     # noqa: E402
from common import load, surface_key                # noqa: E402


def repair(day: str, apply: bool = False) -> dict:
    path = ROOT / "data" / "snapshots" / f"{day}.json"
    snap = json.loads(path.read_text(encoding="utf-8"))
    cells = snap["cells"]

    # ---- 1) goto を実URLに解決 ----
    gotos = sorted({cit["url"] for c in cells for cit in c["citations"]
                    if _is_goto(cit.get("url", ""))})
    if not gotos:
        return {"day": day, "goto": 0, "skipped": True}
    with ThreadPoolExecutor(max_workers=12) as ex:
        resolved = dict(zip(gotos, ex.map(_resolve_goto, gotos)))
    ok = sum(1 for v in resolved.values() if v)

    # ---- 2) 引用を書き換えて分類し直す ----
    for c in cells:
        for cit in c["citations"]:
            u = cit.get("url", "")
            if not _is_goto(u):
                continue
            real = resolved.get(u) or ""
            cit.update(classify_url(real or u), url=real or u)
        # セル単位のフラグも作り直す（本番と同じ定義）
        c["own_cited"] = any(x["bucket"] == "owned" for x in c["citations"])
        c["dealer_cited"] = any(x["bucket"] == "dealer" for x in c["citations"])
        c["affiliated_cited"] = any(x["bucket"] == "affiliated" for x in c["citations"])

    # ---- 3) 本番と同じ関数で集計をやり直す ----
    cfg, br, pf = load("settings"), load("brands"), load("platforms")
    own_id = br["own"]["id"]
    # ブランドは当時の構成をそのまま使う。あとから競合を足しているので、
    # 今の設定で読むと古い日のセルに無いブランドで KeyError になるうえ、
    # 分母が変わって「直したのに数字が動く」原因にもなる。
    all_brands = list(cells[0]["brands"].keys()) if cells else [own_id]
    sov_brands = [b for b in (snap.get("sov_brands") or all_brands) if b in all_brands]

    new_factors, platforms_out, extras = _compute(cells, br, pf, own_id, all_brands, sov_brands)
    cohort_cells = [c for c in cells if c.get("cohort")]
    coh_new, _, coh_extras = (_compute(cohort_cells, br, pf, own_id, all_brands, sov_brands)
                              if cohort_cells else (new_factors, platforms_out, extras))

    # goto が壊したのは「引用の分類」だけ。出現率・順位・センチメント・シェアは
    # 回答テキストから出ており、URLの解決とは無関係。ここまで今のコードで
    # 上書きすると、あとから定義を変えた因数（センチメント）が過去日に
    # 遡って適用され、直したはずが別の理由でスコアが動いてしまう。
    # そのため引用由来の2因数だけを差し替える。
    CITE_FACTORS = ("owned_citation", "earned_citation")

    def merge(orig: dict, new: dict) -> dict:
        return {k: (round(new[k], 2) if k in CITE_FACTORS else v) for k, v in orig.items()}

    factors = merge(snap["factors"], new_factors)
    coh_factors = merge(snap["cohort"]["factors"], coh_new)
    # 重みもその日のものを使う（あとで配点を変えても過去日は当時の基準のまま）
    w = snap.get("weights") or cfg["score_weights"]
    score = sum(factors[k] * w[k] / 100 for k in w)
    coh_score = sum(coh_factors[k] * w[k] / 100 for k in w)

    before = snap["score"]
    snap.update({
        "score": round(score, 2),
        "factors": factors,
        "citation_scopes": extras["citation_scopes"],
        "platforms": platforms_out,
        "top_domains": Counter(cit["host"] for c in cells
                               for cit in c["citations"]).most_common(15),
        "citation_buckets": dict(Counter(cit["bucket"] for c in cells
                                         for cit in c["citations"])),
        "cells": cells,
    })
    snap["cohort"].update({
        "score": round(coh_score, 2),
        "factors": coh_factors,
    })
    # 修復済みの印。あとから「この日は直した日」と分かるようにする。
    snap["repaired"] = {"reason": "google.com/goto redirect resolution",
                        "resolved": ok, "of": len(gotos)}

    sns = sum(p["market_citations"] for p in platforms_out)
    if apply:
        path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return {"day": day, "goto": len(gotos), "resolved": ok,
            "score_before": before, "score_after": round(score, 2), "sns_market": sns}


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    for day in [a for a in sys.argv[1:] if not a.startswith("-")]:
        print(json.dumps(repair(day, apply), ensure_ascii=False))
