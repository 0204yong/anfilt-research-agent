"""체험판이 프롬프트 팩을 서버에서 받아 온다.

→ docs/20 라이선스와 복제 방지 · docs/26 팩을 저장소 밖으로.

**왜 필요한가.** 이 제품의 값어치는 코드가 아니라 프롬프트와 시드다. 그런데
체험판은 공개 저장소에서 배포되므로, 팩이 저장소에 있으면 누구나 받아 갈 수
있다 — 그러면 설치판의 라이선스 서버가 지킬 것이 없다.

정식판(설치판)은 **라이선스**로 팩을 받고, 체험판은 **공유 토큰**으로 받는다.
토큰은 Streamlit Secrets 에만 있고 저장소에는 없다.

정식판과 달리 서명을 확인하지 않는다. 서명은 *고객 PC 에 있는 팩*이 진짜인지
가리려는 장치인데, 여기서는 우리 서버가 TLS 로 직접 받는다 — 지킬 것이 다르다.

캐시를 두는 이유: 체험판 컨테이너는 자주 깨어난다. 매번 받아 오면 첫 화면이
그만큼 느려지고, 서버가 잠깐 흔들릴 때 체험판 전체가 멈춘다.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_ENV = "RA_TRIAL_TOKEN"
SERVER_ENV = "RA_LICENSE_SERVER"
CACHE_NAME = "trial_pack.json"
CACHE_TTL = 6 * 3600            # 이 시간이 지나면 다시 받아 본다
HTTP_TIMEOUT = 10.0

_memo: dict | None = None


class FetchError(RuntimeError):
    """받지 못했다 — 부르는 쪽이 캐시나 로컬 파일로 넘어갈 수 있게 한다."""


def token() -> str:
    return (os.environ.get(TOKEN_ENV) or "").strip()


def configured() -> bool:
    return bool(token())


def _server() -> str:
    from . import licensing
    return (os.environ.get(SERVER_ENV) or licensing.SERVER_DEFAULT).rstrip("/")


def _cache_path() -> Path:
    from . import appdirs
    return appdirs.data_dir() / CACHE_NAME


def _read_cache(max_age: float | None = CACHE_TTL) -> dict | None:
    p = _cache_path()
    try:
        if max_age is not None and time.time() - p.stat().st_mtime > max_age:
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "prompts" in data else None


def _write_cache(pack: dict) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)                       # 반쯤 쓰인 파일을 읽는 일이 없게
    except OSError:
        pass                                 # 캐시는 있으면 좋은 것 — 실패해도 진행한다


def _download() -> dict:
    body = json.dumps({"trial_token": token()}).encode("utf-8")
    req = urllib.request.Request(
        f"{_server()}/pack", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read()).get("reason", "")
        except Exception:                    # noqa: BLE001
            reason = ""
        if reason == "invalid":
            raise FetchError("체험판 토큰이 서버와 맞지 않습니다.") from None
        raise FetchError(f"구성요소를 받지 못했습니다 (HTTP {e.code}).") from None
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(f"구성요소 서버에 연결하지 못했습니다: {e}") from None
    except ValueError:
        raise FetchError("서버 응답을 읽지 못했습니다.") from None

    if not out.get("ok") or not out.get("pack_b64"):
        raise FetchError("서버가 구성요소를 주지 않았습니다.")
    try:
        pack = json.loads(base64.b64decode(out["pack_b64"]).decode("utf-8"))
    except (ValueError, TypeError):
        raise FetchError("받은 구성요소를 해석하지 못했습니다.") from None
    if not isinstance(pack, dict) or "prompts" not in pack:
        raise FetchError("받은 구성요소의 형식이 올바르지 않습니다.")
    return pack


def load() -> dict | None:
    """팩을 얻는다. 토큰이 없으면 None(이 경로를 쓰지 않는다는 뜻).

    순서: 메모리 → 신선한 캐시 → 서버 → **낡은 캐시**.

    마지막이 핵심이다. 서버가 잠깐 흔들려도 체험판은 낡은 팩으로 계속 돈다 —
    프롬프트가 며칠 낡는 것이 화면이 멈추는 것보다 훨씬 낫다.
    """
    global _memo
    if _memo is not None:
        return _memo
    if not configured():
        return None

    fresh = _read_cache()
    if fresh is not None:
        _memo = fresh
        return fresh

    try:
        pack = _download()
    except FetchError:
        stale = _read_cache(max_age=None)
        if stale is not None:
            _memo = stale
            return stale
        raise
    _write_cache(pack)
    _memo = pack
    return pack


def reset() -> None:
    global _memo
    _memo = None
