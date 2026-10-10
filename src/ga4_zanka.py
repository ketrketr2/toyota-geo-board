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


CONV = ["conversions", "conversions_estimate_simulation_complete", "conversions_dealer_search",
        "conversions_dealer_estimate_complete", "conversions_purchase_consultation_complete",
        "conversions_catalog_request_dealer_complete", "conversions_test_drive_instant_reserve_complete",
        "conversions_test_drive_normal_reserve_complete", "conversions_maker_estimate_complete",
        "conversions_lead_complete", "conversions_sign_up"]
AI_SRC = ["chatgpt", "openai", "gemini", "copilot", "perplexity", "claude", "grok", "deepseek"]


def run(tag, fields, dfrom, dto, flt):
    rows = q(fields, dfrom, dto, flt, tag=tag)
    return {"fields": fields, "filter": flt, "rows": rows}


def main():
    res = {"generated_at": datetime.now(JST).isoformat(), "account": ACC,
           "range28": [str(D28), str(YB)], "range90": [str(D90), str(YB)]}
    Z = ZANKA_PATHS + COMPARE_PATHS
    # 1. ページパス別・日別（90日）
    res["pages_daily"] = run("pages_daily", ["date", "page_path", "sessions", "screen_page_views", "totalusers"],
                             D90, YB, or_filter("page_path", Z))
    # 2. ページ×参照元（28日）
    res["pages_source"] = run("pages_source", ["page_path", "source", "medium", "sessions", "totalusers"],
                              D28, YB, or_filter("page_path", Z))
    # 3. ランディング×参照元（28日）
    res["landing_source"] = run("landing_source", ["landing_page", "source", "medium", "session_default_channel_group", "sessions", "newusers"],
                                D28, YB, or_filter("landing_page", Z))
    # 4. ランディング別のCV（28日）＝接触後CV
    res["landing_conv"] = run("landing_conv", ["landing_page", "sessions"] + CONV, D28, YB, or_filter("landing_page", Z))
    # 5. ページ別のCV（28日）
    res["page_conv"] = run("page_conv", ["page_path", "sessions"] + CONV, D28, YB, or_filter("page_path", Z))
    # 6. ページ内のクリック（28日）
    res["page_clicks"] = run("page_clicks", ["page_path", "event_name", "customevent_link_label", "event_count"],
                             D28, YB, [or_filter("page_path", ZANKA_PATHS), "and", ["event_name", "eq", "custom_link_click"]])
    res["page_cta"] = run("page_cta", ["page_path", "customevent_cta_type", "customevent_link_label", "event_count"],
                          D28, YB, or_filter("page_path", ZANKA_PATHS))
    # 7. サイト全体のAI参照元（90日・日別）
    res["site_ai_daily"] = run("site_ai_daily", ["date", "source", "sessions"], D90, YB, or_filter("source", AI_SRC))
    # 8. 残価ページへのAI参照元（90日・日別）
    res["pages_ai_daily"] = run("pages_ai_daily", ["date", "page_path", "source", "sessions"], D90, YB,
                                [or_filter("page_path", Z), "and", or_filter("source", AI_SRC)])
    # 9. タイトル・デバイス・エンゲージメント（28日）
    res["pages_title"] = run("pages_title", ["page_path", "pagetitle", "sessions"], D28, YB, or_filter("page_path", ZANKA_PATHS))
    res["pages_device"] = run("pages_device", ["page_path", "devicecategory", "sessions"], D28, YB, or_filter("page_path", ZANKA_PATHS))
    res["pages_engage"] = run("pages_engage", ["page_path", "sessions", "engaged_sessions", "average_session_duration", "screen_page_views", "newusers"],
                              D28, YB, or_filter("page_path", Z))
    # 10. 見積りシミュレーション完了・販売店検索のサイト全体日別（90日）比較用
    res["site_conv_daily"] = run("site_conv_daily", ["date", "sessions"] + CONV, D90, YB, None)
    # 11. ページ参照元（page_referrer）：残価ページに来る直前のページ（28日）
    res["pages_referrer"] = run("pages_referrer", ["page_path", "page_referrer", "sessions"], D28, YB, or_filter("page_path", ZANKA_PATHS))

    res["log"] = LOG
    (OUT / f"ga4_zanka_{TODAY}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for l in LOG if l.get("ok"))
    print(f"完了: {ok}/{len(LOG)} クエリ成功 → data/zanka/ga4_zanka_{TODAY}.json")


if __name__ == "__main__":
    main()
