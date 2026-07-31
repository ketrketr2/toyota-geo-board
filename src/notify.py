#!/usr/bin/env python3
"""日次サマリを Slack に流す。SLACK_WEBHOOK_URL が無ければ何もしない。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

from common import env, read_json, snapshot_path, today  # noqa: E402

SEV_ICON = {"high": ":rotating_light:", "mid": ":warning:", "low": ":information_source:"}


def main() -> None:
    url = env("SLACK_WEBHOOK_URL")
    if not url:
        print("SLACK_WEBHOOK_URL not set — skip")
        return
    day = sys.argv[1] if len(sys.argv) > 1 else today()
    snap = read_json(snapshot_path(day))
    if not snap:
        print(f"no snapshot for {day}")
        return
    c, f = snap.get("comment", {}), snap["factors"]

    blocks = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"AI可視性 {day}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{c.get('headline','')}*"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*出現率*\n{f['presence']:.1f}%"},
            {"type": "mrkdwn", "text": f"*オウンド引用率*\n{f['owned_citation']:.1f}%"},
            {"type": "mrkdwn", "text": f"*アーンド引用率(SNS)*\n{f['earned_citation']:.1f}%"},
            {"type": "mrkdwn", "text": f"*相対シェア*\n{f['share_of_voice']:.1f}"},
        ]},
    ]
    for p, label in (("dod", "前日比"), ("wow", "先週比"), ("mom", "先月比")):
        lines = (c.get("periods") or {}).get(p) or []
        if lines:
            blocks.append({"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"*{label}* " + " ｜ ".join(lines)}]})

    alerts = [a for a in (c.get("alerts") or []) if a["severity"] == "high"][:5]
    if alerts:
        txt = "\n".join(f"{SEV_ICON.get(a['severity'],'')} {a['text']}"
                        f"（{a.get('surface') or '-'} / {a.get('prompt_id') or '-'}）" for a in alerts)
        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": txt}})

    r = requests.post(url, json={"blocks": blocks}, timeout=30)
    print("slack:", r.status_code)


if __name__ == "__main__":
    main()
