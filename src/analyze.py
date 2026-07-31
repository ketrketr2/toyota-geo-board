"""回答テキストと引用URLから、ブランド判定・引用分類・スコアを作る。

judge_response  : 1回答 → ブランド別の {mentioned, rank, sentiment}
classify_url    : 1URL   → {platform | owned | affiliated | media | ...}
aggregate       : 全回答 → その日のスナップショット（6因数スコア込み）
"""
from __future__ import annotations

import statistics as st
from collections import Counter, defaultdict

from common import contains_any, domain_of, first_index, load, match_domain, sentences

POSITIVE = ["優れ", "強み", "高い評価", "おすすめ", "安心", "有利", "定評", "信頼",
            "満足", "人気", "充実", "得意", "巧み", "完成度"]
NEGATIVE = ["割高", "高い", "遅れ", "弱い", "課題", "劣る", "不満", "地味", "退屈",
            "分かりにくい", "分かりづらい", "没個性", "つまらない", "注意"]


# ---------------------------------------------------------------- ブランド判定
def judge_response(text: str) -> dict:
    """回答テキストから、各ブランドの言及有無・登場順位・センチメントを取る。

    rank は「文字位置の早い順」で決める。AI回答は推薦順に並ぶ性質を利用する。
    sentiment は当該ブランドを含む文だけを見る（回答全体では薄まるため）。
    """
    br = load("brands")
    targets = [(br["own"]["id"], br["own"]["aliases"])] + \
              [(c["id"], c["aliases"]) for c in br["competitors"]]

    pos = {bid: first_index(text, al) for bid, al in targets}
    present = {b: p for b, p in pos.items() if p is not None}
    order = sorted(present, key=lambda b: present[b])

    sents = sentences(text)
    out = {}
    for bid, aliases in targets:
        if bid not in present:
            out[bid] = {"mentioned": False, "rank": None, "sentiment": None}
            continue
        own_sents = [s for s in sents if contains_any(s, aliases)]
        blob = "。".join(own_sents)
        p = sum(blob.count(w) for w in POSITIVE)
        n = sum(blob.count(w) for w in NEGATIVE)
        sentiment = "positive" if p > n else "negative" if n > p else "neutral"
        out[bid] = {"mentioned": True, "rank": order.index(bid) + 1, "sentiment": sentiment}
    return out


def detect_negative_drivers(text: str) -> list[str]:
    br = load("brands")
    own = br["own"]["aliases"]
    hits = []
    for s in sentences(text):
        if not contains_any(s, own):
            continue
        for nd in br["negative_drivers"]:
            if contains_any(s, nd["keywords"]):
                hits.append(nd["id"])
    return sorted(set(hits))


# ---------------------------------------------------------------- 引用分類
def classify_url(url: str) -> dict:
    """URL を platform / owned / affiliated / media 等に分類する。"""
    pf = load("platforms")
    br = load("brands")
    host = domain_of(url)
    if not host:
        return {"host": "", "bucket": "noise", "platform": None}

    if any(p in url.lower() for p in pf["noise_patterns"]):
        return {"host": host, "bucket": "noise", "platform": None}
    if match_domain(host, br["own"]["owned_domains"]):
        return {"host": host, "bucket": "owned", "platform": None}
    if match_domain(host, br["own"]["affiliated_domains"]):
        return {"host": host, "bucket": "affiliated", "platform": None}
    for p in pf["platforms"]:
        if match_domain(host, p["domains"]):
            return {"host": host, "bucket": "earned", "platform": p["id"]}
    if match_domain(host, pf["reference_domains"]):
        return {"host": host, "bucket": "reference", "platform": None}
    if match_domain(host, pf["press_domains"]):
        return {"host": host, "bucket": "press", "platform": None}
    for c in br["competitors"]:
        if match_domain(host, c["domains"]):
            return {"host": host, "bucket": "competitor", "platform": None}
    return {"host": host, "bucket": "media", "platform": None}


# ---------------------------------------------------------------- 集計
def _median_bool(vals: list[bool]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def aggregate(day: str, responses: list[dict], signals: dict) -> dict:
    """全回答を畳んで、その日のスナップショットを作る。"""
    cfg, br, pf = load("settings"), load("brands"), load("platforms")
    own_id = br["own"]["id"]
    all_brands = [own_id] + [c["id"] for c in br["competitors"]]
    prompt_meta = {p["id"]: p for p in __import__("common").load_prompts("core")}

    # ---- 1) レスポンス単位で判定 ----
    per_run = []
    for r in responses:
        j = judge_response(r["text"])
        cites = [dict(classify_url(c["url"]), url=c["url"], title=c.get("title", ""))
                 for c in r.get("citations", [])]
        per_run.append({**{k: r[k] for k in ("prompt_id", "surface", "run")},
                        "judge": j, "citations": cites, "text": r["text"],
                        "negatives": detect_negative_drivers(r["text"])})

    # ---- 2) プロンプト×サーフェス単位で中央値に畳む（サンプリングノイズ対策）----
    grouped: dict[tuple, list] = defaultdict(list)
    for pr in per_run:
        grouped[(pr["prompt_id"], pr["surface"])].append(pr)

    cells = []
    for (pid, sid), runs in grouped.items():
        cell = {"prompt_id": pid, "surface": sid, "runs": len(runs),
                "category": prompt_meta.get(pid, {}).get("category"),
                "driver": prompt_meta.get(pid, {}).get("driver"),
                "brand_query": prompt_meta.get(pid, {}).get("brand", False),
                "brands": {}, "citations": [], "negatives": []}
        for b in all_brands:
            ms = [x["judge"][b]["mentioned"] for x in runs]
            rk = [x["judge"][b]["rank"] for x in runs if x["judge"][b]["rank"]]
            se = [x["judge"][b]["sentiment"] for x in runs if x["judge"][b]["sentiment"]]
            cell["brands"][b] = {
                "mention_rate": round(_median_bool(ms), 3),
                "mentioned": _median_bool(ms) >= 0.5,
                "rank": int(st.median(rk)) if rk else None,
                "sentiment": Counter(se).most_common(1)[0][0] if se else None,
            }
        seen = set()
        for x in runs:
            for c in x["citations"]:
                if c["url"] not in seen:
                    seen.add(c["url"])
                    cell["citations"].append(c)
            cell["negatives"] += x["negatives"]
        cell["negatives"] = sorted(set(cell["negatives"]))
        # 回答例（run 0 の本文）。UIでクリック表示するため1本だけ保持する。
        cell["answer"] = (runs[0].get("text") or "")[:900]
        cell["own_cited"] = any(c["bucket"] == "owned" for c in cell["citations"])
        cells.append(cell)

    # ---- 3) 因数を計算 ----
    own_cells = [c for c in cells if c["brands"][own_id]["mentioned"]]
    total = len(cells) or 1

    presence = len(own_cells) / total * 100
    ranks = [c["brands"][own_id]["rank"] for c in own_cells if c["brands"][own_id]["rank"]]
    rank_quality = (sum(1 / r for r in ranks) / len(ranks) * 100) if ranks else 0.0
    owned_citation = (sum(c["own_cited"] for c in own_cells) / len(own_cells) * 100) if own_cells else 0.0
    pos = sum(1 for c in own_cells if c["brands"][own_id]["sentiment"] == "positive")
    sentiment = (pos / len(own_cells) * 100) if own_cells else 0.0

    mention_counts = {b: sum(c["brands"][b]["mentioned"] for c in cells) for b in all_brands}
    tot_mentions = sum(mention_counts.values()) or 1
    sov = mention_counts[own_id] / tot_mentions * 100

    # ---- 4) アーンド（SNS/UGC）引用 ★ ----
    plat_cfg = {p["id"]: p for p in pf["platforms"]}
    market_cites = Counter()      # そのプラットフォームが引用された回数（市場全体）
    own_cites = Counter()         # うち自社が言及されていた回答での引用
    for c in cells:
        own_here = c["brands"][own_id]["mentioned"]
        for cit in c["citations"]:
            if cit["bucket"] != "earned":
                continue
            market_cites[cit["platform"]] += 1
            if own_here:
                own_cites[cit["platform"]] += 1

    platforms_out, earned = [], 0.0
    for pid, p in plat_cfg.items():
        m, o = market_cites.get(pid, 0), own_cites.get(pid, 0)
        share = (o / m) if m else 0.0
        earned += p["weight"] * share
        platforms_out.append({
            "id": pid, "label": p["label"], "kind": p["kind"],
            "weight": p["weight"], "actionable": p["actionable"], "note": p["note"],
            "market_citations": m, "own_citations": o,
            "share": round(share * 100, 2),
            "market_reference": p.get("market_citations", 0),
        })
    earned_citation = earned * 100
    platforms_out.sort(key=lambda x: -x["weight"])

    # ---- 5) 総合スコア ----
    w = cfg["score_weights"]
    factors = {
        "presence": presence,
        "rank_quality": rank_quality,
        "owned_citation": owned_citation,
        "earned_citation": earned_citation,
        "sentiment": sentiment,
        "share_of_voice": min(sov / 0.35, 100),   # 5社均等=20%を基準に正規化
    }
    score = sum(factors[k] * w[k] / 100 for k in w)

    # ---- 6) 補助集計 ----
    driver_matrix = defaultdict(lambda: Counter())
    for c in cells:
        if not c["driver"]:
            continue
        for b in all_brands:
            if c["brands"][b]["mentioned"]:
                driver_matrix[c["driver"]][b] += 1

    surface_dist = Counter(c["surface"] for c in cells if c["brands"][own_id]["mentioned"])
    domain_counter = Counter(cit["host"] for c in cells for cit in c["citations"])
    bucket_counter = Counter(cit["bucket"] for c in cells for cit in c["citations"])
    negatives = Counter(n for c in cells for n in c["negatives"])

    return {
        "date": day,
        "score": round(score, 2),
        "factors": {k: round(v, 2) for k, v in factors.items()},
        "weights": w,
        "counts": {"prompts": len({c['prompt_id'] for c in cells}),
                   "cells": len(cells), "responses": len(responses),
                   "own_mentioned": len(own_cells)},
        "brands": {b: {"mentions": mention_counts[b],
                       "sov": round(mention_counts[b] / tot_mentions * 100, 2)}
                   for b in all_brands},
        "platforms": platforms_out,
        "drivers": {d: dict(c) for d, c in driver_matrix.items()},
        "surface_dist": dict(surface_dist),
        "top_domains": domain_counter.most_common(15),
        "citation_buckets": dict(bucket_counter),
        "negative_drivers": dict(negatives),
        "cells": cells,
        "signals": signals,
    }
