"""📚 지식 비서 — 지식볼트에 있는 내용만으로 답하는 개인 사서.

→ docs/16 지식 비서.

사용자의 개인 도서관(Obsidian 볼트)을 전부 읽고 있는 비서에게 묻는 화면이다.
답은 **볼트 근거로만** 만들어지고, 근거가 없으면 없다고 말한 뒤 그 빈칸을
조사 주제로 넘겨준다.
"""
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from ui_common import bootstrap, store_required  # noqa: E402

bootstrap("지식 비서 — 리서치 에이전트", page_icon="📚")

from core import librarian, store  # noqa: E402
from core.config import PROVIDER_SPECS, key_status, resolved_light_model  # noqa: E402
from core.providers import build_providers  # noqa: E402
from core.vault_sync import ensure_vault_seeded  # noqa: E402
from core.watch import now_kst  # noqa: E402

st.title("📚 지식 비서")
st.caption(
    "지식볼트(개인 도서관)에 쌓인 내용**만**으로 답합니다. 볼트에 없으면 "
    "지어내지 않고 '없다'고 말한 뒤, 어떤 조사를 돌리면 되는지 알려 드립니다."
)

if not store_required(store):
    st.stop()


@st.cache_data(ttl=120, show_spinner=False)
def _vault():
    """볼트 서버 사본 — 질문마다 REST 왕복하지 않게 2분 캐시."""
    ensure_vault_seeded(now_kst().isoformat(timespec="seconds"))
    return store.vault_list()


# ------------------------------------------------------------------ 사이드바

status = key_status()
with st.sidebar:
    st.header("📚 비서 설정")
    avail = [s for s in PROVIDER_SPECS if status[s.key]]
    if not avail:
        st.error("사용 가능한 API 키가 없습니다.")
        st.stop()
    _pref = {"gemini": 0, "openai": 1, "anthropic": 2}
    avail.sort(key=lambda s: _pref.get(s.key, 9))
    spec = st.selectbox(
        "답변 LLM (경량 모델)", avail,
        format_func=lambda s: f"{s.label} — {resolved_light_model(s)}",
        help="비서는 볼트 발췌를 읽고 정리하는 역할이라 경량 모델로 충분합니다.",
    )
    n_notes = st.slider(
        "근거 노트 수", 3, 15, librarian.MAX_NOTES,
        help="많을수록 폭넓게 보지만 비용·응답 시간이 늘어납니다.",
    )
    include_conf = st.checkbox(
        "🔒 기밀후보 사실 포함", value=False,
        help="첨부 자료에서 온 '기밀후보' 표시 사실까지 근거로 씁니다. "
        "질문 내용과 함께 LLM API로 전송되므로 고객사 내부 자료가 섞였다면 주의하세요.",
    )
    if st.button("🔄 볼트 새로고침", use_container_width=True):
        _vault.clear()
        st.rerun()
    if st.button("🧹 대화 지우기", use_container_width=True):
        st.session_state.pop("lib_history", None)
        st.rerun()

try:
    vault = _vault()
except Exception as e:
    st.error(f"볼트 조회 실패: {e}")
    st.stop()

n_md = len([p for p in vault if str(p).endswith(".md")])
n_entity = len([p for p in vault if str(p).startswith("entities/")])
n_watch = len([p for p in vault if str(p).startswith("watch/")])
n_runs = len([p for p in vault if str(p).startswith("runs/")])
m1, m2, m3, m4 = st.columns(4)
m1.metric("도서관 전체", f"{n_md} 노트")
m2.metric("엔티티", n_entity)
m3.metric("조사 기록", n_runs)
m4.metric("모니터링", n_watch)

# ------------------------------------------------------------------ 대화

history = st.session_state.setdefault("lib_history", [])

for turn in history:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
        for note in turn.get("notes") or []:
            with st.expander(f"📄 {note['name']} · {note['kind']} — {note['why']}"):
                st.markdown(vault.get(note["path"], "(노트를 찾을 수 없습니다)"))

if not history:
    st.info(
        "예) *CBAM 인증서 가격에 대해 볼트에 뭐가 있어?* · "
        "*ESRS E1과 IFRS S2 차이 정리해줘* · "
        "*최근 모니터링에서 잡힌 규제 변화 알려줘*"
    )

# 직전 답변이 남긴 '볼트에 없는 주제' → 메인 화면의 조사 주제로 넘긴다.
# (chat_input 블록 안에 두면 버튼 클릭 시 재실행에서 사라지므로 밖에 둔다)
suggest = st.session_state.get("lib_suggest")
if suggest:
    if st.button(f"🚀 '{suggest[:50]}' 조사하러 가기", use_container_width=True):
        st.session_state["topic"] = suggest
        st.session_state.pop("lib_suggest", None)
        st.switch_page("app.py")

question = st.chat_input("지식볼트에 물어보세요")
if question:
    history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("도서관을 뒤지는 중..."):
            try:
                provider = build_providers([spec.key], light=True)[0]
                res = librarian.ask(
                    provider, question, vault, limit=n_notes,
                    include_confidential=include_conf,
                    history=history[:-1],
                )
            except Exception as e:
                st.error(f"답변 생성 실패: {e}")
                history.pop()
                st.stop()

        # 답변 본문에 '못 채운 부분'까지 함께 담아 대화 이력에 남긴다
        body = res["answer"]
        if not res["has_basis"]:
            body = (
                "> ⚠️ **볼트 근거가 충분하지 않습니다** — 아래는 볼트에 있는 것만으로 "
                "정리한 답이며, 없는 부분은 채우지 않았습니다.\n\n" + body
            )
        if res["gaps"]:
            body += "\n\n**볼트에 없어서 답하지 못한 것**\n" + "\n".join(
                f"- {g}" for g in res["gaps"]
            )
        st.markdown(body)

        if res["notes"]:
            st.caption(
                f"근거 노트 {len(res['notes'])}개 · 볼트 {res['searched']}개 노트에서 검색"
            )
            for note in res["notes"]:
                with st.expander(f"📄 {note['name']} · {note['kind']} — {note['why']}"):
                    st.markdown(vault.get(note["path"], ""))

    history.append({
        "role": "assistant",
        "content": body,
        "notes": res["notes"],
    })
    if res["suggested_research"]:
        st.session_state["lib_suggest"] = res["suggested_research"]
    else:
        st.session_state.pop("lib_suggest", None)
    st.rerun()  # '조사하러 가기' 버튼이 이력 아래에 남도록 다시 그린다
