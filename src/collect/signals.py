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
# source文字列 → サービス名の判定。ドメイン完全一致ではなくキーワードで拾う。
# 実測では "openai" "copilot.com" のような表記が主流で、ドメイン一致だけだと大半を取りこぼす（実測の6割は "openai"）。
_SVC_KEYS = [
    ("chatgpt", ("chatgpt", "openai")),
    ("gemini", ("gemini.google.com",)),        # 社内系 *.gemini.oneoffice.jp を誤検知しないためFQDNで
    ("copilot", ("copilot",)),
    ("grok", ("grok",)),
    ("deepseek", ("deepseek",)),
    ("claude", ("claude",)),
    ("perplexity", ("perplexity",)),
]
# 社内ツール・検証環境の除外（toyota.jp実測で確認済みのノイズ）
_SVC_EXCLUDE = ("toyotaconnected", "azurewebsites", "oneoffice.jp", "uhw.jp", "ngrok")


def _classify_sources(pairs) -> dict:
    """[(source, sessions)] をサービス別に集計する。"""
    out = Counter()
    for src, n in pairs:
        s = (src or "").lower()
        if any(x in s for x in _SVC_EXCLUDE):
            continue
        for svc, keys in _SVC_KEYS:
            if any(k in s for k in keys):
                out[svc] += int(n)
                break
    return {svc: out.get(svc, 0) for svc, _ in _SVC_KEYS}


def _ga4_target_day(day: str) -> str:
    """集計対象日。実行日当日は集計途中なので前日実績を使う（画面の注記と一致）。"""
    from datetime import date, timedelta
    y, m, dd = map(int, day.split("-"))
    return (date(y, m, dd) - timedelta(days=1)).isoformat()


def ga4_sessions(day: str) -> dict:
    """AIアシスタント経由のセッション数をサービス別に返す（day の前日実績）。

    優先順位: ①GA4 Data API ②Windsor.ai（WINDSOR_API_KEY） ③data/ga4_daily.json ④demo値
    """
    t = _ga4_target_day(day)

    # ---- ① GA4 Data API（サービスアカウント） ----
    if not demo_mode() and env("GA4_PROPERTY_ID"):
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (DateRange, Dimension,
                                                        Metric, RunReportRequest)
        client = BetaAnalyticsDataClient()
        req = RunReportRequest(
            property=f"properties/{env('GA4_PROPERTY_ID')}",
            dimensions=[Dimension(name="sessionSource")],
            metrics=[Metric(name="sessions")],
            date_ranges=[DateRange(start_date=t, end_date=t)],
            limit=1000,
        )
        rows = client.run_report(req).rows
        return _classify_sources(
            (r.dimension_values[0].value, r.metric_values[0].value) for r in rows)

    # ---- ② Windsor.ai（GA4コネクタ・無料枠で毎日取れる） ----
    if not demo_mode() and env("WINDSOR_API_KEY"):
        import requests
        r = requests.get(
            "https://connectors.windsor.ai/googleanalytics4",
            params={"api_key": env("WINDSOR_API_KEY"),
                    "date_from": t, "date_to": t,
                    "fields": "source,sessions",
                    "select_accounts": env("GA4_PROPERTY_ID") or "324699885",
                    "_renderer": "json"},
            timeout=60)
        r.raise_for_status()
        body = r.json()
        rows = body.get("data") or body.get("result") or []
        return _classify_sources((x.get("source"), x.get("sessions", 0)) for x in rows)

    # ---- ③ 手動更新ファイル（Claudeセッションが日次で置く実測） ----
    from common import ROOT
    from pathlib import Path as _P
    import json as _json
    f = ROOT / "data" / "ga4_daily.json"
    if f.exists():
        try:
            rec = _json.loads(f.read_text(encoding="utf-8")).get(t)
            if rec:
                return {k: int(v) for k, v in rec.items() if not k.startswith("_")}
        except Exception:
            pass

    # ---- ④ demo値（上のどれも無いときだけ） ----
    rng = _rng(["ga4", day])
    base = {"chatgpt": 1180, "gemini": 640, "perplexity": 210,
            "copilot": 95, "claude": 60, "grok": 18, "deepseek": 7}
    return {k: max(0, int(v * rng.uniform(0.85, 1.15))) for k, v in base.items()}


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
