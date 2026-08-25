"""前日比 / 先週比 / 先月比 と、有意判定つきの要因分解。

設計上の要点:
  - LLMは非決定的でモデル間不一致率は5割超。生の前日比はノイズが支配的。
    → 表示に使うのは 7日移動中央値どうしの差。生値は参考として併記する。
  - 過去30日の日次標準偏差から ±2σ を出し、その内側は「変化なし」と扱う。
  - スコア差分はかならず因数ごとの寄与に分解する（重み × 因数差）。
"""
from __future__ import annotations

import statistics as st

from common import (days_ago, load, prev_snapshot_day, read_json,
                    snapshot_mode, snapshot_path)


def _series(dates: list[str], picker, mode: str | None = None) -> list[float]:
    """移動中央値と標準偏差のもとになる系列。

    mode を指定すると、その種別（live / demo）の日だけを拾う。
    実測が始まった直後にデモの値が混ざると、実測でない数字が
    実測の前日比として出てしまうため、必ず分ける。
    """
    out = []
    for d in dates:
        snap = read_json(snapshot_path(d))
        if snap:
            if mode and snap.get("mode", "demo") != mode:
                continue
            v = picker(snap)
            if v is not None:
                out.append(v)
    return out


def rolling_median(day: str, picker, window: int, mode: str | None = None) -> float | None:
    vals = _series([days_ago(day, i) for i in range(window)], picker,
                   mode if mode is not None else snapshot_mode(day))
    return st.median(vals) if vals else None


def sigma(day: str, picker, baseline_days: int) -> float | None:
    vals = _series([days_ago(day, i) for i in range(1, baseline_days + 1)], picker,
                   snapshot_mode(day))
    return st.pstdev(vals) if len(vals) >= 5 else None


def compare(day: str, picker, label: str = "") -> dict:
    """1指標について DoD / WoW / MoM をまとめて返す。"""
    cfg = load("settings")["diff"]
    win, sig_n, base_n = cfg["smoothing_window"], cfg["significance_sigma"], cfg["baseline_days"]

    cur_raw = _series([day], picker)
    cur_raw = cur_raw[0] if cur_raw else None
    cur = rolling_median(day, picker, win)
    sd = sigma(day, picker, base_n)

    out = {"label": label, "value": cur_raw, "smoothed": cur, "sigma": sd, "deltas": {}}
    for key, n in cfg["compare"].items():
        # 欠測日があっても比較先を見つける（実行失敗・cron遅延への保険）。
        # ただし種別（実測/デモ）が違う日しか無いときは、無理に比べない。
        # 実測が始まった直後は比較先が無いのが正しく、そこに合成データを
        # 当てると「もっともらしい嘘の前日比」になる。
        pd = prev_snapshot_day(day, n)
        prev = rolling_median(pd, picker, win) if pd else None
        if cur is None or prev is None:
            out["deltas"][key] = {"delta": None, "prev": prev, "significant": None}
            continue
        d = cur - prev
        significant = (sd is not None and abs(d) > sig_n * sd) if sd is not None else None
        out["deltas"][key] = {"delta": round(d, 2), "prev": round(prev, 2),
                              "pct": (round(d / prev * 100, 1) if prev else None),
                              "significant": significant}
    return out


def score_decomposition(day: str, period: str = "dod") -> dict:
    """総合スコアの差分を、因数ごとの寄与（重み×因数差）に分解する。

    ノイズを避けるため、因数も 7日移動中央値どうしで比較する。
    したがって寄与の合計と、カードに出るスコア差分は必ず一致する。
    """
    cfg = load("settings")
    n = cfg["diff"]["compare"][period]
    win = cfg["diff"]["smoothing_window"]
    prev_day = prev_snapshot_day(day, n)
    if not prev_day:
        return {}
    w = cfg["score_weights"]
    contrib, total = {}, 0.0
    for k, weight in w.items():
        cur = rolling_median(day, lambda s, kk=k: _coh(s)["factors"].get(kk), win)
        prv = rolling_median(prev_day, lambda s, kk=k: _coh(s)["factors"].get(kk), win)
        if cur is None or prv is None:
            contrib[k] = 0.0
            continue
        c = (cur - prv) * weight / 100
        contrib[k] = round(c, 3)
        total += c
    return {"period": period, "total": round(total, 2),
            "contributions": dict(sorted(contrib.items(), key=lambda x: x[1]))}


def _plat_share(pid: str):
    def pick(s):
        for p in s["platforms"]:
            if p["id"] == pid:
                return p["share"]
        return None
    return pick


def platform_decomposition(day: str, period: str = "dod") -> list[dict]:
    """アーンド引用率の差分を、SNSプラットフォームごとの寄与に分解する。

    スコア側と揃えるため、シェアも7日移動中央値で比較する。
    """
    cfg = load("settings")
    n, win = cfg["diff"]["compare"][period], cfg["diff"]["smoothing_window"]
    ew = cfg["score_weights"]["earned_citation"]
    prev_day = prev_snapshot_day(day, n)
    cur = read_json(snapshot_path(day))
    prev = read_json(snapshot_path(prev_day)) if prev_day else None
    if not cur or not prev:
        return []
    pv = {p["id"]: p for p in prev["platforms"]}
    rows = []
    for p in cur["platforms"]:
        q = pv.get(p["id"])
        if not q:
            continue
        s_now = rolling_median(day, _plat_share(p["id"]), win) or 0
        s_prv = rolling_median(prev_day, _plat_share(p["id"]), win) or 0
        d_share = (s_now - s_prv) / 100
        rows.append({
            "smoothed_share": round(s_now, 2),
            "delta_share": round(s_now - s_prv, 2),
            "id": p["id"], "label": p["label"],
            "share": p["share"], "prev_share": q["share"],
            "contribution": round(p["weight"] * d_share * ew, 3),
            "own_citations": p["own_citations"], "prev_own": q["own_citations"],
            "market_citations": p["market_citations"],
        })
    return sorted(rows, key=lambda r: r["contribution"])


def _coh(s: dict) -> dict:
    """比較には基準コホートの値を使う。

    クエリを入れ替えると全体スコアは母集団の変化でも動いてしまう。
    「ずっと測り続けているクエリ」だけを見れば、動いた＝実力が動いた、になる。
    古いスナップショット（コホート導入前）は全体値にフォールバックする。
    """
    return s.get("cohort") or {"score": s["score"], "factors": s["factors"]}


# ---- GA4実測レイヤー（build_site と同じ規約: スナップショット日Dには前日D-1の実測を使う） ----
_GA4_REAL_CACHE = None


def ga4_fixed_picker(s: dict) -> float:
    """ai_sessions の picker。data/ga4_daily.json に実測があればそちらを使う。"""
    global _GA4_REAL_CACHE
    if _GA4_REAL_CACHE is None:
        from common import DATA, read_json
        _GA4_REAL_CACHE = read_json(DATA / "ga4_daily.json", default={}) or {}
    day = s.get("date", "")
    if day:
        from common import days_ago
        rec = _GA4_REAL_CACHE.get(days_ago(day, 1))
        if rec:
            return sum(v for k, v in rec.items() if not str(k).startswith("_"))
    return sum(s["signals"]["ga4_ai_sessions"].values())


def build(day: str) -> dict:
    """ダッシュボードが必要とする差分情報を一括で作る。"""
    metrics = {
        "score": lambda s: _coh(s)["score"],
        "presence": lambda s: _coh(s)["factors"]["presence"],
        "rank_quality": lambda s: _coh(s)["factors"]["rank_quality"],
        "owned_citation": lambda s: _coh(s)["factors"]["owned_citation"],
        "earned_citation": lambda s: _coh(s)["factors"]["earned_citation"],
        "sentiment": lambda s: _coh(s)["factors"]["sentiment"],
        "share_of_voice": lambda s: _coh(s)["factors"]["share_of_voice"],
        "ai_sessions": ga4_fixed_picker,
        "crawler_hits": lambda s: sum(s["signals"]["crawler_hits"].values()),
    }
    return {
        "metrics": {k: compare(day, f, k) for k, f in metrics.items()},
        "decomposition": {p: score_decomposition(day, p) for p in ("dod", "wow", "mom")},
        "platform_decomposition": {p: platform_decomposition(day, p) for p in ("dod", "wow", "mom")},
    }
