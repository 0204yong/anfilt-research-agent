"""라이트 모드 — 지식창고 우선(Knowledge-first) 저비용 조사 (→ docs/14).

배경: 풀 파이프라인은 실행당 `프로바이더 수 × (1+라운드) + 2`회의 대형 호출.
일상 업무 대부분은 반복 프레임의 재실행이므로, 라이트 모드는
0단계(볼트 검색 — 무료·app.py의 축적 지식 주입 재사용) →
1단계(경량 모델 1~2회 호출) → 2단계(기존 훅이 아카이브·온톨로지 축적)로
비용을 줄인다. 쓸수록 0단계 적중률이 올라가 비용이 더 내려간다.

풀 파이프라인과 같은 PipelineResult를 반환해 결과 화면·보고서 3종·
아카이브·볼트 내보내기를 전부 그대로 재사용한다. Streamlit 비의존.
"""
from .pipeline import (
    REPORT_SCHEMA,
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
LIGHT_TEMPLATES = {
    "free": ("자유 조사", ""),
    "assurance": (
        "보고서 검증 — 4단계 프레임",
        "이 조사는 지속가능경영보고서 제3자 검증 실무를 위한 것이다. 다음 4단계 "
        "프레임으로 정리하라: ① 전년도 대비 달라졌을 수치·내용 영역과 확인 포인트 "
        "② 뉴스 등 외부 공개 소스로 사실 확인이 가능한 항목과 그 확인 결과 "
        "③ 오탈자·외래어 표기·단위 표기 등 표기 리스크 유형 "
        "④ GRI·AA1000·IFRS S2 등 기준 부합 여부 관점의 체크 항목.",
    ),
    "standards": (
        "공시기준 부합 검토 (GRI·ESRS·ISSB/KSSB)",
        "공시기준 부합 검토 관점으로 정리하라: 관련 기준서(GRI·ESRS·IFRS S1/S2·"
        "KSSB)의 요구 공시 항목을 식별하고, 항목별 요구사항·적용 시점·상호운용성 "
        "차이를 표로 대비하라. 기준서 조항 번호와 버전을 병기하라.",
    ),
    "lca": (
        "LCA·제품 탄소발자국(PCF)",
        "LCA/PCF 실무 관점으로 정리하라: 적용 표준(ISO 14040/44·14067, PEF 등)과 "
        "버전, 산정 범위와 기능단위, 배경 DB(ecoinvent 등)와 배출계수 출처, 최신 "
        "규제 요건(EU 배터리 규정·CBAM 등)을 구분해서 다루고 버전·연도를 병기하라.",
    ),
    "scenario": (
        "기후 시나리오·재무영향 (TCFD/IFRS S2)",
        "기후 시나리오 분석 실무 관점으로 정리하라: 적용 가능한 시나리오 세트"
        "(SSP·IEA·NGFS)와 최신 버전, 물리적/전환 리스크 구분, 재무영향 정량화 "
        "방법과 필요한 입력 데이터를 다루라. 시나리오 명칭과 발표 연도를 병기하라.",
    ),
}


def _structure_prompt(brief: ResearchBrief, target_pages: int,
                      research_text: str = "") -> str:
    plan = _length_plan(target_pages)
    research_block = (
        f"\n\n## 사전 조사 메모 (방금 웹 검색으로 직접 조사한 내용)\n{research_text}"
        if research_text else ""
    )
    basis = "위 사전 조사 메모를 바탕으로 " if research_text else ""
    return f"""{_brief_block(brief)}{research_block}

## 작업
{basis}조사 주제에 대해 고객사에 제출할 수준의 보고서 내용을 한국어로 작성하세요.
- 정확성이 최우선입니다. 핵심 수치·주장에는 출처(기관명·연도)를 병기하고,
  확인되지 않은 내용은 추정임을 명시하세요.
- '[축적 지식]' 레퍼런스가 있다면 검증된 전제가 아니라 대조 대상으로만 사용하세요.

## 분량 계획 (반드시 준수)
최종 보고서는 PPT 기준 약 {plan['target']}장 분량입니다. 이를 위해:
- sections: {"빈 배열 [] (본문 섹션 없음)" if plan['sections'] == 0 else f"정확히 {plan['sections']}개 작성 — {plan['depth']}"}
- data_tables: {"빈 배열 [] (데이터 표 없음)" if plan['tables'] == 0 else f"정확히 {plan['tables']}개 작성"}
- {plan['counts']}
- {plan['summary']}

## 출력 필드
- title: 보고서 제목 (주제를 반영, 간결하게)
- subtitle: 부제 (조사 범위나 관점)
- executive_summary: 경영진 요약
- key_findings: 핵심 발견사항
- sections: 본문 섹션 (heading, content 상세 서술, bullets 요점)
- data_tables: 정량 데이터 표 (headers/rows, 모든 셀은 문자열)
- recommendations: 고객사 대상 제언
- sources: 인용 출처 (title, url) — 실제 확인한 출처만
"""


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
        schema=REPORT_SCHEMA,
    )
    result.findings = [AgentFinding(
        provider.key, provider.label, provider.model,
        research_text or "(라이트 모드 — 웹 검색 조사 없이 보고서를 직접 생성. "
                         "키워드를 입력하면 경량 웹 조사가 선행됩니다)",
    )]
    result.report = report
    result.moderator_label = provider.label
    return result
