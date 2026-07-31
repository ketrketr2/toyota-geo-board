"""共通ユーティリティ: 設定読み込み・パス・日付・ドメイン正規化。"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
DOCS = ROOT / "docs"
JST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------- config
_cache: dict[str, dict] = {}


def load(name: str) -> dict:
    """config/<name>.yaml を読む（キャッシュあり）。"""
    if name not in _cache:
        with open(CONFIG / f"{name}.yaml", encoding="utf-8") as f:
            _cache[name] = yaml.safe_load(f)
    return _cache[name]


def load_prompts(tier: str = "core") -> list[dict]:
    """プロンプトはレジストリ（prompts/registry.yaml）が唯一の正。

    収穫で中身が入れ替わるので、旧 prompts/<tier>.yaml は
    レジストリが無い場合のフォールバックとしてのみ残してある。
    """
    reg = ROOT / "prompts" / "registry.yaml"
    if reg.exists():
        with open(reg, encoding="utf-8") as f:
            rows = yaml.safe_load(f)["prompts"]
        rows = [p for p in rows if p.get("tier") == tier]
        rows.sort(key=lambda p: -(p.get("demand") or 0))
        return rows
    with open(ROOT / "prompts" / f"{tier}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["prompts"]


def today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def days_ago(d: str, n: int) -> str:
    return (date.fromisoformat(d) - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- domains
def domain_of(url: str) -> str:
    """URL からホスト名を取り出し、www. と末尾ドットを落とす。"""
    try:
        host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    except ValueError:
        return ""
    host = host.split(":")[0].removeprefix("www.").rstrip(".")
    return host


def match_domain(host: str, patterns: list[str]) -> bool:
    """完全一致・サブドメイン一致・ワイルドカード(fnmatch)のいずれかで判定。"""
    for p in patterns:
        p = p.lower()
        if "*" in p:
            if fnmatch.fnmatch(host, p):
                return True
        elif host == p or host.endswith("." + p):
            return True
    return False


# ---------------------------------------------------------------- text
def contains_any(text: str, needles: list[str]) -> bool:
    return any(n and n in text for n in needles)


def first_index(text: str, needles: list[str]) -> int | None:
    """needles のいずれかが最初に現れる文字位置。無ければ None。"""
    hits = [text.find(n) for n in needles if n and n in text]
    return min(hits) if hits else None


_SENT_SPLIT = re.compile(r"[。．\n]")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


# ---------------------------------------------------------------- io
def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj, compact: bool = False) -> None:
    """compact=True は整形なしで書く。

    日次で積むファイル（スナップショット・latest.json）はインデントの空白だけで
    全体の3割を占めるため、機械しか読まないものは詰めて書く。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)


def prune_snapshots(keep_detail_days: int = 90) -> int:
    """古いスナップショットから明細(cells)を落とす。

    前日比・先週比・先月比が使うのは直近30日、履歴グラフも60日なので、
    それより古い明細は保持しても誰も読まない。スコアと因数だけ残す。
    """
    from datetime import date, timedelta
    cut = (date.today() - timedelta(days=keep_detail_days)).isoformat()
    n = 0
    for d in list_snapshots():
        if d >= cut:
            continue
        p = snapshot_path(d)
        s = read_json(p)
        if not s or "cells" not in s:
            continue
        s.pop("cells", None)
        s["pruned"] = True
        write_json(p, s, compact=True)
        n += 1
    return n


def prev_snapshot_day(day: str, n: int, max_back: int = 6) -> str | None:
    """n日前を起点に、スナップショットが実在する直近の日を返す。

    実行が1日失敗したり、GitHubのcronが遅延して1日飛んだりしても、
    「比較データなし」で無言になるのを防ぐ。見つからなければ None。
    """
    for i in range(n, n + max_back + 1):
        d = days_ago(day, i)
        if (SNAPSHOTS / f"{d}.json").exists():
            return d
    return None


def snapshot_path(d: str) -> Path:
    return SNAPSHOTS / f"{d}.json"


def list_snapshots() -> list[str]:
    if not SNAPSHOTS.exists():
        return []
    return sorted(p.stem for p in SNAPSHOTS.glob("*.json"))


def env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key, default)
    return v if v else default


def demo_mode() -> bool:
    """必要な認証情報が無ければデモモードで動かす。

    ただし「liveにしたのに認証情報が無い」場合は黙って落とさない。
    デモデータは本物そっくりに作ってあるので、黙って戻ると
    偽物を本物として見続けることになる。ここは止めるのが正しい。
    """
    want_live = env("GEO_BOARD_MODE", "demo") != "demo"
    has_key = bool(env("DATAFORSEO_LOGIN")) and bool(env("DATAFORSEO_PASSWORD"))
    if want_live and not has_key:
        missing = [k for k in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD") if not env(k)]
        raise SystemExit(
            f"GEO_BOARD_MODE=live ですが {', '.join(missing)} が空です。\n"
            "GitHub の Settings → Secrets and variables → Actions で、"
            "名前が1文字も違わないか確認してください。\n"
            "デモに黙って戻ると、合成データを実測だと思って見続けることになるため停止します。"
        )
    return not want_live
