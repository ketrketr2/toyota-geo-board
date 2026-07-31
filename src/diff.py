"""前日比 / 先週比 / 先月比 と、有意判定つきの要因分解。

設計上の要点:
  - LLMは非決定的でモデル間不一致率は5割超。生の前日比はノイズが支配的。
    → 表示に使うのは 7日移動中央値どうしの差。生値は参考として併記する。
  - 過去30日の日次標準偏差から ±2σ を出し、その内側は「変化なし」と扱う。
  - スコア差分はかならず因数ごとの寄与に分解する（重み × 因数差）。
"""
from __future__ import annotations

import statistics as st

from common import days_ago, load, read_json, snapshot_path


def _series(dates: list[str], picker) -> list[float]:
    out = []
    for d in dates:
        snap = read_json(snapshot_path(d))
        if snap:
            v = picker(snap)
            if v is not None:
                out.append(v)
    return out


def rolling_median(day: str, picker, window: int) -> float | None:
    vals = _series([days_ago(day, i) for i in range(window)], picker)
    return st.median(vals) if vals else None


def sigma(day: str, picker, baseline_days: int) -> float | None:
    vals = _series([days_ago(day, i) for i in range(1, baseline_days + 1)], picker)
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
        prev = rolling_median(days_ago(day, n), picker, win)
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
    prev_day = days_ago(day, n)
    if not read_json(snapshot_path(prev_day)):
        return {}
    w = cfg["score_weights"]
    contrib, total = {}, 0.0
    for k, weight in w.items():
        cur = rolling_median(day, lambda s, kk=k: s["factors"].get(kk), win)
        prv = rolling_median(prev_day, lambda s, kk=k: s["factors"].get(kk), win)
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
    prev_day = days_ago(day, n)
    cur, prev = read_json(snapshot_path(day)), read_json(snapshot_path(prev_day))
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


def build(day: str) -> dict:
    """ダッシュボードが必要とする差分情報を一括で作る。"""
    metrics = {
        "score": lambda s: s["score"],
        "presence": lambda s: s["factors"]["presence"],
        "rank_quality": lambda s: s["factors"]["rank_quality"],
        "owned_citation": lambda s: s["factors"]["owned_citation"],
        "earned_citation": lambda s: s["factors"]["earned_citation"],
        "sentiment": lambda s: s["factors"]["sentiment"],
        "share_of_voice": lambda s: s["factors"]["share_of_voice"],
        "ai_sessions": lambda s: sum(s["signals"]["ga4_ai_sessions"].values()),
        "crawler_hits": lambda s: sum(s["signals"]["crawler_hits"].values()),
    }
    return {
        "metrics": {k: compare(day, f, k) for k, f in metrics.items()},
        "decomposition": {p: score_decomposition(day, p) for p in ("dod", "wow", "mom")},
        "platform_decomposition": {p: platform_decomposition(day, p) for p in ("dod", "wow", "mom")},
    }
