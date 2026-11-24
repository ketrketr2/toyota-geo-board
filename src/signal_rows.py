#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世の中マガジン用の定点（事業・商品の行ごと）。

1. 行ごとの言葉の検索回数（Google 広告のデータ、月別つき）を1回で照会
2. 行ごとに1問、メーカー名を入れない質問を4つのAI（AIによる概要・AIモード・ChatGPT・Gemini）に1回ずつ
言葉と質問は triggers/signal-youhin.json から受け取る。費用の上限も同じファイルの cap_usd（この回だけの上限）。
安い面（Google の2つ）を全行ぶん先に取り、その後 ChatGPT、Gemini の順。上限に届いたら止める（取れなかった行は記録に残る）。
"""
from __future__ import annotations

import datetime
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "signal"
LANG, LOC = "ja", 2392
ERRORS: list[dict] = []


def log(*a):
    print(*a, flush=True)


def main():
    trig = json.loads((ROOT / "triggers" / "signal-youhin.json").read_text(encoding="utf-8"))
    cap = float(trig.get("cap_usd") or 2.0)
    tag = trig.get("tag") or "rows"
    kws = [k for k in (trig.get("keywords") or []) if k]
    questions = trig.get("questions") or []          # [{"row": "r3", "text": "…"}]
    surfaces = trig.get("surfaces") or ["aimode", "aio", "chatgpt", "gemini"]
    out: dict = {"at_utc": datetime.datetime.utcnow().isoformat(), "tag": tag, "cap_usd": cap,
                 "n_keywords": len(kws), "n_questions": len(questions), "stages": []}

    def save():
        out["spent"] = llm.spent()
        out["errors"] = ERRORS
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"rows_{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    def over() -> bool:
        return llm.spent()["usd"] >= cap

    # 1. 検索回数
    vols = {}
    try:
        for i in range(0, len(kws), 700):
            task = llm._post("keywords_data/google_ads/search_volume/live",
                             [{"keywords": kws[i:i + 700], "language_code": LANG, "location_code": LOC}])
            for it in task.get("result") or []:
                vols[it["keyword"]] = {"volume": it.get("search_volume"),
                                       "monthly": [{"y": m.get("year"), "m": m.get("month"), "v": m.get("search_volume")}
                                                   for m in (it.get("monthly_searches") or [])]}
    except Exception as e:  # noqa: BLE001
        ERRORS.append({"stage": "volumes", "error": f"{type(e).__name__}: {str(e)[:300]}"})
    out["volumes"] = vols
    out["stages"].append({"stage": "volumes", "usd": llm.spent()["usd"], "got": len(vols)})
    log(f"volumes {len(vols)}/{len(kws)} ${llm.spent()['usd']:.3f}")
    save()

    # 2. AIの答え
    cfg = {s["id"]: s for s in (llm.load("settings").get("surfaces") or [])}
    chat = dict(cfg.get("chatgpt") or {"id": "chatgpt", "model": "gpt-5", "max_tokens": 4096})
    gem = dict(cfg.get("gemini") or {"id": "gemini", "model": "gemini-2.5-flash"})
    surf_def = {"aimode": {"id": "aimode", "provider": "serp"}, "aio": {"id": "aio", "provider": "serp"},
                "chatgpt": chat, "gemini": gem}
    answers: list[dict] = []
    out["answers"] = answers

    def one(job):
        row, text, sid = job
        if over():
            ERRORS.append({"stage": "answer", "job": f"{row}/{sid}", "error": f"budget cap ${cap} reached"})
            return None
        for a in range(3):
            try:
                if sid in ("aimode", "aio"):
                    res = llm.fetch_serp_ai(text, surf_def[sid])
                else:
                    res = llm.fetch_llm_response(text, surf_def[sid])
                return {"row": row, "question": text, "surface": sid,
                        "text": (res.get("text") or "")[:9000],
                        "citations": [{"url": c.get("url"), "domain": c.get("domain"), "title": (c.get("title") or "")[:100]}
                                      for c in (res.get("citations") or [])],
                        "fanout": res.get("fanout") or []}
            except Exception as e:  # noqa: BLE001
                if a == 2:
                    ERRORS.append({"stage": "answer", "job": f"{row}/{sid}", "error": str(e)[:300]})
                time.sleep(3 * (a + 1))
        return None

    for sid in surfaces:                               # 面ごとに順に（安い面から）。面の中は並列
        if sid not in surf_def:
            continue
        before = llm.spent()["usd"]
        jobs = [(q["row"], q["text"], sid) for q in questions]
        workers = 6 if sid in ("aimode", "aio") else 3  # 高い面は並列を絞り、上限の行き過ぎを小さくする
        got = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(one, j) for j in jobs]
            for f in as_completed(futs):
                try:
                    r = f.result()
                except Exception as e:  # noqa: BLE001
                    ERRORS.append({"stage": "pool", "error": f"{type(e).__name__}: {str(e)[:200]}"})
                    r = None
                if r is not None:
                    answers.append(r)
                    got += 1
        out["stages"].append({"stage": sid, "usd": round(llm.spent()["usd"] - before, 4), "got": got, "asked": len(jobs)})
        log(f"{sid} {got}/{len(jobs)} +${llm.spent()['usd'] - before:.3f} total ${llm.spent()['usd']:.3f}")
        save()
    save()
    print(json.dumps({"stages": out["stages"], "spent": out["spent"], "errors": len(ERRORS)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
