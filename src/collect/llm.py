"""AIサーフェスにプロンプトを投げ、回答本文と引用URLを集める。

本番は DataForSEO / SERP API を叩く。
認証情報が無い場合は demo モードで、実測値に整合した合成データを返す。
"""
from __future__ import annotations

import hashlib
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from common import demo_mode, env, load, load_prompts  # noqa: E402

DFS_BASE = "https://api.dataforseo.com/v3"
LLM_MAP = {"chatgpt": "chat_gpt", "gemini": "gemini", "perplexity": "perplexity", "claude": "claude"}


# ---------------------------------------------------------------- 本番
def _dfs_auth():
    return (env("DATAFORSEO_LOGIN"), env("DATAFORSEO_PASSWORD"))


def fetch_llm_response(prompt: str, surface: dict) -> dict:
    """DataForSEO LLM Responses を1回叩く。回答本文と annotations(引用) を返す。"""
    body = [{
        "user_prompt": prompt,
        "llm_name": LLM_MAP.get(surface["id"], "chat_gpt"),
        "model_name": surface.get("model"),
        "web_search": True,                      # これが無いと引用が返らない
        "web_search_country_iso_code": "JP",
    }]
    r = requests.post(
        f"{DFS_BASE}/ai_optimization/llm_responses/models/live",
        auth=_dfs_auth(), json=body, timeout=120,
    )
    r.raise_for_status()
    task = r.json()["tasks"][0]
    if task.get("status_code") != 20000:
        raise RuntimeError(f"DataForSEO error: {task.get('status_message')}")
    item = task["result"][0]["items"][0]
    sections = item.get("sections", [])
    text = "\n".join(s.get("text", "") for s in sections)
    citations = []
    for s in sections:
        for a in s.get("annotations", []) or []:
            if a.get("url"):
                citations.append({"url": a["url"], "title": a.get("title", "")})
    return {"text": text, "citations": citations,
            "fanout": item.get("fan_out_queries", []) or []}


def fetch_serp_ai(prompt: str, surface: dict) -> dict:
    """Google SERP の ai_overview / ai_mode 要素を取る。"""
    body = [{
        "keyword": prompt,
        "language_code": "ja",
        "location_code": 2392,                   # Japan
        "device": "desktop",
        "load_async_ai_overview": True,
    }]
    ep = "google/ai_mode/live/advanced" if surface["id"] == "aimode" else "google/organic/live/advanced"
    r = requests.post(f"{DFS_BASE}/serp/{ep}", auth=_dfs_auth(), json=body, timeout=120)
    r.raise_for_status()
    items = r.json()["tasks"][0]["result"][0].get("items", []) or []
    text, citations = "", []
    for it in items:
        if it.get("type") in ("ai_overview", "ai_mode_response", "ai_overview_element"):
            for el in it.get("items", []) or [it]:
                text += (el.get("text") or "") + "\n"
                for ref in (el.get("references") or []):
                    if ref.get("url"):
                        citations.append({"url": ref["url"], "title": ref.get("title", "")})
    return {"text": text, "citations": citations, "fanout": []}


# ---------------------------------------------------------------- demo
# 自社ページはカテゴリごとに「引かれやすいURL」を分けてある。
# 本番では実際に返ってきた annotations の URL がそのまま入る。
_OWNED_PAGES = {
    "safety": [("https://toyota.jp/safety/", "予防安全 | トヨタ自動車WEBサイト"),
               ("https://toyota.jp/safety/update/", "Toyota Safety Sense アップデート情報"),
               ("https://toyota.jp/harrier/", "ハリアー | トヨタ自動車WEBサイト")],
    "cost": [("https://toyota.jp/ucar/", "トヨタ認定中古車"),
             ("https://toyota.jp/aqua/", "アクア | トヨタ自動車WEBサイト"),
             ("https://toyota.jp/raize/", "ライズ | トヨタ自動車WEBサイト")],
    "eco": [("https://toyota.jp/prius/", "プリウス | トヨタ自動車WEBサイト"),
            ("https://toyota.jp/mirai/", "MIRAI | トヨタ自動車WEBサイト"),
            ("https://toyota.jp/aqua/", "アクア | トヨタ自動車WEBサイト")],
    "model": [("https://toyota.jp/carlineup/", "車種一覧 | トヨタ自動車WEBサイト"),
              ("https://toyota.jp/noah/", "ノア | トヨタ自動車WEBサイト"),
              ("https://toyota.jp/voxy/", "ヴォクシー | トヨタ自動車WEBサイト"),
              ("https://toyota.jp/sienta/", "シエンタ | トヨタ自動車WEBサイト"),
              ("https://toyota.jp/information/minivan/", "ミニバンナビ | トヨタ自動車WEBサイト")],
    "service": [("https://toyota.jp/after_service/", "アフターサービス | トヨタ自動車WEBサイト"),
                ("https://toyota.jp/welcab/", "ウェルキャブ（福祉車両）"),
                ("https://toyota.jp/ucar/", "トヨタ認定中古車")],
    "purchase": [("https://toyota.jp/carlineup/", "車種一覧 | トヨタ自動車WEBサイト"),
                 ("https://toyota.jp/alphard/", "アルファード | トヨタ自動車WEBサイト"),
                 ("https://toyota.jp/corollacross/", "カローラ クロス | トヨタ自動車WEBサイト"),
                 ("https://toyota.jp/yariscross/", "ヤリス クロス | トヨタ自動車WEBサイト")],
    "brand": [("https://toyota.jp/", "トヨタ自動車WEBサイト"),
              ("https://toyota.jp/carlineup/", "車種一覧 | トヨタ自動車WEBサイト"),
              ("https://toyota.jp/crown/", "クラウン | トヨタ自動車WEBサイト")],
}
_DEMO_CITES = [
    ("https://www.youtube.com/watch?v={h}", "【徹底比較】おすすめミニバン3選"),
    ("https://note.com/{h}/n/n{h}", "オーナーが語る5年目のリアル"),
    ("https://detail.chiebukuro.yahoo.co.jp/qa/question_detail/q{h}", "ミニバンのおすすめ"),
    ("https://ameblo.jp/{h}/entry-{h}.html", "3人家族のクルマ選び"),
    ("https://www.reddit.com/r/JDM/comments/{h}/", "Japanese minivan recommendations"),
    ("https://global.toyota/jp/newsroom/", "トヨタ グローバルニュースルーム"),
    ("https://www.ibaraki-toyopet.co.jp/afterservice/", "茨城トヨペット｜アフターサービス"),
    ("https://global.honda/jp/news/", "Honda ニュースリリース"),
    ("https://www.nissan-global.com/JP/", "日産グローバル"),
    ("https://ja.wikipedia.org/wiki/{h}", "Wikipedia"),
    ("https://prtimes.jp/main/html/rd/p/{h}.html", "PR TIMES"),
    ("https://kakaku.com/kuruma/", "価格.com 自動車"),
    ("https://www.mapbox.com/legal/end-user-terms", ""),
]
# X / Instagram / TikTok はAIの引用元としては実測でごく少ない。
# ゼロにすると「測っていないから出ない」のか「本当に出ない」のか区別できなくなるため、
# 実測に近い低頻度で混ぜてある（重み妥当性の監査パネルで検証できるようにする）。
_DEMO_SOCIAL = [
    ("https://x.com/{h}/status/{h}", "オーナーの投稿"),
    ("https://www.instagram.com/p/{h}/", "納車報告"),
    ("https://www.tiktok.com/@{h}/video/{h}", "試乗レビュー"),
]

# AIが回答生成中に内部で投げる派生クエリ（fan-out）のデモ用プール。
# 本番では DataForSEO の fan_out_queries がそのまま入る。
_FANOUT_POOL = {
    "purchase": ["ファミリーカー 3列シート 比較", "予算400万 ミニバン おすすめ",
                 "チャイルドシート 2台 乗る車", "国産ミニバン 人気ランキング 最新"],
    "safety": ["自動ブレーキ 性能 メーカー比較", "運転支援 高速道路 疲れにくい",
               "サポカー 補助金 対象車種", "踏み間違い防止装置 後付け"],
    "eco": ["ハイブリッド 実燃費 ランキング", "EV 後悔 理由", "充電インフラ 日本 現状",
            "PHEV ハイブリッド 違い"],
    "cost": ["車 維持費 年間 平均", "残価設定ローン デメリット",
             "リセールバリュー 高い車 ランキング", "軽自動車 維持費 比較"],
    "model": ["SUV 荷室 広い 国産", "スライドドア 使いやすい メーカー",
              "雪道 強い車 4WD", "車中泊 できる車 おすすめ"],
    "service": ["車検 費用 ディーラー 民間", "新車 納期 最新 国産",
                "メーカー保証 延長 比較"],
    "brand": ["トヨタ ホンダ 比較 SUV", "トヨタ ハイブリッド 強み",
              "国産メーカー 信頼性 ランキング"],
}

_BRAND_SENT = {
    "toyota": ["トヨタは信頼性と燃費のバランスに優れ、長期保有でも安心できる選択肢です",
               "トヨタのハイブリッドは実燃費が安定しており、リセールバリューも高い水準です",
               "ただしトヨタは価格がやや割高で、デザインが保守的という評価もあります"],
    "honda": ["ホンダは室内空間の使い方が巧みで、街中での取り回しにも優れます",
              "ホンダの安全支援システムは完成度が高く評価されています"],
    "nissan": ["日産はe-POWERによる滑らかな走りと運転支援の快適性が強みです"],
    "subaru": ["スバルはアイサイトに代表される安全技術で高い評価を得ています"],
    "suzuki": ["スズキは価格と燃費のバランスに優れ、維持費を抑えたい層に向きます"],
    "mazda": ["マツダは走行フィールと内装の質感で高い評価を受けています"],
}


def _rng(seed_parts: list[str]) -> random.Random:
    h = hashlib.sha256("|".join(seed_parts).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def demo_response(prompt_id: str, surface_id: str, day: str, run: int,
                  category: str = "purchase") -> dict:
    """実測分布に沿った合成回答。日付でシードするので日々ゆらぐが再現性はある。"""
    rng = _rng([prompt_id, surface_id, day, str(run)])
    # 日付でゆっくり動くドリフト（施策やアルゴリズム変動の代理）
    drift = 1.0 + 0.12 * __import__("math").sin(
        int(day.replace("-", "")) % 97 / 97 * 6.283) + _rng(["drift", day]).uniform(-.03, .03)

    # 実測SoV（honda .20 / nissan .17 / toyota .16 / subaru .10 / suzuki .09）に寄せた出現確率
    appear = {"toyota": 0.62 * drift, "honda": 0.74, "nissan": 0.66,
              "subaru": 0.40, "suzuki": 0.36, "mazda": 0.31}
    picked = [b for b, p in appear.items() if rng.random() < p]
    rng.shuffle(picked)
    picked.sort(key=lambda b: -rng.random() * {"toyota": 1.55, "honda": 1.35,
                                               "nissan": 1.25, "subaru": 1.0,
                                               "suzuki": 1.0, "mazda": 0.95}[b])

    if not picked:
        text = "ご希望の条件だけでは絞り込みが難しいため、用途と予算を教えてください。"
    else:
        text = "ご希望の条件でしたら、以下のブランドが候補になります。\n"
        for i, b in enumerate(picked, 1):
            s = rng.choice(_BRAND_SENT.get(b, ["特徴があります"]))
            text += f"{i}. {s}。\n"

    # 引用は 0〜5件。自社ドメインが混じるのは全体の2割弱（実測の「オウンド6位」を再現）
    k = rng.choice([0, 2, 3, 3, 4, 5])
    cites = []
    if rng.random() < 0.22 * drift:                 # 自社ページが引かれる回
        owned = _OWNED_PAGES.get(category) or _OWNED_PAGES["purchase"]
        for u, t in rng.sample(owned, min(rng.choice([1, 1, 2]), len(owned))):
            cites.append({"url": u, "title": t})
    for tmpl, title in rng.sample(_DEMO_CITES, min(max(k - len(cites), 0), len(_DEMO_CITES))):
        hh = format(rng.getrandbits(32), "08x")
        cites.append({"url": tmpl.format(h=hh), "title": title})
    if rng.random() < 0.05:                          # 20回に1回だけソーシャルが引かれる
        tmpl, title = rng.choice(_DEMO_SOCIAL)
        cites.append({"url": tmpl.format(h=format(rng.getrandbits(32), "08x")), "title": title})

    # fan-out（AIが内部で投げた派生クエリ）。次サイクルのクエリ候補として還流する。
    pool = _FANOUT_POOL.get(category) or _FANOUT_POOL["purchase"]
    fan = rng.sample(pool, min(rng.choice([0, 1, 2, 2, 3]), len(pool)))
    return {"text": text, "citations": cites, "fanout": fan}


# ---------------------------------------------------------------- 実行
def collect(day: str, tier: str = "core") -> list[dict]:
    cfg = load("settings")
    prompts = load_prompts(tier)
    runs = cfg["sampling"]["runs_per_prompt"]
    limit = cfg["sampling"]["tier_schedule"][tier]["max_prompts"]
    surfaces = [s for s in cfg["surfaces"] if s.get("enabled")]
    demo = demo_mode()

    out = []
    for p in prompts[:limit]:
        for s in surfaces:
            for run in range(runs if tier == "core" else 1):
                if demo:
                    res = demo_response(p["id"], s["id"], day, run,
                                        p.get("category", "purchase"))
                else:
                    fn = fetch_serp_ai if s["provider"] == "serp" else fetch_llm_response
                    try:
                        res = fn(p["text"], s)
                    except Exception as e:                      # 1件の失敗で全体を止めない
                        print(f"  ! {p['id']}/{s['id']}: {e}", file=sys.stderr)
                        continue
                    time.sleep(0.2)
                out.append({"date": day, "prompt_id": p["id"], "surface": s["id"],
                            "run": run, **res})
    print(f"  collected {len(out)} responses ({'demo' if demo else 'live'})")
    return out
