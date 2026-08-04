"""Streamlit 페이지 공통 부트스트랩 — 시크릿 브리지 + 비밀번호 게이트.

app.py에만 있던 두 함수를 꺼낸 모듈이다. `pages/` 의 각 페이지도 독립 스크립트로
실행되므로(멀티페이지 앱), 게이트를 통과시키려면 모든 페이지가 같은 부트스트랩을
호출해야 한다. 세션 상태(`_auth_ok`)는 페이지 간에 공유되므로 로그인은 1회면 된다.

Streamlit 의존 모듈은 여기와 app.py/pages/* 뿐이다 (→ docs/02 설계 원칙).
"""
import hmac
import os

import streamlit as st

APP_TITLE = "🔍 멀티 LLM 리서치 에이전트"


def bridge_secrets_to_env():
    """Streamlit Cloud의 st.secrets 값을 os.environ으로 옮긴다.

    로컬은 .env(load_dotenv), 클라우드 배포는 Streamlit Secrets UI를 쓰는데,
    core 모듈은 전부 os.getenv 로 키를 읽으므로 여기서 한 번 다리를 놓아준다.
    (이미 환경에 있는 값은 덮어쓰지 않는다 → 로컬 .env 우선)
    """
    # st.secrets 는 지연 로딩이라, 실제 접근(.keys())에서 파일이 없으면
    # StreamlitSecretNotFoundError 를 던진다 → 전체를 예외 처리로 감싼다.
    try:
        keys = list(st.secrets.keys())
    except Exception:
        return  # secrets.toml 없음(로컬) — 무시하고 .env 사용
    for key in keys:
        try:
            val = st.secrets[key]
        except Exception:
            continue
        if isinstance(val, str) and not os.getenv(key):
            os.environ[key] = val


def require_password():
    """공개 배포 시 앱을 비밀번호 한 겹으로 잠근다 (fail-closed).

    - `APP_PASSWORD`(환경변수 또는 Streamlit Secrets)가 설정돼 있으면 입장 시 비밀번호를
      요구하고, 맞으면 세션 동안 통과시킨다.
    - 설정돼 있지 **않으면** 앱을 열지 않고 안내만 띄운다 → 공개로 전환했는데 실수로
      비밀번호를 안 넣어도 무방비로 노출되지 않는다(안전한 기본값).
    비교는 타이밍 공격을 피하려 `hmac.compare_digest`(UTF-8 바이트)로 한다.
    """
    if st.session_state.get("_auth_ok"):
        return
    expected = os.getenv("APP_PASSWORD")
    st.title(APP_TITLE)
    if not expected:
        st.warning(
            "🔒 이 앱은 비밀번호로 보호됩니다. **관리자가 아직 비밀번호를 설정하지 않았습니다.**\n\n"
            "관리자: Streamlit → Manage app → Settings → **Secrets** 에 아래 한 줄을 추가하고 "
            "저장하세요 (따옴표 포함).\n\n"
            "```\nAPP_PASSWORD = \"원하는_비밀번호\"\n```"
        )
        st.stop()
    with st.form("_login_form"):
        st.caption("접속하려면 비밀번호를 입력하세요.")
        pw = st.text_input(
            "비밀번호", type="password", label_visibility="collapsed",
            placeholder="비밀번호",
        )
        submitted = st.form_submit_button("입장", use_container_width=True)
    if submitted:
        if hmac.compare_digest(str(pw).encode("utf-8"), str(expected).encode("utf-8")):
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


def bootstrap(page_title: str, page_icon: str = "🔍", layout: str = "wide"):
    """페이지 첫 줄에서 호출 — 페이지 설정 → 시크릿 브리지 → 비밀번호 게이트."""
    st.set_page_config(page_title=page_title, page_icon=page_icon, layout=layout)
    bridge_secrets_to_env()
    require_password()


def store_required(store) -> bool:
    """Supabase 미설정이면 안내를 띄우고 False를 돌려준다 (페이지 공통 가드)."""
    if store.is_configured():
        return True
    st.warning(
        "이 기능은 지식볼트 서버 사본이 필요합니다 — `SUPABASE_URL` 과 "
        "`SUPABASE_SERVICE_ROLE_KEY` 를 설정하세요 (→ 설계서 13)."
    )
    return False
