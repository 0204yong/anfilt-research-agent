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
    """팩을 얻는 순서 — **서버에서 받은 것이 먼저다.**

      1. 정식판: 라이선스 캐시의 서명된 팩 (→ core/licensing.py)
      2. 체험판: 공유 토큰으로 인출한 팩 (→ core/packfetch.py)
      3. 동봉된 로컬 파일 (개발 중에만. 체험판·릴리스 빌드에서는 없다)
      4. 없으면 `PackError` — 화면은 "재설치" 가 아니라 **"활성화"** 를 안내한다

    2번이 생긴 이유: 체험판은 공개 저장소에서 배포된다. 팩을 저장소에 두면
    누구나 받아 가고, 그러면 설치판의 라이선스가 지킬 것이 없다
    (→ docs/26 팩을 저장소 밖으로).

    3번은 개발 편의로만 남긴다 — 토큰 없이 로컬에서 돌릴 수 있어야 한다.
    """
    global _cache
    if _cache is not None:
        return _cache

    licensed = _licensed_pack()
    if licensed is not None:
        _cache = licensed
        return licensed

    hosted = _hosted_pack()
    if hosted is not None:
        _cache = hosted
        return hosted

    try:
        data = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackError(_missing_message()) from None
    except (OSError, ValueError) as e:
        raise PackError(f"프롬프트 팩을 읽을 수 없습니다: {e}") from None
    if not isinstance(data, dict) or "prompts" not in data:
        raise PackError("프롬프트 팩 형식이 올바르지 않습니다.")
    _cache = data
    return data


def _licensed_pack():
    """정식판에서만 라이선스를 본다. 실패는 조용히 다음 순서로 넘긴다."""
    try:
        from . import edition
        if not edition.is_installed():
            return None
        from . import licensing
        pack = licensing.pack_or_none()
    except Exception:                       # noqa: BLE001 — 라이선스 고장이 앱을 막지 않는다
        return None
    if isinstance(pack, dict) and "prompts" in pack:
        return pack
    return None


def _hosted_pack():
    """체험판이 서버에서 받아 온 팩. 토큰이 없으면 이 경로를 쓰지 않는다.

    정식판에서는 보지 않는다 — 거기서는 라이선스가 유일한 자격이어야 하고,
    공유 토큰이 그 옆문이 되면 곤란하다.

    받지 못하면 **여기서 터뜨린다.** 조용히 로컬 파일로 넘어가면, 토큰을 잘못
    넣은 체험판이 "그냥 되는 것처럼" 보이다가 저장소에서 팩을 빼는 순간
    죽는다 — 그때는 원인이 한참 전 일이 된다.
    """
    try:
        from . import edition
        if edition.is_installed():
            return None
        from . import packfetch
        if not packfetch.configured():
            return None
    except Exception:                       # noqa: BLE001
        return None
    try:
        return packfetch.load()
    except packfetch.FetchError as e:
        raise PackError(str(e)) from None


def _missing_message() -> str:
    try:
        from . import edition
        if edition.is_installed():
            return (
                "프로그램 구성요소가 아직 준비되지 않았습니다 — "
                "**⚙️ 설정에서 라이선스를 활성화**해 주세요."
            )
    except Exception:                       # noqa: BLE001
        pass
    return (
        "프롬프트 팩을 찾을 수 없습니다. 프로그램이 손상되었을 수 있습니다 — "
        "다시 설치하거나 업데이트를 실행하세요."
    )


def reload() -> None:
    """팩 갱신(업데이트·재인출) 후 호출."""
    global _cache
    _cache = None
    try:
        from . import packfetch
        packfetch.reset()                   # 여기 남겨 두면 갱신해도 옛 팩이 돈다
    except Exception:                       # noqa: BLE001
        pass


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
