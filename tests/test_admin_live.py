"""실서버 관리 조작 검증 — 발급 · 수정 · 좌석 해제 · 삭제.

    $env:RA_ADMIN_TOKEN = Read-Host "관리 토큰"
    python tests\test_admin_live.py

홈페이지 `/admin/license/` 화면이 부르는 것과 **똑같은 문**을 부른다.
화면(브라우저)은 가짜 서버로 이미 확인했으므로, 여기서는 서버 쪽만 본다.

**뒤처리까지 한다.** 시험용 라이선스를 발급하고, 활성화까지 시켜 보고,
좌석을 해제한 뒤 지운다 — 시험을 돌릴 때마다 판매용 목록에 쓰레기가
쌓이면 다음 사람이 그것을 지우기 무섭다(지워도 되는 것인지 알 수 없으니).

토큰은 **환경변수로만** 받는다 — 인자로 주면 명령 기록에 남는다.
화면에도 찍지 않는다.
"""
import hashlib
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

from core.licensing import SERVER_DEFAULT, normalize_key  # noqa: E402

TOKEN = os.environ.get("RA_ADMIN_TOKEN", "").strip()
# 지문은 아무 값이나 되지만 서버가 32자 이상을 요구한다 (→ index.ts).
# 시험 전용임이 눈에 보이게 씨앗을 박아 둔다.
DEVICE_FP = hashlib.sha256(b"ra-admin-live-test").hexdigest()

_fails, _checks = [], 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


def call(path, body):
    req = urllib.request.Request(
        f"{SERVER_DEFAULT}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return json.loads(raw)
        except ValueError:
            # 게이트웨이가 HTML 을 줄 때가 있다 — 통째로 죽는 대신 보여 준다.
            return {"ok": False, "reason": f"http_{e.code}", "raw": raw[:200]}


def admin(op, **kw):
    return call("admin", {"admin_token": TOKEN, "op": op, **kw})


if not TOKEN:
    print("RA_ADMIN_TOKEN 이 없습니다. 이렇게 넣고 다시 실행하세요:\n")
    print('    $env:RA_ADMIN_TOKEN = Read-Host "관리 토큰"\n')
    sys.exit(2)

print("== 실서버 관리 조작")
print(f"   서버: {SERVER_DEFAULT}")
print("   (응답마다 2초 바닥이 깔려 있다 — 느린 것이 정상이다)\n")

t0 = time.time()

# ---------------------------------------------------------------- 자격
check(call("admin", {"op": "list"}).get("reason") == "invalid",
      "토큰 없이는 거절한다")
check(call("admin", {"admin_token": TOKEN + "x", "op": "list"}).get("reason") == "invalid",
      "틀린 토큰은 거절한다 (한 글자 더 붙여도)")
check(admin("훔쳐보기").get("reason") == "admin_error",
      "모르는 작업은 admin_error")

# ---------------------------------------------------------------- 목록
lst = admin("list")
check(lst.get("ok") and isinstance(lst.get("licenses"), list),
      f"목록을 읽는다 ({len(lst.get('licenses') or [])}건)")
before = len(lst.get("licenses") or [])
check(all("devices" in r for r in lst.get("licenses") or []),
      "라이선스마다 기기 목록이 함께 온다 (요청 한 번으로 화면이 완성된다)")

# ---------------------------------------------------------------- 발급
check(admin("issue", email="").get("reason") == "admin_error",
      "이메일 없이는 발급하지 않는다")

res = admin("issue", email="live-test@anfilt.local", company="자동 시험",
            seats=2, note="tests/test_admin_live.py — 끝나면 스스로 지운다")
KEY = ((res.get("license") or {}).get("license_key")) or ""
check(res.get("ok") and KEY, f"발급했다 ({KEY})")
check(KEY == normalize_key(KEY),
      "키 모양이 클라이언트의 normalize_key() 와 같다")
check(not (set(KEY) & set("O01IL")),
      "헷갈리는 글자(O·0·1·I·L)가 없다 — 전화로 불러 줄 수 있다")

if not KEY:
    print("\n키를 받지 못해 이후 시험을 건너뜁니다.")
    sys.exit(1)

lst = admin("list")
row = next((r for r in lst["licenses"] if r["license_key"] == KEY), None)
check(row is not None and len(lst["licenses"]) == before + 1, "목록에 나타난다")
check(row and row["seats"] == 2 and row["status"] == "active" and not row["expires_at"],
      "좌석 2 · 사용 중 · 무기한(만료일을 안 줬다)")

# ---------------------------------------------------------------- 수정
check(admin("update", license_key=KEY, patch={"status": "얼렁뚱땅"}).get("reason") == "admin_error",
      "모르는 상태는 거절한다")
check(admin("update", license_key=KEY,
            patch={"license_key": "ANF-HACK-HACK-HACK"}).get("reason") == "admin_error",
      "허용하지 않은 칸만 보내면 '바꿀 내용이 없다' (키는 못 바꾼다)")

admin("update", license_key=KEY, patch={"seats": 1, "expires_at": "2027-01-31"})
row = next(r for r in admin("list")["licenses"] if r["license_key"] == KEY)
check(row["seats"] == 1, "좌석을 1로 줄였다")
check(str(row["expires_at"]).startswith("2027-01-31T23:59"),
      f"만료일은 그 날 **끝**이다 ({row['expires_at']}) — 아침에 잠기지 않는다")

# ---------------------------------------------------------------- 활성화 → 해제
act = call("activate", {"license_key": KEY, "device_fp": DEVICE_FP,
                        "device_label": "자동 시험 PC", "app_version": "0.0.0"})
check(act.get("ok") is True, "이 키로 실제 활성화가 된다 (발급이 진짜다)")

row = next(r for r in admin("list")["licenses"] if r["license_key"] == KEY)
check(len(row["devices"]) == 1 and row["devices"][0]["device_label"] == "자동 시험 PC",
      "관리 화면에 그 기기가 보인다")

gone = admin("delete", license_key=KEY)
check(gone.get("reason") == "admin_error" and "활성화" in (gone.get("message") or ""),
      "쓰는 중인 라이선스는 지우지 못한다 (해지하라고 말한다)")

check(admin("release", license_key=KEY, device_fp="없는지문").get("reason") == "admin_error",
      "없는 기기 해제는 admin_error")
check(admin("release", license_key=KEY, device_fp=DEVICE_FP).get("ok") is True,
      "좌석을 해제한다")
row = next(r for r in admin("list")["licenses"] if r["license_key"] == KEY)
check(len(row["devices"]) == 0, "기기 목록이 비었다")

# ---------------------------------------------------------------- 뒤처리
check(admin("delete", license_key=KEY).get("ok") is True, "시험용 라이선스를 지웠다")
check(all(r["license_key"] != KEY for r in admin("list")["licenses"]),
      "목록에서 사라졌다 — 시험 전과 같은 상태다")

print("\n" + "=" * 60)
print(f"({time.time() - t0:.0f}초 걸렸다 — 대부분 응답 시간 바닥이다)")
if _fails:
    print(f"실패 {len(_fails)}/{_checks}")
    for f in _fails:
        print(f"  - {f}")
    print(f"\n⚠️ 시험용 라이선스 {KEY} 가 남아 있을 수 있습니다 — 관리 화면에서 확인하세요.")
else:
    print(f"전부 통과 — {_checks}항목")
sys.exit(1 if _fails else 0)
