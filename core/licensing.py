"""라이선스 — 활성화 · 기기 바인딩 · 서명 검증 · 암호화 캐시.

→ docs/20 라이선스와 복제 방지 · docs/23 설치판 구현 계획 7단계. Streamlit 비의존.

## 이 설계의 요점

라이선스를 **"실행 허가"가 아니라 "프롬프트 팩을 인출할 권한"** 으로 정의한다.
설치판은 파이썬 소스가 그대로 담기므로 "허가 검사"는 한 줄 지우면 끝난다.
그래서 검사를 지워도 소용없게 만든다 — **지울 검사가 없는 대신, 조사를 시작할
부품 자체가 없다.**

    라이선스 키 + 기기 지문 ──▶ 활성화 서버 ──▶ 서명된 프롬프트 팩
                                                    │
                                          암호화 캐시 (이 PC 에서만 열림)

## 기기에 묶는 방법 — 서로 독립인 두 겹

  1. **암호화 키**를 `키체인 비밀 × 기기 지문` 에서 파생한다.
     폴더를 통째로 복사해도 다른 PC 에서는 **복호화가 안 된다.**
  2. **서명 대상에 기기 지문을 넣는다.** 복호화를 어떻게든 뚫어도, 남의 기기용
     팩은 **서명 검증에서 걸린다.**

한 겹이 뚫려도 나머지가 막는다. 그리고 둘 다 뚫려도 L2 가 남는다 —
진짜 팩이 없으면 조사가 시작되지 않는다.

## 정당한 구매자를 막지 않는다 (여기서 신뢰가 갈린다)

  - 인터넷이 끊겨도 캐시된 팩으로 **유효기간까지 정상 동작**
  - 갱신 실패는 **유예**로 처리한다 — 우리 서버 장애로 고객이 멈추면 안 된다
  - 만료돼도 **볼트 열람·내보내기는 계속 열려 있다.** 고객 데이터를 인질로
    잡지 않는다 (→ docs/22 8절 상태표)

## 서버로 나가는 것

**라이선스 키와 기기 지문 해시뿐이다.** 조사 주제·볼트 내용·고객사 자료는
**일절 전송되지 않는다** (→ docs/18 의 최대 판매 포인트를 훼손하지 않는다).
"""
import base64
import hashlib
import json
import os
import platform
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import appdirs, edition, keys

# ---------------------------------------------------------------- 설정

SERVER_DEFAULT = "https://koorjatscpkvomjosenc.supabase.co/functions/v1/license"
SERVER_ENV = "RA_LICENSE_SERVER"

# 릴리스 빌드가 채운다 (packaging/make_signing_key.py 로 만든 공개키).
# 비어 있으면 서명 검증을 **요구할 수 없다** — 그 상태로는 팩을 신뢰하지 않는다.
PUBKEY_B64 = ""
PUBKEY_ENV = "RA_LICENSE_PUBKEY"

LICENSE_KEY_NAME = "RA_LICENSE_KEY"      # 키체인
CACHE_SECRET_NAME = "RA_CACHE_SECRET"    # 키체인 — 캐시 암호화 키의 씨앗

CACHE_FILE = "license.pack"
HTTP_TIMEOUT = 12.0

# 기기 지문 강제 지정 — **시험용**. 이걸로 바인딩을 우회할 수는 없다:
# 그 지문으로 서명된 팩이 있어야 하고, 그건 유효한 라이선스로만 받는다.
DEVICE_FP_ENV = "RA_DEVICE_FP"

_pack_memo = None                        # 복호화·검증 결과를 프로세스 안에서 재사용


class LicenseError(RuntimeError):
    """화면이 그대로 보여 줄 수 있는 문구를 담는다."""


# ---------------------------------------------------------------- 기기 지문


def device_fingerprint() -> str:
    """이 PC 를 가리키는 해시. **원본 식별자는 서버로 보내지 않는다.**

    Windows Machine GUID(레지스트리) + 시스템 드라이브 볼륨 시리얼을 섞어
    SHA-256 한다. 둘 다 못 읽으면 hostname 으로 떨어진다(약하지만 없는 것보다 낫다).
    """
    forced = (os.getenv(DEVICE_FP_ENV) or "").strip()
    if forced:
        return hashlib.sha256(forced.encode("utf-8")).hexdigest()
    parts = [_machine_guid(), _volume_serial(), platform.node() or ""]
    raw = "|".join(p for p in parts if p) or "unknown"
    return hashlib.sha256(("ra1:" + raw).encode("utf-8")).hexdigest()


def _machine_guid() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as k:
            return str(winreg.QueryValueEx(k, "MachineGuid")[0])
    except (ImportError, OSError):
        return ""


def _volume_serial() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        root = (os.getenv("SystemDrive") or "C:") + "\\"
        serial = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), None, 0, ctypes.byref(serial),
            None, None, None, 0,
        )
        return str(serial.value) if ok else ""
    except Exception:                       # noqa: BLE001
        return ""


def device_label() -> str:
    """기기 해제 화면에서 사람이 알아볼 이름. 서버에 그대로 저장된다."""
    try:
        return (socket.gethostname() or "PC")[:60]
    except OSError:
        return "PC"


# ---------------------------------------------------------------- 라이선스 키


def license_key() -> str:
    return keys.get(LICENSE_KEY_NAME)


def masked_key() -> str:
    k = license_key()
    return f"…{k[-4:]}" if len(k) >= 4 else ("" if not k else "…")


def _set_license_key(value: str) -> None:
    keys.set(LICENSE_KEY_NAME, value.strip())


def normalize_key(text: str) -> str:
    """붙여넣기 흔들림을 흡수한다 (공백·소문자·구분자 없이 입력)."""
    raw = "".join(ch for ch in str(text or "").upper() if ch.isalnum())
    if not raw:
        return ""
    if raw.startswith("ANF"):
        raw = raw[3:]
    groups = [raw[i:i + 4] for i in range(0, len(raw), 4)]
    return "ANF-" + "-".join(groups)


# ---------------------------------------------------------------- 암호화 캐시


def _cache_path():
    return appdirs.data_dir() / CACHE_FILE


def _cache_secret() -> bytes:
    """캐시 암호화의 씨앗. 키체인에 두어 **폴더 복사로는 따라가지 않게** 한다."""
    val = keys.get(CACHE_SECRET_NAME)
    if not val:
        val = base64.b64encode(os.urandom(32)).decode("ascii")
        try:
            keys.set(CACHE_SECRET_NAME, val)
        except Exception:                   # noqa: BLE001 — 키체인이 없는 환경
            # 폴백: 기기 지문만으로 파생한다. 약해지지만 동작은 한다.
            return b""
    return base64.b64decode(val)


def _aes_key(fp: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=fp.encode("utf-8"),
        info=b"ra-license-cache-v1",
    ).derive(_cache_secret() or fp.encode("utf-8"))


def _write_cache(payload: dict) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    fp = device_fingerprint()
    nonce = os.urandom(12)
    blob = AESGCM(_aes_key(fp)).encrypt(
        nonce, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None
    )
    tmp = _cache_path().with_suffix(".tmp")
    tmp.write_bytes(nonce + blob)
    os.replace(tmp, _cache_path())
    global _pack_memo
    _pack_memo = None


def _read_cache() -> dict:
    """복호화한 캐시. 열 수 없으면 {} (다른 PC 로 복사된 경우 등)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        raw = _cache_path().read_bytes()
    except OSError:
        return {}
    if len(raw) < 13:
        return {}
    try:
        data = AESGCM(_aes_key(device_fingerprint())).decrypt(raw[:12], raw[12:], None)
        return json.loads(data.decode("utf-8"))
    except Exception:                       # noqa: BLE001 — 위조·손상·다른 PC
        return {}


def clear() -> None:
    """이 PC 에서 라이선스 흔적을 지운다 (해제·키 교체)."""
    global _pack_memo
    _pack_memo = None
    try:
        _cache_path().unlink()
    except OSError:
        pass
    for name in (LICENSE_KEY_NAME,):
        try:
            keys.delete(name)
        except Exception:                   # noqa: BLE001
            pass


# ---------------------------------------------------------------- 서명


def _pubkey_b64() -> str:
    env = (os.getenv(PUBKEY_ENV) or "").strip()
    if env and _test_mode():
        # 시험용 우회는 **로컬 서버를 쓸 때만** 허용한다. 실 서버를 보면서
        # 공개키만 바꿔치기하는 경로를 열어 두지 않는다.
        return env
    return PUBKEY_B64.strip()


def _test_mode() -> bool:
    server = (os.getenv(SERVER_ENV) or "").strip().lower()
    return server.startswith("http://127.0.0.1") or server.startswith("http://localhost")


def signature_message(pack_json: str, pack_version: str, expires_at: str,
                      fp: str) -> bytes:
    """서명 대상. **기기 지문이 들어간다** — 남의 기기용 팩은 검증에서 걸린다."""
    digest = hashlib.sha256(pack_json.encode("utf-8")).hexdigest()
    return f"{digest}|{pack_version}|{expires_at}|{fp}".encode("utf-8")


def verify_signature(pack_json: str, pack_version: str, expires_at: str,
                     fp: str, signature_b64: str) -> bool:
    pub = _pubkey_b64()
    if not pub:
        # 공개키가 없으면 **검증할 수 없다** → 신뢰하지 않는다 (fail-closed).
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub))
        key.verify(base64.b64decode(signature_b64),
                   signature_message(pack_json, pack_version, expires_at, fp))
        return True
    except (InvalidSignature, ValueError, TypeError, Exception):  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 서버


def server_url() -> str:
    return (os.getenv(SERVER_ENV) or "").strip() or SERVER_DEFAULT


def _post(path: str, body: dict) -> dict:
    url = server_url().rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read(4 << 20).decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:                   # noqa: BLE001
            raise LicenseError(f"활성화 서버 오류 ({e.code})") from None
    except (urllib.error.URLError, OSError) as e:
        raise LicenseError(f"활성화 서버에 연결하지 못했습니다: {e}") from None


_REASONS = {
    "invalid": "라이선스 키를 찾을 수 없습니다. 키를 다시 확인해 주세요.",
    "revoked": "해지된 라이선스입니다. 담당자에게 문의해 주세요.",
    "suspended": "일시 정지된 라이선스입니다. 담당자에게 문의해 주세요.",
    "expired": "라이선스 기간이 만료되었습니다. 갱신 후 다시 시도해 주세요.",
    "seats_full": "허용된 기기 수를 모두 사용 중입니다. "
                  "쓰지 않는 PC 를 먼저 해제해 주세요.",
    "not_activated": "이 PC 는 아직 활성화되지 않았습니다.",
}


def _store(result: dict, key_used: str) -> dict:
    """서버 응답을 검증하고 캐시에 넣는다.

    팩은 **서버가 보낸 바이트 그대로**(base64) 받아 그 바이트에 서명을 맞춘다.
    JSON 을 다시 직렬화해서 맞추려 들면 Deno 와 파이썬의 사소한 표기 차이
    하나로 정당한 고객의 활성화가 통째로 실패한다.
    """
    fp = device_fingerprint()
    pack_version = str(result.get("pack_version") or "")
    expires_at = str(result.get("expires_at") or "")
    signature = str(result.get("signature") or "")
    try:
        pack_json = base64.b64decode(result.get("pack_b64") or "").decode("utf-8")
        pack = json.loads(pack_json)
    except Exception:                       # noqa: BLE001
        raise LicenseError("활성화 응답의 구성요소를 읽지 못했습니다.") from None
    if not isinstance(pack, dict) or not pack_version or not expires_at:
        raise LicenseError("활성화 응답이 올바르지 않습니다.")

    if not verify_signature(pack_json, pack_version, expires_at, fp, signature):
        # 서명이 안 맞으면 **쓰지 않는다.** 위조 팩이거나 남의 기기용이다.
        raise LicenseError(
            "받은 구성요소의 서명을 확인하지 못했습니다 — 적용하지 않았습니다."
        )

    payload = {
        "pack": pack,
        "pack_json": pack_json,
        "pack_version": pack_version,
        "expires_at": expires_at,
        "refresh_after": str(result.get("refresh_after") or ""),
        "signature": signature,
        "device_fp": fp,
        "seat_no": result.get("seat_no"),
        "seats": result.get("seats"),
        "used": result.get("used"),
        "issued_at": _now_iso(),
        "key_masked": f"…{key_used[-4:]}" if len(key_used) >= 4 else "",
    }
    _write_cache(payload)
    return payload


def activate(raw_key: str) -> dict:
    """라이선스 키로 이 PC 를 활성화하고 팩을 받아 온다."""
    key = normalize_key(raw_key)
    if not key:
        raise LicenseError("라이선스 키를 입력해 주세요.")
    result = _post("activate", {
        "license_key": key,
        "device_fp": device_fingerprint(),
        "device_label": device_label(),
        "app_version": edition.app_version(),
    })
    if not result.get("ok"):
        raise LicenseError(
            result.get("message")
            or _REASONS.get(result.get("reason"), "활성화하지 못했습니다.")
        )
    payload = _store(result, key)
    _set_license_key(key)
    return payload


def refresh(force: bool = False) -> dict:
    """갱신. **실패는 조용히 넘어간다** — 유예 처리가 여기서 시작된다."""
    key = license_key()
    if not key:
        return {"ok": False, "error": "not_activated"}
    cached = _read_cache()
    if not force and cached:
        after = _parse(cached.get("refresh_after"))
        if after and _now() < after:
            return {"ok": True, "skipped": True}
    try:
        result = _post("refresh", {
            "license_key": key,
            "device_fp": device_fingerprint(),
            "app_version": edition.app_version(),
        })
    except LicenseError as e:
        return {"ok": False, "error": str(e)}
    if not result.get("ok"):
        return {"ok": False, "error": result.get("reason") or "refused",
                "message": result.get("message")}
    try:
        _store(result, key)
    except LicenseError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def deactivate() -> dict:
    """좌석을 반납하고 이 PC 의 캐시를 지운다 (PC 교체·재설치).

    **서버가 안 되어도 로컬은 지운다.** 여기서 막히면 사용자는 "해제도 안 되고
    쓸 수도 없는" 상태에 갇힌다. 좌석은 담당자가 관리 화면에서 회수할 수 있다.
    """
    key = license_key()
    out = {"server": False}
    if key:
        try:
            r = _post("deactivate", {
                "license_key": key, "device_fp": device_fingerprint(),
            })
            out["server"] = bool(r.get("ok"))
            out["message"] = r.get("message")
        except LicenseError as e:
            out["message"] = str(e)
    clear()
    return out


# ---------------------------------------------------------------- 상태


def _now():
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse(text):
    if not text:
        return None
    try:
        s = str(text).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def status() -> dict:
    """지금 라이선스 상태.

    state
      ``none``       활성화 전 (또는 다른 PC 로 복사돼 캐시를 열 수 없음)
      ``active``     정상
      ``grace``      갱신에 실패했지만 유효기간은 남음 — **계속 동작한다**
      ``expired``    유효기간 지남 — 조사·비서 잠금 (볼트는 계속 열림)
    """
    cached = _read_cache()
    if not cached:
        has_file = _cache_path().exists()
        return {
            "state": "none",
            "message": (
                "이 PC 에서는 저장된 라이선스를 열 수 없습니다 — 다시 활성화해 주세요."
                if has_file else "아직 활성화되지 않았습니다."
            ),
            "copied": has_file,          # 파일은 있는데 못 연다 = 다른 PC 로 복사됨
            "key_masked": masked_key(),
        }

    expires = _parse(cached.get("expires_at"))
    after = _parse(cached.get("refresh_after"))
    now = _now()
    days_left = int((expires - now).total_seconds() // 86400) if expires else None

    if expires and now >= expires:
        state, message = "expired", "라이선스 갱신이 필요합니다."
    elif after and now >= after:
        state, message = "grace", "갱신을 시도하는 중입니다 — 사용에는 지장이 없습니다."
    else:
        state, message = "active", "정상"

    return {
        "state": state, "message": message, "days_left": days_left,
        "expires_at": cached.get("expires_at"),
        "pack_version": cached.get("pack_version"),
        "seat_no": cached.get("seat_no"), "seats": cached.get("seats"),
        "key_masked": cached.get("key_masked") or masked_key(),
        "device_fp_short": cached.get("device_fp", "")[:12],
        "copied": False,
    }


def can_run() -> bool:
    """조사·비서를 돌려도 되는가. **볼트 열람과는 무관하다.**"""
    return status()["state"] in ("active", "grace")


def pack_or_none():
    """캐시된 프롬프트 팩. 없거나 만료·검증 실패면 None.

    서명은 **읽을 때마다** 다시 확인한다 — 캐시 파일을 나중에 바꿔치기하는
    경로를 막는다. 결과는 프로세스 안에서만 재사용한다.
    """
    global _pack_memo
    if _pack_memo is not None:
        return _pack_memo.get("pack")

    cached = _read_cache()
    if not cached:
        return None
    expires = _parse(cached.get("expires_at"))
    if expires and _now() >= expires:
        return None
    if cached.get("device_fp") != device_fingerprint():
        return None
    if not verify_signature(
        cached.get("pack_json") or "", cached.get("pack_version") or "",
        cached.get("expires_at") or "", device_fingerprint(),
        cached.get("signature") or "",
    ):
        return None
    _pack_memo = cached
    return cached.get("pack")


def maybe_refresh_background() -> None:
    """화면이 뜰 때 한 번 부르는 조용한 갱신 — 실패해도 아무 일도 없다."""
    if not edition.is_installed() or not license_key():
        return
    marker = appdirs.data_dir() / "license_refresh.ts"
    try:
        last = float(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = 0.0
    if time.time() - last < 3600:
        return
    try:
        marker.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass
    refresh()


def offline_request_blob() -> str:
    """폐쇄망 고객용 — 담당자에게 보낼 활성화 요청 문자열.

    기기 지문과 앱 버전만 담는다. 담당자가 이 값으로 **그 기기 전용 서명 팩**을
    만들어 회신한다 (→ docs/20 오프라인 활성화).
    """
    return base64.b64encode(json.dumps({
        "device_fp": device_fingerprint(),
        "device_label": device_label(),
        "app_version": edition.app_version(),
        "requested_at": _now_iso(),
    }).encode("utf-8")).decode("ascii")


def apply_offline_blob(blob: str) -> dict:
    """담당자가 회신한 활성화 파일을 적용한다 (서명 검증은 동일하게 통과해야 한다)."""
    try:
        result = json.loads(base64.b64decode(blob.strip()).decode("utf-8"))
    except Exception:                       # noqa: BLE001
        raise LicenseError("활성화 파일 형식이 올바르지 않습니다.") from None
    if not isinstance(result, dict) or not result.get("pack_b64"):
        raise LicenseError("활성화 파일에 구성요소가 없습니다.")
    payload = _store(result, str(result.get("license_key") or ""))
    if result.get("license_key"):
        _set_license_key(str(result["license_key"]))
    return payload
