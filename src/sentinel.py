"""スナップショットの健全性を検査し、壊れた日を本番に出さないための門番。

きっかけ（2026-08-28）:
Google の「AIによる概要」「AIモード」の引用URLが追跡リダイレクト
（https://google.com/goto?url=CAES…）に変わり、引用元が全部 google.com に化けた。
回答は正常に返っていたので既存の「取得件数」チェックは素通りし、SNS引用も
オウンド引用も 0% のボードがそのまま公開された。

教訓は「件数が揃っていても中身が壊れることがある」。件数ではなく“分布”を見る。
ここで異常と判定した日はスナップショットを本番に置かず、ボードは直前の
正常な日を出し続ける（＝古いが正しい、を、新しいが嘘、より優先する）。
"""
from __future__ import annotations

from collections import Counter

from common import list_snapshots, read_json, snapshot_path

# 中継URLのパターン。URLが中継のままでも、引用元ホストが解決できていれば正常。
# （Gemini は vertexaisearch の中継URLで返るが title から実ドメインを取れる設計）
REDIRECTOR_MARKS = ("google.com/goto", "vertexaisearch.cloud.google.com",
                    "grounding-api-redirect")
# 「解決できなかった」印。中継元のホスト名が引用元として残っている状態を指す。
REDIRECTOR_HOSTS = ("google.com", "vertexaisearch.cloud.google.com")

# 比較に使う過去日数（同じ面セットの日だけを対象にする）
HISTORY_DAYS = 14
# 過去中央値に対してこれを下回ったら急落とみなす
COLLAPSE_RATIO = 0.4
# 1ホストが引用全体に占める割合の上限。正常日は最大でも1割前後
MAX_SINGLE_HOST_SHARE = 0.35
# 未解決の中継URLの許容割合
MAX_REDIRECTOR_SHARE = 0.05


def _citations(snap: dict) -> list[dict]:
    return [cit for c in snap.get("cells", []) for cit in c.get("citations", [])]


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _history(day: str, surface_key: str, n: int = HISTORY_DAYS) -> list[dict]:
    """比較対象。面セットが同じで、かつ健全だった日だけを使う。

    面が違う日を混ぜると母集団の差を異常と読み違える。隔離した日を混ぜると
    壊れた値が基準になり、翌日以降の異常を見逃す（＝汚染の固定化）。
    """
    out = []
    for d in reversed(list_snapshots()):
        if d >= day:
            continue
        s = read_json(snapshot_path(d)) or {}
        if s.get("quarantined"):
            continue
        if surface_key and s.get("surface_key") != surface_key:
            continue
        out.append(s)
        if len(out) >= n:
            break
    return out


def inspect(snap: dict, day: str | None = None) -> dict:
    """健全性を検査して {ok, checks, reasons} を返す。"""
    day = day or snap.get("date", "")
    cites = _citations(snap)
    total = len(cites)
    reasons, checks = [], {}

    # --- 1) 引用がそもそも取れているか ---
    checks["citations"] = total
    if total == 0:
        reasons.append("引用が1件も取れていない")

    if total:
        # --- 2) 1ホスト寡占（今回の事故はここで 96% になっていた）---
        hosts = Counter(c.get("host") or "" for c in cites)
        top_host, top_n = hosts.most_common(1)[0]
        share = top_n / total
        checks["top_host"] = {"host": top_host, "share": round(share, 4)}
        if share > MAX_SINGLE_HOST_SHARE:
            reasons.append(
                f"引用の{share*100:.0f}%が単一ホスト({top_host})に集中"
                f"（上限{MAX_SINGLE_HOST_SHARE*100:.0f}%）。"
                "リダイレクト解決漏れなど分類の破綻が疑われる")

        # --- 3) 中継URLが「解決されずに」残っていないか ---
        # 中継URLであること自体は異常ではない。異常なのは、引用元が中継元の
        # ホスト（google.com 等）のままになっていること。そこを数える。
        red = sum(1 for c in cites
                  if any(m in (c.get("url") or "") for m in REDIRECTOR_MARKS)
                  and (c.get("host") or "") in REDIRECTOR_HOSTS)
        checks["unresolved_redirects"] = {"count": red, "share": round(red / total, 4)}
        if red / total > MAX_REDIRECTOR_SHARE:
            reasons.append(
                f"中継URLの引用元が未解決のまま{red}件({red/total*100:.0f}%)残っている"
                "（引用元が中継ドメインのままになっている）")

        # --- 4) 分類が全部 media/noise に落ちていないか ---
        buckets = Counter(c.get("bucket") for c in cites)
        classified = sum(v for k, v in buckets.items()
                         if k not in ("media", "noise", "", None))
        checks["classified_share"] = round(classified / total, 4)
        if classified == 0:
            reasons.append("owned/dealer/earned のいずれにも分類された引用が無い")

    # --- 5) 履歴との比較（急落の検知）---
    hist = _history(day, snap.get("surface_key", ""))
    checks["history_days"] = len(hist)
    if len(hist) >= 3:
        def med(pick):
            return _median([v for v in (pick(h) for h in hist) if v is not None])

        targets = {
            "earned_citation": lambda s: (s.get("factors") or {}).get("earned_citation"),
            "owned_citation": lambda s: (s.get("factors") or {}).get("owned_citation"),
            "citations": lambda s: len(_citations(s)) or None,
        }
        for name, pick in targets.items():
            base = med(pick)
            cur = pick(snap)
            if cur is None or base <= 0:
                continue
            checks[f"{name}_vs_median"] = {"now": round(cur, 2), "median": round(base, 2)}
            # 中央値が十分大きい指標だけを見る。もともと小さい値の増減は誤検知になる
            if base >= 5 and cur < base * COLLAPSE_RATIO:
                reasons.append(
                    f"{name} が過去中央値 {base:.1f} に対し {cur:.1f} まで急落"
                    f"（{COLLAPSE_RATIO*100:.0f}%未満）")

    return {"ok": not reasons, "checks": checks, "reasons": reasons}


def format_report(day: str, verdict: dict) -> str:
    lines = [f"{day} スナップショット健全性チェック: "
             + ("正常" if verdict["ok"] else "異常（本番に出しません）")]
    for r in verdict["reasons"]:
        lines.append(f"  - {r}")
    lines.append("  検査値: " + repr(verdict["checks"]))
    if not verdict["ok"]:
        lines.append("")
        lines.append("ボードは直前の正常な日のデータを表示し続けます。"
                     "原因を直してから Actions → daily.yml → Run workflow で再実行してください。")
    return "\n".join(lines)
