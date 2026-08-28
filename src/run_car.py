#!/usr/bin/env python3
"""①車種別AI分析（tier=car）／②ディーラーAI分析（tier=local）の計測ランナー。

日次のメインボード（run_daily.py / tier=core）とは完全に分離する。
ここで集めた結果は data/car/snapshots/ に置き、メインのスコア時系列には一切影響させない。

  python src/run_car.py --tiers car,local --all-surfaces --cap 60   # テスト1周
  python src/run_car.py --tiers car                                 # 定常運用（面は曜日ルール準拠）

・runs は常に 1（車種別は横比較が目的で、同一クエリの反復より本数を優先する）
・--all-surfaces は曜日間引きを無視して有効な全面に投げる（テスト1周用）
・回答本文・引用は全文保存する。後から指標を再定義できるようにするため。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA, demo_mode, load, load_prompts, sentences, contains_any, today, write_json  # noqa: E402
from collect import llm  # noqa: E402
from analyze import classify_url, POSITIVE, NEGATIVE  # noqa: E402

CAR_DIR = DATA / "car" / "snapshots"


# ---------------------------------------------------------------- 車種検出
def _catalog() -> list[dict]:
    """focus + rivals を1枚の検出辞書に。aliasの長い順で照合する。"""
    cfg = load("cars")
    rows = []
    for c in cfg["focus"]:
        rows.append({**c, "own": True})
    for c in cfg["rivals"]:
        if c.get("aliases"):
            rows.append({**c, "own": False})
    return rows


def detect_cars(text: str, catalog: list[dict]) -> dict[str, dict]:
    """回答本文から車種の言及位置を取る。

    alias と guard（誤検出源: アクアリウム等）を1本のトークン列にし、
    「長い順、同長なら alias 優先」で走査して当たった区間をマスクする。
    こうしないと、ある車の guard が別の車の alias（例: ヤリスの guard
    「ヤリスクロス」）を先に潰してしまう。
    """
    if not text:
        return {}
    work = text
    tokens = [(a, c["id"]) for c in catalog for a in c["aliases"]]
    tokens += [(g, None) for c in catalog for g in (c.get("guards") or [])]
    tokens.sort(key=lambda x: (-len(x[0]), x[1] is None))
    found: dict[str, int] = {}
    for tok, cid in tokens:
        start = 0
        while True:
            i = work.find(tok, start)
            if i < 0:
                break
            if cid is not None and (cid not in found or i < found[cid]):
                found[cid] = i
            work = work[:i] + "＊" * len(tok) + work[i + len(tok):]
            start = i + len(tok)
    order = sorted(found, key=lambda k: found[k])
    return {cid: {"pos": pos, "rank": order.index(cid) + 1} for cid, pos in found.items()}


def car_sentiment(text: str, aliases: list[str], guards: list[str]) -> str | None:
    t = text
    for g in guards or []:
        t = t.replace(g, "")
    own_sents = [s for s in sentences(t) if contains_any(s, aliases)]
    if not own_sents:
        return None
    blob = "。".join(own_sents)
    p = sum(blob.count(w) for w in POSITIVE)
    n = sum(blob.count(w) for w in NEGATIVE)
    return "positive" if p > n else "negative" if n > p else "neutral"


# ---------------------------------------------------------------- 収集
def build_jobs(day: str, tiers: list[str], all_surfaces: bool, cap: float) -> list[dict]:
    cfg = load("settings")
    if all_surfaces:
        surfaces = [s for s in cfg["surfaces"] if s.get("enabled")]
    else:
        from common import surfaces_for
        surfaces = surfaces_for(day)
    jobs = []
    for tier in tiers:
        limit = cfg["sampling"]["tier_schedule"].get(tier, {}).get("max_prompts", 500)
        for p in load_prompts(tier)[:limit]:
            for s in surfaces:
                jobs.append({"day": day, "p": p, "s": s, "run": 0, "cap": cap, "tier": tier})
    return jobs


def collect(jobs: list[dict]) -> list[dict]:
    if demo_mode():
        sys.exit("run_car はデモ実行を提供しません（推定値をボードに載せないため）。"
                 "GEO_BOARD_MODE=live と DataForSEO 認証を設定してください。")
    workers = 10
    out, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(llm._one, j): j for j in jobs}
        for fut in as_completed(futs):
            r = fut.result()
            j = futs[fut]
            done += 1
            if r:
                r["tier"] = j["tier"]
                out.append(r)
            if done % 100 == 0:
                print(f"  … {done}/{len(jobs)} 完了（成功 {len(out)} / ${llm.spent()['usd']:.2f}）",
                      flush=True)
    out.sort(key=lambda r: (r["prompt_id"], r["surface"], r["run"]))
    return out


def _dealer_catalog() -> list[dict]:
    cfg = load("dealers")
    rows = []
    for pref in ("niigata", "aichi"):
        for d in cfg[pref]["dealers"]:
            rows.append({**d, "pref": pref, "main": d["id"] == cfg[pref]["main"]})
    return rows


def detect_dealers(text: str, dcat: list[dict]) -> dict[str, int]:
    """回答本文から販社の言及順位を取る（cars と同じ長いalias優先＋マスク）。"""
    if not text:
        return {}
    work = text
    pairs = [(a, d["id"]) for d in dcat for a in d["aliases"]]
    pairs.sort(key=lambda x: -len(x[0]))
    found: dict[str, int] = {}
    for alias, did in pairs:
        i = work.find(alias)
        while i >= 0:
            if did not in found or i < found[did]:
                found[did] = i
            work = work[:i] + "＊" * len(alias) + work[i + len(alias):]
            i = work.find(alias)
    order = sorted(found, key=lambda k: found[k])
    return {did: order.index(did) + 1 for did in found}


# ---------------------------------------------------------------- 集計
def build_cells(responses: list[dict], prompts_by_id: dict) -> list[dict]:
    catalog = _catalog()
    dcat = _dealer_catalog()
    by_id = {c["id"]: c for c in catalog}
    cells = []
    for r in responses:
        p = prompts_by_id.get(r["prompt_id"], {})
        det = detect_cars(r.get("text") or "", catalog)
        models = {}
        for cid, d in det.items():
            c = by_id[cid]
            models[cid] = {
                "rank": d["rank"],
                "own": bool(c.get("own")),
                "sent": car_sentiment(r.get("text") or "", c["aliases"], c.get("guards")),
            }
        cites = []
        slug_hits = []
        for c0 in r.get("citations") or []:
            info = classify_url(c0.get("url") or "")
            cites.append({**info, "url": c0.get("url"), "title": (c0.get("title") or "")[:120]})
            u = (c0.get("url") or "")
            if "toyota.jp" in u:
                for f in load("cars")["focus"]:
                    if f["slug"] and ((f["slug"] + "/") in u or u.rstrip("/").endswith(f["slug"])):
                        slug_hits.append(f["id"])
        dealer_hosts = {}
        for c1 in cites:
            for d in dcat:
                if c1.get("host") and any(c1["host"] == dm or c1["host"].endswith("." + dm)
                                          for dm in d.get("domains") or []):
                    dealer_hosts.setdefault(d["id"], 0)
                    dealer_hosts[d["id"]] += 1
        cells.append({
            "prompt_id": r["prompt_id"], "surface": r["surface"], "tier": r.get("tier"),
            "cars": p.get("cars") or [], "named_cars": p.get("named_cars") or [],
            "seg": p.get("seg"), "category": p.get("category"),
            "pref": p.get("pref"), "role": p.get("role"),
            "answer": r.get("text") or "", "citations": cites,
            "cited_car_pages": sorted(set(slug_hits)),
            "models": models,
            "dealers": detect_dealers(r.get("text") or "", dcat),
            "dealer_cites": dealer_hosts,
        })
    return cells


def summarize(cells: list[dict]) -> dict:
    """車種×面の一次集計。ファネルの定義:
    F2 mention = その車が出現すべきセル（cars に含む・named_car≠当該車）での言及率
    F3 first   = 同セル集合のうち、言及があった中で自社車が全車種中1位だった率
    """
    cfg = load("cars")
    out = {}
    for f in cfg["focus"]:
        cid = f["id"]
        tgt = [c for c in cells if cid in (c["cars"] or [])
               and cid not in (c.get("named_cars") or [])]
        n = len(tgt)
        m = [c for c in tgt if cid in c["models"]]
        first = [c for c in m if c["models"][cid]["rank"] == 1]
        sur = Counter(c["surface"] for c in m)
        sur_n = Counter(c["surface"] for c in tgt)
        rivals_cnt = Counter()
        for c in tgt:
            for rid in c["models"]:
                if rid != cid:
                    rivals_cnt[rid] += 1
        out[cid] = {
            "target_cells": n, "mention": len(m), "first": len(first),
            "mention_rate": round(len(m) / n * 100, 1) if n else None,
            "first_rate": round(len(first) / len(m) * 100, 1) if m else None,
            "by_surface": {s: {"mention": sur[s], "cells": sur_n[s]} for s in sur_n},
            "top_rivals_in_my_queries": rivals_cnt.most_common(6),
        }
    return out


def summarize_local(cells: list[dict]) -> dict:
    """②の一次集計: 県×主役販社の出現。named_dealer は自明出現のため除外して測る。"""
    cfg = load("dealers")
    out = {}
    for pref in ("niigata", "aichi"):
        main = cfg[pref]["main"]
        tgt = [c for c in cells if c.get("pref") == pref and c.get("role") in ("dealer", "market")]
        dq = [c for c in tgt if c.get("role") == "dealer"]
        hit = [c for c in dq if main in (c.get("dealers") or {})]
        cite = [c for c in tgt if main in (c.get("dealer_cites") or {})]
        alld = Counter()
        for c in tgt:
            for did in (c.get("dealers") or {}):
                alld[did] += 1
        out[pref] = {
            "main": main, "dealer_cells": len(dq),
            "main_mention": len(hit),
            "main_mention_rate": round(len(hit) / len(dq) * 100, 1) if dq else None,
            "main_cited_cells": len(cite),
            "dealer_ranking": alld.most_common(10),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", default="car")
    ap.add_argument("--date", default=today())
    ap.add_argument("--all-surfaces", action="store_true")
    ap.add_argument("--cap", type=float, default=60.0)
    a = ap.parse_args()
    tiers = [t.strip() for t in a.tiers.split(",") if t.strip()]

    prompts_by_id = {}
    for t in tiers:
        for p in load_prompts(t):
            prompts_by_id[p["id"]] = p
    jobs = build_jobs(a.date, tiers, a.all_surfaces, a.cap)
    print(f"[{a.date}] car round: {len(prompts_by_id)}本 × {len({j['s']['id'] for j in jobs})}面 "
          f"= {len(jobs)}呼び出し（上限 ${a.cap:.0f}）")
    responses = collect(jobs)

    got_rate = len(responses) / max(len(jobs), 1)
    by_surface = Counter(r["surface"] for r in responses)
    print(f"  取得 {len(responses)}/{len(jobs)} ({got_rate * 100:.1f}%) 面別 {dict(by_surface)}")
    if got_rate < 0.5:
        (DATA / "car").mkdir(parents=True, exist_ok=True)
        (DATA / "car" / "last_error.txt").write_text(
            f"{a.date} car round 取得率 {got_rate * 100:.1f}%（{len(responses)}/{len(jobs)}）\n"
            f"面別: {dict(by_surface)}\n実費 {llm.spent()}\n\n" + "\n".join(llm.errors()),
            encoding="utf-8")
        sys.exit("取得率が50%を下回ったため、スナップショットは書きません。"
                 "data/car/last_error.txt を確認してください。")

    cells = build_cells(responses, prompts_by_id)
    snap = {
        "date": a.date, "tiers": tiers, "mode": "live",
        "n_prompts": len(prompts_by_id), "n_cells": len(cells),
        "surfaces": dict(by_surface),
        "per_car": summarize(cells),
        "per_local": summarize_local(cells) if "local" in tiers else None,
        "api_cost": llm.spent(),
        "errors": llm.errors()[:20],
        "cells": cells,
    }
    write_json(CAR_DIR / f"{a.date}.json", snap, compact=True)
    print(f"  wrote data/car/snapshots/{a.date}.json  実費 ${snap['api_cost']['usd']:.2f} "
          f"/ {snap['api_cost']['calls']}回")
    for cid, s in snap["per_car"].items():
        print(f"    {cid:<10} 出現 {s['mention_rate']}% / 第一想起 {s['first_rate']}% "
              f"(対象{s['target_cells']}セル)")


if __name__ == "__main__":
    main()
