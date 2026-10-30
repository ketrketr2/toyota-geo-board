#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""データマン（Mac mini）の遠隔点検。ops/request.json に書かれた「決まった作業」だけを1回実行する。

呼ばれ方: dataman_open_sealed.py の最後（boards_sync.py の同期のたびに走る）から呼ばれる。
          ops/request.json の id が前回と同じなら何もしない（同じ依頼を二度実行しない）。
できること（これ以外は実行しない）:
  - diag … 読み取りだけの状態点検（起動時刻・ログイン中の利用者・データマン関連のプロセス・
            launchd/cron の登録・ログの末尾など）。トークン類は伏せ字にする
結果: 依頼者の公開鍵（request.json の requester_pub）で封をして ops/result-<id>.bin に置き、push する。
      公開リポジトリに置いても中身は読めない。失敗してもボード資料の取り込みには影響させない。
"""
import base64
import gzip
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, "sources", "toyota-dataman", "secrets", "ops_last_id")
LOG = os.path.join(HOME, "sources", "toyota-dataman", "secrets", "ops.log")
ENV = dict(os.environ, PATH=":".join([
    "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    os.path.join(HOME, ".local", "bin"), os.path.join(HOME, ".npm-global", "bin"),
    os.path.join(HOME, ".claude", "local"), os.environ.get("PATH", "")]), LANG="ja_JP.UTF-8")
SECRET_PAT = re.compile(
    r"(xox[abposr]-[A-Za-z0-9-]{8,}|xapp-[A-Za-z0-9-]{8,}|sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    r"(?i:(?:token|secret|password|passwd|api[_-]?key|authorization)\s*[=:]\s*)\S+)")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(JST):%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def redact(s: str) -> str:
    return SECRET_PAT.sub("［伏せ字］", s or "")


def sh(cmd: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, env=ENV)
        out = (r.stdout or "") + (("\n[stderr] " + r.stderr) if r.stderr.strip() else "")
        return redact(out)[-12000:]
    except Exception as e:  # noqa: BLE001
        return f"[実行できず] {type(e).__name__}: {e}"


def diag() -> dict:
    d = {}
    d["now"] = datetime.now(JST).isoformat()
    for k, c in [
        ("uptime", "uptime"), ("boottime", "sysctl -n kern.boottime"), ("who", "who"),
        ("console_user", "stat -f %Su /dev/console"), ("id", "id"),
        ("last", "last -15"), ("sw_vers", "sw_vers"),
        ("power_events", "pmset -g log 2>/dev/null | grep -E ' (Sleep|Wake|DarkWake|Shutdown|Restart|Start) ' | tail -40"),
        ("procs", "ps -axo pid,ppid,lstart,etime,user,command | grep -Ei 'slack|bolt|claude|dataman|toyota|python|node|tailscale|chrome' | grep -v grep | cut -c1-400"),
        ("launchctl_user", "launchctl list 2>&1 | grep -Ei 'dataman|slack|claude|toyota|bot|tailscale' "),
        ("launchctl_gui", "launchctl print gui/$(id -u) 2>&1 | head -5; launchctl print gui/$(id -u) 2>/dev/null | grep -Ei 'dataman|slack|claude|toyota' | head -40"),
        ("launchagents", "ls -la ~/Library/LaunchAgents/ 2>&1"),
        ("plists", "for f in ~/Library/LaunchAgents/*.plist; do if grep -qiE 'dataman|slack|claude|toyota' \"$f\" 2>/dev/null; then echo \"==== $f\"; plutil -p \"$f\" 2>/dev/null || cat \"$f\"; fi; done"),
        ("crontab", "crontab -l 2>&1"),
        ("dataman_dir", "ls -la ~/sources/toyota-dataman/ 2>&1 | head -80"),
        ("dataman_bin", "ls -la ~/sources/toyota-dataman/*.sh ~/sources/toyota-dataman/bin ~/sources/toyota-dataman/scripts 2>&1 | head -60"),
        ("claude", "which claude; claude --version 2>&1 | head -3"),
        ("tailscale", "tailscale status 2>&1 | head -5"),
        ("df", "df -h ~ 2>&1"),
        ("net", "curl -s -o /dev/null -w 'slack.com %{http_code}\\n' --max-time 8 https://slack.com/api/api.test; curl -s -o /dev/null -w 'api.anthropic.com %{http_code}\\n' --max-time 8 https://api.anthropic.com/"),
    ]:
        d[k] = sh(c)
    # データマン関連のログ（直近10日に更新されたもの、各末尾100行）
    logs = sh("find ~/sources/toyota-dataman ~/Library/Logs -maxdepth 3 -type f \\( -name '*.log' -o -name '*.out' -o -name '*.err' \\) -mtime -10 2>/dev/null | head -20")
    d["log_files"] = logs
    tails = {}
    for p in [x.strip() for x in logs.splitlines() if x.strip().startswith("/")][:12]:
        tails[p] = sh(f"ls -la '{p}'; tail -n 100 '{p}'", timeout=10)
    d["log_tails"] = tails
    return d


def seal(plain: bytes, pub_b64: str, name: str) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    rp = base64.b64decode(pub_b64)
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub + rp, info=b"dataman-seal-v1").derive(
        eph.exchange(X25519PublicKey.from_public_bytes(rp)))
    nonce = os.urandom(12)
    return eph_pub + nonce + AESGCM(key).encrypt(nonce, gzip.compress(plain, 9), name.encode())


def git(*args, timeout=60):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, timeout=timeout, env=ENV)


def run():
    req_path = os.path.join(ROOT, "ops", "request.json")
    if not os.path.exists(req_path):
        return
    req = json.load(open(req_path, encoding="utf-8"))
    rid = str(req.get("id", ""))
    if not re.fullmatch(r"[0-9A-Za-z_-]{4,40}", rid):
        return
    last = open(STATE).read().strip() if os.path.exists(STATE) else ""
    if rid == last:
        return
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    open(STATE, "w").write(rid)          # 先に記録（失敗しても繰り返さない）
    result = {"id": rid, "started": datetime.now(JST).isoformat(), "actions": {}}
    for a in req.get("actions", []):
        if a == "diag":
            result["actions"]["diag"] = diag()
    result["finished"] = datetime.now(JST).isoformat()
    blob = seal(json.dumps(result, ensure_ascii=False).encode(), req["requester_pub"], f"ops-{rid}")
    rel = f"ops/result-{rid}.bin"
    open(os.path.join(ROOT, rel), "wb").write(blob)
    for i in range(3):
        git("pull", "--rebase", "-q")
        git("add", "-f", rel)
        c = git("commit", "-q", "-m", f"ops: 点検結果 {rid}（封済み）")
        p = git("push", "-q")
        if p.returncode == 0:
            log(f"ops {rid}: push OK")
            return
        log(f"ops {rid}: push失敗 {i+1}回目 {redact(p.stderr)[-300:]}")
        time.sleep(5 * (i + 1))


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        log(f"ops error: {type(e).__name__}: {e}")
