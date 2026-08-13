"""실서버 왕복 검증 — 배포된 Edge Function 이 실제로 무엇을 하는가.

    python tests\test_license_live.py

다른 묶음과 달리 **네트워크와 살아 있는 서버가 필요하다.** 그래서 CI 가 아니라
배포 직후·서버 코드를 고친 뒤에 손으로 돌린다.

왜 따로 있나: `test_edge_signature.py` 는 서명 **계산**이 맞는지만 본다.
여기서는 진짜 서버가 진짜 팩을 진짜 개인키로 서명한 것을, **저장소에 박힌
공개키**로 검증한다. 그 둘 사이(시크릿 등록·팩 업로드·공개키 주입)가
어긋나는 것이 실제로 가장 흔한 배포 사고다.

시험용 라이선스가 있어야 한다 (→ docs/24 5번). 팔기 전에 지울 것.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

os.environ["RA_EDITION"] = "installed"
from core import licensing  # noqa: E402

URL = os.environ.get("RA_LICENSE_SERVER") or licensing.SERVER_DEFAULT
KEY = os.environ.get("RA_TEST_LICENSE_KEY", "ANF-TEST-0001-0001")
FP1 = "1" * 64
FP2 = "2" * 64
FP3 = "3" * 64

fails, checks = [], 0


def check(cond, label):
    global checks
    checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


def call(action, **body):
    req = urllib.request.Request(
        f"{URL}/{action}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    dt = time.time() - t0
    try:
        return json.loads(raw), code, dt
    except json.JSONDecodeError:
        # JSON 이 아니면 게이트웨이가 끼어든 것이다 — 그대로 보여 준다
        body = raw.decode("utf-8", "replace")[:300]
        print(f"   ⚠️ {action}: HTTP {code} · JSON 아님 → {body!r}")
        return {"ok": False, "reason": f"_http_{code}", "_raw": body}, code, dt


print("== 실서버 활성화 왕복")
print(f"   공개키(박힌 값) …{licensing.PUBKEY_B64[-8:]}")

# 되풀이해도 같은 결과가 나오도록, 이전 시험의 흔적을 지운다
for fp in (FP1, FP2, FP3):
    call("deactivate", license_key=KEY, device_fp=fp)

# ---------------------------------------------------------------- 1. 활성화
out, code, _ = call("activate", license_key=KEY, device_fp=FP1,
                    device_label="검증-1", app_version="0.1.0")
check(code == 200 and out.get("ok") is True, f"활성화 성공 (HTTP {code})")
if not out.get("ok"):
    print(f"   응답: {out}")
    sys.exit(1)

pack_json = base64.b64decode(out["pack_b64"]).decode("utf-8")
pack = json.loads(pack_json)
check(len(pack.get("prompts") or {}) == 32, f"프롬프트 {len(pack.get('prompts') or {})}개")
check(len(pack.get("seed") or {}) == 35, f"시드 {len(pack.get('seed') or {})}노트")
check(out["pack_version"] == pack.get("pack_version"),
      f"팩 버전 일치 ({out['pack_version']})")

# ---------------------------------------------------------------- 2. 서명
ok = licensing.verify_signature(pack_json, out["pack_version"],
                                out["expires_at"], FP1, out["signature"])
check(ok is True, "**서버 서명을 이 PC 의 클라이언트가 검증한다**")

check(licensing.verify_signature(pack_json, out["pack_version"],
                                 out["expires_at"], FP2, out["signature"]) is False,
      "**다른 기기 지문으로는 검증 실패** (바인딩이 실제로 걸린다)")
check(licensing.verify_signature(pack_json + " ", out["pack_version"],
                                 out["expires_at"], FP1, out["signature"]) is False,
      "팩을 한 바이트 바꾸면 검증 실패")

# ---------------------------------------------------------------- 3. 좌석
check(out["seat_no"] == 1 and out["used"] == 1, f"좌석 {out['seat_no']}/{out['seats']}")

again, _, _ = call("activate", license_key=KEY, device_fp=FP1, device_label="검증-1")
check(again["seat_no"] == 1 and again["used"] == 1,
      "같은 기기를 다시 활성화해도 좌석을 더 먹지 않는다")

two, _, _ = call("activate", license_key=KEY, device_fp=FP2, device_label="검증-2")
check(two.get("ok") and two["seat_no"] == 2, "둘째 기기 = 좌석 2")

three, code3, _ = call("activate", license_key=KEY, device_fp=FP3)
check(code3 == 403 and three.get("reason") == "seats_full",
      "**셋째 기기는 좌석 초과로 막힌다**")

# ---------------------------------------------------------------- 4. 해제
off, _, _ = call("deactivate", license_key=KEY, device_fp=FP2)
check(off.get("ok") is True, "기기 해제")
retry, code4, _ = call("activate", license_key=KEY, device_fp=FP3)
check(code4 == 200 and retry.get("ok"), "해제한 좌석을 새 기기가 쓴다")

# ---------------------------------------------------------------- 5. refresh
ref, _, _ = call("refresh", license_key=KEY, device_fp=FP1)
check(ref.get("ok") is True, "refresh 는 등록된 기기에만 응답한다")
nope, code5, _ = call("refresh", license_key=KEY, device_fp=FP2)
check(code5 == 403 and nope.get("reason") == "not_activated",
      "해제된 기기의 refresh 는 거절")

# ---------------------------------------------------------------- 6. 키 캐내기
t_bad = [call("activate", license_key="ANF-ZZZZ-ZZZZ-ZZZZ", device_fp=FP1)[2]
         for _ in range(3)]
t_good = [call("activate", license_key=KEY, device_fp=FP1)[2] for _ in range(3)]
print(f"   없는 키 {min(t_bad):.2f}~{max(t_bad):.2f}s · "
      f"있는 키 {min(t_good):.2f}~{max(t_good):.2f}s")
# 바닥이 깔렸는지가 아니라 **차이가 지워졌는지**를 본다. 앞의 것은 통과할
# 수밖에 없는 검사였고, 실제로 0.4초 대 2.0초로 갈리는 것을 놓쳤다.
gap = min(t_good) - max(t_bad)
print(f"   가장 빠른 유효 응답 - 가장 느린 무효 응답 = {gap:+.2f}s")
check(gap > -0.35,
      "있는 키가 없는 키보다 눈에 띄게 느리지 않다 (시간으로 존재를 못 잰다)")

# 흔적 정리
for fp in (FP1, FP2, FP3):
    call("deactivate", license_key=KEY, device_fp=fp)

print("\n" + "=" * 60)
if fails:
    print(f"실패 {len(fails)}/{checks}")
    for f in fails:
        print(f"  - {f}")
else:
    print(f"전부 통과 — {checks}항목")
sys.exit(1 if fails else 0)
