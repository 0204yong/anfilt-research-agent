"""8단계 회귀 — 놓친 시각 따라잡기 · 중복 방지 · 스케줄 등록.

실행:  python tests/test_schedule.py

핵심은 `is_due` 다. 두 성질이 **동시에** 성립해야 한다.

  A. PC 가 꺼져 있어 놓친 예정 시각을 **그날 안에 따라잡는다**
  B. 같은 시각대에 **두 번 돌지 않는다**

A 만 보면 매시 도는 고장이 되고, B 만 보면 예전처럼 영영 건너뛴다.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-sch-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")

from core import scheduler, watch as W  # noqa: E402

_fails, _checks = [], 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


def section(t):
    print(f"\n== {t}")


def at(day: int, hour: int, minute: int = 0):
    return datetime(2026, 8, day, hour, minute, tzinfo=W.KST)


def watch(hours="08", last=None, enabled=True, every=None):
    w = {"watch_id": "w1", "name": "테스트", "hours": hours,
         "enabled": enabled,
         "last_checked_at": last.isoformat() if last else None}
    if every is not None:
        w["every_days"] = every
    return w


# ------------------------------------------------------------------ 따라잡기

section("놓친 시각 따라잡기 (DoD 1)")

# 08시 감시를 걸어 두고 10시에 PC 를 켠 상황 — 예전에는 영영 건너뛰었다
check(W.is_due(watch("08", last=at(9, 8, 5)), at(10, 10)) is True,
      "**08시 감시를 10시에 켜면 그날 안에 실행된다** (어제 08:05 이후 기록 없음)")
check(W.is_due(watch("08", last=None), at(10, 23, 59)) is True,
      "한 번도 안 돌았으면 그날 23:59 에도 따라잡는다")
check(W.is_due(watch("08", last=at(1, 8, 0)), at(10, 10)) is True,
      "며칠 꺼져 있었어도 켠 뒤 실행된다")

# 아직 예정 시각 전이면 돌지 않는다
check(W.is_due(watch("08", last=at(9, 8, 5)), at(10, 7, 59)) is False,
      "예정 시각(08:00) 전에는 돌지 않는다")
check(W.is_due(watch("18", last=None), at(10, 9)) is False,
      "18시 감시는 오전에 돌지 않는다")

# **어제 것까지 따라잡지는 않는다** — 새벽에 갑자기 도는 것을 막는다
check(W.is_due(watch("08", last=at(1, 8, 0)), at(10, 3)) is False,
      "새벽 3시에는 돌지 않는다 (어제 것까지 따라잡지 않는다)")

section("중복 방지 (DoD 2)")

check(W.is_due(watch("08", last=at(10, 8, 5)), at(10, 9)) is False,
      "08시에 돌았으면 09시에 다시 돌지 않는다")
check(W.is_due(watch("08", last=at(10, 10, 30)), at(10, 11)) is False,
      "따라잡기로 10:30 에 돌았으면 11시에 또 돌지 않는다")
check(W.is_due(watch("08", last=at(10, 8, 0)), at(10, 8, 30)) is False,
      "같은 시각대 안에서 중복 없음")

# 하루 두 번짜리
w2 = "08,18"
check(W.is_due(watch(w2, last=at(10, 8, 5)), at(10, 17, 59)) is False,
      "08,18 감시 — 18시 전에는 다시 돌지 않는다")
check(W.is_due(watch(w2, last=at(10, 8, 5)), at(10, 18, 1)) is True,
      "08,18 감시 — 18시가 지나면 실행된다")
check(W.is_due(watch(w2, last=at(10, 18, 5)), at(10, 21)) is False,
      "18시 것을 돌았으면 21시에 또 돌지 않는다")
check(W.is_due(watch(w2, last=at(10, 18, 5)), at(11, 9)) is True,
      "다음 날 08시가 지나면 다시 실행된다")

section("그 밖")

check(W.is_due(watch("08", last=None, enabled=False), at(10, 10)) is False,
      "중지된 감시는 돌지 않는다")
check(W.is_due(watch("", last=None), at(10, 9)) is True,
      "시각이 비면 08시로 본다 (기존 규칙 유지)")
check(W.is_due(watch("08", last=at(10, 8, 0)), at(10, 8, 0)) is False,
      "예정 시각 정각에 이미 돌았으면 안 돈다")

slot = W.due_slot(watch("08,18"), at(10, 19))
check(slot == at(10, 18), "due_slot — 지나간 마지막 예정 시각을 준다")
check(W.due_slot(watch("18"), at(10, 9)) is None, "due_slot — 아직이면 None")

# 예전 규칙이었다면 실패했을 항목을 하나 못 박아 둔다
old_rule = at(10, 10).hour in W.parse_hours("08")
check(old_rule is False and W.is_due(watch("08", last=at(9, 8)), at(10, 10)) is True,
      "**예전 규칙(시각 정확 일치)이라면 건너뛰었을 상황이 이제 실행된다**")

# ------------------------------------------------------------------ 시간 진행

section("하루를 시뮬레이션 — 매시 깨워도 필요한 만큼만 돈다")

w = watch("08,18", last=at(9, 18, 3))
runs = []
for hour in range(0, 24):
    now = at(10, hour, 0)
    if W.is_due(w, now):
        runs.append(hour)
        w["last_checked_at"] = now.replace(minute=2).isoformat()
check(runs == [8, 18], f"24번 깨워도 실행은 2번 (실행 시각 {runs})")

# PC 가 12시에 켜진 날 — 08시 것을 따라잡고, 18시 것은 제때
w = watch("08,18", last=at(9, 18, 3))
runs = []
for hour in range(12, 24):
    now = at(10, hour, 0)
    if W.is_due(w, now):
        runs.append(hour)
        w["last_checked_at"] = now.replace(minute=2).isoformat()
check(runs == [12, 18], f"12시에 켜면 08시 것을 따라잡고 18시 것도 돈다 ({runs})")

# ------------------------------------------------------------------ 스케줄러

section("작업 스케줄러 연동")

check(isinstance(scheduler.available(), bool), "available() 은 bool")
check(scheduler.TASK_NAME == "ANFILT 리서치에이전트 모니터링",
      "작업 이름이 인스톨러와 같다")

iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
check(scheduler.TASK_NAME in iss,
      "**인스톨러와 코드가 같은 작업 이름을 쓴다** (다르면 해제 버튼이 헛돈다)")
check("watch_run.py" in iss, "인스톨러도 watch_run.py 를 부른다")

st = scheduler.status()
check(set(st) == {"registered", "state", "last_run", "next_run", "last_result"},
      "status() 모양 고정")
check(scheduler.available() or st["registered"] is False,
      "설치판이 아니면 등록되지 않은 것으로 본다")

if not scheduler.available():
    ok, msg = scheduler.register()
    check(ok is False and "설치판" in msg, "설치판이 아니면 등록을 거부하고 이유를 준다")

# ------------------------------------------------------------------ 기록 갱신

section("점검 결과 기록 — 따라잡기가 여기에 기댄다")

import core.watch_runner as WR  # noqa: E402

marked = []


class _Store:
    def watch_seen_fingerprints(self, wid):
        return set()

    def watch_mark_checked(self, wid, at_, status, snapshot=None):
        marked.append((wid, at_, status))


_orig_page = W.check_page
W.check_page = lambda w, seen: ([], "snap", False)   # 새 항목 없음
try:
    res = WR.run_watch(_Store(), None, {"watch_id": "w1", "name": "n",
                                        "kind": "page", "target": "http://x"},
                       now_iso="2026-08-10T10:00:00", send_notify=False)
finally:
    W.check_page = _orig_page

check(res.status == "새 항목 없음", "새 항목이 없으면 그렇게 보고한다")
check(len(marked) == 1 and marked[0][1] == "2026-08-10T10:00:00",
      "**'새 항목 없음' 일 때도 최근 점검 시각이 기록된다** "
      "(1단계 이후 여기가 조용히 빠져 있었다)")

# ------------------------------------------------------------------ 며칠에 한 번

section("'일' 주기 (every_days)")

check(W.every_days({}) == 1, "값이 없으면 매일 — 기존 감시의 동작이 안 바뀐다")
check(W.every_days({"every_days": None}) == 1, "None 도 매일")
check(W.every_days({"every_days": "7"}) == 7, "문자열 '7' 도 읽는다 (DB 왕복)")
check(W.every_days({"every_days": 0}) == 1, "0 은 1 로 (영원히 안 도는 감시를 막는다)")
check(W.every_days({"every_days": "이레"}) == 1, "말이 안 되는 값은 매일로")

# 3일 주기 · 08시 · 8/10 08:05 에 마지막 점검
w3 = lambda last: watch("08", last=last, every=3)          # noqa: E731
check(W.is_due(w3(at(10, 8, 5)), at(11, 9)) is False, "3일 주기 — 다음 날은 안 돈다")
check(W.is_due(w3(at(10, 8, 5)), at(12, 9)) is False, "3일 주기 — 이틀 뒤도 안 돈다")
check(W.is_due(w3(at(10, 8, 5)), at(13, 9)) is True, "3일 주기 — 사흘 뒤에 돈다")
check(W.is_due(w3(at(10, 8, 5)), at(13, 7)) is False,
      "사흘 뒤라도 예정 시각 전이면 안 돈다 (시각 조건은 그대로다)")
check(W.is_due(w3(None), at(10, 9)) is True, "한 번도 안 돌았으면 주기와 무관하게 돈다")

# 날짜로 센다 — 시각으로 세면 '3일마다 아침'이 하루씩 밀린다
check(W.is_due(watch("08", last=at(10, 18, 0), every=3), at(13, 8, 30)) is True,
      "마지막이 사흘 전 저녁이어도 사흘째 아침에 돈다 (72시간이 아니라 날짜로 센다)")

check(W.is_due(watch("08", last=at(10, 8, 5), every=1), at(11, 9)) is True,
      "주기 1 은 예전과 똑같이 매일")

# ------------------------------------------------------------------ 페이지 넘김

section("목록 여러 장 (page_urls · 겹칠 때까지)")

check(W.page_urls("https://a.kr/list.do") == ["https://a.kr/list.do"],
      "{page} 가 없으면 첫 장만 — 예전 동작 그대로")
urls = W.page_urls("https://a.kr/list.do?p={page}")
check(len(urls) == W.MAX_PAGES and urls[0].endswith("p=1") and urls[1].endswith("p=2"),
      f"{{page}} 를 1..{W.MAX_PAGES} 로 채운다")

# 가짜 페이지들로 '겹칠 때까지'를 확인한다 — 네트워크를 타지 않는다.
_pages = {}          # url -> (본문, 링크들)
_calls = []


def _fake_fetch(url, timeout=25):
    _calls.append(url)
    if url not in _pages:
        raise RuntimeError(f"404 {url}")
    return _pages[url]


_real_fetch = W._fetch_page
W._fetch_page = _fake_fetch
try:
    base = "https://a.kr/list?p={page}"
    _pages.clear(); _calls.clear()
    _pages["https://a.kr/list?p=1"] = ("헤더", [{"title": "새 기사 1", "url": "https://a.kr/n/1"}])
    _pages["https://a.kr/list?p=2"] = ("헤더", [{"title": "옛 기사", "url": "https://a.kr/n/old"}])
    _pages["https://a.kr/list?p=3"] = ("헤더", [{"title": "더 옛날", "url": "https://a.kr/n/old2"}])
    old_fp = {W.fingerprint_url("https://a.kr/n/old"),
              W.fingerprint_url("https://a.kr/n/old2")}
    hits, snap, base_flag = W.check_page({"target": base, "last_snapshot": "헤더"}, old_fp)
    check(len(_calls) == 2,
          f"새 것이 없는 장을 만나면 멈춘다 (2장만 읽음, 실제 {len(_calls)})")
    check([h.title for h in hits] == ["새 기사 1"], "새 항목만 잡는다")

    _pages.clear(); _calls.clear()
    for i in range(1, 6):
        _pages[f"https://a.kr/list?p={i}"] = (
            "헤더", [{"title": f"기사 {i}", "url": f"https://a.kr/n/{i}"}])
    hits, snap, _ = W.check_page({"target": base, "last_snapshot": "헤더"}, {"없는지문"})
    # 6번째 호출(없는 장)은 피할 수 없다 — 5장이 마지막인지 알려면 눌러 봐야 한다.
    check(len(_calls) == 6 and len(hits) == 5,
          f"계속 새 것이면 계속 넘긴다 (5장 다 읽고 6장째에서 끝, 실제 {len(_calls)}장·{len(hits)}건)")

    _pages.clear(); _calls.clear()
    _pages["https://a.kr/list?p=1"] = ("헤더", [{"title": "하나", "url": "https://a.kr/n/1"}])
    hits, snap, _ = W.check_page({"target": base, "last_snapshot": "헤더"}, set())
    check(len(_calls) == 2, "뒷장이 없으면(404) 거기서 멈춘다 — 감시가 죽지 않는다")

    _pages.clear(); _calls.clear()
    try:
        W.check_page({"target": base, "last_snapshot": ""}, set())
        check(False, "첫 장이 실패하면 예외를 올린다")
    except RuntimeError:
        check(True, "첫 장이 실패하면 예외를 올린다 (감시가 고장 났다고 알린다)")
finally:
    W._fetch_page = _real_fetch

section("감시별 알림 상한 (max_hits)")
check(W.max_hits({}) == W.MAX_HITS, f"값이 없으면 기본 {W.MAX_HITS}")
check(W.max_hits({"max_hits": 20}) == 20, "뉴스 매체는 20으로 올린다")
check(W.max_hits({"max_hits": 0}) == W.MAX_HITS, "0 은 미설정으로 보아 기본값")
check(W.max_hits({"max_hits": 9999}) == W.MAX_HITS_LIMIT, f"상한 {W.MAX_HITS_LIMIT}")
check(W.max_hits({"max_hits": "다섯"}) == W.MAX_HITS, "말이 안 되는 값은 기본값")

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
