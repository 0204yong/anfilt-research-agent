"""6단계 회귀 — 업데이트 판정 · 검증 · 안전장치.

실행:  python tests/test_updates.py

여기서 지키려는 것은 "업데이트가 된다"가 아니라 **"잘못된 것이 적용되지
않는다"** 다. 실제 교체는 별도 프로세스라 e2e 로 따로 확인한다(→ docs/23 6단계).

로컬 http 서버를 띄워 진짜로 받아 본다 — sha256 검증은 흉내로는 못 믿는다.
"""
import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-upd-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")
os.environ["RA_EDITION"] = "installed"

from core import updates  # noqa: E402
import updater  # noqa: E402

_fails, _checks = [], 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


def section(t):
    print(f"\n== {t}")


# ------------------------------------------------------------------ 버전

section("버전 비교")

check(updates.parse_version("1.4.2") == (1, 4, 2, 0), "1.4.2 파싱")
check(updates.parse_version("1.4.2-rc1") == (1, 4, 2, 0), "숫자 아닌 꼬리는 버린다")
check(updates.parse_version("0.1") == (0, 1, 0, 0), "짧은 버전은 0으로 채운다")
check(updates.parse_version("") == (0, 0, 0, 0), "빈 값도 터지지 않는다")
check(updates.parse_version("dev") == (0, 0, 0, 0), "'dev' 는 0.0.0 취급")
check(updates.compare("1.4.2", "1.4.10") < 0, "1.4.2 < 1.4.10 (문자열 비교였다면 반대)")
check(updates.compare("1.10.0", "1.9.9") > 0, "1.10.0 > 1.9.9")
check(updates.compare("2.0", "2.0.0") == 0, "2.0 == 2.0.0")

# ------------------------------------------------------------------ 판정

section("decide() — 무엇을 받게 할 것인가")

BASE = {
    "version": "1.2.0", "runtime_version": "3.12.10",
    "delta": {"url": "https://x/app.zip", "sha256": "a" * 64, "size": 100},
    "installer": {"url": "https://x/setup.exe", "sha256": "b" * 64, "size": 200},
}

d = updates.decide(BASE, "1.1.0", "3.12.10")
check(d["available"] and d["kind"] == "delta", "런타임이 같으면 델타")

d = updates.decide(BASE, "1.1.0", "3.11.5")
check(d["available"] and d["kind"] == "installer", "런타임이 다르면 전체 인스톨러")
check("파이썬 구성요소" in d["reason"], "왜 큰 파일을 받는지 설명한다")

d = updates.decide(BASE, "1.2.0", "3.12.10")
check(not d["available"], "같은 버전이면 업데이트 없음")
d = updates.decide(BASE, "1.3.0", "3.12.10")
check(not d["available"], "더 높은 버전을 쓰고 있으면 업데이트 없음 (다운그레이드 금지)")

d = updates.decide({**BASE, "min_supported": "1.2.0"}, "1.1.0", "3.12.10")
check(d["blocked"] and d["available"], "min_supported 미만이면 잠그고, 받을 것도 준다")
d = updates.decide({**BASE, "min_supported": "1.0.0"}, "1.1.0", "3.12.10")
check(not d["blocked"], "min_supported 이상이면 잠그지 않는다")

d = updates.decide({"version": "9.9.9"}, "1.0.0", "3.12.10")
check(not d["available"] and "파일 정보가 없" in d["reason"],
      "받을 파일 정보가 없으면 받지 않는다")
d = updates.decide({"version": "9.9.9", "delta": {"url": "https://x/a.zip"}},
                   "1.0.0", "3.12.10")
check(not d["available"], "**sha256 없는 항목은 쓰지 않는다** (검증할 수 없으므로)")

d = updates.decide({**BASE, "runtime_version": "9.9", "delta": BASE["delta"]},
                   "1.1.0", "3.12.10")
check(d["kind"] == "installer", "런타임이 다르면 델타가 있어도 인스톨러")
check(updates.decide({**BASE, "critical": True}, "1.1.0", "3.12.10")["critical"],
      "critical 플래그가 전달된다")

# ------------------------------------------------------------------ 주소

section("주소 규칙 — 평문으로 받지 않는다")

for url, ok, why in [
    ("https://anfilt.co.kr/releases/latest.json", True, "https 허용"),
    ("http://127.0.0.1:9/latest.json", True, "로컬 http 허용 (테스트용)"),
    ("http://localhost:9/latest.json", True, "localhost http 허용"),
    ("http://example.com/latest.json", False, "**외부 http 거부**"),
    ("file:///C:/evil.json", False, "file:// 거부"),
    ("ftp://x/y", False, "ftp 거부"),
]:
    try:
        updates._check_url(url)
        check(ok, why)
    except updates.UpdateError:
        check(not ok, why)

# ------------------------------------------------------------------ 내려받기

section("download() — sha256 이 맞아야만 통과")

payload = b"PK-fake-payload-" + b"x" * 5000
good_sha = hashlib.sha256(payload).hexdigest()
served = _SANDBOX / "www"
served.mkdir()
(served / "app-1.2.0.zip").write_bytes(payload)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(served), **kw)

    def log_message(self, *a):
        pass


httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{PORT}/app-1.2.0.zip"

dest = _SANDBOX / "dl"
got = updates.download({"url": URL, "sha256": good_sha, "size": len(payload)}, dest)
check(got.exists() and got.read_bytes() == payload, "정상 파일은 받아진다")
check(not list(dest.glob("*.part")), "임시(.part) 파일이 남지 않는다")

try:
    updates.download({"url": URL, "sha256": "c" * 64}, dest)
    check(False, "sha256 불일치는 예외를 내야 한다")
except updates.UpdateError as e:
    check("위변조" in str(e) or "손상" in str(e), "sha256 불일치를 명확히 알린다")
check(not list(dest.glob("*.part")), "**불일치 파일은 남기지 않는다**")

try:
    updates.download({"url": f"http://example.com/x.zip", "sha256": good_sha}, dest)
    check(False, "외부 http 는 받으면 안 된다")
except updates.UpdateError:
    check(True, "외부 http 다운로드 거부")

progress = []
updates.download({"url": URL, "sha256": good_sha, "size": len(payload)}, dest,
                 progress=progress.append)
check(progress and progress[-1] == 1.0, "진행률이 1.0 까지 간다")

# 매니페스트도 같은 서버에서. runtime_version 은 **지금 이 환경의 값**으로 적는다 —
# 그래야 델타 경로가 실제로 타진다 (개발 상태에서는 'unknown').
from core import edition  # noqa: E402

(served / "latest.json").write_text(json.dumps({
    "version": "9.9.9", "runtime_version": edition.runtime_version(),
    "delta": {"url": URL, "sha256": good_sha, "size": len(payload)},
}), encoding="utf-8")
m = updates.fetch_manifest(f"http://127.0.0.1:{PORT}/latest.json")
check(m["version"] == "9.9.9", "매니페스트를 읽는다")

(served / "broken.json").write_text("{ 이건 JSON 이 아님", encoding="utf-8")
try:
    updates.fetch_manifest(f"http://127.0.0.1:{PORT}/broken.json")
    check(False, "깨진 매니페스트는 예외")
except updates.UpdateError:
    check(True, "깨진 매니페스트를 거부한다")

os.environ[updates.MANIFEST_ENV] = f"http://127.0.0.1:{PORT}/nope.json"
res = updates.check(force=True)
check(res.get("error") and not res.get("available"),
      "**확인 실패는 예외가 아니라 error 로 돌아온다** (앱을 막지 않는다)")

os.environ[updates.MANIFEST_ENV] = f"http://127.0.0.1:{PORT}/latest.json"
res = updates.check(force=True)
check(res.get("available") and res["kind"] == "delta", "확인 → 판정까지 이어진다")

updates.skip("9.9.9")
check(updates.skipped("9.9.9") and not updates.skipped("9.9.8"), "버전 건너뛰기")
check(updates.auto_enabled() is True, "자동 확인은 기본 켬")
updates.set_auto(False)
check(updates.auto_enabled() is False, "자동 확인을 끌 수 있다")

# ------------------------------------------------------------------ 조사 중

section("조사 중에는 재시작하지 않는다")

check(updates.busy() == {}, "처음에는 한가하다")
updates.mark_busy("조사")
check(updates.busy().get("what") == "조사", "조사 중 표시가 잡힌다")
updates.clear_busy()
check(updates.busy() == {}, "끝나면 지워진다")

with updates.busy_guard("조사"):
    check(bool(updates.busy()), "guard 안에서는 표시가 있다")
check(updates.busy() == {}, "guard 를 빠져나오면 지워진다")

try:
    with updates.busy_guard("조사"):
        raise RuntimeError("조사 실패")
except RuntimeError:
    pass
check(updates.busy() == {}, "**실패해도 표시가 남지 않는다** (영영 미뤄지면 안 된다)")

updates.mark_busy("조사")
import json as _json
_bf = updates._busy_file()
_d = _json.loads(_bf.read_text(encoding="utf-8"))
_d["since"] = 0
_bf.write_text(_json.dumps(_d), encoding="utf-8")
check(updates.busy() == {}, "낡은 표시는 무시한다 (앱이 죽어 남은 경우)")
check(not _bf.exists(), "낡은 표시는 치운다")

# ------------------------------------------------------------------ zip 검사

section("updater — 무엇을 풀어도 되는가")


def make_zip(names, with_app=True):
    p = _SANDBOX / f"z{len(list(_SANDBOX.glob('z*.zip')))}.zip"
    with zipfile.ZipFile(p, "w") as zf:
        if with_app:
            zf.writestr("app.py", "print('hi')")
        for n in names:
            zf.writestr(n, "x")
    return p


with zipfile.ZipFile(make_zip(["core/pipeline.py", "pages/1.py"])) as zf:
    check(len(updater.safe_members(zf)) == 3, "정상 zip 은 통과")

for names, why in [
    (["../evil.py"], "상위 경로(..) 거부"),
    (["core/../../evil.py"], "중간에 낀 .. 도 거부"),
    (["/etc/passwd"], "절대경로 거부"),
    (["C:/Windows/evil.py"], "드라이브 지정 거부"),
]:
    with zipfile.ZipFile(make_zip(names)) as zf:
        try:
            updater.safe_members(zf)
            check(False, why)
        except ValueError:
            check(True, why)

with zipfile.ZipFile(make_zip(["core/x.py"], with_app=False)) as zf:
    try:
        updater.safe_members(zf)
        check(False, "app.py 없는 zip 은 거부해야 한다")
    except ValueError as e:
        check("앱 압축 파일이 아닙니다" in str(e),
              "**app.py 가 없으면 거부** (엉뚱한 zip 으로 앱을 지우지 않는다)")

empty = _SANDBOX / "empty.zip"
with zipfile.ZipFile(empty, "w"):
    pass
with zipfile.ZipFile(empty) as zf:
    try:
        updater.safe_members(zf)
        check(False, "빈 zip 은 거부해야 한다")
    except ValueError:
        check(True, "빈 zip 거부")

# 검사를 통과한 zip 만 전개된다
out = _SANDBOX / "extracted"
updater.extract(make_zip(["core/pipeline.py"]), out)
check((out / "app.py").exists() and (out / "core" / "pipeline.py").exists(),
      "정상 zip 은 구조 그대로 풀린다")

# ------------------------------------------------------------------ 런처 복구

section("교체 도중 끊겼을 때")

import launcher  # noqa: E402

fake_install = _SANDBOX / "install"
(fake_install / "app.bak" / "core").mkdir(parents=True)
(fake_install / "app.bak" / "app.py").write_text("old", encoding="utf-8")
launcher.BASE = fake_install
launcher.APP_DIR = fake_install / "app"

check(launcher.recover_from_interrupted_update() is True,
      "app 이 없고 app.bak 만 있으면 되돌린다")
check((fake_install / "app" / "app.py").read_text(encoding="utf-8") == "old",
      "이전 버전이 복구된다")
check(not (fake_install / "app.bak").exists(), "복구 후 백업은 사라진다")

# 교체는 끝났는데 뒷정리만 못 한 경우 — 백업만 지운다
(fake_install / "app.bak").mkdir()
(fake_install / "app.bak" / "app.py").write_text("old", encoding="utf-8")
(fake_install / "app" / "app.py").write_text("new", encoding="utf-8")
check(launcher.recover_from_interrupted_update() is False, "정상 상태면 되돌리지 않는다")
check((fake_install / "app" / "app.py").read_text(encoding="utf-8") == "new",
      "**새 버전을 옛 버전으로 덮지 않는다**")
check(not (fake_install / "app.bak").exists(), "남은 백업만 치운다")

# ------------------------------------------------------------------

httpd.shutdown()
print(f"\n{'=' * 60}")
if _fails:
    print(f"실패 {len(_fails)}/{_checks}")
    for f in _fails:
        print(f"  - {f}")
else:
    print(f"전부 통과 — {_checks}항목")
shutil.rmtree(_SANDBOX, ignore_errors=True)
sys.exit(1 if _fails else 0)
