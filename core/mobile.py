"""휴대폰 접속 — 켜고 끄기, PIN, 바인딩 판정, LAN 주소.

→ docs/21 휴대폰에서 쓰기 · docs/23 설치판 구현 계획 5단계.

Streamlit 비의존. **런처(`packaging/launcher.py`)도 이 모듈을 import 한다** —
"0.0.0.0 으로 열어도 되는가"를 두 곳에서 각자 판단하면 언젠가 한쪽만 틀린다.

## 이 모듈의 유일한 규칙

    PIN 이 없으면 절대 LAN 에 열지 않는다.

같은 판정을 **세 겹**으로 건다. 한 겹이 뚫려도 나머지가 막는다.

  1. 화면    — PIN 이 없으면 토글이 켜지지 않는다 (`set_enabled`)
  2. 런처    — 기동할 때 다시 확인한다 (`should_bind_all`). 설정 파일을 손으로
               고쳐 `enabled: true` 로 만들어도 PIN 이 없으면 127.0.0.1 로 뜬다
  3. 앱      — 0.0.0.0 으로 떠 있으면 **모든 세션**에 PIN 을 요구한다
               (`ui_common.require_password`). 내 PC 에서 접속해도 예외 없다 —
               서버가 LAN 에 열려 있다는 사실만으로 충분한 이유다

무엇 하나라도 실패하면 **닫는 쪽**으로 떨어진다 (fail-closed).
"""
import hmac
import ipaddress
import socket
import time

from . import keys, settings

PIN_KEY = "RA_MOBILE_PIN"
MIN_PIN_LEN = 6

# 브루트포스 지연. 세션이 아니라 **프로세스 전역**이다 — 세션 상태에 두면
# 새로 접속하는 것만으로 초기화되어 아무것도 막지 못한다.
LOCK_AFTER = 5
LOCK_SECONDS = 120
_failures = []


class PinError(ValueError):
    """PIN 규칙 위반 — 화면이 그대로 보여 줄 수 있는 문구를 담는다."""


# ---------------------------------------------------------------- PIN


def pin_set() -> bool:
    return bool(keys.get(PIN_KEY))


def set_pin(pin: str) -> None:
    pin = str(pin or "").strip()
    if len(pin) < MIN_PIN_LEN:
        raise PinError(f"PIN 은 {MIN_PIN_LEN}자 이상이어야 합니다.")
    if len(set(pin)) == 1:
        raise PinError("같은 문자만 반복된 PIN 은 쓸 수 없습니다.")
    if not keys.available():
        raise PinError(
            "이 환경에서는 PIN 을 안전하게 저장할 수 없습니다"
            "(자격 증명 저장소를 찾지 못했습니다)."
        )
    keys.set(PIN_KEY, pin)


def clear_pin() -> None:
    """PIN 을 지우면 휴대폰 접속도 함께 꺼진다 — PIN 없이 열린 채로 남지 않게."""
    try:
        keys.delete(PIN_KEY)
    except Exception:                       # noqa: BLE001 — 없던 것을 지운 경우
        pass
    cfg = settings.load()
    cfg.setdefault("mobile", {})["enabled"] = False
    cfg["mobile"]["pin_set"] = False
    settings.save(cfg)


def verify_pin(value: str) -> bool:
    """맞으면 True. 타이밍 차이를 남기지 않으려 `compare_digest` 로 비교한다."""
    expected = keys.get(PIN_KEY)
    if not expected:
        return False
    return hmac.compare_digest(
        str(value or "").encode("utf-8"), str(expected).encode("utf-8")
    )


def locked_for() -> int:
    """남은 잠금 시간(초). 0이면 시도할 수 있다."""
    _prune()
    if len(_failures) < LOCK_AFTER:
        return 0
    return max(0, int(_failures[-1] + LOCK_SECONDS - time.time()))


def failures_left() -> int:
    """잠기기까지 남은 시도 횟수 (화면 안내용)."""
    _prune()
    return max(0, LOCK_AFTER - len(_failures))


def note_failure() -> None:
    _failures.append(time.time())


def reset_failures() -> None:
    _failures.clear()


def _prune() -> None:
    cutoff = time.time() - LOCK_SECONDS
    while _failures and _failures[0] < cutoff:
        _failures.pop(0)


# ---------------------------------------------------------------- 켜고 끄기


def enabled() -> bool:
    return bool((settings.load().get("mobile") or {}).get("enabled"))


def set_enabled(on: bool) -> None:
    """켜기는 PIN 이 있을 때만 된다 (1번 겹). 끄기는 언제나 된다."""
    if on and not pin_set():
        raise PinError("PIN 을 먼저 정해야 휴대폰 접속을 켤 수 있습니다.")
    cfg = settings.load()
    m = cfg.setdefault("mobile", {})
    m["enabled"] = bool(on)
    m["pin_set"] = pin_set()
    settings.save(cfg)


def should_bind_all() -> bool:
    """런처가 묻는 질문 — 0.0.0.0 으로 열어도 되는가 (2번 겹).

    설정만 보지 않고 **PIN 을 다시 확인한다.** config.json 은 평문이라
    사용자가 손으로 고칠 수 있고, 키체인의 PIN 은 그렇게 못 만든다.
    """
    return enabled() and pin_set()


# ---------------------------------------------------------------- 주소


def lan_ips() -> list:
    """이 PC 의 사설망 IPv4 주소들. 휴대폰이 접속할 주소 후보다.

    유선·무선·가상 어댑터(VirtualBox·WSL·Docker)가 섞여 나오므로 **여러 개를
    보여 주고 사용자가 고르게** 한다 — 하나를 골라 주면 하필 그게 가상
    어댑터일 때 "안 된다"로 끝난다.
    """
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            try:
                addr = ipaddress.IPv4Address(ip)
            except ValueError:
                continue
            if addr.is_private and not addr.is_loopback and ip not in found:
                found.append(ip)
    except OSError:
        pass
    # 기본 경로로 나가는 주소를 맨 앞에 — 보통 이게 진짜 Wi-Fi 주소다
    primary = _primary_ip()
    if primary:
        if primary in found:
            found.remove(primary)
        found.insert(0, primary)
    return found


def _primary_ip() -> str:
    """실제로 패킷이 나가는 인터페이스의 주소. UDP 라 연결은 일어나지 않는다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def lan_url(ip: str, port: int) -> str:
    return f"http://{ip}:{port}"
