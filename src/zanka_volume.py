#!/usr/bin/env python3
"""残価の検索語（SERP計測24語＋主要語）の月間検索ボリュームを取り、data/zanka/volumes_<day>.json に置く。"""
import json, os, sys, datetime
import yaml
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from harvest import fetch_volumes

def main():
    if not (os.environ.get("DATAFORSEO_LOGIN") and os.environ.get("DATAFORSEO_PASSWORD")):
        sys.exit("DATAFORSEO 資格情報なし")
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config", "zanka_extra.yaml"), encoding="utf-8"))
    kws = list(cfg.get("serp_keywords", []))
    extra = ["残クレ", "残価設定", "残価設定型クレジット", "残価設定ローン", "残価設定型プラン", "残クレ とは", "残クレ 金利",
             "アルファード", "アルファード 価格", "アルファード 新車 価格", "アルファード 見積もり", "アルファード 値引き", "アルファード 月々",
             "ヴェルファイア", "ヴェルファイア 価格", "KINTO", "KINTO アルファード", "マイカーローン", "マイカーローン 金利", "カーリース",
             "トヨタ 残価設定", "トヨタ 残クレ", "残クレ 審査", "残クレ 3年後", "残クレ 損", "残クレ メリット", "残クレ 乗り換え", "残クレ 一括返済"]
    for k in extra:
        if k not in kws:
            kws.append(k)
    vols = fetch_volumes(kws)
    out = {"generated_at": datetime.datetime.now().isoformat(), "n": len(kws), "volumes": {k: int(vols.get(k) or 0) for k in kws}}
    os.makedirs(os.path.join(ROOT, "data", "zanka"), exist_ok=True)
    path = os.path.join(ROOT, "data", "zanka", f"volumes_{datetime.date.today().isoformat()}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved", path, sum(out["volumes"].values()))

if __name__ == "__main__":
    main()
