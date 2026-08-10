"""자동 업데이트 클라이언트 — 확인 · 판정 · 내려받기.

→ docs/19 두 제품 구성과 자동 업데이트 4절 · docs/23 설치판 구현 계획 6단계.

Streamlit 비의존. **교체는 여기서 하지 않는다** — 실행 중인 앱이 자기 소스를
갈아치우면 import 된 모듈과 디스크가 어긋난다. 교체는 별도 프로세스
(`packaging/updater.py`)가 하고, 이 모듈은 거기까지 데려다 준다.

## 판정 규칙

    앱 버전 < min_supported   → 잠근다 (옛 클라이언트는 조용히 고장 난다)
    런타임 버전이 같다        → 델타 (app zip, 수 MB)
    런타임 버전이 다르다      → 전체 인스톨러 (수백 MB)

## 안전 규칙 넷

  1. **https 만** 받는다 (로컬 테스트 주소는 예외)
  2. **sha256 이 맞아야** 적용한다 — 틀리면 파일을 지우고 중단
  3. 받는 동안 `app/` 을 건드리지 않는다 — 중간에 죽어도 앱은 멀쩡하다
  4. **조사 중에는 재시작하지 않는다** — 20분짜리 작업이 날아간다
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import appdirs, edition, settings

MANIFEST_DEFAULT = "https://anfilt-homepage.netlify.app/releases/latest.json"
MANIFEST_ENV = "RA_UPDATE_MANIFEST"          # 개발·테스트용 우회

CHECK_INTERVAL = 6 * 3600                    # 기동마다 조르지 않는다
BUSY_STALE_SECONDS = 6 * 3600                # 조사 중 표시가 이만큼 낡으면 무시
DOWNLOAD_CHUNK = 256 * 1024

# 로컬 http 는 테스트에서만 허용한다 (자동 업데이트가 평문으로 나가면 안 된다)
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class UpdateError(RuntimeError):
    """확인·내려받기 실패 — 화면이 그대로 보여 줄 수 있는 문구를 담는다."""


# ---------------------------------------------------------------- 버전


def parse_version(text) -> tuple:
    """'1.4.2' → (1, 4, 2). 숫자가 아닌 꼬리는 버린다 ('1.4.2-rc1' → (1,4,2))."""
    parts = []
    for chunk in str(text or "").split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def compare(a, b) -> int:
    va, vb = parse_version(a), parse_version(b)
    return (va > vb) - (va < vb)


# ---------------------------------------------------------------- 매니페스트


def manifest_url() -> str:
    env = (os.getenv(MANIFEST_ENV) or "").strip()
    if env:
        return env
    conf = ((settings.load().get("update") or {}).get("manifest_url") or "").strip()
    return conf or MANIFEST_DEFAULT


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and (parsed.hostname or "") in _LOCAL_HOSTS:
        return                                # 로컬 테스트용
    raise UpdateError(f"안전하지 않은 주소라 받지 않았습니다: {url}")


def fetch_manifest(url: str = None, timeout: float = 8.0) -> dict:
    url = url or manifest_url()
    _check_url(url)
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1 << 20)          # 매니페스트는 작다 — 상한을 둔다
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"업데이트 서버에 연결하지 못했습니다: {e}") from None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError as e:
        raise UpdateError(f"업데이트 정보를 읽지 못했습니다: {e}") from None
    if not isinstance(data, dict) or not data.get("version"):
        raise UpdateError("업데이트 정보 형식이 올바르지 않습니다.")
    return data


# ---------------------------------------------------------------- 판정


def decide(manifest: dict, cur_app: str, cur_runtime: str) -> dict:
    """순수 함수 — 네트워크를 타지 않아 시험하기 쉽다.

    반환: {"available", "version", "kind", "entry", "critical", "blocked", "reason"}
    """
    new = str(manifest.get("version") or "")
    out = {
        "available": False, "version": new, "kind": None, "entry": None,
        "critical": bool(manifest.get("critical")),
        "blocked": False, "notes_url": manifest.get("notes_url") or "",
        "reason": "",
    }

    min_sup = manifest.get("min_supported")
    if min_sup and compare(cur_app, min_sup) < 0:
        # 프롬프트 스키마·모델 ID 가 바뀌면 옛 버전은 **조용히** 고장 난다
        # (실제로 gemini-2.5-flash 가 404 를 내던 일이 있었다 → docs/19 4.5)
        out["blocked"] = True
        out["reason"] = f"이 버전({cur_app})은 더 이상 지원되지 않습니다."

    if compare(new, cur_app) <= 0:
        out["reason"] = out["reason"] or "최신 버전입니다."
        return out

    same_runtime = str(manifest.get("runtime_version") or "") == str(cur_runtime or "")
    delta, installer = manifest.get("delta"), manifest.get("installer")

    if same_runtime and _valid_entry(delta):
        out.update(available=True, kind="delta", entry=delta)
    elif _valid_entry(installer):
        out.update(available=True, kind="installer", entry=installer)
        out["reason"] = (
            "파이썬 구성요소가 바뀌어 전체 설치 파일을 받습니다."
            if not same_runtime else "델타가 없어 전체 설치 파일을 받습니다."
        )
    else:
        out["reason"] = "새 버전이 있지만 받을 파일 정보가 없습니다."
    return out


def _valid_entry(entry) -> bool:
    return bool(
        isinstance(entry, dict) and entry.get("url") and entry.get("sha256")
    )


def check(force: bool = False) -> dict:
    """확인 → 판정. **실패해도 예외를 내지 않는다** — 앱을 막으면 안 된다.

    반환에 `error` 가 있으면 조용히 넘어가면 된다 (오프라인·서버 점검 등).
    """
    cfg = settings.load()
    upd = cfg.setdefault("update", {})
    last = float(upd.get("last_check_ts") or 0)

    # **판정이 아니라 매니페스트를 캐시한다.** 판정을 캐시하면 업데이트를 마친
    # 뒤에도 "새 버전이 있습니다"가 최대 6시간 남는다 — 방금 올린 버전을
    # 다시 권하는 꼴이다. 매니페스트를 두고 매번 다시 판정하면 그럴 일이 없다.
    if not force and time.time() - last < CHECK_INTERVAL:
        cached = upd.get("last_manifest")
        if isinstance(cached, dict):
            return decide(cached, edition.app_version(), edition.runtime_version())

    try:
        manifest = fetch_manifest()
    except UpdateError as e:
        return {"available": False, "blocked": False, "error": str(e)}

    upd["last_check_ts"] = time.time()
    upd["last_check"] = time.strftime("%Y-%m-%d %H:%M")
    upd["last_manifest"] = manifest
    upd.pop("last_result", None)              # 옛 형식 정리
    settings.save(cfg)
    return decide(manifest, edition.app_version(), edition.runtime_version())


def fresh_for_apply() -> dict:
    """적용 직전에 **매니페스트를 다시 받아** 판정한다.

    `check()` 의 결과는 6시간 캐시라 알림에는 좋지만 내려받기에는 위험하다 —
    캐시에 든 url·sha256 이 낡았으면 멀쩡한 파일이 "위변조"로 보인다.
    실제로 그렇게 헛경보가 났다(실측). **검증에 쓰는 해시는 늘 방금 받은 것**이어야
    한다. 오래된 항목으로 받았다가 sha 가 맞는 경우가 더 나쁘다 — 그건 옛 버전을
    새 버전이라고 믿고 설치하는 것이다.
    """
    manifest = fetch_manifest()               # UpdateError 는 호출부가 받는다
    return decide(manifest, edition.app_version(), edition.runtime_version())


def skipped(version: str) -> bool:
    return str((settings.load().get("update") or {}).get("skip_version") or "") == str(version)


def skip(version: str) -> None:
    cfg = settings.load()
    cfg.setdefault("update", {})["skip_version"] = str(version)
    settings.save(cfg)


def auto_enabled() -> bool:
    upd = settings.load().get("update") or {}
    return bool(upd.get("auto", True))


def set_auto(on: bool) -> None:
    cfg = settings.load()
    cfg.setdefault("update", {})["auto"] = bool(on)
    settings.save(cfg)


# ---------------------------------------------------------------- 내려받기


def download(entry: dict, dest_dir=None, progress=None) -> Path:
    """받고 **sha256 을 검증한 뒤에만** 경로를 돌려준다.

    받는 위치는 `app/` 바깥의 임시 폴더다 — 받는 도중 프로그램이 죽어도
    설치된 앱은 손끝 하나 닿지 않는다 (DoD 2).
    """
    url, want = entry["url"], str(entry["sha256"]).lower().strip()
    _check_url(url)
    dest_dir = Path(dest_dir or (appdirs.data_dir() / "updates"))
    dest_dir.mkdir(parents=True, exist_ok=True)

    name = Path(urlparse(url).path).name or "update.bin"
    part = dest_dir / (name + ".part")
    final = dest_dir / name
    digest = hashlib.sha256()
    got = 0
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or entry.get("size") or 0)
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    if progress and total:
                        progress(min(1.0, got / total))
    except (urllib.error.URLError, OSError) as e:
        _unlink(part)
        raise UpdateError(f"내려받지 못했습니다: {e}") from None

    if digest.hexdigest() != want:
        # 위변조·전송 손상 어느 쪽이든 **적용하지 않는다**. 파일도 남기지 않는다.
        _unlink(part)
        raise UpdateError(
            "받은 파일이 손상되었거나 위변조되었습니다(sha256 불일치) — "
            "적용하지 않았습니다."
        )
    _unlink(final)
    os.replace(part, final)
    return final


def _unlink(p: Path) -> None:
    try:
        Path(p).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- 조사 중 표시


def _busy_file() -> Path:
    return appdirs.data_dir() / "busy.json"


def mark_busy(what: str = "조사") -> None:
    try:
        _busy_file().write_text(
            json.dumps({"what": what, "pid": os.getpid(), "since": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass


def clear_busy() -> None:
    _unlink(_busy_file())


def busy() -> dict:
    """지금 재시작하면 안 되는 작업이 도는가. 아니면 빈 dict.

    앱이 중간에 죽으면 표시가 남는다 — 그래서 **낡으면 무시한다.**
    영원히 업데이트가 막히는 것이 더 나쁘다.
    """
    try:
        data = json.loads(_busy_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if time.time() - float(data.get("since") or 0) > BUSY_STALE_SECONDS:
        clear_busy()
        return {}
    return data


class busy_guard:
    """`with updates.busy_guard("조사"):` — 끝나면 반드시 지운다."""

    def __init__(self, what: str = "조사"):
        self.what = what

    def __enter__(self):
        mark_busy(self.what)
        return self

    def __exit__(self, *exc):
        clear_busy()
        return False


# ---------------------------------------------------------------- 적용


def updater_cmd(payload: Path, kind: str = "delta") -> list:
    """교체 프로세스를 띄울 명령. 설치판이 아니거나 파일이 없으면 None."""
    base = edition.install_dir()
    if not base:
        return None
    pythonw = Path(base) / "runtime" / "pythonw.exe"
    updater = Path(base) / "updater.py"
    if not (pythonw.exists() and updater.exists()):
        return None
    return [str(pythonw), str(updater), "--payload", str(payload),
            "--kind", kind, "--install-dir", str(base)]
