#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中古車関連ページの GA4 実測（Windsor.ai 経由・toyota.jp account 324699885）。

toyota.jp の GA4 には U-Car サイト本体（toyota.jp/ucar/ のトップ・在庫検索など）は入っておらず、
入っているのは toyota.jp 側の中古車関連ページ（/service/estimate/ucar_* の見積り、/ucar/support/、
/information/ucar/ など）と、U-Car から toyota.jp へ移ってきた流れ（page_referrer）である。
ここではそれらと、サイト全体の AI 経由流入を取る。

取るもの
  1. 中古車関連ページの日別セッション（90日）・タイトル（28日）
  2. 中古車関連ページの参照元（28日）と、AI経由（90日）
  3. 中古車関連ページをランディングにしたセッションの参照元とキーイベント（28日）
  4. U-Car サイトから toyota.jp に来た流れ（page_referrer に toyota.jp/ucar を含む・28日）
  5. サイト全体：AI経由の日別セッション（365日）と、AI経由で入ったページ（90日）
  6. Search Console（GA4連携）：中古車関連ランディングの表示・クリック・順位（28日）
結果は data/ucar/ga4_ucar_<day>.json に生で置く。必要環境変数: WINDSOR_API_KEY
"""
import json
import os
import sys
import time
import urllib.error
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
D365 = YB - timedelta(days=364)
OUT = Path(__file__).resolve().parent.parent / "data" / "ucar"
OUT.mkdir(parents=True, exist_ok=True)

UCAR = ["ucar", "u-car", "/usedcar"]
AI_SRC = ["chatgpt", "openai", "gemini", "copilot", "perplexity", "claude", "grok", "deepseek", "bing.com/chat"]
CONV1 = ["conversions", "conversions_estimate_simulation_complete", "conversions_dealer_search",
         "conversions_dealer_estimate_complete", "conversions_purchase_consultation_complete"]
CONV2 = ["conversions_catalog_request_dealer_complete", "conversions_test_drive_instant_reserve_complete",
         "conversions_test_drive_normal_reserve_complete", "conversions_maker_estimate_complete",
         "conversions_lead_complete", "conversions_sign_up"]
LOG = []


def q(fields, dfrom, dto, flt=None, tag="", max_rows=None):
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
                data = json.loads(r.read().decode("utf-8"))
            rows = data.get("data", data if isinstance(data, list) else [])
            LOG.append({"tag": tag, "fields": fields, "from": str(dfrom), "to": str(dto), "filter": flt,
                        "rows": len(rows), "ok": True})
            print(f"  ok {tag}: {len(rows)} rows", flush=True)
            return rows
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read().decode('utf-8', 'ignore')[:400]}"
            if e.code in (400, 401, 403, 422):
                break
            time.sleep(10 * (att + 1))
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(10 * (att + 1))
    LOG.append({"tag": tag, "fields": fields, "from": str(dfrom), "to": str(dto), "filter": flt, "ok": False, "error": last})
    print(f"  NG {tag}: {last}", file=sys.stderr, flush=True)
    return None


def anyof(field, needles):
    parts = []
    for i, n in enumerate(needles):
        if i:
            parts.append("or")
        parts.append([field, "contains", n])
    return parts


def run(res, tag, fields, dfrom, dto, flt, max_rows=None):
    res[tag] = {"fields": fields, "filter": flt, "range": [str(dfrom), str(dto)],
                "rows": q(fields, dfrom, dto, flt, tag=tag, max_rows=max_rows)}


def main():
    res = {"generated_at": datetime.now(JST).isoformat(), "account": ACC,
           "range28": [str(D28), str(YB)], "range90": [str(D90), str(YB)], "range365": [str(D365), str(YB)]}
    P = anyof("page_path", UCAR)
    L = anyof("landing_page", UCAR)
    A = anyof("source", AI_SRC)
    # 1. 中古車関連ページ
    run(res, "pages_daily_90", ["date", "page_path", "sessions", "totalusers", "screen_page_views"], D90, YB, P)
    run(res, "pages_title_28", ["page_path", "pagetitle", "sessions"], D28, YB, P)
    # 2. 参照元
    run(res, "pages_source_28", ["page_path", "source", "medium", "sessions", "totalusers"], D28, YB, P)
    run(res, "pages_ai_90", ["page_path", "source", "sessions"], D90, YB, [P, "and", A])
    # 3. ランディングと成果
    run(res, "landing_source_28", ["landing_page", "source", "medium", "session_default_channel_group", "sessions", "newusers",
                                   "conversions"], D28, YB, L)
    run(res, "landing_conv1_28", ["landing_page", "sessions"] + CONV1, D28, YB, L)
    run(res, "landing_conv2_28", ["landing_page", "sessions"] + CONV2, D28, YB, L)
    run(res, "events_28", ["page_path", "event_name", "event_count"], D28, YB, P)
    # 4. U-Car サイトから toyota.jp へ来た流れ
    run(res, "from_ucar_28", ["page_path", "page_referrer", "sessions"], D28, YB,
        anyof("page_referrer", ["toyota.jp/ucar", "toyota.jp/u-car", "gazoo.com/U-Car", "toyota-outlet.gazoo.com"]))
    run(res, "from_ucar_daily_90", ["date", "page_referrer", "sessions"], D90, YB,
        anyof("page_referrer", ["toyota.jp/ucar", "toyota.jp/u-car"]))
    # 5. サイト全体の AI 経由
    run(res, "site_ai_daily_365", ["date", "source", "sessions"], D365, YB, A)
    run(res, "site_ai_pages_90", ["page_path", "source", "sessions"], D90, YB, A, max_rows=20000)
    run(res, "site_ai_landing_conv_90", ["landing_page", "source", "sessions", "conversions"], D90, YB,
        A, max_rows=20000)
    run(res, "site_daily_90", ["date", "sessions", "conversions"], D90, YB, None)
    # 6. Search Console（GA4連携）
    run(res, "sc_ucar_28", ["landing_page_plus_query_string", "organic_google_search_clicks",
                            "organic_google_search_impressions", "organic_google_search_average_position"], D28, YB,
        anyof("landing_page_plus_query_string", UCAR))
    res["log"] = LOG
    path = OUT / f"ga4_ucar_{TODAY}.json"
    path.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ok = sum(1 for x in LOG if x.get("ok"))
    print(f"完了: {ok}/{len(LOG)} クエリ成功 → {path}")


if __name__ == "__main__":
    main()
