"""API 키 보관 — 정식판은 OS 키체인, 체험판은 서버 환경변수.

→ docs/22 설치판 아키텍처 6절 · docs/18 설치형 패키지 4절.

**사용자 키를 `os.environ` 에 넣지 않는다.** Streamlit 은 한 프로세스에 여러 세션이
붙으므로 환경변수에 사용자 값을 실으면 옆 세션에 샌다 (→ docs/17 3.5·3.7절에서
BYO 키의 선결 조건으로 잡았던 바로 그 문제). 그래서 읽기는 이 모듈로만 하고,
쓰기는 **Windows 자격 증명 관리자**(DPAPI 기반)로 간다.

읽는 순서
  1. OS 키체인 (정식판에서만 — 사용자가 화면에서 등록한 값)
  2. 환경변수 (체험판 서버 Secrets, 그리고 개발 중 `.env`)

키체인이 없는 환경(리눅스 헤드리스 = 체험판 호스팅)에서는 1번이 조용히 건너뛰어진다.
그래야 같은 코드가 두 에디션에서 다 돈다.
"""
import os

from . import edition

SERVICE = "ANFILT-ResearchAgent"

# 화면에서 관리하는 키 이름 (프로바이더 키는 config.PROVIDER_SPECS 에서 온다)
PIN = "MOBILE_PIN_HASH"
LICENSE = "LICENSE_KEY"

_kr = False          # False = 아직 안 봄, None = 못 씀, 그 외 = keyring 모듈


def _keyring():
    """쓸 수 있는 키체인 백엔드가 있으면 keyring 모듈, 없으면 None."""
    global _kr
    if _kr is not False:
        return _kr
    _kr = None
    if edition.is_installed():
        try:
            import keyring
            from keyring.backends.fail import Keyring as FailKeyring
            if not isinstance(keyring.get_keyring(), FailKeyring):
                _kr = keyring
        except Exception:
            # 백엔드가 없거나 미설치 — 환경변수로만 동작한다 (기능 저하 없음)
            _kr = None
    return _kr


def available() -> bool:
    """키체인에 저장할 수 있는 상태인가 (설정 화면이 안내 문구를 고를 때 쓴다)."""
    return _keyring() is not None


def get(name: str) -> str:
    kr = _keyring()
    if kr is not None:
        try:
            val = kr.get_password(SERVICE, name)
            if val:
                return val
        except Exception:
            pass
    return os.getenv(name) or ""


def set(name: str, value: str) -> None:
    """키체인에 저장. 저장할 수 없으면 조용히 실패하지 않고 알린다."""
    kr = _keyring()
    if kr is None:
        raise RuntimeError(
            "이 환경에서는 키를 안전하게 저장할 수 없습니다 "
            "(OS 자격 증명 저장소를 찾지 못했습니다)."
        )
    kr.set_password(SERVICE, name, value)


def delete(name: str) -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE, name)
    except Exception:
        pass          # 원래 없던 키를 지우는 건 오류가 아니다


def stored_in_keyring(name: str) -> bool:
    """이 값이 키체인에 있는가 (환경변수에서 온 것과 구분해 화면에 표시한다)."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        return bool(kr.get_password(SERVICE, name))
    except Exception:
        return False
