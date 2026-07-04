"""조사 → 토론 → 종합 파이프라인 오케스트레이션.

1) 조사: 모든 LLM이 동일한 브리프(주제/키워드/레퍼런스)를 받아 병렬로 독립 조사
2) 토론: 서로의 결과를 익명(연구원 A/B/C)으로 교차 검토하며 보완·반박
3) 종합: 진행자(moderator) LLM이 전체 내용을 평가해 구조화된 보고서 JSON 생성
"""
import string
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .config import DEFAULT_PERSONA
from .providers.base import BaseProvider

# ---------------------------------------------------------------- 데이터 모델


@dataclass
class ResearchBrief:
    topic: str
    keywords: list = field(default_factory=list)
    reference_urls: list = field(default_factory=list)
    reference_texts: dict = field(default_factory=dict)  # {url: 본문}
    instructions: str = ""
    persona: str = DEFAULT_PERSONA


@dataclass
class AgentFinding:
    provider_key: str
    provider_label: str
    model: str
    text: str
    error: str = ""


@dataclass
class DiscussionTurn:
    round_no: int
    provider_key: str
    provider_label: str
    text: str
    error: str = ""


@dataclass
class PipelineResult:
    findings: list = field(default_factory=list)       # [AgentFinding]
    discussion: list = field(default_factory=list)     # [DiscussionTurn]
    report: dict = field(default_factory=dict)         # 최종 보고서 JSON
    moderator_label: str = ""


# ------------------------------------------------------------ 보고서 스키마

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "executive_summary": {"type": "string"},
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "content": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "content", "bullets"],
                "additionalProperties": False,
            },
        },
        "data_tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "rows": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "required": ["title", "headers", "rows"],
                "additionalProperties": False,
            },
        },
        "recommendations": {"type": "array", "items": {"type": "string"}},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "title", "subtitle", "executive_summary", "key_findings",
        "sections", "data_tables", "recommendations", "sources",
    ],
    "additionalProperties": False,
}


# ------------------------------------------------------------- 프롬프트 구성


def _brief_block(brief: ResearchBrief) -> str:
    parts = [f"## 조사 주제\n{brief.topic}"]
    if brief.keywords:
        parts.append("## 검색 키워드\n" + ", ".join(brief.keywords))
    if brief.reference_texts:
        ref_parts = []
        for url, text in brief.reference_texts.items():
            ref_parts.append(f"### 출처: {url}\n{text}")
        parts.append("## 레퍼런스 자료 (전체 원문 발췌)\n" + "\n\n".join(ref_parts))
    elif brief.reference_urls:
        parts.append("## 레퍼런스 URL\n" + "\n".join(brief.reference_urls))
    if brief.instructions:
        parts.append("## 추가 지시사항\n" + brief.instructions)
    return "\n\n".join(parts)


def _research_prompt(brief: ResearchBrief) -> str:
    search_note = (
        "웹 검색 도구를 적극 활용하여 최신 정보와 수치를 확인하세요.\n"
        if brief.keywords
        else ""
    )
    return f"""{_brief_block(brief)}

## 작업
위 주제에 대해 심층 조사를 수행하고 결과를 한국어로 정리하세요.
{search_note}
결과는 다음 구조를 따르세요:

1. **핵심 요약** — 3~5문장
2. **주요 발견사항** — 번호를 붙인 핵심 포인트 (근거·수치 포함)
3. **세부 분석** — 소주제별 상세 내용
4. **데이터·수치** — 표로 정리 가능한 정량 정보 (연도, 금액, 비율 등)
5. **출처 목록** — 제목과 URL

정확성이 최우선입니다. 확인되지 않은 내용은 추정임을 명시하세요."""


_ANON_NAMES = [f"연구원 {c}" for c in string.ascii_uppercase]


def _discussion_prompt(
    brief: ResearchBrief, own_text: str, peers: list, round_no: int
) -> str:
    peer_block = "\n\n".join(
        f"### {name}의 조사 결과\n{text}" for name, text in peers
    )
    return f"""{_brief_block(brief)}

## 상황
당신을 포함한 여러 연구원이 같은 주제를 독립적으로 조사했습니다.
지금은 {round_no}차 상호 검토(토론) 단계입니다.

## 당신의 기존 조사 결과
{own_text}

## 동료 연구원들의 조사 결과
{peer_block}

## 작업
동료들의 결과를 비판적으로 검토한 뒤, 한국어로 다음을 작성하세요:

1. **동의/이견** — 동료 결과 중 동의하는 부분과 사실관계가 다르거나 근거가 약한 부분 (구체적으로 지목)
2. **내 결과의 보완** — 동료 결과에서 얻은 인사이트로 자신의 조사를 보완
3. **수정된 최종 입장** — 토론을 반영한 자신의 최종 조사 결과 (핵심 요약 + 주요 발견 + 데이터 + 출처)

근거 없는 양보는 하지 마세요. 자신의 결과가 더 정확하다면 그 근거를 제시하세요."""


def _length_plan(target_pages: int) -> dict:
    """목표 슬라이드 수 → 보고서 구성(섹션/표 개수, 서술 분량) 계획.

    - 1장: 원페이저 (제목·요약·핵심발견·제언을 한 슬라이드에 압축)
    - 2~7장: 컴팩트 — [표지+요약 통합](1) + 섹션(S) + 표(T) + [핵심발견+제언 통합](1)
    - 8장 이상: 표준 — 표지(1)+요약(1)+핵심발견(1)+섹션(S)+표(T)+제언(1)+출처(1)
    """
    if target_pages <= 1:
        return {
            "sections": 0,
            "tables": 0,
            "depth": "본문 섹션 없음",
            "counts": "key_findings 3~4개(각 한 문장), recommendations 2~3개(각 한 문장)",
            "summary": "executive_summary는 3~4문장으로 간결하게",
            "target": 1,
        }
    if target_pages <= 7:
        tables = 1 if target_pages >= 6 else 0
        sections = target_pages - 2 - tables
        return {
            "sections": sections,
            "tables": tables,
            "depth": "각 섹션 content는 150~250자, bullets 3~4개",
            "counts": "key_findings 3~5개, recommendations 2~4개",
            "summary": "executive_summary는 4~5문장",
            "target": target_pages,
        }
    remaining = max(3, target_pages - 5)
    tables = max(1, min(3, remaining // 5))
    sections = remaining - tables
    if target_pages <= 10:
        depth = "각 섹션 content는 200~350자, bullets 3~4개"
        counts = "key_findings 4~5개, recommendations 3~4개"
    elif target_pages <= 15:
        depth = "각 섹션 content는 400~600자, bullets 4~6개"
        counts = "key_findings 5~7개, recommendations 4~5개"
    else:
        depth = "각 섹션 content는 700~1000자, bullets 5~8개"
        counts = "key_findings 6~8개, recommendations 5~6개"
    return {
        "sections": sections,
        "tables": tables,
        "depth": depth,
        "counts": counts,
        "summary": "executive_summary는 5~8문장",
        "target": target_pages,
    }


def _synthesis_prompt(
    brief: ResearchBrief,
    findings: list,
    discussion: list,
    mode: str,
    target_pages: int = 12,
) -> str:
    latest = _latest_positions(findings, discussion)
    position_block = "\n\n".join(
        f"### {name} (최종 입장)\n{text}" for name, text in latest
    )
    if mode == "best":
        mode_instruction = (
            "각 연구원의 결과를 정확성·근거·완결성 기준으로 평가하여 "
            "가장 우수한 결과를 중심으로 보고서를 구성하되, "
            "다른 연구원의 결과에서 검증된 보완 정보만 선별적으로 반영하세요."
        )
    else:
        mode_instruction = (
            "모든 연구원의 결과를 교차 검증하여 종합하세요. "
            "여러 연구원이 공통으로 확인한 내용을 우선하고, "
            "상충하는 내용은 근거가 강한 쪽을 채택하되 불확실성을 명시하세요."
        )
    plan = _length_plan(target_pages)
    return f"""{_brief_block(brief)}

## 상황
여러 LLM 연구원이 같은 주제를 독립 조사하고 상호 토론을 거쳤습니다.
당신은 이 프로젝트의 총괄 책임자(진행자)로서 최종 보고서를 작성합니다.

## 연구원별 최종 입장
{position_block}

## 종합 방식
{mode_instruction}

## 분량 계획 (반드시 준수)
최종 보고서는 PPT 기준 약 {plan['target']}장 분량입니다. 이를 위해:
- sections: {"빈 배열 [] (본문 섹션 없음)" if plan['sections'] == 0 else f"정확히 {plan['sections']}개 작성 — {plan['depth']}"}
- data_tables: {"빈 배열 [] (데이터 표 없음)" if plan['tables'] == 0 else f"정확히 {plan['tables']}개 작성"}
- {plan['counts']}
- {plan['summary']}

## 작업
고객사에 제출할 수준의 최종 보고서 내용을 한국어로 작성하세요.
- title: 보고서 제목 (주제를 반영, 간결하게)
- subtitle: 부제 (조사 범위나 관점)
- executive_summary: 경영진 요약
- key_findings: 핵심 발견사항
- sections: 본문 섹션 (heading, content 상세 서술, bullets 요점)
- data_tables: 정량 데이터 표 (headers/rows, 모든 셀은 문자열)
- recommendations: 고객사 대상 제언
- sources: 인용 출처 (title, url) — 연구원들이 제시한 실제 출처만 사용
"""


def _latest_positions(findings: list, discussion: list) -> list:
    """토론 마지막 라운드의 입장(없으면 최초 조사 결과)을 익명 이름과 함께 반환."""
    name_map = {}
    for i, f in enumerate(findings):
        name_map[f.provider_key] = _ANON_NAMES[i]

    latest = {f.provider_key: f.text for f in findings if not f.error}
    for turn in discussion:  # round 순서대로 저장되므로 마지막 값이 최신
        if not turn.error:
            latest[turn.provider_key] = turn.text
    return [(name_map[k], v) for k, v in latest.items()]


# --------------------------------------------------------------- 실행 단계


def run_research(
    providers: list, brief: ResearchBrief, on_update=None
) -> list:
    """모든 프로바이더가 병렬로 독립 조사를 수행한다."""
    prompt = _research_prompt(brief)
    use_search = bool(brief.keywords)
    findings = []

    def _one(p: BaseProvider) -> AgentFinding:
        try:
            text = p.generate(prompt, system=brief.persona, web_search=use_search)
            return AgentFinding(p.key, p.label, p.model, text)
        except Exception as e:
            return AgentFinding(p.key, p.label, p.model, "", error=str(e))

    with ThreadPoolExecutor(max_workers=len(providers)) as ex:
        futures = {ex.submit(_one, p): p for p in providers}
        for fut in as_completed(futures):
            f = fut.result()
            findings.append(f)
            if on_update:
                status = "실패" if f.error else "완료"
                on_update(f"{f.provider_label} 조사 {status}")

    # 프로바이더 원래 순서 유지 (익명 이름 매핑 일관성)
    order = {p.key: i for i, p in enumerate(providers)}
    findings.sort(key=lambda f: order[f.provider_key])
    return findings


def run_discussion(
    providers: list,
    brief: ResearchBrief,
    findings: list,
    rounds: int,
    on_update=None,
) -> list:
    """교차 검토 토론을 rounds 회 수행한다."""
    name_map = {f.provider_key: _ANON_NAMES[i] for i, f in enumerate(findings)}
    current = {f.provider_key: f.text for f in findings if not f.error}
    active = [p for p in providers if p.key in current]
    if len(active) < 2:
        return []  # 토론 상대가 없음

    discussion = []
    for round_no in range(1, rounds + 1):
        def _one(p: BaseProvider) -> DiscussionTurn:
            own = current[p.key]
            peers = [
                (name_map[k], v) for k, v in current.items() if k != p.key
            ]
            try:
                text = p.generate(
                    _discussion_prompt(brief, own, peers, round_no),
                    system=brief.persona,
                )
                return DiscussionTurn(round_no, p.key, p.label, text)
            except Exception as e:
                return DiscussionTurn(round_no, p.key, p.label, "", error=str(e))

        round_turns = []
        with ThreadPoolExecutor(max_workers=len(active)) as ex:
            futures = {ex.submit(_one, p): p for p in active}
            for fut in as_completed(futures):
                t = fut.result()
                round_turns.append(t)
                if on_update:
                    status = "실패" if t.error else "완료"
                    on_update(f"{round_no}차 토론 — {t.provider_label} {status}")

        order = {p.key: i for i, p in enumerate(active)}
        round_turns.sort(key=lambda t: order[t.provider_key])
        discussion.extend(round_turns)
        # 다음 라운드 입력을 이번 라운드 결과로 갱신
        for t in round_turns:
            if not t.error:
                current[t.provider_key] = t.text
    return discussion


def run_synthesis(
    moderator: BaseProvider,
    brief: ResearchBrief,
    findings: list,
    discussion: list,
    mode: str = "synthesize",
    target_pages: int = 12,
) -> dict:
    """진행자 LLM이 최종 보고서 JSON을 생성한다."""
    prompt = _synthesis_prompt(brief, findings, discussion, mode, target_pages)
    return moderator.generate_json(prompt, system=brief.persona, schema=REPORT_SCHEMA)


def pick_moderator(providers: list) -> BaseProvider:
    """진행자 우선순위: Claude(구조화 출력 지원) → GPT → Gemini."""
    priority = {"anthropic": 0, "openai": 1, "gemini": 2}
    return sorted(providers, key=lambda p: priority.get(p.key, 9))[0]


def run_pipeline(
    providers: list,
    brief: ResearchBrief,
    rounds: int = 1,
    mode: str = "synthesize",
    target_pages: int = 12,
    on_update=None,
) -> PipelineResult:
    """전체 파이프라인을 실행한다. (Streamlit 밖에서도 사용 가능)"""
    result = PipelineResult()
    result.findings = run_research(providers, brief, on_update)
    ok = [f for f in result.findings if not f.error]
    if not ok:
        raise RuntimeError(
            "모든 프로바이더의 조사가 실패했습니다: "
            + "; ".join(f"{f.provider_label}: {f.error}" for f in result.findings)
        )
    if rounds > 0:
        result.discussion = run_discussion(
            providers, brief, result.findings, rounds, on_update
        )
    moderator = pick_moderator([p for p in providers if p.key in {f.provider_key for f in ok}])
    result.moderator_label = moderator.label
    if on_update:
        on_update(f"종합 단계 — 진행자: {moderator.label}")
    result.report = run_synthesis(
        moderator, brief, result.findings, result.discussion, mode, target_pages
    )
    return result
