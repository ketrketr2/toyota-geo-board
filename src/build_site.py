"""スナップショット群 → ダッシュボードが読む docs/data/latest.json を作る。

フロントは静的HTML1枚。JSONを差し替えるだけで表示が変わる構造にしてある。
"""
from __future__ import annotations

from collections import Counter, defaultdict

from common import (DATA, DOCS, days_ago, list_snapshots, load, read_json,
                    snapshot_path, write_json)

HISTORY_DAYS = 60

# ---------------------------------------------------------------- GA4実測レイヤー
# data/ga4_daily.json …… GA4（Windsor.ai経由）で取った日別・サービス別のAI経由セッション実数。
# スナップショット内の ga4_ai_sessions がデモ値だった期間を、表示時に実測へ差し替える。
# 対応表示日: スナップショット日 D には「D-1（前日実績）」の値を使う（画面の注記と一致させる）。
_GA4_REAL = None


def _ga4_real(snap_day: str) -> dict | None:
    """スナップショット日 snap_day に表示すべき実測値（前日ぶん）を返す。無ければ None。"""
    global _GA4_REAL
    if _GA4_REAL is None:
        _GA4_REAL = read_json(DATA / "ga4_daily.json", default={}) or {}
    return _GA4_REAL.get(days_ago(snap_day, 1))


def _fixed_diff(day: str, dif: dict) -> dict:
    """スナップショットに焼き込まれた diff のうち、ai_sessions だけ実測系列で再計算する。"""
    if not _ga4_real(day):
        return dif
    try:
        import diff as _diff
        mt = dict(dif.get("metrics", {}))
        mt["ai_sessions"] = _diff.compare(day, _diff.ga4_fixed_picker, "ai_sessions")
        return {**dif, "metrics": mt}
    except Exception:
        return dif


def _fixed_signals(snap_day: str, sig: dict) -> dict:
    real = _ga4_real(snap_day)
    if not real:
        return sig
    out = dict(sig)
    out["ga4_ai_sessions"] = real
    out["ga4_source"] = "ga4_real"
    return out



def _short_path(url: str) -> str:
    """表示用にパスだけを取り出す（長い場合は省略）。"""
    from urllib.parse import urlparse
    u = urlparse(url)
    p = (u.path or "/") + (("?" + u.query) if u.query else "")
    return p if len(p) <= 46 else p[:43] + "…"


def _active_events(day: str) -> list[dict]:
    """いま期間に入っているイベント（新型車・税制・季節）を返す。"""
    try:
        ev = load("events")
    except Exception:
        return []
    KIND = {"policy": "制度・税制", "newmodel": "新型車", "season": "季節"}
    return [{"id": e["id"], "label": e["label"], "kind": KIND.get(e["kind"], e["kind"]),
             "from": e["from"], "to": e["to"], "seeds": e.get("seeds", []),
             "note": e.get("note", "")}
            for e in ev.get("calendar", [])
            if e.get("from", "9999") <= day <= e.get("to", "0000")]


def _newmodels(day: str, query_rows: list[dict]) -> dict:
    """追跡中の新型車ごとに「実際にAIへ何が聞かれ、どう答えられたか」を束ねる。

    match の語がクエリ本文に含まれれば「その車について聞かれている」、
    AI回答本文にだけ含まれれば「聞かれてはいないがAIが自発的に挙げた」。
    後者は認知の立ち上がりを示すので分けて数える。
    """
    try:
        ev = load("events")
    except Exception:
        return {}
    models = ev.get("newmodels") or []
    if not models:
        return {}

    out = []
    for m in models:
        keys = [k.lower() for k in (m.get("match") or [])]
        asked, spoken = [], 0
        for r in query_rows:
            qt = (r.get("text") or "").lower()
            hit_q = any(k in qt for k in keys)
            ans_hits, own_cited = 0, False
            for sid, sv in (r.get("surfaces") or {}).items():
                a = (sv.get("answer") or "").lower()
                if a and any(k in a for k in keys):
                    ans_hits += 1
            if hit_q:
                own_cited = bool(r.get("own_pages"))
                asked.append({
                    "id": r["id"], "text": r["text"],
                    "best_rank": r.get("best_rank"),
                    "demand": r.get("demand"), "volume": r.get("volume"),
                    "category": r.get("category"),
                    "own_cited": own_cited,
                    "n_own": len(r.get("own_pages") or []),
                    "answer_hits": ans_hits,
                    "is_new": bool(r.get("is_new")),
                })
            elif ans_hits:
                spoken += ans_hits

        asked.sort(key=lambda x: (x["demand"] is None, -(x["demand"] or 0)))
        ranked = [q["best_rank"] for q in asked if q["best_rank"]]
        out.append({
            "id": m["id"], "model_id": m.get("model_id", ""),
            "model": m.get("model", ""), "title": m.get("title", ""),
            "date": m.get("date", ""), "date_precision": m.get("date_precision", "day"),
            "status": m.get("status", "announced"),
            "note": m.get("note", ""), "source": m.get("source", ""),
            "days_from_launch": _daydiff(day, m.get("date", "")),
            "queries": asked[:12], "n_queries": len(asked),
            "n_cited": sum(1 for q in asked if q["own_cited"]),
            "best_rank": min(ranked) if ranked else None,
            "unprompted": spoken,
        })
    out.sort(key=lambda e: (abs(e["days_from_launch"]) if e["days_from_launch"] is not None else 9999))
    return {"updated_on": ev.get("newmodels_updated_on", ""), "items": out}


MODEL_HISTORY_DAYS = 30


def _bucket_domains(snap: dict) -> dict:
    """引用の種別ごとに、実際にどのドメインが何本引かれたかを出す。

    円グラフの「グループ・販売店 4本」だけでは、どの販売店なのかが分からない。
    販売店ドメインは全国に散っているので、名前が見えないと打ち手に繋がらない。
    """
    out: dict[str, Counter] = defaultdict(Counter)
    for c in snap.get("cells") or []:
        for cit in c.get("citations") or []:
            host = cit.get("host") or cit.get("domain") or ""
            if host:
                out[cit.get("bucket", "other")][host] += 1
    return {b: [{"host": h, "n": n} for h, n in cnt.most_common(30)]
            for b, cnt in out.items()}


def _model_status(day: str, query_rows: list[dict]) -> dict:
    """車種ごとに「AIが今どう扱っているか」をまとめる。

    見たいのは車種単位の状況なので、回答本文・クエリ本文・被引用URLの3経路で照合する。
      - 言及  : AIの回答本文に車名が出た回答の数
      - 質問  : クエリ本文自体に車名が入っているもの
      - 引用  : toyota.jp の被引用URLのパスがその車種のもの
      - 併記  : 同じ回答の中に一緒に出てくる競合車種
    """
    try:
        mc = load("models")
    except Exception:
        return {}
    own_models = mc.get("own") or []
    rivals = mc.get("rivals") or []
    cats = mc.get("categories") or {}
    if not own_models:
        return {}

    # 「ヤリス」は「ヤリスクロス」「GRヤリス」にも当たってしまう。
    # 自分より長い別車種の車名を先に伏せ字にしてから照合する（最長一致優先）。
    all_alias = sorted({a.lower() for m in own_models + rivals
                        for a in (m.get("aliases") or [])}, key=len, reverse=True)

    def masks_for(alias: str) -> list[str]:
        return [x for x in all_alias if x != alias and alias in x]

    def hit(text: str, aliases: list[str]) -> bool:
        for a in aliases:
            t = text
            for mk in masks_for(a):
                if mk in t:
                    t = t.replace(mk, "\u0001" * len(mk))
            if a in t:
                return True
        return False

    # ---- 当日のセルを1回だけ小文字化して使い回す ----
    snap = read_json(snapshot_path(day)) or {}
    cells = snap.get("cells") or []
    low = [((c.get("answer") or "").lower(), c) for c in cells]
    n_cells = len(cells) or 1

    # ---- 過去N日の言及回数（スパークライン用）----
    series: dict[str, list] = {m["id"]: [] for m in own_models}
    dates = []
    for d in [days_ago(day, i) for i in range(MODEL_HISTORY_DAYS - 1, -1, -1)]:
        sn = read_json(snapshot_path(d))
        if not sn:
            continue
        dates.append(d)
        texts = [(c.get("answer") or "").lower() for c in (sn.get("cells") or [])]
        for m in own_models:
            al = [a.lower() for a in m.get("aliases") or []]
            series[m["id"]].append(sum(1 for t in texts if hit(t, al)))

    # ---- クエリ側（本文に車名が入っているもの）----
    qlow = [((r.get("text") or "").lower(), r) for r in query_rows]

    out = []
    for m in own_models:
        al = [a.lower() for a in m.get("aliases") or []]
        mcells = [c for t, c in low if hit(t, al)]
        surf = Counter(c.get("surface") for c in mcells)

        asked = [r for t, r in qlow if hit(t, al)]
        asked.sort(key=lambda r: (r.get("demand") is None, -(r.get("demand") or 0)))

        # 被引用ページ（toyota.jp のパスで紐付け）
        slug = (m.get("slug") or "").lower()
        pages = []
        if slug:
            seen = set()
            for r in query_rows:
                for pg in r.get("own_pages") or []:
                    u = (pg.get("url") or "").lower()
                    if slug in u and u not in seen:
                        seen.add(u)
                        pages.append({"url": pg.get("url"), "path": pg.get("path"),
                                      "title": pg.get("title")})

        # 併記されている競合車種
        co = Counter()
        for c in mcells:
            a = (c.get("answer") or "").lower()
            for rv in rivals:
                if hit(a, [x.lower() for x in rv.get("aliases") or []]):
                    co[rv["name"] + "｜" + rv.get("brand", "")] += 1

        ser = series.get(m["id"]) or []
        prev = ser[:-7][-7:] if len(ser) >= 14 else []
        last7 = ser[-7:] if len(ser) >= 7 else ser
        avg = (sum(last7) / len(last7)) if last7 else 0
        pavg = (sum(prev) / len(prev)) if prev else None

        out.append({
            "id": m["id"], "name": m.get("name", ""), "cat": m.get("cat", ""),
            "cat_label": cats.get(m.get("cat"), m.get("cat", "")),
            "slug": m.get("slug", ""),
            "mentions": len(mcells),
            "rate": round(len(mcells) / n_cells * 100, 1),
            "surfaces": dict(surf),
            "asked": [{"id": r["id"], "text": r["text"], "best_rank": r.get("best_rank"),
                       "demand": r.get("demand"), "volume": r.get("volume"),
                       "cited": bool(r.get("own_pages"))} for r in asked[:20]],
            "n_asked": len(asked),
            "pages": pages[:8], "n_pages": len(pages),
            "rivals": [{"name": k.split("｜")[0], "brand": k.split("｜")[1], "n": v}
                       for k, v in co.most_common(5)],
            "series": ser,
            "avg7": round(avg, 1),
            "trend7": (round(avg - pavg, 1) if pavg is not None else None),
        })

    out.sort(key=lambda x: (-x["mentions"], -x["n_asked"], x["name"]))
    return {"updated_on": mc.get("updated_on", ""), "dates": dates,
            "categories": cats, "cells": n_cells, "items": out}


def _citation_counts(snap: dict, own: str) -> dict:
    """出現率と自社サイト引用率の「分母と分子の実数」を出す。

    この2つは率だけ見ると分母が違うことに気づけない。
      出現率        = 自社が登場した回答 ÷ 全回答
      自社サイト引用率 = 根拠に toyota.jp が入った回答 ÷ 自社が登場した回答
    画面でファネルとして見せるため、割合ではなく件数をそのまま渡す。
    """
    cells = snap.get("cells") or []
    own_cells = [c for c in cells if (c.get("brands", {}).get(own) or {}).get("mentioned")]
    n = len(own_cells)
    owned = sum(1 for c in own_cells if c.get("own_cited"))
    dealer = sum(1 for c in own_cells if c.get("dealer_cited") or c.get("affiliated_cited"))
    both = sum(1 for c in own_cells if c.get("own_cited")
               or c.get("dealer_cited") or c.get("affiliated_cited"))
    return {"cited_base": n, "cited_owned": owned,
            "cited_dealer": dealer, "cited_any": both}


def _daydiff(a: str, b: str):
    """a - b を日数で。どちらかが空なら None。"""
    from datetime import date
    try:
        x = date(*map(int, a.split("-")))
        y = date(*map(int, b.split("-")))
        return (x - y).days
    except Exception:
        return None


def _platform_audit(day: str, days: int = 30) -> list[dict]:
    """設定した重みが、実測の被引用構成比とズレていないかを監査する。

    weight は初期値を外部調査から置いたものなので、自前の実測が溜まったら
    そちらに寄せるのが正しい。ここでは直近N日の実測構成比を出し、
    設定値との差を並べる（自動では変えない。勝手に変えると時系列が切れるため）。
    """
    tot = Counter()
    for i in range(days):
        sn = read_json(snapshot_path(days_ago(day, i)))
        if not sn:
            continue
        for p in sn.get("platforms", []):
            tot[p["id"]] += p.get("market_citations", 0)
    grand = sum(tot.values()) or 1
    cur = read_json(snapshot_path(day)) or {}
    wsum = sum(p["weight"] for p in cur.get("platforms", [])) or 1
    out = []
    for p in cur.get("platforms", []):
        actual = tot[p["id"]] / grand * 100
        setw = p["weight"] / wsum * 100
        out.append({
            "id": p["id"], "label": p["label"], "kind": p["kind"],
            "weight": p["weight"], "set_share": round(setw, 2),
            "actual_share": round(actual, 2),
            "gap": round(actual - setw, 2),
            "observed": tot[p["id"]],
            "suggested_weight": round(actual / 100, 3),
            "actionable": p["actionable"],
        })
    out.sort(key=lambda x: -x["actual_share"])
    return out


def _churn_block(day: str, rmeta: dict) -> dict:
    """クエリの新陳代謝（収穫ログ）をダッシュボード用にまとめる。"""
    log = read_json(DATA / "harvest_log.json", []) or []
    if not log:
        return {}
    last = log[-1]
    hc = load("harvest")
    src_label = {k: v["label"] for k, v in hc["sources"].items()}
    src_label.update({"harvest": "収穫", "-": "初期設定", "chiebukuro": "Yahoo!知恵袋の実質問",
                      "paa": "Google「関連する質問」", "suggest": "Googleサジェスト",
                      "llm_fanout": "AIに言い換えさせた質問", "fanout": "AIが自分で調べ直した語",
                      "semrush_import": "SEOトピック輸入", "gsc": "GSC実クエリ"})
    def deco(items):
        out = []
        for x in items:
            m = rmeta.get(x["id"], {})
            out.append({**x, "category": m.get("category"), "driver": m.get("driver"),
                        "source_label": src_label.get(m.get("source") or x.get("source", "-"),
                                                      m.get("source") or "-"),
                        "volume": m.get("volume") or x.get("volume") or 0})
        return out
    return {
        "last_run": last["date"],
        "candidates": last["candidates"],
        "added": deco(last["added"]),
        "promoted": deco(last["promoted"]),
        "demoted": deco(last["demoted"]),
        "surged": deco(last.get("surged", [])),
        "tiers": last["tiers"],
        "history": [{"date": e["date"], "added": len(e["added"]),
                     "promoted": len(e["promoted"]), "demoted": len(e["demoted"]),
                     "surged": len(e.get("surged", [])),
                     "core": e["tiers"].get("core", 0)} for e in log[-12:]],
        "events": _active_events(day),
        "weights": hc["demand_weights"],
        "rules": hc["promotion"],
        "sources": [{"id": k, "label": v["label"]} for k, v in hc["sources"].items()],
    }


def build_site(day: str) -> None:
    snap = read_json(snapshot_path(day))
    if not snap:
        raise SystemExit(f"snapshot not found: {day}")

    cfg, br, pf = load("settings"), load("brands"), load("platforms")
    own = br["own"]["id"]
    labels = {br["own"]["id"]: br["own"]["label"], **{c["id"]: c["label"] for c in br["competitors"]}}
    drivers = {d["id"]: d for d in br["drivers"]}
    surfaces = {s["id"]: s for s in cfg["surfaces"]}

    # ---- 時系列 ----
    hist = []
    for d in [days_ago(day, i) for i in range(HISTORY_DAYS - 1, -1, -1)]:
        s = read_json(snapshot_path(d))
        if not s:
            continue
        _sg = _fixed_signals(d, s["signals"])
        hist.append({
            "date": d, "mode": s.get("mode", "demo"), "score": s["score"], **s["factors"],
            "ai_sessions": sum(_sg["ga4_ai_sessions"].values()),
            "crawler_hits": sum(s["signals"]["crawler_hits"].values()),
            "sov_own": (s["brands"].get(own) or {}).get("sov"),
            "brands": {bid: {"sov": (bv or {}).get("sov"),
                             "mentions": (bv or {}).get("mentions")}
                       for bid, bv in (s.get("brands") or {}).items()},
        })

    # ---- クエリ表（プロンプト単位に畳む）----
    prompts = {p["id"]: p for p in __import__("common").load_prompts("core")}
    surface_label = {k: v.get("label", k) for k, v in surfaces.items()}
    rows = {}
    for c in snap["cells"]:
        r = rows.setdefault(c["prompt_id"], {
            "id": c["prompt_id"], "text": prompts.get(c["prompt_id"], {}).get("text", ""),
            "category": c["category"], "driver": c["driver"],
            "driver_label": drivers.get(c["driver"], {}).get("label", c["driver"]),
            "brand_query": c["brand_query"], "surfaces": {},
            "source": prompts.get(c["prompt_id"], {}).get("source", "-"),
            "own_cited": False, "platforms": set(), "negatives": set(),
            "_cites": {},          # url -> {…, surfaces:set}
        })
        r["surfaces"][c["surface"]] = {
            "answer": c.get("answer", ""),
            "rank": c["brands"][own]["rank"],
            "mention_rate": c["brands"][own]["mention_rate"],
            "sentiment": c["brands"][own]["sentiment"],
            # ブランドを後から足すと、それ以前のスナップショットには存在しない。
            # 欠けているブランドは None（未計測）として扱い、画面を落とさない。
            "competitors": {b: (c["brands"].get(b) or {}).get("rank")
                            for b in labels if b != own},
        }
        r["own_cited"] = r["own_cited"] or c["own_cited"]
        r["platforms"] |= {x["platform"] for x in c["citations"] if x["platform"]}
        r["negatives"] |= set(c["negatives"])
        for cit in c["citations"]:
            e = r["_cites"].setdefault(cit["url"], {
                "url": cit["url"], "title": cit.get("title", ""), "host": cit["host"],
                "bucket": cit["bucket"], "platform": cit["platform"], "surfaces": set()})
            e["surfaces"].add(c["surface"])

    BUCKET_ORDER = {"owned": 0, "dealer": 1, "affiliated": 2, "earned": 3, "media": 4,
                    "reference": 4, "press": 5, "competitor": 6, "noise": 7}
    owned_index: dict[str, dict] = {}     # 自社ページ → どのクエリで引かれたか（逆引き）

    query_rows = []
    for r in rows.values():
        rks = [v["rank"] for v in r["surfaces"].values() if v["rank"]]
        cites = sorted(r.pop("_cites").values(),
                       key=lambda x: (BUCKET_ORDER.get(x["bucket"], 9), -len(x["surfaces"])))
        for c in cites:
            c["surfaces"] = sorted(c["surfaces"])
            c["surface_labels"] = [surface_label.get(s, s) for s in c["surfaces"]]
            c["path"] = _short_path(c["url"])
        own_pages = [c for c in cites if c["bucket"] == "owned"]
        aff_pages = [c for c in cites if c["bucket"] in ("affiliated", "dealer")]
        dealer_pages = [c for c in cites if c["bucket"] == "dealer"]

        for c in own_pages:                                    # 逆引きインデックスを作る
            e = owned_index.setdefault(c["url"], {
                "url": c["url"], "title": c["title"], "path": c["path"],
                "queries": [], "surfaces": set(), "n": 0})
            e["queries"].append({"id": r["id"], "text": r["text"],
                                 "category": r["category"],
                                 "rank": min(rks) if rks else None,
                                 "surfaces": c["surface_labels"]})
            e["surfaces"] |= set(c["surface_labels"])
            e["n"] += len(c["surfaces"])

        query_rows.append({**r,
                           "platforms": sorted(r["platforms"]),
                           "negatives": sorted(r["negatives"]),
                           "citations": cites,
                           "own_pages": own_pages,
                           "affiliated_pages": aff_pages,
                           "dealer_pages": dealer_pages,
                           "n_citations": len(cites),
                           "best_rank": min(rks) if rks else None,
                           "avg_rank": round(sum(rks) / len(rks), 1) if rks else None,
                           "coverage": round(len(rks) / max(len(r["surfaces"]), 1) * 100)})
    # ---- レジストリの情報（需要スコア・在籍期間・新規判定）を各行に載せる ----
    try:
        import registry as _R
        reg = _R.load_registry()
        rmeta = {p["id"]: p for p in reg["prompts"]}
    except Exception:
        rmeta = {}
    obs = load("harvest")["promotion"]["observation_days"]
    cohort_ids = set()
    for r in query_rows:
        m = rmeta.get(r["id"], {})
        r["demand"] = m.get("demand")
        r["volume"] = m.get("volume") or 0
        r["fanout_hits"] = m.get("fanout_hits") or 0
        r["added_on"] = m.get("added_on")
        r["tier_since"] = m.get("tier_since")
        r["demand_history"] = m.get("demand_history") or []
        days = None
        if m.get("tier_since"):
            from datetime import date as _d
            days = (_d.fromisoformat(day) - _d.fromisoformat(m["tier_since"])).days
        r["days_in_core"] = days
        r["is_new"] = (days is not None and days < obs)
        if not r["is_new"]:
            cohort_ids.add(r["id"])
    query_rows.sort(key=lambda x: (x["best_rank"] is None, x["best_rank"] or 99))

    # ---- クエリ単位の前日比 / 先週比（順位と自社引用の変化）----
    from common import days_ago as _da
    from common import prev_snapshot_day as _pd
    prev_maps = {}
    for key, n in (("dod", 1), ("wow", 7)):
        _p = _pd(day, n)
        ps = read_json(snapshot_path(_p)) if _p else None
        if not ps:
            continue
        m = {}
        for c in ps["cells"]:
            e = m.setdefault(c["prompt_id"], {"ranks": [], "own_cited": False})
            if c["brands"][own]["rank"]:
                e["ranks"].append(c["brands"][own]["rank"])
            e["own_cited"] = e["own_cited"] or c["own_cited"]
        prev_maps[key] = m
    for r in query_rows:
        r["trend"] = {}
        for key, m in prev_maps.items():
            q = m.get(r["id"])
            if not q:
                r["trend"][key] = None
                continue
            pb = min(q["ranks"]) if q["ranks"] else None
            r["trend"][key] = {
                "prev_best": pb,
                "rank_delta": (pb - r["best_rank"]) if (pb and r["best_rank"]) else None,
                "was_cited": q["own_cited"],
                "cite_change": ("gained" if r["own_cited"] and not q["own_cited"]
                                else "lost" if q["own_cited"] and not r["own_cited"] else "same"),
            }

    # ---- カテゴリ別の傾向（概要用）----
    # 出現率は「プロンプト×サーフェス」のセル単位で数える。
    # プロンプト単位だと4サーフェスのどれかに出れば100%になり、飽和して意味を失うため。
    def _cat_stats(cells):
        agg = defaultdict(lambda: {"cells": 0, "mentioned": 0, "ranks": [], "cited": 0})
        for c in cells:
            a = agg[c["category"]]
            a["cells"] += 1
            if c["brands"][own]["mentioned"]:
                a["mentioned"] += 1
                if c["brands"][own]["rank"]:
                    a["ranks"].append(c["brands"][own]["rank"])
            if c["own_cited"]:
                a["cited"] += 1
        return agg

    now_agg = _cat_stats(snap["cells"])
    _p7 = _pd(day, 7)
    ps7 = read_json(snapshot_path(_p7)) if _p7 else None
    prev_agg = _cat_stats(ps7["cells"]) if ps7 else {}
    qcount = Counter(r["category"] for r in query_rows)

    CATJ = {"purchase": "購入検討", "model": "車種・スペック", "safety": "安全・運転支援",
            "cost": "価格・維持費", "eco": "燃費・電動化", "service": "整備・アフター",
            "brand": "ブランド指名"}
    category_trend = []
    for cid, a in now_agg.items():
        rate = a["mentioned"] / a["cells"] * 100 if a["cells"] else 0
        pv = prev_agg.get(cid)
        prate = (pv["mentioned"] / pv["cells"] * 100) if pv and pv["cells"] else None
        category_trend.append({
            "id": cid, "label": CATJ.get(cid, cid), "queries": qcount.get(cid, 0),
            "presence": round(rate, 1),
            "presence_prev": round(prate, 1) if prate is not None else None,
            "delta": round(rate - prate, 1) if prate is not None else None,
            "cited": a["cited"], "cells": a["cells"],
            "cite_rate": round(a["cited"] / a["cells"] * 100, 1) if a["cells"] else 0,
            "avg_rank": round(sum(a["ranks"]) / len(a["ranks"]), 2) if a["ranks"] else None,
        })
    category_trend.sort(key=lambda x: -x["presence"])

    # ---- クエリの出所内訳 ----
    SRCJ = {"chiebukuro": "知恵袋に実際に書かれた質問", "paa": "Googleの「関連する質問」",
            "llm_fanout": "AIに言い換えさせた質問", "fanout": "AIが自分で調べ直した語",
            "suggest": "Googleの検索候補", "semrush_import": "検索キーワード調査から",
            "harvest": "自動収集（経路の記録なし）", "gsc": "自社サイトへの検索流入から",
            "-": "運用開始時の初期設定"}
    src_counter = Counter(r["source"] for r in query_rows)
    query_sources = [{"id": k, "label": SRCJ.get(k, k), "n": v}
                     for k, v in src_counter.most_common()]

    owned_pages = sorted(
        ({**v, "surfaces": sorted(v["surfaces"]), "n_queries": len(v["queries"])}
         for v in owned_index.values()),
        key=lambda x: (-x["n"], -x["n_queries"]))

    # ---- 判断軸マトリクス ----
    driver_rows = []
    for did, meta in drivers.items():
        counts = snap["drivers"].get(did, {})
        mine = counts.get(own, 0)
        top = max(((b, n) for b, n in counts.items()), key=lambda x: x[1], default=(None, 0))
        driver_rows.append({"id": did, "label": meta["label"], "priority": meta["priority"],
                            "counts": {labels.get(b, b): n for b, n in counts.items()},
                            "own": mine, "leader": labels.get(top[0], "—"), "leader_n": top[1],
                            "gap": top[1] - mine})
    driver_rows.sort(key=lambda x: (-{"high": 2, "mid": 1, "low": 0}[x["priority"]], -x["gap"]))

    out = {
        "generated_at": day,
        "site": cfg["site"],
        "mode": snap.get("mode", "demo"),
        # その日どの面を測ったか／比較の土台がどれか。母集団が違う日を
        # 並べて見せてしまわないよう、画面にも出す。
        # 陣営別のオウンド引用率（表示専用。GEOスコアは toyota.jp 基準のまま）
        "citation_scopes": snap.get("citation_scopes"),
        "sentiment_detail": snap.get("sentiment_detail"),
        "tiers": cfg.get("tiers", {}),
        "sov_brands": snap.get("sov_brands"),
        "live_days": sum(1 for d in list_snapshots()
                         if (read_json(snapshot_path(d)) or {}).get("mode") == "live"),
        "measured": {
            "surfaces": [surfaces.get(s, {}).get("label", s)
                         for s in snap.get("surfaces_measured", [])],
            "basis": [surfaces.get(s, {}).get("label", s)
                      for s in (snap.get("cohort", {}).get("surfaces") or [])],
            "weekly": [surfaces.get(s["id"], {}).get("label", s["id"])
                       for s in cfg["surfaces"] if s.get("enabled") and s.get("weekdays")],
        },
        "score": snap["score"],
        "factors": snap["factors"],
        "weights": cfg["score_weights"],
        "counts": {**snap["counts"], **_citation_counts(snap, own)},
        "diff": _fixed_diff(day, snap.get("diff", {})),
        "comment": snap.get("comment", {}),
        "brands": [{"id": b, "label": labels[b], **v} for b, v in snap["brands"].items()],
        "platforms": snap["platforms"],
        "surfaces": [{"id": k, "label": surfaces.get(k, {}).get("label", k), "count": v}
                     for k, v in snap["surface_dist"].items()],
        "top_domains": [{"host": h, "n": n} for h, n in snap["top_domains"]],
        "citation_buckets": snap["citation_buckets"],
        "negative_drivers": snap["negative_drivers"],
        "drivers": driver_rows,
        "queries": query_rows,
        "owned_pages": owned_pages,
        "category_trend": category_trend,
        "query_sources": query_sources,
        "prompt_tiers": cfg["sampling"]["tier_schedule"],
        "cohort": snap.get("cohort", {}),
        "churn": _churn_block(day, rmeta),
        "platform_audit": _platform_audit(day),
        "signals": _fixed_signals(day, snap["signals"]),
        "history": hist,
        "newmodels": _newmodels(day, query_rows),
        "models": _model_status(day, query_rows),
        "bucket_domains": _bucket_domains(snap),
        "available_days": len(list_snapshots()),
    }
    write_json(DOCS / "data" / "latest.json", out, compact=True)
    print(f"  wrote docs/data/latest.json ({len(hist)} days of history)")


if __name__ == "__main__":
    # push 起因の再ビルド用。AIへの実行はせず、最新スナップショットから
    # docs/data/latest.json を作り直すだけ（＝APIコストゼロ）。
    import sys
    day = sys.argv[1] if len(sys.argv) > 1 else (list_snapshots() or [""])[-1]
    if not day:
        raise SystemExit("スナップショットがありません")
    print(f"rebuild from snapshot: {day}")
    build_site(day)
