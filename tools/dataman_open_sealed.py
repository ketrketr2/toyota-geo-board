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
  - 開けられない・壊れている・古い（36時間超）ときは、前回の資料を残したまま、理由を _取り込み状況.md に書く
  - 終了コード1（＝同期側が Slack に「失敗しました」と通知する）は、新しい失敗のときと、同じ失敗が続くときは
    12時間に1回だけにする（15分ごとの同期のたびに同じ通知が飛ばないように）。直ったら一度だけ「復旧」を知らせる
  - 一部のボードだけ読み取れていない（例: U-CAR SIGNAL の画面が0）ときは、その組み合わせが変わったときに一度だけ知らせ、
    直ったときにも一度だけ知らせる（2026-11-07: 「復旧しました」が同期のたびに流れた不具合の修正）
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
    try:
        tar.extractall(dest, filter="data")   # 3.12+（中身は上で確認済み。警告を出さないため）
    except TypeError:                          # filter 引数の無い古い Python
        tar.extractall(dest)


STATE_PATH = os.path.expanduser("~/sources/toyota-dataman/secrets/open_sealed_state.json")
RENOTIFY_HOURS = 12


def _state_load():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _state_save(st):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        json.dump(st, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def gate(failed: bool, sig: str = "", confirm_runs: int = 1) -> int:
    """戻り値1（同期側が Slack に通知）を出すかどうか。
    新しい失敗→1（confirm_runs 回続いたら）、同じ失敗の継続→12時間に1回だけ1、それ以外は0。"""
    now = time.time()
    st = _state_load()
    if not failed:
        _state_save({"sig": "", "since": now, "last_notified": 0, "count": 0})
        return 0
    if st is None:            # この仕組みを入れた直後：すでに通知が出ているので黙って記録だけ
        _state_save({"sig": sig, "since": now, "last_notified": now, "count": 1})
        print(f"（通知は控えた: {sig}）")
        return 0
    if st.get("sig") != sig:
        st = {"sig": sig, "since": now, "last_notified": 0, "count": 1}
    else:
        st["count"] = int(st.get("count", 1)) + 1
    if st["count"] >= confirm_runs and now - float(st.get("last_notified", 0)) >= RENOTIFY_HOURS * 3600:
        st["last_notified"] = now
        _state_save(st)
        return 1
    _state_save(st)
    print(f"（通知は控えた: {sig}。同じ失敗の再通知は{RENOTIFY_HOURS}時間ごと）")
    return 0


def digest_job_status() -> str:
    """資料作り（roomy-command の GitHub Actions: dataman-digest）の直近の結果。公開APIで読むだけ・失敗しても空。"""
    try:
        url = "https://api.github.com/repos/ketrketr2/roomy-command/actions/workflows/dataman-digest.yml/runs?per_page=1"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "dataman-open-sealed/1"}),
                                    timeout=10) as r:
            run = (json.loads(r.read().decode()).get("workflow_runs") or [{}])[0]
        when = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).astimezone(JST)
        res = {"success": "成功", "failure": "失敗", "cancelled": "中止"}.get(run.get("conclusion") or "", "実行中")
        return f"資料作りのジョブの直近: {when:%m/%d %H:%M} {res} {run.get('html_url', '')}"
    except Exception:  # noqa: BLE001
        return ""


BOARD_STATE_PATH = os.path.expanduser("~/sources/toyota-dataman/secrets/open_sealed_boards.json")
BOARD_NAMES = {"roomy_aio": "ROOMY AIO", "roomy_command": "ROOMY COMMAND", "car_signal": "CAR×AI SIGNAL",
               "car_deepdive": "車種別AI分析", "ucar_signal": "U-CAR SIGNAL", "ga4_command": "GA4 COMMAND"}


def board_watch(failed: list):
    """一部のボードの読み取り失敗は、失敗の組み合わせが変わったときだけ知らせる（毎回は知らせない）。
    この仕組みを入れた直後（記録が無いとき）は黙って記録だけする。知らせた失敗が直ったときだけ「直った」を知らせる。"""
    try:
        prev_st = json.load(open(BOARD_STATE_PATH, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        prev_st = None
    new_st = {"failed": sorted(failed), "notified": False, "at": time.time()}
    if prev_st is not None:
        prev = sorted(prev_st.get("failed", []))
        if prev == sorted(failed):
            return
        if failed:
            names = "・".join(BOARD_NAMES.get(x, x) for x in failed)
            notify_recovered(f":warning: ボード資料のうち {names} の画面が読み取れていません（ほかのボードは最新）。"
                             f"{names} については、データマンは画面の内容を答えられません。")
            new_st["notified"] = True
        elif prev_st.get("notified"):
            names = "・".join(BOARD_NAMES.get(x, x) for x in prev)
            notify_recovered(f":white_check_mark: {names} も読み取れるようになりました。すべてのボードが最新です。")
    try:
        os.makedirs(os.path.dirname(BOARD_STATE_PATH), exist_ok=True)
        json.dump(new_st, open(BOARD_STATE_PATH, "w", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass


def notify_recovered(text: str):
    """直ったことを一度だけ知らせる（データマンのボットから中井さんのDMへ）。失敗しても何もしない。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        import dataman_ops  # noqa: E402
        dataman_ops.notify(text)
    except Exception:  # noqa: BLE001
        pass


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
    st0 = _state_load() or {}
    was_failing = bool(st0.get("sig"))     # 直前まで「古い・開けない」等の失敗が続いていたか（通知済みの状態）

    def report(ok: bool, lines, result=None):
        os.makedirs(kb_boards, exist_ok=True)
        res = result or ("OK" if ok else "**失敗（前回の資料のまま）**")
        head = f"# ボード資料の取り込み状況\n\n- 最終確認: {now:%Y-%m-%d %H:%M} JST\n- 結果: {res}\n"
        open(status_path, "w", encoding="utf-8").write(head + "\n".join(f"- {x}" for x in lines) + "\n")
        print(("OK " if ok else "FAIL ") + " / ".join(lines))

    try:
        priv = X25519PrivateKey.from_private_bytes(open(a.key, "rb").read())
    except Exception as e:  # noqa: BLE001
        report(False, [f"秘密鍵を読めない（{type(e).__name__}）。tools/dataman_keygen.py を実行したか確認"])
        return gate(True, "key")
    last, last_type = "", "Unknown"
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
                job = digest_job_status()
                print(f"ボード資料が古いままです（最終作成 {built:%m/%d %H:%M}、{age_h:.0f}時間前）。データマンは前回の資料で答えます。"
                      + (f"\n{job}" if job else ""), file=sys.stderr)
            failed = [b["name"] for b in manifest["boards"] if not b["ok"]]
            if stale:
                report(False, lines, "**古い（前回の資料のまま）**")
            elif failed:
                report(False, lines, "一部のボードが読み取れていない: " + "・".join(BOARD_NAMES.get(x, x) for x in failed))
            else:
                report(True, lines)
            if not stale:
                if was_failing:          # 失敗（古い・開けない）から直ったときだけ、一度知らせる
                    notify_recovered(f":white_check_mark: ボード資料の取り込みが復旧しました（資料の作成 {manifest['built_at'][:16]}）。"
                                     "データマンは最新のボード資料で答えます。")
                board_watch(failed)
            return gate(stale, "stale")
        except Exception as e:  # noqa: BLE001
            last = f"{url}: {type(e).__name__} {str(e)[:200]}"
            last_type = type(e).__name__
            continue
    report(False, [f"開けなかった: {last}"])
    print(f"ボード資料を開けませんでした（{last_type}）。データマンは前回の資料で答えます。", file=sys.stderr)
    # 通信の一時的な失敗で通知しないよう、開けない失敗は2回続いたら（約30分）知らせる
    return gate(True, "open:" + last_type, confirm_runs=2)


def _run_ops():
    """遠隔点検（tools/dataman_ops.py）。毎回: 応答役の見張り（Slack 接続が切れ続けていたら再起動）。
    ops/request.json に新しい依頼があるときだけ、その作業を1回実行する。失敗しても取り込みに影響させない。"""
    try:
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        subprocess.run([sys.executable, os.path.join(here, "dataman_ops.py")], timeout=480,
                       capture_output=True)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    rc = main()
    _run_ops()
    sys.exit(rc)
