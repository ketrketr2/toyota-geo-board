"""AI流入・AIクローラー・YouTube のサイド指標を集める。

- GA4        : AI Assistant チャネル + Claude/Perplexity のカスタム判定
- CDN/ログ   : AIボットのUser-Agent別ヒット数
- YouTube    : 自社チャンネルの動画・再生数（無料枠 10,000ユニット/日）
いずれも認証が無ければ demo 値を返す。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import demo_mode, env  # noqa: E402
from collect.llm import _rng  # noqa: E402

# GA4 の AI Assistant チャネル公式対象は ChatGPT/Gemini/Deepseek/Copilot/Grok の5つ。
# Claude と Perplexity は含まれないので自前で足す。
AI_REFERRERS = {
    "chatgpt": ["chatgpt.com", "chat.openai.com"],
    "gemini": ["gemini.google.com"],
    "copilot": ["copilot.microsoft.com"],
    "grok": ["grok.com", "x.ai"],
    "deepseek": ["deepseek.com"],
    "claude": ["claude.ai"],          # ← GA4公式定義に無い。カスタムで拾う
    "perplexity": ["perplexity.ai"],  # ← 同上
}

# 2026年時点の主要AIボット。role は学習 / 検索インデックス / ユーザー起点。
AI_BOTS = [
    ("GPTBot", "openai", "train"),
    ("OAI-SearchBot", "openai", "index"),
    ("ChatGPT-User", "openai", "user"),
    ("ClaudeBot", "anthropic", "train"),
    ("Claude-SearchBot", "anthropic", "index"),
    ("Claude-User", "anthropic", "user"),
    ("PerplexityBot", "perplexity", "index"),
    ("Perplexity-User", "perplexity", "user"),
    ("Google-Extended", "google", "train"),
    ("Amazonbot", "amazon", "train"),
    ("meta-externalagent", "meta", "train"),
    ("Bytespider", "bytedance", "train"),
]
_BOT_RE = re.compile("|".join(re.escape(b[0]) for b in AI_BOTS), re.I)


# ---------------------------------------------------------------- GA4
def ga4_sessions(day: str) -> dict:
    """AIアシスタント経由のセッション数をサービス別に返す。"""
    if demo_mode() or not env("GA4_PROPERTY_ID"):
        rng = _rng(["ga4", day])
        base = {"chatgpt": 1180, "gemini": 640, "perplexity": 210,
                "copilot": 95, "claude": 60, "grok": 18, "deepseek": 7}
        return {k: max(0, int(v * rng.uniform(0.85, 1.15))) for k, v in base.items()}

    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (DateRange, Dimension,
                                                    Metric, RunReportRequest)
    client = BetaAnalyticsDataClient()
    req = RunReportRequest(
        property=f"properties/{env('GA4_PROPERTY_ID')}",
        dimensions=[Dimension(name="sessionSource")],
        metrics=[Metric(name="sessions")],
        date_ranges=[DateRange(start_date=day, end_date=day)],
        limit=500,
    )
    rows = client.run_report(req).rows
    out = Counter()
    for r in rows:
        src = r.dimension_values[0].value.lower()
        for svc, doms in AI_REFERRERS.items():
            if any(d in src for d in doms):
                out[svc] += int(r.metric_values[0].value)
    return dict(out)


# ---------------------------------------------------------------- AIボット
def crawler_hits(day: str, log_path: str | None = None) -> dict:
    """アクセスログからAIボットのヒット数を数える。

    log_path が無ければ demo。Cloudflare を使う場合は GraphQL に差し替え可能。
    """
    log_path = log_path or env("ACCESS_LOG_PATH")
    if demo_mode() or not log_path or not Path(log_path).exists():
        rng = _rng(["bots", day])
        base = {"GPTBot": 4200, "OAI-SearchBot": 1850, "ChatGPT-User": 610,
                "ClaudeBot": 980, "Claude-SearchBot": 320, "Claude-User": 140,
                "PerplexityBot": 760, "Perplexity-User": 210,
                "Google-Extended": 3100, "Amazonbot": 240,
                "meta-externalagent": 180, "Bytespider": 90}
        return {k: max(0, int(v * rng.uniform(0.8, 1.2))) for k, v in base.items()}

    counts = Counter()
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = _BOT_RE.search(line)
            if m:
                counts[m.group(0)] += 1
    return dict(counts)


def bot_roles() -> dict:
    return {name: {"vendor": v, "role": r} for name, v, r in AI_BOTS}


# ---------------------------------------------------------------- YouTube
def youtube_stats(day: str) -> dict:
    """自社チャンネルの動画数・総再生数。無料枠 10,000ユニット/日で十分。"""
    if demo_mode() or not env("YOUTUBE_API_KEY"):
        rng = _rng(["yt", day])
        return {"videos": 412, "views_total": int(58_400_000 * rng.uniform(1.0, 1.002)),
                "views_delta": int(rng.uniform(18_000, 42_000)),
                "captioned_ratio": round(rng.uniform(0.42, 0.48), 3)}

    import requests
    key, ch = env("YOUTUBE_API_KEY"), env("YOUTUBE_CHANNEL_ID")
    r = requests.get("https://www.googleapis.com/youtube/v3/channels",
                     params={"part": "statistics", "id": ch, "key": key}, timeout=30)
    r.raise_for_status()
    st = r.json()["items"][0]["statistics"]
    return {"videos": int(st.get("videoCount", 0)),
            "views_total": int(st.get("viewCount", 0)),
            "views_delta": None, "captioned_ratio": None}


def collect(day: str) -> dict:
    return {
        "ga4_ai_sessions": ga4_sessions(day),
        "crawler_hits": crawler_hits(day),
        "youtube": youtube_stats(day),
    }
