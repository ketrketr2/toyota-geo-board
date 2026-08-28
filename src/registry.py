"""クエリレジストリ: 全プロンプトの一元管理。

設計の肝は「IDを絶対に振り直さない」こと。
クエリは追加され、tier(core/strategic/longtail/retired) が変わるだけで、
一度発行した ID が別の文言を指すことは無い。これが崩れると時系列が全部壊れる。

  core      : 日次実行。前日比を見る対象。
  strategic : 週次実行。網羅性の担当。
  longtail  : 月次実行。収穫したばかりの候補もここに入る。
  retired   : 需要が消えたもの。履歴のため削除はせず残す。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

from common import ROOT, today

REGISTRY = ROOT / "prompts" / "registry.yaml"
# car/local はメインボードのローテーション（rebalance）対象外の独立枠。
#   car   = ①車種別AI分析の現役枠 / car_bench   = その補欠（週次入替で昇降格）
#   local = ②ディーラーAI分析の現役枠 / local_bench = その補欠
TIERS = ("core", "strategic", "longtail", "retired",
         "car", "car_bench", "local", "local_bench")
DEMAND_HISTORY_MAX = 12


# ---------------------------------------------------------------- 正規化 / 重複判定
_PUNCT = re.compile(r"[\s　、。，．・！？!?\"'“”‘’（）()\[\]「」【】：:；;／/\\\-—ー~〜]+")


def norm(text: str) -> str:
    """重複判定用に文字列を潰す。全角半角・記号・空白の揺れを吸収する。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    return _PUNCT.sub("", t)


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def similarity(a: str, b: str) -> float:
    """文字bigramのJaccard係数。日本語は分かち書きが要らないこれで十分。"""
    A, B = _bigrams(norm(a)), _bigrams(norm(b))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


# ---------------------------------------------------------------- 読み書き
def load_registry() -> dict:
    if not REGISTRY.exists():
        return {"version": 2, "updated_at": today(), "next_seq": 1, "prompts": []}
    with open(REGISTRY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_registry(reg: dict) -> None:
    reg["updated_at"] = today()
    reg["prompts"].sort(key=lambda p: p["id"])
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY, "w", encoding="utf-8") as f:
        f.write("# 自動生成 + 手編集可。IDは絶対に振り直さないこと。\n")
        f.write("# tier: core=日次 / strategic=週次 / longtail=月次 / retired=停止\n")
        yaml.safe_dump(reg, f, allow_unicode=True, sort_keys=False, width=10**6)


def prompts_for(tier: str = "core") -> list[dict]:
    """指定tierのプロンプトを、需要スコアの高い順で返す。"""
    reg = load_registry()
    rows = [p for p in reg["prompts"] if p.get("tier") == tier]
    rows.sort(key=lambda p: -(p.get("demand") or 0))
    return rows


def by_id(reg: dict) -> dict[str, dict]:
    return {p["id"]: p for p in reg["prompts"]}


# ---------------------------------------------------------------- 追加
def next_id(reg: dict) -> str:
    n = reg.get("next_seq", 1)
    reg["next_seq"] = n + 1
    return f"p{n:03d}"


def find_duplicate(reg: dict, text: str, threshold: float = 0.82) -> dict | None:
    """既存クエリと重複していないか。完全一致だけでなく近似も見る。"""
    n = norm(text)
    for p in reg["prompts"]:
        if norm(p["text"]) == n:
            return p
    for p in reg["prompts"]:
        if similarity(p["text"], text) >= threshold:
            return p
    return None


def add_candidate(reg: dict, cand: dict, day: str | None = None) -> dict | None:
    """候補を1件追加する。重複していたら None を返し、既存側の指標だけ更新する。"""
    day = day or today()
    dup = find_duplicate(reg, cand["text"])
    if dup:
        # 重複でも「別経路からも見つかった」情報は需要の裏付けになるので加算する
        dup["ugc_hits"] = (dup.get("ugc_hits") or 0) + (cand.get("ugc_hits") or 0)
        dup["fanout_hits"] = (dup.get("fanout_hits") or 0) + (cand.get("fanout_hits") or 0)
        if cand.get("volume") and cand["volume"] > (dup.get("volume") or 0):
            dup["volume"] = cand["volume"]
        return None

    p = {
        "id": next_id(reg),
        "text": cand["text"],
        "category": cand.get("category", "purchase"),
        "driver": cand.get("driver"),
        "brand": bool(cand.get("brand", False)),
        "source": cand.get("source", "harvest"),
        "tier": "longtail",              # 収穫直後は必ずロングテール枠から始める
        "added_on": day,
        "tier_since": day,
        "demand": round(float(cand.get("demand") or 0), 1),
        "volume": int(cand.get("volume") or 0),
        "fanout_hits": int(cand.get("fanout_hits") or 0),
        "ugc_hits": int(cand.get("ugc_hits") or 0),
        "growth": float(cand.get("growth") or 1.0),
        "keyword": cand.get("keyword") or cand.get("raw"),
        "demand_history": [],
    }
    reg["prompts"].append(p)
    return p


def push_demand(p: dict, day: str, score: float) -> None:
    h = p.setdefault("demand_history", [])
    if h and h[-1][0] == day:
        h[-1][1] = round(score, 1)
    else:
        h.append([day, round(score, 1)])
    del h[:-DEMAND_HISTORY_MAX]
    p["demand"] = round(score, 1)


def set_tier(p: dict, tier: str, day: str) -> None:
    assert tier in TIERS
    if p.get("tier") == tier:
        return
    p["tier_prev"] = p.get("tier")
    p["tier"] = tier
    p["tier_since"] = day


# ---------------------------------------------------------------- 移行
def migrate_from_core_yaml() -> dict:
    """旧 prompts/core.yaml を registry.yaml へ移す（初回のみ）。"""
    src = ROOT / "prompts" / "core.yaml"
    reg = {"version": 2, "updated_at": today(), "next_seq": 1, "prompts": []}
    if not src.exists():
        return reg
    with open(src, encoding="utf-8") as f:
        old = yaml.safe_load(f)["prompts"]
    day = min(p.get("added_on", "2026-06-15") for p in old) if old else today()
    seq = 0
    for p in old:
        seq = max(seq, int(re.sub(r"\D", "", p["id"]) or 0))
        reg["prompts"].append({
            "id": p["id"], "text": p["text"],
            "category": p.get("category", "purchase"), "driver": p.get("driver"),
            "brand": bool(p.get("brand", False)), "source": p.get("source", "-"),
            "tier": "core", "added_on": day, "tier_since": day,
            "demand": 50.0, "volume": 0, "fanout_hits": 0, "ugc_hits": 0,
            "growth": 1.0, "demand_history": [],
        })
    reg["next_seq"] = seq + 1
    return reg


if __name__ == "__main__":
    import sys
    if "--migrate" in sys.argv:
        r = migrate_from_core_yaml()
        save_registry(r)
        print(f"migrated {len(r['prompts'])} prompts -> {REGISTRY} (next_seq={r['next_seq']})")
