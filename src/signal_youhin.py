#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世の中モニタリングレポート用：用品・アップグレード（純正アクセサリー）の検索とAIの現状を、
トヨタと他社（メーカー・社外品・用品店）で比べるための一回計測。

  1. 検索量（Google 広告のデータ）と、語ごとの検索結果の型（AIによる概要が出るか）
  2. 主要語の検索結果（上位20・AIによる概要・関連する質問）PC とスマホ
  3. 質問文での AIモード／AIによる概要／ChatGPT／Gemini／Perplexity の答えと引用
  4. 各社サイトが上位に出ている語（自然検索・AIによる概要の引用）と、用品ページの見られ方の推定
  5. 語のまとまりごとの「上位に出ているサイト」
  6. 検索の関心の推移（Google トレンド）、YouTube の検索結果
結果は data/signal/youhin_<day>.json に生で置く。既存のダッシュボードの入出力には触れない。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

from collect import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "signal"
CAP = float(os.environ.get("SIGNAL_CAP_USD", "10") or 10)
LOC, LANG = 2392, "ja"
ERRORS: list[dict] = []


def log(*a):
    print(*a, flush=True)


def over() -> bool:
    return llm.spent()["usd"] >= CAP


def guard(name: str, fn, *a, **k):
    """1段ぶんの計測。失敗しても記録して次へ進む。"""
    if over():
        ERRORS.append({"stage": name, "error": f"budget cap ${CAP} reached"})
        return None
    t0 = time.time()
    try:
        r = fn(*a, **k)
        log(f"[ok] {name} {time.time()-t0:.0f}s ${llm.spent()['usd']:.3f}")
        return r
    except Exception as e:  # noqa: BLE001
        ERRORS.append({"stage": name, "error": f"{type(e).__name__}: {str(e)[:400]}"})
        log(f"[NG] {name}: {type(e).__name__}: {str(e)[:300]}")
        traceback.print_exc(limit=1)
        return None


def get(path: str) -> dict:
    r = requests.get(f"{llm.DFS_BASE}/{path}", auth=llm._dfs_auth(), timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:300]}")
    task = r.json()["tasks"][0]
    if task.get("status_code") != 20000:
        raise RuntimeError(f"{task.get('status_code')} {task.get('status_message')}")
    llm._charge(task)
    return task


# ------------------------------------------------------------------ 対象
MAKERS = {
    "toyota": "toyota.jp", "honda": "honda.co.jp", "nissan": "nissan.co.jp", "subaru": "subaru.jp",
    "suzuki": "suzuki.co.jp", "mazda": "mazda.co.jp", "daihatsu": "daihatsu.co.jp",
    "mitsubishi": "mitsubishi-motors.co.jp",
}
OTHERS = {
    "carmate": "carmate.co.jp", "yupiteru": "yupiteru.co.jp", "comtec": "e-comtec.co.jp",
    "autobacs": "autobacs.com", "yellowhat": "yellowhat.jp", "kinto_factory": "factory.kinto-jp.com",
    "modellista": "modellista.co.jp", "minkara": "minkara.carview.co.jp", "kakaku": "kakaku.com",
}
# 用品のページだと分かるパス（大文字小文字は区別しない）
SECTION = {
    "toyota": ["%/dop/%", "%accessor%", "%customize%"],
    "honda": ["%/access/%", "%accessor%"],
    "nissan": ["%optional-parts%", "%accessor%"],
    "subaru": ["%accessor%"],
    "suzuki": ["%accessor%"],
    "mazda": ["%accessor%"],
    "daihatsu": ["%accessor%"],
    "mitsubishi": ["%accessor%"],
}
TOPIC = ["%エンジンスターター%", "%リモートスタート%", "%アクセサリー%", "%ドライブレコーダー%",
         "%盗難%", "%セキュリティ%", "%純正%", "%オプション%"]

KW_START = ["エンジンスターター", "エンジンスターター 後付け", "エンジンスターター 純正", "エンジンスターター おすすめ",
            "リモートスタート", "リモコンエンジンスターター", "トヨタ エンジンスターター", "ホンダ エンジンスターター",
            "日産 エンジンスターター", "スバル エンジンスターター", "スズキ エンジンスターター", "ダイハツ エンジンスターター"]
KW_THEFT = ["車 盗難対策", "CANインベーダー 対策", "リレーアタック 対策", "カーセキュリティ おすすめ",
            "ランクル 盗難対策", "アルファード 盗難対策", "トヨタ セキュリティシステム", "ハンドルロック"]
KW_ACC = ["純正アクセサリー", "トヨタ 純正アクセサリー", "ホンダ 純正アクセサリー", "日産 純正アクセサリー",
          "スバル 純正アクセサリー", "スズキ 純正アクセサリー", "ディーラーオプション 後付け", "車 後付け オプション おすすめ"]
KW_WINTER = ["車 冬 準備", "雪道 車 必需品", "フロントガラス 凍結 対策", "スノーワイパー"]
KW_NAVI = ["ドライブレコーダー 純正", "ドライブレコーダー おすすめ", "ディスプレイオーディオ 後付け", "カーナビ 地図更新"]
SERP_KWS = KW_START + KW_THEFT + KW_ACC + KW_WINTER + KW_NAVI

VOL_EXTRA = [
    # メーカー × 用品
    "トヨタ 純正用品", "トヨタ ディーラーオプション", "トヨタ 純正ナビ", "トヨタ 純正 ドライブレコーダー", "トヨタ 純正 フロアマット",
    "トヨタ リモートスタート", "トヨタ 純正セキュリティ", "トヨタ 盗難防止", "トヨタ アップグレード", "トヨタ 後付け",
    "トヨタ ナビ 更新", "トヨタ 地図更新", "KINTO FACTORY", "キントファクトリー", "モデリスタ", "TRD パーツ", "T-Connect",
    "ディスプレイオーディオ", "リモートエアコン", "トヨタ リモートエアコン",
    "ホンダアクセス", "ホンダ 純正ナビ", "ギャザズ", "ホンダ 純正 ドライブレコーダー", "ホンダ リモコンエンジンスターター",
    "ホンダ ディーラーオプション", "無限 パーツ", "モデューロ", "Honda CONNECT", "ホンダ リモートエアコン",
    "日産 ディーラーオプション", "日産 純正ナビ", "日産 リモコンエンジンスターター", "日産 純正 ドライブレコーダー", "NISMO パーツ",
    "オーテック", "NissanConnect", "日産 乗る前エアコン",
    "スバル リモートエンジンスターター", "スバル 純正 ドライブレコーダー", "スバル ディーラーオプション", "STI パーツ", "SUBARU STARLINK",
    "スズキ リモートエンジンスターター", "スズキ 純正ナビ", "スズキ 純正 ドライブレコーダー", "スズキ ディーラーオプション", "スズキコネクト",
    "マツダ 純正アクセサリー", "マツダ エンジンスターター", "マツダ リモートエンジンスターター", "マツダ 純正 ドライブレコーダー",
    "ダイハツ 純正アクセサリー", "ダイハツ リモコンエンジンスターター", "ダイハツ 純正ナビ", "ダイハツ 純正 ドライブレコーダー",
    "三菱 純正アクセサリー", "三菱 エンジンスターター", "三菱 リモコンエンジンスターター",
    # エンジンスターター
    "エンジンスターター 取り付け", "エンジンスターター 工賃", "エンジンスターター スマホ", "エンジンスターター 社外",
    "エンジンスターター プッシュスタート", "エンジンスターター 違法", "エンジンスターター デメリット", "エンジンスターター 価格",
    "カーメイト エンジンスターター", "ユピテル エンジンスターター", "コムテック エンジンスターター",
    "オートバックス エンジンスターター", "イエローハット エンジンスターター", "エンジンスターター 軽自動車", "リモートスタート 後付け",
    # 盗難
    "車 盗難 対策", "盗難防止 車", "車 盗難", "リレーアタック", "CANインベーダー", "ゲームボーイ 盗難", "タイヤロック",
    "カーセキュリティ", "パンテーラ", "ゴルゴ セキュリティ", "ユピテル セキュリティ", "ランクル 盗難", "ランドクルーザー 盗難",
    "プリウス 盗難対策", "レクサス 盗難対策", "ハイエース 盗難対策", "車 GPS 追跡", "スマートキー 節電モード", "盗難されやすい車",
    # 冬
    "スタッドレスタイヤ", "タイヤ交換", "スノーブレード", "解氷スプレー", "霜取り 車", "シートヒーター 後付け",
    "ステアリングヒーター 後付け", "寒冷地仕様", "ラバーマット 車", "スノーブラシ", "タイヤチェーン", "ルーフボックス",
    "スキーキャリア", "車 暖機", "フロントガラス 凍結",
    # ドラレコ・ナビ
    "ドライブレコーダー", "ドライブレコーダー 前後", "ドラレコ 後付け", "カーナビ 後付け", "CarPlay 後付け", "純正ナビ 社外ナビ",
    "後席モニター", "デジタルインナーミラー 後付け", "ETC 後付け",
    # 売り場・社外品
    "オートバックス", "イエローハット", "ジェームス", "カーメイト", "ユピテル", "コムテック", "データシステム", "セルスター",
    "ディーラーオプション", "メーカーオプション", "車 アクセサリー", "カー用品",
]

QUESTIONS = [
    ("q01", "start", "冬の朝に車の窓が凍って出発に時間がかかります。エンジンスターターを付けたいのですが、メーカー純正と社外品のどちらがよいですか。"),
    ("q02", "start", "プッシュスタートの車にエンジンスターターは後付けできますか。費用の目安と、どこで付けるのがよいか教えてください。"),
    ("q03", "start", "スマホから車のエンジンをかけて暖房を入れておける国産車はありますか。メーカーごとの違いを教えてください。"),
    ("q04", "start", "子どもの送り迎えで冬の朝が大変です。出発前に車内を暖めておく方法を教えてください。"),
    ("q05", "theft", "車の盗難が心配です。CANインベーダーやリレーアタックに効く対策を教えてください。"),
    ("q06", "theft", "ランドクルーザーの盗難対策は何をすればよいですか。純正のセキュリティだけで足りますか。"),
    ("q07", "theft", "アルファードを買いました。盗難対策でおすすめの純正オプションと社外品を教えてください。"),
    ("q08", "theft", "車の盗難防止は、メーカー純正のセキュリティと専門店のセキュリティのどちらが効果的ですか。"),
    ("q09", "acc", "純正アクセサリーが充実している国産車メーカーはどこですか。"),
    ("q10", "acc", "納車したあとでも付けられるメーカー純正オプションには、どんなものがありますか。"),
    ("q11", "acc", "買ったあとの車に新しい機能を後から追加できるサービスがある自動車メーカーを教えてください。"),
    ("q12", "acc", "ドライブレコーダーは純正と社外品のどちらを選ぶべきですか。"),
    ("q13", "winter", "雪道を走る前に車に付けておくとよい用品を教えてください。"),
    ("q14", "winter", "スキーや雪国への帰省に使う車に、付けておくと便利なメーカー純正アクセサリーはありますか。"),
    ("q15", "navi", "車のディスプレイオーディオでCarPlayや動画を使うには何が必要ですか。"),
    ("q16", "navi", "純正カーナビの地図を新しくするにはどうすればよいですか。費用も教えてください。"),
]

TRENDS = [
    ["トヨタ 純正アクセサリー", "ホンダアクセス", "日産 純正アクセサリー", "スバル 純正アクセサリー", "スズキ 純正アクセサリー"],
    ["エンジンスターター", "スタッドレスタイヤ", "ドライブレコーダー", "カーセキュリティ", "純正アクセサリー"],
    ["トヨタ エンジンスターター", "ホンダ エンジンスターター", "日産 エンジンスターター", "スバル エンジンスターター", "スズキ エンジンスターター"],
    ["KINTO FACTORY", "ホンダアクセス", "モデリスタ", "カーメイト", "ユピテル"],
]
YT = ["エンジンスターター 取り付け", "エンジンスターター 純正", "車 盗難対策", "CANインベーダー 対策", "純正アクセサリー おすすめ", "雪道 車 準備"]


# ------------------------------------------------------------------ 1. 検索量
def volumes(kws: list[str]) -> dict:
    out = {}
    for i in range(0, len(kws), 700):
        task = llm._post("keywords_data/google_ads/search_volume/live",
                         [{"keywords": kws[i:i + 700], "language_code": LANG, "location_code": LOC}])
        for it in task.get("result") or []:
            out[it["keyword"]] = {"volume": it.get("search_volume"), "competition": it.get("competition"),
                                  "cpc": it.get("cpc"),
                                  "monthly": [{"y": m.get("year"), "m": m.get("month"), "v": m.get("search_volume")}
                                              for m in (it.get("monthly_searches") or [])]}
    return out


def overview(kws: list[str]) -> dict:
    out = {}
    for i in range(0, len(kws), 700):
        task = llm._post("dataforseo_labs/google/keyword_overview/live",
                         [{"keywords": kws[i:i + 700], "language_code": LANG, "location_code": LOC,
                           "include_serp_info": True}])
        for it in ((task.get("result") or [{}])[0].get("items") or []):
            ki, si = it.get("keyword_info") or {}, it.get("serp_info") or {}
            out[it.get("keyword")] = {
                "volume": ki.get("search_volume"),
                "difficulty": (it.get("keyword_properties") or {}).get("keyword_difficulty"),
                "intent": (it.get("search_intent_info") or {}).get("main_intent"),
                "serp_item_types": si.get("serp_item_types"), "serp_updated": si.get("last_updated_time"),
                "se_results_count": si.get("se_results_count")}
    return out


# ------------------------------------------------------------------ 2. 検索結果
def serp(keyword: str, device: str) -> dict:
    task = llm._post("serp/google/organic/live/advanced",
                     [{"keyword": keyword, "language_code": LANG, "location_code": LOC, "device": device,
                       "load_async_ai_overview": True, "depth": 20}])
    res = (task.get("result") or [{}])[0]
    organic, aio, paa, types, video, shop = [], None, [], {}, [], []
    for it in res.get("items") or []:
        t = str(it.get("type", ""))
        types[t] = types.get(t, 0) + 1
        if t == "organic":
            organic.append({"rank": it.get("rank_absolute"), "group": it.get("rank_group"), "url": it.get("url"),
                            "domain": it.get("domain"), "title": (it.get("title") or "")[:120]})
        elif t.startswith("ai_overview"):
            cites = []
            llm._walk_refs(it.get("references"), cites)
            llm._walk_refs(it.get("items"), cites)
            text = ""
            for el in (it.get("items") or [it]):
                text += (el.get("text") or el.get("markdown") or "") + "\n"
            aio = {"text": text.strip()[:6000],
                   "citations": [{"url": c.get("url"), "domain": c.get("domain"), "title": (c.get("title") or "")[:100]}
                                 for c in llm._dedup(cites)]}
        elif t == "people_also_ask":
            for el in it.get("items") or []:
                paa.append(el.get("title") or "")
        elif t in ("video", "short_videos"):
            for el in it.get("items") or []:
                video.append({"title": (el.get("title") or "")[:100], "source": el.get("source"), "url": el.get("url")})
        elif t in ("popular_products", "shopping", "product_considerations"):
            for el in (it.get("items") or [])[:10]:
                shop.append({"title": (el.get("title") or "")[:80], "seller": el.get("seller") or el.get("source"),
                             "price": (el.get("price") or {}).get("current") if isinstance(el.get("price"), dict) else el.get("price")})
    return {"keyword": keyword, "device": device, "organic": organic[:20], "aio": aio, "paa": paa, "video": video[:10],
            "shop": shop, "types": types, "item_types": res.get("item_types"), "cost": llm.last_cost(task)}


def pool(jobs: list, fn, workers: int = 8) -> list:
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, j): j for j in jobs}
        for f in as_completed(futs):
            j = futs[f]
            try:
                r = f.result()
                if r is not None:
                    out.append(r)
            except Exception as e:  # noqa: BLE001
                ERRORS.append({"stage": "pool", "job": str(j)[:160], "error": f"{type(e).__name__}: {str(e)[:300]}"})
    return out


def run_serps() -> list:
    def one(j):
        if over():
            return None
        for a in range(3):
            try:
                return serp(*j)
            except Exception as e:  # noqa: BLE001
                if a == 2:
                    ERRORS.append({"stage": "serp", "job": str(j), "error": str(e)[:300]})
                time.sleep(2 * (a + 1))
        return None
    return pool([(k, d) for k in SERP_KWS for d in ("desktop", "mobile")], one)


# ------------------------------------------------------------------ 3. AIの答え
def run_answers(models: dict) -> list:
    cfg = {s["id"]: s for s in (llm.load("settings").get("surfaces") or [])}
    chat = dict(cfg.get("chatgpt") or {"id": "chatgpt", "model": "gpt-5", "max_tokens": 4096})
    gem = dict(cfg.get("gemini") or {"id": "gemini", "model": "gemini-2.5-flash"})
    jobs = []
    for qid, theme, text in QUESTIONS:
        jobs.append((qid, theme, text, "aimode", 0, {"id": "aimode", "provider": "serp"}))
        jobs.append((qid, theme, text, "aio", 0, {"id": "aio", "provider": "serp"}))
        for r in range(2):
            jobs.append((qid, theme, text, "gemini", r, gem))
            jobs.append((qid, theme, text, "chatgpt", r, chat))
        if models.get("perplexity"):
            jobs.append((qid, theme, text, "perplexity", 0, {"id": "perplexity", "model": models["perplexity"]}))

    def one(j):
        qid, theme, text, sid, run, surf = j
        if over():
            return None
        for a in range(3):
            try:
                if sid in ("aimode", "aio"):
                    res = llm.fetch_serp_ai(text, surf)
                elif sid == "perplexity":
                    task = llm._post("ai_optimization/perplexity/llm_responses/live",
                                     [{"user_prompt": text[:500], "model_name": surf["model"], "max_output_tokens": 1500}])
                    items = (task.get("result") or [{}])[0].get("items") or []
                    body, cites = "", []
                    for it in items:
                        for sec in it.get("sections") or []:
                            body += (sec.get("text") or "") + "\n"
                            llm._walk_refs(sec.get("annotations"), cites)
                    res = {"text": body.strip(), "citations": llm._dedup(cites), "fanout": []}
                else:
                    res = llm.fetch_llm_response(text, surf)
                return {"qid": qid, "theme": theme, "question": text, "surface": sid, "run": run,
                        "text": (res.get("text") or "")[:9000],
                        "citations": [{"url": c.get("url"), "domain": c.get("domain"), "title": (c.get("title") or "")[:100]}
                                      for c in (res.get("citations") or [])],
                        "fanout": res.get("fanout") or []}
            except Exception as e:  # noqa: BLE001
                if a == 2:
                    ERRORS.append({"stage": "answer", "job": f"{qid}/{sid}/{run}", "error": str(e)[:300]})
                time.sleep(3 * (a + 1))
        return None
    return pool(jobs, one, workers=10)


def llm_models() -> dict:
    out = {}
    for name in ("perplexity", "chat_gpt", "gemini", "claude"):
        try:
            task = get(f"ai_optimization/{name}/llm_responses/models")
            out[name] = [m.get("model_name") for m in (task.get("result") or []) if m.get("model_name")]
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": str(e)[:200]}
    return out


# ------------------------------------------------------------------ 4. 各社サイト
def _or(field: str, pats: list[str], op: str = "ilike") -> list:
    f = []
    for p in pats[:8]:
        if f:
            f.append("or")
        f.append([field, op, p])
    return f if len(pats) > 1 else f[0]


def ranked(target: str, filters, item_types: list[str], limit: int = 300) -> dict:
    body = {"target": target, "language_code": LANG, "location_code": LOC, "item_types": item_types, "limit": limit,
            "order_by": ["keyword_data.keyword_info.search_volume,desc"]}
    if filters:
        body["filters"] = filters
    task = llm._post("dataforseo_labs/google/ranked_keywords/live", [body])
    res = (task.get("result") or [{}])[0]
    items = []
    for it in res.get("items") or []:
        kd, se = it.get("keyword_data") or {}, (it.get("ranked_serp_element") or {}).get("serp_item") or {}
        ki = kd.get("keyword_info") or {}
        items.append({"keyword": kd.get("keyword"), "volume": ki.get("search_volume"), "type": se.get("type"),
                      "rank": se.get("rank_absolute"), "group": se.get("rank_group"), "url": se.get("relative_url"),
                      "etv": se.get("etv"), "serp_types": (kd.get("serp_info") or {}).get("serp_item_types")})
    return {"target": target, "total_count": res.get("total_count"), "metrics": res.get("metrics"),
            "items": items, "cost": llm.last_cost(task)}


def pages(target: str, pats: list[str], limit: int = 100) -> dict:
    body = {"target": target, "language_code": LANG, "location_code": LOC, "limit": limit,
            "filters": _or("page_address", pats), "order_by": ["metrics.organic.etv,desc"]}
    task = llm._post("dataforseo_labs/google/relevant_pages/live", [body])
    res = (task.get("result") or [{}])[0]
    return {"target": target, "total_count": res.get("total_count"),
            "items": [{"page": it.get("page_address"), "organic": {k: (it.get("metrics") or {}).get("organic", {}).get(k)
                                                                     for k in ("etv", "count", "pos_1", "pos_2_3", "pos_4_10")}}
                      for it in res.get("items") or []], "cost": llm.last_cost(task)}


def rank_overview(target: str) -> dict:
    task = llm._post("dataforseo_labs/google/domain_rank_overview/live",
                     [{"target": target, "language_code": LANG, "location_code": LOC}])
    it = (((task.get("result") or [{}])[0].get("items") or [{}])[0].get("metrics") or {})
    keep = ("etv", "count", "pos_1", "pos_2_3", "pos_4_10", "pos_11_20")
    return {"target": target, "organic": {k: (it.get("organic") or {}).get(k) for k in keep},
            "ai_overview_reference": {k: (it.get("ai_overview_reference") or {}).get(k) for k in keep},
            "featured_snippet": {k: (it.get("featured_snippet") or {}).get(k) for k in keep}}


def serp_competitors(kws: list[str], limit: int = 40) -> list:
    task = llm._post("dataforseo_labs/google/serp_competitors/live",
                     [{"keywords": kws[:200], "language_code": LANG, "location_code": LOC,
                       "item_types": ["organic", "featured_snippet"], "limit": limit, "include_subdomains": True}])
    res = (task.get("result") or [{}])[0]
    return [{k: it.get(k) for k in ("domain", "avg_position", "median_position", "rating", "etv", "keywords_count", "visibility")}
            for it in res.get("items") or []]


def suggestions(seed: str, limit: int = 200) -> list:
    task = llm._post("dataforseo_labs/google/keyword_suggestions/live",
                     [{"keyword": seed, "language_code": LANG, "location_code": LOC, "include_serp_info": True,
                       "include_seed_keyword": True, "limit": limit, "order_by": ["keyword_info.search_volume,desc"]}])
    out = []
    for it in ((task.get("result") or [{}])[0].get("items") or []):
        ki, si = it.get("keyword_info") or {}, it.get("serp_info") or {}
        out.append({"keyword": it.get("keyword"), "volume": ki.get("search_volume"),
                    "serp_item_types": si.get("serp_item_types")})
    return out


# ------------------------------------------------------------------ 6. 関心の推移・YouTube
def trends(kws: list[str]) -> dict:
    today = datetime.date.today()
    task = llm._post("keywords_data/google_trends/explore/live",
                     [{"keywords": kws[:5], "location_code": LOC, "language_code": LANG, "type": "web",
                       "date_from": "2025-11-01", "date_to": today.isoformat()}])
    res = (task.get("result") or [{}])[0]
    out = {"keywords": kws, "graph": None, "averages": None}
    for it in res.get("items") or []:
        if it.get("type") == "google_trends_graph":
            out["graph"] = [{"from": d.get("date_from"), "to": d.get("date_to"), "values": d.get("values"),
                             "missing": d.get("missing_data")} for d in it.get("data") or []]
            out["averages"] = it.get("averages")
    return out


def youtube(keyword: str) -> dict:
    task = llm._post("serp/youtube/organic/live/advanced",
                     [{"keyword": keyword, "language_code": LANG, "location_code": LOC, "block_depth": 20}])
    res = (task.get("result") or [{}])[0]
    return {"keyword": keyword,
            "items": [{"type": it.get("type"), "title": (it.get("title") or "")[:100], "channel": it.get("channel_name"),
                       "views": it.get("views_count"), "published": it.get("timestamp") or it.get("publication_date"),
                       "url": it.get("url"), "badges": it.get("badges")}
                      for it in (res.get("items") or [])[:20]]}


# ------------------------------------------------------------------ 試し（AIの検索量・AIでの言及の集計）
def ai_volume(kws: list[str]) -> dict:
    task = llm._post("ai_optimization/ai_keyword_data/keywords_search_volume/live",
                     [{"keywords": kws[:200], "language_code": LANG, "location_code": LOC}])
    res = (task.get("result") or [{}])[0]
    return {"items": [{"keyword": it.get("keyword"), "ai_search_volume": it.get("ai_search_volume"),
                       "monthly": it.get("ai_monthly_searches")} for it in res.get("items") or []]}


def mentions_locations() -> list:
    task = get("ai_optimization/llm_mentions/locations_and_languages")
    return [r for r in (task.get("result") or []) if "Japan" in str(r.get("location_name"))]


def mentions_search(keyword: str, platform: str) -> dict:
    task = llm._post("ai_optimization/llm_mentions/search/live",
                     [{"target": [{"keyword": keyword}], "platform": platform, "language_code": LANG,
                       "location_code": LOC, "limit": 30, "order_by": ["ai_search_volume,desc"]}])
    res = (task.get("result") or [{}])[0]
    items = []
    for it in res.get("items") or []:
        items.append({"question": it.get("question"), "ai_search_volume": it.get("ai_search_volume"),
                      "answer": (it.get("answer") or "")[:1500],
                      "sources": [{"domain": s.get("domain"), "url": s.get("url"), "title": (s.get("title") or "")[:80]}
                                  for s in (it.get("sources") or [])[:12]],
                      "brands": it.get("brand_entities")})
    return {"keyword": keyword, "platform": platform, "total_count": res.get("total_count"), "items": items,
            "cost": llm.last_cost(task)}


def main():
    day = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    out: dict = {"generated_at_utc": datetime.datetime.utcnow().isoformat(), "day_jst": day, "cap_usd": CAP,
                 "serp_keywords": {"start": KW_START, "theft": KW_THEFT, "acc": KW_ACC, "winter": KW_WINTER, "navi": KW_NAVI},
                 "questions": [{"qid": q, "theme": t, "text": x} for q, t, x in QUESTIONS]}
    path = OUT / f"youhin_{day}.json"

    def save():
        out["errors"], out["spent"] = ERRORS, llm.spent()
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    all_kws = list(dict.fromkeys(SERP_KWS + VOL_EXTRA))
    log(f"keywords {len(all_kws)} / serp {len(SERP_KWS)} / questions {len(QUESTIONS)} / cap ${CAP}")

    out["volumes"] = guard("volumes", volumes, all_kws)
    out["overview"] = guard("overview", overview, all_kws)
    save()
    out["serps"] = guard("serps", run_serps)
    save()
    out["llm_models"] = guard("llm_models", llm_models) or {}
    px = out["llm_models"].get("perplexity") if isinstance(out["llm_models"], dict) else None
    px_model = next((m for m in (px or []) if isinstance(px, list) and m == "sonar"), None) or (px[0] if isinstance(px, list) and px else None)
    out["answers"] = guard("answers", run_answers, {"perplexity": px_model})
    save()

    out["domain_overview"] = {k: guard(f"overview:{k}", rank_overview, d) for k, d in {**MAKERS, **OTHERS}.items()}
    save()
    out["ranked_topic"] = {k: guard(f"ranked_topic:{k}", ranked, d, _or("keyword_data.keyword", TOPIC, "like"), ["organic", "featured_snippet"])
                           for k, d in MAKERS.items()}
    out["ranked_topic_aio"] = {k: guard(f"ranked_topic_aio:{k}", ranked, d, _or("keyword_data.keyword", TOPIC, "like"), ["ai_overview_reference"])
                               for k, d in MAKERS.items()}
    save()
    out["ranked_section"] = {k: guard(f"ranked_section:{k}", ranked, d, _or("ranked_serp_element.serp_item.relative_url", SECTION[k]), ["organic", "featured_snippet"])
                             for k, d in MAKERS.items()}
    out["ranked_section_aio"] = {k: guard(f"ranked_section_aio:{k}", ranked, d, _or("ranked_serp_element.serp_item.relative_url", SECTION[k]), ["ai_overview_reference"])
                                 for k, d in MAKERS.items()}
    save()
    out["ranked_others"] = {k: guard(f"ranked_others:{k}", ranked, d, None, ["organic", "featured_snippet"], 200)
                            for k, d in list(OTHERS.items())[:7]}
    out["ranked_others_aio"] = {k: guard(f"ranked_others_aio:{k}", ranked, d, None, ["ai_overview_reference"], 200)
                                for k, d in list(OTHERS.items())[:7]}
    save()
    out["pages"] = {k: guard(f"pages:{k}", pages, d, SECTION[k]) for k, d in MAKERS.items()}
    save()
    out["serp_competitors"] = {
        "start": guard("competitors:start", serp_competitors, KW_START + [k for k in VOL_EXTRA if "エンジンスターター" in k or "リモートスタート" in k]),
        "theft": guard("competitors:theft", serp_competitors, KW_THEFT + [k for k in VOL_EXTRA if "盗難" in k or "セキュリティ" in k or k in ("リレーアタック", "CANインベーダー", "タイヤロック")]),
        "acc": guard("competitors:acc", serp_competitors, KW_ACC + [k for k in VOL_EXTRA if "アクセサリー" in k or "オプション" in k]),
        "dorareco_navi": guard("competitors:navi", serp_competitors, KW_NAVI + [k for k in VOL_EXTRA if "ドライブレコーダー" in k or "ナビ" in k or "ドラレコ" in k]),
    }
    save()
    out["suggestions"] = {s: guard(f"suggest:{s}", suggestions, s) for s in ("エンジンスターター", "盗難対策", "純正アクセサリー", "リモートスタート")}
    save()
    out["trends"] = [guard(f"trends:{i}", trends, t) for i, t in enumerate(TRENDS)]
    out["youtube"] = [guard(f"youtube:{k}", youtube, k) for k in YT]
    save()

    # ここから下は、日本語のデータがあるか分からない計測。無ければ理由だけ残る。
    out["ai_volume"] = guard("ai_volume", ai_volume, SERP_KWS + VOL_EXTRA[:120])
    loc = guard("mentions_locations", mentions_locations)
    out["mentions_locations"] = loc
    if loc:
        out["mentions"] = [guard(f"mentions:{k}:{p}", mentions_search, k, p)
                           for k in ("エンジンスターター", "純正アクセサリー", "盗難対策", "ドライブレコーダー")
                           for p in ("google", "chat_gpt")]
    save()
    log("saved", path, "errors", len(ERRORS), "spent", llm.spent())
    for e in ERRORS[:40]:
        log("  -", json.dumps(e, ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
