#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toyota.jp GA4実測（Windsor.ai経由・account 324699885）→ データマン用の日本語資料・CSV と、GEOボード用のAI流入日次を作る。

GitHub Actions（ga4-daily）で毎朝実行する。Mac側のデータマンはこのリポジトリを pull して取り込む。

出力（既定はリポジトリの data/ 配下。GA4_OUT_DIR を指定するとそこへ出す＝試験実行）
  ga4/toyota_jp_ga4.md          データマンが読む日本語資料
  ga4/toyota_jp_ga4_daily.csv   日次（約25か月分）。任意期間の集計用
  ga4/toyota_jp_ga4_monthly.csv 月次（UUは月内の重複を除いた値）
  ga4/ai_referrals_daily.csv    AIアシスタント経由セッションの日次×サービス
  ga4/last_check.txt            自己点検の結果
  ga4_daily.json                GEOボード用のAI経由セッション日次（確定値で毎朝上書き）

守っていること（2026-10-09 の点検で見つかった問題の再発防止）
  1. UUは足し算しない。28日・365日・各月のUUは、それぞれの期間で集計した値を別に取る
     （以前は日次UUを足して「28日UU」にしており、実際より44.6%多かった）
  2. 長い期間は月ごとに分けて取り、日付の抜けを照合する。抜けた日は1日単位で取り直す
     （1年まとめて取ると、ある日の行だけが黙って欠けることがあった）
  3. 打ち切り・欠損・不整合は資料と last_check.txt に必ず書く。致命的なものは終了コード1で止め、古い資料を上書きしない
  4. AIらしいのにどのサービスにも入らない流入元を毎朝探し、last_check.txt に書く（9月の gemini 表記の見落としの再発防止）
  5. 月次で前月から35%以上動いた月は資料に明記する（計測の変更か実際の変化かを確かめずに前年比を外へ出さない）
  6. 毎日の取得は差分だけ（日次は直近40日と抜けのある月、AI流入は直近10日と抜けのある月）。毎月1日と判定規則の変更時は全期間を取り直す

環境変数
  WINDSOR_API_KEY       必須（GitHub Secrets）
  GA4_MONTHS            月次・日次CSVの月数（既定25＝前年同月比と2年分）
  GA4_DAILY_DAYS        資料に載せる日次の日数（既定40）
  GA4_AI_REFRESH_DAYS   AI流入を毎回取り直す直近日数（既定10。速報値を確定値で上書きするため）
  GA4_OUT_DIR           試験用の出力先
  GA4_FULL=1            保存済みの日次を使わず全期間を取り直す（毎月1日は自動で全期間）
  GA4_MOCK=1            ネットに出ずに合成データで動かす（コードの動作確認用）
"""
from __future__ import annotations

import csv
import io
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_sources import (RULES_VERSION, SERVICES, classify, hint_filter, unclassified_ai_like,  # noqa: E402
                        windsor_filter)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACC = "324699885"
BASE = "https://connectors.windsor.ai/googleanalytics4"
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).date()
YB = TODAY - timedelta(days=1)                     # 前日（データ終端）
MOCK = os.environ.get("GA4_MOCK") == "1"
KEY = os.environ.get("WINDSOR_API_KEY", "")
MONTHS = max(13, int(os.environ.get("GA4_MONTHS", "25")))
DAILY_DAYS = int(os.environ.get("GA4_DAILY_DAYS", "40"))
AI_REFRESH_DAYS = int(os.environ.get("GA4_AI_REFRESH_DAYS", "10"))
OUT_DIR = os.environ.get("GA4_OUT_DIR") or os.path.join(ROOT, "data")
MAX_ROWS = 200000
FULL = os.environ.get("GA4_FULL") == "1" or TODAY.day == 1
HINT_MIN = 20                                       # 見落とし点検：直近7日でこのセッション数以上なら知らせる
STEP = 0.35                                         # 月次の段差：前月比でこれ以上動いたら資料に書く

D28 = YB - timedelta(days=27)
D365 = YB - timedelta(days=364)
DD = YB - timedelta(days=DAILY_DAYS - 1)

METRICS = ["sessions", "totalusers", "newusers", "conversions", "screen_page_views"]

COUNTRY_JP = {"Japan": "日本", "Singapore": "シンガポール", "United States": "米国", "Taiwan": "台湾", "China": "中国",
              "Hong Kong": "香港", "Thailand": "タイ", "South Korea": "韓国", "Brazil": "ブラジル", "France": "フランス",
              "Philippines": "フィリピン", "Australia": "オーストラリア", "Vietnam": "ベトナム", "Indonesia": "インドネシア",
              "United Kingdom": "英国", "Germany": "ドイツ", "Malaysia": "マレーシア", "Canada": "カナダ", "India": "インド",
              "Sri Lanka": "スリランカ", "Macau": "マカオ"}
DEVICE_JP = {"mobile": "モバイル", "desktop": "PC(デスクトップ)", "tablet": "タブレット", "smart tv": "スマートTV",
             "smarttv": "スマートTV", "(other)": "その他（GA4がまとめた行）"}
SVC_JP = {"chatgpt": "ChatGPT", "gemini": "Gemini", "copilot": "Copilot", "perplexity": "Perplexity",
          "claude": "Claude", "grok": "Grok", "deepseek": "DeepSeek"}

WARN: list[str] = []      # 資料と点検結果に書く注意
FAIL: list[str] = []      # 致命的。資料を更新しない
NOTE: list[str] = []      # 点検で確認できたこと
OWNER: list[str] = []     # けんけんさんだけに知らせる注意（資料には書かない。last_check.txt の WARN 行になる）


# ---------------------------------------------------------------- 取得
def _mock(fields, dfrom, dto, flt):
    """合成データ（GA4_MOCK=1）。日付の抜けや (other) も混ぜて分岐を通す。"""
    rnd = random.Random(f"{fields}{dfrom}{dto}{flt}")
    d0, d1 = date.fromisoformat(str(dfrom)), date.fromisoformat(str(dto))
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]

    def m(scale=1.0):
        s = int(rnd.uniform(300000, 700000) * scale)
        return {"sessions": s, "totalusers": int(s * 0.8), "newusers": int(s * 0.35),
                "conversions": int(s * 0.14), "screen_page_views": int(s * 3.1)}
    rows = []
    if "date" in fields and "source" in fields:
        for d in days:
            if d.day == 29 and len(days) > 3:     # 長い期間では29日を黙って落とす（実際に起きた欠け方の再現）
                continue
            for src, base in (("chatgpt.com", 700), ("openai", 650), ("gemini", 30), ("gemini.google.com", 20),
                              ("copilot.com", 12), ("perplexity", 3), ("claude.ai", 1), ("wbtxa.gemini.oneoffice.jp", 2)):
                rows.append({"date": str(d), "source": src, "sessions": int(base * rnd.uniform(0.8, 1.2))})
            if not flt:
                rows.append({"date": str(d), "source": "google", "sessions": 150000})
    elif "source" in fields:
        for src, c in (("chatgpt.com", 5000), ("gemini", 200), ("felo.ai", 25), ("kagi.com", 18), ("notebooklm.google.com", 4),
                       ("tagassistant.google.com", 30), ("u.email.openai.com", 2)):
            rows.append({"source": src, "sessions": c})
    elif "date" in fields:
        for d in days:
            if d.day == 29 and len(days) > 3 and d.month % 3 == 0:
                continue
            rows.append({"date": str(d), **m()})
    elif "year_month" in fields:
        ym = sorted({d.strftime("%Y%m") for d in days})
        for y in ym:
            n = sum(1 for d in days if d.strftime("%Y%m") == y)
            r = m(n * 0.95)
            r["totalusers"] = int(r["sessions"] * 0.55)
            rows.append({"year_month": y, **r})
    elif "devicecategory" in fields:
        for k, f in (("mobile", .74), ("desktop", .22), ("tablet", .013), ("(other)", .0005), ("smart tv", .00002)):
            r = m(len(days) * f)
            r["totalusers"] = int(r["sessions"] * 0.45)
            rows.append({"devicecategory": k, **r})
    elif "country" in fields:
        for k, f in (("Japan", .97), ("United States", .003), ("Taiwan", .002), ("(other)", .00001)):
            rows.append({"country": k, **m(len(days) * f)})
    elif "pagetitle" in fields:
        for i in range(120):
            r = m(len(days) * 0.05 / (i + 1))
            rows.append({"pagetitle": f"トヨタ 車種{i} | トヨタ自動車WEBサイト", **r})
        rows.append({"pagetitle": "(other)", **m(0.0001)})
    else:
        r = m(len(days))
        r["totalusers"] = int(r["sessions"] * 0.56)
        rows.append(r)
    if flt and "sessions" in json.dumps(flt):
        lim = [x for x in flt if isinstance(x, list) and x[0] == "sessions"]
        if lim:
            rows = [r for r in rows if r.get("sessions", 0) > lim[0][2]]
    return [{k: v for k, v in r.items() if k in fields or k in ("date",)} for r in rows]


def windsor(fields, dfrom, dto, flt=None, max_rows=MAX_ROWS, tries=3):
    """Windsor.ai から行を取る。失敗は例外。打ち切りの疑いは WARN に書く。"""
    fields = list(fields)
    if MOCK:
        return _mock(fields, dfrom, dto, flt)
    p = {"api_key": KEY, "select_accounts": ACC, "_renderer": "json", "_max_rows": str(max_rows),
         "fields": ",".join(fields), "date_from": str(dfrom), "date_to": str(dto)}
    if flt:
        p["filter"] = json.dumps(flt, ensure_ascii=False)
    url = BASE + "?" + urllib.parse.urlencode(p)
    last = None
    for att in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                data = json.loads(r.read().decode("utf-8"))
            rows = data.get("data", data if isinstance(data, list) else [])
            rows = [x for x in rows if str(x.get("account_id", ACC)) == ACC]
            if len(rows) >= max_rows:
                WARN.append(f"取得件数が上限 {max_rows} 行に達した（{','.join(fields)} {dfrom}〜{dto}）。打ち切りの可能性")
            return rows
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(8 * (att + 1))
    raise RuntimeError(f"Windsor取得失敗 {','.join(fields)} {dfrom}..{dto}: {str(last)[:200]}")


def n(x):
    try:
        f = float(x or 0)
        return int(f) if f.is_integer() else round(f, 2)
    except (TypeError, ValueError):
        return 0


def month_start(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def month_end(d: date) -> date:
    nxt = month_start(d, -1) if d.month < 12 else date(d.year + 1, 1, 1)
    return nxt - timedelta(days=1)


def month_chunks(d0: date, d1: date):
    cur = date(d0.year, d0.month, 1)
    while cur <= d1:
        a, b = max(cur, d0), min(month_end(cur), d1)
        yield a, b
        cur = month_end(cur) + timedelta(days=1)


def daterange(d0: date, d1: date):
    return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]


def fetch_daily(d0: date, d1: date):
    """日次の指標を月ごとに取り、抜けた日は1日単位で取り直す。"""
    by: dict[str, dict] = {}
    for a, b in month_chunks(d0, d1):
        for r in windsor(["date"] + METRICS, a, b):
            d = str(r.get("date", ""))[:10]
            if d:
                by[d] = {k: n(r.get(k)) for k in METRICS}
    missing = [str(d) for d in daterange(d0, d1) if str(d) not in by]
    refetched = []
    for d in missing[:40]:
        rows = windsor(["date"] + METRICS, d, d)
        if rows:
            by[d] = {k: n(rows[0].get(k)) for k in METRICS}
            refetched.append(d)
    still = [d for d in missing if d not in by]
    if refetched:
        NOTE.append(f"日次：月ごとの取得で抜けた {len(refetched)} 日を1日単位で取り直した（{', '.join(refetched[:8])}{' ほか' if len(refetched) > 8 else ''}）")
    if still:
        WARN.append(f"日次の欠損 {len(still)} 日：{', '.join(still[:12])}{' ほか' if len(still) > 12 else ''}（0とはみなさない）")
    return by, still


def load_daily_csv() -> dict:
    """前回までの日次CSV（リポジトリの data/ga4/）。読めない・列が足りないときは空＝全期間を取り直す。"""
    p = os.path.join(ROOT, "data", "ga4", "toyota_jp_ga4_daily.csv")
    try:
        with open(p, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:  # noqa: BLE001
        return {}
    if not rows or any(k not in rows[0] for k in ["date"] + METRICS):
        return {}
    return {r["date"]: {k: n(r.get(k)) for k in METRICS} for r in rows if r.get("date")}


def fetch_ai(d0: date, d1: date, use_filter: bool = True):
    """AIアシスタント経由セッションを日次×サービスで取る（月ごと・抜けは1日単位で取り直す）。"""
    flt = windsor_filter()
    by: dict[str, list] = defaultdict(list)
    for a, b in month_chunks(d0, d1):
        try:
            rows = windsor(["date", "source", "sessions"], a, b, flt if use_filter else None)
        except RuntimeError:
            if not use_filter:
                raise
            use_filter = False
            NOTE.append("AI流入：絞り込み付きの取得が失敗したため、絞り込みなしで取得した")
            rows = windsor(["date", "source", "sessions"], a, b)
        for r in rows:
            d = str(r.get("date", ""))[:10]
            if d:
                by[d].append((r.get("source"), r.get("sessions", 0)))
    missing = [str(d) for d in daterange(d0, d1) if str(d) not in by]
    refetched = []
    for d in missing[:40]:
        rows = windsor(["date", "source", "sessions"], d, d, flt if use_filter else None)
        if rows:
            by[d] = [(r.get("source"), r.get("sessions", 0)) for r in rows]
            refetched.append(d)
    still = [d for d in missing if d not in by]
    if refetched:
        NOTE.append(f"AI流入：月ごとの取得で抜けた {len(refetched)} 日を1日単位で取り直した（{', '.join(refetched[:8])}）")
    if still:
        WARN.append(f"AI流入の欠損 {len(still)} 日：{', '.join(still[:12])}（0とはみなさず、以前の値があれば残す）")
    return {d: classify(p) for d, p in by.items()}, still, use_filter


def aggregate(d0: date, d1: date):
    rows = windsor(METRICS, d0, d1)
    if not rows:
        return None
    return {k: sum(n(r.get(k)) for r in rows) for k in METRICS}


# ---------------------------------------------------------------- 書式
def fmt(x):
    return f"{int(round(x)):,}"


def pct(a, b):
    return f"{(a / b - 1) * 100:+.1f}%" if b else "—"


def esc(s: str) -> str:
    """表のセル用。「|」で列がずれないように全角へ。"""
    return str(s).replace("|", "｜").replace("\n", " ").strip()


def out_path(*parts):
    p = os.path.join(OUT_DIR, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


# ---------------------------------------------------------------- 本体
def main():
    if not KEY and not MOCK:
        sys.exit("WINDSOR_API_KEY 未設定")
    m0 = month_start(YB, MONTHS - 1)              # 月次・日次CSVの開始（月初）

    # --- 取得 ---
    # 日次：保存済みのCSVがあれば、直近40日と抜けのある月だけ取り直す（毎月1日・GA4_FULL=1 は全期間）
    old = {} if FULL else load_daily_csv()
    recent_from = max(m0, YB - timedelta(days=max(DAILY_DAYS, 40) - 1))
    if old:
        holes = [(a, b) for a, b in month_chunks(m0, recent_from - timedelta(days=1))
                 if any(str(d) not in old for d in daterange(a, b))]
        ranges = holes + [(recent_from, YB)]
        daily = {d: v for d, v in old.items() if str(m0) <= d < str(recent_from)}
        NOTE.append(f"日次：保存済み {len(daily)} 日はそのまま使い、直近 {(YB - recent_from).days + 1} 日"
                    + (f"と抜けのある {len(holes)} か月" if holes else "") + "を取り直した（毎月1日は全期間を取り直す）")
    else:
        ranges = [(m0, YB)]
        daily = {}
        NOTE.append("日次：全期間を取り直した" + ("（毎月1日または GA4_FULL=1）" if FULL else "（保存済みのCSVが無い・読めない）"))
    daily_missing: list[str] = []
    for a, b in ranges:
        got, miss = fetch_daily(a, b)
        daily.update(got)
        daily_missing += miss
    agg28 = aggregate(D28, YB)
    agg365 = aggregate(D365, YB)
    monthly_rows = windsor(["year_month"] + METRICS, m0, YB)
    dev = windsor(["devicecategory", "sessions", "totalusers", "newusers"], D28, YB)
    ctry = windsor(["country", "sessions", "totalusers"], D28, YB)
    try:
        pages = windsor(["pagetitle", "sessions", "totalusers", "newusers"], D28, YB, [["sessions", "gt", 1000]])
    except RuntimeError:
        pages = windsor(["pagetitle", "sessions", "totalusers", "newusers"], D28, YB)
        NOTE.append("ページ別（28日）：絞り込みが使えなかったため全件取得")
    try:
        pages365 = windsor(["pagetitle", "sessions", "totalusers"], D365, YB, [["sessions", "gt", 100000]])
    except RuntimeError as e:
        pages365 = []
        WARN.append(f"ページ別（365日）の取得に失敗：{str(e)[:120]}")

    # AI流入：まず前日分で「絞り込みあり」と「絞り込みなし」を比べ、絞り込みが正しく効くか確かめる
    flt_ok = True
    full_yb = None
    try:
        raw = windsor(["date", "source", "sessions"], YB, YB)
        full_yb = classify((r.get("source"), r.get("sessions", 0)) for r in raw)
        try:
            fr = windsor(["date", "source", "sessions"], YB, YB, windsor_filter())
            filt_yb = classify((r.get("source"), r.get("sessions", 0)) for r in fr)
        except RuntimeError:
            filt_yb = None
        if filt_yb is None or filt_yb != full_yb:
            flt_ok = False
            NOTE.append(f"AI流入：絞り込みの結果が絞りなしと一致しないため（あり {filt_yb} ／なし {full_yb}）、絞り込みなしで取得")
        else:
            NOTE.append(f"AI流入の点検：前日の値は絞り込みあり・なしで一致（合計 {sum(full_yb.values())}）")
    except RuntimeError as e:
        WARN.append(f"AI流入の点検に失敗：{str(e)[:120]}")

    # 既存の ga4_daily.json に抜けがある月は丸ごと、直近は毎回取り直す（前日分は速報値のため）
    gpath = os.path.join(ROOT, "data", "ga4_daily.json")
    try:
        gd = json.load(open(gpath, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        gd = {}
    old_rules = (gd.get("_meta") or {}).get("rules")
    if gd and old_rules != RULES_VERSION:
        NOTE.append(f"AI流入：判定規則が変わったため（{old_rules or '旧規則'} → {RULES_VERSION}）、{m0}以降を全て取り直した")
        gd = {}
    have = {k for k in gd if not k.startswith("_")}
    need_months = [(a, b) for a, b in month_chunks(m0, YB) if any(str(d) not in have for d in daterange(a, b))]
    refresh_from = YB - timedelta(days=AI_REFRESH_DAYS - 1)
    ai: dict[str, dict] = {}
    ai_missing: list[str] = []
    for a, b in need_months + [(refresh_from, YB)]:
        got, miss, _ = fetch_ai(a, b, flt_ok)
        ai.update(got)
        ai_missing += [d for d in miss if d not in got]
    if full_yb is not None:
        ai[str(YB)] = full_yb
    ai_missing = sorted(set(d for d in ai_missing if d not in ai))

    # 見落としの点検：AIらしいのにどのサービスにも入っていない流入元（直近7日）
    d7 = YB - timedelta(days=6)
    try:
        try:
            hrows = windsor(["source", "sessions"], d7, YB, hint_filter())
        except RuntimeError:
            hrows = windsor(["source", "sessions"], d7, YB)
        cand = unclassified_ai_like((r.get("source"), r.get("sessions", 0)) for r in hrows)
        big = [(s_, c) for s_, c in cand if c >= HINT_MIN]
        small = [(s_, c) for s_, c in cand if c < HINT_MIN]
        if big:
            OWNER.append(f"AIらしいのに分類されていない流入元（{d7}〜{YB}・{HINT_MIN}セッション以上）："
                         + "、".join(f"{s_} {c}" for s_, c in big[:8])
                         + "。src/ai_sources.py の SVC_KEYS（数える）か KNOWN_OTHER（数えない）に追加するか判断が要る")
        NOTE.append("AI流入元の見落とし点検（直近7日）："
                    + ("、".join(f"{s_} {c}" for s_, c in small[:6]) + f" は{HINT_MIN}未満のため様子見" if small else "該当なし")
                    + ("" if not big else f"／{HINT_MIN}以上 {len(big)} 件は上の WARN を参照"))
    except RuntimeError as e:
        OWNER.append(f"AI流入元の見落とし点検に失敗：{str(e)[:120]}")

    # --- 致命的な点検（失敗したら資料を更新しない） ---
    if not daily or str(YB) not in daily:
        FAIL.append("前日分の日次データが無い")
    if not agg28 or agg28["sessions"] <= 0:
        FAIL.append("直近28日の集計が空")
    if not monthly_rows:
        FAIL.append("月次の取得が空")
    d28 = [str(d) for d in daterange(D28, YB)]
    if agg28 and daily and not FAIL:
        sum_uu = sum(daily[d]["totalusers"] for d in d28 if d in daily)
        max_uu = max((daily[d]["totalusers"] for d in d28 if d in daily), default=0)
        if not (max_uu <= agg28["totalusers"] <= sum_uu):
            FAIL.append(f"28日UUが範囲外（集計 {agg28['totalusers']} ／ 日次最大 {max_uu} ／ 日次合計 {sum_uu}）")
        else:
            NOTE.append(f"28日UU：期間集計 {fmt(agg28['totalusers'])} を採用（日次UUを足すと {fmt(sum_uu)}＝{pct(sum_uu, agg28['totalusers'])} 多くなる）")
    if FAIL:
        write_check()
        print("\n".join("FAIL: " + x for x in FAIL))
        sys.exit(1)

    # --- 月次 ---
    mon = {}
    for r in monthly_rows:
        ym = str(r.get("year_month", ""))
        if len(ym) == 6:
            mon[ym] = {k: n(r.get(k)) for k in METRICS}
    want = []
    cur = m0
    while cur <= YB:
        want.append(cur.strftime("%Y%m"))
        cur = month_end(cur) + timedelta(days=1)
    for ym in [y for y in want if y not in mon]:
        a = date(int(ym[:4]), int(ym[4:]), 1)
        got = aggregate(a, min(month_end(a), YB))
        if got:
            mon[ym] = got
            NOTE.append(f"月次：{ym} が抜けたため単独で取り直した")
        else:
            WARN.append(f"月次の欠損：{ym}")
    # 日次の合計と月次の整合（日をまたぐ訪問は両日に数えられるため、日次合計の方がわずかに多いのが普通）
    for ym, v in mon.items():
        ds = [d for d in daily if d.replace("-", "")[:6] == ym]
        s = sum(daily[d]["sessions"] for d in ds)
        if v["sessions"] and ds:
            ratio = s / v["sessions"]
            if not 0.97 <= ratio <= 1.10:
                WARN.append(f"月次と日次のセッションの差が大きい：{ym} 月次 {fmt(v['sessions'])} ／日次合計 {fmt(s)}（{ratio:.3f}倍）")

    # --- AI流入を ga4_daily.json に反映 ---
    for d, v in ai.items():
        gd[d] = v
    keys = sorted(k for k in gd if not k.startswith("_"))
    gd_out = {"_meta": {
        "source": "GA4 property 324699885 (toyota.jp) via Windsor.ai（ga4-daily が毎朝更新）",
        "fetched": str(TODAY),
        "coverage": [keys[0], keys[-1]] if keys else None,
        "refresh": f"直近{AI_REFRESH_DAYS}日は毎朝取り直し（前日分は速報値のため）",
        "rules": RULES_VERSION,
        "missing_days": ai_missing,
        "note": "値は各日のセッション実数。サービス判定は src/ai_sources.py。",
    }}
    for k in keys:
        gd_out[k] = {svc: int(gd[k].get(svc, 0)) for svc in SERVICES}

    # --- 書き出し ---
    write_csvs(daily, mon, gd_out, m0)
    with open(out_path("ga4_daily.json"), "w", encoding="utf-8") as f:
        json.dump(gd_out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    md = build_md(daily, agg28, agg365, mon, dev, ctry, pages, pages365, gd_out, daily_missing, m0)
    with open(out_path("ga4", "toyota_jp_ga4.md"), "w", encoding="utf-8") as f:
        f.write(md)
    write_check()
    print(f"wrote {OUT_DIR}: sessions28d={fmt(agg28['sessions'])} uu28d={fmt(agg28['totalusers'])} "
          f"months={len(mon)} daily={len(daily)} ai_days={len(keys)} warn={len(WARN)}")


def write_csvs(daily, mon, gd_out, m0):
    with open(out_path("ga4", "toyota_jp_ga4_daily.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + METRICS)
        for d in sorted(daily):
            if d >= str(m0):
                w.writerow([d] + [daily[d][k] for k in METRICS])
    with open(out_path("ga4", "toyota_jp_ga4_monthly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["year_month"] + METRICS + ["partial_until"])
        for ym in sorted(mon):
            part = str(YB) if ym == YB.strftime("%Y%m") and YB != month_end(YB) else ""
            w.writerow([ym] + [mon[ym][k] for k in METRICS] + [part])
    with open(out_path("ga4", "ai_referrals_daily.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + SERVICES + ["total"])
        for d in sorted(k for k in gd_out if not k.startswith("_")):
            v = gd_out[d]
            w.writerow([d] + [v[s] for s in SERVICES] + [sum(v[s] for s in SERVICES)])


def build_md(daily, agg28, agg365, mon, dev, ctry, pages, pages365, gd_out, daily_missing, m0):
    S = agg28["sessions"]
    L = []
    a = L.append
    a("# GA4実測データ：toyota.jp（本格移行用 / account 324699885）")
    a("")
    a("- 区分: 共有可。**実測データ**（デモ・推定ではない）。出典: GA4「Toyota.jp【本格移行用】- GA4」を Windsor.ai 経由で取得（GitHub Actions ga4-daily が毎朝自動更新）")
    a(f"- 取得日: {TODAY}（データ終端 {YB}）。**この数値を答えるときの出典は「GA4実測（toyota.jp）M/D〜M/D／{TODAY.month}/{TODAY.day}取得」の形で、答えに使った期間と取得日を明記する**")
    a(f"- 期間の早見: 28日集計 {D28}〜{YB} ／ 365日集計 {D365}〜{YB} ／ 月次 {m0.strftime('%Y-%m')}〜{YB.strftime('%Y-%m')}（{len(mon)}か月）／ 日次の表 {DD}〜{YB}（{DAILY_DAYS}日）／ 日次CSV {m0}〜{YB}")
    a("- 指標の意味: セッション=訪問数、UU=総ユーザー、新規=初訪問のユーザー、PV=表示回数（screen_page_views）、CV=キーイベント（GA4の\"conversions\"）")
    a("- **UUは足し算しない**: 28日・365日・各月のUUは、その期間で重複を除いて集計した値。日次や月次のUUを足して別の期間のUUにしない（足すと同じ人を何度も数える）。セッション・新規・PV・CVは足してよい")
    a("- どのチャンネルでも実数で回答してよい。数値は原文ママ、捏造しない")
    a("")
    a(f"## 全体サマリー（直近28日：{D28}〜{YB}）")
    a(f"- セッション: **{fmt(S)}**（1日平均 約{fmt(S / 28)}）")
    a(f"- UU（28日間の重複を除いた値）: **{fmt(agg28['totalusers'])}**")
    a(f"- 新規ユーザー: **{fmt(agg28['newusers'])}**（UUに占める新規の割合 約{agg28['newusers'] / max(agg28['totalusers'], 1) * 100:.1f}%）")
    a(f"- PV: **{fmt(agg28['screen_page_views'])}**")
    a(f"- キーイベント(CV): **{fmt(agg28['conversions'])}**（対セッションCV率 約{agg28['conversions'] / max(S, 1) * 100:.1f}%）")
    if agg365:
        a("")
        a(f"## 直近365日の合計（{D365}〜{YB}）")
        a(f"- セッション {fmt(agg365['sessions'])} ／ UU（365日間の重複を除いた値）{fmt(agg365['totalusers'])} ／ 新規 {fmt(agg365['newusers'])} ／ PV {fmt(agg365['screen_page_views'])} ／ CV {fmt(agg365['conversions'])}")
    a("")
    a("## デバイス別（直近28日・セッション）")
    dv = defaultdict(lambda: defaultdict(float))
    for r in dev:
        k = str(r.get("devicecategory", "")).lower()
        for m in ("sessions", "totalusers", "newusers"):
            dv[k][m] += n(r.get(m))
    for k, v in sorted(dv.items(), key=lambda kv: -kv[1]["sessions"]):
        a(f"- {DEVICE_JP.get(k, k)}: {fmt(v['sessions'])}（{v['sessions'] / max(S, 1) * 100:.1f}%）／ UU {fmt(v['totalusers'])}・新規 {fmt(v['newusers'])}")
    a("")
    a("## 国別（直近28日・セッション・上位10）")
    cs = defaultdict(lambda: defaultdict(float))
    for r in ctry:
        k = str(r.get("country", ""))
        for m in ("sessions", "totalusers"):
            cs[k][m] += n(r.get(m))
    for k, v in sorted(cs.items(), key=lambda kv: -kv[1]["sessions"])[:10]:
        a(f"- {COUNTRY_JP.get(k, k)}: {fmt(v['sessions'])}（{v['sessions'] / max(S, 1) * 100:.1f}%）／ UU {fmt(v['totalusers'])}")
    a("")

    def page_table(rows, title, cols3, top=40):
        pg = defaultdict(lambda: defaultdict(float))
        for r in rows:
            k = str(r.get("pagetitle", "")).strip()
            if not k or k in ("(not set)",):
                continue
            for m in ("sessions", "totalusers", "newusers"):
                pg[k][m] += n(r.get(m))
        a(title)
        a("※「TOYOTAアカウント」系はログイン/認証フロー。車種ページは購買検討の指標。（other）はGA4が少ない行をまとめたもの")
        if cols3:
            a("| セッション | UU | 新規 | ページ |")
            a("|---:|---:|---:|---|")
            for k, v in sorted(pg.items(), key=lambda kv: -kv[1]["sessions"])[:top]:
                a(f"| {fmt(v['sessions'])} | {fmt(v['totalusers'])} | {fmt(v['newusers'])} | {esc(k)[:70]} |")
        else:
            a("| セッション | UU | ページ |")
            a("|---:|---:|---|")
            for k, v in sorted(pg.items(), key=lambda kv: -kv[1]["sessions"])[:top]:
                a(f"| {fmt(v['sessions'])} | {fmt(v['totalusers'])} | {esc(k)[:70]} |")
        a("")
    page_table(pages, "## 主要ページ／車種別（直近28日・セッション上位40）", True)
    if pages365:
        page_table(pages365, f"## 主要ページ／車種別（直近365日：{D365}〜{YB}・セッション上位30）", False, 30)

    a(f"## 月次推移（{m0.strftime('%Y-%m')}〜{YB.strftime('%Y-%m')}。UUは月内の重複を除いた値＝月をまたいで足さない）")
    a("| 年月 | セッション | 月間UU | 新規 | PV | CV |")
    a("|---|---:|---:|---:|---:|---:|")
    for ym in sorted(mon):
        v = mon[ym]
        lab = f"{ym[:4]}-{ym[4:]}" + (f"（{YB.month}/{YB.day}まで）" if ym == YB.strftime("%Y%m") and YB != month_end(YB) else "")
        a(f"| {lab} | {fmt(v['sessions'])} | {fmt(v['totalusers'])} | {fmt(v['newusers'])} | {fmt(v['screen_page_views'])} | {fmt(v['conversions'])} |")
    a("")
    a("## 前年同月比（完了した月のみ）")
    a("| 年月 | セッション | 月間UU | PV | CV |")
    a("|---|---:|---:|---:|---:|")
    big = []
    for ym in sorted(mon):
        y0 = f"{int(ym[:4]) - 1}{ym[4:]}"
        if y0 not in mon or ym == YB.strftime("%Y%m"):
            continue
        v, p = mon[ym], mon[y0]
        a(f"| {ym[:4]}-{ym[4:]} | {pct(v['sessions'], p['sessions'])} | {pct(v['totalusers'], p['totalusers'])} | {pct(v['screen_page_views'], p['screen_page_views'])} | {pct(v['conversions'], p['conversions'])} |")
        if p["sessions"] and abs(v["sessions"] / p["sessions"] - 1) >= 0.3:
            big.append(f"{ym[:4]}-{ym[4:]}")
    if big:
        a("")
        a(f"※ セッションが前年同月から3割以上動いている月: {', '.join(big)}。原因（計測方法の変更など）はこの資料では確かめていない。前年比を伝えるときはこの点を添える")
    steps = month_steps(mon)
    if steps:
        a("")
        a(f"※ 前月から{int(STEP * 100)}%以上動いた月（計測設定の変更か実際の変化かは、この資料では確かめていない。前年比や期間の比較がこの月をまたぐときは、その旨を添える）")
        for line in steps:
            a(f"- {line}")
    a("")
    a(f"## 日次推移（直近{DAILY_DAYS}日：{DD}〜{YB}。UUはその日のUU）")
    a("| 日付 | セッション | UU | 新規 | PV | CV |")
    a("|---|---:|---:|---:|---:|---:|")
    for d in daterange(DD, YB):
        v = daily.get(str(d))
        if v:
            a(f"| {str(d)[5:]} | {fmt(v['sessions'])} | {fmt(v['totalusers'])} | {fmt(v['newusers'])} | {fmt(v['screen_page_views'])} | {fmt(v['conversions'])} |")
        else:
            a(f"| {str(d)[5:]} | 欠損 | 欠損 | 欠損 | 欠損 | 欠損 |")
    a("")

    days = sorted(k for k in gd_out if not k.startswith("_"))
    if days:
        a("## AIアシスタント経由のセッション")
        a(f"- 日次×サービス（ChatGPT／Gemini／Copilot／Perplexity／Claude／Grok／DeepSeek）の実測を {days[0]}〜{days[-1]} で毎朝更新。表はダッシュボード資料「GA4 AI経由セッション（日次）」、計算用は ai_referrals_daily.csv")
        a("")
    a("## データの注意（自動点検の結果）")
    a("- 前日分は速報値。GA4は処理に24〜48時間かかり、その間に数字が増えることがある（直近1〜2日は低めに出る）")
    a("- 毎朝の更新時刻はGitHubの混み具合で前後する（おおむね朝5〜11時）。それより前に聞かれたら、前々日までが最新")
    if daily_missing:
        a(f"- 日次の欠損日: {', '.join(daily_missing[:20])}（表では「欠損」と表示。0ではない）")
    for w in WARN:
        a(f"- {w}")
    if not daily_missing and not WARN:
        a("- 欠損・打ち切り・不整合は見つからなかった")
    a("")
    a("## この資料の使い方（データマン向け）")
    a("- 直近の数字・デバイス・国・ページ（28日／365日）・月次・前年同月比・日次・AI経由セッションは、この資料の実数で答える")
    a("- 任意の期間のセッション・新規・PV・CVは、同じ場所の toyota_jp_ga4_daily.csv を python で合計して答える（使った期間と日数を添える）。その期間のUUは足し算で作らない。28日・365日・各月以外の期間のUUが要るときは ga4.py で取りに行く")
    a("- AI経由の日次・サービス別の計算は ai_referrals_daily.csv を使う")
    a("- ここに無い切り口（AI以外の流入元・チャネル、URL単位など）は ga4.py で取りに行く。取れないときは、出せる範囲を先に出し、出せない部分は「この切り口は今の集計に含まれていない」とだけ書く")
    return "\n".join(L) + "\n"


def month_steps(mon) -> list:
    """完了した月どうしで、前月比 STEP 以上動いた指標を拾う。セッションが動かずに他だけ動いた場合はそれも書く。"""
    names = {"sessions": "セッション", "totalusers": "月間UU", "screen_page_views": "PV", "conversions": "CV"}
    cur_ym = YB.strftime("%Y%m")
    yms = [ym for ym in sorted(mon) if ym != cur_ym or YB == month_end(YB)]
    out = []
    for prev, ym in zip(yms, yms[1:]):
        p, v = mon[prev], mon[ym]
        moved = []
        for k, lab in names.items():
            if p.get(k) and abs(v[k] / p[k] - 1) >= STEP:
                moved.append(f"{lab} {pct(v[k], p[k])}")
        if not moved:
            continue
        line = f"{ym[:4]}-{ym[4:]}：" + "、".join(moved)
        if p.get("sessions") and abs(v["sessions"] / p["sessions"] - 1) < 0.15:
            line += f"（セッションは {pct(v['sessions'], p['sessions'])}。この指標だけが大きく動いている）"
        out.append(line)
    return out


def write_check():
    lines = [f"ga4_report 点検 {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}（データ終端 {YB}）"]
    lines += ["FAIL: " + x for x in FAIL]
    lines += ["WARN: " + x for x in WARN + OWNER]
    lines += ["OK: " + x for x in NOTE]
    txt = "\n".join(lines) + "\n"
    with open(out_path("ga4", "last_check.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write("### GA4 点検\n\n```\n" + txt + "```\n")
    print(txt)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        FAIL.append(f"想定外のエラー: {type(e).__name__}: {str(e)[:300]}")
        write_check()
        raise
