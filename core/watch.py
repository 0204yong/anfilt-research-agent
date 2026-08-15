"""자동 모니터링 — 사이트·키워드를 정기 점검해 '새로운 내용'만 잡아낸다.

→ docs/15 자동 모니터링과 알림. Streamlit 비의존 (감시 CLI가 이 모듈만 쓴다).

두 가지 감시 종류:
- `page`    : 특정 페이지를 가져와 ① 새로 생긴 링크 ② 본문 증가분을 잡는다.
- `keyword` : 경량 LLM의 웹 검색으로 최근 항목을 찾고 URL로 중복을 거른다.

설계 원칙:
1. **'새로움'은 지문(fingerprint)으로 정의한다.** LLM에게 "새로운가?"를 묻지
   않는다 — 판정이 흔들리고 비용이 든다. 코드가 URL/콘텐츠 해시로 판정하고,
   LLM은 이미 새롭다고 확정된 것의 요약만 맡는다.
2. **첫 실행은 기준선(baseline)이다.** 처음 보는 감시 대상의 기존 항목 전부를
   '새 소식'으로 알리면 알림이 무의미해지므로, 첫 회는 지문만 적재하고 조용히
   끝낸다.
3. **알림 실패가 축적을 막지 않는다.** 볼트 반영 → 지문 기록 → 알림 순서로,
   앞 단계가 끝난 뒤에 알림을 시도한다.
"""
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from . import packs
from .vault_render import _safe_filename
from .webfetch import MAX_CHARS_PER_URL, _HEADERS

KST = timezone(timedelta(hours=9))

KINDS = {"page": "특정 페이지 변경 감시", "keyword": "키워드 검색 감시"}

MAX_LINKS = 300          # 페이지에서 추적할 링크 상한
MAX_HITS = 8             # 한 번에 요약·알림할 새 항목 상한 (비용·가독성) — 기본값
MAX_HITS_LIMIT = 50      # 감시별로 올리더라도 여기까지 (요약 한 번에 담기는 양)


def max_hits(watch: dict) -> int:
    """이 감시가 한 번에 요약·알림할 새 항목 수.

    하나로 묶어 두면 뉴스 매체(하루 20건)와 공지 게시판(주 1건)이 같은 값을
    쓴다. 매체 쪽은 잘리고, 게시판 쪽은 쓸데없이 큰 요약을 부른다.
    넘친 항목이 사라지지는 않는다 — 볼트 노트의 '원본 링크'에는 전부 남는다.
    """
    try:
        n = int(watch.get("max_hits") or MAX_HITS)
    except (TypeError, ValueError):
        return MAX_HITS
    return min(max(n, 1), MAX_HITS_LIMIT)
# 검색·요약 스키마는 프롬프트 팩에서 온다 (→ docs/22 7절).
_PACK_ATTRS = {
    "SEARCH_SCHEMA": lambda: packs.schema("watch_search"),
    "DIGEST_SCHEMA": lambda: packs.schema("watch_digest"),
}


def __getattr__(name):
    if name in _PACK_ATTRS:
        return _PACK_ATTRS[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


MAX_SEARCH_ITEMS = 10    # 키워드 검색이 가져올 후보 상한
MIN_DIFF_CHARS = 200     # 본문 증가분을 '변경'으로 볼 최소 글자 수
# 목록이 여러 장으로 나뉜 사이트에서 **몇 장까지 넘겨 볼 것인가.**
#
# 1장만 보면 조용히 놓친다. 목록에 여러 분류가 섞여 있으면 관심 항목이 다음
# 장으로 밀리고, PC 를 며칠 꺼 뒀다 켜면 그 사이 올라온 것이 1장을 넘긴다.
# 그렇다고 859장을 매번 훑을 수는 없다 — **겹칠 때까지만** 넘긴다.
MAX_PAGES = 10
PAGE_TOKEN = "{page}"    # 주소에 이걸 넣으면 넘긴다 (없으면 예전처럼 1장)
MIN_LINK_TEXT = 6        # 링크 텍스트가 이보다 짧으면 메뉴·아이콘으로 보고 무시


@dataclass
class WatchHit:
    """새롭다고 확정된 항목 1건."""
    fingerprint: str
    title: str
    url: str = ""
    excerpt: str = ""


@dataclass
class WatchResult:
    watch_id: str
    name: str
    hits: list = field(default_factory=list)     # [WatchHit]
    digest: dict = field(default_factory=dict)   # 요약 JSON (hits 있을 때만)
    note_path: str = ""                          # 볼트에 쌓인 노트 경로
    baseline: bool = False                       # 첫 실행(기준선 수집)
    status: str = ""
    error: str = ""

    @property
    def has_news(self) -> bool:
        return bool(self.hits) and not self.baseline


# ---------------------------------------------------------------- 공통 유틸


def new_watch_id(now_iso: str) -> str:
    stamp = re.sub(r"[^0-9]", "", now_iso)[:14]
    return f"watch-{stamp}-{uuid.uuid4().hex[:6]}"


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_hours(hours: str) -> list:
    """'08,18' → [8, 18]. 잘못된 값은 버리고, 비면 [8]로 본다."""
    out = []
    for part in str(hours or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23 and int(part) not in out:
            out.append(int(part))
    return sorted(out) or [8]


def every_days(watch: dict) -> int:
    """며칠에 한 번 돌 것인가. 1 = 매일 (예전 동작).

    시각만 있고 '일' 단위가 없으면 **매일 도는 것 말고는 선택지가 없었다.**
    주 1회면 충분한 감시(분기 보고서, 월간 동향)를 매일 돌리면 비용도 알림도
    낭비다 — 키워드 감시는 한 번에 경량 모델 2회를 부른다.
    """
    try:
        n = int(watch.get("every_days") or 1)
    except (TypeError, ValueError):
        return 1
    return min(max(n, 1), 365)


def _parse_ts(value: str):
    """Supabase timestamptz 문자열 → KST datetime (실패 시 None)."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    # 마이크로초 자릿수가 6자리를 넘으면 fromisoformat이 거부한다 → 잘라준다
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def due_slot(watch: dict, now: datetime = None):
    """오늘 이미 지나간 예정 시각 중 **가장 늦은 것**. 아직 없으면 None.

    예) 시각 08,18 · 지금 19:30 → 오늘 18:00
        시각 08    · 지금 07:00 → None (오늘은 아직 차례가 안 왔다)
    """
    now = now or now_kst()
    passed = [h for h in parse_hours(watch.get("hours")) if h <= now.hour]
    if not passed:
        return None
    return now.replace(hour=max(passed), minute=0, second=0, microsecond=0)


def is_due(watch: dict, now: datetime = None) -> bool:
    """지금 실행할 차례인가 — **놓친 시각을 그날 안에 따라잡는다.**

    예전에는 `now.hour` 가 예정 시각과 **정확히 일치**할 때만 True 였다.
    상시 켜져 있는 서버(GitHub Actions)에는 맞지만, **PC 는 꺼져 있다.**
    08시 감시를 걸어 둔 사람이 10시에 PC를 켜면 그날은 영영 건너뛰었다
    (→ docs/23 8단계).

    지금 규칙: **오늘 지나간 마지막 예정 시각 이후로 아직 안 돌았으면 실행한다.**

      08시 감시 · 10시에 켬  → 오늘 08:00 이후 기록 없음 → 실행 (따라잡기)
      같은 감시 · 11시       → 10시에 돌았으므로 08:00 이후 → 실행 안 함 (중복 방지)
      08,18시 감시 · 19시    → 오늘 18:00 이후 기록 없음 → 실행

    **어제 것까지 따라잡지는 않는다.** 새벽 1시에 갑자기 도는 것보다,
    "그날의 예정 시각이 지났는데 아직 안 돌았으면 돈다"가 설명하기 쉽고
    예측 가능하다. 며칠 꺼져 있었어도 켠 뒤 첫 예정 시각에 한 번만 돈다.

    ⚠️ 이 판단은 `last_checked_at` 에 전적으로 기댄다 — 점검이 끝나면 결과와
    무관하게 반드시 기록되어야 한다 (→ watch_runner `_mark`).
    """
    if not watch.get("enabled", True):
        return False
    now = now or now_kst()
    slot = due_slot(watch, now)
    if slot is None:
        return False
    last = _parse_ts(watch.get("last_checked_at"))
    if last is None:
        return True
    if last >= slot:
        return False
    # '며칠에 한 번' — 날짜로 센다. 시각으로 세면 08시 감시가 3일 주기일 때
    # 마지막이 3일 전 18시였다는 이유로 하루를 더 밀린다. 사람은 "3일마다
    # 아침에"로 이해하지, 72시간 뒤로 이해하지 않는다.
    days = every_days(watch)
    if days > 1 and (now.date() - last.date()).days < days:
        return False
    return True


def normalize_url(url: str) -> str:
    """추적 파라미터·프래그먼트·꼬리 슬래시를 없앤 비교용 URL."""
    try:
        p = urlparse(str(url).strip())
    except ValueError:
        return str(url).strip()
    if not p.scheme:
        return str(url).strip().rstrip("/").casefold()
    query = "&".join(
        q for q in p.query.split("&")
        if q and not q.split("=")[0].lower().startswith(("utm_", "fbclid", "gclid"))
    )
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", query, ""))


def fingerprint_url(url: str) -> str:
    return "u:" + hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:20]


def fingerprint_text(text: str) -> str:
    norm = re.sub(r"\s+", " ", str(text)).strip()
    return "t:" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:20]


def has_deep_path(url: str) -> bool:
    """개별 문서 주소인가 (도메인 루트가 아닌가).

    검색 LLM이 기사 대신 언론사 홈 주소만 주는 일이 잦다(실측). 그 URL로 지문을
    만들면 **같은 매체의 다음 기사가 전부 '이미 본 것'이 되어 조용히 누락된다.**
    루트면 제목으로 지문을 만들어 이 함정을 피한다.
    """
    try:
        p = urlparse(str(url))
    except ValueError:
        return False
    return bool(p.scheme and (p.path.strip("/") or p.query))


def md_link_text(text: str) -> str:
    """마크다운 링크 라벨용 — 대괄호를 없앤다.

    제목이 "[기사] …"이면 `[[기사] …](url)`이 되어 링크가 깨지고, Obsidian에서는
    `[[…]]` 위키링크로 오인된다(실측).
    """
    return str(text).replace("[", "(").replace("]", ")")


# ------------------------------------------------------------ page 감시


def _fetch_page(url: str, timeout: int = 25) -> tuple:
    """(본문 텍스트, [{title, url}]) — 링크는 절대 URL로 정규화해 돌려준다."""
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    if "pdf" in resp.headers.get("content-type", ""):
        raise RuntimeError("PDF 문서는 페이지 감시 대상이 될 수 없습니다 (본문 추출 미지원)")

    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(separator=" ").split())
        href = a["href"].strip()
        if len(text) < MIN_LINK_TEXT or href.startswith(
            ("#", "javascript:", "mailto:", "tel:")
        ):
            continue
        absolute = urljoin(resp.url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        key = normalize_url(absolute)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        links.append({"title": text[:200], "url": absolute})
        if len(links) >= MAX_LINKS:
            break

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    # ⚠️ 줄 구조를 **지운 채로** 두면 안 된다. 예전에는 여기서
    #       text = " ".join(soup.get_text(separator="\n").split("\n"))
    # 로 모든 줄을 공백으로 이어 붙였다 — 본문이 통째로 한 줄이 된다.
    # 그러면 `_added_lines` 의 줄 집합 비교가 전부 아니면 전무가 되어, 조회수
    # 하나만 바뀌어도 **페이지 전체가 '새 줄'** 이 되고, 요약 LLM 은 메뉴까지
    # 뒤섞인 5천 자를 받는다. 목록 한 행이 한 줄로 남아야 '새로 올라온 행'만
    # 골라낼 수 있다 (2026-08-15 6개 사이트 실측에서 드러났다).
    text = "\n".join(
        l.strip() for l in soup.get_text(separator="\n").split("\n") if l.strip()
    )

    if not text and not links:
        # JS로 본문을 그리는 페이지(SPA)는 requests로는 빈 껍데기만 온다.
        # 조용히 '변경 없음'으로 두면 감시가 영원히 아무것도 못 잡으므로 알린다.
        raise RuntimeError(
            "이 페이지는 본문을 자바스크립트로 그려서 수집할 수 없습니다 "
            "(빈 HTML). RSS 주소나 목록 페이지를 대신 등록하거나, "
            "키워드 감시로 바꿔 보세요."
        )
    return text[:MAX_CHARS_PER_URL], links


def _added_lines(old: str, new: str) -> list:
    """직전 본문에 없던 줄들 (집합 비교 — 순서 바뀜을 변경으로 오인하지 않는다)."""
    def norm(s):
        return re.sub(r"\s+", " ", s).strip()

    old_set = {norm(l) for l in str(old).split("\n") if norm(l)}
    out = []
    for line in str(new).split("\n"):
        n = norm(line)
        if len(n) >= 15 and n not in old_set:
            out.append(n)
    return out


IMPORTANCE_LEVELS = 5     # 1~5. 사이값이 없다 — 정의한 만큼만 쓴다


def importance_scale() -> list:
    """1~5 각 단계의 뜻. 낮은 쪽부터 다섯 줄.

    기본 문장은 팩에서 오고, 고객이 설정에서 고치면 그쪽을 쓴다.
    **고객마다 5점의 뜻이 다르다** — 규제 감시와 경쟁사 동향 감시가 같은
    잣대를 쓸 이유가 없다. 대신 줄 수는 다섯으로 고정한다: 일곱 줄을 적으면
    스키마도 정렬도 어긋나므로, 고칠 수 있는 것은 **설명뿐**이다.

    구간(7~8)이 아니라 낱값인 이유: 구간을 쓰면 7과 8의 차이가 여전히
    정의되지 않는다. 지금 없애려는 것이 바로 그 빈칸이다.
    """
    try:
        base = list(packs.conf("watch_importance_scale") or [])
    except Exception:                            # noqa: BLE001 — 팩이 없어도 화면은 떠야 한다
        base = []
    base = (base + [""] * IMPORTANCE_LEVELS)[:IMPORTANCE_LEVELS]
    try:
        from . import settings
        custom = list(settings.load().get("watch_importance_scale") or [])
    except Exception:                            # noqa: BLE001
        custom = []
    custom = (custom + [""] * IMPORTANCE_LEVELS)[:IMPORTANCE_LEVELS]
    # 빈 칸은 기본 문장으로 — 한 줄만 고치고 나머지는 그대로 두고 싶을 때
    return [(c or b or f"{i + 1}단계").strip()
            for i, (c, b) in enumerate(zip(custom, base))]


def scale_block() -> str:
    """프롬프트에 넣을 척도 — 높은 쪽부터 읽는 것이 사람 눈에 자연스럽다."""
    lines = importance_scale()
    return "\n".join(f"- **{n}**: {lines[n - 1]}"
                     for n in range(IMPORTANCE_LEVELS, 0, -1))


def clamp_importance(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 3                                  # 판단 불가 — 가운데로 둔다
    return max(1, min(n, IMPORTANCE_LEVELS))


BODY_LIMIT_MAX = 30       # 한 번에 원문을 열어 볼 수 있는 최대치 (요청 수 안전장치)
BODY_CHARS = 12_000       # 원문에서 요약에 넘길 분량


def body_settings(watch: dict) -> dict:
    """원문(기사 본문)을 열어 볼지, 어떤 것만, 몇 건까지.

    감시는 **목록 한 장**을 읽을 뿐이라 지금까지 제목·링크만 쌓였다. 그것만으로는
    지식볼트가 링크 모음이 된다 — 비서에게 물으면 제목만 돌려준다.
    그렇다고 전부 열면 하루 수백 번 요청이고 토큰도 그만큼이다.

    그래서 **사람이 정한 문턱**을 넘은 것만 연다. 문턱은 셋이 겹친다:
    단계(중요도) · 낱말 · 건수. 셋 다 고객이 정한다 — 규제 감시와 뉴스 감시가
    같은 문턱을 쓸 이유가 없다.
    """
    def _int(key, default, lo, hi):
        try:
            return max(lo, min(int(watch.get(key) or default), hi))
        except (TypeError, ValueError):
            return default
    kws = keywords({"keywords": watch.get("fetch_keywords")})
    return {
        "on": bool(watch.get("fetch_body")),
        "min_importance": _int("fetch_min_importance", 4, 1, IMPORTANCE_LEVELS),
        # 본문용 낱말을 안 적으면 제목 필터와 같은 것을 쓴다 — 두 번 적게 하지 않는다
        "keywords": kws or keywords(watch),
        "limit": _int("fetch_limit", 5, 1, BODY_LIMIT_MAX),
    }


def pick_for_body(watch: dict, digest: dict) -> list:
    """원문을 열어 볼 항목 — 1차 요약이 매긴 단계를 보고 고른다.

    **순서가 이렇게 될 수밖에 없다.** 단계는 요약이 끝나야 나오는데, 원문은
    요약 전에 있어야 한다. 그래서 요약을 두 번 부른다 — 1차는 제목만으로
    (싸다), 2차는 고른 것의 원문을 붙여서. 단계로 거르겠다면 치러야 하는 값이다.
    """
    cfg = body_settings(watch)
    if not cfg["on"]:
        return []
    out = []
    for it in digest.get("items") or []:
        url = str(it.get("url") or "").strip()
        if not url or not has_deep_path(url):
            continue                              # 목록 주소 자체는 열어 봐야 소용없다
        if clamp_importance(it.get("importance")) < cfg["min_importance"]:
            continue
        if not _kw_hit(f"{it.get('title', '')} {it.get('what_is_new', '')}",
                       cfg["keywords"]):
            continue
        out.append(it)
        if len(out) >= cfg["limit"]:
            break
    return out


def attach_bodies(hits: list, picked: list) -> tuple:
    """고른 항목의 원문을 받아 해당 hit 의 발췌로 붙인다. (붙인 수, 실패 목록).

    실패는 **삼키지 않고 돌려준다** — 사이트가 막았는지 주소가 죽었는지는
    고객이 알아야 할 정보다. 다만 하나가 막혀도 나머지는 그대로 간다.
    """
    from .webfetch import fetch_url_text
    by_url = {}
    for h in hits:
        if h.url:
            by_url.setdefault(normalize_url(h.url), h)
    done, failed = 0, []
    for it in picked:
        h = by_url.get(normalize_url(str(it.get("url") or "")))
        if h is None:
            continue
        try:
            text = fetch_url_text(h.url)
        except Exception as e:                    # noqa: BLE001
            failed.append(f"{h.title[:30]}: {e}")
            continue
        if not (text or "").strip():
            failed.append(f"{h.title[:30]}: 본문이 비어 있습니다")
            continue
        h.excerpt = text[:BODY_CHARS]
        done += 1
    return done, failed


def keywords(watch: dict) -> list:
    """제목에서 걸러 낼 낱말들. 비면 거르지 않는다 (전부 가져온다).

    고객이 주는 감시 명세는 대개 "이 사이트에서 **이런 주제만**" 이다
    (→ 첫 고객의 카테고리 × 검색어 확장 표). 그런데 걸러 낼 자리가 없으면
    메뉴 링크('찾아오시는 길')까지 요약 LLM 에게 넘어가 토큰을 쓴다.
    수집 단계에서 거르는 편이 싸고, 알림도 깨끗해진다.
    """
    raw = str(watch.get("keywords") or "")
    out = []
    for part in re.split(r"[,\n]", raw):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _kw_hit(text: str, kws: list) -> bool:
    """공백·대소문자를 무시하고 하나라도 들어 있으면 참.

    'Scope 3' 과 'Scope3', 'IFRS S2' 와 'IFRSS2' 를 같게 본다 — 사람이 적는
    검색어와 사이트가 쓰는 표기는 띄어쓰기가 자주 어긋난다.
    """
    if not kws:
        return True
    hay = _norm_kw(text)
    return any(_norm_kw(k) in hay for k in kws)


def _norm_kw(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).casefold()


# ---------------------------------------------------------------- 날짜로 끊기

_DATE_FULL = re.compile(r"(20\d{2})[-./년]\s?(\d{1,2})[-./월]\s?(\d{1,2})")
_DATE_SHORT = re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})(?!\d)")


def page_dates(text: str, today: date = None) -> list:
    """목록 글자에서 읽어 낸 날짜들.

    게시판은 대개 등록일을 함께 찍는다. 그 날짜가 있으면 **어디까지 넘길지**
    를 지문이 아니라 날짜로 정할 수 있다 — 주 1회 감시라면 일주일치를 다
    가져와야 하는데, "새 것이 없을 때까지"만으로는 지문이 어긋나는 순간
    놓친다 (2026-08-15 지적).
    """
    today = today or now_kst().date()
    body = str(text)
    out = []
    for y, m, d in _DATE_FULL.findall(body):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    # 온전한 날짜를 먼저 걷어 낸다 — 안 그러면 '2026-08-14' 안의 '08-14' 가
    # 짧은 형식으로 한 번 더 잡혀 같은 날짜가 두 번 들어온다.
    body = _DATE_FULL.sub(" ", body)
    # '08.14' 처럼 연도를 생략한 목록(임팩트온 등) — 올해로 읽되, 미래면 작년으로
    for m, d in _DATE_SHORT.findall(body):
        try:
            cand = date(today.year, int(m), int(d))
        except ValueError:
            continue
        if cand > today + timedelta(days=1):
            try:
                cand = date(today.year - 1, int(m), int(d))
            except ValueError:
                continue
        out.append(cand)
    return sorted(set(out))


def cutoff_date(watch: dict, now: datetime = None) -> date:
    """이 날짜보다 **오래된** 항목만 남은 장을 만나면 그만 넘긴다.

    마지막 점검일에서 하루를 더 물린다 — 게시판의 등록일이 하루 밀려 찍히거나
    시간대가 어긋나는 일이 흔해서, 딱 맞추면 경계의 글을 놓친다.
    한 번도 안 돌았으면 주기의 두 배만큼만 거슬러 간다 (기준선을 잡는 것이지
    2002년치를 다 읽자는 것이 아니다).
    """
    now = now or now_kst()
    last = _parse_ts(watch.get("last_checked_at"))
    if last is not None:
        return last.date() - timedelta(days=1)
    return now.date() - timedelta(days=min(every_days(watch) * 2, 30))


def page_urls(target: str) -> list:
    """넘겨 볼 주소들. `{page}` 가 없으면 1장짜리 목록을 준다 (예전 동작)."""
    t = str(target or "")
    if PAGE_TOKEN not in t:
        return [t]
    return [t.replace(PAGE_TOKEN, str(i)) for i in range(1, MAX_PAGES + 1)]


def check_page(watch: dict, seen: set) -> tuple:
    """(hits, 새 스냅샷, 기준선 여부).

    **어디까지 넘길지는 날짜가 정한다.** 주 1회 감시라면 일주일치를 다 가져와야
    하는데, "새 것이 없을 때까지"만으로는 지문이 한 번 어긋나면 그대로 놓친다.
    게시판은 대개 등록일을 함께 찍으므로, **그 장에서 가장 새 날짜가 기준일보다
    오래되면** 그 아래는 볼 필요가 없다 (→ `cutoff_date`).

    날짜가 없는 목록도 있다. 그때는 예전 규칙 — 새 것이 하나도 없는 장에서 멈춘다.
    둘 중 어느 쪽이든 상한(MAX_PAGES)이 마지막 안전장치다.

    스냅샷은 **넘겨 본 장들을 이어 붙인 것**이다. `_added_lines` 가 줄 집합으로
    비교하므로, 항목이 장 사이를 오가도 '새 줄'로 오인하지 않는다.
    """
    old_snapshot = watch.get("last_snapshot") or ""
    baseline = not seen  # 지문이 하나도 없으면 첫 실행
    kws = keywords(watch)
    cutoff = cutoff_date(watch)
    hits = []
    texts = []
    seen_now = set(seen)

    for i, url in enumerate(page_urls(watch["target"])):
        try:
            text, links = _fetch_page(url)
        except Exception:                        # noqa: BLE001
            if i == 0:
                raise                            # 1장부터 실패면 감시가 고장 난 것이다
            break                                # 뒷장 실패는 거기까지만 보고 넘어간다
        texts.append(text)

        fresh = 0
        for link in links:
            fp = fingerprint_url(link["url"])
            if fp in seen_now:
                continue
            seen_now.add(fp)
            if not _kw_hit(link["title"], kws):
                continue                         # 관심 밖 — 지문은 남기고 알리진 않는다
            fresh += 1
            hits.append(WatchHit(
                fingerprint=fp, title=link["title"], url=link["url"],
            ))
        # 링크가 없는 목록(자바스크립트 href·표 형태)은 줄로 센다
        new_lines = len(_added_lines(old_snapshot + "\n" + "\n".join(texts[:i]), text))

        dates = page_dates(text)
        if dates and max(dates) < cutoff:
            break                                # 이 장은 통째로 기준일보다 오래됐다
        if not dates and i and not fresh and not new_lines:
            break                                # 날짜가 없는 목록 — 예전 규칙으로 멈춘다

    text = "\n".join(texts)[:MAX_CHARS_PER_URL * 3]

    added = _added_lines(old_snapshot, text)
    if kws:
        # 낱말을 정해 뒀으면 **걸린 줄 하나가 항목 하나**다.
        # 뭉뚱그린 '본문 변경 (535자 추가)' 는 알림 제목으로 쓸모가 없다 —
        # 열어 보기 전에는 무슨 일인지 알 수 없다. 줄을 가려낼 수 있게 된
        # 지금은 제목을 그대로 쓴다.
        for line in added:
            if not _kw_hit(line, kws):
                continue
            fp = fingerprint_text(line)
            if fp in seen_now:
                continue
            seen_now.add(fp)
            hits.append(WatchHit(
                fingerprint=fp, title=line[:200], url=watch["target"],
                excerpt=line[:1000],
            ))
    else:
        added_text = "\n".join(added)
        if old_snapshot and len(added_text) >= MIN_DIFF_CHARS:
            fp = fingerprint_text(added_text)
            if fp not in seen:
                hits.append(WatchHit(
                    fingerprint=fp,
                    title=f"본문 변경 ({len(added_text):,}자 추가)",
                    url=watch["target"],
                    excerpt=added_text[:4000],
                ))

    return hits, text, baseline


# ---------------------------------------------------------- keyword 감시

def _search_prompt(watch: dict, days: int) -> str:
    """1단계 — 웹 검색 프롬프트(텍스트 응답).

    구조화 출력(generate_json)에는 검색 도구가 붙지 않으므로, 라이트 모드와
    같은 2단계(검색 → 정리)로 나눈다 (→ core/light.py 와 동일한 관례).
    """
    return packs.render(
        "watch.search",
        target=watch["target"],
        days=days,
        max_items=MAX_SEARCH_ITEMS,
        extra=(packs.render("watch.search.extra",
                            instructions=watch["instructions"])
               if watch.get("instructions") else ""),
    )


def _search_structure_prompt(watch: dict, days: int, research_text: str) -> str:
    """2단계 — 검색 결과 텍스트를 항목 배열로 정리(JSON)."""
    return packs.render(
        "watch.search_structure",
        target=watch["target"],
        research_text=research_text[:20000],
        max_items=MAX_SEARCH_ITEMS,
        days=days,
    )


def check_keyword(provider, watch: dict, seen: set, days: int = 7) -> tuple:
    """(hits, 기준선 여부). 검색 결과를 URL 지문으로 걸러 새 항목만 남긴다.

    경량 모델 2회 호출(웹 검색 1 + 구조화 1)이다.
    """
    research_text = provider.generate(
        _search_prompt(watch, days), web_search=True, max_tokens=8000,
    )
    raw = provider.generate_json(
        _search_structure_prompt(watch, days, research_text), schema=_PACK_ATTRS["SEARCH_SCHEMA"](),
    )
    items = (raw or {}).get("items") or []
    baseline = not seen
    kws = keywords(watch)
    hits = []
    for it in items[:MAX_SEARCH_ITEMS]:
        url = str(it.get("url", "")).strip()
        title = str(it.get("title", "")).strip()
        if not title:
            continue
        # 검색 감시에도 같은 잣대를 댄다. LLM 이 넓게 물어 오는 편이라
        # (그게 검색의 장점이다) 낱말을 정해 뒀으면 여기서 좁힌다.
        if not _kw_hit(title + " " + str(it.get("summary", "")), kws):
            continue
        fp = fingerprint_url(url) if has_deep_path(url) else fingerprint_text(title)
        if fp in seen or any(h.fingerprint == fp for h in hits):
            continue
        published = str(it.get("published", "")).strip()
        hits.append(WatchHit(
            fingerprint=fp,
            title=(f"{title} ({published})" if published else title)[:300],
            url=url,
            excerpt=str(it.get("summary", "")).strip(),
        ))
    return hits, baseline


# ---------------------------------------------------------------- 요약

def _digest_prompt(watch: dict, hits: list) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        block = f"### {i}. {h.title}"
        if h.url:
            block += f"\nURL: {h.url}"
        if h.excerpt:
            block += f"\n발췌:\n{h.excerpt[:4000]}"
        blocks.append(block)
    return packs.render(
        "watch.digest",
        name=watch["name"],
        kind_label=KINDS.get(watch["kind"], watch["kind"]),
        target=watch["target"],
        count=len(hits),
        blocks=chr(10).join(blocks),
        scale=scale_block(),
        perspective=(packs.render("watch.digest.perspective",
                                  instructions=watch["instructions"])
                     if watch.get("instructions") else ""),
    )


def summarize_hits(provider, watch: dict, hits: list) -> dict:
    """새 항목들을 브리핑 JSON으로. importance는 코드에서 클램프한다."""
    digest = provider.generate_json(_digest_prompt(watch, hits), schema=_PACK_ATTRS["DIGEST_SCHEMA"]())
    if not isinstance(digest, dict):
        raise ValueError("요약 응답이 JSON 객체가 아닙니다")
    # 제목 → 그 항목이 목록에서 달고 있던 날짜 (동점을 가를 때 쓴다)
    when = {}
    for h in hits:
        ds = page_dates(f"{h.title}\n{h.excerpt}")
        if ds:
            when[h.title.strip()] = max(ds)

    items = []
    for it in digest.get("items") or []:
        items.append({
            "title": str(it.get("title", "")).strip(),
            "url": str(it.get("url", "")).strip(),
            "what_is_new": str(it.get("what_is_new", "")).strip(),
            "why_it_matters": str(it.get("why_it_matters", "")).strip(),
            "importance": clamp_importance(it.get("importance")),
        })
    # 같은 단계가 여러 건이면 **코드가** 정한다 — LLM 에게 더 잘게 물으면
    # 정의되지 않은 차이를 지어내고, 같은 입력에서 순서가 흔들린다.
    # 최신 → 제목순이면 언제 돌려도 같은 결과가 나온다.
    items.sort(key=lambda x: (
        -x["importance"],
        -(when.get(x["title"]).toordinal() if when.get(x["title"]) else 0),
        x["title"],
    ))
    return {
        "headline": str(digest.get("headline", "")).strip() or watch["name"],
        "summary": str(digest.get("summary", "")).strip(),
        "items": items,
    }


# ------------------------------------------------------------ 볼트 노트

WATCH_DIR = "watch"


def watch_note_stem(watch: dict, executed_at: str) -> str:
    return f"{executed_at[:10]} {_safe_filename(watch['name'])}"


def render_watch_note(watch: dict, digest: dict, hits: list,
                      executed_at: str) -> str:
    """모니터링 결과 노트 (Obsidian). run 노트와 같은 미검증 라벨 관례를 따른다."""
    as_of = executed_at[:10]
    lines = [
        "---",
        "type: watch",
        f"watch_id: {watch['watch_id']}",
        f"watch_name: \"{watch['name']}\"",
        f"kind: {watch['kind']}",
        f"target: \"{watch['target']}\"",
        f"executed_at: {executed_at}",
        f"as_of: {as_of}",
        f"hits: {len(hits)}",
        "verified: false",
        "tags: [watch, 자동수집, 미검증]",
        "---",
        "",
        "> [!warning] 자동 수집·미검증",
        f"> 모니터링이 자동 수집한 새 항목의 요약입니다 (as_of {as_of}). "
        "원문을 확인하기 전까지는 **대조 대상**으로만 사용하세요.",
        "",
        f"# 📡 {digest.get('headline') or watch['name']} ({as_of})",
        "",
        f"- **감시 대상**: {watch['name']} — {KINDS.get(watch['kind'], watch['kind'])}",
        f"- **타깃**: {watch['target']}",
        f"- **새 항목**: {len(hits)}건",
        "",
        "## 요약",
        "",
        digest.get("summary", ""),
        "",
        "## 새로 확인된 항목",
        "",
    ]
    for it in digest.get("items") or []:
        title = it.get("title", "")
        head = (
            f"### [{md_link_text(title)}]({it['url']})"
            if it.get("url") else f"### {title}"
        )
        lines += [
            head,
            "",
            f"- **무엇이 새로운가** (중요도 {it.get('importance', '?')}/{IMPORTANCE_LEVELS}): "
            f"{it.get('what_is_new', '')}",
            f"- **왜 중요한가**: {it.get('why_it_matters', '')}",
            "",
        ]
    lines += ["## 원본 링크", ""]
    for h in hits:
        lines.append(f"- {h.title}" + (f" — {h.url}" if h.url else ""))
    lines.append("")
    return "\n".join(lines)


def digest_to_report(watch: dict, digest: dict, as_of: str) -> dict:
    """온톨로지 추출기에 넘길 보고서 형태 (엔티티 축적 재사용).

    감시 결과도 조사 결과와 같은 경로로 지식이 쌓여야 비서가 답할 수 있다.
    """
    items = digest.get("items") or []
    return {
        "title": f"[모니터링] {watch['name']} — {as_of}",
        "executive_summary": digest.get("summary", ""),
        "key_findings": [
            f"{it['title']} — {it['what_is_new']}" for it in items if it.get("title")
        ],
        "sections": [
            {"heading": it["title"], "content": it["why_it_matters"], "bullets": []}
            for it in items if it.get("why_it_matters")
        ],
        "data_tables": [],
        "recommendations": [],
        "sources": [
            {"title": it["title"], "url": it["url"]} for it in items if it.get("url")
        ],
    }


# ------------------------------------------------------------ 알림 본문


def notify_subject(watch: dict, digest: dict, n: int) -> str:
    return f"[리서치 에이전트] {watch['name']} — 새 소식 {n}건: " \
           f"{digest.get('headline', '')}"[:120]


def notify_body(watch: dict, digest: dict, hits: list, executed_at: str) -> str:
    lines = [
        f"📡 {watch['name']} 모니터링 — {executed_at[:16].replace('T', ' ')} (KST)",
        f"대상: {watch['target']}",
        f"새 항목: {len(hits)}건",
        "",
        digest.get("summary", ""),
        "",
        "─" * 40,
    ]
    for it in digest.get("items") or []:
        lines += [
            "",
            f"▸ [{it.get('importance', '?')}/{IMPORTANCE_LEVELS}] {it.get('title', '')}",
            f"  새로운 점: {it.get('what_is_new', '')}",
            f"  중요한 이유: {it.get('why_it_matters', '')}",
        ]
        if it.get("url"):
            lines.append(f"  {it['url']}")
    lines += [
        "",
        "─" * 40,
        "이 내용은 지식볼트에 자동 축적되었습니다 — 앱의 '지식 비서'에서 "
        "언제든 물어볼 수 있습니다.",
        "※ 자동 수집·미검증 자료입니다. 원문 확인 전에는 대조 대상으로만 쓰세요.",
    ]
    return "\n".join(lines)


def notify_short(watch: dict, digest: dict, n: int) -> str:
    """카카오톡용 짧은 본문 (200자 상한은 notify가 최종 절단)."""
    top = (digest.get("items") or [{}])[0].get("title", "")
    return (
        f"📡 {watch['name']} 새 소식 {n}건\n"
        f"{digest.get('headline', '')}\n"
        f"· {top}\n"
        f"{digest.get('summary', '')}"
    )


def first_link(digest: dict, hits: list) -> str:
    for it in digest.get("items") or []:
        if it.get("url"):
            return it["url"]
    for h in hits:
        if h.url:
            return h.url
    return ""
