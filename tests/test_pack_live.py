"""실서버 팩 인출 검증 — 체험판 경로가 진짜로 도는가.

    $env:RA_TRIAL_TOKEN = Read-Host "토큰"
    python tests\test_pack_live.py

`test_packfetch.py` 는 가짜 서버로 로직만 본다. 여기서는 **배포된 함수**에서
실제 팩을 받아, 프롬프트 32·시드 35노트가 다 오는지 확인한다.

토큰은 **환경변수로만** 받는다 — 인자로 주면 명령 기록에 남는다.
화면에도 토큰을 찍지 않는다.
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

os.environ["RA_EDITION"] = "trial"

from core import packfetch  # noqa: E402

_fails, _checks = [], 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


if not packfetch.configured():
    print("RA_TRIAL_TOKEN 이 없습니다. 이렇게 넣고 다시 실행하세요:\n")
    print('    $env:RA_TRIAL_TOKEN = Read-Host "토큰"\n')
    sys.exit(2)

print("== 실서버 팩 인출 (체험판 경로)")
print(f"   서버: {packfetch._server()}")

# 캐시를 비우고 진짜로 받아 온다
packfetch._cache_path().unlink(missing_ok=True)
packfetch.reset()

t0 = time.time()
try:
    pack = packfetch.load()
except packfetch.FetchError as e:
    print(f"  FAIL 받지 못했습니다: {e}")
    sys.exit(1)
dt = time.time() - t0

check(isinstance(pack, dict), f"팩을 받았다 ({dt:.1f}초)")
check(len(pack.get("prompts") or {}) == 32,
      f"프롬프트 {len(pack.get('prompts') or {})}개")
check(len(pack.get("schemas") or {}) == 5, f"스키마 {len(pack.get('schemas') or {})}개")
check(len(pack.get("config") or {}) == 8, f"구성표 {len(pack.get('config') or {})}개")
check(len(pack.get("seed") or {}) == 35, f"시드 {len(pack.get('seed') or {})}노트")
check(bool(pack.get("pack_version")), f"팩 버전 {pack.get('pack_version')}")

check(packfetch._cache_path().exists(), "캐시가 만들어졌다 (다음 기동이 빠르다)")

# 저장소에 팩이 없어도 도는가 — 구멍을 닫는 목적 그 자체
from core import packs  # noqa: E402

packs.reload()
check(packs.is_available() is True, "packs 가 서버 팩을 쓴다")
try:
    packs.schema("librarian_answer")
    check(True, "실제 스키마를 하나 꺼내 본다")
except Exception as e:                      # noqa: BLE001
    check(False, f"스키마를 꺼내지 못했다: {e}")
check(len(packs.seed()) == 35, "시드도 서버 팩에서 온다")

print("\n" + "=" * 60)
if _fails:
    print(f"실패 {len(_fails)}/{_checks}")
    for f in _fails:
        print(f"  - {f}")
else:
    print(f"전부 통과 — {_checks}항목")
sys.exit(1 if _fails else 0)
