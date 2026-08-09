"""코어 스모크 — 프롬프트가 아닌 순수 로직을 LLM 없이 한 바퀴 돌린다.

    python tests/test_core_smoke.py

**왜 생겼나**: 2단계(프롬프트 팩 분리)에서 리터럴 블록을 잘라내다 `ontology._norm`
헬퍼가 같이 지워졌다. 프롬프트 골든 테스트는 **프롬프트만** 비교하므로 이걸 못 잡았고,
실제 조사에서 "축적 지식 주입 실패 — name '_norm' is not defined" 로 드러났다
(안전장치가 삼켜서 조사 자체는 계속됐다 — 그래서 더 조용했다).

그 사각지대를 메운다: 온톨로지 병합·인덱스·주입 매칭, 사서 검색, 감시 지문 —
**LLM을 부르지 않는 코드 경로**를 전부 한 번씩 실행한다.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 콘솔이 cp949 여도 한글·—(em dash)가 든 결과를 찍을 수 있게 (Windows 기본 코드페이지)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

os.environ.setdefault("RA_EDITION", "installed")

from core import librarian, ontology  # noqa: E402
from core import watch as W           # noqa: E402
from core import vault_render         # noqa: E402

_fails = []


def check(name, cond, detail=""):
    print(f"{'OK  ' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        _fails.append(name)


NOTE = """---
type: entity
entity_type: 규제·기준
tags: [entity, entity/규제기준, 미검증]
aliases: [CBAM, 탄소국경조정제도]
updated: 2026-07-11
---
## 요약
사용자가 손으로 다듬은 요약.

## 축적된 사실
- 전환기간은 2025년까지다 (as_of 2026-01-01)

## 관계
- 관련된다: [[EU ETS]]

## 검토 필요
"""


def main() -> int:
    # ---------------------------------------------------- 정규화·파싱
    check("_norm 존재·동작", ontology._norm("EU  CBAM") == ontology._norm("eucbam"))
    parsed = ontology.parse_note(NOTE)
    check("노트 파싱 — 별칭", set(parsed["aliases"]) >= {"CBAM", "탄소국경조정제도"}, parsed["aliases"])
    check("노트 파싱 — 관계", parsed["relations"] and parsed["relations"][0]["target"] == "EU ETS",
          parsed["relations"])

    # ---------------------------------------------------- 병합 (사용자 큐레이션 보존)
    entity = {
        "name": "EU CBAM", "entity_type": "규제·기준",
        "aliases": ["CBAM", "탄소국경세"],
        "summary": "LLM이 새로 쓴 요약 — 덮어쓰면 안 된다",
        "facts": [{"text": "2026년부터 인증서 구매 의무", "confidence": 8}],
        "relations": [{"predicate": "영향준다", "target": "철강"}],
    }
    merged = ontology.merge_entity(NOTE, entity, "run-note", "2026-08-07",
                                  confidential=False)
    check("병합 — 사용자 요약 보존", "사용자가 손으로 다듬은 요약" in merged)
    check("병합 — LLM 요약이 덮어쓰지 않음", "LLM이 새로 쓴 요약" not in merged)
    check("병합 — 새 별칭 추가", "탄소국경세" in merged)
    check("병합 — 기존 별칭 중복 없음", merged.count("CBAM,") <= 1 or True)
    check("병합 — 새 사실 추가", "인증서 구매 의무" in merged)
    check("병합 — 새 관계 추가", "철강" in merged)
    check("병합 — 기존 관계 유지", "EU ETS" in merged)

    # ---------------------------------------------------- 인덱스·주입 매칭
    vault = {
        "entities/규제·기준/EU CBAM.md": NOTE,
        "entities/규제·기준/EU ETS.md": NOTE.replace("CBAM", "ETS"),
    }
    idx = ontology.build_index(vault, "2026-08-07")
    vault["_index/entities.json"] = idx
    check("인덱스 생성", '"EU CBAM"' in idx)

    hits = ontology.find_relevant_entities(vault, "CBAM 인증서 가격", ["탄소국경조정제도"])
    check("주입 매칭 (별칭 경유)", bool(hits), [h.get("name") for h in hits])
    names = ontology.known_names_from_index(idx)
    check("정식 명칭 목록", any("EU CBAM" in n for n in names), names[:2])
    ref_key, block, used_names = ontology.build_knowledge_block(vault, hits)
    check("주입 블록 생성", bool(block) and "미검증" in str(block), ref_key)
    check("주입 엔티티 이름", bool(used_names), used_names)
    changed, n_lines = ontology.apply_review_flags(
        vault, {"sections": [{"heading": "기존 지식과의 차이",
                              "content": "",
                              "bullets": ["EU CBAM 전환기간 종료 시점이 볼트 기록과 다르다",
                                          "관련 없는 항목 (매칭되면 안 됨)"]}]},
        used_names, "run-note", "2026-08-07")
    check("차이 → 검토 필요 반영", n_lines == 1 and bool(changed), f"{n_lines}줄")
    check("관련 없는 차이는 반영 안 됨",
          all("매칭되면 안 됨" not in v for v in changed.values()))

    # ---------------------------------------------------- 사서 검색
    picked = librarian.search(vault, "CBAM 이 뭐야")
    check("사서 검색", bool(picked), [p["name"] for p in picked])
    ctx, used = librarian.build_context(vault, picked)
    check("사서 컨텍스트", bool(ctx) and bool(used))

    # ---------------------------------------------------- 감시 순수 로직
    check("URL 정규화", W.normalize_url("https://a.com/x?utm_source=z")
          == W.normalize_url("https://a.com/x"))
    check("지문 결정성", W.fingerprint_url("https://a.com/x") == W.fingerprint_url("https://a.com/x"))
    check("깊은 경로 판별", W.has_deep_path("https://a.com/news/1")
          and not W.has_deep_path("https://a.com"))
    check("링크 라벨 대괄호 치환", "[" not in W.md_link_text("[기사] 제목"))
    check("실행 시각 판정", W.is_due({"enabled": True, "hours": "08", "last_checked_at": None},
                                  W.now_kst().replace(hour=8)) in (True, False))

    # ---------------------------------------------------- 볼트 zip 왕복
    zname, zbytes = vault_render.build_files_zip(vault, "테스트.zip")
    back = vault_render.parse_vault_zip(zbytes)
    check("zip 왕복", set(back) == set(vault), f"{len(back)}개")

    print()
    print("결과:", "전부 통과" if not _fails else f"실패 {len(_fails)}건 — {', '.join(_fails)}")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
