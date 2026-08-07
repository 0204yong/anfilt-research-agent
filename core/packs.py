"""프롬프트 팩 — 이 제품의 값어치(프롬프트·스키마·구성표)를 코드 밖에 둔다.

→ docs/22 설치판 아키텍처 7절 · docs/20 라이선스와 복제 방지.

**왜 코드에서 뺐나**: 설치판은 파이썬 소스가 그대로 담긴다. 라이선스 검사를
코드 안에 두면 한 줄 지우는 것으로 끝난다. 그래서 라이선스를 "실행 허가"가 아니라
**"이 팩을 인출할 권한"** 으로 정의한다 — 팩이 없는 복제본은 지울 검사가 없는 대신
**조사를 시작할 부품 자체가 없다.**

부수 효과가 하나 더 있다: 프롬프트를 **앱 재배포 없이** 갱신할 수 있다. ESG 규제는
매년 바뀌므로(CSRD·ESRS·K-ESG) 이건 라이선스와 무관하게도 값이 있다.

2단계에서는 **서명 없는 로컬 파일**(`core/prompts/pack.json`)이다. 7단계에서
서명 검증·서버 인출·암호화 캐시로 승격하되, **호출부는 바뀌지 않는다** —
`packs.get()` / `packs.render()` / `packs.schema()` / `packs.conf()` 넷이 전부다.

조립 로직은 코드에 남는다. 팩은 **문구**를 갖고, 코드는 **어디에 무엇을 끼울지**를
안다 (→ docs/02 파이프라인 설계가 검증한 조립을 보존하려고).
"""
import json
from pathlib import Path
from string import Template

PACK_PATH = Path(__file__).resolve().parent / "prompts" / "pack.json"

_cache = None


class PackError(RuntimeError):
    """팩이 없거나 손상됐다 — 조사·비서는 막고 볼트 열람은 계속 허용한다.

    (→ docs/22 8절 상태표. 사용자에게는 스택트레이스가 아니라 안내가 가야 한다.)
    """


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    try:
        data = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackError(
            "프롬프트 팩을 찾을 수 없습니다. 프로그램이 손상되었을 수 있습니다 — "
            "다시 설치하거나 업데이트를 실행하세요."
        ) from None
    except (OSError, ValueError) as e:
        raise PackError(f"프롬프트 팩을 읽을 수 없습니다: {e}") from None
    if not isinstance(data, dict) or "prompts" not in data:
        raise PackError("프롬프트 팩 형식이 올바르지 않습니다.")
    _cache = data
    return data


def reload() -> None:
    """팩 갱신(업데이트·재인출) 후 호출."""
    global _cache
    _cache = None


def version() -> str:
    try:
        return str(_load().get("pack_version", "")) or "unknown"
    except PackError:
        return "없음"


def is_available() -> bool:
    try:
        _load()
        return True
    except PackError:
        return False


def _fetch(bucket: str, key: str):
    data = _load()
    section = data.get(bucket) or {}
    if key not in section:
        # 키 오타가 조용히 빈 프롬프트가 되면 LLM이 엉뚱한 답을 낸다 — 즉시 터뜨린다
        raise PackError(f"프롬프트 팩에 '{bucket}.{key}' 가 없습니다 (팩 버전 {version()}).")
    return section[key]


def get(key: str) -> str:
    """치환자 없는 문구 그대로."""
    return str(_fetch("prompts", key))


def render(key: str, **vars) -> str:
    """`$name` 치환자를 채워 완성한다.

    `substitute`(safe_substitute 아님)를 쓴다 — 채우지 못한 치환자가 있으면
    프롬프트에 `$name` 이 그대로 남는 대신 **KeyError로 즉시 드러난다.**
    """
    try:
        return Template(get(key)).substitute(**vars)
    except KeyError as e:
        raise PackError(f"프롬프트 '{key}' 의 치환자 {e} 가 채워지지 않았습니다.") from None


def schema(key: str) -> dict:
    return _fetch("schemas", key)


def conf(key: str):
    return _fetch("config", key)


def seed() -> dict:
    """시드 온톨로지 {경로: 내용}.

    지금은 repo 의 `vault_seed/` 를 읽는다. 7단계에서 팩 안으로 들어오지만
    호출부는 이 함수만 보므로 바뀌지 않는다.
    """
    data = _load()
    if data.get("seed"):
        return dict(data["seed"])
    from .vault_sync import load_seed_files
    return load_seed_files()
