#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""残価設定型プラン関連ページの GA4 実測（Windsor.ai 経由・account 324699885）。

取るもの（直近90日 / 28日）
  1. 残価関連ページのページパス別・日別セッション／PV／ユーザー
  2. 残価関連ページへのセッション参照元（AI経由を含む）
  3. 残価関連ページをランディングにしたセッションの参照元・メディア
  4. 残価関連ページでのイベント名別イベント数（クリック・CTA）
  5. アルファード／ヴェルファイア車種ページ、見積りシミュレーションの日別セッション（比較用）
結果は data/zanka/ga4_*.json に生で置き、集計は手元で行う。
必要環境変数: WINDSOR_API_KEY
"""
import os
import sys
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEY = os.environ.get("WINDSOR_API_KEY", "")
if not KEY:
    sys.exit("WINDSOR_API_KEY 未設定")
ACC = "324699885"
BASE = "https://connectors.windsor.ai/googleanalytics4"
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).date()
YB = TODAY - timedelta(days=1)
D28 = YB - timedelta(days=27)
D90 = YB - timedelta(days=89)
OUT = Path(__file__).resolve().parent.parent / "data" / "zanka"
OUT.mkdir(parents=True, exist_ok=True)

ZANKA_PATHS = ["/request/payment", "/request/howtobuy", "/request/tripleassist", "/request/compare",
               "/request/kinto", "/information/minivan/zanka", "/madoguchi/column/06"]
COMPARE_PATHS = ["/alphard/", "/vellfire/", "/estimate"]

LOG = []


def q(fields, dfrom, dto, flt=None, max_rows=None, tag=""):
    p = {"api_key": KEY, "select_accounts": ACC, "_renderer": "json",
         "fields": ",".join(fields), "date_from": str(dfrom), "date_to": str(dto)}
    if flt is not None:
        p["filter"] = json.dumps(flt, ensure_ascii=False)
    if max_rows:
        p["_max_rows"] = str(max_rows)
    url = BASE + "?" + urllib.parse.urlencode(p)
    last = None
    for att in range(3):
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                raw = r.read().decode("utf-8")
            data = json.loads(raw)
            rows = data.get("data", data if isinstance(data, list) else [])
            LOG.append({"tag": tag, "fields": fields, "from": str(dfrom), "to": str(dto), "filter": flt,
                        "rows": len(rows), "ok": True, "keys": sorted(rows[0].keys()) if rows else []})
            print(f"  ok {tag}: {len(rows)} rows", flush=True)
            return rows
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:500]
            last = f"HTTP {e.code} {body}"
            if e.code in (400, 401, 403, 422):
                break
            time.sleep(10 * (att + 1))
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(10 * (att + 1))
    LOG.append({"tag": tag, "fields": fields, "from": str(dfrom), "to": str(dto), "filter": flt, "ok": False, "error": last})
    print(f"  NG {tag}: {last}", file=sys.stderr, flush=True)
    return None


def or_filter(field, needles):
    parts = []
    for i, n in enumerate(needles):
        if i:
            parts.append("or")
        parts.append([field, "contains", n])
    return parts


def first_ok(variants, dfrom, dto, flt_field_candidates, needles, tag, max_rows=None):
    """フィールド名の表記ゆれに備えて候補を順に試し、通ったものを返す。"""
    for fields, ffield in zip(variants, flt_field_candidates):
        flt = or_filter(ffield, needles) if ffield else None
        rows = q(fields, dfrom, dto, flt, max_rows=max_rows, tag=tag + "/" + ffield)
        if rows is not None:
            return {"fields": fields, "filter_field": ffield, "rows": rows}
    return {"fields": None, "rows": None}


def main():
    # 0. 使えるフィールド一覧（表記の確認用）
    try:
        with urllib.request.urlopen(f"{BASE}/fields?api_key={KEY}", timeout=120) as r:
            fields_doc = json.loads(r.read().decode("utf-8"))
        (OUT / "ga4_fields.json").write_text(json.dumps(fields_doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print("fields doc saved", flush=True)
    except Exception as e:
        print(f"fields doc NG: {e}", file=sys.stderr)

    res = {"generated_at": datetime.now(JST).isoformat(), "account": ACC, "range28": [str(D28), str(YB)], "range90": [str(D90), str(YB)]}

    # 1. ページパス別・日別（90日）
    res["pages_daily"] = first_ok(
        [["date", "pagepath", "sessions", "screenpageviews", "totalusers"],
         ["date", "page_path", "sessions", "screenpageviews", "totalusers"],
         ["date", "pagePath", "sessions", "screenPageViews", "totalUsers"]],
        D90, YB, ["pagepath", "page_path", "pagePath"], ZANKA_PATHS + COMPARE_PATHS, "pages_daily")

    # 2. 参照元別（28日）：AI経由（chatgpt / gemini / copilot / perplexity / claude）を含む
    res["pages_source"] = first_ok(
        [["pagepath", "sessionsource", "sessionmedium", "sessions", "totalusers"],
         ["pagepath", "source", "medium", "sessions", "totalusers"],
         ["page_path", "session_source", "session_medium", "sessions", "totalusers"]],
        D28, YB, ["pagepath", "pagepath", "page_path"], ZANKA_PATHS + COMPARE_PATHS, "pages_source")

    # 3. ランディング別（28日）
    res["landing_source"] = first_ok(
        [["landingpage", "sessionsource", "sessionmedium", "sessiondefaultchannelgroup", "sessions", "newusers"],
         ["landingpage", "source", "medium", "sessions", "newusers"],
         ["landing_page", "session_source", "session_medium", "sessions", "newusers"]],
        D28, YB, ["landingpage", "landingpage", "landing_page"], ZANKA_PATHS + COMPARE_PATHS, "landing_source")

    # 4. イベント（28日）：残価ページ内のクリック等
    res["pages_events"] = first_ok(
        [["pagepath", "eventname", "eventcount"],
         ["page_path", "event_name", "event_count"],
         ["pagePath", "eventName", "eventCount"]],
        D28, YB, ["pagepath", "page_path", "pagePath"], ZANKA_PATHS, "pages_events")

    # 5. サイト全体のAI参照元（90日・日別）比較用
    res["site_ai_daily"] = first_ok(
        [["date", "sessionsource", "sessions"], ["date", "source", "sessions"]],
        D90, YB, ["sessionsource", "source"],
        ["chatgpt", "openai", "gemini", "copilot", "perplexity", "claude", "grok", "deepseek"], "site_ai_daily")

    # 6. 残価ページのページタイトル確認（28日）
    res["pages_title"] = first_ok(
        [["pagepath", "pagetitle", "sessions"], ["page_path", "page_title", "sessions"]],
        D28, YB, ["pagepath", "page_path"], ZANKA_PATHS, "pages_title")

    res["log"] = LOG
    (OUT / f"ga4_zanka_{TODAY}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for l in LOG if l.get("ok"))
    print(f"完了: {ok}/{len(LOG)} クエリ成功 → data/zanka/ga4_zanka_{TODAY}.json")


if __name__ == "__main__":
    main()
