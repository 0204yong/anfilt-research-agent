"""Streamlit 페이지 공통 부트스트랩 — 시크릿 브리지 + 비밀번호 게이트.

app.py에만 있던 두 함수를 꺼낸 모듈이다. `pages/` 의 각 페이지도 독립 스크립트로
실행되므로(멀티페이지 앱), 게이트를 통과시키려면 모든 페이지가 같은 부트스트랩을
호출해야 한다. 세션 상태(`_auth_ok`)는 페이지 간에 공유되므로 로그인은 1회면 된다.

Streamlit 의존 모듈은 여기와 app.py/pages/* 뿐이다 (→ docs/02 설계 원칙).
"""
import hmac
import os

import streamlit as st

from core import edition

APP_TITLE = "🔍 멀티 LLM 리서치 에이전트"

# 서버가 여기에 묶여 있으면 "내 PC 안에서만 열린 것"으로 본다
_LOCAL_ADDRS = {"127.0.0.1", "localhost", "::1"}


def bound_address() -> str:
    """Streamlit 이 실제로 묶인 주소. 런처가 `--server.address` 로 정한다."""
    try:
        return str(st.get_option("server.address") or "")
    except Exception:                       # noqa: BLE001
        return ""


def is_lan_open() -> bool:
    """LAN 에 열려 있는가. **모르겠으면 열려 있다고 본다** (fail-closed)."""
    return bound_address() not in _LOCAL_ADDRS


def _require_pin():
    """정식판 게이트 — LAN 에 열려 있을 때만 잠근다 (3번 겹, → core/mobile.py).

    내 PC 안(127.0.0.1)에서만 열린 서버에 매번 비밀번호를 묻는 것은 마찰만 늘고
    지켜 주는 것이 없다. 반대로 0.0.0.0 으로 떠 있으면 **내 PC 에서 접속하든
    말든** 전부 잠근다 — 서버가 LAN 에 열려 있다는 사실 하나로 충분하다.
    """
    if not is_lan_open():
        return

    from core import mobile

    if st.session_state.get("_auth_ok"):
        return

    st.title(APP_TITLE)
    if not mobile.pin_set():
        # 런처가 막았어야 하는 상태다. 여기까지 왔다면 무언가 어긋난 것이므로
        # 열지 않는다 — LAN 에 노출된 채로 무방비인 것보다 안 열리는 게 낫다.
        st.error(
            "🔒 **휴대폰 접속이 켜져 있는데 PIN 이 없습니다.** 안전을 위해 앱을 "
            "열지 않았습니다.\n\n"
            "이 PC 에서 프로그램을 다시 실행한 뒤 **⚙️ 설정 → 휴대폰에서 쓰기**"
            "에서 PIN 을 정하거나 휴대폰 접속을 꺼 주세요."
        )
        st.stop()

    wait = mobile.locked_for()
    if wait:
        st.error(f"🔒 PIN 을 여러 번 틀렸습니다. **{wait}초 뒤** 다시 시도해 주세요.")
        st.stop()

    with st.form("_pin_form"):
        st.caption("휴대폰 접속용 PIN 을 입력하세요.")
        pin = st.text_input(
            "PIN", type="password", label_visibility="collapsed", placeholder="PIN",
        )
        submitted = st.form_submit_button("입장", use_container_width=True)
    if submitted:
        if mobile.verify_pin(pin):
            mobile.reset_failures()
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            mobile.note_failure()
            left = mobile.failures_left()
            st.error(
                "PIN 이 올바르지 않습니다."
                + (f" ({left}회 더 틀리면 잠깁니다)" if 0 < left <= 2 else "")
            )
    st.stop()


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
    """입장 게이트. **에디션마다 지켜야 할 것이 다르다.**

    - **체험판(호스팅)** — 인터넷에 열려 있으므로 `APP_PASSWORD` 로 잠근다.
      비밀번호가 없으면 앱을 열지 않는다(fail-closed). 아래 로직 그대로다.
    - **정식판(설치)** — `_require_pin()` 으로 간다. 내 PC 안(127.0.0.1)이면
      잠그지 않고, LAN 에 열려 있으면(0.0.0.0) PIN 을 요구한다.

    정식판을 나누는 이유는 실측으로 드러났다: 정식판에는 `APP_PASSWORD` 가 없어서
    **설치한 고객이 앱을 아예 열 수 없었다.** 그것도 "Streamlit → Manage app →
    Secrets 에 넣으세요"라는, 데스크톱 사용자가 따를 수 없는 안내와 함께.
    """
    if edition.is_installed():
        return _require_pin()

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
    """페이지 첫 줄에서 호출 — 페이지 설정 → 시크릿 브리지 → 입장 게이트."""
    st.set_page_config(page_title=page_title, page_icon=page_icon, layout=layout)
    bridge_secrets_to_env()
    require_password()


# 자동 내비게이션은 `.streamlit/config.toml` 에서 껐다. `pages/` 는 그대로 두어
# URL 과 `st.switch_page` 는 살아 있고, **보이는 목록만 우리가 정한다.**
_PAGES = [
    ("app.py", "조사", "🔍", None),
    ("pages/1_📡_모니터링.py", "모니터링", "📡", None),
    ("pages/2_📚_지식_비서.py", "지식 비서", "📚", None),
    ("pages/0_⚙️_설정.py", "설정", "⚙️", "settings_page"),
]


def nav():
    """사이드바 내비게이션. 각 페이지가 사이드바 맨 위에서 부른다.

    에디션에 없는 화면은 **아예 보여 주지 않는다.** 체험판 사용자에게
    "설정"을 보여 준 뒤 눌렀을 때 "설치판 전용입니다"라고 말하는 것은
    없는 기능을 광고하는 것과 같다.
    """
    with st.sidebar:
        for path, label, icon, feature in _PAGES:
            if feature and not edition.can(feature):
                continue
            try:
                st.page_link(path, label=label, icon=icon)
            except Exception:               # noqa: BLE001 — 구버전 폴백
                return
        st.divider()


def pack_required() -> bool:
    """프롬프트 팩이 없으면 안내를 띄우고 False (LLM을 부르는 기능의 공통 가드).

    팩은 프로그램의 일부라 정상 설치에서는 늘 있다. 없다는 건 설치 손상이거나
    (7단계 이후) 라이선스 갱신이 필요한 상태다. 어느 쪽이든 **스택트레이스 대신
    할 일이 적힌 안내**가 나가야 한다 (→ 설계서 22 8절).

    볼트 열람·내보내기는 팩이 없어도 막지 않는다 — 고객 데이터를 인질로 잡지 않는다.
    """
    from core import packs
    if packs.is_available():
        return True
    st.error(
        "**프롬프트 구성요소를 불러오지 못했습니다.**\n\n"
        "조사·지식 비서·모니터링은 잠시 사용할 수 없습니다. "
        "프로그램을 다시 설치하거나 업데이트를 실행해 주세요.\n\n"
        "지식볼트 열람과 내보내기는 그대로 사용하실 수 있습니다."
    )
    return False


def store_required(store) -> bool:
    """저장소가 준비되지 않았으면 안내를 띄우고 False (페이지 공통 가드).

    안내 문구가 에디션마다 다르다 — 정식판 사용자에게 Supabase 키를 설정하라고
    하면 안 된다. 그쪽은 볼트 폴더를 고르라는 뜻이다 (→ 설계서 22 8절).
    """
    if store is not None and store.is_configured():
        return True
    from core import edition
    if edition.is_installed():
        st.warning(
            "지식볼트가 아직 없습니다 — 먼저 볼트를 만들어야 이 기능이 켜집니다."
        )
        # 첫 화면이 곧 마법사다 (→ docs/23 4단계). 안내만 하고 끝내면
        # 사용자가 어디로 가야 할지 모른다.
        try:
            st.page_link("app.py", label="🔍 지식볼트 만들기", icon="🚀")
        except Exception:      # 구버전 Streamlit — 링크 없이 안내만
            st.caption("왼쪽 메뉴의 **🔍 멀티 LLM 리서치 에이전트** 로 이동하세요.")
    else:
        st.warning(
            "이 기능은 지식볼트 서버 사본이 필요합니다 — `SUPABASE_URL` 과 "
            "`SUPABASE_SERVICE_ROLE_KEY` 를 설정하세요 (→ 설계서 13)."
        )
    return False
