"""7단계 회귀 — 라이선스·기기 바인딩·유예.

실행:  python tests/test_licensing.py

지키려는 성질은 둘이고, 서로 반대 방향이다.

  A. **구매자가 아니면 조사가 시작되지 않는다** (복사·위조·좌석 초과·만료)
  B. **구매자는 어떤 경우에도 막히지 않는다** (오프라인·서버 장애·갱신 실패)

키체인은 메모리로 바꿔 끼우고, 활성화 서버는 로컬 대역을 띄운다.
개발 PC 의 자격 증명 저장소에 시험용 라이선스를 남기지 않기 위해서다.
"""
import base64
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-lic-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")
os.environ["RA_EDITION"] = "installed"
os.environ["RA_DEVICE_FP"] = "device-A"

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from core import keys  # noqa: E402

# --- 키체인을 메모리로
_kc = {}
keys.available = lambda: True
keys.get = lambda name: _kc.get(name, "")
keys.set = lambda name, value: _kc.__setitem__(name, value)
keys.delete = lambda name: _kc.pop(name, None)
keys.stored_in_keyring = lambda name: name in _kc

from core import licensing, packs  # noqa: E402
from mock_license_server import LicenseServer  # noqa: E402

_fails, _checks = [], 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


def section(t):
    print(f"\n== {t}")


def iso(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(
        timespec="seconds")


# ------------------------------------------------------------------ 준비

priv = Ed25519PrivateKey.generate()
seed = priv.private_bytes(encoding=serialization.Encoding.Raw,
                          format=serialization.PrivateFormat.Raw,
                          encryption_algorithm=serialization.NoEncryption())
pub_b64 = base64.b64encode(priv.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw)).decode("ascii")

PACK = {
    "pack_version": "7.0", "prompts": {"x": "안녕"}, "schemas": {}, "config": {},
    # ESG 시드 온톨로지도 팩에 실린다 (→ docs/20) — 패키지에서 빠지고 활성화 때 온다
    "seed": {"entities/규제·기준/EU CBAM.md": "---\ntype: entity\n---\n\n## 요약\n\n라이선스 시드\n"},
}
LICENSES = {
    "ANF-AAAA-BBBB-CCCC": {"seats": 2, "status": "active",
                           "pack_expires": iso(30), "refresh_after": iso(7)},
    "ANF-SEAT-0001-0001": {"seats": 1, "status": "active",
                           "pack_expires": iso(30), "refresh_after": iso(7)},
    "ANF-REVO-KED0-0000": {"seats": 1, "status": "revoked"},
    "ANF-SUSP-ENDE-D000": {"seats": 1, "status": "suspended"},
    "ANF-SOON-0000-0000": {"seats": 1, "status": "active",
                           "pack_expires": iso(3), "refresh_after": iso(-1)},
    "ANF-DEAD-0000-0000": {"seats": 1, "status": "active",
                           "pack_expires": iso(-1), "refresh_after": iso(-5)},
}

srv = LicenseServer(PACK, LICENSES, seed)
os.environ[licensing.SERVER_ENV] = srv.start()
os.environ[licensing.PUBKEY_ENV] = pub_b64        # 로컬 서버일 때만 먹는다


def reset_device(fp="device-A"):
    os.environ["RA_DEVICE_FP"] = fp
    licensing._pack_memo = None
    packs.reload()


# ------------------------------------------------------------------ 키 정규화

section("라이선스 키 입력")

for raw, want in [
    ("ANF-AAAA-BBBB-CCCC", "ANF-AAAA-BBBB-CCCC"),
    ("anf aaaa bbbb cccc", "ANF-AAAA-BBBB-CCCC"),
    ("AAAABBBBCCCC", "ANF-AAAA-BBBB-CCCC"),
    ("  ANF-aaaa-BBBB-cccc  ", "ANF-AAAA-BBBB-CCCC"),
]:
    check(licensing.normalize_key(raw) == want, f"'{raw.strip()}' → 정규화")
check(licensing.normalize_key("") == "", "빈 입력은 빈 키")

# ------------------------------------------------------------------ 활성화 전

section("활성화 전 — 잠기지만 볼트는 열려 있다")

check(licensing.status()["state"] == "none", "상태 none")
check(licensing.can_run() is False, "조사·비서 잠김")
check(licensing.pack_or_none() is None, "팩 없음")

# 팩 파일이 남아 있으면(개발 상태) 그쪽으로 떨어진다 — 릴리스 빌드는 그 파일이 없다
_real_pack_path = packs.PACK_PATH
packs.PACK_PATH = _SANDBOX / "no-such-pack.json"
packs.reload()
check(packs.is_available() is False, "동봉 팩이 없으면 조사 구성요소도 없다")
try:
    packs.get("pipeline.research")
    check(False, "팩 없이 프롬프트를 꺼내면 안 된다")
except packs.PackError as e:
    check("활성화" in str(e), "안내가 '재설치'가 아니라 '활성화'를 가리킨다")

# ------------------------------------------------------------------ 활성화

section("활성화")

try:
    licensing.activate("ANF-0000-0000-0000")
    check(False, "없는 키는 거절해야 한다")
except licensing.LicenseError as e:
    check("찾을 수 없" in str(e), "없는 키 거절")

for key, word in [("ANF-REVO-KED0-0000", "해지"), ("ANF-SUSP-ENDE-D000", "정지")]:
    try:
        licensing.activate(key)
        check(False, f"{word} 키는 거절해야 한다")
    except licensing.LicenseError as e:
        check(word in str(e), f"{word} 키 거절")

payload = licensing.activate("anf aaaa bbbb cccc")
check(payload["pack_version"] == "7.0", "팩을 받았다")
check(licensing.status()["state"] == "active", "상태 active")
check(licensing.can_run() is True, "조사 가능")
packs.reload()
check(packs.is_available() is True, "라이선스 팩으로 구성요소가 채워졌다")
check(packs.version() == "7.0", "팩 버전이 라이선스 것으로 잡힌다")
check(packs.get("x") == "안녕", "팩 내용을 읽는다")
seed = packs.seed()
check(len(seed) == 1 and "라이선스 시드" in list(seed.values())[0],
      "**시드 온톨로지도 라이선스 팩에서 온다** (패키지에서 빠진다)")

sent = [b for a, b in srv.calls if a == "activate"][-1]
check("license_key" in sent and "device_fp" in sent, "키와 기기 지문을 보낸다")
check(set(sent) <= {"license_key", "device_fp", "device_label", "app_version"},
      "**그 밖의 것은 보내지 않는다** (조사 내용·볼트는 전송 대상이 아니다)")
check(len(sent["device_fp"]) == 64 and "device-A" not in sent["device_fp"],
      "기기 지문은 해시 — 원본 식별자를 보내지 않는다")

# ------------------------------------------------------------------ 기기 바인딩

section("다른 PC 로 복사 — 동작하지 않는다 (DoD 2)")

cache_bytes = licensing._cache_path().read_bytes()
check(b"prompts" not in cache_bytes and b"pack_version" not in cache_bytes,
      "캐시 파일이 암호화되어 있다 (평문 팩이 보이지 않는다)")

# ① 폴더만 복사한 경우 — 키체인 비밀이 없다
saved_secret = _kc.pop(licensing.CACHE_SECRET_NAME)
licensing._pack_memo = None
check(licensing.pack_or_none() is None,
      "키체인 비밀 없이는 캐시를 열 수 없다 (폴더 복사 차단)")
check(licensing.status()["copied"] is True, "'다른 PC 로 복사됨' 을 알아챈다")
_kc[licensing.CACHE_SECRET_NAME] = saved_secret

# ② 키체인까지 통째로 옮긴 경우 — 기기 지문이 다르다
reset_device("device-B")
check(licensing.pack_or_none() is None,
      "**기기 지문이 다르면 열리지 않는다** (키체인까지 옮겨도 소용없다)")
check(licensing.can_run() is False, "복사본에서는 조사가 잠긴다")
reset_device("device-A")
check(licensing.pack_or_none() is not None, "원래 PC 에서는 그대로 동작한다")

# ③ 서명 대상에도 기기 지문이 들어간다 — 복호화를 뚫어도 걸린다
cached = licensing._read_cache()
check(licensing.verify_signature(
    cached["pack_json"], cached["pack_version"], cached["expires_at"],
    "다른기기지문", cached["signature"]) is False,
    "남의 기기 지문으로는 서명 검증이 실패한다")

# ------------------------------------------------------------------ 위조

section("위조 팩")

forged = Ed25519PrivateKey.generate()
bad_sig = base64.b64encode(forged.sign(licensing.signature_message(
    cached["pack_json"], cached["pack_version"], cached["expires_at"],
    licensing.device_fingerprint()))).decode("ascii")
check(licensing.verify_signature(
    cached["pack_json"], cached["pack_version"], cached["expires_at"],
    licensing.device_fingerprint(), bad_sig) is False,
    "다른 키로 만든 서명은 거부된다")

tampered = dict(cached)
tampered["pack"] = {"pack_version": "7.0", "prompts": {"x": "탈취"}}
tampered["pack_json"] = json.dumps(tampered["pack"], ensure_ascii=False)
licensing._write_cache(tampered)
licensing._pack_memo = None
check(licensing.pack_or_none() is None,
      "**캐시를 손으로 바꿔치기하면 서명 검증에서 걸린다**")

saved_pub = os.environ.pop(licensing.PUBKEY_ENV)
licensing._pack_memo = None
check(licensing.verify_signature("a", "b", "c", "d", "e") is False,
      "공개키가 없으면 검증을 통과시키지 않는다 (fail-closed)")
os.environ[licensing.PUBKEY_ENV] = saved_pub

licensing.activate("ANF-AAAA-BBBB-CCCC")     # 정상 상태로 복구
check(licensing.pack_or_none() is not None, "재활성화로 복구된다")

# ------------------------------------------------------------------ 좌석

section("좌석 (DoD 3)")

reset_device("seat-1")
licensing.activate("ANF-SEAT-0001-0001")
check(licensing.status()["state"] == "active", "1좌석 키로 첫 기기 활성화")

reset_device("seat-2")
try:
    licensing.activate("ANF-SEAT-0001-0001")
    check(False, "좌석 초과는 거절해야 한다")
except licensing.LicenseError as e:
    check("기기 수" in str(e), "좌석 초과 거절")

reset_device("seat-1")
licensing.deactivate()
check(licensing.license_key() == "", "해제하면 이 PC 의 키가 지워진다")
check(not licensing._cache_path().exists(), "해제하면 캐시도 지워진다")

reset_device("seat-2")
licensing.activate("ANF-SEAT-0001-0001")
check(licensing.status()["state"] == "active", "해제 후 다른 기기에서 활성화 성공")

# 서버가 죽어도 해제는 로컬에서 끝난다 — "해제도 못 하고 쓰지도 못하는" 상태 금지
srv.fail_next = 5
out = licensing.deactivate()
check(out["server"] is False and not licensing._cache_path().exists(),
      "서버가 안 되어도 **로컬 해제는 된다**")
srv.fail_next = 0

# ------------------------------------------------------------------ 유예

section("오프라인·서버 장애 (DoD 4·5) — 구매자를 막지 않는다")

reset_device("device-A")
licensing.activate("ANF-AAAA-BBBB-CCCC")

srv.stop()                                   # 서버를 통째로 내린다
r = licensing.refresh(force=True)
check(r["ok"] is False, "서버가 없으면 갱신은 실패한다")
check(licensing.can_run() is True,
      "**그래도 조사는 계속된다** — 우리 장애로 고객이 멈추면 안 된다")
check(licensing.status()["state"] == "active", "유효기간 안이면 상태도 정상")

# 갱신 시점이 지난 경우 = 유예. 여전히 쓸 수 있어야 한다.
c = licensing._read_cache()
c["refresh_after"] = iso(-1)
licensing._write_cache(c)
s = licensing.status()
check(s["state"] == "grace", "갱신 시점이 지나면 grace")
check(licensing.can_run() is True, "grace 에서도 조사는 된다")
check("지장이 없" in s["message"], "사고가 아니라는 걸 문구로 알린다")

# 유효기간까지 지나면 잠긴다 — 다만 볼트는 계속 열린다
c["expires_at"] = iso(-1)
licensing._write_cache(c)
licensing._pack_memo = None
check(licensing.status()["state"] == "expired", "유효기간이 지나면 expired")
check(licensing.can_run() is False, "만료되면 조사·비서 잠김")
check(licensing.pack_or_none() is None, "만료된 팩은 쓰지 않는다")

# ------------------------------------------------------------------ 오프라인 활성화

section("폐쇄망 활성화")

blob = licensing.offline_request_blob()
req = json.loads(base64.b64decode(blob))
check(set(req) == {"device_fp", "device_label", "app_version", "requested_at"},
      "요청 코드에는 기기 지문·버전만 들어간다")
check(len(req["device_fp"]) == 64, "요청 코드의 지문도 해시다")

fp = licensing.device_fingerprint()
expires = iso(30)
pack_text = json.dumps(PACK, ensure_ascii=False)
sig = base64.b64encode(priv.sign(licensing.signature_message(
    pack_text, "7.0", expires, fp))).decode("ascii")
reply = base64.b64encode(json.dumps({
    "pack_b64": base64.b64encode(pack_text.encode("utf-8")).decode("ascii"),
    "pack_version": "7.0", "expires_at": expires, "signature": sig,
    "license_key": "ANF-AAAA-BBBB-CCCC",
}).encode("utf-8")).decode("ascii")
licensing.apply_offline_blob(reply)
check(licensing.can_run() is True, "오프라인 활성화 파일로 동작한다")

reset_device("device-Z")
try:
    licensing.apply_offline_blob(reply)
    check(False, "남의 기기용 활성화 파일은 거절해야 한다")
except licensing.LicenseError as e:
    check("서명" in str(e), "**다른 기기용 활성화 파일은 서명에서 걸린다**")
reset_device("device-A")

# ------------------------------------------------------------------ 체험판

section("체험판은 영향을 받지 않는다 (R1)")

packs.PACK_PATH = _real_pack_path
os.environ["RA_EDITION"] = "trial"
import importlib  # noqa: E402
importlib.reload(__import__("core.edition", fromlist=["edition"]))
from core import edition  # noqa: E402
edition.current.cache_clear()
packs.reload()
check(edition.is_installed() is False, "체험판 판정")
check(packs.is_available() is True, "체험판은 동봉 팩을 그대로 쓴다")
check(packs.version() != "7.0", "라이선스 팩이 아니라 저장소의 팩이다")
os.environ["RA_EDITION"] = "installed"
edition.current.cache_clear()

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
