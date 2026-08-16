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
from datetime import date, datetime, timedelta
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


def _fake_fetch(url, timeout=25, use_browser=False):
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
    hits, snap, base_flag = W.check_page({"target": base, "last_snapshot": "헤더", "browser_mode": "never"}, old_fp)
    check(len(_calls) == 2,
          f"새 것이 없는 장을 만나면 멈춘다 (2장만 읽음, 실제 {len(_calls)})")
    check([h.title for h in hits] == ["새 기사 1"], "새 항목만 잡는다")

    _pages.clear(); _calls.clear()
    for i in range(1, 6):
        _pages[f"https://a.kr/list?p={i}"] = (
            "헤더", [{"title": f"기사 {i}", "url": f"https://a.kr/n/{i}"}])
    hits, snap, _ = W.check_page({"target": base, "last_snapshot": "헤더", "browser_mode": "never"}, {"없는지문"})
    # 6번째 호출(없는 장)은 피할 수 없다 — 5장이 마지막인지 알려면 눌러 봐야 한다.
    check(len(_calls) == 6 and len(hits) == 5,
          f"계속 새 것이면 계속 넘긴다 (5장 다 읽고 6장째에서 끝, 실제 {len(_calls)}장·{len(hits)}건)")

    _pages.clear(); _calls.clear()
    _pages["https://a.kr/list?p=1"] = ("헤더", [{"title": "하나", "url": "https://a.kr/n/1"}])
    hits, snap, _ = W.check_page({"target": base, "last_snapshot": "헤더", "browser_mode": "never"}, set())
    check(len(_calls) == 2, "뒷장이 없으면(404) 거기서 멈춘다 — 감시가 죽지 않는다")

    _pages.clear(); _calls.clear()
    try:
        W.check_page({"target": base, "last_snapshot": ""}, set())
        check(False, "첫 장이 실패하면 예외를 올린다")
    except RuntimeError:
        check(True, "첫 장이 실패하면 예외를 올린다 (감시가 고장 났다고 알린다)")
finally:
    W._fetch_page = _real_fetch

section("제목 키워드 필터 (keywords)")

check(W.keywords({}) == [], "비면 거르지 않는다")
check(W.keywords({"keywords": "KSSB, IFRS S2 , ,GRI"}) == ["KSSB", "IFRS S2", "GRI"],
      "쉼표로 나누고 공백·빈 칸을 버린다")
check(W._kw_hit("아무거나", []) is True, "낱말이 없으면 전부 통과")
check(W._kw_hit("【기획】국내 대기업 KSSB 전수 분석", ["KSSB"]) is True, "제목에 있으면 통과")
check(W._kw_hit("찾아오시는 길", ["KSSB", "GRI"]) is False, "메뉴 링크는 걸러진다")
check(W._kw_hit("Scope3 배출량 산정", ["Scope 3"]) is True,
      "'Scope 3' 과 'Scope3' 을 같게 본다 (띄어쓰기 무시)")
check(W._kw_hit("ifrs s2 도입", ["IFRS S2"]) is True, "대소문자 무시")

section("중요도 척도 (1~5 · 고객이 고칠 수 있다)")

sys.path.insert(0, str(ROOT / "tests"))
from packfix import install_pack                                   # noqa: E402
install_pack()

from core import settings as _settings                             # noqa: E402

_settings.save({})                                   # 기본값 상태로 시작
_base = W.importance_scale()
check(len(_base) == 5 and W.IMPORTANCE_LEVELS == 5, "다섯 단계 (사이값 없음)")
check(all(s.strip() for s in _base), "기본 설명이 팩에서 다섯 줄 다 온다")
check("규제" in _base[4] or "확정" in _base[4], f"5단계가 가장 무겁다 — {_base[4][:24]}")

check(W.clamp_importance(7) == 5, "옛 10점 척도 값(7)이 와도 5로 눌린다")
check(W.clamp_importance(0) == 1 and W.clamp_importance(-3) == 1, "1 아래는 1")
check(W.clamp_importance("셋") == 3, "말이 안 되는 값은 가운데(3)로")
check(W.clamp_importance("4") == 4, "문자열 숫자도 읽는다")

_settings.save({"watch_importance_scale": ["", "", "우리 회사 기준 3단계", "", ""]})
_mine = W.importance_scale()
check(_mine[2] == "우리 회사 기준 3단계", "고친 줄이 반영된다")
check(_mine[0] == _base[0] and _mine[4] == _base[4],
      "비워 둔 줄은 기본 설명 그대로 — 한 줄만 고칠 수 있다")
blk = W.scale_block()
check(blk.startswith("- **5**:") and "우리 회사 기준 3단계" in blk,
      "프롬프트 블록은 높은 쪽부터, 고친 문장을 담는다")
_settings.save({})
check(W.importance_scale() == _base, "설정을 비우면 기본값으로 돌아간다")

section("원문까지 읽기 (문턱은 셋 다 고객이 정한다)")

check(W.body_settings({})["on"] is False, "기본은 꺼짐 — 예전 동작")
_b = W.body_settings({"fetch_body": 1})
check((_b["min_importance"], _b["limit"]) == (4, 5), "켜면 기본 4단계 이상 · 5건")
_b = W.body_settings({"fetch_body": 1, "fetch_min_importance": 2, "fetch_limit": 12})
check((_b["min_importance"], _b["limit"]) == (2, 12), "단계·건수를 고객이 정한다")
_b = W.body_settings({"fetch_body": 1, "fetch_min_importance": 9, "fetch_limit": 999})
check(_b["min_importance"] == 5 and _b["limit"] == W.BODY_LIMIT_MAX,
      f"범위를 넘으면 5단계·{W.BODY_LIMIT_MAX}건에서 자른다")
check(W.body_settings({"fetch_body": 1, "keywords": "CBAM"})["keywords"] == ["CBAM"],
      "본문 낱말을 안 적으면 제목 키워드를 쓴다 — 두 번 적게 하지 않는다")
check(W.body_settings({"fetch_body": 1, "keywords": "CBAM",
                       "fetch_keywords": "KSSB"})["keywords"] == ["KSSB"],
      "따로 적으면 그쪽을 쓴다")

_dg = {"items": [
    {"title": "규제 확정", "url": "https://a.kr/news/1", "importance": 5, "what_is_new": ""},
    {"title": "동향 해설", "url": "https://a.kr/news/2", "importance": 3, "what_is_new": ""},
    {"title": "또 규제", "url": "https://a.kr/news/3", "importance": 5, "what_is_new": ""},
    {"title": "목록 자체", "url": "https://a.kr", "importance": 5, "what_is_new": ""},
]}
check(W.pick_for_body({}, _dg) == [], "꺼져 있으면 하나도 안 연다")
_p = W.pick_for_body({"fetch_body": 1, "fetch_min_importance": 4}, _dg)
check([x["title"] for x in _p] == ["규제 확정", "또 규제"],
      "문턱을 넘은 것만 · 도메인 루트(목록 주소)는 열지 않는다")
_p = W.pick_for_body({"fetch_body": 1, "fetch_min_importance": 4, "fetch_limit": 1}, _dg)
check(len(_p) == 1, "건수 상한이 마지막 안전장치")
_p = W.pick_for_body({"fetch_body": 1, "fetch_min_importance": 1,
                      "fetch_keywords": "해설"}, _dg)
check([x["title"] for x in _p] == ["동향 해설"], "본문 낱말로도 좁힌다")

section("날짜로 끊기 (cutoff_date · page_dates)")

_today = date(2026, 8, 16)
check(W.page_dates("등록일 2026-08-14 조회 12", _today) == [date(2026, 8, 14)],
      "YYYY-MM-DD 를 읽는다")
check(date(2026, 8, 14) in W.page_dates("2026.08.14", _today), "YYYY.MM.DD 도 읽는다")
check(date(2026, 8, 14) in W.page_dates("2026년 8월 14일", _today), "한글 날짜도 읽는다")
check(date(2026, 8, 14) in W.page_dates("08.14 16:54", _today),
      "연도 없는 '08.14' 는 올해로 (임팩트온 형식)")
check(date(2025, 12, 30) in W.page_dates("12.30", _today),
      "올해로 읽으면 미래가 되는 날짜는 작년으로")

# 마지막 점검 하루 전까지 거슬러 간다 — 등록일이 밀려 찍히는 게시판 대비
w_last = {"last_checked_at": at(10, 9, 0).isoformat()}
check(W.cutoff_date(w_last, at(16, 9)) == date(2026, 8, 9),
      "마지막 점검(8/10)에서 하루 물린 8/9 가 기준")
check(W.cutoff_date({"every_days": 7}, at(16, 9)) == date(2026, 8, 2),
      "한 번도 안 돌았으면 주기(7일)의 두 배만큼 (기준선)")
check(W.cutoff_date({}, at(16, 9)) == date(2026, 8, 14),
      "주기가 없으면 매일로 보아 이틀치")
check(W.cutoff_date({"every_days": 365}, at(16, 9)) == date(2026, 7, 17),
      "주기가 길어도 30일에서 끊는다 (2002년치를 다 읽지 않는다)")

section("감시별 알림 상한 (max_hits)")
check(W.max_hits({}) == W.MAX_HITS, f"값이 없으면 기본 {W.MAX_HITS}")
check(W.max_hits({"max_hits": 20}) == 20, "뉴스 매체는 20으로 올린다")
check(W.max_hits({"max_hits": 0}) == W.MAX_HITS, "0 은 미설정으로 보아 기본값")
check(W.max_hits({"max_hits": 9999}) == W.MAX_HITS_LIMIT, f"상한 {W.MAX_HITS_LIMIT}")
check(W.max_hits({"max_hits": "다섯"}) == W.MAX_HITS, "말이 안 되는 값은 기본값")

section("브라우저로 읽기 (→ core/browserfetch.py)")

from core import browserfetch as BF  # noqa: E402

check(W.browser_mode({}) == "auto", "기본은 자동 — 고객이 고르지 않아도 된다")
check(W.browser_mode({"browser_mode": "always"}) == "always", "항상")
check(W.browser_mode({"browser_mode": "never"}) == "never", "쓰지 않음")
check(W.browser_mode({"browser_mode": "이상한값"}) == "auto", "모르는 값은 자동으로")
# 참/거짓 하나뿐이던 시절의 값 — 켜 뒀던 감시가 갱신 한 번에 꺼지면 안 된다
check(W.browser_mode({"use_browser": 1}) == "always", "옛 켬(1)은 '항상' 으로 읽는다")
check(W.browser_mode({"use_browser": 0}) == "auto", "옛 끔(0)은 자동으로 (그게 더 낫다)")

# **껍데기 판정** — ESG Finance Hub 의 목록 페이지가 requests 로는 547자였다.
# 목록 한 장이면 최소 수천 자는 나온다.
check(BF.looks_thin("메뉴 " * 30, []) is True, "547자짜리 껍데기는 껍데기로 본다")
check(BF.looks_thin("가" * 5000, []) is False, "본문이 실하면 권하지 않는다")
check(BF.looks_thin("가" * 500, [{"u": i} for i in range(40)]) is False,
      "글자가 적어도 링크가 많으면 목록은 읽힌 것이다")

# '항상' 이면 requests 를 아예 안 부른다 — 방화벽이 막는 건 requests 쪽이다
_called = []
_real_get = W.requests.get
W.requests.get = lambda *a, **k: _called.append(a) or (_ for _ in ()).throw(
    AssertionError("브라우저 모드인데 requests 를 불렀다"))
try:
    import core.browserfetch as _bf
    _orig = _bf.get_html
    _bf.get_html = lambda url, timeout=60: (
        "<html><body><a href='/a'>기후공시 의무화 확정</a><p>본문</p></body></html>")
    text, links = W._fetch_page("https://x.kr/list", use_browser=True)
    check(not _called, "브라우저 모드에서는 requests 를 부르지 않는다")
    check(any("기후공시" in l["title"] for l in links),
          "브라우저가 준 DOM 에서도 링크를 뽑는다")
    check(links[0]["url"] == "https://x.kr/a",
          f"상대 주소를 절대 주소로 편다 (실제 {links[0]['url']})")
finally:
    W.requests.get = _real_get
    _bf.get_html = _orig

section("자동 — 그냥 읽어 보고, 목록이 아닐 때만 브라우저로")

# 실측이 뒷받침하는 규칙이다: 브라우저가 더 나쁜 적은 없었고(같거나 나음),
# 대신 3.6배 느리다. 그래서 **필요할 때만** 두 번 읽는다.
_shell = ("메뉴\n로그인\nESG 포털 소개 페이지입니다\n사이트맵 안내 페이지\n"
          "아시아태평양(Asia-Pacific)\n남아프리카(South Africa)", [])
_list = ("\n".join(sum(
    [[f"KSSB 기후공시 소식 제{i}보입니다", "발행일 :", f"2026-08-1{i}"]
     for i in range(1, 5)], [])), [])

_hits = []


def _spy(url, timeout=25, use_browser=False):
    _hits.append("browser" if use_browser else "plain")
    return _list if (use_browser or url.endswith("ok")) else _shell


_real_fetch = W._fetch_page
W._fetch_page = _spy
try:
    import core.browserfetch as _bf
    _avail, _bf.available = _bf.available, lambda: True

    _hits.clear()
    text, links, used = W.fetch_list_page({}, "https://plainly.ok")
    check(_hits == ["plain"], f"잘 읽히면 **한 번만** 읽는다 (실제 {_hits})")
    check(used is False, "브라우저를 안 썼다고 답한다")

    _hits.clear()
    text, links, used = W.fetch_list_page({}, "https://js.kr/list")
    check(_hits == ["plain", "browser"], f"껍데기면 브라우저로 다시 (실제 {_hits})")
    check(used is True and W.looks_like_list(text, links),
          "다시 읽은 쪽이 목록이면 그걸 쓴다")

    _hits.clear()
    W.fetch_list_page({"browser_mode": "never"}, "https://js.kr/list")
    check(_hits == ["plain"], "'쓰지 않음' 이면 껍데기라도 다시 읽지 않는다")

    _hits.clear()
    W.fetch_list_page({"browser_mode": "always"}, "https://plainly.ok")
    check(_hits == ["browser"], "'항상' 이면 그냥 읽기를 건너뛴다")

    # 브라우저가 없는 PC 에서 자동이 죽으면 안 된다
    _bf.available = lambda: False
    _hits.clear()
    text, links, used = W.fetch_list_page({}, "https://js.kr/list")
    check(_hits == ["plain"] and used is False,
          "브라우저가 없으면 그냥 읽은 것으로 끝낸다 (죽지 않는다)")
    _bf.available = _avail
finally:
    W._fetch_page = _real_fetch

check(W.looks_like_list(_list[0], []) is True, "날짜 붙은 항목이 여럿이면 목록")
check(W.looks_like_list(_shell[0], []) is False, "메뉴 줄만 있으면 목록이 아니다")

section("낱말은 메뉴에도 걸린다 (2026-08-16 실측)")

# 회계기준원에 '지속가능성 공시' 로 감시를 걸었더니 새 항목 9건 중 3건이
# **왼쪽 메뉴**였다 — '한국 지속가능성 공시기준 적용지원' 같은 것. 게다가 그
# 항목의 링크로 감시 대상 주소가 박혀 `{page}` 가 그대로 든 못 여는 주소가
# 볼트에 남았다.
_menu_page = "\n".join([
    "한국 지속가능성 공시기준 적용지원",          # ← 메뉴. 낱말에는 걸린다
    "지속가능성 공시기준 제정개정 업무",          # ← 메뉴
    "KSSB 법정공시 시대, GRI 리포트의 운명은", "발행일 :", "2026-08-14",
    "지속가능성 공시 의무화 로드맵 확정", "발행일 :", "2026-08-13",
    "기후공시 시범적용 결과 공개합니다", "발행일 :", "2026-08-12",
])

_pages.clear(); _calls.clear()
_pages["https://m.kr/list?p=1"] = (_menu_page, [
    {"title": "KSSB 법정공시 시대, GRI 리포트의 운명은",
     "url": "https://m.kr/news/1"}])
for i in range(2, 11):
    _pages[f"https://m.kr/list?p={i}"] = ("", [])

_real_fetch = W._fetch_page
W._fetch_page = _fake_fetch
try:
    hits, snap, base = W.check_page(
        {"target": "https://m.kr/list?p={page}", "last_snapshot": "옛 내용",
         "keywords": "지속가능성 공시, 기후공시, KSSB", "browser_mode": "never"},
        {"이미본지문"})
finally:
    W._fetch_page = _real_fetch

titles = [h.title for h in hits]
check(not any("적용지원" in t or "제정개정" in t for t in titles),
      f"메뉴 줄은 새 항목이 아니다 (실제 {titles})")
check(len(hits) == 3, f"날짜가 붙은 기사 3건만 (실제 {len(hits)}건)")
check(all(W.PAGE_TOKEN not in (h.url or "") for h in hits),
      "**{page} 가 든 못 여는 주소가 볼트에 남지 않는다**")
check([h.url for h in hits if "GRI" in h.title] == ["https://m.kr/news/1"],
      "같은 제목의 링크가 있으면 그 주소를 되찾아 붙인다")
check(all(h.url == "" for h in hits if "GRI" not in h.title),
      "못 찾으면 비워 둔다 — 엉뚱한 주소를 지어내지 않는다")

section("자동은 **한 번만** 재고 그 판단을 적어 둔다")

# 안 적어 두면 날짜 없는 목록에서 매 점검마다 두 번씩 읽는다 (그냥 + 브라우저).
# 하루 한 번이면 티가 안 나지만, 열 장짜리 감시면 스무 번 읽는 것이 매일 반복된다.
saved = []


class _SaveStore(_Store):
    def watch_save(self, row):
        saved.append(row)
        return row["watch_id"]

    def watch_mark_checked(self, wid, at_, status, snapshot=None):
        pass


_orig_page = W.check_page


def _page_auto(w, seen):
    w["resolved_browser"] = "always"          # 첫 장에서 브라우저가 낫다고 판정
    return [], "snap", False


W.check_page = _page_auto
try:
    WR.run_watch(_SaveStore(), None,
                 {"watch_id": "w9", "name": "n", "kind": "page",
                  "target": "http://x", "browser_mode": "auto"},
                 now_iso="2026-08-10T10:00:00", send_notify=False)
    check(len(saved) == 1 and saved[0]["browser_mode"] == "always",
          f"정해진 방식이 감시에 적힌다 (실제 {[s.get('browser_mode') for s in saved]})")
    check("resolved_browser" not in saved[0],
          "임시 표시는 저장물에 남기지 않는다")

    saved.clear()
    WR.run_watch(_SaveStore(), None,
                 {"watch_id": "w9", "name": "n", "kind": "page",
                  "target": "http://x", "browser_mode": "never"},
                 now_iso="2026-08-10T10:00:00", send_notify=False)
    check(not saved, "고객이 직접 고른 값은 덮어쓰지 않는다")
finally:
    W.check_page = _orig_page

check(isinstance(BF.available(), bool), "브라우저 유무는 참·거짓으로 답한다")
check(BF.MIN_HTML > 0 and BF.TIMEOUT_SEC >= 30, "빈 화면·무한 대기 방지선이 있다")

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
