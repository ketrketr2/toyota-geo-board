"""日次の自動コメントを、要因分解から機械的に生成する。

LLMは使わない（毎日走るのでコストと再現性を優先）。
文章はテンプレートだが、選ぶ要因・数値・該当プロンプトはすべてデータ由来。
"""
from __future__ import annotations

from common import load, read_json, snapshot_path

FACTOR_JA = {
    "presence": "出現率", "rank_quality": "順位品質", "owned_citation": "オウンド引用率",
    "earned_citation": "アーンド引用率（SNS）", "sentiment": "センチメント",
    "share_of_voice": "相対シェア",
}
PERIOD_JA = {"dod": "前日比", "wow": "先週比", "mom": "先月比"}


def _fmt(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.2f}"


def headline(day: str, diffs: dict) -> str:
    m = diffs["metrics"]["score"]
    cur = m["smoothed"] or m["value"] or 0
    d = m["deltas"]["dod"]
    if d["delta"] is None:
        return f"GEOスコア {cur:.1f}。比較対象日のデータが無いため差分は算出していません。"
    if d["significant"] is False:
        return (f"GEOスコア {cur:.1f}（前日比 {_fmt(d['delta'])}pt）。"
                f"日次のばらつき（±{2*(m['sigma'] or 0):.2f}pt）の範囲内で、"
                f"有意な変化ではありません。")
    direction = "上昇" if d["delta"] > 0 else "低下"
    return (f"GEOスコア {cur:.1f}（前日比 {_fmt(d['delta'])}pt）。"
            f"日次ばらつきを超える{direction}です。")


def factor_lines(diffs: dict, period: str = "dod") -> list[str]:
    dec = diffs["decomposition"].get(period) or {}
    contrib = dec.get("contributions") or {}
    if not contrib:
        return []
    ranked = sorted(contrib.items(), key=lambda x: -abs(x[1]))
    lines = []
    for k, v in ranked[:3]:
        if abs(v) < 0.01:
            continue
        lines.append(f"{FACTOR_JA.get(k, k)} {_fmt(v)}pt")
    return lines


def sns_lines(diffs: dict, period: str = "dod") -> list[str]:
    rows = diffs["platform_decomposition"].get(period) or []
    moved = [r for r in rows if abs(r["contribution"]) >= 0.01]
    moved.sort(key=lambda r: -abs(r["contribution"]))
    out = []
    for r in moved[:3]:
        d_cite = r["own_citations"] - r["prev_own"]
        detail = f"引用{'+' if d_cite >= 0 else ''}{d_cite}件" if d_cite else "シェア変動"
        out.append(f"{r['label']} {_fmt(r['contribution'])}pt（{detail}）")
    return out


def alerts(day: str) -> list[dict]:
    """順位急落・引用消失・新規ネガを拾う。"""
    cfg = load("settings")["alerts"]
    cur = read_json(snapshot_path(day))
    prev = read_json(snapshot_path(__import__("common").days_ago(day, 1)))
    if not cur or not prev:
        return []
    own = load("brands")["own"]["id"]
    pcells = {(c["prompt_id"], c["surface"]): c for c in prev["cells"]}
    out = []

    for c in cur["cells"]:
        p = pcells.get((c["prompt_id"], c["surface"]))
        if not p:
            continue
        r_now, r_prev = c["brands"][own]["rank"], p["brands"][own]["rank"]
        if r_prev and r_now and (r_now - r_prev) >= cfg["rank_drop_threshold"]:
            out.append({"type": "rank_drop", "severity": "high",
                        "prompt_id": c["prompt_id"], "surface": c["surface"],
                        "text": f"順位が {r_prev}位 → {r_now}位 に低下"})
        if r_prev and not r_now:
            out.append({"type": "dropped_out", "severity": "high",
                        "prompt_id": c["prompt_id"], "surface": c["surface"],
                        "text": f"{r_prev}位から圏外に脱落"})
        if p["own_cited"] and not c["own_cited"]:
            out.append({"type": "citation_lost", "severity": "mid",
                        "prompt_id": c["prompt_id"], "surface": c["surface"],
                        "text": "自社ドメインの引用が消失"})

    new_neg = set(cur["negative_drivers"]) - set(prev["negative_drivers"])
    for n in new_neg:
        out.append({"type": "new_negative", "severity": "mid", "prompt_id": None,
                    "surface": None, "text": f"新規ネガ要因を検出: {n}"})
    return out[:30]


def build(day: str, diffs: dict) -> dict:
    lines = {}
    for p in ("dod", "wow", "mom"):
        dec = diffs["decomposition"].get(p) or {}
        total = dec.get("total")
        body = []
        if total is not None:
            body.append(f"{PERIOD_JA[p]} {_fmt(total)}pt")
        f = factor_lines(diffs, p)
        if f:
            body.append("内訳：" + " / ".join(f))
        s = sns_lines(diffs, p)
        if s:
            body.append("SNS内訳：" + " / ".join(s))
        lines[p] = body

    al = alerts(day)
    return {
        "headline": headline(day, diffs),
        "periods": lines,
        "alerts": al,
        "alert_summary": (f"要対応 {sum(1 for a in al if a['severity']=='high')}件 / "
                          f"注意 {sum(1 for a in al if a['severity']=='mid')}件") if al else "アラートなし",
    }
