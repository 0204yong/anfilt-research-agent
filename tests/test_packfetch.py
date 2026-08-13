"""체험판의 팩 인출 — `python tests\\test_packfetch.py`

네트워크를 타지 않는다. 가짜 HTTP 서버를 띄워 **실제 urllib 경로**를 지난다.

여기서 지키려는 것 둘:
  · 팩이 저장소에 없어도 체험판이 돈다 (구멍을 닫는 목적 자체)
  · **서버가 흔들려도 체험판은 멈추지 않는다** — 낡은 팩이 화면 정지보다 낫다
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-packfetch-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")
os.environ["RA_EDITION"] = "trial"

from core import packfetch  # noqa: E402

_fails, _checks = [], 0

TOKEN = "t0ken-" + "x" * 20
PACK = {"pack_version": "9.9", "prompts": {"a": "가"}, "schemas": {}, "config": {},
        "seed": {"n.md": "노트"}}

state = {"token": TOKEN, "fail": False, "calls": 0, "version": "9.9"}


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        state["calls"] += 1
        if state["fail"]:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"ok":false,"reason":"server_error"}')
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("trial_token") != state["token"]:
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":false,"reason":"invalid"}')
            return
        pack = dict(PACK, pack_version=state["version"])
        out = json.dumps({
            "ok": True, "pack_version": state["version"],
            "pack_b64": base64.b64encode(
                json.dumps(pack, ensure_ascii=False).encode("utf-8")).decode("ascii"),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["RA_LICENSE_SERVER"] = f"http://127.0.0.1:{srv.server_port}"

print("== 체험판 팩 인출")

# ---------------------------------------------------------------- 토큰이 없을 때
os.environ.pop(packfetch.TOKEN_ENV, None)
packfetch.reset()
check(packfetch.configured() is False, "토큰이 없으면 이 경로를 쓰지 않는다")
check(packfetch.load() is None, "토큰이 없으면 None (로컬 파일로 넘어간다)")

# ---------------------------------------------------------------- 정상 인출
os.environ[packfetch.TOKEN_ENV] = TOKEN
packfetch.reset()
got = packfetch.load()
check(isinstance(got, dict) and got["prompts"]["a"] == "가", "서버에서 팩을 받는다")
check(state["calls"] == 1, "한 번만 받았다")

packfetch.reset()
before = state["calls"]
packfetch.load()
check(state["calls"] == before, "**캐시가 있으면 서버를 다시 부르지 않는다**")

# ---------------------------------------------------------------- 서버가 죽어도
state["fail"] = True
packfetch.reset()
out = packfetch.load()
check(isinstance(out, dict) and out["prompts"]["a"] == "가",
      "**서버가 500 이어도 캐시로 계속 돈다**")

# 캐시가 낡아도 마찬가지여야 한다 (신선도 만료 + 서버 장애)
os.utime(packfetch._cache_path(), (time.time() - 999999, time.time() - 999999))
packfetch.reset()
out = packfetch.load()
check(isinstance(out, dict), "**낡은 캐시라도 쓴다** (며칠 낡는 것 < 화면 정지)")

# ---------------------------------------------------------------- 캐시도 없으면
state["fail"] = True
packfetch._cache_path().unlink(missing_ok=True)
packfetch.reset()
try:
    packfetch.load()
    check(False, "캐시도 서버도 없으면 오류를 낸다")
except packfetch.FetchError:
    check(True, "캐시도 서버도 없으면 오류를 낸다 (조용히 넘어가지 않는다)")

# ---------------------------------------------------------------- 토큰이 틀리면
state["fail"] = False
os.environ[packfetch.TOKEN_ENV] = "wrong-token-wrong-token"
packfetch.reset()
try:
    packfetch.load()
    check(False, "틀린 토큰은 거절된다")
except packfetch.FetchError as e:
    check("토큰" in str(e), "틀린 토큰은 **원인을 말하는** 오류가 난다")

# ---------------------------------------------------------------- 갱신
os.environ[packfetch.TOKEN_ENV] = TOKEN
state["version"] = "10.0"
packfetch.reset()
packfetch._cache_path().unlink(missing_ok=True)
fresh = packfetch.load()
check(fresh["pack_version"] == "10.0", "팩을 갈면 새 버전을 받는다")

# ---------------------------------------------------------------- packs 와의 접합
from core import packs  # noqa: E402

packs.reload()
check(packs.is_available() is True, "packs 가 인출한 팩을 쓴다")
check(packs.get("a") == "가", "packs.get() 이 서버 팩에서 읽는다")
check(packs.version() == "10.0", "packs.version() 이 서버 팩 버전이다")
check(packs.seed() == {"n.md": "노트"}, "시드도 서버 팩에서 온다")

# 정식판에서는 이 옆문을 쓰지 않는다.
# edition.current() 는 lru_cache 다 — 환경변수만 바꾸면 반영되지 않는다.
from core import edition  # noqa: E402

os.environ["RA_EDITION"] = "installed"
edition.current.cache_clear()
packs.reload()
check(packs._hosted_pack() is None,
      "**정식판은 공유 토큰 경로를 쓰지 않는다** (라이선스가 유일한 자격)")
os.environ["RA_EDITION"] = "trial"
edition.current.cache_clear()
packs.reload()

srv.shutdown()
print("\n" + "=" * 60)
if _fails:
    print(f"실패 {len(_fails)}/{_checks}")
    for f in _fails:
        print(f"  - {f}")
else:
    print(f"전부 통과 — {_checks}항목")
shutil.rmtree(_SANDBOX, ignore_errors=True)
sys.exit(1 if _fails else 0)
