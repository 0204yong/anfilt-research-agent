"""조사 레코드 ↔ 화면 상태 변환 — 저장소와 무관한 순수 변환.

→ docs/22 설치판 아키텍처 5.1절. `SupabaseStore`(체험판)와 `LocalStore`(정식판)가
공유하므로 어느 한쪽 저장소 모듈에 두면 안 된다.
"""


def record_to_state(record: dict) -> tuple:
    """아카이브 레코드를 (brief, params, result)로 복원한다.

    필드를 명시적으로 골라 담는다 — 과거/미래 schema_version의 여분 키가
    dataclass 생성자를 깨지 않게 (schema_version 필드의 존재 이유).
    """
    from .pipeline import (
        AgentFinding, DiscussionTurn, PipelineResult, ResearchBrief,
    )

    b = record.get("brief") or {}
    brief = ResearchBrief(
        topic=b.get("topic", ""),
        keywords=b.get("keywords") or [],
        reference_urls=b.get("reference_urls") or [],
        reference_texts=b.get("reference_texts") or {},
        instructions=b.get("instructions", ""),
        persona=b.get("persona", ""),
    )
    r = record.get("result") or {}
    result = PipelineResult(
        findings=[
            AgentFinding(
                provider_key=f.get("provider_key", ""),
                provider_label=f.get("provider_label", ""),
                model=f.get("model", ""),
                text=f.get("text", ""),
                error=f.get("error", ""),
            )
            for f in r.get("findings") or []
        ],
        discussion=[
            DiscussionTurn(
                round_no=t.get("round_no", 0),
                provider_key=t.get("provider_key", ""),
                provider_label=t.get("provider_label", ""),
                text=t.get("text", ""),
                error=t.get("error", ""),
            )
            for t in r.get("discussion") or []
        ],
        scorecard=r.get("scorecard") or {},
        report=r.get("report") or {},
        moderator_label=r.get("moderator_label", ""),
        anon_map=r.get("anon_map") or {},
    )
    return brief, record.get("params") or {}, result
