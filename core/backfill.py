"""과거 자료 적재 — 감시가 오늘부터 쌓는다면, 이건 **어제까지를 채운다**.

→ docs/15 자동 모니터링과 알림. Streamlit 비의존.

## 왜 감시와 따로인가

감시의 첫 실행은 **기준선**이다: 지금 목록에 있는 것을 전부 '이미 봤다'고
기억하고 조용히 끝낸다. 그래야 첫날 알림이 쏟아지지 않는다. 그런데 그 규칙
때문에 **볼트는 빈 채로 시작한다** — 오늘부터의 새 글만 쌓이므로 지식볼트라기
보다 알림함에 가깝다.

과거를 채우려면 성질이 반대인 기능이 필요하다.

    감시                    적재
    ────────────────────────────────────────
    놓치지 않기             바닥 깔기
    매일 반복               한 번
    알림 보냄               **안 보냄**
    마지막 점검 이후        고른 기간
    건당 요약               **묶음 요약**

## 어떻게 감당하나

목록이 1,927장이면 19,270건이다. 전부 요약하면 감당이 안 된다. 세 겹으로 줄인다.

1. **기간으로 자른다** — 2022년 1~2월처럼 년/월 범위를 사람이 고른다.
   나눠서 채울 수 있다: 두 달 돌리고 결과를 본 뒤 다음 두 달을 돌린다.
2. **낱말로 거른다** — 제목만 보고 거르므로 **LLM 을 한 번도 안 부른다.**
   19,270건이 대개 수백 건이 된다.
3. **묶어서 요약한다** — 걸러진 것을 달별로 모아 25건씩 한 번에 요약한다.
   300건이면 LLM 호출 12회다.

## 중단·재개

수집과 요약을 **sqlite 에 적어 가며** 한다. 앱을 껐다 켜도, 중간에 멈춰도
다음에 이어서 한다 — 몇 시간짜리 작업에서 "어디까지 했더라"는 물어볼 수
없는 질문이다.
"""
import json
import re
import uuid
from datetime import date

from . import ontology, watch as W
from .vault_sync import ensure_vault_seeded

BATCH = 25                # 노트 하나에 담을 항목 수 (= 요약 1회)
MAX_COLLECT_PAGES = 400   # 수집이 폭주하지 않게 (기간·상한이 먼저 멈추는 것이 정상)
PAGES_PER_STEP = 5        # 화면 한 번에 넘길 장 수 — 이보다 크면 멈춤이 굼떠진다
NOTE_DIR = "backfill"


# ---------------------------------------------------------------- 기간

def ym(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def ym_range(from_ym: str, to_ym: str) -> tuple:
    """('2022-01', '2022-02') → (2022-01-01, 2022-02-28) — 끝 달의 **말일까지**.

    사람은 "2월까지"라고 하면 2월 28일을 뜻한다. 2월 1일로 읽으면 한 달을
    통째로 빠뜨린다.
    """
    y1, m1 = (int(x) for x in str(from_ym).split("-"))
    y2, m2 = (int(x) for x in str(to_ym).split("-"))
    start = date(y1, m1, 1)
    end = date(y2 + (m2 // 12), (m2 % 12) + 1, 1)
    return start, date.fromordinal(end.toordinal() - 1)


def months_between(from_ym: str, to_ym: str) -> list:
    y1, m1 = (int(x) for x in str(from_ym).split("-"))
    y2, m2 = (int(x) for x in str(to_ym).split("-"))
    out, y, m = [], y1, m1
    while (y, m) <= (y2, m2) and len(out) < 240:
        out.append(ym(y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ---------------------------------------------------------------- 일감

def new_job(store, watch: dict, from_ym: str, to_ym: str, keywords: str,
            max_items: int, extract: bool, now_iso: str) -> dict:
    job = {
        "job_id": f"bf-{re.sub(r'[^0-9]', '', now_iso)[:14]}-{uuid.uuid4().hex[:6]}",
        "watch_id": watch["watch_id"],
        "name": watch["name"],
        "target": watch["target"],
        "from_ym": from_ym, "to_ym": to_ym,
        "keywords": keywords or "",
        "max_items": int(max_items),
        "extract": 1 if extract else 0,
        # 감시가 브라우저로 읽는 대상이면 과거도 그래야 한다 — 목록을 그리는
        # 것은 같은 자바스크립트다
        "use_browser": 1 if W.use_browser(watch) else 0,
        "phase": "collect",       # collect → summarize → done
        "page": 1,
        "collected": 0,
        "cursor": "",             # 목록을 어느 날짜까지 내려왔는지
        "status": "수집 준비",
        "created_at": now_iso, "updated_at": now_iso,
    }
    store.backfill_save(job)
    return job


def pages_hint(watch: dict) -> str:
    """목록 주소에 `{page}` 가 없으면 1장밖에 못 읽는다 — 미리 말해 준다."""
    if W.PAGE_TOKEN in str(watch.get("target") or ""):
        return ""
    return (f"이 감시의 주소에 `{W.PAGE_TOKEN}` 이 없어 **첫 장만** 읽습니다. "
            f"과거를 채우려면 감시 대상 주소의 쪽 번호 자리를 "
            f"`{W.PAGE_TOKEN}` 으로 바꿔 주세요.")


# ---------------------------------------------------------------- 1) 수집

def collect_step(store, job: dict) -> dict:
    """한 장을 읽어 기간·낱말에 맞는 항목만 담는다. 진행된 job 을 돌려준다.

    **LLM 을 부르지 않는다.** 제목·날짜·링크만 보므로 몇백 장을 읽어도 싸다.
    """
    start, end = ym_range(job["from_ym"], job["to_ym"])
    kws = W.keywords({"keywords": job.get("keywords")})
    paged = W.has_pages(job["target"])
    page = int(job["page"])

    if page > MAX_COLLECT_PAGES:
        return _to_summarize(store, job, f"{MAX_COLLECT_PAGES}장까지 읽었습니다")

    try:
        text, links = W._fetch_page(W.page_url(job["target"], page),
                                    use_browser=W.use_browser(job))
    except Exception as e:                        # noqa: BLE001
        if page == 1:
            job.update(phase="done", status=f"수집 실패: {e}")
            store.backfill_save(job)
            return job
        return _to_summarize(store, job, "더 읽을 장이 없습니다")

    rows, newest, oldest = [], None, None
    for line, when in _rows(text, links):
        if when:
            newest = when if newest is None else max(newest, when)
            oldest = when if oldest is None else min(oldest, when)
            if not (start <= when <= end):
                continue                          # 기간 밖 — 담지 않는다
        elif kws:
            pass                                  # 날짜를 못 읽어도 낱말이 맞으면 담는다
        if not W._kw_hit(line["title"], kws):
            continue
        rows.append({
            "fingerprint": (W.fingerprint_url(line["url"]) if line["url"]
                            else W.fingerprint_text(line["title"])),
            "title": line["title"][:300],
            "url": line["url"],
            "published": when.isoformat() if when else "",
        })

    # 날짜가 붙은 항목이 여럿이면 **이 장은 날짜를 찍는 목록**이다. 그렇다면
    # 날짜 없는 긴 줄은 기사가 아니라 메뉴·안내문이다 — 볼트에 "ESG 포털" 같은
    # 줄이 섞이는 것을 여기서 막는다 (실측: 브라우저로 읽은 50건 중 14건이 그것).
    dated = [r for r in rows if r["published"]]
    if len(dated) >= DATED_ENOUGH:
        rows = dated

    added = store.backfill_add_items(job["job_id"], rows)
    job["collected"] = int(job["collected"]) + added
    job["page"] = page + 1
    job["cursor"] = oldest.isoformat() if oldest else ""

    # 어디쯤인지 **한 줄로** 말해 준다. 이게 없으면 몇 분 동안 화면이 똑같아
    # 보여서 "멈춘 건가"를 묻게 된다 — 실제로는 열심히 넘기는 중이다.
    if oldest is not None and oldest > end:
        where = f"아직 기간 전 ({oldest.isoformat()} 까지 내려옴)"
    elif oldest is not None:
        where = f"{oldest.isoformat()} 까지 내려옴"
    else:
        where = "날짜를 못 읽는 목록"
    job["status"] = f"{page}장 읽음 · {where} · 담은 항목 {job['collected']}건"

    # 멈출 때 — 기간보다 옛날로 넘어갔거나, 상한에 닿았거나
    if job["collected"] >= int(job["max_items"]):
        return _to_summarize(store, job, f"상한 {job['max_items']}건에 닿았습니다")
    if newest is not None and newest < start:
        return _to_summarize(store, job, "기간보다 옛날 장에 닿았습니다")
    if not paged:
        return _to_summarize(store, job, "이 목록은 한 장뿐입니다")

    store.backfill_save(job)
    return job


MIN_ROW = 15              # 이보다 짧은 줄은 항목이 아니다 (메뉴·라벨·쪽 번호)
LABEL_GAP = 3             # 제목과 날짜 사이에 낄 수 있는 라벨 줄 수
DATED_ENOUGH = 3          # 이만큼 날짜가 붙었으면 '날짜를 찍는 목록' 으로 본다
# 날짜만(또는 날짜+시각만) 있는 줄. 시각을 빼먹으면 '2026.08.14 16:54' 가
# 열여섯 자라 **기사 제목으로 담긴다** (임팩트온 실측).
_DATE_ONLY = re.compile(
    r"^[\s.\-/]*\d{2,4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
    r"(?:[\s.\-/]+\d{1,2}:\d{2}(?::\d{2})?)?[\s.\-/]*$")


def _rows(text: str, links: list):
    """(항목, 날짜) 짝. 링크가 있으면 링크로, 없으면 목록 글줄로 본다.

    **제목과 날짜가 다른 줄에 있는 목록이 많다.** 카드형 목록이 특히 그렇다
    (실측: ESG Finance Hub 를 브라우저로 읽으면 제목 한 줄, 그 아래 날짜 한 줄).
    날짜만 있는 줄을 만나면 **바로 앞 제목 줄에 얹어 준다** — 그러지 않으면
    제목은 날짜가 없어 기간 밖으로 안 걸러지고, 날짜는 제목이 없어 낱말에
    안 걸린다. 둘 다 쓸모없어진다.
    """
    for ln in links:
        ds = W.page_dates(ln["title"])
        yield {"title": ln["title"], "url": ln["url"]}, (max(ds) if ds else None)

    pending, gap = None, 0               # 아직 날짜를 못 만난 제목 줄과, 그 뒤 짧은 줄 수
    for line in str(text).split("\n"):
        line = line.strip()
        ds = W.page_dates(line)
        if ds and _DATE_ONLY.match(line):
            if pending is not None and gap <= LABEL_GAP:
                yield {"title": pending, "url": ""}, max(ds)
                pending = None
            continue                     # 날짜만 있는 줄 자체는 항목이 아니다
        if len(line) < MIN_ROW:
            gap += 1                     # '발행일 :' 같은 라벨 — 사이에 끼어도 넘긴다
            continue
        if pending is not None:
            yield {"title": pending, "url": ""}, None   # 끝내 날짜를 못 만난 제목
        if ds:
            yield {"title": line, "url": ""}, max(ds)
            pending, gap = None, 0
        else:
            pending, gap = line, 0
    if pending is not None:
        yield {"title": pending, "url": ""}, None


def _to_summarize(store, job: dict, why: str) -> dict:
    total = store.backfill_count(job["job_id"])
    job.update(phase="summarize", status=f"{why} — 담은 항목 {total}건")
    store.backfill_save(job)
    return job


# ---------------------------------------------------------------- 2) 요약

def chunks(store, job: dict) -> list:
    """남은 묶음들 — 달별로 모아 BATCH 씩. [(달, 회차, [항목])]"""
    items = store.backfill_items(job["job_id"], done=False)
    by_month = {}
    for it in items:
        key = (it.get("published") or "")[:7] or "날짜없음"
        by_month.setdefault(key, []).append(it)
    out = []
    for month in sorted(by_month):
        group = by_month[month]
        for i in range(0, len(group), BATCH):
            out.append((month, i // BATCH + 1, group[i:i + BATCH]))
    return out


def summarize_step(store, provider, job: dict, now_iso: str) -> dict:
    """묶음 하나를 요약해 노트로 남긴다. 알림은 보내지 않는다."""
    left = chunks(store, job)
    if not left:
        total = store.backfill_count(job["job_id"])
        # 0건으로 끝나는 것은 실패가 아니라 **원인이 있는 결과**다. 그냥
        # "완료 — 0건"만 남기면 고객은 프로그램이 고장 났다고 읽는다.
        job.update(phase="done", status=(
            f"완료 — {total}건" if total else
            "완료 — 담은 항목이 0건입니다. 그 기간에 글이 없었거나, 키워드가 "
            "너무 좁거나, 목록 주소에 " + W.PAGE_TOKEN + " 이 없어 첫 장만 "
            "읽었을 수 있습니다"))
        store.backfill_save(job)
        return job

    month, part, group = left[0]
    hits = [W.WatchHit(fingerprint=it["fingerprint"], title=it["title"],
                       url=it.get("url") or "", excerpt="")
            for it in group]
    fake = {**{k: job[k] for k in ("name", "target")},
            "kind": "page", "instructions": job.get("instructions") or ""}
    try:
        digest = W.summarize_hits(provider, fake, hits)
    except Exception as e:                        # noqa: BLE001
        job["status"] = f"요약 실패({month}): {e}"
        store.backfill_save(job)
        return job

    stem = f"적재 {month} {W._safe_filename(job['name'])}" + (f" ({part})" if part > 1 else "")
    path = f"{NOTE_DIR}/{stem}.md"
    ensure_vault_seeded(store, now_iso)
    vault = store.vault_list()
    changed = {path: _render(job, month, digest, hits, now_iso)}

    if int(job.get("extract") or 0):
        try:
            entities = ontology.extract_entities(
                provider, W.digest_to_report(fake, digest, month + "-01"),
                ontology.known_names_from_index(vault.get("_index/entities.json", "")))
            onto, n_new, n_upd = ontology.apply_extraction(
                vault, entities, stem, month + "-01", confidential=False)
            changed.update(onto)
            job["status"] = f"{month} ({part}) — 엔티티 신규 {n_new} · 갱신 {n_upd}"
        except Exception as e:                    # noqa: BLE001
            job["status"] = f"{month} ({part}) — 엔티티 반영 실패: {e}"
    else:
        job["status"] = f"{month} ({part}) — 노트 저장"

    store.vault_upsert_many(changed, now_iso)
    store.backfill_mark_done(job["job_id"], [h.fingerprint for h in hits])
    # 감시가 같은 항목을 '새 글'로 다시 알리지 않게 지문을 넘겨 준다
    store.watch_seen_add(job["watch_id"],
                         [{"fingerprint": h.fingerprint, "title": h.title, "url": h.url}
                          for h in hits], now_iso)
    store.backfill_save(job)
    return job


def _render(job: dict, month: str, digest: dict, hits: list, now_iso: str) -> str:
    lines = [
        "---", "type: backfill",
        f"job_id: {job['job_id']}",
        f"watch_name: \"{job['name']}\"",
        f"target: \"{job['target']}\"",
        f"period: {month}",
        f"executed_at: {now_iso}",
        f"items: {len(hits)}",
        "verified: false",
        "tags: [backfill, 자동수집, 미검증]",
        "---", "",
        "> [!warning] 과거 자료 적재 · 미검증",
        f"> {month} 의 목록에서 조건에 맞는 항목을 모아 요약한 것입니다. "
        "**목록의 제목만 읽었습니다** — 원문을 확인하기 전까지는 대조 자료로만 쓰세요.",
        "",
        f"# 📥 {digest.get('headline') or job['name']} ({month})", "",
        f"- **감시 대상**: {job['name']}",
        f"- **기간**: {month}",
        f"- **담긴 항목**: {len(hits)}건",
        "", "## 요약", "", digest.get("summary", ""), "",
        "## 항목", "",
    ]
    for it in digest.get("items") or []:
        title = it.get("title", "")
        head = (f"### [{W.md_link_text(title)}]({it['url']})"
                if it.get("url") else f"### {title}")
        lines += [head, "",
                  f"- **무엇인가** ({it.get('importance', '?')}/{W.IMPORTANCE_LEVELS}): "
                  f"{it.get('what_is_new', '')}",
                  f"- **왜 중요한가**: {it.get('why_it_matters', '')}", ""]
    lines += ["## 원본 링크", ""]
    for h in hits:
        lines.append(f"- {h.title}" + (f" — {h.url}" if h.url else ""))
    lines.append("")
    return "\n".join(lines)


def progress(store, job: dict) -> dict:
    total = store.backfill_count(job["job_id"])
    done = store.backfill_count(job["job_id"], done=True)
    return {"total": total, "done": done,
            "left_chunks": len(chunks(store, job)),
            "ratio": (done / total) if total else 0.0}


def to_json(job: dict) -> str:
    return json.dumps(job, ensure_ascii=False)
