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
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from . import packs
from .vault_render import _safe_filename
from .webfetch import MAX_CHARS_PER_URL, _HEADERS

KST = timezone(timedelta(hours=9))

KINDS = {"page": "특정 페이지 변경 감시", "keyword": "키워드 검색 감시"}

MAX_LINKS = 300          # 페이지에서 추적할 링크 상한
MAX_HITS = 8             # 한 번에 요약·알림할 새 항목 상한 (비용·가독성)
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
    text = " ".join(soup.get_text(separator="\n").split("\n"))
    text = "\n".join(l.strip() for l in text.split("\n") if l.strip())

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


def check_page(watch: dict, seen: set) -> tuple:
    """(hits, 새 스냅샷, 기준선 여부)."""
    text, links = _fetch_page(watch["target"])
    baseline = not seen  # 지문이 하나도 없으면 첫 실행
    hits = []

    for link in links:
        fp = fingerprint_url(link["url"])
        if fp in seen:
            continue
        hits.append(WatchHit(
            fingerprint=fp, title=link["title"], url=link["url"],
        ))

    added = _added_lines(watch.get("last_snapshot") or "", text)
    added_text = "\n".join(added)
    if watch.get("last_snapshot") and len(added_text) >= MIN_DIFF_CHARS:
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
    hits = []
    for it in items[:MAX_SEARCH_ITEMS]:
        url = str(it.get("url", "")).strip()
        title = str(it.get("title", "")).strip()
        if not title:
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
        perspective=(packs.render("watch.digest.perspective",
                                  instructions=watch["instructions"])
                     if watch.get("instructions") else ""),
    )


def summarize_hits(provider, watch: dict, hits: list) -> dict:
    """새 항목들을 브리핑 JSON으로. importance는 코드에서 클램프한다."""
    digest = provider.generate_json(_digest_prompt(watch, hits), schema=_PACK_ATTRS["DIGEST_SCHEMA"]())
    if not isinstance(digest, dict):
        raise ValueError("요약 응답이 JSON 객체가 아닙니다")
    items = []
    for it in digest.get("items") or []:
        try:
            imp = max(1, min(10, int(it.get("importance", 5))))
        except (TypeError, ValueError):
            imp = 5
        items.append({
            "title": str(it.get("title", "")).strip(),
            "url": str(it.get("url", "")).strip(),
            "what_is_new": str(it.get("what_is_new", "")).strip(),
            "why_it_matters": str(it.get("why_it_matters", "")).strip(),
            "importance": imp,
        })
    items.sort(key=lambda x: -x["importance"])
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
            f"- **무엇이 새로운가** (중요도 {it.get('importance', '?')}/10): "
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
            f"▸ [{it.get('importance', '?')}/10] {it.get('title', '')}",
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
