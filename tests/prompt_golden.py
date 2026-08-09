"""프롬프트 골든 스냅샷 — 팩 분리가 문구를 바꾸지 않았는지 **바이트 단위**로 검증한다.

    python tests/prompt_golden.py --write     # 분리 **전에** 기준을 뜬다
    python tests/prompt_golden.py             # 분리 **후에** 비교한다

→ docs/23 설치판 구현 계획 2단계 DoD.

2단계는 "문자열의 출처만 바꾸고 조립 로직은 그대로 둔다"는 기계적 치환이다.
출력이 한 글자라도 달라지면 그건 리팩터링이 아니라 버그다. LLM을 부르지 않고
프롬프트 문자열만 만들어 비교하므로 빠르고 결정적이다.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 콘솔이 cp949 여도 한글·—(em dash)가 든 결과를 찍을 수 있게 (Windows 기본 코드페이지)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


GOLDEN = Path(__file__).parent / "prompt_golden.json"

from core import librarian, light, ontology, pipeline  # noqa: E402
from core import watch as W                            # noqa: E402
from core.pipeline import (                            # noqa: E402
    AgentFinding, DiscussionTurn, ResearchBrief,
)

# ---------------------------------------------------------------- 고정 입력

BRIEF = ResearchBrief(
    topic="EU CBAM 전환기간 종료 후 국내 철강 수출기업의 대응 과제",
    keywords=["CBAM 인증서", "탄소국경세"],
    reference_urls=["https://ec.europa.eu/cbam"],
    reference_texts={
        "https://ec.europa.eu/cbam": "CBAM 본문 원문 발췌 ±① ※ 특수문자 포함",
        "[축적 지식] EU CBAM": "과거 조사에서 축적된 내용 (자동 생성·미검증)",
    },
    instructions="국내 철강업 관점에서 분석",
    persona="수석 연구원",
)

BRIEF_MIN = ResearchBrief(
    topic="탄소중립 개요", keywords=[], reference_urls=[], reference_texts={},
    instructions="", persona="",
)

FINDINGS = [
    AgentFinding(provider_key="gemini", provider_label="Gemini (Google)",
                 model="gemini-3.5-flash", text="제미나이 조사 본문", error=""),
    AgentFinding(provider_key="openai", provider_label="GPT (OpenAI)",
                 model="gpt-5", text="지피티 조사 본문", error=""),
]
DISCUSSION = [
    DiscussionTurn(round_no=1, provider_key="gemini", provider_label="Gemini (Google)",
                   text="1라운드 수정된 최종 입장", error=""),
]
SCORECARD = {
    "evaluations": [
        {"researcher": "연구원 A", "scores": [{"criterion": "정확성", "score": 8,
                                              "comment": "근거 충실"}],
         "strengths": "출처가 명확", "weaknesses": "수치가 얕음", "total": 8},
    ],
    "best": "연구원 A", "rationale": "근거가 가장 탄탄하다",
}
WATCH = {
    "watch_id": "watch-1", "name": "EFRAG 공지", "kind": "keyword",
    "target": "CSRD 개정", "hours": "08", "enabled": True, "notify": "email",
    "instructions": "규제 변화 위주로", "last_snapshot": "",
}
HITS = [
    W.WatchHit(fingerprint="fp1", title="[기사] CSRD 개정안 공개",
          url="https://example.org/news/1", excerpt="본문 발췌 1"),
    W.WatchHit(fingerprint="fp2", title="EFRAG 초안 발표",
          url="https://example.org/news/2", excerpt="본문 발췌 2"),
]
REPORT = {
    "title": "보고서 제목", "subtitle": "부제",
    "executive_summary": "요약 본문",
    "sections": [{"heading": "1. 배경", "content": "본문", "bullets": ["가", "나"]}],
    "tables": [{"title": "표", "headers": ["연도"], "rows": [["2026"]]}],
    "key_findings": ["발견 1"], "recommendations": ["제언 1"],
    "sources": [{"title": "EC", "url": "https://ec.europa.eu/"}],
}


def collect() -> dict:
    """이름 → 문자열. 프롬프트·스키마·구성표를 전부 담는다."""
    out = {}

    # --- pipeline: 프롬프트
    out["pipeline.brief_block"] = pipeline._brief_block(BRIEF)
    out["pipeline.brief_block.no_accumulated"] = pipeline._brief_block(
        BRIEF, include_accumulated=False)
    out["pipeline.brief_block.minimal"] = pipeline._brief_block(BRIEF_MIN)
    out["pipeline.research"] = pipeline._research_prompt(BRIEF)
    out["pipeline.research.no_keywords"] = pipeline._research_prompt(BRIEF_MIN)
    out["pipeline.discussion"] = pipeline._discussion_prompt(
        BRIEF, "내 조사 결과", [("연구원 B", "동료 결과")], 1)
    out["pipeline.scoring"] = pipeline._scoring_prompt(
        BRIEF, [("연구원 A", "최종 입장")], pipeline.SCORING_CRITERIA[:3])
    for mode in ("best", "merge", "compare"):
        for pages in (1, 6, 12, 20):
            out[f"pipeline.synthesis.{mode}.{pages}"] = pipeline._synthesis_prompt(
                BRIEF, FINDINGS, DISCUSSION, mode, target_pages=pages,
                scorecard=SCORECARD if mode == "best" else None)
    out["pipeline.scorecard_block"] = pipeline._scorecard_block(SCORECARD)

    # --- pipeline: 스키마·구성표
    out["pipeline.REPORT_SCHEMA"] = json.dumps(
        pipeline.REPORT_SCHEMA, ensure_ascii=False, sort_keys=True, indent=1)
    out["pipeline.SCORING_CRITERIA"] = json.dumps(
        pipeline.SCORING_CRITERIA, ensure_ascii=False, sort_keys=True, indent=1)
    out["pipeline.DEFAULT_CRITERIA_KEYS"] = json.dumps(
        pipeline.DEFAULT_CRITERIA_KEYS, ensure_ascii=False)
    out["pipeline.scorecard_schema"] = json.dumps(
        pipeline._scorecard_schema(["정확성", "근거"], ["연구원 A", "연구원 B"]),
        ensure_ascii=False, sort_keys=True, indent=1)
    for pages in range(1, 21):
        out[f"pipeline.length_plan.{pages}"] = json.dumps(
            pipeline._length_plan(pages), ensure_ascii=False, sort_keys=True)

    # --- ontology
    out["ontology.extract"] = ontology._extract_prompt(
        REPORT, ["EU CBAM", "ESRS E1"], has_attachments=False)
    out["ontology.extract.attachments"] = ontology._extract_prompt(
        REPORT, ["EU CBAM"], has_attachments=True, has_injected=True)
    out["ontology.EXTRACT_SCHEMA"] = json.dumps(
        ontology.EXTRACT_SCHEMA, ensure_ascii=False, sort_keys=True, indent=1)
    for name in ("ENTITY_TYPES", "PREDICATES", "SYSTEM", "KNOWLEDGE_INSTRUCTION"):
        val = getattr(ontology, name, None)
        if val is not None:
            out[f"ontology.{name}"] = (
                val if isinstance(val, str)
                else json.dumps(val, ensure_ascii=False, sort_keys=True))

    # --- librarian
    out["librarian.SYSTEM"] = librarian.SYSTEM
    out["librarian.ANSWER_SCHEMA"] = json.dumps(
        librarian.ANSWER_SCHEMA, ensure_ascii=False, sort_keys=True, indent=1)
    out["librarian.answer"] = librarian._answer_prompt("CBAM이 뭐야?", "노트 발췌")
    out["librarian.answer.history"] = librarian._answer_prompt(
        "그럼 ETS는?", "노트 발췌",
        [{"role": "user", "content": "이전 질문"},
         {"role": "assistant", "content": "이전 답변"}])

    # --- watch
    out["watch.search"] = W._search_prompt(WATCH, 7)
    out["watch.search_structure"] = W._search_structure_prompt(WATCH, 7, "검색 결과 본문")
    out["watch.digest"] = W._digest_prompt(WATCH, HITS)
    out["watch.SEARCH_SCHEMA"] = json.dumps(
        W.SEARCH_SCHEMA, ensure_ascii=False, sort_keys=True, indent=1)
    out["watch.DIGEST_SCHEMA"] = json.dumps(
        W.DIGEST_SCHEMA, ensure_ascii=False, sort_keys=True, indent=1)

    # --- light
    out["light.structure"] = light._structure_prompt(BRIEF, 6)
    out["light.structure.with_research"] = light._structure_prompt(
        BRIEF, 12, "사전 조사 메모 본문")
    out["light.LIGHT_TEMPLATES"] = json.dumps(
        light.LIGHT_TEMPLATES, ensure_ascii=False, sort_keys=True, indent=1)

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="현재 상태를 기준으로 저장")
    args = ap.parse_args()

    current = collect()
    if args.write:
        GOLDEN.write_text(
            json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8")
        digest = hashlib.sha256(
            json.dumps(current, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        print(f"기준 저장: {GOLDEN.name} — {len(current)}개 항목, sha256 {digest}")
        return 0

    if not GOLDEN.exists():
        print(f"기준 파일이 없습니다: {GOLDEN}\n먼저 --write 로 기준을 뜨세요.",
              file=sys.stderr)
        return 2
    base = json.loads(GOLDEN.read_text(encoding="utf-8"))

    added = sorted(set(current) - set(base))
    removed = sorted(set(base) - set(current))
    changed = sorted(k for k in set(base) & set(current) if base[k] != current[k])

    for k in changed:
        print(f"✗ 달라짐: {k}")
        b, c = base[k], current[k]
        for i, (x, y) in enumerate(zip(b, c)):
            if x != y:
                lo, hi = max(0, i - 60), i + 60
                print(f"    첫 차이 위치 {i}")
                print(f"    기준: ...{b[lo:hi]!r}...")
                print(f"    현재: ...{c[lo:hi]!r}...")
                break
        else:
            print(f"    길이만 다름: 기준 {len(b)} → 현재 {len(c)}")
            print(f"    꼬리 차이: {(b[len(c):] or c[len(b):])[:120]!r}")
    for k in removed:
        print(f"✗ 사라짐: {k}")
    for k in added:
        print(f"· 추가됨(무해): {k}")

    ok = not changed and not removed
    print()
    print(f"항목 {len(current)}개 · 달라짐 {len(changed)} · 사라짐 {len(removed)} · 추가 {len(added)}")
    print("결과:", "바이트 단위 동일" if ok else "차이 발견 — 분리 과정에서 문구가 바뀌었다")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
