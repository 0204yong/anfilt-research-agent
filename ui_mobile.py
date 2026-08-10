"""📱 휴대폰에서 쓰기 · 🩺 진단 — 설정 페이지의 두 절.

→ docs/21 휴대폰에서 쓰기 · docs/23 설치판 구현 계획 5단계.

휴대폰 접속은 **PC 를 개인 서버로 쓰는 것**이다. 볼트를 클라우드에 두는 것과는
다르다 — 클라우드는 파일을 동기화할 뿐 조사를 대신 돌려 주지 않는다.
그래서 조건이 둘 붙는다: **같은 Wi-Fi** 와 **PC 가 켜져 있을 것**.

화면이 지켜야 하는 것은 하나다. **PIN 없이는 켤 수 없다** (→ core/mobile.py 1번 겹).
"""
import io
import platform
import re
import sys
from pathlib import Path
from urllib.parse import quote

import streamlit as st

import ui_common
from core import appdirs, edition, keys, mobile, packs, settings
from core.config import PROVIDER_SPECS, api_key_for


# ---------------------------------------------------------------- 재시작


def _launcher_cmd() -> list:
    """설치판에서 자기를 다시 띄우는 명령. 개발 상태면 None."""
    base = edition.install_dir()
    if not base:
        return None
    pythonw = Path(base) / "runtime" / "pythonw.exe"
    launcher = Path(base) / "launcher.py"
    if not (pythonw.exists() and launcher.exists()):
        return None
    return [str(pythonw), str(launcher), "--restart"]


def _restart_button(label: str, key: str) -> None:
    """Streamlit 은 기동할 때 묶는 주소를 못박는다 — 바꾸려면 재시작뿐이다."""
    cmd = _launcher_cmd()
    if not cmd:
        st.info("변경 사항은 **프로그램을 다시 시작하면** 적용됩니다.")
        return
    if st.button(label, key=key, type="primary", use_container_width=True):
        st.warning("다시 시작하는 중입니다 — 잠시 뒤 새 창이 열립니다.")
        # 앱의 프로세스 트리 밖에서 띄운다 — 이 도우미가 할 첫 일이 "앱 죽이기"다
        ui_common.spawn_detached(cmd)
        st.stop()


# ---------------------------------------------------------------- QR


def _qr_png(url: str):
    """QR 이미지 bytes. 라이브러리가 없으면 None (링크는 그대로 쓸 수 있다)."""
    try:
        import qrcode
    except ImportError:
        return None
    try:
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:                       # noqa: BLE001
        return None


# ---------------------------------------------------------------- 휴대폰 접속


def mobile_section() -> None:
    st.header("📱 휴대폰에서 쓰기")
    st.caption(
        "이 PC 를 **내 개인 서버**로 써서, 같은 Wi‑Fi 에 있는 휴대폰 브라우저로 "
        "접속합니다. 조사는 이 PC 가 하므로 **PC 가 켜져 있어야** 하고, "
        "지식볼트도 이 PC 의 것 그대로입니다 (→ 설계서 21)."
    )

    lan_open = ui_common.is_lan_open()
    has_pin = mobile.pin_set()
    want = mobile.enabled()

    # --- 지금 상태. 설정값이 아니라 **실제로 묶인 주소**를 보여 준다.
    if lan_open:
        st.success("🟢 지금 **켜져 있습니다** — 같은 Wi‑Fi 에서 접속할 수 있습니다.")
    else:
        st.info("⚪ 지금은 **이 PC 에서만** 열려 있습니다 (127.0.0.1).")
    if want != lan_open:
        st.warning(
            "설정과 실제 상태가 다릅니다 — **다시 시작해야 적용됩니다.**"
            if want or has_pin else
            "휴대폰 접속이 켜져 있었지만 PIN 이 없어 **이 PC 전용으로 열렸습니다.**"
        )

    # --- PIN
    st.subheader("PIN")
    if has_pin:
        st.caption("✅ PIN 이 저장되어 있습니다 (이 PC 의 자격 증명 저장소).")
    else:
        st.caption(
            f"휴대폰에서 접속할 때 입력할 번호입니다. **{mobile.MIN_PIN_LEN}자 이상.** "
            "PIN 이 없으면 휴대폰 접속을 켤 수 없습니다."
        )
    c1, c2 = st.columns([3, 1])
    new_pin = c1.text_input(
        "PIN", type="password", key="_m_pin", label_visibility="collapsed",
        placeholder=("새 PIN 으로 변경" if has_pin else f"{mobile.MIN_PIN_LEN}자 이상"),
    )
    if c2.button("저장", key="_m_pin_save", use_container_width=True,
                 disabled=not new_pin):
        try:
            mobile.set_pin(new_pin)
            st.success("PIN 을 저장했습니다.")
            st.rerun()
        except Exception as e:                          # noqa: BLE001
            st.error(str(e))
    if has_pin and st.button("PIN 삭제", key="_m_pin_del"):
        mobile.clear_pin()
        st.info("PIN 을 지웠고 휴대폰 접속도 껐습니다.")
        st.rerun()

    # --- 토글
    st.subheader("접속 허용")
    if not has_pin:
        st.checkbox(
            "같은 Wi‑Fi 의 휴대폰에서 접속 허용 — **PIN 을 먼저 정하세요**",
            value=False, disabled=True, key="_m_toggle_off",
        )
    else:
        on = st.checkbox(
            "같은 Wi‑Fi 의 휴대폰에서 접속 허용", value=want, key="_m_toggle",
        )
        if on != want:
            try:
                mobile.set_enabled(on)
                st.rerun()
            except mobile.PinError as e:
                st.error(str(e))

    if want and has_pin:
        st.caption(
            "⚠️ 켜면 **같은 네트워크의 다른 기기도 이 주소를 볼 수 있습니다.** "
            "카페·호텔·공용 Wi‑Fi 에서는 꺼 두세요. Windows 방화벽이 물으면 "
            "**개인 네트워크**만 허용하세요."
        )

    if want != lan_open:
        _restart_button("🔄 지금 다시 시작해서 적용", "_m_restart")

    # --- 접속 주소
    if lan_open:
        _address_block()


def _address_block() -> None:
    st.subheader("접속 주소")
    port = 8501
    try:
        port = int(st.get_option("server.port") or 8501)
    except Exception:                       # noqa: BLE001
        pass

    ips = mobile.lan_ips()
    if not ips:
        st.warning("이 PC 의 네트워크 주소를 찾지 못했습니다 — Wi‑Fi 연결을 확인하세요.")
        return

    ip = ips[0]
    if len(ips) > 1:
        ip = st.selectbox(
            "주소가 여러 개입니다 — 하나가 안 되면 다른 것을 써 보세요", ips,
            help="유선·무선·가상 어댑터(WSL·VirtualBox 등)가 섞여 나옵니다. "
                 "보통 맨 위가 Wi‑Fi 주소입니다.",
        )
    base = mobile.lan_url(ip, port)
    # 휴대폰으로 오는 목적은 **비서에게 묻기**다 (→ 설계서 21). 조사 화면은
    # 첨부·보고서 생성이 얽혀 있어 폰에서 할 일이 아니므로, QR 은 비서로 보낸다.
    target = st.radio(
        "휴대폰에서 열 화면", ["librarian", "home"], horizontal=True,
        format_func=lambda v: "📚 지식 비서 (권장)" if v == "librarian" else "🔍 조사",
        key="_m_target", label_visibility="collapsed",
    )
    url = base + ("/" + quote("지식_비서") if target == "librarian" else "")

    c1, c2 = st.columns([1, 2])
    png = _qr_png(url)
    if png:
        c1.image(png, caption="휴대폰 카메라로 찍으세요", width=200)
    else:
        c1.caption("QR 을 만들 수 없어 주소만 표시합니다.")
    c2.markdown("**휴대폰 브라우저 주소**")
    c2.code(url, language=None)
    c2.caption(
        "① 휴대폰이 **이 PC 와 같은 Wi‑Fi** 인지 확인 → ② 위 주소로 접속 → "
        "③ PIN 입력. 홈 화면에 추가해 두면 앱처럼 쓸 수 있습니다."
    )


# ---------------------------------------------------------------- 진단


_SECRET_PAT = re.compile(
    r"(sk-ant-api\S+|AIzaSy\S+|sk-proj-\S+|sk-[A-Za-z0-9]{20,}|"
    r"eyJ[A-Za-z0-9_.-]{20,}|Bearer\s+\S+)"
)


def _scrub(text: str) -> str:
    """로그에서 키처럼 생긴 것을 지운다 — 내보내기 전에 반드시 통과시킨다."""
    return _SECRET_PAT.sub("[제거됨]", text)


def _log_tail(lines: int = 80) -> str:
    path = appdirs.logs_dir() / "server.log"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(로그 파일이 아직 없습니다)"
    return _scrub("\n".join(content.splitlines()[-lines:]))


def _report() -> str:
    """지원 문의용 진단 정보. **키 값과 볼트 내용은 절대 담지 않는다.**

    담는 것: 버전·경로·키 등록 여부·볼트 개수와 경로·휴대폰 상태·로그 꼬리.
    담지 않는 것: API 키, PIN, 노트 본문, 조사 내용.
    """
    cfg = settings.load()
    vaults = settings.vaults(cfg)
    lines = [
        "# 리서치 에이전트 진단 정보",
        "",
        "## 버전",
        f"- 앱: {edition.app_version()}",
        f"- 구성요소(팩): {packs.version() if packs.is_available() else '없음'}",
        f"- 런타임: {edition.runtime_version()}",
        f"- 에디션: {edition.current()}",
        f"- 파이썬: {sys.version.split()[0]}",
        f"- OS: {platform.platform()}",
        "",
        "## 경로",
        f"- 설치 폴더: {edition.install_dir() or '(개발 상태)'}",
        f"- 설정 폴더: {appdirs.data_dir()}",
        "",
        "## 지식볼트",
        f"- 등록: {len(vaults)}개 / 활성: {cfg.get('active_vault', 0)}",
    ]
    for i, v in enumerate(vaults):
        exists = Path(v["path"]).is_dir()
        lines.append(f"  - [{i}] {v['name']} — {v['path']} "
                     f"({'있음' if exists else '없음'})")
    lines += [
        "",
        "## API 키 (등록 여부만)",
    ]
    for spec in PROVIDER_SPECS:
        var = spec.env_vars[0]
        have = bool(api_key_for(spec))
        where = "키체인" if keys.stored_in_keyring(var) else "환경변수"
        lines.append(f"- {spec.label}: {'등록됨(' + where + ')' if have else '미등록'}")
    lines += [
        "",
        "## 휴대폰 접속",
        f"- 설정: {'켬' if mobile.enabled() else '끔'}",
        f"- PIN: {'있음' if mobile.pin_set() else '없음'}",
        f"- 실제 바인딩: {ui_common.bound_address() or '(알 수 없음)'}",
        f"- 자격 증명 저장소: {'사용 가능' if keys.available() else '없음'}",
        "",
        "## 최근 로그 (키 패턴 제거됨)",
        "```",
        _log_tail(),
        "```",
    ]
    return "\n".join(lines)


def diagnostics_section() -> None:
    st.header("🩺 진단")
    st.caption(
        "문제가 있을 때 이 내용을 지원팀에 보내 주시면 원인을 빨리 찾을 수 있습니다. "
        "**API 키와 지식볼트 내용은 담기지 않습니다** — 버전·경로·키 등록 여부와 "
        "최근 오류 기록만 담기며, 키처럼 생긴 문자열은 지우고 내보냅니다."
    )
    report = _report()
    st.download_button(
        "📄 진단 정보 내려받기", data=report.encode("utf-8"),
        file_name=f"진단_{edition.app_version()}.md", mime="text/markdown",
        use_container_width=True,
    )
    with st.expander("내용 미리 보기"):
        st.code(report, language="markdown")
