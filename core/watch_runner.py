"""감시 1회 실행 오케스트레이션 — 점검 → 요약 → 볼트 축적 → 알림.

→ docs/15 자동 모니터링과 알림. Streamlit 비의존.

`watch.py`가 순수 로직(무엇이 새로운가·어떻게 요약하나)이라면 이 모듈은
저장소(store)·온톨로지·알림을 엮는 배선이다. 앱의 '지금 점검' 버튼과
스케줄러(watch_run.py)가 **같은 함수**를 호출한다 — 수동 실행과 자동 실행의
동작이 갈라지지 않게.
"""
import os

from . import notify as notify_mod
from . import ontology, watch as W
from .config import PROVIDER_SPECS, has_key
from .providers import build_providers
from .vault_sync import ensure_vault_seeded

# 첫 실행을 조용히 넘기는(기준선만 수집) 감시 종류.
# page: 목록 페이지의 기존 링크 수십 개가 '새 소식'으로 쏟아지면 알림이 무의미해진다.
# keyword: 검색은 애초에 최근 항목 10건 이내라 첫 회부터 보고하는 편이 유용하다.
BASELINE_KINDS = {"page"}

# 감시는 매일 도는 저비용 작업 — 라이트 모드와 같은 경량 모델 우선순위를 쓴다
_PROVIDER_PREFERENCE = ["gemini", "openai", "anthropic"]


def build_watch_provider():
    """감시용 경량 프로바이더 1개. WATCH_PROVIDER 환경변수로 고정 가능."""
    forced = (os.getenv("WATCH_PROVIDER") or "").strip().lower()
    order = [forced] if forced else _PROVIDER_PREFERENCE
    for key in order:
        spec = next((s for s in PROVIDER_SPECS if s.key == key), None)
        if spec and has_key(spec):
            providers = build_providers([key], light=True)
            if providers:
                return providers[0]
    raise RuntimeError(
        "감시에 쓸 LLM이 없습니다 — API 키를 하나 이상 설정하세요 "
        "(GEMINI/OPENAI/ANTHROPIC)."
    )


def run_watch(store, provider, watch: dict, now_iso: str = None,
              send_notify: bool = True, extract: bool = True,
              search_days: int = 7) -> W.WatchResult:
    """감시 1건 실행. 예외를 던지지 않고 WatchResult.error에 담는다.

    `store` 는 저장소 객체(SupabaseStore | LocalStore) — 어느 볼트에 축적할지
    정하지 않고는 실행할 수 없다 (→ docs/22 설치판 아키텍처 4절).
    """
    now_iso = now_iso or W.now_kst().isoformat(timespec="seconds")
    result = W.WatchResult(watch_id=watch["watch_id"], name=watch["name"])

    # ---- 1. 점검 (새 항목 판별은 코드가 한다)
    snapshot = None
    try:
        seen = store.watch_seen_fingerprints(watch["watch_id"])
        if watch["kind"] == "page":
            hits, snapshot, baseline = W.check_page(watch, seen)
        elif watch["kind"] == "keyword":
            hits, baseline = W.check_keyword(provider, watch, seen, days=search_days)
        else:
            raise ValueError(f"알 수 없는 감시 종류: {watch['kind']}")
    except Exception as e:
        result.error = f"점검 실패: {e}"
        result.status = result.error
        _mark(store, watch, now_iso, result.status)
        return result

    result.hits = hits
    result.baseline = baseline and watch["kind"] in BASELINE_KINDS

    # '자동' 이 첫 장에서 내린 판단을 **적어 둔다.** 안 적으면 날짜 없는 목록을
    # 매번 두 번씩 읽는다 (그냥 한 번, 브라우저로 또 한 번). 적어 두면 다음부터
    # 한 번이고, 고객은 화면에서 무엇으로 정해졌는지 볼 수 있다.
    _resolved = watch.pop("resolved_browser", "")
    if _resolved and W.browser_mode(watch) == "auto":
        try:
            store.watch_save({**watch, "browser_mode": _resolved})
        except Exception:                        # noqa: BLE001
            pass                                 # 못 적어도 감시 자체는 계속된다

    # ---- 2. 첫 실행이면 지문만 적재하고 조용히 끝낸다
    if result.baseline:
        _remember(store, watch, hits, now_iso)
        result.status = f"기준선 수집 {len(hits)}건 (다음 점검부터 새 항목만 알림)"
        _mark(store, watch, now_iso, result.status, snapshot)
        return result

    if not hits:
        result.status = "새 항목 없음"
        # ⚠️ 1단계에서 `store` 인자가 빠진 채 남아 있었다. `_mark` 이 예외를 삼키는
        # 탓에 **"새 항목 없음"일 때만 최근 점검 시각이 갱신되지 않았다.**
        # 8단계의 따라잡기(`is_due`)는 이 시각을 기준으로 판단하므로, 그대로 뒀으면
        # 조용한 감시가 매시 다시 도는 고장이 됐을 것이다.
        _mark(store, watch, now_iso, result.status, snapshot)
        return result

    # ---- 3. 요약 (이미 '새롭다'고 확정된 것만 LLM에 넘긴다)
    try:
        shown = hits[: W.max_hits(watch)]
        result.digest = W.summarize_hits(provider, watch, shown)

        # ---- 3-1. 문턱을 넘은 것만 원문을 열어 다시 요약한다
        #
        # 감시는 목록 한 장만 읽으므로 제목·링크밖에 없다. 그것만 쌓이면
        # 지식볼트가 링크 모음이 된다. 그렇다고 전부 열면 요청도 토큰도 감당이
        # 안 되므로, 고객이 정한 문턱(단계·낱말·건수)을 넘은 것만 연다.
        #
        # 요약을 두 번 부르는 이유는 순서다 — 단계는 요약이 끝나야 나오는데
        # 원문은 요약 전에 있어야 한다. 2차는 **전체를 다시** 요약한다
        # (고른 것만 따로 요약해 기워 붙이면 머리말과 총평이 어긋난다).
        picked = W.pick_for_body(watch, result.digest)
        if picked:
            # 원문은 대개 다른 매체다 — 목록이 그냥 읽혔다고 기사도 그런 것은
            # 아니다. '쓰지 않음' 이 아니면 **실패했을 때만** 브라우저로 다시 연다.
            got, failed = W.attach_bodies(
                shown, picked, W.browser_mode(watch) != "never")
            if got:
                result.digest = W.summarize_hits(provider, watch, shown)
                result.status = f"원문 {got}건 읽음"
            if failed:
                result.error = (result.error + " / " if result.error else "") + \
                    "원문 읽기 실패 — " + "; ".join(failed[:3])
    except Exception as e:
        # 요약이 실패해도 발견 자체는 알린다 — 제목·링크만으로도 가치가 있다
        result.digest = {
            "headline": f"{watch['name']} 새 항목 {len(hits)}건 (요약 실패)",
            "summary": f"요약 생성에 실패했습니다({e}). 아래 원본 링크를 확인하세요.",
            "items": [
                {"title": h.title, "url": h.url, "what_is_new": h.excerpt[:300],
                 "why_it_matters": "", "importance": 3}   # 판단 불가 — 가운데
                for h in hits[: W.max_hits(watch)]
            ],
        }
        result.error = f"요약 실패: {e}"

    # ---- 4. 볼트 축적 (알림보다 먼저 — 알림이 실패해도 지식은 남는다)
    stem = W.watch_note_stem(watch, now_iso)
    result.note_path = f"{W.WATCH_DIR}/{stem}.md"
    try:
        ensure_vault_seeded(store, now_iso)
        vault = store.vault_list()
        changed = {
            result.note_path: W.render_watch_note(watch, result.digest, hits, now_iso)
        }
        if extract:
            try:
                entities = ontology.extract_entities(
                    provider,
                    W.digest_to_report(watch, result.digest, now_iso[:10]),
                    ontology.known_names_from_index(
                        vault.get("_index/entities.json", "")
                    ),
                )
                onto_changed, n_new, n_upd = ontology.apply_extraction(
                    vault, entities, stem, now_iso[:10], confidential=False,
                )
                changed.update(onto_changed)
                result.status = f"엔티티 신규 {n_new} · 갱신 {n_upd}"
            except Exception as e:
                # 추출 실패는 삼킨다 (조사 파이프라인과 같은 원칙)
                result.status = f"엔티티 반영 실패: {e}"
        store.vault_upsert_many(changed, now_iso)
    except Exception as e:
        result.error = (result.error + " / " if result.error else "") + \
            f"볼트 축적 실패: {e}"
        result.note_path = ""

    # ---- 5. 지문 기록 (여기까지 왔으면 다음부턴 '새 항목'이 아니다)
    _remember(store, watch, hits, now_iso)

    # ---- 6. 알림
    if send_notify:
        channels = [c.strip() for c in str(watch.get("notify", "")).split(",")
                    if c.strip()]
        outcomes = notify_mod.notify(
            channels,
            subject=W.notify_subject(watch, result.digest, len(hits)),
            body=W.notify_body(watch, result.digest, hits, now_iso),
            short=W.notify_short(watch, result.digest, len(hits)),
            link_url=W.first_link(result.digest, hits),
        )
        failed = [f"{ch}: {note}" for ch, ok, note in outcomes if not ok]
        if failed:
            result.error = (result.error + " / " if result.error else "") + \
                "알림 실패 — " + "; ".join(failed)

    head = f"새 항목 {len(hits)}건"
    result.status = f"{head} · {result.status}" if result.status else head
    if result.error:
        result.status += f" ⚠️ {result.error}"
    _mark(store, watch, now_iso, result.status, snapshot)
    return result


def _remember(store, watch: dict, hits: list, now_iso: str) -> None:
    try:
        store.watch_seen_add(
            watch["watch_id"],
            [{"fingerprint": h.fingerprint, "title": h.title, "url": h.url}
             for h in hits],
            now_iso,
        )
    except Exception:
        # 실패하면 다음 실행에서 같은 항목이 한 번 더 알림될 뿐 — 중단할 이유는 없다
        pass


def _mark(store, watch: dict, now_iso: str, status: str, snapshot: str = None) -> None:
    try:
        store.watch_mark_checked(watch["watch_id"], now_iso, status, snapshot)
    except Exception:
        pass


def run_due_watches(store, now=None, force_ids: list = None,
                    send_notify: bool = True, extract: bool = True) -> list:
    """지금 실행할 차례인 감시들을 모두 돈다. 반환: [WatchResult].

    force_ids가 주어지면 시각 조건을 무시하고 그 감시들만 실행한다(수동 실행).
    """
    now = now or W.now_kst()
    watches = store.watch_list()
    if force_ids:
        targets = [w for w in watches if w["watch_id"] in set(force_ids)]
    else:
        targets = [w for w in watches if W.is_due(w, now)]
    if not targets:
        return []

    provider = build_watch_provider()
    now_iso = now.isoformat(timespec="seconds")
    return [
        run_watch(store, provider, w, now_iso,
                  send_notify=send_notify, extract=extract)
        for w in targets
    ]
