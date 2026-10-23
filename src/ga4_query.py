#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toyota.jp GA4 の「臨時クエリ」。資料に無い切り口を、鍵なしで・数分で取って返す仕組み。

背景（2026-10-23 の点検）
  データマンが「答えきれなかった」質問の多くは、資料（toyota_jp_ga4.md）に無い切り口だった。
    ・特定URL配下のアクセス実績（/madoguchi/ など）… 資料は「上位ページ」までで、URL単位が無い
    ・参照元ページからの遷移数（リクエスト完了ページ → /aushop/ など）
    ・任意期間・任意のイベント名／参照元
  GA4（Windsor.ai）には全部あるが、Mac側に WINDSOR_API_KEY が無い・取り方が決まっていない、で止まっていた。

使い方（Mac側のデータマンも、人間も同じ）
  1. triggers/ga4-query.json に依頼を書いて main に push する（書式は下）
  2. GitHub Actions（ga4-query）が鍵付きで取得し、data/ga4/adhoc/<id>.md と .csv を push する（通常2〜5分）
  3. pull して <id>.md を読む。一覧は data/ga4/adhoc/INDEX.md
  鍵はGitHub Secretsにだけ置く。Mac側に鍵を置く必要はない。

依頼JSONの書式（queries は複数可）
  {
    "queries": [{
      "id": "madoguchi_monthly",            必須。英数字と _ - のみ。出力ファイル名になる
      "title": "toyota.jp/madoguchi/ 配下の月別アクセス",
      "from": "2025-06-01", "to": "2026-02-28",   必須
      "by": "month",                         "none"（期間全体・UUは期間で集計）/ "month" / "day"。既定 none
      "dims": ["page_path"],                 任意。下の DIMS から。既定なし（by だけ）
      "metrics": ["sessions", "totalusers"], 任意。下の METRICS から。既定 sessions, totalusers, screen_page_views
      "filters": [["page_path", "contains", "/madoguchi/"]],   任意。AND結合
      "top": 100,                            任意。行数の上限（セッション降順）。既定 200
      "rollup_depth": 2                      任意。page_path 系の dims をURLの先頭N階層にまとめる（足せる指標だけ）
    }],
    "note": "誰が何のために", "requested_at": "ISO日時"
  }

守っていること
  ・UU（totalusers）は足し算しない。by=month は「月内の重複を除いたUU」。期間全体のUUが要るときは by=none で取る
  ・長い期間（by=month/day）は月ごとに分けて取る。by=none で2か月を超える場合は1回で取る（UUを正しくするため）。
    重くて落ちたときは失敗として md に書く（0件とは書かない）
  ・取得できなかった・打ち切りの疑いは md の先頭に必ず書く。黙って空の表を出さない
  ・失敗した依頼があれば終了コード1（Actionsが赤くなる）。成功した依頼の出力は残す

環境変数: WINDSOR_API_KEY（必須）／ GA4_MOCK=1（ネットに出ず合成データで動作確認）／ GA4_OUT_DIR（試験用の出力先）
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ga4_report as G  # noqa: E402  windsor()・month_chunks() など実績のある取得部分を共用する

ROOT = G.ROOT
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
OUT_DIR = os.environ.get("GA4_OUT_DIR") or os.path.join(ROOT, "data")
ADHOC = os.path.join(OUT_DIR, "ga4", "adhoc")
MOCK = os.environ.get("GA4_MOCK") == "1"

# Windsor の get_fields で実在を確認したIDだけ許す（綴り違いで「0件」になるのを防ぐ）
DIMS = {
    "date", "year_month", "year_week", "page_path", "page_path_plus_query_string", "page_location", "pagetitle",
    "page_referrer", "landing_page", "landing_page_plus_query_string", "event_name", "source", "medium",
    "source_medium", "session_source_medium", "session_default_channel_group", "country", "region", "city",
    "devicecategory", "hostname", "outbound",
}
METRICS = {
    "sessions", "totalusers", "newusers", "active_users", "conversions", "event_count", "screen_page_views",
    "engaged_sessions", "engagement_rate", "bounce_rate", "average_session_duration", "user_engagement_duration",
}
ADDITIVE = {"sessions", "newusers", "conversions", "event_count", "screen_page_views", "engaged_sessions"}
OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "contains", "ncontains", "null", "notnull", "in"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")
DEFAULT_METRICS = ["sessions", "totalusers", "screen_page_views"]


def build_filter(filters):
    """[[f,op,v],...] → Windsor の filter（AND結合）"""
    out = []
    for f in filters or []:
        if not (isinstance(f, (list, tuple)) and len(f) == 3):
            raise ValueError(f"filters の各要素は [項目, 演算子, 値]：{f}")
        field, op, val = f
        if field not in DIMS | METRICS:
            raise ValueError(f"filters の項目が許可リストに無い：{field}")
        if op not in OPS:
            raise ValueError(f"filters の演算子が不正：{op}（使えるもの {sorted(OPS)}）")
        if out:
            out.append("and")
        out.append([field, op, val])
    return out or None


def rollup_path(p: str, depth: int) -> str:
    base = (p or "").split("?")[0]
    segs = [s for s in base.split("/") if s]
    return "/" + "/".join(segs[:depth]) + ("/" if segs else "")


def _mock_rows(fields, a, b, flt):
    import random
    rnd = random.Random(f"{fields}{a}{b}{flt}")
    rows = []
    paths = ["/madoguchi/", "/madoguchi/column/06/", "/aushop/", "/aushop/service/", "/request/webcatalog/"]
    for p in paths:
        r = {}
        for fld in fields:
            if fld == "date":
                r[fld] = str(a)
            elif fld == "year_month":
                r[fld] = a.strftime("%Y%m")
            elif fld in ("page_path", "page_path_plus_query_string", "page_location", "landing_page"):
                r[fld] = p
            elif fld == "page_referrer":
                r[fld] = "https://toyota.jp/request/webcatalog/"
            elif fld in METRICS:
                r[fld] = rnd.randint(10, 3000)
            else:
                r[fld] = "x"
        rows.append(r)
    return rows


def fetch(fields, a, b, flt):
    if MOCK:
        return _mock_rows(fields, a, b, flt)
    return G.windsor(fields, a, b, flt)


def run_query(q: dict):
    """1つの依頼を実行して (rows, notes) を返す。例外は呼び出し側で失敗として記録する。"""
    qid = q.get("id", "")
    if not ID_RE.match(str(qid)):
        raise ValueError(f"id は英数字と _ - のみ（60字まで）：{qid!r}")
    try:
        d0, d1 = date.fromisoformat(q["from"]), date.fromisoformat(q["to"])
    except Exception:  # noqa: BLE001
        raise ValueError("from / to は YYYY-MM-DD で必須")
    if d0 > d1:
        raise ValueError("from が to より後")
    if d1 >= G.TODAY:
        d1 = G.YB
        notes = [f"to を前日（{d1}）に丸めた（当日分は未確定のため）"]
    else:
        notes = []
    by = q.get("by", "none")
    if by not in ("none", "month", "day"):
        raise ValueError("by は none / month / day")
    dims = list(q.get("dims") or [])
    for d in dims:
        if d not in DIMS:
            raise ValueError(f"dims が許可リストに無い：{d}（使えるもの {sorted(DIMS)}）")
    metrics = list(q.get("metrics") or DEFAULT_METRICS)
    for m in metrics:
        if m not in METRICS:
            raise ValueError(f"metrics が許可リストに無い：{m}（使えるもの {sorted(METRICS)}）")
    flt = build_filter(q.get("filters"))
    time_dim = {"none": [], "month": ["year_month"], "day": ["date"]}[by]
    fields = time_dim + [d for d in dims if d not in time_dim] + metrics
    top = int(q.get("top") or 200)

    # 取得：by=month/day は月ごと。by=none は期間全体を1回（UUを正しく出すため）
    rows: list[dict] = []
    if by == "none":
        if (d1 - d0).days > 62:
            notes.append(f"期間が長い（{(d1 - d0).days + 1}日）。重くて失敗したら by=month に分けて依頼する")
        rows = fetch(fields, d0, d1, flt)
    else:
        for a, b in G.month_chunks(d0, d1):
            rows += fetch(fields, a, b, flt)

    # 同じキーの行を足す（月ごと取得の重複や、Windsorが分割して返す行のまとめ）。UUは行単位でそのまま
    key_dims = time_dim + [d for d in dims if d not in time_dim]
    rd = q.get("rollup_depth")
    path_dims = [d for d in key_dims if d in ("page_path", "page_path_plus_query_string", "landing_page",
                                              "landing_page_plus_query_string")]
    if rd and path_dims:
        rd = int(rd)
        use_metrics = [m for m in metrics if m in ADDITIVE]
        dropped = [m for m in metrics if m not in ADDITIVE]
        if dropped:
            notes.append(f"rollup_depth を使うと、足し算できない指標（{', '.join(dropped)}）は出せない。必要なら rollup なしで取る")
        agg: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
        for r in rows:
            k = tuple(rollup_path(str(r.get(d, "")), rd) if d in path_dims else str(r.get(d, "")) for d in key_dims)
            for m in use_metrics:
                agg[k][m] += G.n(r.get(m))
        out = [dict(zip(key_dims, k), **{m: v[m] for m in use_metrics}) for k, v in agg.items()]
        metrics = use_metrics
    else:
        agg2: dict[tuple, dict] = {}
        for r in rows:
            k = tuple(str(r.get(d, "")) for d in key_dims)
            if k in agg2:
                for m in metrics:
                    if m in ADDITIVE:
                        agg2[k][m] += G.n(r.get(m))
                    else:
                        agg2[k][m] = max(agg2[k][m], G.n(r.get(m)))     # UU等は足さない（大きい方を残す）
            else:
                agg2[k] = {m: G.n(r.get(m)) for m in metrics}
        out = [dict(zip(key_dims, k), **v) for k, v in agg2.items()]

    sort_m = "sessions" if "sessions" in metrics else metrics[0]
    out.sort(key=lambda r: (r.get(time_dim[0], "") if time_dim else "", -(r.get(sort_m) or 0)) if time_dim else -(r.get(sort_m) or 0))
    if time_dim:
        # 時間軸があるときは、各期間の上位 top 件に絞る
        per = defaultdict(list)
        for r in out:
            per[r[time_dim[0]]].append(r)
        out = [r for t in sorted(per) for r in sorted(per[t], key=lambda x: -(x.get(sort_m) or 0))[:top]]
    else:
        out = out[:top]
    if not out:
        notes.append("該当する行が0件。絞り込み・期間・綴りを確認（GA4側に実際に無い可能性もあるが、0と断定しない）")
    return {"id": qid, "title": q.get("title", qid), "from": str(d0), "to": str(d1), "by": by, "dims": key_dims,
            "metrics": metrics, "filters": q.get("filters") or [], "top": top, "rows": out, "notes": notes,
            "raw_rows": len(rows)}


def fmt(x):
    return f"{int(round(x)):,}" if isinstance(x, (int, float)) and float(x).is_integer() else (f"{x:,.2f}" if isinstance(x, float) else str(x))


def write_result(res: dict | None, qid: str, title: str, spec: dict, err: str | None = None):
    os.makedirs(ADHOC, exist_ok=True)
    md_path = os.path.join(ADHOC, f"{qid}.md")
    L = [f"# {title}", ""]
    L.append(f"- 取得: {NOW:%Y-%m-%d %H:%M} JST ／ 出典: GA4「Toyota.jp【本格移行用】- GA4」（臨時クエリ ga4-query）")
    if err:
        L += ["- **取得失敗**: " + err, "- この切り口は数値を出せていない。0件ではない。依頼JSONを直すか、期間を分けて再依頼する", ""]
        L += ["## 依頼内容", "```json", json.dumps(spec, ensure_ascii=False, indent=1), "```"]
        open(md_path, "w", encoding="utf-8").write("\n".join(L) + "\n")
        return
    L.append(f"- 期間: {res['from']}〜{res['to']} ／ 単位: {res['by']} ／ 絞り込み: "
             + (" かつ ".join(f"{a} {b} {c}" for a, b, c in res["filters"]) or "なし"))
    L.append(f"- 取得行数: {res['raw_rows']:,} ／ 表示: {len(res['rows']):,}（上位 {res['top']}）")
    L.append("- 注意: UU（totalusers）は足し算しない。by=month の値は月内で重複を除いた値。期間全体のUUは by=none で取った値だけを使う")
    for nt in res["notes"]:
        L.append(f"- **注意**: {nt}")
    L.append("")
    cols = res["dims"] + res["metrics"]
    L.append("| " + " | ".join(cols) + " |")
    L.append("|" + "|".join("---:" if c in METRICS else "---" for c in cols) + "|")
    for r in res["rows"][:300]:
        L.append("| " + " | ".join(G.esc(fmt(r.get(c, ""))) if c in METRICS else G.esc(str(r.get(c, ""))[:140]) for c in cols) + " |")
    if len(res["rows"]) > 300:
        L.append(f"\n（表は先頭300行。全件は {qid}.csv）")
    open(md_path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    with open(os.path.join(ADHOC, f"{qid}.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in res["rows"]:
            w.writerow([r.get(c, "") for c in cols])


def write_index(entries: list[dict]):
    path = os.path.join(ADHOC, "INDEX.md")
    old = []
    try:
        for line in open(path, encoding="utf-8"):
            if line.startswith("| ") and not line.startswith("| 実行") and not line.startswith("|---"):
                old.append(line.rstrip("\n"))
    except FileNotFoundError:
        pass
    ids_new = {e["id"] for e in entries}
    keep = [ln for ln in old if ln.split("|")[2].strip() not in ids_new]
    new = [f"| {NOW:%Y-%m-%d %H:%M} | {e['id']} | {e['status']} | {G.esc(e['title'])[:60]} | {e['period']} | {e['rows']} |" for e in entries]
    lines = (new + keep)[:80]
    head = ["# GA4 臨時クエリ 一覧（新しい順）", "",
            "依頼は triggers/ga4-query.json（書式は src/ga4_query.py の先頭）。結果は <id>.md と <id>.csv。", "",
            "| 実行(JST) | id | 状態 | 題 | 期間 | 行数 |", "|---|---|---|---|---|---|"]
    os.makedirs(ADHOC, exist_ok=True)
    open(path, "w", encoding="utf-8").write("\n".join(head + lines) + "\n")


def main(argv):
    spec_path = argv[1] if len(argv) > 1 else os.path.join(ROOT, "triggers", "ga4-query.json")
    if not G.KEY and not MOCK:
        sys.exit("WINDSOR_API_KEY 未設定")
    spec = json.load(open(spec_path, encoding="utf-8"))
    qs = spec.get("queries") or []
    if not qs:
        print("queries が空。何もしない")
        return 0
    entries, failed = [], 0
    for q in qs:
        qid, title = str(q.get("id", "noid")), q.get("title") or str(q.get("id", ""))
        try:
            res = run_query(q)
            write_result(res, res["id"], res["title"], q)
            entries.append({"id": res["id"], "title": res["title"], "status": "OK" if res["rows"] else "0件",
                            "period": f"{res['from']}〜{res['to']}", "rows": len(res["rows"])})
            print(f"OK {qid}: {len(res['rows'])} rows")
        except Exception as e:  # noqa: BLE001
            failed += 1
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            if ID_RE.match(qid):
                write_result(None, qid, title, q, err=msg)
            entries.append({"id": qid, "title": title, "status": "失敗", "period": f"{q.get('from')}〜{q.get('to')}", "rows": 0})
            print(f"FAIL {qid}: {msg}")
    write_index(entries)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
