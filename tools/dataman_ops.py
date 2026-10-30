#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""データマン（Mac mini）の遠隔点検。ops/request.json に書かれた「決まった作業」だけを1回実行する。

呼ばれ方: dataman_open_sealed.py の最後（boards_sync.py の同期のたびに走る）から呼ばれる。
          ops/request.json の id が前回と同じなら何もしない（同じ依頼を二度実行しない）。
できること（これ以外は実行しない）:
  - diag        … 読み取りだけの状態点検（起動時刻・ログイン中の利用者・データマン関連のプロセス・
                   launchd/cron の登録・ログの末尾など）。トークン類は伏せ字にする
  - restart_bot … 応答役（launchd: com.slash.dataman.bot）を再起動する（launchctl kickstart -k）。
                   直前の状態（接続数など）を記録してから行う。見張りが5分以内に再起動済みなら重ねない
  - check_bot   … 再起動後に待って、起動ログ・接続エラーの有無・Slack との接続数を確かめる
  - read_source … bot.py / answer.py の中身とライブラリの版を読む（トークン類は伏せ字）
  - slack_probe … ボットのトークンで Slack を読むだけの確認（ボット自身・指定利用者の状態・ボットのDM一覧と
                   指定利用者とのDMの有無／共有状態／直近5件の時刻）。書き込みはしない
  - claude_check … 回答役の頭脳（claude -p）が動くかを短い問いで確かめる
  - missed_scan … 止まっていた間（since 以降）にボットへ来ていた質問（ボットとのDM・チャンネルでの@メンション）を
                   読むだけで一覧にする。回答や投稿はしない
常時（依頼が無くても毎回）:
  - 見張り … 応答役のエラーログに Slack 接続の切断（BrokenPipe 等）が出続けていたら（前回点検から3件以上、
             かつ30秒の観察中にも新たに出る）、自動で再起動し、中井さんとデータマンのDMに一言知らせる。
             回答を作っている最中なら最大30分待つ。再起動は1時間に1回まで、通知は6時間に1回まで
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


BOT_LABEL = "com.slash.dataman.bot"
DM_DIR = os.path.join(HOME, "sources", "toyota-dataman")
BOT_ERR = os.path.join(DM_DIR, "logs", "bot.err.log")
BOT_LOG = os.path.join(DM_DIR, "logs", "bot.log")
WD_STATE = os.path.join(DM_DIR, "secrets", "bot_watchdog.json")
WD_LOG = os.path.join(DM_DIR, "logs", "watchdog.log")
NOTIFY_DM = "D0BVAL5DRP1"          # 中井さんとデータマンのDM
# Socket Mode（slack_sdk builtin）の接続エラー行。10/26〜30 の停止では on_error invoked（BrokenPipe）が約10秒おきに続いた。
# 10/30 10:31 には handshake timeout / SSL BAD_LENGTH で一度切れ、自力で戻った（このときは件数・観察で見送りになる）
DISCONNECT_PAT = re.compile(r"on_error invoked|Failed to establish a connection|Failed to check the state of sock|"
                            r"BrokenPipeError|ConnectionResetError|ConnectionRefusedError|WebSocketConnectionClosed|"
                            r"Connection is already closed|SSLEOFError|SlackClientNotConnectedError|too_many_websockets")


def wd_log(msg):
    try:
        os.makedirs(os.path.dirname(WD_LOG), exist_ok=True)
        with open(WD_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(JST):%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def wd_load() -> dict:
    try:
        return json.load(open(WD_STATE, encoding="utf-8"))
    except Exception:
        return {}


def wd_save(st: dict):
    try:
        os.makedirs(os.path.dirname(WD_STATE), exist_ok=True)
        json.dump(st, open(WD_STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def bot_pid() -> str:
    out = sh(f"launchctl list | awk '$3==\"{BOT_LABEL}\"{{print $1}}'")
    pid = out.strip().splitlines()[0].strip() if out.strip() else ""
    return pid if pid.isdigit() else ""


def err_size() -> int:
    try:
        return os.path.getsize(BOT_ERR)
    except OSError:
        return 0


def count_disconnects(since: int) -> int:
    size = err_size()
    if size <= since:
        return 0
    with open(BOT_ERR, "rb") as f:
        f.seek(max(since, size - 400000))
        return sum(1 for ln in f.read().decode("utf-8", "replace").splitlines() if DISCONNECT_PAT.search(ln))


def answering_now(pid: str) -> bool:
    """応答役が回答を作っている最中（子プロセスに claude がいる）か。"""
    if not pid:
        return False
    return "claude" in sh(f"ps -o pid,command -p $(pgrep -P {pid} | tr '\\n' ',' | sed 's/,$//') 2>/dev/null | tail -n +2")


def restart_bot(source: str) -> dict:
    """応答役を再起動する。直前の様子（接続数・稼働時間）を残してから kickstart -k。"""
    uid = os.getuid()
    info = {"source": source, "at": datetime.now(JST).isoformat()}
    pid = bot_pid()
    info["old_pid"] = pid
    if pid:
        info["old_proc"] = sh(f"ps -o pid,lstart,etime,%cpu,rss,command -p {pid}")
        info["old_tcp"] = sh(f"lsof -nP -a -p {pid} -iTCP 2>/dev/null | head -40")
        info["old_tcp_count"] = sh(f"lsof -nP -a -p {pid} -iTCP 2>/dev/null | tail -n +2 | wc -l").strip()
        info["old_threads"] = sh(f"ps -M -p {pid} 2>/dev/null | tail -n +2 | wc -l").strip()
    # エラーログが大きければ退避（新しいプロセスは新しいファイルに書く）
    try:
        if err_size() > 20 * 1024 * 1024:
            os.replace(BOT_ERR, BOT_ERR + ".1")
            info["rotated_err_log"] = True
    except Exception as e:  # noqa: BLE001
        info["rotate_error"] = f"{type(e).__name__}: {e}"
    info["err_size_before"] = err_size()
    rc_list = []
    for dom in (f"gui/{uid}", f"user/{uid}"):
        try:
            r = subprocess.run(["launchctl", "kickstart", "-k", f"{dom}/{BOT_LABEL}"], capture_output=True,
                               text=True, timeout=30, env=ENV)
        except Exception as e:  # noqa: BLE001
            rc_list.append(f"{dom}: {type(e).__name__}")
            continue
        rc_list.append(f"{dom}: rc={r.returncode} {redact(r.stdout + r.stderr).strip()[:200]}")
        if r.returncode == 0:
            break
    else:
        if pid:   # 最後の手段: 止めれば KeepAlive で起き直す
            try:
                os.kill(int(pid), 15)
                rc_list.append(f"kill -TERM {pid}: ok")
            except Exception as e:  # noqa: BLE001
                rc_list.append(f"kill -TERM {pid}: {type(e).__name__}")
    info["kickstart"] = rc_list
    time.sleep(3)
    info["new_pid"] = bot_pid()
    wd_log(f"応答役を再起動（{source}） old={pid} new={info['new_pid']} {' / '.join(rc_list)}")
    st = wd_load()
    st.update(err_size=err_size(), err_size_at_restart=err_size(), last_restart=time.time(),
              last_restart_source=source)
    st["restarts"] = int(st.get("restarts", 0)) + 1
    wd_save(st)
    return info


def notify(text: str):
    """データマンのボットトークン（.env の xoxb-）で、中井さんとデータマンのDMに一言送る。トークンは表示しない。"""
    try:
        import urllib.parse
        import urllib.request
        tok = bot_token()
        if not tok:
            wd_log("通知できず（ボットトークンが見つからない）")
            return
        data = urllib.parse.urlencode({"channel": NOTIFY_DM, "text": text}).encode()
        req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=data,
                                     headers={"Authorization": "Bearer " + tok})
        res = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        wd_log(f"通知 ok={res.get('ok')} {res.get('error', '')}")
    except Exception as e:  # noqa: BLE001
        wd_log(f"通知失敗 {type(e).__name__}")


def watchdog():
    """プロセスは生きているのに Slack 接続が切れ続けて応答しない状態を見つけて再起動する（KeepAlive では拾えない）。"""
    st = wd_load()
    size = err_size()
    prev = int(st.get("err_size", 0))
    if size < prev:          # ログが作り直された
        prev = 0
    hits = count_disconnects(prev)
    now = time.time()
    st.update(err_size=size, last_check=now, last_hits=hits)
    wd_save(st)
    if hits < 3:
        st.pop("defer_since", None)
        wd_save(st)
        return
    # 今も続いているか（30秒観察）
    time.sleep(30)
    live = count_disconnects(size)
    st = wd_load()
    st.update(err_size=err_size(), last_live_hits=live)
    wd_save(st)
    if live < 1:
        wd_log(f"切断エラー {hits}件（前回点検以降）。観察中は止んでいたので様子見")
        return
    pid = bot_pid()
    if answering_now(pid):
        ds = float(st.get("defer_since", 0)) or now
        st["defer_since"] = ds
        wd_save(st)
        if now - ds < 1800:
            wd_log(f"切断エラー継続（{hits}件）。回答作成中のため再起動を見送り")
            return
    last_restart = float(st.get("last_restart", 0))
    if now - last_restart < 3600:
        if now - float(st.get("last_fail_notify", 0)) > 6 * 3600:
            notify(":warning: データマンを再起動しましたが、Slack との接続がまだ切れ続けています（直近の切断エラー"
                   f"{hits}件）。Mac mini のネットワークか、同じ鍵で動いている別の応答役が無いかの確認が必要です。")
            st = wd_load()
            st["last_fail_notify"] = now
            wd_save(st)
        wd_log(f"切断エラー継続（{hits}件）。1時間以内に再起動済みのため今回は見送り")
        return
    info = restart_bot(f"watchdog hits={hits} live={live}")
    wd_log(f"再起動前の様子: TCP接続 {info.get('old_tcp_count')}本／スレッド {info.get('old_threads')}／"
           f"{(info.get('old_proc') or '').strip().splitlines()[-1][:120] if info.get('old_proc') else ''}")
    st = wd_load()
    st.pop("defer_since", None)
    if now - float(st.get("last_notify", 0)) > 6 * 3600:
        notify(":wrench: データマンの Slack 接続が切れたまま応答できない状態になっていたため、自動で再起動しました"
               f"（前回点検以降の切断エラー {hits}件）。1〜2分後から通常どおり応答します。")
        st["last_notify"] = now
    wd_save(st)


def check_bot(since_size: int, wait: int = 60) -> dict:
    time.sleep(wait)
    pid = bot_pid()
    return {
        "waited_sec": wait,
        "pid": pid,
        "launchctl": sh(f"launchctl print gui/{os.getuid()}/{BOT_LABEL} 2>&1 | grep -E 'state =|pid =|last exit|runs =' | head -10"),
        "proc": sh(f"ps -o pid,lstart,etime,%cpu,rss,command -p {pid}") if pid else "",
        "tcp": sh(f"lsof -nP -a -p {pid} -iTCP 2>/dev/null | head -20") if pid else "",
        "disconnects_since_restart": count_disconnects(since_size),
        "bot_log_tail": sh(f"tail -n 8 '{BOT_LOG}' | cut -c1-240"),
        "bot_out_tail": sh(f"ls -la '{os.path.join(DM_DIR, 'logs', 'bot.out.log')}'; tail -n 6 '{os.path.join(DM_DIR, 'logs', 'bot.out.log')}' | cut -c1-200"),
        "answer_tail": sh(f"ls -la '{os.path.join(DM_DIR, 'logs', 'answer.log')}'; tail -n 6 '{os.path.join(DM_DIR, 'logs', 'answer.log')}' | cut -c1-200"),
        "bot_err_tail": sh(f"ls -la '{BOT_ERR}'; tail -n 12 '{BOT_ERR}'"),
        "watchdog_log": sh(f"tail -n 10 '{WD_LOG}'"),
    }


def read_source() -> dict:
    def rd(path, limit=150000):
        try:
            return redact(open(path, encoding="utf-8", errors="replace").read())[:limit]
        except Exception as e:  # noqa: BLE001
            return f"[読めず] {type(e).__name__}"
    py = os.path.join(DM_DIR, ".venv", "bin", "python3")
    return {
        "bot.py": rd(os.path.join(DM_DIR, "bot.py")),
        "answer.py": rd(os.path.join(DM_DIR, "answer.py")),
        "python": sh(f"'{py}' -V"),
        "pip": sh(f"'{py}' -m pip list 2>/dev/null | grep -iE 'slack|websocket|aiohttp|certifi|requests'"),
        "env_keys": sh(f"sed -E 's/=.*/=…/' '{os.path.join(DM_DIR, '.env')}'"),
    }


def bot_token() -> str:
    try:
        for line in open(os.path.join(DM_DIR, ".env"), encoding="utf-8"):
            v = line.strip().split("=", 1)[1].strip().strip('"').strip("'") if "=" in line else ""
            if v.startswith("xoxb-"):
                return v
    except Exception:
        pass
    return ""


def slack_api(method: str, params: dict) -> dict:
    import urllib.parse
    import urllib.request
    tok = bot_token()
    if not tok:
        return {"ok": False, "error": "no_bot_token"}
    import urllib.error
    url = "https://slack.com/api/" + method + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
            return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(min(int(e.headers.get("Retry-After", "3") or 3), 20))
                continue
            return {"ok": False, "error": f"HTTP{e.code}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": type(e).__name__}
    return {"ok": False, "error": "ratelimited"}


def slack_probe(user_ids) -> dict:
    out = {}
    a = slack_api("auth.test", {})
    out["auth"] = {k: a.get(k) for k in ("ok", "error", "team", "team_id", "user", "user_id", "bot_id",
                                         "enterprise_id", "is_enterprise_install")}
    for uid in user_ids:
        u = slack_api("users.info", {"user": uid})
        uu = u.get("user") or {}
        out[f"user_{uid}"] = {"ok": u.get("ok"), "error": u.get("error"),
                              **{k: uu.get(k) for k in ("name", "deleted", "is_restricted", "is_ultra_restricted",
                                                        "is_stranger", "team_id", "is_bot", "updated",
                                                        "is_invited_user")},
                              "enterprise_user": bool(uu.get("enterprise_user")),
                              "real_name": (uu.get("profile") or {}).get("real_name")}
    ims, cursor = [], ""
    for _ in range(10):
        prm = {"types": "im", "limit": 200}
        if cursor:
            prm["cursor"] = cursor
        r = slack_api("conversations.list", prm)
        if not r.get("ok"):
            out["im_list_error"] = r.get("error")
            break
        ims += r.get("channels", [])
        cursor = (r.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    out["im_count"] = len(ims)
    out["ims"] = [{k: c.get(k) for k in ("id", "user", "is_user_deleted", "created", "is_ext_shared",
                                         "is_org_shared", "is_archived", "is_open")} for c in ims]
    for uid in user_ids:
        hit = [c for c in ims if c.get("user") == uid]
        if not hit:
            out[f"im_{uid}"] = "DM一覧を取得できず" if "im_list_error" in out else "ボットのDM一覧に無い"
            continue
        cid = hit[0]["id"]
        info = slack_api("conversations.info", {"channel": cid})
        ch = info.get("channel") or {}
        latest = ch.get("latest")
        d = {"id": cid, "info_ok": info.get("ok"), "error": info.get("error"),
             **{k: ch.get(k) for k in ("is_ext_shared", "is_org_shared", "is_shared", "is_pending_ext_shared",
                                       "is_archived", "is_open", "is_user_deleted", "created", "last_read",
                                       "context_team_id", "shared_team_ids", "connected_team_ids")},
             "latest_ts": latest.get("ts") if isinstance(latest, dict) else latest}
        h = slack_api("conversations.history", {"channel": cid, "limit": 5})
        d["history"] = {"ok": h.get("ok"), "error": h.get("error"),
                        "msgs": [{"ts": m.get("ts"), "user": m.get("user"), "bot_id": m.get("bot_id"),
                                  "subtype": m.get("subtype"), "text": (m.get("text") or "")[:60]}
                                 for m in h.get("messages", [])]}
        out[f"im_{uid}"] = d
    return out


def claude_check() -> dict:
    """answer.py と同じく .env の Claude 用トークンを渡して、短い問いに答えられるかを見る（値は表示しない）。"""
    t0 = time.time()
    env = dict(ENV)
    try:
        for line in open(os.path.join(DM_DIR, ".env"), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    try:
        r = subprocess.run([os.path.join(HOME, ".local", "bin", "claude"), "-p", "1+1の答えを半角数字だけで返して",
                            "--max-turns", "1"], capture_output=True, text=True, timeout=100, env=env, cwd=DM_DIR)
        return {"rc": r.returncode, "sec": round(time.time() - t0, 1), "out": redact(r.stdout)[:300],
                "err": redact(r.stderr)[-600:]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:300], "sec": round(time.time() - t0, 1)}


SYSTEM_SUBTYPES = {"channel_join", "channel_leave", "group_join", "group_leave", "bot_message", "bot_add",
                   "bot_remove", "channel_topic", "channel_purpose", "channel_name", "pinned_item",
                   "message_changed", "message_deleted"}


def missed_scan(since: float) -> dict:
    out = {"since": datetime.fromtimestamp(since, JST).isoformat()}
    me = slack_api("auth.test", {})
    bot_uid = me.get("user_id", "")
    found, errors, calls = [], [], 0
    # 1) ボットとのDM
    ims, cursor = [], ""
    for _ in range(10):
        prm = {"types": "im", "limit": 200}
        if cursor:
            prm["cursor"] = cursor
        r = slack_api("conversations.list", prm)
        if not r.get("ok"):
            errors.append(f"im list: {r.get('error')}")
            break
        ims += r.get("channels", [])
        cursor = (r.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    out["im_total"] = len(ims)
    for c in ims:
        if c.get("is_user_deleted") or c.get("user") in ("USLACKBOT", bot_uid):
            continue
        if calls > 180:
            errors.append("呼び出し上限で打ち切り")
            break
        h = slack_api("conversations.history", {"channel": c["id"], "oldest": f"{since:.6f}", "limit": 50})
        calls += 1
        if not h.get("ok"):
            errors.append(f"{c['id']}: {h.get('error')}")
            continue
        for m in h.get("messages", []):
            if m.get("bot_id") or m.get("user") == bot_uid or m.get("subtype") in SYSTEM_SUBTYPES:
                continue
            found.append({"where": "DM", "channel": c["id"], "user": m.get("user"), "ts": m.get("ts"),
                          "thread_ts": m.get("thread_ts"), "replies": m.get("reply_count", 0),
                          "text": (m.get("text") or "")[:160]})
    # 2) ボットが入っているチャンネルでの @メンション（親メッセージ）
    chans, cursor = [], ""
    for _ in range(10):
        prm = {"types": "public_channel,private_channel", "limit": 200, "exclude_archived": "true"}
        if cursor:
            prm["cursor"] = cursor
        r = slack_api("users.conversations", prm)
        if not r.get("ok"):
            errors.append(f"channel list: {r.get('error')}")
            break
        chans += r.get("channels", [])
        cursor = (r.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    out["channel_total"] = len(chans)
    for c in chans:
        if calls > 260:
            errors.append("呼び出し上限で打ち切り")
            break
        h = slack_api("conversations.history", {"channel": c["id"], "oldest": f"{since:.6f}", "limit": 200})
        calls += 1
        if not h.get("ok"):
            errors.append(f"{c.get('name')}: {h.get('error')}")
            continue
        for m in h.get("messages", []):
            if f"<@{bot_uid}>" in (m.get("text") or "") and not m.get("bot_id") and m.get("user") != bot_uid:
                found.append({"where": "#" + str(c.get("name")), "channel": c["id"], "user": m.get("user"),
                              "ts": m.get("ts"), "thread_ts": m.get("thread_ts"), "replies": m.get("reply_count", 0),
                              "text": (m.get("text") or "")[:160]})
            # スレッド内の @メンションも拾う（返信のあるスレッドだけ）
            if m.get("reply_count") and calls <= 260:
                rp = slack_api("conversations.replies", {"channel": c["id"], "ts": m["ts"], "oldest": f"{since:.6f}",
                                                         "limit": 100})
                calls += 1
                for x in (rp.get("messages") or [])[1:]:
                    if float(x.get("ts", 0)) >= since and f"<@{bot_uid}>" in (x.get("text") or "") \
                            and not x.get("bot_id") and x.get("user") != bot_uid:
                        found.append({"where": "#" + str(c.get("name")) + "（スレッド）", "channel": c["id"],
                                      "user": x.get("user"), "ts": x.get("ts"), "thread_ts": x.get("thread_ts"),
                                      "text": (x.get("text") or "")[:160]})
    # 名前を付ける
    names = {}
    for f in found:
        u = f.get("user")
        if u and u not in names:
            ui = slack_api("users.info", {"user": u})
            names[u] = ((ui.get("user") or {}).get("profile") or {}).get("real_name") or u
        f["name"] = names.get(u, u)
        f["at"] = datetime.fromtimestamp(float(f["ts"]), JST).strftime("%m/%d %H:%M")
    found.sort(key=lambda x: float(x["ts"]))
    out.update(found=found, errors=errors[:30], api_calls=calls)
    return out


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
        elif a == "read_source":
            result["actions"]["read_source"] = read_source()
        elif a == "restart_bot":
            st = wd_load()
            if time.time() - float(st.get("last_restart", 0)) < 300:
                result["actions"]["restart_bot"] = {"skipped": "見張りが直前に再起動済み",
                                                    "last_restart_source": st.get("last_restart_source"),
                                                    "last_restart": datetime.fromtimestamp(float(st["last_restart"]), JST).isoformat()}
                restart_size = 0 if st.get("err_size") is None else int(st.get("err_size", 0))
            else:
                restart_size = err_size()
                result["actions"]["restart_bot"] = restart_bot(f"ops {rid}")
                restart_size = result["actions"]["restart_bot"].get("err_size_before", restart_size)
            result["restart_err_size"] = restart_size
        elif a == "check_bot":
            result["actions"]["check_bot"] = check_bot(int(result.get("restart_err_size", err_size())))
        elif a == "check_now":
            result["actions"]["check_now"] = check_bot(int(wd_load().get("err_size_at_restart", 0)), wait=0)
        elif a == "slack_probe":
            uids = [u for u in req.get("probe_users", []) if re.fullmatch(r"[UW][A-Z0-9]{6,15}", str(u))][:6]
            result["actions"]["slack_probe"] = slack_probe(uids)
        elif a == "claude_check":
            result["actions"]["claude_check"] = claude_check()
        elif a == "missed_scan":
            result["actions"]["missed_scan"] = missed_scan(float(req.get("since", time.time() - 86400)))
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
        watchdog()
    except Exception as e:  # noqa: BLE001
        log(f"watchdog error: {type(e).__name__}: {e}")
    try:
        run()
    except Exception as e:  # noqa: BLE001
        log(f"ops error: {type(e).__name__}: {e}")
