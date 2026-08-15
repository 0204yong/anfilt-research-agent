"""📡 자동 모니터링 — 감시 대상 등록·수동 점검·수집 이력 열람.

→ docs/15 자동 모니터링과 알림.

정해진 시각의 자동 실행은 이 페이지가 아니라 GitHub Actions(watch_run.py)가
담당한다 — Streamlit 앱은 접속 중일 때만 살아 있기 때문이다. 이 페이지는
등록·설정·수동 실행·열람을 맡는다.
"""
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from ui_common import bootstrap, nav, pack_required, store_required  # noqa: E402

bootstrap("모니터링 — 리서치 에이전트", page_icon="📡")
nav()

from core import notify, scheduler, watch as W  # noqa: E402
from core import store as store_mod  # noqa: E402
from core.watch_runner import build_watch_provider, run_watch  # noqa: E402

store = store_mod.active() or store_mod.supabase_store()
_store_key = getattr(store, "label", store.kind)   # 캐시 키 (→ docs/17 3.7절)
_pack_ok = pack_required()

st.title("📡 자동 모니터링")
st.caption(
    "특정 페이지나 검색 키워드를 정해진 시각에 점검해 **새로운 내용만** 잡아내고, "
    "요약을 지식볼트에 축적한 뒤 이메일·카카오톡으로 알려 드립니다."
)

if not store_required(store):
    st.stop()

HOURS = list(range(24))


@st.cache_data(ttl=30, show_spinner=False)
def _watches(store_key: str):
    # store_key 는 캐시 키 전용 — 볼트가 바뀌면 목록이 새로 불린다
    return store.watch_list()


def _refresh():
    _watches.clear()
    st.rerun()


# ------------------------------------------------------------ 알림 채널 상태

ch_status = notify.channel_status()
c1, c2, c3 = st.columns([2, 2, 5])
c1.metric("📧 이메일 알림", "설정됨" if ch_status["email"] else "미설정")
c2.metric("💬 카카오톡 알림", "설정됨" if ch_status["kakao"] else "미설정")
with c3:
    if not any(ch_status.values()):
        st.warning(
            "알림 채널이 하나도 설정되지 않았습니다. 감시는 볼트에 계속 쌓이지만 "
            "알림은 오지 않습니다 — 설정 방법은 아래 '⚙️ 설정 안내'를 보세요."
        )
    else:
        st.info(
            f"알림 수신: {notify.email_recipient() or '(미설정)'} · "
            "새 항목이 있을 때만 발송됩니다."
        )

st.divider()

# ------------------------------------------------------ 이 PC 에서 자동 실행
# 체험판은 GitHub Actions 가 매시 깨워 준다. 정식판에는 그 서버가 없으므로
# Windows 작업 스케줄러가 그 역할을 한다 (→ docs/23 8단계).
if scheduler.available():
    _sched = scheduler.status()
    with st.expander(
        "🕒 이 PC 에서 자동 실행 — "
        + ("✅ 켜져 있습니다" if _sched["registered"] else "⚠️ 꺼져 있습니다"),
        expanded=not _sched["registered"],
    ):
        st.caption(
            "매시 확인해서 **예정 시각이 지났는데 아직 안 돈 감시만** 실행합니다. "
            "PC 가 꺼져 있어 놓친 시각은 켜질 때 그날 안에 따라잡습니다. "
            "PC 가 꺼져 있는 동안에는 실행되지 않습니다."
        )
        if _sched["registered"]:
            st.text(f"마지막 실행  {_sched['last_run'] or '아직 없음'}")
            st.text(f"다음 예정    {_sched['next_run'] or '-'}")
            if _sched["last_result"]:
                st.caption(
                    f"마지막 결과 코드 {_sched['last_result']} — "
                    "0이 아니면 로그(`%APPDATA%\\ANFILT\\ResearchAgent\\logs`)를 확인하세요."
                )
            b1, b2 = st.columns(2)
            if b1.button("지금 한 번 실행", use_container_width=True, key="_sch_run"):
                ok, msg = scheduler.run_now()
                (st.success if ok else st.error)(msg)
            if b2.button("자동 실행 끄기", use_container_width=True, key="_sch_off"):
                ok, msg = scheduler.unregister()
                (st.success if ok else st.error)(msg)
                st.rerun()
        else:
            st.warning(
                "지금은 **이 화면을 열어 두거나 직접 점검할 때만** 감시가 실행됩니다."
            )
            if st.button("자동 실행 켜기", type="primary", use_container_width=True,
                         key="_sch_on"):
                ok, msg = scheduler.register()
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()

    st.divider()

# 목록을 먼저 읽는다 — 테이블 미생성(첫 사용)을 여기서 한 번에 안내하기 위해
try:
    watches = _watches(_store_key)
except Exception as e:
    st.error(
        f"감시 목록 조회 실패: {e}\n\n"
        "Supabase SQL Editor에서 `supabase-ra-watch-setup.sql` 의 내용을 "
        "실행했는지 확인하세요."
    )
    st.stop()

# ------------------------------------------------------------ 새 감시 등록

with st.expander("➕ 새 감시 등록", expanded=not watches):
    with st.form("new_watch", clear_on_submit=True):
        n1, n2 = st.columns([3, 2])
        with n1:
            name = st.text_input(
                "감시 이름 *", placeholder="예) 환경부 보도자료, CBAM 최신 동향",
            )
        with n2:
            kind = st.selectbox(
                "감시 종류", list(W.KINDS), format_func=lambda k: W.KINDS[k],
            )
        target = st.text_input(
            "감시 대상 *",
            placeholder="페이지 감시: https://me.go.kr/... · 키워드 감시: CBAM 인증서 가격",
            help="페이지 감시는 URL(목록·공지 페이지 권장), 키워드 감시는 검색어를 넣으세요.\n\n"
                 "목록이 여러 장이면 쪽 번호 자리에 {page} 를 넣으세요 — "
                 "예: .../list.do?page={page} · 새 글이 없는 장을 만날 때까지만 넘깁니다. "
                 "넣지 않으면 첫 장만 봅니다.",
        )
        t1, t0, t2 = st.columns([2, 1, 2])
        with t1:
            hours = st.multiselect(
                "실행 시각 (KST)", HOURS, default=[8],
                format_func=lambda h: f"{h:02d}:00",
                help="이 시각대에 점검합니다. 여러 개 선택 가능.",
            )
        with t0:
            every = st.number_input(
                "주기 (일)", min_value=1, max_value=365, value=1, step=1,
                help="1이면 매일. 7이면 이레에 한 번 — 분기 보고서나 월간 동향처럼 "
                     "매일 볼 필요가 없는 감시에 쓰세요 (비용·알림이 그만큼 줄어듭니다).",
            )
            cap = st.number_input(
                "한 번에 알릴 항목", min_value=1, max_value=W.MAX_HITS_LIMIT,
                value=W.MAX_HITS, step=1,
                help="한 번 돌 때 요약·알림할 새 항목 수. 뉴스 매체는 20, 공지 게시판은 5 정도. "
                     "넘친 항목도 사라지지 않고 볼트 노트의 '원본 링크'에 남습니다.",
            )
        with t2:
            channels = st.multiselect(
                "알림 채널",
                list(notify.CHANNELS),
                default=[k for k, v in ch_status.items() if v] or ["email"],
                format_func=lambda k: notify.CHANNELS[k][0]
                + ("" if ch_status[k] else " (미설정)"),
            )
        kw = st.text_input(
            "제목 키워드 (선택)",
            placeholder="예) KSSB, IFRS S2, ISSB, 지속가능성 공시, GRI, Scope 3",
            help="제목에 이 낱말이 든 항목만 가져옵니다 (쉼표로 구분). "
                 "비워 두면 전부 가져옵니다. 띄어쓰기·대소문자는 무시합니다 — "
                 "'Scope 3' 과 'Scope3' 이 같습니다.",
        )
        with st.expander("📄 원문까지 읽기 (선택)", expanded=False):
            st.caption(
                "감시는 목록 한 장만 읽으므로 기본은 **제목·링크**만 쌓입니다. "
                "여기를 켜면 문턱을 넘은 항목의 **기사 본문을 열어** 제대로 요약합니다 — "
                "지식볼트에 읽을 만한 내용이 남습니다. 대신 그만큼 느리고 비쌉니다."
            )
            fb = st.checkbox("원문까지 읽기", value=False, key="_fb_new")
            f1, f2 = st.columns(2)
            fmin = f1.number_input(
                "몇 단계 이상", min_value=1, max_value=W.IMPORTANCE_LEVELS, value=4, step=1,
                help="1차 요약이 매긴 단계입니다. 뜻은 ⚙️ 설정에서 고칠 수 있습니다.",
            )
            flim = f2.number_input(
                "한 번에 몇 건까지", min_value=1, max_value=W.BODY_LIMIT_MAX, value=5, step=1,
                help="문턱을 넘어도 이 수까지만 엽니다. 비용이 튀지 않게 하는 마지막 안전장치입니다.",
            )
            fkw = st.text_input(
                "본문 키워드 (선택)", key="_fkw_new",
                placeholder="예) KSSB, CBAM, 공시 의무화",
                help="비우면 위의 제목 키워드를 그대로 씁니다.",
            )
        instructions = st.text_input(
            "요약 관점 (선택)",
            placeholder="예) 국내 철강 수출기업 관점에서 실무 영향 위주로",
        )
        submitted = st.form_submit_button("등록", type="primary",
                                          use_container_width=True)
    if submitted:
        if not name.strip() or not target.strip():
            st.error("감시 이름과 대상을 입력해 주세요.")
        elif kind == "page" and not target.strip().startswith(("http://", "https://")):
            st.error("페이지 감시의 대상은 http(s)로 시작하는 URL이어야 합니다.")
        else:
            now_iso = W.now_kst().isoformat(timespec="seconds")
            try:
                store.watch_save({
                    "watch_id": W.new_watch_id(now_iso),
                    "name": name.strip(),
                    "kind": kind,
                    "target": target.strip(),
                    "hours": ",".join(f"{h:02d}" for h in sorted(hours)) or "08",
                    "every_days": int(every),
                    "max_hits": int(cap),
                    "enabled": True,
                    "notify": ",".join(channels),
                    "keywords": kw.strip(),
                    "fetch_body": bool(fb),
                    "fetch_min_importance": int(fmin),
                    "fetch_limit": int(flim),
                    "fetch_keywords": fkw.strip(),
                    "instructions": instructions.strip(),
                })
                st.success(
                    f"'{name.strip()}' 감시를 등록했습니다."
                    + (" 페이지 감시는 **첫 점검이 기준선 수집**이라 알림이 가지 않고, "
                       "다음 점검부터 새 항목만 알려 드립니다."
                       if kind == "page" else "")
                )
                _refresh()
            except Exception as e:
                st.error(f"등록 실패: {e}\n\n`supabase-ra-watch-setup.sql` 을 먼저 실행했는지 확인하세요.")

# ------------------------------------------------------------ 등록된 감시

st.subheader(f"등록된 감시 ({len(watches)}건)")
if not watches:
    st.caption("아직 등록된 감시가 없습니다 — 위에서 추가하세요.")

for w in watches:
    icon = "🟢" if w.get("enabled") else "⏸️"
    last = (w.get("last_checked_at") or "")[:16].replace("T", " ")
    title = f"{icon} {w['name']} — {W.KINDS.get(w['kind'], w['kind'])}"
    every = W.every_days(w)
    cycle = f"{every}일에 한 번" if every > 1 else "매일"
    slots = ", ".join(f"{h:02d}:00" for h in W.parse_hours(w.get("hours")))
    with st.expander(title, expanded=False):
        st.markdown(
            f"- **대상**: {w['target']}\n"
            f"- **주기**: {cycle} · {slots} (KST)\n"
            f"- **알림**: {w.get('notify') or '없음'}\n"
            f"- **최근 점검**: {last or '아직 없음'} — {w.get('last_status') or ''}"
        )
        if w.get("keywords"):
            st.caption(f"제목 키워드: {w['keywords']}")
        if w.get("fetch_body"):
            _b = W.body_settings(w)
            st.caption(f"원문 읽기: {_b['min_importance']}단계 이상 · "
                       f"한 번에 {_b['limit']}건까지")
        if w.get("instructions"):
            st.caption(f"요약 관점: {w['instructions']}")

        b1, b2, b3 = st.columns(3)
        if b1.button("🔍 지금 점검", key=f"run_{w['watch_id']}",
                     use_container_width=True):
            with st.spinner("점검 중... (페이지 수집 → 새 항목 판별 → 요약 → 볼트 축적)"):
                try:
                    provider = build_watch_provider()
                    res = run_watch(store, provider, w)
                except Exception as e:
                    st.error(f"점검 실패: {e}")
                else:
                    if res.baseline:
                        st.info(f"🌱 {res.status}")
                    elif res.has_news:
                        st.success(f"🔔 {res.status}")
                        st.markdown(f"**{res.digest.get('headline', '')}**")
                        st.write(res.digest.get("summary", ""))
                        for it in res.digest.get("items", []):
                            line = f"- [{it['importance']}/10] {it['title']}"
                            if it.get("url"):
                                line += f" — [원문]({it['url']})"
                            st.markdown(line)
                    else:
                        st.info("새 항목이 없습니다.")
                    if res.error:
                        st.warning(res.error)
                    _watches.clear()

        toggle_label = "⏸️ 중지" if w.get("enabled") else "▶️ 재개"
        if b2.button(toggle_label, key=f"tog_{w['watch_id']}",
                     use_container_width=True):
            try:
                store.watch_save({**w, "enabled": not w.get("enabled", True)})
                _refresh()
            except Exception as e:
                st.error(f"변경 실패: {e}")

        if b3.button("🗑 삭제", key=f"del_{w['watch_id']}", use_container_width=True):
            st.session_state[f"confirm_del_{w['watch_id']}"] = True
        if st.session_state.get(f"confirm_del_{w['watch_id']}"):
            st.warning(
                f"'{w['name']}' 감시와 그 지문 이력을 삭제합니다. "
                "볼트에 쌓인 노트는 남습니다."
            )
            d1, d2 = st.columns(2)
            if d1.button("삭제 확정", key=f"delok_{w['watch_id']}",
                         type="primary", use_container_width=True):
                try:
                    store.watch_delete(w["watch_id"])
                    st.session_state.pop(f"confirm_del_{w['watch_id']}", None)
                    _refresh()
                except Exception as e:
                    st.error(f"삭제 실패: {e}")
            if d2.button("취소", key=f"delno_{w['watch_id']}",
                         use_container_width=True):
                st.session_state.pop(f"confirm_del_{w['watch_id']}", None)
                st.rerun()

        # ---- 설정 변경
        with st.form(f"edit_{w['watch_id']}"):
            st.caption("설정 변경")
            e1, e0, e2 = st.columns([2, 1, 2])
            with e1:
                new_hours = st.multiselect(
                    "실행 시각 (KST)", HOURS,
                    default=W.parse_hours(w.get("hours")),
                    format_func=lambda h: f"{h:02d}:00",
                    key=f"h_{w['watch_id']}",
                )
            with e0:
                new_every = st.number_input(
                    "주기 (일)", min_value=1, max_value=365,
                    value=W.every_days(w), step=1, key=f"d_{w['watch_id']}",
                )
                new_cap = st.number_input(
                    "한 번에 알릴 항목", min_value=1, max_value=W.MAX_HITS_LIMIT,
                    value=W.max_hits(w), step=1, key=f"m_{w['watch_id']}",
                )
            with e2:
                new_channels = st.multiselect(
                    "알림 채널", list(notify.CHANNELS),
                    default=[c for c in str(w.get("notify", "")).split(",")
                             if c in notify.CHANNELS],
                    format_func=lambda k: notify.CHANNELS[k][0],
                    key=f"c_{w['watch_id']}",
                )
            new_kw = st.text_input(
                "제목 키워드", value=w.get("keywords", ""),
                key=f"k_{w['watch_id']}",
                help="비워 두면 전부 가져옵니다.",
            )
            new_fb = st.checkbox(
                "📄 원문까지 읽기", value=bool(w.get("fetch_body")),
                key=f"fb_{w['watch_id']}",
            )
            g1, g2 = st.columns(2)
            new_fmin = g1.number_input(
                "몇 단계 이상", min_value=1, max_value=W.IMPORTANCE_LEVELS,
                value=W.body_settings(w)["min_importance"], step=1,
                key=f"fm_{w['watch_id']}",
            )
            new_flim = g2.number_input(
                "한 번에 몇 건까지", min_value=1, max_value=W.BODY_LIMIT_MAX,
                value=W.body_settings(w)["limit"], step=1,
                key=f"fl_{w['watch_id']}",
            )
            new_fkw = st.text_input(
                "본문 키워드", value=w.get("fetch_keywords", ""),
                key=f"fk_{w['watch_id']}", help="비우면 제목 키워드를 씁니다.",
            )
            new_instructions = st.text_input(
                "요약 관점", value=w.get("instructions", ""),
                key=f"i_{w['watch_id']}",
            )
            if st.form_submit_button("저장", use_container_width=True):
                try:
                    store.watch_save({
                        **w,
                        "hours": ",".join(f"{h:02d}" for h in sorted(new_hours)) or "08",
                        "every_days": int(new_every),
                        "max_hits": int(new_cap),
                        "notify": ",".join(new_channels),
                        "keywords": new_kw.strip(),
                        "fetch_body": bool(new_fb),
                        "fetch_min_importance": int(new_fmin),
                        "fetch_limit": int(new_flim),
                        "fetch_keywords": new_fkw.strip(),
                        "instructions": new_instructions.strip(),
                    })
                    st.success("저장했습니다.")
                    _refresh()
                except Exception as e:
                    st.error(f"저장 실패: {e}")

# ------------------------------------------------------------ 수집 이력

st.divider()
st.subheader("🗂 수집된 모니터링 노트")
st.caption(
    "모니터링이 발견한 새 항목의 요약은 지식볼트 `watch/` 폴더에 쌓이고, "
    "엔티티 노트에도 반영됩니다 — **📚 지식 비서**에서 바로 물어볼 수 있습니다."
)
if st.button("최근 노트 불러오기"):
    try:
        vault = store.vault_list()
        st.session_state["_watch_notes"] = {
            p: c for p, c in vault.items() if p.startswith(f"{W.WATCH_DIR}/")
        }
    except Exception as e:
        st.error(f"볼트 조회 실패: {e}")

notes = st.session_state.get("_watch_notes")
if notes is not None:
    if not notes:
        st.caption("아직 수집된 모니터링 노트가 없습니다.")
    for path in sorted(notes, reverse=True)[:20]:
        with st.expander(path.split("/", 1)[-1][:-3]):
            st.markdown(notes[path])

# ------------------------------------------------------------ 설정 안내

with st.expander("⚙️ 설정 안내 — 자동 실행과 알림"):
    st.markdown(
        """
**자동 실행 (정해진 시각)** — Streamlit 앱은 접속 중일 때만 살아 있어서
스케줄 실행을 할 수 없습니다. GitHub Actions가 매시 정각에 깨어나
`watch_run.py` 를 돌리고, 그때가 실행 시각인 감시만 점검합니다.

1. GitHub 저장소 → Settings → Secrets and variables → **Actions**
2. 아래 시크릿 등록
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (필수)
   - `GOOGLE_API_KEY` (또는 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) — 요약용
   - 이메일: `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` `NOTIFY_EMAIL_TO`
   - 카카오: `KAKAO_REST_API_KEY` `KAKAO_REFRESH_TOKEN`
3. Actions 탭 → **자동 모니터링** 워크플로에서 수동 실행으로 검증

**📧 이메일** — Gmail이면 2단계 인증 후 [앱 비밀번호](https://myaccount.google.com/apppasswords)를
발급해 `SMTP_PASSWORD`에 넣으세요 (`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`).

**💬 카카오톡 (나에게 보내기)** — [카카오 개발자](https://developers.kakao.com)에서
① 애플리케이션 생성 → REST API 키 확보 ② 카카오 로그인 활성화 + Redirect URI 등록
③ 동의항목에서 **카카오톡 메시지 전송(talk_message)** 활성화 ④ 인가코드로 토큰을
발급받아 **refresh_token** 을 `KAKAO_REFRESH_TOKEN` 에 저장. 친구에게 보내기는
카카오 검수가 필요하지만 '나에게 보내기'는 검수 없이 동작합니다.

**로컬 테스트**
```
python watch_run.py --list
python watch_run.py --all --no-notify
```
        """
    )
