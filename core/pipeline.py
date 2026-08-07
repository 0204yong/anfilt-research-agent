"""조사 → 토론 → 채점 → 종합 파이프라인 오케스트레이션.

1) 조사: 모든 LLM이 동일한 브리프(주제/키워드/레퍼런스)를 받아 병렬로 독립 조사
2) 토론: 서로의 결과를 익명(연구원 A/B/C)으로 교차 검토하며 보완·반박
3) 채점: 진행자(moderator) LLM이 연구원별 최종 입장을 기준별 1~10점으로 채점하고
   베스트를 선정 (채점표는 UI에 공개, 종합 단계의 근거로 주입)
4) 종합: 진행자 LLM이 채점표를 근거로 전체 내용을 평가해 구조화된 보고서 JSON 생성
"""
import string
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import packs
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
    scorecard: dict = field(default_factory=dict)      # 진행자 채점표 JSON
    report: dict = field(default_factory=dict)         # 최종 보고서 JSON
    moderator_label: str = ""
    anon_map: dict = field(default_factory=dict)       # {"연구원 A": provider_label}


# ------------------------------------------------------------ 보고서 스키마

# 보고서 스키마·채점 기준표는 프롬프트 팩에서 온다 (→ docs/22 7절).
#
# **모듈 임포트 시점에 팩을 읽지 않는다.** 팩이 없거나 손상돼도 앱은 떠야 하고
# 볼트 열람은 계속돼야 하기 때문이다 (→ docs/22 8절 상태표). 그래서 PEP 562
# 모듈 `__getattr__` 로 **실제 접근하는 순간에** 팩에서 꺼낸다 — 프록시가 아니라
# 진짜 dict/list 가 나오므로 SDK 에 그대로 넘겨도 안전하다.
#
# 쓰는 쪽은 `from core import pipeline` 후 `pipeline.REPORT_SCHEMA` 로 접근한다.
# `from core.pipeline import REPORT_SCHEMA` 는 임포트 시점에 팩을 읽게 되므로 쓰지 않는다.
_PACK_ATTRS = {
    "REPORT_SCHEMA": lambda: packs.schema("report"),
    "SCORING_CRITERIA": lambda: packs.conf("scoring_criteria"),
    "DEFAULT_CRITERIA_KEYS": lambda: packs.conf("default_criteria_keys"),
}


def __getattr__(name):
    if name in _PACK_ATTRS:
        return _PACK_ATTRS[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _scorecard_schema(criterion_labels: list, researcher_names: list) -> dict:
    """채점표 구조화 출력 스키마. (min/max 같은 수치 제약은 프로바이더별 지원이
    갈리므로 스키마에 넣지 않고, 프롬프트 지시 + 코드 클램프로 보장한다.)"""
    return {
        "type": "object",
        "properties": {
            "evaluations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "researcher": {"type": "string", "enum": researcher_names},
                        "scores": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "criterion": {
                                        "type": "string", "enum": criterion_labels,
                                    },
                                    "score": {"type": "integer"},
                                    "comment": {"type": "string"},
                                },
                                "required": ["criterion", "score", "comment"],
                                "additionalProperties": False,
                            },
                        },
                        "strengths": {"type": "string"},
                        "weaknesses": {"type": "string"},
                    },
                    "required": [
                        "researcher", "scores", "strengths", "weaknesses",
                    ],
                    "additionalProperties": False,
                },
            },
            "best": {"type": "string", "enum": researcher_names},
            "rationale": {"type": "string"},
        },
        "required": ["evaluations", "best", "rationale"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------- 프롬프트 구성


def _brief_block(brief: ResearchBrief, include_accumulated: bool = True) -> str:
    """브리프 → 프롬프트 블록.

    include_accumulated=False면 '[축적 지식]' 레퍼런스를 제외한다 — 채점 단계
    전용 (진행자가 기존 지식과 일치하는 연구원을 높게 평가하면 기존 지식과
    어긋나는 가장 가치 있는 신규 발견이 저평가되므로. → docs/13 설계 원칙 2)
    """
    lab = packs.conf("brief_labels")
    parts = [f"{lab['topic']}\n{brief.topic}"]
    if brief.keywords:
        parts.append(f"{lab['keywords']}\n" + ", ".join(brief.keywords))
    if brief.reference_texts:
        ref_parts = []
        for url, text in brief.reference_texts.items():
            if not include_accumulated and url.startswith(lab["accumulated_prefix"]):
                continue
            ref_parts.append(f"{lab['reference_source']}{url}\n{text}")
        parts.append(f"{lab['reference_texts']}\n" + "\n\n".join(ref_parts))
    elif brief.reference_urls:
        parts.append(f"{lab['reference_urls']}\n" + "\n".join(brief.reference_urls))
    if brief.instructions:
        parts.append(f"{lab['instructions']}\n" + brief.instructions)
    return "\n\n".join(parts)


def _research_prompt(brief: ResearchBrief) -> str:
    return packs.render(
        "research",
        brief_block=_brief_block(brief),
        search_note=packs.get("research.search_note") if brief.keywords else "",
    )


def _anon_names() -> list:
    """익명 이름 목록. 팩을 임포트 시점에 읽지 않으려고 함수로 둔다."""
    prefix = packs.conf("anon_prefix")
    return [f"{prefix}{c}" for c in string.ascii_uppercase]


def _discussion_prompt(
    brief: ResearchBrief, own_text: str, peers: list, round_no: int
) -> str:
    peer_block = "\n\n".join(
        f"### {name}의 조사 결과\n{text}" for name, text in peers
    )
    return packs.render(
        "discussion",
        brief_block=_brief_block(brief),
        round_no=round_no,
        own_text=own_text,
        peer_block=peer_block,
    )


def _length_plan(target_pages: int) -> dict:
    """목표 슬라이드 수 → 보고서 구성(섹션/표 개수, 서술 분량) 계획.

    - 1장: 원페이저 (제목·요약·핵심발견·제언을 한 슬라이드에 압축)
    - 2~7장: 컴팩트 — [표지+요약 통합](1) + 섹션(S) + 표(T) + [핵심발견+제언 통합](1)
    - 8장 이상: 표준 — 표지(1)+요약(1)+핵심발견(1)+섹션(S)+표(T)+제언(1)+출처(1)

    **개수 산식은 코드에 남고, 문구(depth/counts/summary)는 팩에서 온다** —
    LLM에게 주는 서술 지시는 프롬프트 자산이지만 산식은 조립 로직이기 때문이다.
    """
    text = packs.conf("length_plan")
    if target_pages <= 1:
        band = text["onepager"]
        return {
            "sections": 0,
            "tables": 0,
            "depth": band["depth"],
            "counts": band["counts"],
            "summary": band["summary"],
            "target": 1,
        }
    if target_pages <= 7:
        tables = 1 if target_pages >= 6 else 0
        sections = target_pages - 2 - tables
        band = text["compact"]
        return {
            "sections": sections,
            "tables": tables,
            "depth": band["depth"],
            "counts": band["counts"],
            "summary": band["summary"],
            "target": target_pages,
        }
    remaining = max(3, target_pages - 5)
    tables = max(1, min(3, remaining // 5))
    sections = remaining - tables
    if target_pages <= 10:
        band = text["standard_small"]
    elif target_pages <= 15:
        band = text["standard_mid"]
    else:
        band = text["standard_large"]
    return {
        "sections": sections,
        "tables": tables,
        "depth": band["depth"],
        "counts": band["counts"],
        "summary": text["standard_summary"],
        "target": target_pages,
    }


def _scoring_prompt(brief: ResearchBrief, latest: list, criteria: list) -> str:
    position_block = "\n\n".join(
        f"### {name} (최종 입장)\n{text}" for name, text in latest
    )
    criteria_block = "\n".join(f"- **{c['label']}** — {c['desc']}" for c in criteria)
    return packs.render(
        "scoring",
        brief_block=_brief_block(brief, include_accumulated=False),
        position_block=position_block,
        criteria_block=criteria_block,
    )


def _scorecard_block(scorecard: dict) -> str:
    """채점표를 종합 프롬프트에 주입할 요약 블록으로 변환한다."""
    if not scorecard or not scorecard.get("evaluations"):
        return ""
    lines = []
    for ev in scorecard["evaluations"]:
        detail = ", ".join(
            f"{s['criterion']} {s['score']}" for s in ev.get("scores", [])
        )
        lines.append(f"- {ev.get('researcher', '?')}: 총점 {ev.get('total', '?')} ({detail})")
    return (
        packs.get("scorecard_block.header")
        + "\n".join(lines)
        + packs.render("scorecard_block.best",
                       best=scorecard.get("best", ""),
                       rationale=scorecard.get("rationale", ""))
    )


def _synthesis_prompt(
    brief: ResearchBrief,
    findings: list,
    discussion: list,
    mode: str,
    target_pages: int = 12,
    scorecard: dict = None,
) -> str:
    latest = _latest_positions(findings, discussion)
    position_block = "\n\n".join(
        f"### {name} (최종 입장)\n{text}" for name, text in latest
    )
    has_card = bool(scorecard and scorecard.get("evaluations"))
    if mode == "best":
        mode_instruction = packs.get(
            "synthesis.mode.best_with_card" if has_card else "synthesis.mode.best")
    else:
        mode_instruction = packs.get("synthesis.mode.merge")
        if has_card:
            mode_instruction += packs.get("synthesis.mode.merge_with_card")
    plan = _length_plan(target_pages)
    return packs.render(
        "synthesis",
        brief_block=_brief_block(brief),
        position_block=position_block,
        scorecard_block=_scorecard_block(scorecard) if has_card else "",
        mode_instruction=mode_instruction,
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


def _latest_positions(findings: list, discussion: list) -> list:
    """토론 마지막 라운드의 입장(없으면 최초 조사 결과)을 익명 이름과 함께 반환."""
    name_map = {}
    for i, f in enumerate(findings):
        name_map[f.provider_key] = _anon_names()[i]

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
    _anon = _anon_names()
    name_map = {f.provider_key: _anon[i] for i, f in enumerate(findings)}
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


def run_scoring(
    moderator: BaseProvider,
    brief: ResearchBrief,
    findings: list,
    discussion: list,
    criteria: list,
) -> dict:
    """진행자 LLM이 연구원별 채점표를 작성한다.

    점수는 코드에서 1~10으로 클램프하고 총점(total)도 코드로 재계산한다
    (LLM의 산술을 신뢰하지 않음). 비교 대상이 2명 미만이면 빈 dict.
    """
    latest = _latest_positions(findings, discussion)
    if len(latest) < 2 or not criteria:
        return {}
    names = [name for name, _ in latest]
    schema = _scorecard_schema([c["label"] for c in criteria], names)
    card = moderator.generate_json(
        _scoring_prompt(brief, latest, criteria),
        system=brief.persona,
        schema=schema,
    )
    if not isinstance(card, dict):
        raise ValueError(f"채점표 응답이 JSON 객체가 아닙니다: {type(card).__name__}")
    for ev in card.get("evaluations", []):
        total = 0
        for s in ev.get("scores", []):
            try:
                s["score"] = max(1, min(10, int(s.get("score", 1))))
            except (TypeError, ValueError):
                s["score"] = 1
            total += s["score"]
        ev["total"] = total
    return card


def run_synthesis(
    moderator: BaseProvider,
    brief: ResearchBrief,
    findings: list,
    discussion: list,
    mode: str = "synthesize",
    target_pages: int = 12,
    scorecard: dict = None,
) -> dict:
    """진행자 LLM이 최종 보고서 JSON을 생성한다."""
    prompt = _synthesis_prompt(
        brief, findings, discussion, mode, target_pages, scorecard
    )
    return moderator.generate_json(prompt, system=brief.persona, schema=__getattr__("REPORT_SCHEMA"))


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
    criteria: list = None,
    on_update=None,
) -> PipelineResult:
    """전체 파이프라인을 실행한다. (Streamlit 밖에서도 사용 가능)

    criteria: 채점 기준 목록(SCORING_CRITERIA 항목들). None이면 기본 6개,
    빈 리스트면 채점 단계를 건너뛴다.
    """
    if criteria is None:
        criteria = [c for c in __getattr__("SCORING_CRITERIA")
                    if c["key"] in __getattr__("DEFAULT_CRITERIA_KEYS")]
    result = PipelineResult()
    result.findings = run_research(providers, brief, on_update)
    result.anon_map = {
        _anon_names()[i]: f.provider_label for i, f in enumerate(result.findings)
    }
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
    if len(ok) >= 2 and criteria:
        if on_update:
            on_update(f"채점표 작성 — 진행자: {moderator.label}")
        # 채점 실패가 보고서 생성까지 막으면 안 된다 — 채점표 없이 계속 진행
        try:
            result.scorecard = run_scoring(
                moderator, brief, result.findings, result.discussion, criteria
            )
        except Exception as e:
            if on_update:
                on_update(f"⚠️ 채점표 작성 실패(채점표 없이 계속 진행): {e}")
    if on_update:
        on_update(f"종합 단계 — 진행자: {moderator.label}")
    result.report = run_synthesis(
        moderator, brief, result.findings, result.discussion, mode,
        target_pages, result.scorecard,
    )
    return result
