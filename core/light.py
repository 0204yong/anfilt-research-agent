"""라이트 모드 — 지식창고 우선(Knowledge-first) 저비용 조사 (→ docs/14).

배경: 풀 파이프라인은 실행당 `프로바이더 수 × (1+라운드) + 2`회의 대형 호출.
일상 업무 대부분은 반복 프레임의 재실행이므로, 라이트 모드는
0단계(볼트 검색 — 무료·app.py의 축적 지식 주입 재사용) →
1단계(경량 모델 1~2회 호출) → 2단계(기존 훅이 아카이브·온톨로지 축적)로
비용을 줄인다. 쓸수록 0단계 적중률이 올라가 비용이 더 내려간다.

풀 파이프라인과 같은 PipelineResult를 반환해 결과 화면·보고서 3종·
아카이브·볼트 내보내기를 전부 그대로 재사용한다. Streamlit 비의존.
"""
from . import packs, pipeline
from .pipeline import (
    AgentFinding,
    PipelineResult,
    ResearchBrief,
    _brief_block,
    _length_plan,
    _research_prompt,
)

# 업무 유형별 고정 템플릿 — "매번 같은 지시를 다시 쓰는" 반복을 없앤다
# (ESG_에이전트_업무패턴_분석.docx 2-2: 검증 업무 = 고정 4단계 프레임).
# {key: (표시명, instructions에 덧붙는 고정 지시)}
# 업무 템플릿·구조화 프롬프트는 프롬프트 팩에서 온다 (→ docs/22 7절).
# 임포트 시점에 팩을 읽지 않으려고 PEP 562 모듈 __getattr__ 로 늦게 꺼낸다.
_PACK_ATTRS = {
    "LIGHT_TEMPLATES": lambda: {k: tuple(v)
                               for k, v in packs.conf("light_templates").items()},
}


def __getattr__(name):
    if name in _PACK_ATTRS:
        return _PACK_ATTRS[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _structure_prompt(brief: ResearchBrief, target_pages: int,
                      research_text: str = "") -> str:
    plan = _length_plan(target_pages)
    return packs.render(
        "light.structure",
        brief_block=_brief_block(brief),
        research_block=(packs.render("light.research_block",
                                     research_text=research_text)
                        if research_text else ""),
        basis=packs.get("light.basis") if research_text else "",
        target=plan["target"],
        sections_line=(
            packs.get("synthesis.plan.sections_none") if plan["sections"] == 0
            else packs.render("synthesis.plan.sections",
                              sections=plan["sections"], depth=plan["depth"])),
        tables_line=(
            packs.get("synthesis.plan.tables_none") if plan["tables"] == 0
            else packs.render("synthesis.plan.tables", tables=plan["tables"])),
        counts=plan["counts"],
        summary=plan["summary"],
    )


def run_light_pipeline(provider, brief: ResearchBrief, target_pages: int = 6,
                       on_update=None) -> PipelineResult:
    """경량 모델 1~2회 호출로 조사한다.

    - 키워드가 있으면: 웹 검색 조사 1회 + 보고서 구조화 1회 (2회)
    - 없으면: 구조화 1회 (모델 자체 지식 + 레퍼런스/축적 지식 기반)
    반환은 풀 파이프라인과 동일한 PipelineResult (토론·채점 없음).
    """
    result = PipelineResult()
    research_text = ""
    if brief.keywords:
        if on_update:
            on_update(f"{provider.label} 경량 웹 조사 (`{provider.model}`)")
        research_text = provider.generate(
            _research_prompt(brief), system=brief.persona, web_search=True,
        )
    if on_update:
        on_update(f"{provider.label} 보고서 구조화 (`{provider.model}`)")
    report = provider.generate_json(
        _structure_prompt(brief, target_pages, research_text),
        system=brief.persona,
        schema=pipeline.REPORT_SCHEMA,
    )
    result.findings = [AgentFinding(
        provider.key, provider.label, provider.model,
        research_text or "(라이트 모드 — 웹 검색 조사 없이 보고서를 직접 생성. "
                         "키워드를 입력하면 경량 웹 조사가 선행됩니다)",
    )]
    result.report = report
    result.moderator_label = provider.label
    return result
