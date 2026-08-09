"""5단계 회귀 — 휴대폰 접속의 fail-closed 성질.

실행:  python tests/test_mobile.py

여기서 검사하는 건 기능이 아니라 **안전 성질**이다. "PIN 없이는 LAN 에 열리지
않는다"가 세 겹 전부에서 성립하는지 본다 (→ core/mobile.py).

키체인을 실제로 쓰지 않으려고 `core.keys` 를 메모리 저장소로 바꿔 끼운다 —
개발 PC 의 자격 증명 저장소에 테스트 PIN 을 남기지 않기 위해서다.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 콘솔이 cp949 여도 한글·—(em dash)가 든 결과를 찍을 수 있게 (Windows 기본 코드페이지)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-mob-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")
os.environ["RA_EDITION"] = "installed"

from core import keys, mobile, settings  # noqa: E402

# --- 키체인을 메모리로 바꿔 끼운다 (실제 자격 증명 저장소를 건드리지 않는다)
_store = {}
keys.available = lambda: True
keys.get = lambda name: _store.get(name) or os.getenv(name) or ""
keys.set = lambda name, value: _store.__setitem__(name, value)
keys.delete = lambda name: _store.pop(name, None)
keys.stored_in_keyring = lambda name: name in _store

_fails = []
_checks = 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


def section(t):
    print(f"\n== {t}")


# ------------------------------------------------------------------ PIN

section("PIN 규칙")

check(mobile.pin_set() is False, "처음에는 PIN 이 없다")

for bad, why in [("", "빈 값"), ("12345", "5자"), ("111111", "같은 문자 반복")]:
    try:
        mobile.set_pin(bad)
        check(False, f"{why} PIN 은 거부해야 한다")
    except mobile.PinError:
        check(True, f"{why} PIN 거부")

mobile.set_pin("482913")
check(mobile.pin_set() is True, "PIN 저장됨")
check(mobile.verify_pin("482913") is True, "맞는 PIN 통과")
check(mobile.verify_pin("482914") is False, "틀린 PIN 거부")
check(mobile.verify_pin("") is False, "빈 PIN 거부")
check(mobile.verify_pin(None) is False, "None 거부")

# ------------------------------------------------------------------ 세 겹

section("fail-closed 세 겹")

# 1번 겹 — 화면
mobile.set_enabled(True)
check(mobile.enabled() is True, "PIN 이 있으면 켤 수 있다")
check(mobile.should_bind_all() is True, "켜져 있고 PIN 이 있으면 0.0.0.0")

# 2번 겹 — 런처. 설정 파일을 손으로 고쳐도 PIN 이 없으면 안 열린다.
_store.pop(mobile.PIN_KEY, None)
check(mobile.enabled() is True, "설정에는 여전히 '켬' 이 남아 있다")
check(mobile.should_bind_all() is False,
      "**PIN 이 사라지면 설정이 '켬' 이어도 LAN 에 열지 않는다** (런처가 막는다)")

mobile.set_pin("482913")
try:
    mobile.set_enabled(True)
    _store.pop(mobile.PIN_KEY, None)
    mobile.set_enabled(True)
    check(False, "PIN 없이 켜는 것은 거부해야 한다")
except mobile.PinError:
    check(True, "PIN 없이 켜기 거부 (화면이 막는다)")

# PIN 을 지우면 토글도 함께 꺼진다 — 켠 채로 PIN 만 없는 상태를 남기지 않는다
mobile.set_pin("482913")
mobile.set_enabled(True)
mobile.clear_pin()
check(mobile.enabled() is False, "PIN 을 지우면 휴대폰 접속도 꺼진다")
check(mobile.should_bind_all() is False, "지운 뒤에는 당연히 127.0.0.1")
check((settings.load().get("mobile") or {}).get("pin_set") is False,
      "설정 파일의 pin_set 도 함께 내려간다")

# 끄기는 PIN 이 없어도 언제나 된다
mobile.set_enabled(False)
check(mobile.enabled() is False, "PIN 없이도 끌 수는 있다")

# ------------------------------------------------------------------ 잠금

section("브루트포스 지연")

mobile.set_pin("482913")
mobile.reset_failures()
check(mobile.locked_for() == 0, "처음에는 잠기지 않았다")
for i in range(mobile.LOCK_AFTER - 1):
    mobile.note_failure()
check(mobile.locked_for() == 0, f"{mobile.LOCK_AFTER - 1}회까지는 통과")
check(mobile.failures_left() == 1, "남은 시도 횟수를 알려 준다")
mobile.note_failure()
check(mobile.locked_for() > 0, f"{mobile.LOCK_AFTER}회 실패하면 잠긴다")
check(mobile.failures_left() == 0, "잠긴 뒤 남은 시도는 0")
mobile.reset_failures()
check(mobile.locked_for() == 0, "성공하면 초기화된다")

# ------------------------------------------------------------------ 주소

section("LAN 주소")

ips = mobile.lan_ips()
check(isinstance(ips, list), "주소 목록을 돌려준다")
check(all("." in ip for ip in ips), f"IPv4 형식 {ips}")
check(all(not ip.startswith("127.") for ip in ips), "루프백은 빼고 준다")
check(mobile.lan_url("192.168.0.5", 8501) == "http://192.168.0.5:8501", "URL 조립")

# ------------------------------------------------------------------ 런처 판정

section("런처 bind_address()")

sys.path.insert(0, str(ROOT / "packaging"))
import launcher  # noqa: E402

mobile.set_pin("482913")
mobile.set_enabled(True)
check(launcher.bind_address() == "0.0.0.0", "켬 + PIN → 0.0.0.0")
mobile.set_enabled(False)
check(launcher.bind_address() == "127.0.0.1", "끔 → 127.0.0.1")

# 판정이 터지면 닫는 쪽으로 떨어진다
_broken = mobile.should_bind_all
mobile.should_bind_all = lambda: (_ for _ in ()).throw(RuntimeError("고장"))
try:
    check(launcher.bind_address() == "127.0.0.1",
          "판정이 실패하면 127.0.0.1 (fail-closed)")
finally:
    mobile.should_bind_all = _broken

# ------------------------------------------------------------------ 진단 스크럽

section("진단 내보내기 — 비밀 제거")

import ui_mobile  # noqa: E402

# 가짜 키를 **조립해서** 만든다 — 소스에 키 모양 문자열을 그대로 두면
# 저장소 시크릿 사전 검사(→ docs/11)와 GitHub 푸시 보호에 영원히 걸린다.
FAKE = [
    "sk-ant-" + "api03-" + "A" * 8 + "B" * 8,
    "AIza" + "Sy" + "C" * 20 + "0123",
    "sk-" + "proj-" + "Z" * 16,
    "ey" + "JhbGciOiJIUzI1NiJ9." + "d" * 16,
]
raw = (
    f"ERROR anthropic: {FAKE[0]} 로 호출 실패\n"
    f"google: key={FAKE[1]} 거부\n"
    f"openai: {FAKE[2]}\n"
    f"Authorization: Bearer {FAKE[3]}\n"
    "정상 로그 한 줄\n"
)
clean = ui_mobile._scrub(raw)
for token in FAKE:
    check(token not in clean, f"{token[:12]}… 가 제거됐다")
check("정상 로그 한 줄" in clean, "정상 로그는 남는다")
check(clean.count("[제거됨]") >= 4, "제거된 자리를 표시한다")

# ------------------------------------------------------------------

print(f"\n{'=' * 60}")
if _fails:
    print(f"실패 {len(_fails)}/{_checks}")
    for f in _fails:
        print(f"  - {f}")
else:
    print(f"전부 통과 — {_checks}항목")
shutil.rmtree(_SANDBOX, ignore_errors=True)
sys.exit(1 if _fails else 0)
