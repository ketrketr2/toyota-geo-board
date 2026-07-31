"""スナップショット群 → ダッシュボードが読む docs/data/latest.json を作る。

フロントは静的HTML1枚。JSONを差し替えるだけで表示が変わる構造にしてある。
"""
from __future__ import annotations

from collections import Counter, defaultdict

from common import (DOCS, days_ago, list_snapshots, load, read_json,
                    snapshot_path, write_json)

HISTORY_DAYS = 60


def _short_path(url: str) -> str:
    """表示用にパスだけを取り出す（長い場合は省略）。"""
    from urllib.parse import urlparse
    u = urlparse(url)
    p = (u.path or "/") + (("?" + u.query) if u.query else "")
    return p if len(p) <= 46 else p[:43] + "…"


def build_site(day: str) -> None:
    snap = read_json(snapshot_path(day))
    if not snap:
        raise SystemExit(f"snapshot not found: {day}")

    cfg, br, pf = load("settings"), load("brands"), load("platforms")
    own = br["own"]["id"]
    labels = {br["own"]["id"]: br["own"]["label"], **{c["id"]: c["label"] for c in br["competitors"]}}
    drivers = {d["id"]: d for d in br["drivers"]}
    surfaces = {s["id"]: s for s in cfg["surfaces"]}

    # ---- 時系列 ----
    hist = []
    for d in [days_ago(day, i) for i in range(HISTORY_DAYS - 1, -1, -1)]:
        s = read_json(snapshot_path(d))
        if not s:
            continue
        hist.append({
            "date": d, "score": s["score"], **s["factors"],
            "ai_sessions": sum(s["signals"]["ga4_ai_sessions"].values()),
            "crawler_hits": sum(s["signals"]["crawler_hits"].values()),
            "sov_own": s["brands"][own]["sov"],
        })

    # ---- クエリ表（プロンプト単位に畳む）----
    prompts = {p["id"]: p for p in __import__("common").load_prompts("core")}
    surface_label = {k: v.get("label", k) for k, v in surfaces.items()}
    rows = {}
    for c in snap["cells"]:
        r = rows.setdefault(c["prompt_id"], {
            "id": c["prompt_id"], "text": prompts.get(c["prompt_id"], {}).get("text", ""),
            "category": c["category"], "driver": c["driver"],
            "driver_label": drivers.get(c["driver"], {}).get("label", c["driver"]),
            "brand_query": c["brand_query"], "surfaces": {},
            "source": prompts.get(c["prompt_id"], {}).get("source", "-"),
            "own_cited": False, "platforms": set(), "negatives": set(),
            "_cites": {},          # url -> {…, surfaces:set}
        })
        r["surfaces"][c["surface"]] = {
            "answer": c.get("answer", ""),
            "rank": c["brands"][own]["rank"],
            "mention_rate": c["brands"][own]["mention_rate"],
            "sentiment": c["brands"][own]["sentiment"],
            "competitors": {b: c["brands"][b]["rank"] for b in labels if b != own},
        }
        r["own_cited"] = r["own_cited"] or c["own_cited"]
        r["platforms"] |= {x["platform"] for x in c["citations"] if x["platform"]}
        r["negatives"] |= set(c["negatives"])
        for cit in c["citations"]:
            e = r["_cites"].setdefault(cit["url"], {
                "url": cit["url"], "title": cit.get("title", ""), "host": cit["host"],
                "bucket": cit["bucket"], "platform": cit["platform"], "surfaces": set()})
            e["surfaces"].add(c["surface"])

    BUCKET_ORDER = {"owned": 0, "affiliated": 1, "earned": 2, "media": 3,
                    "reference": 4, "press": 5, "competitor": 6, "noise": 7}
    owned_index: dict[str, dict] = {}     # 自社ページ → どのクエリで引かれたか（逆引き）

    query_rows = []
    for r in rows.values():
        rks = [v["rank"] for v in r["surfaces"].values() if v["rank"]]
        cites = sorted(r.pop("_cites").values(),
                       key=lambda x: (BUCKET_ORDER.get(x["bucket"], 9), -len(x["surfaces"])))
        for c in cites:
            c["surfaces"] = sorted(c["surfaces"])
            c["surface_labels"] = [surface_label.get(s, s) for s in c["surfaces"]]
            c["path"] = _short_path(c["url"])
        own_pages = [c for c in cites if c["bucket"] == "owned"]
        aff_pages = [c for c in cites if c["bucket"] == "affiliated"]

        for c in own_pages:                                    # 逆引きインデックスを作る
            e = owned_index.setdefault(c["url"], {
                "url": c["url"], "title": c["title"], "path": c["path"],
                "queries": [], "surfaces": set(), "n": 0})
            e["queries"].append({"id": r["id"], "text": r["text"],
                                 "category": r["category"],
                                 "rank": min(rks) if rks else None,
                                 "surfaces": c["surface_labels"]})
            e["surfaces"] |= set(c["surface_labels"])
            e["n"] += len(c["surfaces"])

        query_rows.append({**r,
                           "platforms": sorted(r["platforms"]),
                           "negatives": sorted(r["negatives"]),
                           "citations": cites,
                           "own_pages": own_pages,
                           "affiliated_pages": aff_pages,
                           "n_citations": len(cites),
                           "best_rank": min(rks) if rks else None,
                           "avg_rank": round(sum(rks) / len(rks), 1) if rks else None,
                           "coverage": round(len(rks) / max(len(r["surfaces"]), 1) * 100)})
    query_rows.sort(key=lambda x: (x["best_rank"] is None, x["best_rank"] or 99))

    # ---- クエリ単位の前日比 / 先週比（順位と自社引用の変化）----
    from common import days_ago as _da
    prev_maps = {}
    for key, n in (("dod", 1), ("wow", 7)):
        ps = read_json(snapshot_path(_da(day, n)))
        if not ps:
            continue
        m = {}
        for c in ps["cells"]:
            e = m.setdefault(c["prompt_id"], {"ranks": [], "own_cited": False})
            if c["brands"][own]["rank"]:
                e["ranks"].append(c["brands"][own]["rank"])
            e["own_cited"] = e["own_cited"] or c["own_cited"]
        prev_maps[key] = m
    for r in query_rows:
        r["trend"] = {}
        for key, m in prev_maps.items():
            q = m.get(r["id"])
            if not q:
                r["trend"][key] = None
                continue
            pb = min(q["ranks"]) if q["ranks"] else None
            r["trend"][key] = {
                "prev_best": pb,
                "rank_delta": (pb - r["best_rank"]) if (pb and r["best_rank"]) else None,
                "was_cited": q["own_cited"],
                "cite_change": ("gained" if r["own_cited"] and not q["own_cited"]
                                else "lost" if q["own_cited"] and not r["own_cited"] else "same"),
            }

    # ---- カテゴリ別の傾向（概要用）----
    # 出現率は「プロンプト×サーフェス」のセル単位で数える。
    # プロンプト単位だと4サーフェスのどれかに出れば100%になり、飽和して意味を失うため。
    def _cat_stats(cells):
        agg = defaultdict(lambda: {"cells": 0, "mentioned": 0, "ranks": [], "cited": 0})
        for c in cells:
            a = agg[c["category"]]
            a["cells"] += 1
            if c["brands"][own]["mentioned"]:
                a["mentioned"] += 1
                if c["brands"][own]["rank"]:
                    a["ranks"].append(c["brands"][own]["rank"])
            if c["own_cited"]:
                a["cited"] += 1
        return agg

    now_agg = _cat_stats(snap["cells"])
    ps7 = read_json(snapshot_path(_da(day, 7)))
    prev_agg = _cat_stats(ps7["cells"]) if ps7 else {}
    qcount = Counter(r["category"] for r in query_rows)

    CATJ = {"purchase": "購入検討", "model": "車種・スペック", "safety": "安全・運転支援",
            "cost": "価格・維持費", "eco": "燃費・電動化", "service": "整備・アフター",
            "brand": "ブランド指名"}
    category_trend = []
    for cid, a in now_agg.items():
        rate = a["mentioned"] / a["cells"] * 100 if a["cells"] else 0
        pv = prev_agg.get(cid)
        prate = (pv["mentioned"] / pv["cells"] * 100) if pv and pv["cells"] else None
        category_trend.append({
            "id": cid, "label": CATJ.get(cid, cid), "queries": qcount.get(cid, 0),
            "presence": round(rate, 1),
            "presence_prev": round(prate, 1) if prate is not None else None,
            "delta": round(rate - prate, 1) if prate is not None else None,
            "cited": a["cited"], "cells": a["cells"],
            "cite_rate": round(a["cited"] / a["cells"] * 100, 1) if a["cells"] else 0,
            "avg_rank": round(sum(a["ranks"]) / len(a["ranks"]), 2) if a["ranks"] else None,
        })
    category_trend.sort(key=lambda x: -x["presence"])

    # ---- クエリの出所内訳 ----
    SRCJ = {"chiebukuro": "Yahoo!知恵袋の実質問", "paa": "Google「関連する質問」",
            "llm_fanout": "LLMによるfan-out生成", "semrush_import": "SEOトピック輸入",
            "gsc": "GSC実クエリ", "-": "未設定"}
    src_counter = Counter(r["source"] for r in query_rows)
    query_sources = [{"id": k, "label": SRCJ.get(k, k), "n": v}
                     for k, v in src_counter.most_common()]

    owned_pages = sorted(
        ({**v, "surfaces": sorted(v["surfaces"]), "n_queries": len(v["queries"])}
         for v in owned_index.values()),
        key=lambda x: (-x["n"], -x["n_queries"]))

    # ---- 判断軸マトリクス ----
    driver_rows = []
    for did, meta in drivers.items():
        counts = snap["drivers"].get(did, {})
        mine = counts.get(own, 0)
        top = max(((b, n) for b, n in counts.items()), key=lambda x: x[1], default=(None, 0))
        driver_rows.append({"id": did, "label": meta["label"], "priority": meta["priority"],
                            "counts": {labels.get(b, b): n for b, n in counts.items()},
                            "own": mine, "leader": labels.get(top[0], "—"), "leader_n": top[1],
                            "gap": top[1] - mine})
    driver_rows.sort(key=lambda x: (-{"high": 2, "mid": 1, "low": 0}[x["priority"]], -x["gap"]))

    out = {
        "generated_at": day,
        "site": cfg["site"],
        "score": snap["score"],
        "factors": snap["factors"],
        "weights": cfg["score_weights"],
        "counts": snap["counts"],
        "diff": snap.get("diff", {}),
        "comment": snap.get("comment", {}),
        "brands": [{"id": b, "label": labels[b], **v} for b, v in snap["brands"].items()],
        "platforms": snap["platforms"],
        "surfaces": [{"id": k, "label": surfaces.get(k, {}).get("label", k), "count": v}
                     for k, v in snap["surface_dist"].items()],
        "top_domains": [{"host": h, "n": n} for h, n in snap["top_domains"]],
        "citation_buckets": snap["citation_buckets"],
        "negative_drivers": snap["negative_drivers"],
        "drivers": driver_rows,
        "queries": query_rows,
        "owned_pages": owned_pages,
        "category_trend": category_trend,
        "query_sources": query_sources,
        "prompt_tiers": cfg["sampling"]["tier_schedule"],
        "signals": snap["signals"],
        "history": hist,
        "available_days": len(list_snapshots()),
    }
    write_json(DOCS / "data" / "latest.json", out)
    print(f"  wrote docs/data/latest.json ({len(hist)} days of history)")
