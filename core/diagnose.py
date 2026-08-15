"""사이트 진단 — "여기는 감시가 되는 곳인가"를 **미리** 답한다.

→ docs/15 자동 모니터링과 알림. Streamlit 비의존.

## 왜 필요한가

사이트마다 되는 것과 안 되는 것이 다르다. 지금까지 고객이 그것을 아는 길은
하나뿐이었다 — 감시를 걸고, 하루를 기다리고, 0건을 받는 것. 그러고도 **왜**
0건인지는 알 수 없다. 사이트가 원래 조용한 것인지, 낱말이 너무 좁은 것인지,
목록을 자바스크립트로 그려서 아예 못 읽은 것인지 구분이 안 된다.

셋은 대응책이 전혀 다르다. 그래서 진단이 필요하다.

## 무엇을 보는가 — 네 가지

    1. 읽히는가        그냥 읽어서 되나, 브라우저를 켜야 하나, 둘 다 안 되나
    2. 항목이 잡히는가  목록의 줄·링크가 잡히나
    3. 날짜가 읽히는가  '지난 이레치' 를 끊으려면 날짜가 있어야 한다
    4. 쪽이 넘어가는가  주소에 쪽 번호가 드러나나 — 과거 적재의 전제다

이 넷이 **매일 감시**와 **과거 적재**의 가부를 가른다. 둘은 요구 조건이
다르다: 매일 감시는 첫 장만 읽혀도 되지만, 과거 적재는 쪽을 넘겨야 한다.

## 태도

**추측해서 되는 척하지 않는다.** 안 되는 것은 안 된다고 말하고, 대신 무엇을
하면 되는지 말한다 — 브라우저를 켜라, 주소의 쪽 번호를 `{page}` 로 바꿔라,
이 사이트는 과거 적재가 안 되니 원문 매체를 대신 걸어라.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import browserfetch, watch as W

# 국내 게시판이 쪽 번호에 쓰는 이름들. 전자정부 표준프레임워크(pageIndex)가
# 가장 흔하고, 그다음이 currentPageNo·page 다.
PAGE_KEYS = ("pageindex", "currentpageno", "pageno", "page", "cpage",
             "nowpage", "startpage", "pagenum", "p", "offset", "start")

# 주소에 쪽 번호가 아예 없을 때 **붙여서 시험해 볼** 후보들
TRY_KEYS = ("pageIndex", "currentPageNo", "page", "pageNo")

MIN_ITEM_LINE = 15        # 목록 한 항목으로 볼 최소 글자 수
ENOUGH_ITEMS = 5          # 이만큼 잡히면 '목록이 읽혔다'
ENOUGH_DATES = 3          # 이만큼 날짜가 잡히면 '날짜를 찍는 목록'


def probe(url: str, use_browser: bool = False) -> dict:
    """한 번 읽고 **숫자로** 답한다. 실패해도 예외를 내지 않는다.

    세는 기준이 `backfill._rows` 와 **같아야** 한다. 진단이 "38건 잡힙니다"
    라고 해 놓고 실제 감시가 3건만 담으면 진단이 거짓말을 한 것이 된다.

    그리고 **날짜가 붙은 항목**을 따로 센다. 이것이 목록인지 아닌지를 가르는
    가장 정직한 신호다 — 그냥 줄 수는 메뉴·안내문에 쉽게 속는다 (실측:
    ESG Finance Hub 의 껍데기에서도 '항목' 이 14줄 잡혔다. 전부 메뉴였다).
    """
    from . import backfill as B

    out = {"ok": False, "error": "", "chars": 0, "links": 0,
           "items": 0, "dated": 0, "newest": None, "sample": []}
    try:
        text, links = W._fetch_page(url, use_browser=use_browser)
    except Exception as e:                       # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    rows = [(r["title"], d) for r, d in B._rows(text, links)]
    dated = [(t, d) for t, d in rows if d]
    out.update(ok=True, chars=len(text), links=len(links),
               items=len(rows), dated=len(dated),
               newest=(max(d for _, d in dated) if dated else None),
               sample=[t for t, _ in (dated or rows)][:5])
    return out


# ------------------------------------------------------------------ 쪽 넘김

def page_key_in(url: str) -> str:
    """주소에 이미 있는 쪽 번호 이름. 없으면 빈 문자열."""
    for k, v in parse_qsl(urlparse(str(url or "")).query):
        if k.lower() in PAGE_KEYS and str(v).strip().isdigit():
            return k
    return ""


def with_page_token(url: str, key: str = "") -> str:
    """그 자리에 `{page}` 를 넣은 주소. 없는 이름이면 붙여서 만든다."""
    parts = urlparse(str(url or ""))
    q = parse_qsl(parts.query)
    key = key or page_key_in(url) or TRY_KEYS[0]
    # 있으면 그 자리를 바꾸고, 없으면 **붙인다.** 쿼리가 아예 없는 주소에
    # '바꾸기' 만 하면 조용히 아무것도 안 한 주소가 나온다.
    if any(k == key for k, _ in q):
        q = [(k, W.PAGE_TOKEN if k == key else v) for k, v in q]
    else:
        q = q + [(key, W.PAGE_TOKEN)]
    # urlencode 가 중괄호를 %7B 로 바꾸므로 되돌린다 — 주소창에 보일 모양이어야 한다
    query = urlencode(q).replace("%7Bpage%7D", W.PAGE_TOKEN)
    return urlunparse(parts._replace(query=query))


def _fingerprint(items: list) -> set:
    return {re.sub(r"\s+", "", t)[:60] for t in items}


def try_paging(url: str, use_browser: bool, first: dict) -> dict:
    """2쪽이 **정말 다른 내용**인지 실측한다.

    주소를 만들 수 있다고 넘어가지는 것은 아니다. 쪽 번호를 무시하고 늘 같은
    첫 장을 주는 사이트가 흔하다 — 그걸 모르고 적재를 돌리면 같은 열 건을
    수백 번 담는다. 그래서 **내용이 바뀌는지**까지 본다.
    """
    base = _fingerprint(first.get("sample") or [])
    key = page_key_in(url)
    tries = [key] if key else list(TRY_KEYS)
    for k in tries:
        cand = with_page_token(url, k)
        second = probe(cand.replace(W.PAGE_TOKEN, "2"), use_browser)
        if not second["ok"] or second["items"] < ENOUGH_ITEMS:
            continue
        got = _fingerprint(second["sample"])
        if base and got and len(base & got) <= len(base) // 2:
            return {"works": True, "key": k, "url": cand,
                    "found": "주소에 있던 쪽 번호" if key else "붙여 본 쪽 번호"}
    return {"works": False, "key": key, "url": with_page_token(url, key) if key else "",
            "found": ""}


# ------------------------------------------------------------------ 진단

def diagnose(url: str, check_paging: bool = True) -> dict:
    """사람이 읽을 판정을 돌려준다.

    반환의 `lines` 는 화면에 그대로 뿌릴 문장들이다 — (아이콘, 제목, 설명).
    """
    raw = str(url or "").strip()
    bare = raw.replace(W.PAGE_TOKEN, "1")        # 진단은 늘 1쪽으로 한다
    res = {"url": raw, "lines": [], "daily": False, "backfill": False,
           "need_browser": False, "suggest_url": "", "browser_available":
           browserfetch.available()}

    plain = probe(bare, False)
    best, mode = plain, "plain"

    # 그냥 읽어서 **날짜 붙은 항목**이 안 나오면 브라우저로 한 번 더.
    # 줄 수로 판단하면 안 된다 — 메뉴 열네 줄에 속아 "잘 읽힙니다" 라고
    # 답하게 된다 (ESG Finance Hub 실측). 목록은 날짜를 찍는다.
    if res["browser_available"] and (plain["dated"] < ENOUGH_DATES
                                     or plain["items"] < ENOUGH_ITEMS):
        deep = probe(bare, True)
        better = (deep["dated"], deep["items"]) > (plain["dated"], plain["items"])
        if better:
            best, mode = deep, "browser"
            res["need_browser"] = True

    res["probe"] = best
    res["mode"] = mode

    # 1) 읽히는가
    if not best["ok"]:
        res["lines"].append(("❌", "페이지를 열 수 없습니다", best["error"]))
        return res
    if best["items"] < ENOUGH_ITEMS:
        res["lines"].append((
            "❌", f"목록을 찾지 못했습니다 (읽은 글 {best['chars']:,}자)",
            "로그인이 필요하거나, 목록이 아닌 페이지이거나, 저희가 못 읽는 방식으로 "
            "그리는 곳입니다. 목록·공지 페이지 주소가 맞는지 확인해 주세요."
            + ("" if res["browser_available"] else
               " (이 PC 에 Edge·Chrome 이 없어 브라우저로는 시험하지 못했습니다)")))
        return res

    if mode == "browser":
        res["lines"].append((
            "💡", "브라우저로 읽기를 **켜야** 합니다",
            f"그냥 읽으면 {plain['chars']:,}자에 날짜 붙은 항목 {plain['dated']}건인데, "
            f"브라우저로 열면 {best['chars']:,}자에 {best['dated']}건입니다 — "
            "목록을 자바스크립트로 그리는 곳입니다."))
    else:
        res["lines"].append((
            "✅", "그냥 읽힙니다", "브라우저를 켤 필요가 없습니다 — 그만큼 빠릅니다."))

    # 2) 항목과 날짜 — 하나로 묶어 말한다. 고객이 세는 단위는 '기사 몇 건' 이다
    if best["dated"] >= ENOUGH_DATES:
        res["lines"].append((
            "✅", f"기사 {best['dated']}건이 날짜와 함께 잡힙니다 (최신 {best['newest']})",
            "'지난 이레치' 처럼 기간으로 끊을 수 있습니다.\n\n"
            + "\n".join(f"· {t[:44]}" for t in best["sample"][:3])))
    else:
        # 날짜가 하나도 없으면 **이게 목록인지 확신할 수 없다.** 잡힌 줄이
        # 기사일 수도, 메뉴일 수도 있다. 되는 척도 안 되는 척도 하지 않고,
        # 무엇을 보고 판단하면 되는지 알려 준다.
        res["lines"].append((
            "⚠️", f"줄 {best['items']}건은 잡히는데 **날짜가 하나도 안 보입니다**",
            "그래서 이 줄들이 기사인지 메뉴인지 저희가 확인하지 못했습니다. "
            "감시는 걸 수 있지만 두 가지를 알아 두세요 — 기간으로 끊지 못해 "
            "'전에 못 보던 줄' 로만 가려내고, 과거 적재의 기간 지정도 헐거워집니다. "
            "**등록한 뒤 '지금 점검' 을 한 번 눌러 결과를 확인하세요.** 목록에 "
            "등록일이 함께 보이는 주소가 따로 있다면 그쪽이 훨씬 낫습니다.\n\n"
            + "\n".join(f"· {t[:44]}" for t in best["sample"][:3])))

    res["daily"] = True

    # 3) 쪽 넘김 — 과거 적재의 전제
    if not check_paging:
        return res
    if W.PAGE_TOKEN in raw:
        res["backfill"] = True
        res["lines"].append(("✅", "쪽 번호가 이미 들어 있습니다",
                             "과거 자료 적재를 쓸 수 있습니다."))
        return res

    pg = try_paging(bare, mode == "browser", best)
    if pg["works"]:
        res["backfill"] = True
        res["suggest_url"] = pg["url"]
        res["lines"].append((
            "🔧", f"쪽을 넘길 수 있습니다 ({pg['found']}: {pg['key']})",
            f"감시 대상 주소를 아래로 바꾸면 과거 적재까지 됩니다.\n\n`{pg['url']}`"))
    else:
        res["lines"].append((
            "⚠️", "과거 자료 적재는 안 됩니다 — 첫 장만 읽힙니다",
            "쪽 번호가 주소에 드러나지 않아 2쪽을 지정할 방법이 없습니다. "
            "**매일 감시는 그대로 됩니다** (첫 장에 새 글이 올라오므로). "
            "과거를 쌓아야 한다면 이 사이트가 인용하는 원문 매체를 대신 거세요."))
    return res


def verdict(res: dict) -> tuple:
    """(아이콘, 한 줄 결론) — 표에 넣을 요약."""
    if not res.get("daily"):
        return "❌", "감시할 수 없는 주소입니다"
    head = "브라우저로 읽으면 " if res.get("need_browser") else ""
    if res.get("backfill"):
        return "✅", f"{head}매일 감시·과거 적재 모두 됩니다"
    return "⚠️", f"{head}매일 감시는 되고, 과거 적재는 안 됩니다"
