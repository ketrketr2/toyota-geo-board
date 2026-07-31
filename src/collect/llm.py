"""AIサーフェスにプロンプトを投げ、回答本文と引用URLを集める。

本番は DataForSEO / SERP API を叩く。
認証情報が無い場合は demo モードで、実測値に整合した合成データを返す。
"""
from __future__ import annotations

import hashlib
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from common import demo_mode, domain_of, env, load, load_prompts  # noqa: E402

DFS_BASE = "https://api.dataforseo.com/v3"

# DataForSEO は LLM ごとにエンドポイントが分かれている。
# 旧実装は /ai_optimization/llm_responses/models/live を叩いていたが、これは存在しない。
LLM_PATH = {"chatgpt": "chat_gpt", "gemini": "gemini",
            "perplexity": "perplexity", "claude": "claude"}

_COST_LOCK = threading.Lock()
_COST = {"total": 0.0, "calls": 0}
_ERRORS: list[str] = []      # 無人実行なので、失敗理由は必ず残して後から読めるようにする
_LAST_RAW: dict = {}         # 疎通確認で構造を確認するための直近レスポンス


def errors() -> list[str]:
    with _COST_LOCK:
        return list(_ERRORS)


def spent() -> dict:
    """この実行で DataForSEO に支払った実額。予算管理のために毎日記録する。"""
    with _COST_LOCK:
        return {"usd": round(_COST["total"], 4), "calls": _COST["calls"]}


def _charge(task: dict) -> None:
    with _COST_LOCK:
        _COST["total"] += float(task.get("cost") or 0)
        _COST["calls"] += 1


def last_cost(task: dict) -> float:
    return float(task.get("cost") or 0)


def _dfs_auth():
    return (env("DATAFORSEO_LOGIN"), env("DATAFORSEO_PASSWORD"))


def _post(path: str, body: list) -> dict:
    r = requests.post(f"{DFS_BASE}/{path}", auth=_dfs_auth(), json=body, timeout=180)
    if r.status_code >= 400:
        # 本文に「なぜ弾かれたか」が書いてある。捨てると原因が永久に分からない。
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:300]}")
    task = r.json()["tasks"][0]
    if task.get("status_code") != 20000:
        raise RuntimeError(f"{task.get('status_code')} {task.get('status_message')}")
    _charge(task)
    return task


_REDIRECTORS = ("vertexaisearch.cloud.google.com", "grounding-api-redirect")


def _walk_refs(node, out: list) -> None:
    """references / annotations は階層がまちまちなので、URLを持つ辞書を再帰で拾う。

    Gemini の引用は vertexaisearch の中継URLで返り、URLからは引用元が分からない。
    ただし title に実ドメイン（例: e-nenpi.com）が入っているので、そちらを採用する。
    ここを取り違えると、自社ドメイン判定もSNS判定も全部ゼロになる。
    """
    if isinstance(node, dict):
        if node.get("url"):
            url = node["url"]
            title = node.get("title") or node.get("source") or ""
            dom = node.get("domain") or ""
            if any(x in url for x in _REDIRECTORS):
                # title が裸のドメインならそれを引用元として扱う
                if title and "/" not in title and "." in title and " " not in title:
                    dom = title.lower()
            out.append({"url": url, "title": title,
                        "domain": dom or domain_of(url)})
            return
        for v in node.values():
            _walk_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_refs(v, out)


def _dedup(cites: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in cites:
        key = c.get("url")
        # 中継URLは同じページでも毎回違う文字列になるため、URLでは重複を潰せない
        if any(x in (c.get("url") or "") for x in _REDIRECTORS):
            key = ("dom", c.get("domain"), c.get("title"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def fetch_llm_response(prompt: str, surface: dict) -> dict:
    """ChatGPT / Gemini などに実際に投げ、回答本文と引用URLを取る。

    web_search を true にしないと引用（annotations）が返らない。
    """
    path = LLM_PATH.get(surface["id"], "chat_gpt")
    body = {
        "user_prompt": prompt[:500],                 # 仕様上の上限
        "model_name": surface["model"],
        "max_output_tokens": surface.get("max_tokens", 1500),
        "web_search": True,
    }
    # 検索地域の指定は ChatGPT 系にしか無い。Gemini に送ると 40501 で弾かれる。
    if path == "chat_gpt":
        body["web_search_country_iso_code"] = "JP"
    # web_search を有効にしても、モデルが「知識で答えられる」と判断すると検索せず、
    # 引用が0件で返る。実際のChatGPT利用時は検索が走るため、そちらに揃える。
    if surface.get("system_message"):
        body["system_message"] = surface["system_message"]
    body = [body]
    task = _post(f"ai_optimization/{path}/llm_responses/live", body)
    items = (task.get("result") or [{}])[0].get("items") or []
    _LAST_RAW[surface["id"]] = {"cost": last_cost(task), "items": items}
    text, cites = "", []
    for it in items:
        if it.get("type") == "reasoning":            # 思考過程は本文に混ぜない
            continue
        for sec in it.get("sections") or []:
            text += (sec.get("text") or "") + "\n"
            _walk_refs(sec.get("annotations"), cites)
    return {"text": text.strip(), "citations": _dedup(cites),
            "fanout": (items[0].get("fan_out_queries") if items else None) or []}


def fetch_serp_ai(prompt: str, surface: dict) -> dict:
    """Google の「AIによる概要」/「AIモード」を取る。"""
    if surface["id"] == "aimode":
        path = "serp/google/ai_mode/live/advanced"
        body = [{"keyword": prompt[:700], "language_code": "ja",
                 "location_code": 2392, "device": "desktop"}]
    else:
        path = "serp/google/organic/live/advanced"
        body = [{"keyword": prompt[:700], "language_code": "ja",
                 "location_code": 2392, "device": "desktop",
                 "load_async_ai_overview": True}]
    task = _post(path, body)
    items = (task.get("result") or [{}])[0].get("items") or []
    _LAST_RAW[surface["id"]] = {"cost": last_cost(task), "items": items}
    text, cites = "", []
    for it in items:
        if not str(it.get("type", "")).startswith("ai_"):
            continue
        for el in (it.get("items") or [it]):
            text += (el.get("text") or "") + "\n"
        _walk_refs(it.get("references"), cites)
        _walk_refs(it.get("items"), cites)
    return {"text": text.strip(), "citations": _dedup(cites), "fanout": []}


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
class BudgetExceeded(RuntimeError):
    pass


def _over_budget(cap: float) -> bool:
    with _COST_LOCK:
        return cap > 0 and _COST["total"] >= cap


def _one(job: dict) -> dict | None:
    """1回分の実測。失敗しても None を返すだけで、全体は止めない。"""
    p, s = job["p"], job["s"]
    if _over_budget(job.get("cap", 0)):
        return None
    fn = fetch_serp_ai if s["provider"] == "serp" else fetch_llm_response
    for attempt in range(3):
        try:
            res = fn(p["text"], s)
            return {"date": job["day"], "prompt_id": p["id"], "surface": s["id"],
                    "run": job["run"], **res}
        except Exception as e:
            # 一時的な混雑（429/5xx）は時間をおけば通ることが多い。
            # 恒久的な失敗（認証ミス等）でも3回で諦めるので待ち時間は上限つき。
            if attempt == 2:
                msg = f"{p['id']}/{s['id']} run{job['run']}: {type(e).__name__}: {e}"
                print(f"  ! {msg}", file=sys.stderr)
                with _COST_LOCK:
                    if len(_ERRORS) < 40:
                        _ERRORS.append(msg)
                return None
            time.sleep(2 ** attempt * 1.5)
    return None


def collect(day: str, tier: str = "core") -> list[dict]:
    cfg = load("settings")
    prompts = load_prompts(tier)
    runs = cfg["sampling"]["runs_per_prompt"]
    limit = cfg["sampling"]["tier_schedule"][tier]["max_prompts"]
    surfaces = [s for s in cfg["surfaces"] if s.get("enabled")]
    demo = demo_mode()

    cap = float((cfg.get("budget") or {}).get("daily_usd_max") or 0)
    jobs = [{"day": day, "p": p, "s": s, "run": run, "cap": cap}
            for p in prompts[:limit]
            for s in surfaces
            for run in range(s.get("runs", runs) if tier == "core" else 1)]

    if demo:
        out = [{"date": day, "prompt_id": j["p"]["id"], "surface": j["s"]["id"], "run": j["run"],
                **demo_response(j["p"]["id"], j["s"]["id"], day, j["run"],
                                j["p"].get("category", "purchase"))}
               for j in jobs]
        print(f"  collected {len(out)} responses (demo)")
        return out

    # ---- 実測は並列で投げる ----
    # DataForSEO の live 系は1本あたり10〜60秒かかる。順番に投げると
    # 720本で数時間になり、GitHub Actions の実行上限に確実にぶつかる。
    # 公式の同時接続上限（30）に対して十分に余裕をとった本数で回す。
    workers = int(env("GEO_BOARD_WORKERS", "10") or 10)
    out, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_one, j) for j in jobs]):
            r = fut.result()
            done += 1
            if r:
                out.append(r)
            if done % 60 == 0:
                print(f"  … {done}/{len(jobs)} 完了（成功 {len(out)} / ${spent()['usd']:.2f}）",
                      flush=True)
    if _over_budget(cap):
        print(f"  ! 1日の上限 ${cap:.2f} に達したため、残りの呼び出しを打ち切りました",
              file=sys.stderr)
        _ERRORS.insert(0, f"予算上限 ${cap:.2f} に到達。設定を見直してください。")

    out.sort(key=lambda r: (r["prompt_id"], r["surface"], r["run"]))
    print(f"  collected {len(out)}/{len(jobs)} responses (live)")
    return out
