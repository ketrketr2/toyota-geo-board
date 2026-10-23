#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""データマン（Mac mini）専用の受け取り鍵を作る。1回だけ実行する。

- 秘密鍵は Mac mini の中にだけ置く（既定: ~/sources/toyota-dataman/secrets/dataman_x25519.key、権限600）。
  画面・Slack・ログに中身を出さない。リポジトリにも入れない
- 公開鍵（base64・公開してよい）を config/dataman_recipient.pub に書く。これを push すると、
  GitHub Actions がボードの中身をこの公開鍵で封をして置けるようになる（開けられるのはこの Mac mini だけ）
- 既に秘密鍵があるときは作り直さず、公開鍵だけ書き直す（鍵を勝手に入れ替えない）

使い方: python3 tools/dataman_keygen.py [秘密鍵のパス]
"""
import base64
import os
import sys

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
except ImportError:
    sys.exit("cryptography が無い。先に: python3 -m pip install --user cryptography")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
key_path = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "DATAMAN_KEY", "~/sources/toyota-dataman/secrets/dataman_x25519.key"))
os.makedirs(os.path.dirname(key_path), mode=0o700, exist_ok=True)
if os.path.exists(key_path):
    k = X25519PrivateKey.from_private_bytes(open(key_path, "rb").read())
    state = "既存の鍵を使用"
else:
    k = X25519PrivateKey.generate()
    raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    state = "新規作成"
os.chmod(key_path, 0o600)
pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
os.makedirs(os.path.join(ROOT, "config"), exist_ok=True)
open(os.path.join(ROOT, "config", "dataman_recipient.pub"), "w").write(pub + "\n")
print(f"秘密鍵: {state}（{key_path}・権限600・中身は表示しない）")
print(f"公開鍵（公開してよい）: {pub}")
print("次: git add config/dataman_recipient.pub && git commit -m 'config: データマン受け取り公開鍵' && git push")
