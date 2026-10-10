#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""残価・残クレの検索面（Google）の現状把握：AIによる概要（AIO）の出現・引用元・内容、自然検索の上位、関連する質問。

  ① 主要語（volumes_*.json の全語＋追加語）× desktop/mobile の自然検索（AIO 非同期ロード）
  ② 「残クレ」「残価設定」系のキーワード候補（DataForSEO Labs keyword_suggestions, SERP item types 付き）
     → AIO が出る語の割合を、数百語の規模で見る
結果は data/zanka/aio_<day>.json に生で置く。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import llm  # noqa: E402
from analyze import classify_url  # noqa: E402
from harvest import fetch_volumes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "zanka"
EXTRA = ["残クレ 仕組み", "残クレ 頭金", "残クレ ボーナス払い", "残クレ 5年", "残クレ 月々", "残クレ 返却", "残クレ 買取",
         "残クレ 残価率", "残クレ 乗り換え 時期", "残クレ 途中解約", "残クレ 事故", "残クレ 走行距離 超過",
         "アルファード 残クレ 5年", "アルファード 残クレ 3年", "アルファード 残クレ 頭金なし", "ヴェルファイア 残価設定",
         "ヴェルファイア 残クレ 月々", "トヨタ 残価設定 シミュレーション", "トヨタ 残価設定型プラン 金利", "トヨタ 残クレ 金利",
         "残価設定型プラン 金利", "残価設定ローン 金利", "残価設定ローン 仕組み", "残クレ 恥ずかしい", "残クレ 貧乏", "残クレ 後悔"]
SEEDS = ["残クレ", "残価設定", "残価設定ローン", "残価設定型クレジット"]


def latest_volumes() -> dict:
    files = sorted(OUT.glob("volumes_*.json"))
    if not files:
        return {}
    return json.load(open(files[-1], encoding="utf-8")).get("volumes") or {}


def serp(keyword: str, device: str) -> dict:
    body = [{"keyword": keyword, "language_code": "ja", "location_code": 2392, "device": device,
             "load_async_ai_overview": True, "depth": 20}]
    task = llm._post("serp/google/organic/live/advanced", body)
    res = (task.get("result") or [{}])[0]
    items = res.get("items") or []
    organic, aio, paa, types = [], None, [], Counter()
    for it in items:
        t = str(it.get("type", ""))
        types[t] += 1
        if t == "organic":
            organic.append({"rank": it.get("rank_absolute"), "url": it.get("url"), "domain": it.get("domain"),
                            "title": (it.get("title") or "")[:120]})
        elif t.startswith("ai_overview"):
            cites = []
            llm._walk_refs(it.get("references"), cites)
            llm._walk_refs(it.get("items"), cites)
            text = ""
            for el in (it.get("items") or [it]):
                text += (el.get("text") or "") + "\n"
            cl = [classify_url(c.get("url") or "") | {"url": c.get("url"), "title": (c.get("title") or "")[:100]} for c in llm._dedup(cites)]
            aio = {"text": text.strip()[:4000], "citations": cl, "n_items": len(it.get("items") or []),
                   "mentions_toyota": ("トヨタ" in text) or ("TOYOTA" in text.upper()),
                   "has_yen": "円" in text, "has_rate": ("%" in text) or ("％" in text)}
        elif t == "people_also_ask":
            for el in it.get("items") or []:
                paa.append(el.get("title") or "")
    return {"keyword": keyword, "device": device, "organic": organic[:20], "aio": aio, "paa": paa,
            "types": dict(types), "item_types": res.get("item_types"), "se_results_count": res.get("se_results_count"),
            "toyota_rank": next((o["rank"] for o in organic if "toyota.jp" in (o.get("domain") or "")), None),
            "toyota_url": next((o["url"] for o in organic if "toyota.jp" in (o.get("domain") or "")), None),
            "cost": llm.last_cost(task)}


def suggestions(seed: str, limit: int = 300) -> list[dict]:
    body = [{"keyword": seed, "language_code": "ja", "location_code": 2392, "include_serp_info": True,
             "include_seed_keyword": True, "limit": limit, "order_by": ["keyword_info.search_volume,desc"]}]
    task = llm._post("dataforseo_labs/google/keyword_suggestions/live", body)
    res = (task.get("result") or [{}])[0]
    out = []
    for it in res.get("items") or []:
        ki = it.get("keyword_info") or {}
        si = it.get("serp_info") or {}
        out.append({"keyword": it.get("keyword"), "volume": ki.get("search_volume"), "cpc": ki.get("cpc"),
                    "competition": ki.get("competition_level"), "serp_item_types": si.get("serp_item_types"),
                    "se_results_count": si.get("se_results_count"), "serp_updated": si.get("last_updated_time"),
                    "monthly": [{"y": m.get("year"), "m": m.get("month"), "v": m.get("search_volume")} for m in (ki.get("monthly_searches") or [])]})
    return out


def main():
    day = datetime.date.today().isoformat()
    vols = latest_volumes()
    kws = [k for k, _ in sorted(vols.items(), key=lambda t: -(t[1] or 0))]
    for k in EXTRA:
        if k not in kws:
            kws.append(k)
    print(f"keywords: {len(kws)}")
    # 追加語のボリューム
    try:
        more = fetch_volumes([k for k in EXTRA if k not in vols])
        vols.update({k: int(v or 0) for k, v in more.items()})
    except Exception as e:
        print("volumes NG", e, file=sys.stderr)
    serps, errs = [], []
    for i, k in enumerate(kws):
        for dev in ("desktop", "mobile"):
            try:
                serps.append(serp(k, dev))
            except Exception as e:
                errs.append({"keyword": k, "device": dev, "error": str(e)[:200]})
                time.sleep(2)
        if (i + 1) % 10 == 0:
            print(f"  … {i+1}/{len(kws)} ${llm.spent()['usd']:.2f}", flush=True)
    sugg = {}
    for s in SEEDS:
        try:
            sugg[s] = suggestions(s)
            print(f"  suggestions {s}: {len(sugg[s])}")
        except Exception as e:
            errs.append({"seed": s, "error": str(e)[:200]})
    out = {"generated_at": datetime.datetime.now().isoformat(), "n_keywords": len(kws), "volumes": vols,
           "serps": serps, "suggestions": sugg, "errors": errs, "spent": llm.spent()}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"aio_{day}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", path, "serps", len(serps), "errors", len(errs), "spent", llm.spent())


if __name__ == "__main__":
    main()
