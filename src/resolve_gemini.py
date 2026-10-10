#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemini 回答の引用URL（vertexaisearch.cloud.google.com/grounding-api-redirect/...）を実URLに解決する。
data/zanka/responses_<day>.json を読み、data/zanka/gemini_resolved_<day>.json に {redirect_url: real_url} を書く。"""
import glob, json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
f = sorted(glob.glob(str(ROOT / 'data' / 'zanka' / 'responses_*.json')))[-1]
day = Path(f).stem.split('_')[-1]
R = json.load(open(f, encoding='utf-8'))
urls = sorted({c['url'] for r in R for c in (r.get('citations') or []) if 'vertexaisearch' in (c.get('url') or '')})
print(len(urls), 'redirect urls')
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'}

def one(u):
    for att in range(2):
        try:
            r = requests.get(u, headers=UA, allow_redirects=False, timeout=20)
            loc = r.headers.get('Location')
            if loc:
                return u, loc
            return u, None
        except Exception:
            time.sleep(1)
    return u, None

out = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    for u, loc in ex.map(one, urls):
        if loc:
            out[u] = loc
print(len(out), 'resolved')
json.dump(out, open(ROOT / 'data' / 'zanka' / f'gemini_resolved_{day}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
