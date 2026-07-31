"""クエリ収穫エンジン: 市場で実際に聞かれているクエリを取り込み、コア枠を入れ替える。

5経路から候補を集める:
  1. Googleサジェスト        … 検索窓に実際に打たれている語（無料・公式endpoint）
  2. Google「関連する質問」  … 検索結果のPAA。すでに疑問文なので加工不要
  3. Yahoo!知恵袋            … 生活者が自分の言葉で書いた実質問（最も実態に近い）
  4. LLM fan-out還流         … AIが回答生成中に内部で投げた派生クエリ
  5. DataForSEO 検索ボリューム … 上記候補の実需要を数字で裏取り

集めた候補は需要スコアで並べ、コア枠（日次実行）を毎週入れ替える。
ただし入替には上限をかける。一気に替えるとスコアの母集団が変わって時系列が壊れるため。
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

import registry as R  # noqa: E402
from common import (DATA, days_ago, demo_mode, env, load, today,  # noqa: E402
                    read_json, write_json)

FANOUT_DIR = DATA / "fanout"
HARVEST_LOG = DATA / "harvest_log.json"
DFS = "https://api.dataforseo.com/v3"


# ================================================================ 分類
CAT_RULES = [
    ("safety",   ["安全", "衝突", "自動ブレーキ", "サポカー", "踏み間違い", "事故", "アイサイト",
                  "運転支援", "レーンキープ", "アダプティブ", "見守り", "死角"]),
    ("eco",      ["燃費", "ハイブリッド", "電気自動車", "ev", "phev", "電動", "充電", "水素",
                  "エコ", "リッター", "航続"]),
    ("cost",     ["維持費", "価格", "値引き", "安い", "コスト", "保険", "税金", "ローン",
                  "残価", "リース", "サブスク", "車検", "頭金", "予算"]),
    ("service",  ["車検", "点検", "整備", "修理", "ディーラー", "保証", "リコール", "納期",
                  "アフター", "部品"]),
    ("model",    ["どっち", "比較", "違い", "スペック", "サイズ", "内装", "乗り心地", "室内",
                  "グレード", "型落ち"]),
    ("brand",    ["トヨタ", "レクサス", "ホンダ", "日産", "スバル", "スズキ", "マツダ"]),
]
DRIVER_RULES = [
    ("safety",       ["安全", "衝突", "自動ブレーキ", "サポカー", "踏み間違い", "アイサイト"]),
    ("adas",         ["運転支援", "レーンキープ", "アダプティブ", "高速", "疲れ", "渋滞",
                      "自動運転", "クルコン"]),
    ("urban",        ["街乗り", "取り回し", "駐車", "狭い道", "小回り", "都市", "コンパクト"]),
    ("hybrid",       ["ハイブリッド", "燃費", "電動", "ev", "phev", "充電", "水素"]),
    ("minivan",      ["ミニバン", "family", "ファミリー", "子供", "チャイルドシート", "3列",
                      "スライドドア"]),
    ("cost",         ["維持費", "安い", "予算", "コスト", "税金", "保険"]),
    ("resale",       ["リセール", "下取り", "買取", "残価", "値落ち"]),
    ("cargo",        ["積載", "荷室", "キャンプ", "車中泊", "アウトドア", "ラゲッジ"]),
    ("subscription", ["リース", "サブスク", "kinto", "ローン", "残クレ"]),
    ("warranty",     ["保証", "車検", "点検", "整備", "修理"]),
    ("dealer",       ["ディーラー", "販売店", "納期", "在庫"]),
    ("reliability",  ["故障", "壊れ", "耐久", "長持ち", "10年", "距離"]),
    ("connected",    ["コネクテッド", "アプリ", "ナビ", "通信", "オンライン"]),
    ("esg",          ["環境", "co2", "サステナ", "リサイクル"]),
]
OWN_WORDS = ["トヨタ", "toyota", "レクサス", "lexus", "プリウス", "アクア", "ヤリス",
             "ノア", "ヴォクシー", "アルファード", "ハリアー", "ランクル", "クラウン",
             "シエンタ", "ライズ", "カローラ"]


def _hit(text: str, words: list[str]) -> bool:
    t = text.lower()
    return any(w.lower() in t for w in words)


def classify(text: str) -> dict:
    cat = next((c for c, ws in CAT_RULES if _hit(text, ws)), "purchase")
    drv = next((d for d, ws in DRIVER_RULES if _hit(text, ws)), None)
    return {"category": cat, "driver": drv, "brand": _hit(text, OWN_WORDS)}


def to_question(kw: str, category: str, cfg: dict, rnd: random.Random) -> str:
    """名詞句のサジェストを、生活者が実際にAIへ打つ文体に変換する。"""
    if kw.endswith(("か", "？", "?")) or "教えて" in kw or "ですか" in kw:
        return kw                                   # すでに疑問文ならそのまま
    tpl = cfg["question_templates"].get(category) or cfg["question_templates"]["purchase"]
    return rnd.choice(tpl).format(kw=kw)


def acceptable(text: str, cfg: dict) -> bool:
    if not (cfg["min_chars"] <= len(text) <= cfg["max_chars"]):
        return False
    return not _hit(text, cfg["reject_patterns"])


# ================================================================ 収穫（本番）
def fetch_suggest(seed: str, n: int) -> list[str]:
    """Googleサジェスト。公式のcomplete endpointをJSONで叩く。"""
    r = requests.get("https://suggestqueries.google.com/complete/search",
                     params={"client": "firefox", "hl": "ja", "q": seed}, timeout=20)
    r.raise_for_status()
    return [s for s in (r.json()[1] or []) if s != seed][:n]


def _dfs_post(path: str, body: list) -> dict:
    r = requests.post(f"{DFS}/{path}",
                      auth=(env("DATAFORSEO_LOGIN"), env("DATAFORSEO_PASSWORD")),
                      json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_paa(seed: str, n: int) -> list[str]:
    """検索結果の「他の人はこちらも質問」を取る。すでに疑問文なので加工不要。"""
    js = _dfs_post("serp/google/organic/live/advanced",
                   [{"keyword": seed, "language_code": "ja", "location_code": 2392}])
    out = []
    for it in js["tasks"][0]["result"][0].get("items", []) or []:
        if it.get("type") != "people_also_ask":
            continue
        for el in it.get("items", []) or []:
            if el.get("title"):
                out.append(el["title"])
    return out[:n]


def fetch_chiebukuro(seed: str, n: int) -> list[str]:
    """知恵袋の実質問タイトル。site: 検索のSERPから拾う（スクレイピングはしない）。"""
    js = _dfs_post("serp/google/organic/live/advanced",
                   [{"keyword": f"site:chiebukuro.yahoo.co.jp {seed}",
                     "language_code": "ja", "location_code": 2392}])
    out = []
    for it in js["tasks"][0]["result"][0].get("items", []) or []:
        t = (it.get("title") or "").split(" - ")[0].strip()
        if it.get("type") == "organic" and len(t) >= 10:
            out.append(t)
    return out[:n]


def fetch_volumes(keywords: list[str]) -> dict[str, int]:
    """月間検索ボリューム。Google Ads のデータをDataForSEO経由で取る。"""
    vols: dict[str, int] = {}
    for i in range(0, len(keywords), 700):            # APIの1リクエスト上限に合わせて分割
        chunk = keywords[i:i + 700]
        js = _dfs_post("keywords_data/google_ads/search_volume/live",
                       [{"keywords": chunk, "language_code": "ja", "location_code": 2392}])
        for it in js["tasks"][0]["result"] or []:
            vols[it["keyword"]] = it.get("search_volume") or 0
    return vols


# ================================================================ 収穫（demo）
_DEMO_POOL = [
    ("3列シートで一番運転しやすいミニバンはどれですか。狭い住宅街を毎日通ります。", "purchase"),
    ("チャイルドシートを2台つけても窮屈にならない国産車を教えてください。", "purchase"),
    ("高速道路で長距離を走ると腰が痛くなります。運転支援が優秀な国産ブランドはどこですか。", "safety"),
    ("自動ブレーキの性能を国産メーカー別に比べると、どこが一番進んでいますか。", "safety"),
    ("駐車が苦手でも安心して乗れる、駐車支援が優秀な車を教えてください。", "safety"),
    ("ハイブリッド車の実燃費はカタログ値と比べてどのくらい落ちますか。メーカー別に知りたいです。", "eco"),
    ("電気自動車を買って後悔した人はどんな理由が多いですか。国産ならどこが無難ですか。", "eco"),
    ("寒冷地でハイブリッドとガソリン車、どちらが維持費で有利ですか。", "eco"),
    ("残価設定ローンとカーリース、結局どちらが損しませんか。", "cost"),
    ("車の維持費を年間20万円以内に抑えたいです。どの車種が現実的ですか。", "cost"),
    ("新車の納期が長いと聞きました。いま国産で比較的早く納車される車種はどれですか。", "service"),
    ("車検を安く済ませたいのですが、ディーラーと民間工場で何が違いますか。", "service"),
    ("10年20万キロ乗るつもりです。故障が少ない国産ブランドはどこですか。", "model"),
    ("雪道に強い国産SUVを教えてください。年に数回スキーに行きます。", "model"),
    ("車中泊ができてキャンプにも使える国産車を教えてください。", "model"),
    ("親が高齢なので、乗り降りしやすく運転支援も充実した車を探しています。", "safety"),
    ("初めて車を買う新社会人です。予算200万円で後悔しない選び方を教えてください。", "purchase"),
    ("中古車を買うなら何年落ちが一番お得ですか。国産のおすすめ車種も知りたいです。", "cost"),
    ("軽自動車とコンパクトカー、街乗り中心ならどちらが良いですか。", "purchase"),
    ("リセールバリューが落ちにくい国産車はどれですか。3年で乗り換える予定です。", "cost"),
    ("トヨタのハイブリッドは他社と比べて何が優れていますか。弱点も教えてください。", "brand"),
    ("スライドドアの車で、開閉の使い勝手が一番良いのはどのメーカーですか。", "model"),
    ("コネクテッド機能はどのメーカーが使いやすいですか。スマホ連携を重視しています。", "model"),
    ("SUVで荷室が広く、後席も快適な国産車を教えてください。", "model"),
    ("燃費だけでなく静粛性も重視したいです。おすすめの国産セダンはありますか。", "eco"),
    ("ディーラーの対応が良いメーカーはどこですか。長く付き合うことを考えています。", "service"),
    ("水素自動車は今買う価値がありますか。実際の使い勝手を教えてください。", "eco"),
    ("同じ価格帯でトヨタとホンダのSUVを比べると、どちらが買いですか。", "model"),
]


def demo_candidates(day: str, cfg: dict) -> list[dict]:
    """実行日でシードした合成候補。日ごとに違うものが出るが再現性はある。"""
    rnd = random.Random(int(day.replace("-", "")))
    picked = rnd.sample(_DEMO_POOL, k=rnd.choice([9, 11, 13]))
    srcs = ["suggest", "paa", "chiebukuro", "fanout"]
    out = []
    for txt, _cat in picked:
        meta = classify(txt)
        out.append({
            "text": txt, **meta,
            "source": rnd.choice(srcs),
            "volume": rnd.choice([0, 90, 140, 260, 480, 720, 1300, 2400, 4800]),
            "fanout_hits": rnd.choice([0, 0, 1, 2, 3, 5, 8]),
            "ugc_hits": rnd.choice([0, 1, 2, 4, 7]),
            "growth": round(rnd.uniform(0.75, 1.9), 2),
        })
    return out


# ================================================================ fan-out還流
def save_fanout(day: str, responses: list[dict]) -> int:
    """日次収集の際にAIが内部で投げた派生クエリを保存する（run_daily から呼ぶ）。"""
    c = Counter()
    for r in responses:
        for q in (r.get("fanout") or []):
            q = (q if isinstance(q, str) else q.get("query") or "").strip()
            if q:
                c[q] += 1
    if c:
        write_json(FANOUT_DIR / f"{day}.json", dict(c.most_common(300)))
    return len(c)


def collect_fanout(day: str, cfg: dict) -> list[dict]:
    """直近N日のfan-outを集計し、閾値以上のものを候補にする。"""
    s = cfg["sources"]["fanout"]
    if not s.get("enabled"):
        return []
    agg = Counter()
    for i in range(s["lookback_days"]):
        d = read_json(FANOUT_DIR / f"{days_ago(day, i)}.json", {}) or {}
        for q, n in d.items():
            agg[q] += n
    # fan-outは「ハイブリッド 実燃費 ランキング」のような語句なので、
    # そのまま投げても実際の聞かれ方にならない。質問文へ変換してから登録する。
    rnd = random.Random(int(day.replace("-", "")))
    out = []
    for q, n in agg.items():
        if n < s["min_hits"]:
            continue
        m = classify(q)
        out.append({"text": to_question(q, m["category"], cfg, rnd),
                    "raw": q, "keyword": q, "source": "fanout",
                    "fanout_hits": n, **m})
    return out


# ================================================================ 需要スコア
def demand_score(p: dict, maxvol: int, w: dict) -> float | None:
    """0〜100。ボリュームは対数で効かせる（桁違いの語に引っ張られないため）。

    重要: 取れなかったシグナルは「ゼロ」ではなく「未計測」として扱い、
    使えた重みだけで正規化する。そうしないと、検索ボリュームが取れない
    長文クエリが一律に低評価となり、実態と無関係に降格してしまう。
    まだ何のシグナルも無いクエリは None を返し、昇降格の対象外にする。
    """
    parts, used = 0.0, 0.0
    if p.get("volume"):
        vol = int(p["volume"])
        parts += w["volume"] * (math.log10(vol + 1) / math.log10(max(maxvol, 10) + 1))
        used += w["volume"]
    if p.get("fanout_hits"):
        parts += w["fanout"] * min(p["fanout_hits"] / 8.0, 1.0)
        used += w["fanout"]
    if p.get("growth") is not None and p.get("growth") != 1.0:
        parts += w["growth"] * min(max((p["growth"] - 0.8) / 0.8, 0.0), 1.0)
        used += w["growth"]
    if p.get("ugc_hits"):
        parts += w["ugc"] * min(p["ugc_hits"] / 6.0, 1.0)
        used += w["ugc"]
    if used == 0:
        return None
    # 証拠の少なさを割り引く。1シグナルだけで満点になると、たまたま
    # fan-outに数回出ただけのクエリが、実検索が多いクエリを押しのけてしまう。
    total = sum(w.values())
    confidence = used / total
    return parts / used * 100 * (0.55 + 0.45 * confidence)


def rescore(reg: dict, day: str, w: dict) -> None:
    maxvol = max([int(p.get("volume") or 0) for p in reg["prompts"]] + [0])
    for p in reg["prompts"]:
        if p.get("tier") == "retired":
            continue
        s = demand_score(p, maxvol, w)
        if s is None:
            p["demand"] = None          # 未計測。昇降格の判断材料にしない
        else:
            R.push_demand(p, day, s)


# ================================================================ 昇降格
def rebalance(reg: dict, day: str, cfg: dict) -> dict:
    """需要スコア順にコア枠を組み替える。

    ルールは3つ。
      ① 定員(core_target)を守る。空いた分だけ入れる、超えた分だけ出す。
      ② 出入りには回数上限をかける。一気に替えるとスコアの母集団が変わって時系列が壊れる。
      ③ 高優先度の判断軸（安全・都市の取り回し・運転支援）は需要が落ちても守る。
         事業として捨てられない論点は、市場人気に関係なく measure し続ける必要があるため。
    """
    from datetime import date
    pr = cfg["promotion"]
    prio = {d["id"]: d["priority"] for d in load("brands")["drivers"]}

    core = sorted([p for p in reg["prompts"] if p["tier"] == "core"],
                  key=lambda p: -(p.get("demand") if p.get("demand") is not None else 50))
    bench = sorted([p for p in reg["prompts"] if p["tier"] in ("strategic", "longtail")],
                   key=lambda p: -(p.get("demand") if p.get("demand") is not None else 0))

    def days_in_core(p):
        return (date.fromisoformat(day)
                - date.fromisoformat(p.get("tier_since", day))).days

    def protected(p):
        if p.get("driver") in pr["protect_drivers"] or prio.get(p.get("driver")) == "high":
            return True
        return days_in_core(p) < pr["min_days_in_core"]

    target = pr["core_target"]
    # 急上昇で緊急投入した分は、その週は超過を許す（翌週の収穫で戻す）
    over = len(core) - target - ((cfg.get("surge") or {}).get("overflow_allowed", 0)
                                 if any(p.get("surged_on") for p in core) else 0)
    cat_count = Counter(p["category"] for p in core)

    # ---- 降格: 需要が下限割れ、または定員オーバーぶんの下位 ----
    demote = []
    for p in reversed(core):                       # 需要の低い方から見る
        if len(demote) >= pr["max_demote_per_run"]:
            break
        if protected(p) or p.get("demand") is None:
            continue                               # 未計測は判断材料が無いので触らない
        if cat_count[p["category"]] - 1 < pr["min_per_category"]:
            continue                               # そのテーマが痩せすぎるので残す
        if p["demand"] < pr["demand_floor"] or len(demote) < over:
            demote.append(p)
            cat_count[p["category"]] -= 1

    # ---- 昇格: 空き枠のぶんだけ。ただし現コア最下位より需要が高いものに限る ----
    remain = [p for p in core if p not in demote]
    weakest = min([p["demand"] for p in remain if p.get("demand") is not None] or [0])
    room = min(target, pr["core_max"]) - len(remain)
    promote = []
    for p in bench:
        if len(promote) >= min(pr["max_promote_per_run"], max(room, 0)):
            break
        if p.get("demand") is None or p["demand"] < pr["demand_floor"]:
            continue
        if remain and p["demand"] <= weakest:
            continue                               # 入替の意味が無いので見送る
        promote.append(p)

    # ---- 入替: 定員が埋まっていても、ベンチの方が明確に需要が高ければ交代させる ----
    #  「明確に」= swap_margin 以上の差。僅差で毎週入れ替えると時系列が落ち着かない。
    swaps = []
    if room <= 0:
        pool = [p for p in bench if p not in promote and p.get("demand") is not None]
        outs = [p for p in remain
                if not protected(p) and p.get("demand") is not None]
        outs.sort(key=lambda p: p["demand"])
        for cand in pool:
            if len(swaps) >= pr.get("max_swap_per_run", 0):
                break
            if not outs:
                break
            low = outs[0]
            if cand["demand"] < low["demand"] + pr.get("swap_margin", 8):
                break                              # 以降はもっと差が小さいので打ち切り
            if cat_count[low["category"]] - 1 < pr["min_per_category"]:
                outs.pop(0)
                continue
            cat_count[low["category"]] -= 1
            cat_count[cand["category"]] += 1
            swaps.append((cand, low))
            outs.pop(0)

    for p in demote:
        R.set_tier(p, "strategic", day)
    for p in promote:
        R.set_tier(p, "core", day)
    for cand, low in swaps:
        R.set_tier(cand, "core", day)
        R.set_tier(low, "strategic", day)

    promoted = [p["id"] for p in promote] + [c["id"] for c, _ in swaps]
    demoted = [p["id"] for p in demote] + [l["id"] for _, l in swaps]
    return {"promoted": promoted, "demoted": demoted,
            "swaps": [[c["id"], l["id"]] for c, l in swaps],
            "core_size": len(remain) + len(promote), "target": target}


# ================================================================ イベント連動
def event_seeds(day: str) -> list[tuple[str, str]]:
    """カレンダーに入っているイベントのうち、今日が期間内のものからシードを出す。

    新型車の発表や税制改正は「起きる日」が事前に分かる。
    その期間だけシードを増やし、関連クエリを先回りで拾う。
    """
    try:
        ev = load("events")
    except Exception:
        return []
    out = []
    for e in ev.get("calendar", []):
        if e.get("from", "9999") <= day <= e.get("to", "0000"):
            for sd in e.get("seeds", []):
                out.append((sd, e["label"]))
    return out


def watch_hit(text: str) -> str | None:
    """炎上・不祥事型の監視テーマに該当するか。該当したらそのラベルを返す。"""
    try:
        ev = load("events")
    except Exception:
        return None
    for w in ev.get("watch", []):
        if _hit(text, w["keywords"]):
            return w["label"]
    return None


def detect_surge(reg: dict, day: str, cfg: dict) -> list[dict]:
    """急上昇クエリを検出し、通常ルールを飛ばしてコア枠へ入れる。

    「残クレでアルファードは危ない」のような不安が広がったとき、
    需要スコア順の週次入替を待っていると、いちばん見たい2週間を逃す。
    """
    sc = cfg.get("surge") or {}
    if not sc.get("enabled"):
        return []
    hits = []
    for p in reg["prompts"]:
        if p.get("tier") in ("core", "retired"):
            continue
        g = min(p.get("growth") or 1.0, 3.0)
        w = watch_hit(p["text"])
        if w:
            g *= sc.get("watch_bonus", 1.0)
        if g < sc.get("growth_threshold", 1.6):
            continue
        if (p.get("volume") or 0) < sc.get("min_volume", 300) and not w:
            continue
        hits.append({"p": p, "growth": round(g, 2), "watch": w})
    hits.sort(key=lambda x: -x["growth"])
    hits = hits[:sc.get("max_per_run", 3)]
    for h in hits:
        R.set_tier(h["p"], "core", day)
        h["p"]["surged_on"] = day
        h["p"]["surge_reason"] = h["watch"] or "検索需要の急上昇"
    return hits


# ================================================================ 既存クエリの需要更新
def refresh_existing(reg: dict, day: str) -> int:
    """すでに登録済みのクエリについても、毎回 需要シグナルを取り直す。

    「追加する」だけでは不十分で、既存クエリの人気が落ちたことを検知できないと
    入れ替えが片道通行になる。ここで volume / growth を更新する。
    """
    live = [p for p in reg["prompts"] if p.get("tier") != "retired"]
    if demo_mode():
        rnd = random.Random(int(day.replace("-", "")))
        for p in live:
            # IDで固定した基準値に、日付由来のドリフトを掛ける（再現性のある擬似実測）
            base = random.Random(p["id"]).choice([0, 110, 190, 320, 540, 880, 1400, 2600, 5200])
            drift = 1.0 + 0.35 * math.sin((int(day.replace("-", "")) + hash(p["id"]) % 97) / 31.0)
            prev = p.get("volume")
            p["volume"] = max(int(base * max(drift, 0.2)), 0)
            # 初回は比較対象が無いので「不明＝1.0」。伸び率は3倍で頭打ちにする
            p["growth"] = 1.0 if not prev else round(min((p["volume"] + 1) / (prev + 1), 3.0), 2)
            if rnd.random() < 0.30:
                p["ugc_hits"] = (p.get("ugc_hits") or 0) + rnd.choice([0, 1, 1, 2])
        return len(live)

    keys = [(p.get("keyword") or p["text"])[:80] for p in live]
    try:
        vols = fetch_volumes(list(set(keys)))
    except Exception as e:
        print(f"  ! refresh volumes: {e}", file=sys.stderr)
        return 0
    for p, k in zip(live, keys):
        v = vols.get(k)
        if v is None:
            continue
        prev = p.get("volume") or 0
        p["volume"] = int(v)
        p["growth"] = round(min((p["volume"] + 1) / (prev + 1), 3.0), 2) if prev else 1.0
    return len(live)


# ================================================================ fan-out の反映
def apply_fanout_hits(reg: dict, day: str, cfg: dict) -> None:
    """既存クエリが直近でどれだけ fan-out に現れたかを更新する。

    AIが自分から派生させて聞き直している = その話題がいま中心にある、という指標。
    """
    s = cfg["sources"]["fanout"]
    agg = Counter()
    for i in range(s["lookback_days"]):
        for q, n in (read_json(FANOUT_DIR / f"{days_ago(day, i)}.json", {}) or {}).items():
            agg[q] += n
    if not agg:
        return
    for p in reg["prompts"]:
        if p.get("tier") == "retired":
            continue
        hits = sum(n for q, n in agg.items() if R.similarity(q, p["text"]) >= 0.35)
        p["fanout_hits"] = hits


# ================================================================ 実行
def harvest(day: str | None = None) -> dict:
    day = day or today()
    cfg = load("harvest")
    reg = R.load_registry()
    rnd = random.Random(int(day.replace("-", "")))
    before = Counter(p["tier"] for p in reg["prompts"])

    # ---- 1) 候補を集める ----
    cands: list[dict] = []
    if demo_mode():
        cands += demo_candidates(day, cfg)
        rnd2 = random.Random(int(day.replace("-", "")) + 7)
        for sd, label in event_seeds(day):                # 期間中のイベント語も候補に混ぜる
            m = classify(sd)
            cands.append({"text": to_question(sd, m["category"], cfg, rnd2),
                          "raw": sd, "keyword": sd, "source": "event",
                          "event": label,
                          "volume": rnd2.choice([420, 780, 1500, 2900, 5600]),
                          "growth": round(rnd2.uniform(1.4, 2.6), 2),
                          "ugc_hits": rnd2.choice([1, 2, 4]), **m})
    else:
        S = cfg["sources"]
        evs = event_seeds(day)
        if evs:
            print(f"  イベント連動シード {len(evs)}件: " +
                  ", ".join(sorted({l for _, l in evs})))
        for seed in cfg["seeds"] + [s for s, _ in evs]:
            if S["suggest"]["enabled"]:
                try:
                    for kw in fetch_suggest(seed, S["suggest"]["per_seed"]):
                        m = classify(kw)
                        cands.append({"text": to_question(kw, m["category"], cfg, rnd),
                                      "source": "suggest", "raw": kw, "keyword": kw, **m})
                except Exception as e:
                    print(f"  ! suggest {seed}: {e}", file=sys.stderr)
            if S["paa"]["enabled"]:
                try:
                    for q in fetch_paa(seed, S["paa"]["per_seed"]):
                        cands.append({"text": q, "source": "paa", **classify(q)})
                except Exception as e:
                    print(f"  ! paa {seed}: {e}", file=sys.stderr)
            if S["chiebukuro"]["enabled"]:
                try:
                    for q in fetch_chiebukuro(seed, S["chiebukuro"]["per_seed"]):
                        cands.append({"text": q, "source": "chiebukuro",
                                      "ugc_hits": 1, **classify(q)})
                except Exception as e:
                    print(f"  ! chiebukuro {seed}: {e}", file=sys.stderr)
    cands += collect_fanout(day, cfg)

    # ---- 2) ノイズ除去 ----
    cands = [c for c in cands if acceptable(c["text"], cfg)]

    # ---- 3) 実需要の裏取り（検索ボリューム）----
    if not demo_mode() and cands:
        try:
            keys = list({(c.get("raw") or c["text"])[:80] for c in cands})
            vols = fetch_volumes(keys)
            for c in cands:
                c["volume"] = vols.get((c.get("raw") or c["text"])[:80], 0)
        except Exception as e:
            print(f"  ! search_volume: {e}", file=sys.stderr)

    # ---- 4) レジストリへ追加（重複は既存側の指標だけ更新）----
    added = [p for c in cands if (p := R.add_candidate(reg, c, day))]

    # ---- 5) 既存クエリの需要も取り直す（落ちたものを検知するため）----
    n_ref = refresh_existing(reg, day)
    apply_fanout_hits(reg, day, cfg)

    # ---- 6) 需要スコア再計算 → 急上昇の緊急投入 → コア枠の組み替え ----
    rescore(reg, day, cfg["demand_weights"])
    surged = detect_surge(reg, day, cfg)
    moved = rebalance(reg, day, cfg)
    R.save_registry(reg)

    after = Counter(p["tier"] for p in reg["prompts"])
    idx = R.by_id(reg)
    log = read_json(HARVEST_LOG, []) or []
    entry = {
        "date": day,
        "candidates": len(cands),
        "added": [{"id": p["id"], "text": p["text"], "source": p["source"],
                   "demand": p["demand"], "volume": p["volume"]} for p in added],
        "promoted": [{"id": i, "text": idx[i]["text"], "demand": idx[i]["demand"],
                      "source": idx[i]["source"]} for i in moved["promoted"]],
        "surged": [{"id": h["p"]["id"], "text": h["p"]["text"], "growth": h["growth"],
                    "reason": h["p"].get("surge_reason"), "source": h["p"]["source"],
                    "demand": h["p"].get("demand"), "volume": h["p"].get("volume") or 0}
                   for h in surged],
        "demoted": [{"id": i, "text": idx[i]["text"], "demand": idx[i]["demand"]}
                    for i in moved["demoted"]],
        "tiers": dict(after),
        "tiers_before": dict(before),
    }
    log = [e for e in log if e["date"] != day] + [entry]
    log.sort(key=lambda e: e["date"])
    write_json(HARVEST_LOG, log[-52:])                     # 1年分だけ残す

    print(f"[{day}] harvest: 候補{len(cands)}件 / 新規{len(added)}本 / "
          f"昇格{len(moved['promoted'])} 降格{len(moved['demoted'])} / "
          f"急上昇{len(surged)} / コア{after['core']}本（前{before['core']}）")
    for h in surged:
        print(f"    ⚡ 急上昇: {h['p']['text'][:44]}… ({h['p'].get('surge_reason')} ×{h['growth']})")
    return entry


if __name__ == "__main__":
    d = None
    if "--date" in sys.argv:
        d = sys.argv[sys.argv.index("--date") + 1]
    harvest(d)
