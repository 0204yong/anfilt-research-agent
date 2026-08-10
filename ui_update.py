"""🔄 업데이트 — 확인 · 내려받기 · 적용, 그리고 사이드바 알림.

→ docs/19 두 제품 구성과 자동 업데이트 4절 · docs/23 6단계.

화면이 지켜야 하는 것 셋.

  1. **조사 중에는 재시작하지 않는다** — 20분짜리 작업이 날아간다
  2. **확인 실패는 조용히 넘어간다** — 업데이트 서버가 죽었다고 앱이 막히면 안 된다
  3. **sha256 검증은 화면이 건너뛸 수 없다** — 검증은 `core/updates.py` 안에 있고,
     화면은 성공한 파일 경로만 받는다
"""
import streamlit as st

import ui_common
from core import edition, updates


def _spawn(payload, kind: str) -> bool:
    cmd = updates.updater_cmd(payload, kind)
    if not cmd:
        st.error(
            "업데이트 프로그램을 찾지 못했습니다. 홈페이지에서 최신 설치 파일을 "
            "내려받아 다시 설치해 주세요."
        )
        return False
    ui_common.spawn_detached(cmd)
    return True


def _size(n) -> str:
    """델타는 수백 KB 일 수 있다 — MB 로만 쓰면 '약 0MB' 가 된다 (실측)."""
    n = float(n or 0)
    return f"{n / 1024 / 1024:.0f}MB" if n >= 1024 * 1024 else f"{n / 1024:.0f}KB"


def _busy_note() -> bool:
    """조사 중이면 안내하고 True."""
    busy = updates.busy()
    if not busy:
        return False
    st.warning(
        f"지금 **{busy.get('what', '작업')}가 진행 중**이라 업데이트를 미뤘습니다. "
        "끝난 뒤에 다시 눌러 주세요 — 중간에 재시작하면 결과가 사라집니다."
    )
    return True


def _apply(_cached: dict) -> None:
    # 캐시된 판정으로 받지 않는다 — 낡은 sha256 은 멀쩡한 파일을 "위변조"로
    # 보이게 하고, 반대로 옛 파일을 새 버전이라 믿게 만든다 (→ core/updates.py)
    try:
        result = updates.fresh_for_apply()
    except updates.UpdateError as e:
        st.error(str(e))
        return
    if not result.get("available"):
        st.info("이미 최신 버전입니다.")
        st.session_state["_u_result"] = result
        return

    entry, kind = result["entry"], result["kind"]
    bar = st.progress(0.0, text="내려받는 중...")
    try:
        path = updates.download(entry, progress=lambda f: bar.progress(f, "내려받는 중..."))
    except updates.UpdateError as e:
        bar.empty()
        st.error(str(e))
        return
    bar.progress(1.0, text="검증 완료")
    if _spawn(path, kind):
        st.success(
            "업데이트를 적용합니다 — 프로그램이 잠시 닫혔다가 다시 열립니다."
            if kind == "delta" else
            "전체 설치 파일을 실행합니다 — 설치가 끝나면 다시 열립니다."
        )
        st.stop()


# ---------------------------------------------------------------- 사이드바


def notice() -> None:
    """모든 화면의 사이드바에 뜨는 한 줄. **막지 않고 알리기만 한다.**"""
    if not edition.is_installed():
        return
    try:
        result = updates.check()
    except Exception:                           # noqa: BLE001 — 확인 실패가 앱을 막지 않는다
        return

    if result.get("blocked"):
        with st.sidebar:
            st.error(
                f"⛔ **업데이트가 필요합니다** — {result.get('reason', '')}\n\n"
                "⚙️ 설정에서 업데이트를 적용해 주세요."
            )
        return
    if not result.get("available") or updates.skipped(result.get("version")):
        return
    with st.sidebar:
        icon = "🚨" if result.get("critical") else "🔄"
        st.info(f"{icon} 새 버전 **{result['version']}** 이 있습니다 — ⚙️ 설정에서 적용")


# ---------------------------------------------------------------- 설정 화면


def section() -> None:
    st.header("🔄 업데이트")
    st.caption(
        f"현재 버전 **{edition.app_version()}**. 새 패치가 나오면 이 화면에서 "
        "적용합니다. 지식볼트·API 키·설정은 프로그램 폴더 **바깥**에 있어 "
        "업데이트가 건드리지 않습니다 (→ 설계서 22 2절)."
    )

    auto = st.checkbox(
        "새 버전을 자동으로 확인", value=updates.auto_enabled(), key="_u_auto",
        help="확인만 자동으로 합니다. **적용은 항상 사용자가 누를 때** 이뤄집니다.",
    )
    if auto != updates.auto_enabled():
        updates.set_auto(auto)
        st.rerun()

    if st.button("지금 확인", key="_u_check", use_container_width=True):
        st.session_state["_u_result"] = updates.check(force=True)

    result = st.session_state.get("_u_result")
    if result is None:
        result = updates.check() if auto else {"available": False, "reason": ""}

    if result.get("error"):
        # 오프라인·서버 점검 — 알리되 막지 않는다
        st.caption(f"업데이트 확인 실패 (오프라인일 수 있습니다): {result['error']}")
        return

    if result.get("blocked"):
        st.error(
            f"⛔ **{result.get('reason', '')}** 계속 쓰시려면 업데이트가 필요합니다 — "
            "옛 버전은 모델·프롬프트가 바뀌면 조용히 잘못된 결과를 낼 수 있습니다."
        )

    if not result.get("available"):
        st.success(result.get("reason") or "최신 버전을 쓰고 계십니다.")
        return

    kind_label = "빠른 업데이트" if result["kind"] == "delta" else "전체 재설치"
    st.info(
        f"**새 버전 {result['version']}** 이 있습니다 — {kind_label}"
        + (f" · 약 {_size(result['entry'].get('size'))}" if result["entry"].get("size") else "")
        + (f"\n\n{result['reason']}" if result.get("reason") else "")
    )
    if result.get("notes_url"):
        st.markdown(f"[바뀐 점 보기]({result['notes_url']})")

    if _busy_note():
        return

    c1, c2 = st.columns([2, 1])
    if c1.button("⬇️ 지금 업데이트", type="primary", use_container_width=True,
                 key="_u_apply"):
        _apply(result)
    if not result.get("critical") and not result.get("blocked"):
        if c2.button("이 버전 건너뛰기", use_container_width=True, key="_u_skip"):
            updates.skip(result["version"])
            st.rerun()
