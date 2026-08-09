"""지식볼트 마법사·관리 화면 — 설치판에서 볼트를 만들고 고르고 바꾼다.

→ docs/23 설치판 구현 계획 4단계.

두 곳에서 쓴다.
  - `wizard()`  : 볼트가 아직 없을 때 **첫 화면을 대신한다** (app.py 게이트)
  - `manager()` : ⚙️ 설정 페이지의 지식볼트 절 (추가·전환·가져오기)

둘 다 같은 조각(`_target_block` / `_create_block` / `_import_block`)을 쓴다 —
마법사와 설정이 서로 다르게 동작하기 시작하면 "설정에서 만든 볼트는 왜 다르지"가
된다. 실무는 전부 `core/vault_setup.py` 에 있고 여기는 그리기만 한다.
"""
from datetime import datetime

import streamlit as st

from core import appdirs, settings, store as store_mod, vault_setup


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _switch_to(index: int) -> None:
    """볼트 전환 — 설정·sqlite 연결·화면 캐시를 함께 갈아 끼운다.

    캐시를 지우지 않으면 조사 이력·감시 목록이 이전 볼트 것으로 남는다
    (캐시 키에 볼트가 들어 있어 보통은 괜찮지만, 전환은 드물고 실수는 비싸다).
    """
    settings.set_active_vault(index)
    store_mod.reset_cache()
    st.cache_data.clear()


# ------------------------------------------------------------------ 조각


def _target_block(key: str, default_path: str) -> tuple:
    """폴더 입력 + 폴더 선택 버튼 + 진단. 반환 (경로문자열, inspect 결과)."""
    picked_key = f"_v_picked_{key}"
    value = st.session_state.get(picked_key, default_path)

    c1, c2 = st.columns([5, 2])
    path = c1.text_input(
        "볼트 폴더", value=value, key=f"_v_path_{key}",
        help="Obsidian 이 열게 될 폴더입니다. 폴더째 복사하면 다른 PC로 그대로 옮겨집니다.",
    ).strip()
    c2.write("")   # 라벨 높이 맞춤
    if c2.button("📁 폴더 선택", key=f"_v_pick_{key}", use_container_width=True):
        with st.spinner("폴더 선택 창을 여는 중... (다른 창 뒤에 있을 수 있습니다)"):
            picked = vault_setup.pick_folder(path or default_path)
        if picked:
            st.session_state[picked_key] = picked
            st.rerun()
        else:
            st.caption("선택이 취소되었습니다 — 경로를 직접 입력하셔도 됩니다.")

    if not path:
        return path, None
    info = vault_setup.inspect(path)
    for msg in info["blocking"]:
        st.error(msg)
    for msg in info["warnings"]:
        st.warning(msg)
    if not info["blocking"]:
        label = {
            "new": "새 폴더를 만듭니다.",
            "empty": "빈 폴더입니다 — 여기에 만듭니다.",
            "nonempty": "기존 폴더에 볼트 구조를 더합니다.",
            "vault_root": "기존 Obsidian 볼트에 볼트 구조를 더합니다.",
            "inside_vault": "기존 볼트 안쪽 폴더에 만듭니다.",
        }.get(info["state"], "")
        if label:
            st.caption(f"📁 `{info['path']}` — {label}")
    return path, info


def _manual_open_help(vault_path) -> None:
    st.caption(
        "Obsidian 에서 **왼쪽 아래 볼트 아이콘 → Open folder as vault** 로 "
        "아래 폴더를 여세요."
    )
    st.code(str(vault_path), language=None)


def _opened_block(vault_path) -> None:
    """Obsidian 으로 열기.

    링크만 내면 안 된다 — `obsidian://` 은 **Obsidian 이 아는 볼트**에만 통하고,
    갓 만든 폴더에는 오류 창이 뜬다(실측). 등록 여부를 매번 확인해서, 안 되어
    있으면 등록 버튼을 먼저 낸다.
    """
    if not vault_setup.obsidian_installed():
        st.info(
            "**Obsidian 이 설치되어 있지 않은 것 같습니다.** 없어도 조사·비서는 그대로 "
            "동작합니다 — 노트를 그래프로 보고 직접 고치고 싶을 때 설치하시면 됩니다.\n\n"
            f"[Obsidian 내려받기]({vault_setup.OBSIDIAN_DOWNLOAD_URL})"
        )
        _manual_open_help(vault_path)
        return

    if vault_setup.registered_with_obsidian(vault_path):
        st.link_button(
            "🔗 Obsidian 으로 열기", vault_setup.obsidian_uri(vault_path),
            use_container_width=True,
        )
        st.caption(
            "브라우저가 “Obsidian 을 여시겠습니까?”라고 물으면 **열기**를 누르세요."
        )
        return

    st.caption("이 볼트가 아직 Obsidian 의 볼트 목록에 없습니다.")
    if st.button("🔗 Obsidian 에 등록하기", key=f"_v_reg_{vault_path}",
                 use_container_width=True):
        if vault_setup.register_with_obsidian(vault_path):
            st.rerun()
        st.warning(
            "Obsidian 의 볼트 목록을 갱신하지 못했습니다 — **Obsidian 을 닫고** "
            "다시 시도하시거나, 아래 방법으로 직접 여세요."
        )
    _manual_open_help(vault_path)


def _create_block(key: str, default_path: str, default_name: str,
                  button_label: str = "✅ 지식볼트 만들기") -> dict:
    """폴더를 고르고 만든다. 성공하면 결과 dict, 아니면 None."""
    path, info = _target_block(key, default_path)
    name = st.text_input(
        "볼트 이름", value=default_name, key=f"_v_name_{key}",
        help="화면에서 구분하는 이름입니다. 고객사별로 볼트를 나눌 때 씁니다.",
    ).strip()

    blocked = not path or info is None or bool(info["blocking"])
    if not st.button(button_label, key=f"_v_go_{key}", type="primary",
                     disabled=blocked, use_container_width=True):
        return None
    try:
        with st.spinner("볼트를 만들고 ESG 시드 노트를 넣는 중..."):
            return vault_setup.create_vault(path, name, _now())
    except Exception as e:                          # noqa: BLE001
        st.error(f"볼트를 만들지 못했습니다: {e}")
        return None


def _import_block(store, key: str) -> None:
    """체험판 볼트 zip 가져오기 — 노트는 폴더로, 조사 이력은 sqlite 로."""
    st.caption(
        "체험판 화면의 **🧰 지식볼트 관리 → 볼트 zip 다운로드**로 받은 파일을 올리면, "
        "노트와 **조사 이력**이 이 볼트로 들어옵니다."
    )
    up = st.file_uploader("볼트 zip", type=["zip"], key=f"_v_zip_{key}")
    if up is None:
        return

    has_notes = not store.vault_is_empty()
    mode = "merge"
    if has_notes:
        mode = st.radio(
            "이미 노트가 있습니다 — 어떻게 할까요?",
            ["merge", "replace"], key=f"_v_mode_{key}",
            format_func=lambda m: "합치기 — 같은 이름만 갱신 (권장)"
            if m == "merge" else "⚠️ 교체 — 이 볼트의 기존 노트를 지우고 zip 내용으로",
        )
        if mode == "replace" and not st.checkbox(
            "기존 노트가 지워지는 것을 이해했습니다", key=f"_v_ok_{key}"
        ):
            return

    if not st.button("📥 가져오기", key=f"_v_imp_{key}", type="primary",
                     use_container_width=True):
        return
    try:
        with st.spinner("zip 을 여는 중..."):
            res = vault_setup.import_zip(store, up.getvalue(), _now(), mode)
    except Exception as e:                          # noqa: BLE001
        st.error(f"가져오기 실패: {e}")
        return
    st.success(f"파일 {res['files']}개 · 조사 이력 {res['runs']}건을 가져왔습니다.")
    if res["conflicts"]:
        st.warning(
            "직접 고치신 노트는 덮어쓰지 않았습니다 — 앱 버전은 "
            f"`{appdirs.AGENT_DIRNAME}/conflicts/` 에 두었습니다: "
            + ", ".join(str(c) for c in res["conflicts"][:5])
        )
    if res["skipped"]:
        st.caption("건너뛴 항목: " + ", ".join(res["skipped"][:5]))
    st.cache_data.clear()


# ------------------------------------------------------------------ 마법사


def wizard() -> None:
    """볼트가 없을 때의 첫 화면. 호출한 쪽이 이어서 `st.stop()` 한다."""
    st.title("🔍 멀티 LLM 리서치 에이전트")
    st.subheader("먼저 지식볼트를 만들어 주세요")
    st.caption(
        "지식볼트는 조사 결과가 쌓이는 **당신의 폴더**입니다. 마크다운 파일이라 "
        "Obsidian 으로 열어 직접 고칠 수 있고, 폴더째 복사하면 그대로 옮겨집니다. "
        "프로그램을 지워도 이 폴더는 남습니다."
    )

    done = st.session_state.get("_v_created")
    if done:
        st.success(
            f"**지식볼트를 만들었습니다** — 노트 {done['notes']}개\n\n`{done['path']}`"
        )
        if not done["preset"]:
            st.caption("이미 있던 Obsidian 설정은 그대로 두었습니다.")
        if done.get("seed_error"):
            # 폴더는 만들어졌다 — 시작 노트만 못 넣었다. 둘을 구분해서 말해야
            # 사용자가 "볼트가 안 만들어졌다"고 오해하지 않는다.
            st.warning(
                "폴더는 만들었지만 **시작 노트(ESG 시드 35개)를 넣지 못했습니다.** "
                f"{done['seed_error']}\n\n"
                "볼트는 그대로 쓰실 수 있습니다 — 문제가 풀리면 다음 실행에서 "
                "자동으로 채워집니다."
            )

        # zip 으로 시작한 경우에만 2단계가 이어진다. 폴더가 먼저 있어야
        # 가져오기가 쓸 저장소(sqlite 포함)가 생기기 때문에 순서가 이렇다.
        if done.get("then_import"):
            st.divider()
            st.markdown("**② 체험판 볼트 zip 올리기**")
            _import_block(store_mod.active(), "wiz")

        st.divider()
        _opened_block(done["path"])
        if st.button("🚀 조사 시작하기", type="primary", use_container_width=True):
            st.session_state.pop("_v_created", None)
            st.rerun()
        return

    choice = st.radio(
        "어떻게 시작할까요?",
        ["new", "import"],
        format_func=lambda c: "새로 만들기 — ESG 시드 노트 35개로 시작 (권장)"
        if c == "new" else "체험판에서 받은 볼트 zip 가져오기",
        key="_v_choice",
    )

    st.divider()
    if choice == "import":
        st.markdown("**① 볼트 폴더 먼저 만들기** — 가져온 노트가 여기에 풀립니다.")
    result = _create_block(
        "wiz", str(appdirs.default_vault_path()), appdirs.DEFAULT_VAULT_NAME,
        "✅ 지식볼트 만들기" if choice == "new" else "① 폴더 만들기",
    )
    if result:
        result["then_import"] = choice == "import"
        st.session_state["_v_created"] = result
        st.rerun()

    with st.expander("이 폴더에는 무엇이 들어가나요?"):
        st.markdown(
            f"""
| 폴더 | 내용 |
|---|---|
| `entities/` | 규제·기관·산업·이슈·지표 노트 — 조사할 때마다 사실이 쌓입니다 |
| `runs/` | 조사 1건마다 만들어지는 보고 노트 |
| `watch/` | 모니터링이 잡아낸 변화 요약 |
| `_index/` | 비서가 빨리 찾도록 만드는 색인 |
| `{appdirs.AGENT_DIRNAME}/` | 조사 이력 DB — Obsidian 에는 보이지 않습니다 |
"""
        )


# ------------------------------------------------------------------ 설정 화면


def manager(store) -> None:
    """⚙️ 설정 페이지의 지식볼트 절."""
    st.header("🗂 지식볼트")

    cfg = settings.load()
    items = settings.vaults(cfg)
    active_idx = cfg.get("active_vault", 0)

    if not items:
        st.info("아직 볼트가 없습니다 — 아래에서 만들어 주세요.")
    else:
        current = items[min(active_idx, len(items) - 1)]
        st.caption(f"현재 볼트 — **{current['name']}**")
        st.code(current["path"], language=None)
        _opened_block(current["path"])

    if len(items) > 1:
        st.markdown("**볼트 전환**")
        st.caption(
            "고객사별로 볼트를 나눠 쓸 때 씁니다. 바꾸면 조사 이력·감시 목록도 "
            "함께 바뀝니다."
        )
        pick = st.radio(
            "등록된 볼트", range(len(items)), index=min(active_idx, len(items) - 1),
            format_func=lambda i: f"{items[i]['name']} — {items[i]['path']}",
            key="_v_pick_active", label_visibility="collapsed",
        )
        c1, c2 = st.columns(2)
        if c1.button("전환", use_container_width=True, disabled=pick == active_idx):
            _switch_to(pick)
            st.rerun()
        if c2.button("목록에서 제거", use_container_width=True,
                     help="폴더와 노트는 지우지 않습니다 — 목록에서만 뺍니다."):
            settings.remove_vault(pick)
            store_mod.reset_cache()
            st.cache_data.clear()
            st.rerun()

    with st.expander("➕ 볼트 추가 · 새로 만들기"):
        st.caption(
            "고객사마다 볼트를 나누면 지식이 섞이지 않습니다. "
            "만들면 바로 그 볼트로 전환됩니다."
        )
        base = appdirs.default_vault_path()
        result = _create_block(
            "mgr", str(base.parent / f"{base.name} 2"), "새 볼트", "만들기",
        )
        if result:
            st.success(f"만들었습니다 — 노트 {result['notes']}개 · `{result['path']}`")
            st.cache_data.clear()
            st.rerun()

    if store is not None and store.is_configured():
        with st.expander("📥 체험판 볼트 zip 가져오기"):
            _import_block(store, "mgr")
