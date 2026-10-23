#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config/ga4_sections.json に書いたURL配下の月別アクセスを自動集計する（毎朝 ga4-sections）。
目的: 「このページ群の実績は？」にデータマンが資料だけで答えられるようにする。聞かれてから取りに行かない。
UUは各月・各セクションでGA4が重複を除いた値（セクション間・月間の合算はしない）。失敗したセクションは md に明記し、0にしない。
"""
import csv, json, os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ga4_query as Q
import ga4_report as G

def main():
    if not G.KEY and not Q.MOCK:
        sys.exit("WINDSOR_API_KEY 未設定")
    cfg = json.load(open(os.path.join(G.ROOT, "config", "ga4_sections.json"), encoding="utf-8"))
    months = int(cfg.get("months", 13))
    y, m = G.YB.year, G.YB.month
    for _ in range(months - 1):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    d0 = date(y, m, 1)
    rows, fails = [], []
    for s in cfg["sections"]:
        try:
            res = Q.run_query({"id": "sec", "from": str(d0), "to": str(G.YB), "by": "month",
                               "filters": [["page_path", "contains", s["contains"]]],
                               "metrics": ["sessions", "totalusers", "screen_page_views"], "top": 1})
            for r in res["rows"]:
                rows.append([r["year_month"], s["name"], s["contains"], int(r["sessions"]), int(r["totalusers"]), int(r["screen_page_views"])])
        except Exception as e:  # noqa: BLE001
            fails.append(f"{s['name']}: {type(e).__name__} {str(e)[:150]}")
    out = os.path.join(Q.OUT_DIR, "ga4", "sections")
    os.makedirs(out, exist_ok=True)
    rows.sort(key=lambda r: (r[1], r[0]))
    with open(os.path.join(out, "sections_monthly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["year_month", "section", "path_contains", "sessions", "uu_in_month", "pageviews"]); w.writerows(rows)
    L = ["# toyota.jp セクション別 月次アクセス（自動集計）", "",
         f"- 取得: {Q.NOW:%Y-%m-%d %H:%M} JST ／ 出典: GA4（Windsor）／ 期間: {d0}〜{G.YB}",
         "- 対象は config/ga4_sections.json の URL 条件（page_path に含む）。セッションは「その配下のページを見たセッション」。UUは月内の重複除外値で、月・セクションをまたいで足さない"]
    if fails:
        L.append("- **取得失敗のセクション（数値なし。0ではない）**: " + " ／ ".join(fails))
    L.append("")
    for name in dict.fromkeys(r[1] for r in rows):
        L += [f"## {name}", "", "| 月 | セッション | UU(月内) | PV |", "|---|---:|---:|---:|"]
        L += [f"| {r[0]} | {r[3]:,} | {r[4]:,} | {r[5]:,} |" for r in rows if r[1] == name]
        L.append("")
    open(os.path.join(out, "sections_monthly.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"sections rows={len(rows)} fails={len(fails)}")
    return 1 if fails or not rows else 0

if __name__ == "__main__":
    sys.exit(main())
