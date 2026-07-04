"""멀티 LLM 리서치 에이전트 — Streamlit 웹 UI.

실행:  streamlit run app.py
"""
import hmac
import os
import re
from datetime import date

import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _bridge_secrets_to_env():
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


_bridge_secrets_to_env()


def _require_password():
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
    st.title("🔍 멀티 LLM 리서치 에이전트")
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


from core.config import DEFAULT_PERSONA, PROVIDER_SPECS, key_status, resolved_model
from core.discovery import discover_with_fallback
from core.filerefs import extract_file_text
from core.pipeline import (
    DEFAULT_CRITERIA_KEYS,
    SCORING_CRITERIA,
    ResearchBrief,
    run_pipeline,
)
from core.ppt_engines import list_engines
from core.providers import build_providers
from core.reports import build_docx, build_pptx, build_xlsx
from core.webfetch import fetch_references

st.set_page_config(page_title="멀티 LLM 리서치 에이전트", page_icon="🔍", layout="wide")

_require_password()  # 공개 배포 시 비밀번호 게이트 (APP_PASSWORD 미설정 시 앱 안 열림)

st.title("🔍 멀티 LLM 리서치 에이전트")
st.caption(
    "여러 LLM(Claude·GPT·Gemini)이 동일한 주제를 병렬 조사하고 상호 토론한 뒤, "
    "종합된 결과를 PPT / Word / Excel 보고서로 만들어 드립니다."
)

# ------------------------------------------------------------------ 사이드바

status = key_status()

with st.sidebar:
    st.header("⚙️ 설정")

    st.subheader("참여 LLM")
    selected_keys = []
    for spec in PROVIDER_SPECS:
        available = status[spec.key]
        label = f"{spec.label} — `{resolved_model(spec)}`"
        if available:
            if st.checkbox(label, value=True, key=f"prov_{spec.key}"):
                selected_keys.append(spec.key)
        else:
            st.checkbox(
                label + "  (API 키 없음)", value=False, disabled=True,
                key=f"prov_{spec.key}",
            )

    st.divider()
    rounds = st.slider(
        "토론 라운드 수", 0, 3, 1,
        help="0이면 토론 없이 개별 조사 결과를 바로 종합합니다.",
    )
    target_pages = st.slider(
        "보고서 분량 (PPT 슬라이드 기준 장수)", 1, 20, 12,
        help="목표 장수에 맞춰 본문 섹션 수·데이터 표 수·서술 분량이 자동 조절됩니다. "
        "1장은 원페이저, 2~7장은 컴팩트 구성, 8장 이상은 표준 구성으로 만들어집니다. "
        "Word/Excel 분량도 비례해서 달라집니다.",
    )
    mode = st.radio(
        "종합 방식",
        options=["synthesize", "best"],
        format_func=lambda v: "종합 — 모든 결과를 교차 검증해 통합"
        if v == "synthesize"
        else "베스트 선정 — 가장 우수한 결과 중심",
    )

    _crit_labels = {c["key"]: c["label"] for c in SCORING_CRITERIA}
    criteria_keys = st.multiselect(
        "채점 기준 (진행자 사전 평가)",
        [c["key"] for c in SCORING_CRITERIA],
        default=DEFAULT_CRITERIA_KEYS,
        format_func=lambda k: _crit_labels[k],
        help="종합 전에 진행자 LLM이 각 연구원의 최종 결과를 이 기준들로 채점(기준당 1~10점)하고 "
        "베스트를 선정합니다. 채점표는 결과 화면의 '채점표' 탭에서 확인할 수 있으며, "
        "종합 단계의 판단 근거로도 사용됩니다. 모두 해제하면 채점 없이 종합합니다. "
        "(참여 LLM이 2개 이상일 때만 채점이 수행됩니다)",
    )
    formats = st.multiselect(
        "보고서 형식",
        ["PPT", "Word", "Excel"],
        default=["PPT", "Word", "Excel"],
    )

    with st.expander("고급 설정"):
        persona = st.text_area(
            "연구원 페르소나 (시스템 프롬프트)", value=DEFAULT_PERSONA, height=160
        )

    if not any(status.values()):
        st.error(
            "사용 가능한 API 키가 없습니다.\n\n"
            "`.env.example`을 `.env`로 복사한 뒤 키를 입력하고 앱을 재시작하세요."
        )

# ------------------------------------------------------------------ 입력 영역

col1, col2 = st.columns(2)
with col1:
    topic = st.text_area(
        "조사 주제 *",
        placeholder="예) EU CBAM(탄소국경조정제도) 최신 동향과 국내 수출기업 대응 방안",
        height=90,
    )
    keywords_raw = st.text_input(
        "검색 키워드 (쉼표로 구분 — 입력 시 각 LLM이 웹 검색 수행)",
        placeholder="예) CBAM 전환기간, CBAM 인증서 가격, 탄소국경세",
    )
with col2:
    urls_raw = st.text_area(
        "레퍼런스 URL (한 줄에 하나 — 모든 LLM에 동일한 원문 제공)",
        placeholder="https://ec.europa.eu/...\nhttps://www.example.org/report",
        height=90,
    )
    instructions = st.text_input(
        "추가 지시사항 (선택)",
        placeholder="예) 2024년 이후 데이터 중심으로, 국내 철강업 관점에서 분석",
    )

uploaded_files = st.file_uploader(
    "📎 레퍼런스 파일 첨부 — 첨부한 문서의 내용이 모든 LLM에게 레퍼런스 원문으로 제공됩니다",
    type=["pdf", "docx", "pptx", "xlsx", "xlsm", "txt", "md", "csv"],
    accept_multiple_files=True,
)

# ------------------------------------------------------ 레퍼런스 자동 탐색

with st.container(border=True):
    dc1, dc2, dc3 = st.columns([6, 1.5, 2.5])
    with dc1:
        st.markdown(
            "**🔎 레퍼런스 자동 탐색** — 주제만 입력하면 LLM이 웹 검색으로 "
            "신뢰할 수 있는 출처(공식 기관·표준기구·연구기관 등)를 직접 찾아옵니다."
        )
    with dc2:
        n_refs = st.number_input(
            "찾을 개수", min_value=3, max_value=10, value=6,
            label_visibility="collapsed",
            help="찾을 레퍼런스 개수 (3~10)",
        )
    with dc3:
        discover_clicked = st.button(
            "레퍼런스 찾기", use_container_width=True,
            disabled=not any(status.values()),
        )

    if discover_clicked:
        if not topic.strip():
            st.error("먼저 조사 주제를 입력해 주세요.")
        elif not selected_keys:
            st.error("사이드바에서 참여 LLM을 최소 1개 선택해 주세요.")
        else:
            with st.spinner("LLM이 웹 검색으로 레퍼런스를 탐색하는 중... (실패 시 다른 LLM으로 자동 전환)"):
                try:
                    used_label, found = discover_with_fallback(
                        build_providers(selected_keys),
                        topic.strip(), instructions.strip(), int(n_refs),
                    )
                    # 이전 탐색의 체크박스 상태 초기화
                    for k in list(st.session_state.keys()):
                        if str(k).startswith("ref_sel_"):
                            del st.session_state[k]
                    st.session_state["discovered_refs"] = found
                    st.success(f"{used_label} 이(가) 레퍼런스 {len(found)}개를 찾았습니다.")
                except Exception as e:
                    st.error(f"레퍼런스 탐색 실패: {e}")

    discovered = st.session_state.get("discovered_refs", [])
    if discovered:
        st.caption("✔ 체크된 항목은 **조사 시작** 시 레퍼런스 원문으로 자동 포함됩니다.")
        for i, r in enumerate(discovered):
            label = f"**{r['title']}**"
            if r.get("publisher"):
                label += f" · {r['publisher']}"
            st.checkbox(label, value=True, key=f"ref_sel_{i}")
            caption = r["url"]
            if r.get("reason"):
                caption = f"{r['reason']}  \n{r['url']}"
            st.caption(caption)

run_clicked = st.button(
    "🚀 조사 시작", type="primary", use_container_width=True,
    disabled=not any(status.values()),
)

# ------------------------------------------------------------------ 실행

if run_clicked:
    if not topic.strip():
        st.error("조사 주제를 입력해 주세요.")
        st.stop()
    if not selected_keys:
        st.error("참여할 LLM을 최소 1개 선택해 주세요.")
        st.stop()

    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    urls = [u.strip() for u in urls_raw.splitlines() if u.strip()]
    # 자동 탐색에서 체크된 레퍼런스를 병합
    for i, r in enumerate(st.session_state.get("discovered_refs", [])):
        if st.session_state.get(f"ref_sel_{i}") and r["url"] not in urls:
            urls.append(r["url"])
    if not keywords and not urls and not uploaded_files:
        st.warning(
            "키워드·레퍼런스 URL·첨부 파일이 모두 비어 있어, 각 LLM의 자체 지식만으로 조사합니다. "
            "최신 정보가 필요하면 키워드를 입력하세요."
        )

    providers = build_providers(selected_keys)
    st.session_state.pop("result", None)
    st.session_state.pop("files", None)

    reference_texts = {}
    if urls:
        with st.status("📄 레퍼런스 자료 수집 중...", expanded=False) as s:
            reference_texts.update(fetch_references(urls))
            for url, text in reference_texts.items():
                ok = not text.startswith("[")
                s.write(("✅ " if ok else "⚠️ ") + url)
            s.update(label="📄 레퍼런스 자료 수집 완료", state="complete")

    if uploaded_files:
        with st.status("📎 첨부 파일 텍스트 추출 중...", expanded=False) as s:
            for uf in uploaded_files:
                text = extract_file_text(uf.name, uf.getvalue())
                ok = not text.startswith("[")
                reference_texts[f"[첨부 파일] {uf.name}"] = text
                s.write(("✅ " if ok else "⚠️ ") + uf.name + ("" if ok else f" — {text}"))
            s.update(label="📎 첨부 파일 처리 완료", state="complete")

    brief = ResearchBrief(
        topic=topic.strip(),
        keywords=keywords,
        reference_urls=urls,
        reference_texts=reference_texts,
        instructions=instructions.strip(),
        persona=persona.strip() or DEFAULT_PERSONA,
    )

    try:
        with st.status(
            f"🤖 파이프라인 실행 중 — 참여 LLM {len(providers)}개, 토론 {rounds}라운드",
            expanded=True,
        ) as s:
            def on_update(msg):
                s.write("• " + msg)

            result = run_pipeline(
                providers, brief, rounds=rounds, mode=mode,
                target_pages=target_pages,
                criteria=[c for c in SCORING_CRITERIA if c["key"] in criteria_keys],
                on_update=on_update,
            )
            s.update(label="✅ 조사·토론·종합 완료", state="complete")
        st.session_state["result"] = result
        st.session_state["target_pages_used"] = target_pages
    except Exception as e:
        st.error(f"파이프라인 실행 실패: {e}")
        st.stop()

# ------------------------------------------------------------------ 결과 표시

result = st.session_state.get("result")
if result:
    report = result.report
    tab_report, tab_score, tab_findings, tab_discussion, tab_download = st.tabs(
        ["📋 최종 보고서", "🏅 채점표", "🔎 개별 조사 결과", "💬 토론 내용", "⬇️ 다운로드"]
    )

    with tab_report:
        st.markdown(f"## {report.get('title', '')}")
        st.markdown(f"*{report.get('subtitle', '')}*")
        st.caption(f"종합 진행자: {result.moderator_label}")

        st.markdown("### Executive Summary")
        st.write(report.get("executive_summary", ""))

        if report.get("key_findings"):
            st.markdown("### 핵심 발견사항")
            for i, item in enumerate(report["key_findings"], 1):
                st.markdown(f"{i}. {item}")

        for sec in report.get("sections", []):
            st.markdown(f"### {sec.get('heading', '')}")
            st.write(sec.get("content", ""))
            for b in sec.get("bullets") or []:
                st.markdown(f"- {b}")

        for t in report.get("data_tables", []):
            st.markdown(f"### 📊 {t.get('title', '')}")
            headers, rows = t.get("headers", []), t.get("rows", [])
            if headers and rows:
                st.table(
                    [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in rows]
                )

        if report.get("recommendations"):
            st.markdown("### 제언")
            for i, item in enumerate(report["recommendations"], 1):
                st.markdown(f"{i}. {item}")

        if report.get("sources"):
            st.markdown("### 출처")
            for s_ in report["sources"]:
                st.markdown(f"- [{s_.get('title', '')}]({s_.get('url', '')})")

    with tab_score:
        card = getattr(result, "scorecard", None) or {}
        anon_map = getattr(result, "anon_map", None) or {}

        def _disp(name: str) -> str:
            """익명 이름(연구원 A)에 실제 LLM 이름을 붙여 표시."""
            real = anon_map.get(name)
            return f"{name} ({real})" if real else name

        if not card.get("evaluations"):
            st.info(
                "채점표가 없습니다 — 조사에 성공한 LLM이 2개 미만이었거나, "
                "사이드바에서 채점 기준을 모두 해제했거나, 채점 생성에 실패한 경우입니다."
            )
        else:
            st.markdown(f"## 🏅 베스트: {_disp(card.get('best', ''))}")
            st.write(card.get("rationale", ""))
            st.caption(
                f"채점자(진행자): {result.moderator_label} · 기준당 1~10점 · "
                "총점은 시스템이 재계산한 값입니다. 이 채점표가 종합 단계의 근거로 사용되었습니다."
            )

            evs = card["evaluations"]
            # 기준 × 연구원 점수 매트릭스
            crit_order = []
            for ev in evs:
                for s in ev.get("scores", []):
                    if s["criterion"] not in crit_order:
                        crit_order.append(s["criterion"])
            rows = []
            for crit in crit_order:
                row = {"채점 기준": crit}
                for ev in evs:
                    score = next(
                        (s["score"] for s in ev.get("scores", [])
                         if s["criterion"] == crit),
                        None,
                    )
                    row[_disp(ev.get("researcher", "?"))] = score
                rows.append(row)
            total_row = {"채점 기준": "합계 (총점)"}
            for ev in evs:
                total_row[_disp(ev.get("researcher", "?"))] = ev.get("total")
            rows.append(total_row)
            st.table(rows)

            st.markdown("### 기준별 채점 사유")
            best_name = card.get("best", "")
            for ev in evs:
                title = f"{_disp(ev.get('researcher', '?'))} — 총점 {ev.get('total')}"
                if ev.get("researcher") == best_name:
                    title = "🏅 " + title
                with st.expander(title, expanded=ev.get("researcher") == best_name):
                    for s in ev.get("scores", []):
                        st.markdown(
                            f"- **{s['criterion']} {s['score']}점** — {s.get('comment', '')}"
                        )
                    if ev.get("strengths"):
                        st.markdown(f"**강점** · {ev['strengths']}")
                    if ev.get("weaknesses"):
                        st.markdown(f"**약점** · {ev['weaknesses']}")

    with tab_findings:
        for f in result.findings:
            with st.expander(f"{f.provider_label} ({f.model})", expanded=False):
                if f.error:
                    st.error(f"조사 실패: {f.error}")
                else:
                    st.write(f.text)

    with tab_discussion:
        if not result.discussion:
            st.info("토론 라운드가 0이었거나 토론에 참여 가능한 LLM이 2개 미만이었습니다.")
        else:
            current_round = None
            for t in result.discussion:
                if t.round_no != current_round:
                    current_round = t.round_no
                    st.markdown(f"## 🗣️ {current_round}차 토론")
                with st.expander(f"{t.provider_label}", expanded=False):
                    if t.error:
                        st.error(f"발언 실패: {t.error}")
                    else:
                        st.write(t.text)

    with tab_download:
        # 파일은 한 번만 생성해 세션에 캐시 (download_button 클릭 시 rerun 대비)
        if "files" not in st.session_state:
            files = {}
            safe_title = re.sub(r'[\\/*?:"<>|]', "_", report.get("title", "report"))[:60]
            stem = f"{safe_title}_{date.today().isoformat()}"
            try:
                files["PPT"] = (
                    f"{stem}.pptx",
                    build_pptx(report, st.session_state.get("target_pages_used")),
                )
            except Exception as e:
                st.error(f"PPT 생성 실패: {e}")
            try:
                files["Word"] = (f"{stem}.docx", build_docx(report))
            except Exception as e:
                st.error(f"Word 생성 실패: {e}")
            try:
                files["Excel"] = (f"{stem}.xlsx", build_xlsx(report))
            except Exception as e:
                st.error(f"Excel 생성 실패: {e}")
            st.session_state["files"] = files

        files = st.session_state["files"]
        mime = {
            "PPT": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "Word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        icons = {"PPT": "📊", "Word": "📝", "Excel": "📈"}
        cols = st.columns(3)
        for col, fmt in zip(cols, ["PPT", "Word", "Excel"]):
            with col:
                if fmt in formats and fmt in files:
                    fname, data = files[fmt]
                    st.download_button(
                        f"{icons[fmt]} {fmt} 다운로드",
                        data=data,
                        file_name=fname,
                        mime=mime[fmt],
                        use_container_width=True,
                    )
                elif fmt not in formats:
                    st.button(
                        f"{icons[fmt]} {fmt} (미선택)", disabled=True,
                        use_container_width=True,
                    )

        # ---------------------------------- 외부 디자인 엔진 (선택)
        st.divider()
        st.markdown("#### 🎨 외부 디자인 엔진으로 PPT 만들기 (선택)")
        st.caption(
            "Gamma·Canva·Google Slides로 시각적 완성도가 높은 PPT를 만듭니다. "
            "`.env`에 해당 키를 넣으면 활성화됩니다. "
            "⚠️ 보고서 내용이 외부 서비스로 전송되므로 고객사 민감 자료는 주의하세요."
        )
        engines = list_engines()
        engine_labels = {
            e.key: e.label + ("" if e.available() else f"  (키 없음: {e.requires})")
            for e in engines
        }
        sel_engine_key = st.selectbox(
            "디자인 엔진",
            [e.key for e in engines],
            format_func=lambda k: engine_labels[k],
        )
        engine = next(e for e in engines if e.key == sel_engine_key)

        if not engine.available():
            st.info(
                f"이 엔진을 쓰려면 `.env`에 **{engine.requires}** 를 설정한 뒤 "
                "앱을 재시작하세요. (발급 방법은 README·설계서 참고)"
            )
        elif st.button("🎨 디자인 엔진 실행", use_container_width=True):
            with st.spinner(f"{engine.label} 생성 중... (1~3분 소요될 수 있습니다)"):
                try:
                    eres = engine.generate(
                        report, st.session_state.get("target_pages_used", 12)
                    )
                    st.session_state.setdefault("engine_results", {})[engine.key] = eres
                except Exception as e:
                    st.error(f"{engine.label} 생성 실패: {e}")

        eres = st.session_state.get("engine_results", {}).get(sel_engine_key)
        if eres:
            if eres.url:
                st.markdown(f"🔗 **[{eres.engine}에서 열기]({eres.url})**")
            if eres.pptx_bytes:
                st.download_button(
                    f"📊 {eres.engine} PPTX 다운로드",
                    data=eres.pptx_bytes,
                    file_name=f"{eres.engine}_report.pptx",
                    mime=mime["PPT"],
                    use_container_width=True,
                )
            if eres.note:
                st.caption(eres.note)
