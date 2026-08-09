"""에디션 — 체험판(호스팅) / 정식판(설치) 구분과 기능 플래그.

→ docs/19 두 제품 구성과 자동 업데이트 2절 · docs/22 설치판 아키텍처 4절.

두 제품이 갈리는 지점은 셋뿐이다: `store`(저장소) · `keys`(키 출처) · `packs`(프롬프트).
화면 코드는 `edition.can("...")` 만 묻는다. `if EDITION == "trial"` 이 화면 곳곳에
흩어지기 시작하면 그때부터 두 제품이 서서히 갈라져 유지가 무너진다.

판정 순서
  1. `RA_EDITION` 환경변수 (개발·테스트용 강제 지정)
  2. 설치 배치의 표식 — `<설치폴더>/version.json` (빌드가 만든다)
  3. 없으면 trial (= 저장소에서 그냥 실행한 개발 상태 == 지금까지의 동작)
"""
import functools
import json
import os
from pathlib import Path

TRIAL = "trial"
INSTALLED = "installed"

# core/edition.py → core → app → <설치폴더>
_VERSION_JSON = Path(__file__).resolve().parents[2] / "version.json"

_FEATURES = {
    TRIAL: {
        "monitoring": False,     # 장기 기능이라 체험에 안 맞는다
        "full_mode": False,      # 라이트 고정 (비용)
        "vault_zip_export": True,   # 이주 경로 — 체험의 마지막 화면에 반드시 있어야 한다
        # ⚠️ 체험판의 사이드바 zip 업로드(서버 사본 교체, → docs/13)와는 다른 것이다.
        # 이 플래그는 **마법사의 "체험판 볼트 가져오기" 단계**만 가리킨다.
        "vault_import_wizard": False,
        "settings_page": False,
        "local_vault": False,
    },
    INSTALLED: {
        "monitoring": True,
        "full_mode": True,
        "vault_zip_export": True,
        "vault_import_wizard": True,
        "settings_page": True,
        "local_vault": True,
    },
}


@functools.lru_cache(maxsize=1)
def current() -> str:
    forced = (os.getenv("RA_EDITION") or "").strip().lower()
    if forced in (TRIAL, INSTALLED):
        return forced
    return INSTALLED if _VERSION_JSON.exists() else TRIAL


def is_installed() -> bool:
    return current() == INSTALLED


def can(feature: str) -> bool:
    """알 수 없는 기능 이름은 False — 오타가 기능을 조용히 켜지 않게."""
    return _FEATURES.get(current(), {}).get(feature, False)


def install_dir():
    """설치 폴더 (`version.json` 이 있는 곳). 개발 상태면 None.

    런처(`launcher.py`)와 런타임(`runtime\\pythonw.exe`)이 여기 있다 —
    앱이 자기를 재시작하려면 이 경로가 필요하다 (→ docs/23 5단계).
    """
    return _VERSION_JSON.parent if _VERSION_JSON.exists() else None


def _read_version_json() -> dict:
    """`utf-8-sig` 로 읽는다 — PowerShell 의 `Out-File -Encoding utf8` 이 **BOM을 붙이기**
    때문이다. utf-8 로 읽으면 `json.loads` 가 조용히 실패해 버전이 늘 'dev' 가 되고,
    6단계 자동 업데이트의 버전 비교가 통째로 망가진다 (실측으로 잡은 버그)."""
    try:
        return json.loads(_VERSION_JSON.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def app_version() -> str:
    return str(_read_version_json().get("app_version", "")) or "dev"


def runtime_version() -> str:
    """동봉된 파이썬 런타임 버전 — 자동 업데이트가 델타/전체를 가르는 기준
    (→ docs/19 두 제품 구성과 자동 업데이트 4.4절)."""
    return str(_read_version_json().get("runtime_version", "")) or "unknown"
