#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封をされたボード資料（sealed bundle）を Mac mini の秘密鍵で開けて kb/boards/ に展開する。

ID/PW付きボード（ROOMY AIO・ROOMY COMMAND・CAR×AI SIGNAL・車種別AI分析・U-CAR SIGNAL・GA4 COMMAND）は
GitHub Actions が毎朝復号して全画面の本文・図の数値・元データを資料化し、データマンの公開鍵で封をして置く。
封を開けられるのはこの Mac mini の秘密鍵だけ（公開リポジトリに置いても中身は読めない）。

使い方:
  python3 tools/dataman_open_sealed.py [--kb ~/sources/toyota-dataman/kb] [--key 秘密鍵] [--url URL ...]
既定の取得元:
  https://raw.githubusercontent.com/ketrketr2/roomy-command/dataman-sealed/sealed/boards.bin
守っていること:
  - 開けられない・壊れている・古い（36時間超）ときは、前回の資料を残したまま、理由を _取り込み状況.md に書いて終了コード1
  - 展開は一時フォルダに出してから入れ替える（途中で失敗しても半端な状態にしない）
  - アーカイブ内のパスは boards/ 配下の相対パスだけを許す（../ や絶対パスは拒否）
"""
import argparse
import base64
import gzip
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:
    sys.exit("cryptography が無い。先に: python3 -m pip install --user cryptography")

JST = timezone(timedelta(hours=9))
DEFAULT_URL = "https://raw.githubusercontent.com/ketrketr2/roomy-command/dataman-sealed/sealed/boards.bin"


def open_seal(blob: bytes, priv: X25519PrivateKey, name: str) -> bytes:
    my_pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    eph_pub, nonce, ct = blob[:32], blob[32:44], blob[44:]
    shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub + my_pub, info=b"dataman-seal-v1").derive(shared)
    return gzip.decompress(AESGCM(key).decrypt(nonce, ct, name.encode()))


def fetch(url: str) -> bytes:
    if os.path.exists(os.path.expanduser(url)):
        return open(os.path.expanduser(url), "rb").read()
    req = urllib.request.Request(url + ("&" if "?" in url else "?") + f"t={int(time.time())}",
                                 headers={"User-Agent": "dataman-open-sealed/1", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def safe_extract(tar: tarfile.TarFile, dest: str):
    root = os.path.realpath(dest)
    for m in tar.getmembers():
        p = os.path.realpath(os.path.join(dest, m.name))
        if not (p == root or p.startswith(root + os.sep)) or m.issym() or m.islnk() or m.isdev():
            raise ValueError(f"不正なパスを含む: {m.name}")
    tar.extractall(dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.expanduser("~/sources/toyota-dataman/kb"))
    ap.add_argument("--key", default=os.path.expanduser(os.environ.get("DATAMAN_KEY", "~/sources/toyota-dataman/secrets/dataman_x25519.key")))
    ap.add_argument("--url", action="append", default=None)
    ap.add_argument("--max-age-hours", type=float, default=36)
    a = ap.parse_args()
    urls = a.url or [DEFAULT_URL]
    kb_boards = os.path.join(os.path.expanduser(a.kb), "boards")
    status_path = os.path.join(kb_boards, "_取り込み状況.md")
    now = datetime.now(JST)

    def report(ok: bool, lines):
        os.makedirs(kb_boards, exist_ok=True)
        head = f"# ボード資料の取り込み状況\n\n- 最終確認: {now:%Y-%m-%d %H:%M} JST\n- 結果: {'OK' if ok else '**失敗（前回の資料のまま）**'}\n"
        open(status_path, "w", encoding="utf-8").write(head + "\n".join(f"- {x}" for x in lines) + "\n")
        print(("OK " if ok else "FAIL ") + " / ".join(lines))

    try:
        priv = X25519PrivateKey.from_private_bytes(open(a.key, "rb").read())
    except Exception as e:  # noqa: BLE001
        report(False, [f"秘密鍵を読めない（{type(e).__name__}）。tools/dataman_keygen.py を実行したか確認"])
        return 1
    for url in urls:
        try:
            blob = fetch(url)
            raw = open_seal(blob, priv, "boards")
            tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
            manifest = json.loads(tar.extractfile("boards/_manifest.json").read())
            built = datetime.fromisoformat(manifest["built_at"])
            age_h = (now - built).total_seconds() / 3600
            os.makedirs(os.path.dirname(kb_boards), exist_ok=True)
            with tempfile.TemporaryDirectory(dir=os.path.dirname(kb_boards)) as td:
                safe_extract(tar, td)
                new_dir = os.path.join(td, "boards")
                old = kb_boards + ".old"
                if os.path.exists(old):
                    shutil.rmtree(old)
                if os.path.exists(kb_boards):
                    os.rename(kb_boards, old)
                shutil.move(new_dir, kb_boards)
                if os.path.exists(old):
                    shutil.rmtree(old)
            lines = [f"取得元: {url}", f"資料の作成: {manifest['built_at']}（{age_h:.1f}時間前）",
                     *[f"{b['name']}: {'OK' if b['ok'] else '失敗'}（画面 {b.get('screens', 0)}）{b.get('note', '')}" for b in manifest["boards"]]]
            stale = age_h > a.max_age_hours
            if stale:
                lines.insert(0, f"**資料が古い（{age_h:.0f}時間前）。GitHub Actions の dataman-digest が止まっている可能性**")
            report(not stale and all(b["ok"] for b in manifest["boards"]), lines)
            return 0 if not stale else 1
        except Exception as e:  # noqa: BLE001
            last = f"{url}: {type(e).__name__} {str(e)[:200]}"
            continue
    report(False, [f"開けなかった: {last}"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
