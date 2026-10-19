#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中古車U（トヨタ認定中古車・第三者発信）の事前計測。

・config/ucar_prompts.yaml の質問（重点34・比較用30・トヨタ指名20＝84本）を4つのAI面に投げる。
    AIによる概要（AIO）・AIモード … 検索語（kw）
    ChatGPT・Gemini            … 会話文（chat）
・AIO は同じ呼び出しで自然検索の上位20件も保存する（toyota.jp・GAZOO・販売店の順位を見るため）。
・検索語の月間検索数（Google Ads・日本）を1日1回取る。
・1回の計測で、4つのAIそれぞれに --runs 回ずつ投げる（10/13の設計：64問×4つのAI×3回）。
・会話型AIのモデルは、DataForSEO が受け付けるモデル一覧から最新を選ぶ（失敗したら設定値に戻す）。
結果は data/ucar/ に置く。メインボードの時系列には一切触らない。

  python src/run_ucar.py --cap 30 --runs 3
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402
import yaml  # noqa: E402

from common import DATA, JST, ROOT, demo_mode, load, today, write_json  # noqa: E402
from collect import llm  # noqa: E402

OUT = DATA / "ucar"


# ---------------------------------------------------------------- 質問
def ucar_prompts() -> list[dict]:
    with open(ROOT / "config" / "ucar_prompts.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["prompts"]


# ---------------------------------------------------------------- モデル
_VER = re.compile(r"(\d+(?:\.\d+)?)")


def _names(node, out: list) -> None:
    if isinstance(node, dict):
        for k in ("model_name", "name"):
            v = node.get(k)
            if isinstance(v, str):
                out.append(v)
        for v in node.values():
            _names(v, out)
    elif isinstance(node, list):
        for v in node:
            _names(v, out)


def list_models(path: str) -> list[str]:
    """DataForSEO が受け付けるモデル名の一覧（取れなければ空）。"""
    try:
        r = requests.get(f"{llm.DFS_BASE}/ai_optimization/{path}/llm_responses/models",
                         auth=llm._dfs_auth(), timeout=60)
        js = r.json()
        names: list[str] = []
        _names((js.get("tasks") or [{}])[0].get("result"), names)
        return sorted(set(names))
    except Exception as e:  # noqa: BLE001
        print(f"  ! モデル一覧 {path}: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def _version(name: str) -> float:
    m = _VER.search(name)
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0


def pick_latest(names: list[str], family: str, default: str) -> str:
    """一般向けの既定モデルに近いものを選ぶ。
    ChatGPT … gpt-<版> の素の名前（mini / nano / pro / codex / audio / search 付きは除く）
    Gemini  … gemini-<版>-flash（lite・試験版は除く。無ければ pro）
    """
    bad = ("mini", "nano", "codex", "audio", "search", "realtime", "lite", "exp", "image", "tts", "embedding")
    if family == "chatgpt":
        # 2026-10 時点の ChatGPT は GPT-6（無料・Go＝Luna、有料＝Sol）。名前に luna / sol が付く版も拾う
        cand = [n for n in names if re.fullmatch(r"gpt-\d+(?:\.\d+)?(?:-(?:luna|sol))?(?:-\d{4}-\d{2}-\d{2})?", n)]
        cand = [n for n in cand if not any(b in n for b in bad + ("astra", "pro", "thinking"))]
        if not cand:
            return default
        # いちばん新しい版。同じ版なら、利用者がいちばん多い無料版の Luna → 素の名前 → Sol の順
        pref = lambda n: (0 if "luna" in n else 1 if "sol" not in n else 2)  # noqa: E731
        best = max(cand, key=lambda n: (_version(n), -pref(n), -len(n)))
        return best if _version(best) >= _version(default) else default
    else:
        cand = [n for n in names if re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash(?:-preview)?(?:-\d+-\d+)?", n)]
        if not cand:
            cand = [n for n in names if re.fullmatch(r"gemini-\d+(?:\.\d+)?-pro(?:-preview)?(?:-\d+-\d+)?", n)]
    # 「gemini」自体が mini を含むので、判定は家名を外した残りで行う
    cand = [n for n in cand if not any(b in n.replace("gemini", "") for b in bad)]
    if not cand:
        return default
    # いちばん新しい版を選ぶ。同じ版なら正式版（preview でない方）を優先する
    best = max(cand, key=lambda n: (_version(n), "preview" not in n, -len(n)))
    return best if _version(best) >= _version(default) else default


# ---------------------------------------------------------------- 取得
def serp_aio(keyword: str) -> dict:
    """自然検索（上位20件）とAIによる概要を1回で取る。"""
    body = [{"keyword": keyword[:700], "language_code": "ja", "location_code": 2392,
             "device": "desktop", "load_async_ai_overview": True, "depth": 20}]
    task = llm._post("serp/google/organic/live/advanced", body)
    res = (task.get("result") or [{}])[0]
    items = res.get("items") or []
    text, cites, organic, paa, types = "", [], [], [], Counter()
    for it in items:
        t = str(it.get("type", ""))
        types[t] += 1
        if t.startswith("ai_"):
            for el in (it.get("items") or [it]):
                text += (el.get("text") or "") + "\n"
            llm._walk_refs(it.get("references"), cites)
            llm._walk_refs(it.get("items"), cites)
        elif t == "organic":
            organic.append({"rank": it.get("rank_absolute"), "url": it.get("url"),
                            "domain": it.get("domain"), "title": (it.get("title") or "")[:120]})
        elif t == "people_also_ask":
            for el in it.get("items") or []:
                paa.append(el.get("title") or "")
    return {"text": text.strip(), "citations": llm._dedup(cites), "fanout": [],
            "has_ai": any(k.startswith("ai_") for k in types), "organic": organic[:20],
            "paa": paa[:8], "types": dict(types)}


_FALLBACK: dict[str, str] = {}   # 新しいモデル名で弾かれた面は、以後ずっと設定値で投げる

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"}


def _resolve_one(u: str) -> str:
    """Gemini の中継URL（vertexaisearch …/grounding-api-redirect/…）を実URLにする。失敗は ""。"""
    cur = u
    for _ in range(3):
        try:
            r = requests.get(cur, headers=_UA, allow_redirects=False, timeout=15)
        except Exception:  # noqa: BLE001
            return ""
        loc = r.headers.get("Location") or r.headers.get("location") or ""
        if r.status_code in (301, 302, 303, 307, 308) and loc:
            if "vertexaisearch" in loc or "grounding-api-redirect" in loc or "google.com/goto" in loc:
                cur = loc
                continue
            return loc
        return ""
    return ""


def resolve_redirects(rows: list[dict]) -> dict:
    """中継URLは記事単位の判定（どの記事が引用されたか）に要るため、計測の直後に解決しておく。
    元のURLは redirect に残す。中継URLのトークンは時間がたつと切れるので、後回しにしない。"""
    urls = sorted({c["url"] for r in rows for c in (r.get("citations") or [])
                   if any(x in (c.get("url") or "") for x in ("vertexaisearch", "grounding-api-redirect"))})
    got: dict[str, str] = {}
    if urls:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for u, loc in zip(urls, ex.map(_resolve_one, urls)):
                if loc:
                    got[u] = loc
        for r in rows:
            for c in r.get("citations") or []:
                real = got.get(c.get("url") or "")
                if real:
                    c["redirect"] = c["url"]
                    c["url"] = real
    return {"redirects": len(urls), "resolved": len(got)}


def run_one(job: dict) -> dict | None:
    p, s = job["p"], job["s"]
    if llm._over_budget(job["cap"]):
        return None
    if s["id"] in _FALLBACK:
        s = dict(s, model=_FALLBACK[s["id"]])
    query = p["kw"] if s["provider"] == "serp" else p["chat"]
    for attempt in range(3):
        try:
            if s["id"] == "aio":
                res = serp_aio(query)
            elif s["provider"] == "serp":
                res = llm.fetch_serp_ai(query, s)
                res["has_ai"] = bool((res.get("text") or "").strip())
            else:
                res = llm.fetch_llm_response(query, s)
            return {"stamp": job["stamp"], "date": job["day"], "qid": p["id"], "group": p["group"],
                    "named": bool(p.get("named")), "surface": s["id"], "model": s.get("model"),
                    "run": job["run"], "query": query, **res}
        except Exception as e:  # noqa: BLE001
            msg = f"{p['id']}/{s['id']} run{job['run']}: {type(e).__name__}: {e}"
            # 新しいモデル名で弾かれたら設定値に戻してやり直す
            if s.get("model_default") and s.get("model") != s["model_default"] and "model" in str(e).lower():
                print(f"  ! {msg} → {s['model_default']} に戻す", file=sys.stderr)
                _FALLBACK[s["id"]] = s["model_default"]
                s = dict(s, model=s["model_default"])
                continue
            if attempt == 2:
                print(f"  ! {msg}", file=sys.stderr)
                with llm._COST_LOCK:
                    if len(llm._ERRORS) < 60:
                        llm._ERRORS.append(msg)
                return None
            import time
            time.sleep(2 ** attempt * 1.5)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=30.0, help="この1回の支払上限（USD）")
    ap.add_argument("--runs", type=int, default=3, help="会話型AIの回数（ChatGPT・Gemini）")
    ap.add_argument("--serp-runs", type=int, default=None, help="AIO・AIモードの回数（既定は --runs と同じ）")
    ap.add_argument("--no-llm", action="store_true", help="ChatGPT・Gemini を飛ばす")
    ap.add_argument("--no-volume", action="store_true")
    ap.add_argument("--only", default="", help="カンマ区切りの質問ID（試し打ち用）")
    a = ap.parse_args()
    if demo_mode():
        sys.exit("run_ucar はデモ実行を提供しません。GEO_BOARD_MODE=live と DataForSEO 認証を設定してください。")

    day = today()
    stamp = datetime.now(JST).strftime("%Y-%m-%dT%H%M")
    OUT.mkdir(parents=True, exist_ok=True)
    prompts = ucar_prompts()
    if a.only:
        keep = set(a.only.split(","))
        prompts = [p for p in prompts if p["id"] in keep]
    print(f"質問 {len(prompts)} 本（{dict(Counter(p['group'] for p in prompts))}）", flush=True)

    # 会話型AIのモデルを決める
    cfg = load("settings")
    surfaces = []
    models_seen = {}
    for s in cfg["surfaces"]:
        if s["id"] not in ("aio", "aimode", "chatgpt", "gemini"):
            continue
        s = dict(s)
        if s["provider"] != "serp":
            if a.no_llm:
                continue
            path = llm.LLM_PATH.get(s["id"])
            names = list_models(path)
            models_seen[s["id"]] = names
            s["model_default"] = s["model"]
            s["model"] = pick_latest(names, s["id"], s["model"])
        surfaces.append(s)
    print("AI面: " + ", ".join(f"{s['id']}({s.get('model', 'SERP')})" for s in surfaces), flush=True)
    write_json(OUT / f"models_{day}.json", {"stamp": stamp, "available": models_seen,
                                            "used": {s["id"]: s.get("model") for s in surfaces}})

    # 検索数（1日1回）
    vol_path = OUT / f"volumes_{day}.json"
    if not a.no_volume and not vol_path.exists():
        try:
            from harvest import fetch_volumes
            kws = sorted({p["kw"] for p in ucar_prompts()})
            vols = fetch_volumes(kws)
            write_json(vol_path, {"generated_at": stamp, "n": len(kws),
                                  "volumes": {k: int(vols.get(k) or 0) for k in kws}})
            print(f"検索数 {len(kws)} 語", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  ! 検索数: {type(e).__name__}: {e}", file=sys.stderr)

    jobs = []
    for p in prompts:
        for s in surfaces:
            n = a.runs if s["provider"] != "serp" else (a.serp_runs if a.serp_runs is not None else a.runs)
            for run in range(n):
                jobs.append({"day": day, "stamp": stamp, "p": p, "s": s, "run": run, "cap": a.cap})
    print(f"ジョブ {len(jobs)} 件（上限 ${a.cap}）", flush=True)

    out, done = [], 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(run_one, j) for j in jobs]
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            if r:
                out.append(r)
            if done % 50 == 0:
                print(f"  … {done}/{len(jobs)} 完了（成功 {len(out)} / ${llm.spent()['usd']:.2f}）", flush=True)
    out.sort(key=lambda r: (r["qid"], r["surface"], r["run"]))
    try:
        rs = resolve_redirects(out)
        print(f"中継URL {rs['redirects']} 件 → 解決 {rs['resolved']} 件", flush=True)
    except Exception as e:  # noqa: BLE001
        rs = {"error": f"{type(e).__name__}: {e}"}
        print(f"  ! 中継URLの解決: {rs['error']}", file=sys.stderr)
    write_json(OUT / f"responses_{stamp}.json", out, compact=True)

    by = Counter((r["surface"]) for r in out)
    write_json(OUT / f"summary_{stamp}.json", {
        "stamp": stamp, "day": day, "n_prompts": len(prompts), "n_jobs": len(jobs), "n_responses": len(out),
        "by_surface": dict(by), "runs_llm": a.runs, "spent_usd": llm.spent().get("usd"),
        "calls": llm.spent().get("calls"), "errors": llm.errors(), "redirects": rs,
        "models": {s["id"]: s.get("model") for s in surfaces}})
    print(f"完了: 回答 {len(out)}/{len(jobs)} 件、${llm.spent().get('usd', 0):.2f}")


if __name__ == "__main__":
    main()
