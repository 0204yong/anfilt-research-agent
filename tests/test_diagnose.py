"""사이트 진단 회귀 — "여기는 감시가 되는 곳인가" 를 정직하게 답하는가.

    python tests/test_diagnose.py

네트워크를 타지 않는다. 여기서 지키려는 것은 **진단이 거짓말을 하지 않는
것**이다. 진단이 "잘 읽힙니다" 라고 해 놓고 감시가 0건을 돌려주면, 고객은
프로그램을 두 번 믿지 않는다.

실측(2026-08-15)에서 실제로 저지른 오답 셋을 여기에 못 박아 둔다.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-dg-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")
os.environ["RA_EDITION"] = "installed"

from core import diagnose as D, watch as W  # noqa: E402

_fails, _checks = [], 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


def section(t):
    print(f"\n== {t}")


# ---------------------------------------------------------------- 가짜 사이트

PLAIN = {}          # url -> (text, links)
BROWSER = {}


def fake_fetch(url, timeout=25, use_browser=False):
    book = BROWSER if use_browser else PLAIN
    if url not in book:
        raise RuntimeError(f"404 {url}")
    return book[url]


W._fetch_page = fake_fetch


def listing(titles, dates=True, label="발행일 :"):
    """카드형 목록 한 장 — 제목 / 라벨 / 날짜."""
    out = []
    for i, t in enumerate(titles):
        out.append(t)
        if dates:
            out += [label, f"2026-08-{14 - (i % 5):02d}"]
    return "\n".join(out), []


NEWS = [f"KSSB 기후공시 관련 소식 제{i}보입니다" for i in range(10)]
NEWS2 = [f"전혀 다른 둘째 장 기사 제{i}호입니다" for i in range(10)]

# ---------------------------------------------------------------- 1

section("껍데기에 속지 않는가 (ESG Finance Hub 실측)")

# 실제로 저지른 오답: 메뉴 열네 줄을 '항목 14건' 으로 세고 "잘 읽힙니다" 라고
# 답했다. 목록은 **날짜를 찍는다** — 그것이 목록과 껍데기를 가른다.
SHELL = "\n".join(["ESG Finance Hub - ESG 금융플랫폼", "ESG Finance Hub Site Map",
                   "ESG 뉴스 트렌드를 확인할 수 있는 포털입니다",
                   "아시아태평양(Asia-Pacific)", "남아프리카(South Africa)",
                   "라틴아메리카(Latin America)", "ESG 포털 소개 페이지입니다"])
PLAIN["https://js.kr/list"] = (SHELL, [])
BROWSER["https://js.kr/list"] = listing(NEWS)

res = D.diagnose("https://js.kr/list", check_paging=False)
check(res["need_browser"] is True,
      "메뉴만 잡히는 껍데기를 '읽혔다' 로 착각하지 않는다")
check(res["daily"] is True, "브라우저로 열면 매일 감시는 된다고 답한다")
check(any("켜야" in h for _, h, _ in res["lines"]),
      "무엇을 하면 되는지 말해 준다 — 브라우저를 켜라")
icon, one = D.verdict(res)
check("브라우저" in one, f"한 줄 결론에도 나온다 — {one}")

section("멀쩡한 사이트에 괜한 짐을 지우지 않는가")

PLAIN["https://ok.kr/list"] = listing(NEWS)
res = D.diagnose("https://ok.kr/list", check_paging=False)
check(res["need_browser"] is False, "그냥 읽히면 브라우저를 켜라고 하지 않는다")
check(res["daily"] is True, "매일 감시 가능")
check(any("그냥 읽힙니다" in h for _, h, _ in res["lines"]), "그렇게 말해 준다")

section("아예 안 되는 주소")

res = D.diagnose("https://nowhere.kr/list", check_paging=False)
check(res["daily"] is False, "못 여는 주소는 안 된다고 답한다")
check(res["lines"][0][0] == "❌" and "열 수 없" in res["lines"][0][1],
      "이유를 첫 줄에 둔다")
icon, one = D.verdict(res)
check(icon == "❌", "한 줄 결론도 ❌")

PLAIN["https://thin.kr/list"] = ("메뉴\n로그인\n회원가입\n검색", [])
res = D.diagnose("https://thin.kr/list", check_paging=False)
check(res["daily"] is False, "열리긴 해도 목록이 없으면 안 된다고 답한다")

# ---------------------------------------------------------------- 2

section("쪽 넘김 — 주소에 드러나는가")

check(D.page_key_in("https://a.kr/l.do?currentPageNo=3&x=1") == "currentPageNo",
      "주소에 있는 쪽 번호 이름을 찾는다")
check(D.page_key_in("https://a.kr/l.do?menuId=10") == "",
      "menuId 같은 것을 쪽 번호로 오해하지 않는다")
check(D.page_key_in("https://a.kr/portal/news/summaryModal/2021121315") == "",
      "쿼리가 없으면 없다고 답한다")

url = D.with_page_token("https://a.kr/l.do?currentPageNo=3&x=1")
check(url == "https://a.kr/l.do?currentPageNo={page}&x=1",
      f"그 자리에 {{page}} 를 넣는다 (실제 {url})")
check("%7B" not in D.with_page_token("https://a.kr/l.do?page=2"),
      "중괄호가 %7B 로 깨지지 않는다 — 주소창에 그대로 보여야 한다")

section("쪽 넘김 — **정말 넘어가는지** 실측한다")

# 쪽 번호를 붙일 수 있다고 넘어가는 것은 아니다. 무시하고 늘 같은 장을 주는
# 사이트가 흔하다 (회계기준원 실측: currentPageNo=2 가 1쪽과 글자까지 같았다).
PLAIN["https://same.kr/l.do"] = listing(NEWS)
for k in D.TRY_KEYS:
    PLAIN[f"https://same.kr/l.do?{k}=2"] = listing(NEWS)      # 늘 같은 장
res = D.diagnose("https://same.kr/l.do")
check(res["backfill"] is False,
      "쪽 번호를 무시하는 사이트를 '된다' 고 하지 않는다")
check(any("첫 장만" in h for _, h, _ in res["lines"]), "첫 장만 읽힌다고 말해 준다")
check(any("매일 감시는 그대로" in n for _, _, n in res["lines"]),
      "**되는 것까지 안 된다고 하지 않는다** — 매일 감시는 된다고 함께 말한다")

PLAIN["https://real.kr/l.do"] = listing(NEWS)
PLAIN["https://real.kr/l.do?pageIndex=2"] = listing(NEWS2)    # 진짜로 넘어간다
res = D.diagnose("https://real.kr/l.do")
check(res["backfill"] is True, "정말 넘어가면 과거 적재가 된다고 답한다")
check(res["suggest_url"] == "https://real.kr/l.do?pageIndex={page}",
      f"바꿔 넣을 주소를 만들어 준다 (실제 {res['suggest_url']})")
check(W.PAGE_TOKEN in [n for _, _, n in res["lines"] if W.PAGE_TOKEN in n][0],
      "그 주소를 화면 문구에 넣어 준다")

PLAIN["https://has.kr/l.do?currentPageNo=1"] = listing(NEWS)
res = D.diagnose("https://has.kr/l.do?currentPageNo={page}")
check(res["backfill"] is True, "이미 {page} 가 든 주소는 그대로 인정한다")
check(len([1 for _, h, _ in res["lines"] if "이미" in h]) == 1,
      "다시 시험해 보지 않고 넘어간다")

# ---------------------------------------------------------------- 3

section("날짜가 없는 목록")

PLAIN["https://nodate.kr/list"] = listing(NEWS, dates=False)
res = D.diagnose("https://nodate.kr/list", check_paging=False)
check(res["daily"] is True, "날짜가 없어도 매일 감시는 된다")
check(any("날짜가 하나도 안 보입니다" in h for _, h, _ in res["lines"]),
      "다만 그 사실을 숨기지 않는다")

# ---------------------------------------------------------------- 4

section("한 줄 결론")

for flag, want in ((True, "✅"), (False, "⚠️")):
    icon, one = D.verdict({"daily": True, "backfill": flag, "need_browser": False})
    check(icon == want, f"과거 적재 {flag} → {icon} {one}")

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
