"""⚙️ 설정 — API 키 등록·검증, 모델 선택, 프로그램 정보.

→ docs/23 설치판 구현 계획 3단계 · docs/22 설치판 아키텍처 6절.

**키는 OS 자격 증명 저장소에 들어간다** (→ core/keys.py). 화면에 다시 보여주지
않고, 등록 여부와 끝 4자리만 표시한다.

온보딩 순서가 Gemini 부터인 이유: 무료 티어가 있어 카드 없이 시작할 수 있고,
이 앱의 기본이 라이트 모드라 **Gemini 키 하나로 전 기능이 돈다**
(→ docs/18 설치형 패키지 4절). Claude·GPT 는 토론 품질을 올리고 싶을 때 더한다.
"""
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from ui_common import bootstrap, nav  # noqa: E402

bootstrap("설정 — 리서치 에이전트", page_icon="⚙️", layout="centered")
nav()

import ui_license  # noqa: E402
import ui_mobile  # noqa: E402
import ui_update  # noqa: E402
import ui_vault  # noqa: E402
from core import appdirs, edition, keys, packs, settings  # noqa: E402
from core import notify  # noqa: E402
from core import watch as watch_mod  # noqa: E402
from core import store as store_mod  # noqa: E402
from core.config import (  # noqa: E402
    LIGHT_MODEL_DEFAULTS,
    PROVIDER_SPECS,
    api_key_for,
    resolved_light_model,
    resolved_model,
)
from core.providers import verify_key  # noqa: E402

st.title("⚙️ 설정")

if not edition.can("settings_page"):
    st.info(
        "이 화면은 **설치판 전용**입니다. 체험판은 서버에 등록된 키로 동작합니다."
    )
    st.stop()

# ---------------------------------------------------------------- 라이선스

# 맨 위에 둔다 — 잠겨 있으면 다른 설정이 무의미하고, 잠긴 이유를 먼저
# 알려 주는 것이 사용자에게 친절하다 (→ docs/20).
ui_license.section()

st.divider()

# ---------------------------------------------------------------- 지식볼트

# 키보다 볼트를 위에 둔다 — 볼트가 없으면 조사 결과를 둘 곳이 없고,
# 첫 화면 마법사도 볼트부터 묻는다 (→ docs/23 4단계).
ui_vault.manager(store_mod.active())

st.divider()

# ---------------------------------------------------------------- API 키

st.header("🔑 API 키")
st.caption(
    "조사에 쓸 LLM 계정을 등록합니다. **키는 이 PC의 자격 증명 저장소에만** "
    "저장되고 외부로 전송되지 않습니다 — 조사 요청은 등록하신 계정으로 직접 나갑니다."
)

if not keys.available():
    st.warning(
        "이 환경에서는 키를 안전하게 저장할 수 없습니다(자격 증명 저장소를 찾지 못함). "
        "`.env` 파일의 값으로만 동작합니다."
    )

# Gemini 를 맨 앞에 (무료 티어 → 카드 없이 시작)
_ORDER = {"gemini": 0, "anthropic": 1, "openai": 2}
_ISSUE_URL = {
    "gemini": "https://aistudio.google.com/apikey",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
}
_HINT = {
    "gemini": "**무료 티어가 있어 카드 등록 없이 시작할 수 있습니다.** "
              "이 키 하나만 있어도 라이트 모드로 전 기능을 쓸 수 있습니다.",
    "anthropic": "토론 품질을 올리고 싶을 때 추가하세요 (선택).",
    "openai": "토론 품질을 올리고 싶을 때 추가하세요 (선택).",
}

for spec in sorted(PROVIDER_SPECS, key=lambda s: _ORDER.get(s.key, 9)):
    var = spec.env_vars[0]
    current = api_key_for(spec)
    in_keyring = keys.stored_in_keyring(var)
    if current:
        origin = "이 PC에 저장됨" if in_keyring else "환경변수(.env)"
        badge = f"✅ 등록됨 · …{current[-4:]} · {origin}"
    else:
        badge = "⬜ 미등록"

    with st.expander(f"{spec.label} — {badge}", expanded=not current):
        st.caption(_HINT.get(spec.key, ""))
        st.markdown(f"[키 발급받기]({_ISSUE_URL.get(spec.key, '')})")

        new_key = st.text_input(
            "API 키", type="password", key=f"key_{spec.key}",
            placeholder="붙여넣기 (등록 후에는 다시 표시되지 않습니다)",
        )
        c1, c2, c3 = st.columns(3)
        if c1.button("저장", key=f"save_{spec.key}", use_container_width=True,
                     disabled=not new_key):
            try:
                keys.set(var, new_key.strip())
                st.success("저장했습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"저장 실패: {e}")
        if c2.button("검증", key=f"test_{spec.key}", use_container_width=True,
                     disabled=not (new_key or current),
                     help="실제로 한 번 호출해 키가 통하는지 확인합니다."):
            with st.spinner("확인 중..."):
                ok, note = verify_key(spec, (new_key or "").strip() or None)
            (st.success if ok else st.error)(note)
        if c3.button("삭제", key=f"del_{spec.key}", use_container_width=True,
                     disabled=not in_keyring):
            keys.delete(var)
            st.info("이 PC에서 지웠습니다.")
            st.rerun()

if not any(api_key_for(s) for s in PROVIDER_SPECS):
    st.error("키가 하나도 없습니다 — **Gemini** 부터 등록하시면 바로 시작할 수 있습니다.")

# ---------------------------------------------------------------- 모델

st.divider()
st.header("🤖 모델")
st.caption(
    "비워 두면 기본값을 씁니다. 모델 이름이 바뀌었을 때만 손대세요 — "
    "잘못 적으면 조사가 실패합니다."
)

cfg = settings.load()
models = dict(cfg.get("models") or {})
light_models = dict(cfg.get("light_models") or {})
changed = False

for spec in sorted(PROVIDER_SPECS, key=lambda s: _ORDER.get(s.key, 9)):
    if not api_key_for(spec):
        continue
    st.subheader(spec.label)
    c1, c2 = st.columns(2)
    full = c1.text_input(
        "일반 모델 (풀 모드)", value=models.get(spec.key, ""),
        placeholder=spec.default_model, key=f"m_{spec.key}",
    ).strip()
    lite = c2.text_input(
        "경량 모델 (라이트 모드)", value=light_models.get(spec.key, ""),
        placeholder=LIGHT_MODEL_DEFAULTS.get(spec.key, ""), key=f"lm_{spec.key}",
    ).strip()
    if full != models.get(spec.key, ""):
        models[spec.key] = full
        changed = True
    if lite != light_models.get(spec.key, ""):
        light_models[spec.key] = lite
        changed = True
    st.caption(f"적용 중 — 풀 `{resolved_model(spec)}` · 라이트 `{resolved_light_model(spec)}`")

if changed and st.button("모델 설정 저장", type="primary"):
    cfg["models"] = {k: v for k, v in models.items() if v}
    cfg["light_models"] = {k: v for k, v in light_models.items() if v}
    settings.save(cfg)
    st.success("저장했습니다.")
    st.rerun()

# ---------------------------------------------------------------- 알림

st.divider()
st.header("🔔 알림")
st.caption(
    "모니터링이 새 항목을 찾았을 때 알려 드리는 방법입니다. "
    "**새 항목이 있을 때만** 보냅니다 — 조용한 날은 아무 것도 오지 않습니다."
)

_ch = notify.channel_status()
_n1, _n2 = st.columns(2)
_n1.metric("🔔 윈도우 알림", "쓸 수 있음" if _ch.get("toast") else "이 환경에서 불가")
_n2.metric("📧 이메일", "설정됨" if _ch.get("email") else "미설정")

st.caption(
    "**윈도우 알림은 설정할 것이 없습니다** — 화면 오른쪽 아래에 뜹니다. "
    "다만 PC 앞에 있어야 보입니다. 밖에서도 받으시려면 이메일을 설정하세요."
)
if st.button("🔔 지금 알림 띄워 보기", key="_toast_test",
             disabled=not _ch.get("toast")):
    ok, note = notify.send_toast("리서치 에이전트", "알림이 이렇게 뜹니다.")
    (st.success if ok else st.error)(note)

with st.expander("📧 이메일 설정" + ("" if _ch.get("email") else " — 미설정"),
                 expanded=not _ch.get("email")):
    st.caption(
        "**쓰시는 메일로 알림을 보냅니다.** 그 메일함에 앱이 로그인해야 하므로 "
        "주소와 **앱 비밀번호**가 필요합니다. 서버 주소 같은 건 저희가 채웁니다."
    )
    # 쓰는 메일을 고르게 하고 서버·포트는 우리가 채운다 — 고객이 'SMTP 서버'를
    # 알 이유가 없다. 회사 메일만 직접 입력으로 보낸다.
    _keys = list(notify.SMTP_PRESETS)
    _cur_preset = notify.preset_for_host(notify.conf("SMTP_HOST"))
    _sel = st.selectbox(
        "쓰시는 메일", _keys, index=_keys.index(_cur_preset),
        format_func=lambda k: notify.SMTP_PRESETS[k][0], key="_sm_kind",
    )
    _label, _phost, _pport, _purl, _pnote = notify.SMTP_PRESETS[_sel]

    if _sel == "custom":
        _c1, _c2 = st.columns([3, 1])
        _host = _c1.text_input("메일 서버 주소", value=notify.conf("SMTP_HOST"),
                               placeholder="smtp.회사.co.kr", key="_sm_host")
        _port = _c2.text_input("포트", value=notify.conf("SMTP_PORT") or "587",
                               key="_sm_port")
    else:
        _host, _port = _phost, str(_pport)
        st.caption(f"서버 `{_phost}` · 포트 `{_pport}` — 자동으로 채웁니다.")

    _user = st.text_input(
        "내 메일 주소", value=notify.conf("SMTP_USER"), key="_sm_user",
        placeholder="you@gmail.com" if _sel == "gmail" else "",
        help="알림을 **보낼 때 쓸** 메일함입니다. 앱 비밀번호를 발급받은 그 계정과 같아야 합니다.",
    )
    _pw = st.text_input(
        "앱 비밀번호", type="password", key="_sm_pw",
        placeholder=("등록됨 — 바꿀 때만 입력하세요" if notify.conf("SMTP_PASSWORD")
                     else "메일 서비스에서 발급받은 값"),
        help="**평소 로그인하는 비밀번호가 아닙니다.** 이 앱 전용으로 따로 발급받는 "
             "값이고, 언제든 취소할 수 있습니다. 이 PC 의 자격 증명 저장소에만 저장됩니다.",
    )
    if _purl:
        st.caption(f"→ [{_label} 설정 열기]({_purl}) — {_pnote}")
    else:
        st.caption(f"→ {_pnote}")
    # 여기서 막히는 사람이 많다. "발급" 이라는 말 때문에 신청하면 메일이 온다고
    # 읽는다 — 실제로는 그 자리에서 화면에 한 번 뜨고 다시는 안 보인다.
    st.info(
        "💡 **앱 비밀번호는 메일로 오지 않습니다.** 발급 화면에 그 자리에서 "
        "16자리가 뜨고, 창을 닫으면 다시 볼 수 없습니다 — 뜨는 즉시 복사해서 "
        "위 칸에 붙여 넣으세요. 공백은 있어도 없어도 됩니다."
    )

    _to = st.text_input(
        "알림 받을 주소", value=notify.conf("NOTIFY_EMAIL_TO"), key="_sm_to",
        placeholder="비우면 내 메일 주소로 보냅니다",
        help="다른 사람에게 보내려면 그 주소를 적으세요.",
    )
    _s1, _s2 = st.columns(2)
    if _s1.button("저장", type="primary", use_container_width=True, key="_sm_save"):
        _vals = {"SMTP_HOST": _host, "SMTP_PORT": _port, "SMTP_USER": _user,
                 "NOTIFY_EMAIL_TO": _to}
        if _pw.strip():                     # 비우면 이미 저장된 것을 지우지 않는다
            _vals["SMTP_PASSWORD"] = _pw
        try:
            notify.save_conf(_vals)
            st.success("저장했습니다.")
            st.rerun()
        except Exception as e:
            st.error(f"저장 실패: {e}")
    if _s2.button("시험 발송", use_container_width=True, key="_sm_test",
                  disabled=not _ch.get("email")):
        with st.spinner("보내는 중..."):
            ok, note = notify.send_email(
                "[리서치 에이전트] 알림 시험",
                "이 메일이 보이면 알림 설정이 끝난 것입니다.\n"
                "모니터링이 새 항목을 찾으면 이런 식으로 알려 드립니다.")
        (st.success if ok else st.error)(note)

with st.expander("💬 카카오톡 — 설정이 까다롭습니다", expanded=False):
    st.caption(
        "카카오 개발자 사이트에서 **앱을 직접 등록**해야 합니다 "
        "(REST API 키 → 카카오 로그인 활성화 → Redirect URI → 동의항목 "
        "`talk_message` → 인가코드로 리프레시 토큰 발급). "
        "검수 없이 되는 것은 **'나에게 보내기'뿐**입니다. "
        "먼저 이메일을 쓰시고, 꼭 필요할 때 설정하세요."
    )
    _k1 = st.text_input("REST API 키", type="password", key="_kk_key",
                        placeholder=("등록됨" if notify.conf("KAKAO_REST_API_KEY")
                                     else ""))
    _k2 = st.text_input("리프레시 토큰", type="password", key="_kk_ref",
                        placeholder=("등록됨" if notify.conf("KAKAO_REFRESH_TOKEN")
                                     else ""))
    _kc1, _kc2 = st.columns(2)
    if _kc1.button("저장", use_container_width=True, key="_kk_save"):
        _kv = {}
        if _k1.strip():
            _kv["KAKAO_REST_API_KEY"] = _k1
        if _k2.strip():
            _kv["KAKAO_REFRESH_TOKEN"] = _k2
        if _kv:
            notify.save_conf(_kv)
            st.success("저장했습니다.")
            st.rerun()
        else:
            st.info("입력한 값이 없습니다.")
    if _kc2.button("시험 발송", use_container_width=True, key="_kk_test",
                   disabled=not _ch.get("kakao")):
        ok, note = notify.send_kakao("[리서치 에이전트] 알림 시험입니다.")
        (st.success if ok else st.error)(note)

# ------------------------------------------------------- 모니터링 중요도 기준

st.divider()
st.header("📡 모니터링 중요도 기준")
st.caption(
    "감시가 찾아낸 항목에 매기는 1~5 단계의 뜻입니다. **회사마다 5점의 의미가 "
    "다릅니다** — 규제 감시와 경쟁사 동향 감시가 같은 잣대를 쓸 이유가 없습니다. "
    "설명을 고치면 다음 점검부터 그 기준으로 매깁니다."
)
with st.expander("단계별 뜻 보기·고치기", expanded=False):
    # 칸은 다섯 개로 **고정**이다. 일곱 줄을 적게 두면 스키마도 정렬도 어긋난다 —
    # 고칠 수 있는 것은 설명뿐이다.
    _cur = watch_mod.importance_scale()
    _new = []
    for _n in range(watch_mod.IMPORTANCE_LEVELS, 0, -1):
        # 한 줄짜리 칸은 이 설명들에 너무 짧다 — 문장이 넘치면 앞이 밀려
        # 나가 무엇을 고치는 중인지 볼 수 없다. 여러 줄 상자는 아래로 접히고,
        # 모서리를 끌어 더 키울 수도 있다. 줄바꿈은 저장할 때 공백으로 편다.
        # height 는 라벨을 포함한 칸 전체다 — 28(라벨) + 네 줄. 세 줄로 잡으면
        # 글 상자의 최소 높이(68)에 눌려 두 줄밖에 안 나온다.
        _raw = st.text_area(
            f"{_n}단계", value=_cur[_n - 1], key=f"_imp_{_n}", height=28 + 22 * 4,
        )
        _new.append((_n, " ".join(str(_raw or "").split())))
    st.caption(
        "비워 두면 기본 설명으로 돌아갑니다. 같은 단계가 여러 건이면 "
        "**등록일 최신순 → 제목순**으로 정렬합니다 (LLM 이 아니라 코드가 정합니다)."
    )
    _c1, _c2 = st.columns(2)
    if _c1.button("기준 저장", type="primary", use_container_width=True, key="_imp_save"):
        cfg = settings.load()
        cfg["watch_importance_scale"] = [v for _, v in sorted(_new)]
        settings.save(cfg)
        st.success("저장했습니다. 다음 점검부터 적용됩니다.")
        st.rerun()
    if _c2.button("기본값으로 되돌리기", use_container_width=True, key="_imp_reset"):
        cfg = settings.load()
        cfg.pop("watch_importance_scale", None)
        settings.save(cfg)
        st.success("기본값으로 되돌렸습니다.")
        st.rerun()

# ---------------------------------------------------------------- 업데이트

st.divider()
ui_update.section()

# ---------------------------------------------------------------- 휴대폰·진단

st.divider()
ui_mobile.mobile_section()

st.divider()
ui_mobile.diagnostics_section()

# ---------------------------------------------------------------- 정보

st.divider()
st.header("ℹ️ 프로그램 정보")
st.caption(
    "지원 문의 시 이 정보를 함께 알려 주시면 원인을 빨리 찾을 수 있습니다."
)
info = {
    "버전": edition.app_version(),
    # 팩이 없어도 이 화면은 떠야 한다 — 문제를 진단하러 오는 곳이기 때문이다.
    #
    # "재설치 필요" 라고 적혀 있었는데 **틀린 처방**이다. 팩은 설치 파일에 들어
    # 있지 않고 활성화로 서버에서 받는다(→ docs/26) — 다시 깔아도 그대로다.
    # 고객이 그 말을 믿고 재설치하면 시간만 버리고 같은 화면으로 돌아온다.
    "구성요소": (
        packs.version() if packs.is_available()
        else ("없음 — 활성화가 필요합니다" if edition.is_installed()
              else "없음 — 서버에서 받지 못했습니다")
    ),
    "에디션": edition.current(),
    "설정 폴더": str(appdirs.data_dir()),
}
vault = settings.active_vault(cfg)
info["지식볼트"] = vault["path"] if vault else "미설정 (조사 시 자동 생성)"
for k, v in info.items():
    st.text(f"{k:8} {v}")
