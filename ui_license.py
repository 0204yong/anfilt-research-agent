"""🔑 라이선스 — 활성화 · 상태 · 기기 해제.

→ docs/20 라이선스와 복제 방지 · docs/23 설치판 구현 계획 7단계.

화면이 지켜야 하는 것 셋.

  1. **잠겨도 볼트는 열려 있다.** 조사·비서만 잠근다. 고객 데이터를 인질로
     잡지 않는다 (→ 설계서 22 8절 상태표)
  2. **유예 상태를 사고처럼 보이게 하지 않는다.** 갱신 실패는 우리 쪽 사정이지
     고객의 잘못이 아니다 — 조용히 알리고 계속 쓰게 한다
  3. **기기 해제는 셀프서비스다.** 없으면 PC 교체·재설치 때마다 문의가 오고
     그게 지원 부담 1위가 된다 (→ 설계서 20)
"""
import streamlit as st

from core import edition, licensing


def _state_badge(s: dict) -> None:
    state = s["state"]
    if state == "active":
        left = s.get("days_left")
        st.success(
            "🟢 **활성화됨**"
            + (f" · {left}일 뒤 자동 갱신" if isinstance(left, int) else "")
            + (f" · 좌석 {s['seat_no']}/{s['seats']}"
               if s.get("seat_no") and s.get("seats") else "")
        )
    elif state == "grace":
        # 사고처럼 보이면 안 된다 — 쓰는 데 아무 지장이 없다
        st.info(
            "🔄 갱신을 시도하는 중입니다 — **사용에는 지장이 없습니다.** "
            + (f"{s['days_left']}일 안에 인터넷에 연결되면 자동으로 끝납니다."
               if isinstance(s.get("days_left"), int) else "")
        )
    elif state == "expired":
        st.error(
            "⛔ **라이선스 갱신이 필요합니다.** 조사와 지식 비서가 잠겼습니다 — "
            "**지식볼트 열람과 내보내기는 그대로 쓰실 수 있습니다.**"
        )
    elif s.get("copied"):
        st.error(
            "⛔ **이 PC 에서는 저장된 라이선스를 열 수 없습니다.** "
            "다른 PC 에서 복사해 온 설치본으로 보입니다 — "
            "이 PC 에서 다시 활성화해 주세요."
        )
    else:
        st.warning("⚪ 아직 활성화되지 않았습니다.")


def section() -> None:
    st.header("🔑 라이선스")
    if not edition.is_installed():
        return

    s = licensing.status()
    _state_badge(s)

    if s["state"] in ("active", "grace"):
        rows = {
            "라이선스": s.get("key_masked") or "-",
            "구성요소": s.get("pack_version") or "-",
            "유효기간": (s.get("expires_at") or "-")[:10],
            "이 기기": s.get("device_fp_short", "") + "…",
        }
        for k, v in rows.items():
            st.text(f"{k:8} {v}")

        c1, c2 = st.columns(2)
        if c1.button("🔄 지금 갱신", use_container_width=True, key="_l_refresh"):
            with st.spinner("확인 중..."):
                r = licensing.refresh(force=True)
            if r.get("ok"):
                st.success("갱신했습니다.")
                st.rerun()
            else:
                st.warning(
                    f"지금은 갱신하지 못했습니다 — {r.get('message') or r.get('error')}\n\n"
                    "**사용에는 지장이 없습니다.** 유효기간 안에 다시 시도됩니다."
                )
        with c2:
            _deactivate_button()
        return

    # ---- 활성화 전 / 만료
    st.caption(
        "구매하실 때 받으신 라이선스 키를 입력해 주세요. "
        "**서버로 나가는 것은 라이선스 키와 이 PC 의 식별 해시뿐**이며, "
        "조사 내용과 지식볼트는 전송되지 않습니다."
    )
    key = st.text_input(
        "라이선스 키", placeholder="ANF-XXXX-XXXX-XXXX", key="_l_key",
        help="대소문자·하이픈은 신경 쓰지 않으셔도 됩니다.",
    )
    if st.button("활성화", type="primary", use_container_width=True,
                 disabled=not key, key="_l_activate"):
        with st.spinner("활성화하는 중..."):
            try:
                licensing.activate(key)
            except licensing.LicenseError as e:
                st.error(str(e))
            else:
                from core import packs
                packs.reload()
                st.success("활성화했습니다.")
                st.rerun()

    if s.get("copied") or s["state"] == "expired":
        with st.expander("이 PC 의 라이선스 정보 지우기"):
            st.caption(
                "다른 라이선스로 바꾸거나, 이 PC 를 정리할 때 씁니다. "
                "지식볼트는 지워지지 않습니다."
            )
            _deactivate_button(label="기기 해제 · 정보 지우기")

    with st.expander("인터넷이 안 되는 환경(폐쇄망)에서 활성화"):
        st.caption(
            "아래 요청 코드를 담당자에게 보내시면, 이 PC 전용 활성화 파일을 "
            "회신해 드립니다. 요청 코드에는 **기기 식별 해시와 앱 버전만** 들어 있습니다."
        )
        st.code(licensing.offline_request_blob(), language=None)
        blob = st.text_area("회신받은 활성화 파일", key="_l_offline", height=100)
        if st.button("활성화 파일 적용", disabled=not blob, key="_l_offline_go"):
            try:
                licensing.apply_offline_blob(blob)
            except licensing.LicenseError as e:
                st.error(str(e))
            else:
                from core import packs
                packs.reload()
                st.success("활성화했습니다.")
                st.rerun()


def _deactivate_button(label: str = "이 PC 해제 (좌석 반납)") -> None:
    """PC 를 바꾸거나 재설치할 때. **서버가 안 되어도 로컬은 지운다** —
    여기서 막히면 "해제도 안 되고 쓸 수도 없는" 상태에 갇힌다."""
    if not st.session_state.get("_l_confirm_off"):
        if st.button(label, use_container_width=True, key="_l_off"):
            st.session_state["_l_confirm_off"] = True
            st.rerun()
        return
    st.warning(
        "해제하면 이 PC 에서는 조사·비서를 쓸 수 없게 됩니다 "
        "(지식볼트는 그대로입니다). 좌석은 다른 PC 에서 쓸 수 있게 됩니다."
    )
    c1, c2 = st.columns(2)
    if c1.button("해제합니다", type="primary", use_container_width=True,
                 key="_l_off_yes"):
        r = licensing.deactivate()
        from core import packs
        packs.reload()
        st.session_state.pop("_l_confirm_off", None)
        if r.get("server"):
            st.success("해제했습니다 — 좌석이 반납되었습니다.")
        else:
            st.warning(
                "이 PC 의 정보는 지웠지만 **서버에는 알리지 못했습니다** "
                f"({r.get('message') or '연결 실패'}). "
                "좌석이 남아 있으면 담당자에게 회수를 요청해 주세요."
            )
        st.rerun()
    if c2.button("취소", use_container_width=True, key="_l_off_no"):
        st.session_state.pop("_l_confirm_off", None)
        st.rerun()


def gate_notice() -> None:
    """사이드바 한 줄 — 잠긴 상태를 모든 화면에서 알린다."""
    if not edition.is_installed():
        return
    s = licensing.status()
    if s["state"] in ("active", "grace"):
        return
    with st.sidebar:
        st.error("🔑 라이선스 활성화가 필요합니다 — ⚙️ 설정")
