"""과거 자료 적재 회귀 — 기간·필터·중단·재개.

    python tests/test_backfill.py

네트워크를 타지 않는다: `_fetch_page` 를 가짜 목록으로 바꾼다. 확인하려는 것은
"페이지를 잘 읽는가"가 아니라 **"어디까지 했는지 잃지 않는가"** 다 — 몇 시간짜리
작업에서 그것이 유일하게 중요한 성질이다.
"""
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-bf-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")
os.environ["RA_EDITION"] = "installed"

from packfix import install_pack  # noqa: E402

install_pack()

from core import backfill as B, watch as W  # noqa: E402
from core.store_local import LocalStore  # noqa: E402

_fails, _checks = [], 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


def section(t):
    print(f"\n== {t}")


# ------------------------------------------------------------------ 기간

section("년/월 범위")

check(B.ym(2022, 1) == "2022-01", "ym()")
s, e = B.ym_range("2022-01", "2022-02")
check(s == date(2022, 1, 1) and e == date(2022, 2, 28),
      "'2월까지' 는 2월 **말일**까지 — 1일로 읽으면 한 달을 통째로 빠뜨린다")
s, e = B.ym_range("2024-02", "2024-02")
check(e == date(2024, 2, 29), "윤년도 말일로 (2024-02-29)")
s, e = B.ym_range("2022-12", "2023-01")
check(e == date(2023, 1, 31), "해를 넘겨도 말일")
check(B.months_between("2022-11", "2023-02")
      == ["2022-11", "2022-12", "2023-01", "2023-02"], "달 목록")

# ------------------------------------------------------------------ 수집

section("수집 — LLM 없이 기간·낱말로 거른다")

PAGES = {}
CALLS = []


def fake_fetch(url, timeout=25, use_browser=False):
    CALLS.append(url)
    if url not in PAGES:
        raise RuntimeError(f"404 {url}")
    return PAGES[url]


_real = W._fetch_page
W._fetch_page = fake_fetch

vault = _SANDBOX / "볼트"
vault.mkdir(parents=True, exist_ok=True)
store = LocalStore(vault)

TARGET = "https://a.kr/list?p={page}"
store.watch_save({"watch_id": "w1", "name": "시험 감시", "kind": "page",
                  "target": TARGET, "created_at": "2026-08-16T09:00:00"})

# 1장 2022-02, 2장 2022-01, 3장 2021-12(기간 밖)
PAGES["https://a.kr/list?p=1"] = (
    "\n".join([
        "2022-02-20 KSSB 초안 공개 — 지속가능성 공시기준",
        "2022-02-10 회사 소풍 사진 모음입니다",
        "2022-02-05 IFRS S2 기후 공시 논의 시작",
    ]), [])
PAGES["https://a.kr/list?p=2"] = (
    "\n".join([
        "2022-01-25 GRI 개정안 의견수렴 개시",
        "2022-01-11 사무실 이전 안내드립니다",
    ]), [])
PAGES["https://a.kr/list?p=3"] = (
    "2021-12-30 옛날 글 — KSSB 관련이지만 기간 밖입니다", [])

job = B.new_job(store, store.watch_get("w1"), "2022-01", "2022-02",
                "KSSB, IFRS S2, GRI", 500, False, "2026-08-16T09:00:00")
check(job["phase"] == "collect", "일감이 수집 단계로 시작한다")

while job["phase"] == "collect":
    job = B.collect_step(store, job)

items = store.backfill_items(job["job_id"])
titles = [i["title"] for i in items]
check(len(items) == 3, f"기간·낱말에 맞는 3건만 담는다 (실제 {len(items)})")
check(all("소풍" not in t and "이전 안내" not in t for t in titles),
      "낱말에 안 걸리는 글은 안 담는다")
check(all("옛날 글" not in t for t in titles), "기간 밖(2021-12)은 안 담는다")
check(any("KSSB" in t for t in titles) and any("GRI" in t for t in titles),
      "두 달치가 모두 담긴다")
check(job["phase"] == "summarize", "다 읽으면 요약 단계로 넘어간다")

# 같은 장을 다시 읽어도 두 번 담기지 않는다 (재개할 때 겹친다)
again = store.backfill_add_items(job["job_id"], [{
    "fingerprint": items[0]["fingerprint"], "title": items[0]["title"],
    "url": "", "published": ""}])
check(again == 0, "이미 담은 지문은 다시 담지 않는다 — 재개해도 중복이 없다")

section("상한과 안전장치")

job2 = B.new_job(store, store.watch_get("w1"), "2022-01", "2022-02", "", 2,
                 False, "2026-08-16T09:00:00")
while job2["phase"] == "collect":
    job2 = B.collect_step(store, job2)
check(store.backfill_count(job2["job_id"]) >= 2 and "상한" in job2["status"],
      f"상한에 닿으면 멈추고 그렇게 말한다 — {job2['status']}")

check("{page}" in B.pages_hint({"target": "https://a.kr/list"}),
      "주소에 {page} 가 없으면 첫 장만 읽는다고 미리 알린다")
check(B.pages_hint({"target": TARGET}) == "", "있으면 잔소리하지 않는다")

section("제목과 날짜가 다른 줄에 있는 목록 (카드형)")

# 실측(ESG Finance Hub 를 브라우저로 읽은 DOM): 제목 한 줄, 그 아래 날짜 한 줄.
# 얹어 주지 않으면 제목은 기간에 안 걸리고 날짜는 낱말에 안 걸린다.
CARD = "\n".join([
    "신협, 폐전자제품 재활용 ESG 자원순환 캠페인",
    "발행일 :",                               # ← 실제 DOM 에 끼어 있는 라벨
    "2026-08-14",
    "링크 :",
    "라이센스뉴스",
    "KSSB 기후공시 기준 초안 공개",
    "발행일 :",
    "2026-08-13",
    "메뉴",                                   # 짧은 줄은 그냥 버린다
])
paired = {r["title"]: d for r, d in B._rows(CARD, [])}
check(str(paired.get("KSSB 기후공시 기준 초안 공개")) == "2026-08-13",
      f"아래 줄의 날짜를 제목에 얹는다 (실제 {paired.get('KSSB 기후공시 기준 초안 공개')})")
check(str(paired.get("신협, 폐전자제품 재활용 ESG 자원순환 캠페인")) == "2026-08-14",
      "첫 항목도 마찬가지")
check(not any(_DO := t.strip().startswith("2026-") for t in paired),
      "날짜만 있는 줄이 항목으로 남지 않는다")

# 날짜가 한 줄에 같이 있는 예전 목록은 그대로 동작해야 한다
same = {r["title"]: d for r, d in B._rows("2022-02-20 KSSB 초안 공개합니다", [])}
check(len(same) == 1 and str(list(same.values())[0]) == "2022-02-20",
      "한 줄에 같이 있는 목록은 예전과 똑같이 읽는다")

# 날짜를 찍는 목록이면 날짜 없는 긴 줄은 기사가 아니다 (메뉴·안내문)
PAGES["https://a.kr/list?p=1"] = ("\n".join([
    "ESG 뉴스 트렌드를 확인할 수 있는 포털입니다",      # 안내문 — 날짜 없음
    "KSSB 기후공시 기준 초안 공개", "발행일 :", "2022-02-20",
    "GRI 개정안 의견수렴 개시", "발행일 :", "2022-02-15",
    "IFRS S2 기후 공시 논의 시작", "발행일 :", "2022-02-10",
]), [])
PAGES.pop("https://a.kr/list?p=2", None)
PAGES.pop("https://a.kr/list?p=3", None)
jobc = B.new_job(store, store.watch_get("w1"), "2022-02", "2022-02", "",
                 500, False, "2026-08-16T09:00:00")
while jobc["phase"] == "collect":
    jobc = B.collect_step(store, jobc)
got = [i["title"] for i in store.backfill_items(jobc["job_id"])]
check(len(got) == 3, f"기사 3건만 담는다 (실제 {len(got)}건: {got[:4]})")
check(not any("포털입니다" in t for t in got),
      "날짜 없는 안내문은 볼트에 안 들어간다")

section("깊이 — 감시의 10장 상한에 갇히면 안 된다")

# 감시는 MAX_PAGES(=10) 에서 멈추는 것이 맞다. 하지만 적재는 2022년을 찾으러
# 수백 장을 내려가야 한다 — 같은 상한을 쓰면 열 장 읽고 "0건"을 돌려준다.
DEEP = "https://deep.kr/list?p={page}"
store.watch_save({"watch_id": "w2", "name": "깊은 목록", "kind": "page",
                  "target": DEEP, "created_at": "2026-08-16T09:00:00"})
for p in range(1, 41):                     # 1~30장은 2026년, 31장부터 2022-01
    when = "2026-08-01" if p <= 30 else "2022-01-15"
    PAGES[f"https://deep.kr/list?p={p}"] = (f"{when} KSSB 제{p}호 글입니다", [])

deep = B.new_job(store, store.watch_get("w2"), "2022-01", "2022-01",
                 "KSSB", 500, False, "2026-08-16T09:00:00")
while deep["phase"] == "collect":
    deep = B.collect_step(store, deep)
check(int(deep["page"]) > W.MAX_PAGES,
      f"{W.MAX_PAGES}장을 넘어 계속 내려간다 (실제 {deep['page']}장까지)")
check(store.backfill_count(deep["job_id"]) > 0,
      f"31장째의 2022-01 을 찾아낸다 (실제 {store.backfill_count(deep['job_id'])}건)")

# 화면이 "멈춘 건가"로 읽히지 않으려면, 어디까지 내려왔는지가 남아야 한다
mid = B.new_job(store, store.watch_get("w2"), "2022-01", "2022-01",
                "KSSB", 500, False, "2026-08-16T09:00:00")
mid = B.collect_step(store, mid)
check(mid.get("cursor") == "2026-08-01",
      f"한 장 읽을 때마다 어느 날짜까지 왔는지 적는다 (실제 {mid.get('cursor')!r})")
check("아직 기간 전" in mid["status"],
      f"기간에 닿기 전이라 0건인 것을 말로 알린다 — {mid['status']}")
check(dict(store.backfill_get(mid["job_id"]))["cursor"] == "2026-08-01",
      "그 자리가 디스크에도 남는다 — 껐다 켜도 '어디까지'를 잃지 않는다")

# ------------------------------------------------------------------ 요약·재개

section("요약 — 달별 묶음 · 중단하고 이어하기")

ch = B.chunks(store, job)
check([m for m, _, _ in ch] == ["2022-01", "2022-02"],
      f"달별로 묶는다 (실제 {[m for m, _, _ in ch]})")


class FakeProvider:
    label, model = "가짜", "fake"
    calls = 0

    def generate_json(self, prompt, schema=None):
        FakeProvider.calls += 1
        return {"headline": "시험 요약", "summary": "요약 본문",
                "items": [{"title": "항목", "url": "", "what_is_new": "새 내용",
                           "why_it_matters": "중요한 이유", "importance": 4}]}


prov = FakeProvider()
job = B.summarize_step(store, prov, job, "2026-08-16T09:00:00")
pr = B.progress(store, job)
check(pr["done"] >= 1 and pr["left_chunks"] == 1,
      f"묶음 하나만 처리하고 멈춘다 (완료 {pr['done']} · 남음 {pr['left_chunks']})")

# 여기서 앱이 죽었다고 치자 — 새 연결로 이어받는다
store.close()
store2 = LocalStore(vault)
job_again = store2.backfill_get(job["job_id"])
check(job_again["phase"] == "summarize" and job_again["job_id"] == job["job_id"],
      "**앱을 껐다 켜도 일감이 남아 있다**")
check(B.progress(store2, job_again)["done"] == pr["done"],
      "완료한 묶음은 다시 하지 않는다")

before = FakeProvider.calls
while job_again["phase"] != "done":
    job_again = B.summarize_step(store2, prov, job_again, "2026-08-16T10:00:00")
check(FakeProvider.calls - before == 1,
      f"남은 묶음만큼만 LLM 을 부른다 (실제 {FakeProvider.calls - before}회)")
check(B.progress(store2, job_again)["left_chunks"] == 0, "남은 묶음 없음")

files = store2.vault_list()
notes = sorted(p for p in files if p.startswith("backfill/"))
check(len(notes) == 2, f"달마다 노트 하나 (실제 {len(notes)}개: {notes})")
check(any("2022-01" in p for p in notes) and any("2022-02" in p for p in notes),
      "노트 이름에 달이 들어간다")
body = files[notes[0]]
check("type: backfill" in body and "미검증" in body,
      "적재 노트에도 미검증 라벨이 붙는다")
check("## 원본 링크" in body, "원본 링크가 남는다")

seen = store2.watch_seen_fingerprints("w1")
check(len(seen) >= 3,
      "적재한 항목은 감시의 '이미 본 것' 에도 들어간다 — 내일 또 알리지 않는다")

W._fetch_page = _real
store2.close()
shutil.rmtree(_SANDBOX, ignore_errors=True)

print("\n" + "=" * 60)
if _fails:
    print(f"실패 {len(_fails)}/{_checks}")
    for f in _fails:
        print(f"  - {f}")
else:
    print(f"전부 통과 — {_checks}항목")
sys.exit(1 if _fails else 0)
