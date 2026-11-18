#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世の中モニタリングレポート用の小さな追加照会：残高（無料）と、サービス名の検索量（1回）。"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

from collect import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "signal"
NAMES = ["トヨタ アップグレードファクトリー", "アップグレードファクトリー", "トヨタアップグレードファクトリー",
         "TOYOTA UPGRADE FACTORY", "UPGRADE FACTORY", "レクサス アップグレードファクトリー",
         "KINTO FACTORY", "キントファクトリー", "ホンダアクセス", "モデリスタ", "スズキコネクト", "T-Connect",
         "トヨタ セキュリティシステム", "スマートアップグレードスイッチ", "トヨタ 後付け", "ホンダ 後付け"]


def main():
    out = {"at_utc": datetime.datetime.utcnow().isoformat()}
    try:
        r = requests.get(f"{llm.DFS_BASE}/appendix/user_data", auth=llm._dfs_auth(), timeout=60)
        res = (r.json().get("tasks") or [{}])[0].get("result") or [{}]
        money = (res[0] or {}).get("money") or {}
        out["money"] = {"balance": money.get("balance"), "total": money.get("total")}
    except Exception as e:  # noqa: BLE001
        out["money_error"] = str(e)[:200]
    try:
        task = llm._post("keywords_data/google_ads/search_volume/live",
                         [{"keywords": NAMES, "language_code": "ja", "location_code": 2392}])
        out["volumes"] = {it["keyword"]: {"volume": it.get("search_volume"),
                                          "monthly": [{"y": m.get("year"), "m": m.get("month"), "v": m.get("search_volume")}
                                                      for m in (it.get("monthly_searches") or [])]}
                          for it in task.get("result") or []}
    except Exception as e:  # noqa: BLE001
        out["volumes_error"] = str(e)[:200]
    out["spent"] = llm.spent()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "extra.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "volumes"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
