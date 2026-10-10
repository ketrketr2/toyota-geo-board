#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""残価設定型プラン（残クレ）専用の臨時計測。

・レジストリの残価・残クレ関連プロンプト（約86本）＋ config/zanka_extra.yaml の追加プロンプトを、
  有効な全AI面（ChatGPT / AIによる概要 / AIモード / Gemini）に投げ、回答本文と引用URLを全文保存する。
・あわせて残価関連の検索語について Google 自然検索の上位10件とAIによる概要の有無を取る。
・結果は data/zanka/ に置く。メインボードのスコア時系列には一切影響させない。

  python src/run_zanka.py --cap 40
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA, ROOT, demo_mode, load, today, write_json  # noqa: E402
from collect import llm  # noqa: E402
from analyze import classify_url  # noqa: E402
import yaml  # noqa: E402

OUT = DATA / "zanka"
KEYS = ("残価", "残クレ")


def zanka_prompts() -> list[dict]:
    with open(ROOT / "prompts" / "registry.yaml", encoding="utf-8") as f:
        rows = yaml.safe_load(f)["prompts"]
    base = [p for p in rows if any(k in (p.get("text") or "") for k in KEYS)]
    extra = load("zanka_extra").get("prompts") or []
    for p in extra:
        p.setdefault("tier", "zanka_extra")
        p.setdefault("category", "cost")
    return base + extra


def build_jobs(day: str, prompts: list[dict], cap: float) -> list[dict]:
    cfg = load("settings")
    surfaces = [s for s in cfg["surfaces"] if s.get("enabled")]
    return [{"day": day, "p": p, "s": s, "run": 0, "cap": cap, "tier": p.get("tier")}
            for p in prompts for s in surfaces]


def collect(jobs: list[dict]) -> list[dict]:
    out, done = [], 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(llm._one, j): j for j in jobs}
        for fut in as_completed(futs):
            r = fut.result()
            j = futs[fut]
            done += 1
            if r:
                r["tier"] = j["tier"]
                r["prompt_text"] = j["p"]["text"]
                r["keyword"] = j["p"].get("keyword")
                r["cars"] = j["p"].get("cars") or []
                out.append(r)
            if done % 50 == 0:
                print(f"  … {done}/{len(jobs)} 完了（成功 {len(out)} / ${llm.spent()['usd']:.2f}）", flush=True)
    out.sort(key=lambda r: (r["prompt_id"], r["surface"]))
    return out


def serp_organic(keyword: str) -> dict:
    """Google 自然検索の上位10件とAIによる概要の有無・引用元。"""
    body = [{"keyword": keyword, "language_code": "ja", "location_code": 2392,
             "device": "desktop", "load_async_ai_overview": True, "depth": 20}]
    task = llm._post("serp/google/organic/live/advanced", body)
    res = (task.get("result") or [{}])[0]
    items = res.get("items") or []
    organic, aio, paa, types = [], None, [], Counter()
    for it in items:
        t = str(it.get("type", ""))
        types[t] += 1
        if t == "organic":
            organic.append({"rank": it.get("rank_absolute"), "url": it.get("url"), "domain": it.get("domain"),
                            "title": (it.get("title") or "")[:120],
                            "description": (it.get("description") or "")[:300]})
        elif t.startswith("ai_overview"):
            cites = []
            llm._walk_refs(it.get("references"), cites)
            llm._walk_refs(it.get("items"), cites)
            text = ""
            for el in (it.get("items") or [it]):
                text += (el.get("text") or "") + "\n"
            aio = {"text": text.strip()[:3000], "citations": llm._dedup(cites)}
        elif t == "people_also_ask":
            for el in it.get("items") or []:
                paa.append(el.get("title") or "")
        elif t in ("video", "top_stories", "discussions_and_forums", "short_videos"):
            pass
    return {"keyword": keyword, "organic": organic[:20], "aio": aio, "paa": paa,
            "types": dict(types), "se_results_count": res.get("se_results_count"),
            "item_types": res.get("item_types")}


def summarize(responses: list[dict], serps: list[dict]) -> dict:
    """後で読みやすいように要約を作る（本文は responses 側に残る）。"""
    per = []
    for r in responses:
        cites = [classify_url(c.get("url") or "") | {"url": c.get("url")} for c in (r.get("citations") or [])]
        buckets = Counter(c.get("bucket") for c in cites)
        hosts = Counter(c.get("host") for c in cites)
        txt = r.get("text") or ""
        per.append({
            "prompt_id": r["prompt_id"], "surface": r["surface"], "tier": r.get("tier"),
            "keyword": r.get("keyword"), "cars": r.get("cars"),
            "prompt": r.get("prompt_text"),
            "n_cites": len(cites), "buckets": dict(buckets), "hosts": dict(hosts),
            "owned_cited": any("toyota.jp" in (c.get("host") or "") for c in cites),
            "owned_urls": sorted({c["url"] for c in cites if "toyota.jp" in (c.get("host") or "")}),
            "mentions_toyota": ("トヨタ" in txt) or ("TOYOTA" in txt.upper()),
            "has_yen": ("円" in txt), "has_rate": ("%" in txt) or ("％" in txt),
            "answer_head": txt[:400],
        })
    return {"responses": per, "serps": [{"keyword": s["keyword"],
                                          "toyota_rank": next((o["rank"] for o in s["organic"] if "toyota.jp" in (o.get("domain") or "")), None),
                                          "toyota_url": next((o["url"] for o in s["organic"] if "toyota.jp" in (o.get("domain") or "")), None),
                                          "has_aio": bool(s["aio"]),
                                          "aio_hosts": [classify_url(c.get("url") or "").get("host") for c in ((s["aio"] or {}).get("citations") or [])],
                                          "top5": [o.get("domain") for o in s["organic"][:5]],
                                          "paa": s["paa"]} for s in serps]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=40.0)
    ap.add_argument("--no-serp", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    if demo_mode():
        sys.exit("run_zanka はデモ実行を提供しません。GEO_BOARD_MODE=live と DataForSEO 認証を設定してください。")
    day = today()
    OUT.mkdir(parents=True, exist_ok=True)

    prompts = zanka_prompts()
    print(f"残価プロンプト {len(prompts)} 本", flush=True)

    serps = []
    if not a.no_serp:
        for kw in load("zanka_extra").get("serp_keywords") or []:
            try:
                serps.append(serp_organic(kw))
                print(f"  SERP {kw}: ok", flush=True)
            except Exception as e:
                print(f"  SERP {kw}: {type(e).__name__}: {e}", file=sys.stderr)
        write_json(OUT / f"serp_{day}.json", serps)

    responses = []
    if not a.no_llm:
        jobs = build_jobs(day, prompts, a.cap)
        print(f"ジョブ {len(jobs)} 件（上限 ${a.cap}）", flush=True)
        responses = collect(jobs)
        write_json(OUT / f"responses_{day}.json", responses)

    write_json(OUT / f"summary_{day}.json", {
        "day": day, "n_prompts": len(prompts), "n_responses": len(responses),
        "spent_usd": llm.spent().get("usd"), "errors": llm.errors(),
        **summarize(responses, serps)})
    print(f"完了: 回答 {len(responses)} 件、SERP {len(serps)} 語、${llm.spent().get('usd', 0):.2f}")


if __name__ == "__main__":
    main()
