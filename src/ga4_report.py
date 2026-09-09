#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toyota.jp GA4実測（Windsor.ai経由・account 324699885）→ データマン用の日本語資料を生成する。
GitHub Actions（ga4-daily）で毎朝実行し data/ga4/toyota_jp_ga4.md を更新。Mac側のデータマンが pull して取り込む。
必要環境変数: WINDSOR_API_KEY
"""
import os
import sys
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from collections import defaultdict

KEY = os.environ.get("WINDSOR_API_KEY", "")
if not KEY:
    sys.exit("WINDSOR_API_KEY 未設定")
ACC = "324699885"
BASE = "https://connectors.windsor.ai/googleanalytics4"
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).date()
YB = TODAY - timedelta(days=1)        # 前日（データ終端）
D28 = YB - timedelta(days=27)
D40 = YB - timedelta(days=39)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ga4", "toyota_jp_ga4.md")

COUNTRY_JP = {"Japan": "日本", "Singapore": "シンガポール", "United States": "米国", "Taiwan": "台湾", "China": "中国",
              "Hong Kong": "香港", "Thailand": "タイ", "South Korea": "韓国", "Brazil": "ブラジル", "France": "フランス",
              "Philippines": "フィリピン", "Australia": "オーストラリア", "Vietnam": "ベトナム", "Indonesia": "インドネシア",
              "United Kingdom": "英国", "Germany": "ドイツ", "Malaysia": "マレーシア", "Canada": "カナダ", "India": "インド"}
DEVICE_JP = {"mobile": "モバイル", "desktop": "PC(デスクトップ)", "tablet": "タブレット", "smart tv": "スマートTV", "smarttv": "スマートTV"}


def q(fields, dfrom, dto):
    p = {"api_key": KEY, "select_accounts": ACC, "_renderer": "json",
         "fields": ",".join(fields), "date_from": str(dfrom), "date_to": str(dto)}
    url = BASE + "?" + urllib.parse.urlencode(p)
    last = None
    for att in range(3):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data.get("data", data if isinstance(data, list) else [])
        except Exception as e:
            last = e
            time.sleep(10 * (att + 1))
    raise SystemExit(f"Windsor取得失敗 {fields} {dfrom}..{dto}: {last}")


def n(x):
    try:
        f = float(x or 0)
        return int(f) if f.is_integer() else round(f, 2)
    except Exception:
        return 0


def fmt(x):
    return f"{int(round(x)):,}"


def main():
    daily = q(["date", "sessions", "totalusers", "newusers", "conversions"], D40, YB)
    dev = q(["devicecategory", "sessions", "totalusers", "newusers"], D28, YB)
    ctry = q(["country", "sessions", "totalusers"], D28, YB)
    pages = q(["pagetitle", "sessions", "totalusers", "newusers"], D28, YB)

    by = defaultdict(lambda: defaultdict(float))
    for r in daily:
        d = str(r.get("date", ""))[:10]
        for k in ("sessions", "totalusers", "newusers", "conversions"):
            by[d][k] += n(r.get(k))
    days = [str(D40 + timedelta(days=i)) for i in range(40)]
    d28 = [d for d in days if d >= str(D28)]
    tot = {k: sum(by[d][k] for d in d28) for k in ("sessions", "totalusers", "newusers", "conversions")}
    if tot["sessions"] <= 0 or str(YB) not in by:
        sys.exit("GA4データが空、または前日分が未反映。資料を更新しません")

    dv = defaultdict(lambda: defaultdict(float))
    for r in dev:
        k = str(r.get("devicecategory", "")).lower()
        for m in ("sessions", "totalusers", "newusers"):
            dv[k][m] += n(r.get(m))
    cs = defaultdict(lambda: defaultdict(float))
    for r in ctry:
        k = str(r.get("country", ""))
        for m in ("sessions", "totalusers"):
            cs[k][m] += n(r.get(m))
    pg = defaultdict(lambda: defaultdict(float))
    for r in pages:
        k = str(r.get("pagetitle", "")).strip()
        if not k or k == "(not set)":
            continue
        for m in ("sessions", "totalusers", "newusers"):
            pg[k][m] += n(r.get(m))

    S = tot["sessions"]
    L = []
    L.append("# GA4実測データ：toyota.jp（本格移行用 / account 324699885）")
    L.append("")
    L.append(f"- 区分: 共有可。**実測データ**（デモ・推定ではない）。出典: GA4「Toyota.jp【本格移行用】- GA4」を Windsor.ai 経由で取得（GitHub Actions ga4-daily が毎朝自動更新）")
    L.append(f"- 取得日: {TODAY}（データ終端 {YB}）。**この数値を答えるときの出典は「GA4実測（toyota.jp本格移行用）／取得 {TODAY}」と明記する**")
    L.append("- 指標の意味: セッション=訪問数、総ユーザー=UU、新規ユーザー=初訪問UU、キーイベント(CV)=重要イベント数（GA4の\"conversions\"）")
    L.append("- どのチャンネルでも実数で回答してよい。数値は原文ママ、捏造しない")
    L.append("")
    L.append(f"## 全体サマリー（直近28日：{D28}〜{YB}）")
    L.append(f"- セッション: **{fmt(S)}**（1日平均 約{fmt(S/28)}）")
    L.append(f"- 総ユーザー(UU): **{fmt(tot['totalusers'])}**")
    L.append(f"- 新規ユーザー: **{fmt(tot['newusers'])}**（新規率 約{tot['newusers']/max(tot['totalusers'],1)*100:.1f}%）")
    L.append(f"- キーイベント(CV): **{fmt(tot['conversions'])}**（対セッションCV率 約{tot['conversions']/max(S,1)*100:.1f}%）")
    L.append("")
    L.append("## デバイス別（直近28日・セッション）")
    for k, v in sorted(dv.items(), key=lambda kv: -kv[1]["sessions"]):
        L.append(f"- {DEVICE_JP.get(k, k)}: {fmt(v['sessions'])}（{v['sessions']/max(S,1)*100:.1f}%）／ UU {fmt(v['totalusers'])}・新規 {fmt(v['newusers'])}")
    L.append("")
    L.append("## 国別（直近28日・セッション・上位）")
    for k, v in sorted(cs.items(), key=lambda kv: -kv[1]["sessions"])[:10]:
        L.append(f"- {COUNTRY_JP.get(k, k)}: {fmt(v['sessions'])}（{v['sessions']/max(S,1)*100:.1f}%）／ UU {fmt(v['totalusers'])}")
    L.append("")
    L.append("## 主要ページ／車種別（直近28日・セッション上位40）")
    L.append("※「TOYOTAアカウント」系はログイン/認証フロー。車種ページは購買検討の指標")
    L.append("| セッション | UU | 新規 | ページ |")
    L.append("|---:|---:|---:|---|")
    for k, v in sorted(pg.items(), key=lambda kv: -kv[1]["sessions"])[:40]:
        L.append(f"| {fmt(v['sessions'])} | {fmt(v['totalusers'])} | {fmt(v['newusers'])} | {k[:60]} |")
    L.append("")
    L.append(f"## 日次推移（直近40日：{D40}〜{YB}）")
    L.append("| 日付 | セッション | UU | 新規 | CV |")
    L.append("|---|---:|---:|---:|---:|")
    for d in days:
        v = by.get(d, {})
        L.append(f"| {d[5:]} | {fmt(v.get('sessions',0))} | {fmt(v.get('totalusers',0))} | {fmt(v.get('newusers',0))} | {fmt(v.get('conversions',0))} |")
    L.append("")
    L.append("## この資料の使い方（データマン向け）")
    L.append("- 「今月/直近のセッション・UU・CV」「デバイス比率」「国別」「車種別（アルファード等）」「日別推移」はこの資料の実数で答える")
    L.append("- ここに無い切り口（流入元/チャネル別、任意期間の集計等）は「未取得」と正直に伝え、取得方法（Windsor.ai／GA4）を添える。憶測で埋めない")
    L.append("- この資料は毎朝自動更新（GitHub Actions）。日付が古い場合はその旨添える")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {OUT}: sessions28d={fmt(S)} pages={len(pg)} days={len(days)}")


if __name__ == "__main__":
    main()
