#!/usr/bin/env python3
"""car/car_bench/local クエリの月間検索ボリュームを DataForSEO で日次更新する。

- 対象: prompts/registry.yaml の tier が car / car_bench / local の keyword（ユニーク）
- 出力: data/search_volumes.json  {"keyword": volume, ...} と updated_at
- 費用: search_volume/live は 700kw/リクエスト。対象 ~350kw = 1リクエスト ≒ $0.05〜0.075/日
- DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD が無ければ何もせず正常終了（既存流儀に合わせる）
- registry.yaml 自体には書き込まない（クエリID・収穫botと競合させない）
"""
import json, os, sys, datetime
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "search_volumes.json")

def main() -> int:
    if not (os.environ.get("DATAFORSEO_LOGIN") and os.environ.get("DATAFORSEO_PASSWORD")):
        print("DATAFORSEO 資格情報なし。search_volumes 更新をスキップ（既存ファイルはそのまま）")
        return 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from harvest import fetch_volumes  # 既存実装を流用（700kw分割・失敗時例外）

    reg = yaml.safe_load(open(os.path.join(ROOT, "prompts", "registry.yaml"), encoding="utf-8"))
    kws = []
    for p in reg.get("prompts", []):
        if p.get("tier") in ("car", "car_bench", "local"):
            kw = (p.get("keyword") or "").strip()
            if kw and kw not in kws:
                kws.append(kw)
    if not kws:
        print("対象keywordなし。スキップ")
        return 0

    old = {}
    if os.path.exists(OUT):
        try: old = json.load(open(OUT)).get("volumes", {})
        except Exception: old = {}
    try:
        vols = fetch_volumes(kws)
    except Exception as e:
        print(f"search_volume 取得失敗（既存値を維持）: {e}")
        return 0  # 日次は落とさない。翌日に再試行
    merged = {**old, **{k: int(v) for k, v in vols.items()}}
    json.dump({"updated_at": datetime.date.today().isoformat(), "n_keywords": len(kws), "volumes": merged},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    got = sum(1 for k in kws if merged.get(k))
    print(f"search_volumes.json 更新: 対象{len(kws)}kw / 値あり{got}kw")
    return 0

if __name__ == "__main__":
    sys.exit(main())
