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


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)


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
    """必要な認証情報が無ければデモモードで動かす。"""
    return env("GEO_BOARD_MODE", "demo") == "demo" or not env("DATAFORSEO_LOGIN")
