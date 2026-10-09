"""AIアシスタント経由の流入元（GA4 の source）をサービス別に数える規則。

GEOボード（collect/signals.py）とデータマン用のGA4資料（ga4_report.py）で同じ規則を使うため、ここに一本化する。

- 判定はドメイン完全一致ではなくキーワード。実測では "openai" "copilot.com" のような表記が主流で、
  ドメイン一致だけだと大半を取りこぼす（2026-08 時点で実測の6割は "openai"）。
- Gemini は 2026-09-04 から "gemini.google.com" ではない単独表記 "gemini"（utm_source=gemini）が出るようになった。
  旧規則（FQDN一致）ではこれを数えておらず、2026年9月は Gemini 経由の56%が漏れていた → キーワード "gemini" に変更。
  社内系の *.gemini.oneoffice.jp は除外リストで先に落とすので誤検知しない。
- 除外は toyota.jp 実測で確認済みの社内ツール・検証環境と、AIの回答ではない流入（OpenAI のメール配信）。
- 見落としの点検：HINT_KEYWORDS／looks_like_ai() は分類には使わず、ga4_report.py が毎朝
  「AIらしいのにどのサービスにも入っていない流入元」を探すためだけに使う（9月の gemini 表記を1か月見落とした反省）。
"""
from __future__ import annotations

import re
from collections import Counter

RULES_VERSION = "ai_sources v2（2026-10-09: gemini 単独表記を追加、OpenAIのメール配信を除外）"

# (サービス名, 含まれていたらそのサービスとみなすキーワード) … 上から順に判定
SVC_KEYS = [
    ("chatgpt", ("chatgpt", "openai")),
    ("gemini", ("gemini",)),
    ("copilot", ("copilot",)),
    ("grok", ("grok",)),
    ("deepseek", ("deepseek",)),
    ("claude", ("claude",)),
    ("perplexity", ("perplexity",)),
]
SERVICES = [svc for svc, _ in SVC_KEYS]

# 社内ツール・検証環境（toyota.jp 実測で確認済みのノイズ）と、AIの回答ではない流入
EXCLUDE = ("toyotaconnected", "azurewebsites", "oneoffice.jp", "uhw.jp", "ngrok", "email.openai")

# Windsor.ai の絞り込み用（どれかを含む source だけを取る）
FILTER_KEYWORDS = ("chatgpt", "openai", "gemini", "copilot", "grok", "deepseek", "claude", "perplexity")


def service_of(source: str | None) -> str | None:
    """source 文字列 → サービス名（該当なしは None）。"""
    s = (source or "").lower()
    if any(x in s for x in EXCLUDE):
        return None
    for svc, keys in SVC_KEYS:
        if any(k in s for k in keys):
            return svc
    return None


def classify(pairs) -> dict:
    """[(source, sessions)] をサービス別に集計する。全サービスのキーを必ず返す。"""
    out: Counter = Counter()
    for src, n in pairs:
        svc = service_of(src)
        if svc:
            try:
                out[svc] += int(float(n or 0))
            except (TypeError, ValueError):
                pass
    return {svc: out.get(svc, 0) for svc in SERVICES}


def windsor_filter() -> list:
    """Windsor.ai の filter 用の式（source にキーワードのどれかを含む）。"""
    expr: list = []
    for i, k in enumerate(FILTER_KEYWORDS):
        if i:
            expr.append("or")
        expr.append(["source", "contains", k])
    return expr


# ---------------------------------------------------------------- 見落としの点検（分類には使わない）
# Windsor.ai の絞り込み用。広めに拾い、下の正規表現で絞る
HINT_KEYWORDS = ("gpt", "openai", "gemini", "bard", "copilot", "perplexity", "claude", "anthropic", "grok",
                 "deepseek", "mistral", "meta.ai", "you.com", "phind", "felo", "genspark", "manus", "poe.com",
                 "qwen", "kimi", "moonshot", "doubao", "monica", "notebooklm", "aistudio", "chat.", "llm", ".ai")
_HINT_RE = re.compile(
    r"gpt|openai|gemini|bard|copilot|perplexity|claude|anthropic|grok|deepseek|mistral|meta\.ai|you\.com|"
    r"phind|felo\.|genspark|manus\.im|poe\.com|qwen|kimi\.(?:ai|com|moonshot)|moonshot|doubao|monica\.im|"
    r"notebooklm|aistudio|(?:^|\.)chat\.|llm|\.ai(?:$|[/:?])", re.I)
# 手がかりに当たるが、AIアシスタントの回答からの流入ではないと確認したもの（2026-10-09 時点の実測で確認）
KNOWN_OTHER = ("tagassistant", "chatbot-ntp", "stockmark.ai", "ainow.ai", "sitebunny")


def hint_filter() -> list:
    """Windsor.ai の filter 用（source に手がかりのどれかを含む）。"""
    expr: list = []
    for i, k in enumerate(HINT_KEYWORDS):
        if i:
            expr.append("or")
        expr.append(["source", "contains", k])
    return expr


def looks_like_ai(source: str | None) -> bool:
    return bool(_HINT_RE.search(source or ""))


def unclassified_ai_like(pairs) -> list:
    """AIらしいのに、どのサービスにも入らず除外もされていない流入元を [(source, sessions)] で多い順に返す。"""
    out: Counter = Counter()
    for src, n in pairs:
        s = (src or "").strip().lower()
        if not s or service_of(s) or any(x in s for x in EXCLUDE) or any(x in s for x in KNOWN_OTHER):
            continue
        if looks_like_ai(s):
            try:
                out[src] += int(float(n or 0))
            except (TypeError, ValueError):
                pass
    return out.most_common()
