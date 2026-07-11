"""엔티티 온톨로지 — 추출·노트 업서트·인덱스 (로드맵 3단계).

→ docs/13 지식 볼트와 온톨로지. Streamlit 비의존.

- 노트 하나 = 엔티티 하나, 마크다운이 원본 (사용자가 Obsidian에서 고치면 그대로 반영)
- 타입 6종·술어 12종은 닫힌 enum — 명명 파편화 방지
- 추출은 진행자 LLM의 generate_json 1회. confidence는 코드에서 클램프
  (LLM 산술 불신 원칙), 실패는 호출부가 채점처럼 삼키고 계속한다
"""
import json
import re

from .vault_render import _safe_filename

ENTITY_TYPES = ["기업", "규제·기준", "산업", "이슈", "지표", "기관"]

PREDICATES = [
    "적용된다", "요구한다", "공시한다", "영향준다", "다룬다", "측정한다",
    "속한다", "제정한다", "상호운용된다", "대체한다", "공급한다", "관련된다",
]

MAX_ENTITIES = 8
MAX_FACTS = 5
MAX_RELATIONS = 8
MAX_ALIASES = 8

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entity_type": {"type": "string", "enum": ENTITY_TYPES},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                    "facts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "confidence": {"type": "integer"},
                            },
                            "required": ["text", "confidence"],
                            "additionalProperties": False,
                        },
                    },
                    "relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "predicate": {"type": "string", "enum": PREDICATES},
                                "target": {"type": "string"},
                            },
                            "required": ["predicate", "target"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "name", "entity_type", "aliases", "summary",
                    "facts", "relations",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities"],
    "additionalProperties": False,
}


def _norm(s: str) -> str:
    """이름 매칭용 정규화 — 대소문자·공백 차이를 무시한다."""
    return re.sub(r"\s+", "", str(s)).casefold()


# ---------------------------------------------------------------- 추출


def _report_digest(report: dict, max_chars: int = 12_000) -> str:
    slim = {
        k: report.get(k)
        for k in ("title", "executive_summary", "key_findings",
                  "sections", "data_tables", "recommendations")
        if report.get(k)
    }
    text = json.dumps(slim, ensure_ascii=False)
    return text[:max_chars]


def _extract_prompt(report: dict, known_names: list, has_attachments: bool) -> str:
    known_block = "\n".join(f"- {n}" for n in known_names[:120])
    confidential_note = (
        "\n- 이번 조사에는 첨부 파일(내부 자료 가능성)이 포함되었다. **첨부 자료에만 "
        "존재하는 기업 내부 정보(미공개 수치·계약·전략)는 사실로 추출하지 마라.** "
        "공개 출처로 확인 가능한 내용만 담아라."
        if has_attachments else ""
    )
    return f"""## 최종 보고서 (JSON)
{_report_digest(report)}

## 기존 지식볼트의 엔티티 (정식 명칭)
{known_block}

## 작업
위 보고서에서 ESG 지식볼트에 축적할 **핵심 엔티티 3~{MAX_ENTITIES}개**를 추출하라.

규칙:
- entity_type은 {ENTITY_TYPES} 중 하나만.
- 같은 대상이 기존 엔티티 목록에 있으면 **반드시 그 정식 명칭을 name으로 재사용**하라
  (예: 목록에 "EU CBAM"이 있으면 "CBAM"·"탄소국경조정제도"가 아니라 "EU CBAM").
- facts: 이 보고서가 근거인 **구체적 사실**만 (수치·연도·기관명 포함 문장,
  엔티티당 최대 {MAX_FACTS}개). 일반 상식이나 정의는 넣지 마라 — 요약(summary)에만.
- confidence: 사실의 확실성 1~10 정수 (보고서 내 출처가 분명하면 높게).
- relations: 술어는 {PREDICATES} 만. 주어는 해당 엔티티 자신이다
  (예: ESRS E1 → "속한다": "ESRS"). target은 가능하면 기존 엔티티 명칭.
- summary: 2~3문장, 한국어.{confidential_note}"""


def extract_entities(provider, report: dict, known_names: list,
                     has_attachments: bool = False) -> list:
    """진행자 LLM으로 엔티티를 추출하고 코드 레벨에서 정제·클램프한다."""
    raw = provider.generate_json(
        _extract_prompt(report, known_names, has_attachments),
        schema=EXTRACT_SCHEMA,
    )
    if not isinstance(raw, dict):
        raise ValueError(f"엔티티 추출 응답이 JSON 객체가 아닙니다: {type(raw).__name__}")
    cleaned = []
    for e in (raw.get("entities") or [])[:MAX_ENTITIES]:
        name = str(e.get("name", "")).strip()
        etype = e.get("entity_type", "")
        if not name or etype not in ENTITY_TYPES:
            continue
        facts = []
        for f in (e.get("facts") or [])[:MAX_FACTS]:
            text = str(f.get("text", "")).strip()
            if not text:
                continue
            try:
                conf = max(1, min(10, int(f.get("confidence", 5))))
            except (TypeError, ValueError):
                conf = 5
            facts.append({"text": text, "confidence": conf})
        relations = []
        seen_rel = set()
        for r in (e.get("relations") or [])[:MAX_RELATIONS]:
            pred, target = r.get("predicate", ""), str(r.get("target", "")).strip()
            key = (pred, _norm(target))
            if pred in PREDICATES and target and _norm(target) != _norm(name) \
                    and key not in seen_rel:
                seen_rel.add(key)
                relations.append({"predicate": pred, "target": target})
        aliases = []
        for a in (e.get("aliases") or [])[:MAX_ALIASES]:
            a = str(a).strip()
            if a and _norm(a) != _norm(name):
                aliases.append(a)
        cleaned.append({
            "name": name, "entity_type": etype, "aliases": aliases,
            "summary": str(e.get("summary", "")).strip(),
            "facts": facts, "relations": relations,
        })
    return cleaned


# ------------------------------------------------------------ 노트 파싱/병합

_NOTE_TEMPLATE = """---
type: entity
entity_type: {etype}
aliases: [{aliases}]
updated: {as_of}
---

## 요약

{summary}

## 축적된 사실

{facts}

## 관계

{relations}

## 검토 필요

<!-- 신규 조사가 기존 지식과 다르다고 보고한 항목이 여기에 쌓입니다 -->
"""


def parse_note(md: str) -> dict:
    """엔티티 노트에서 frontmatter 필드와 관계 목록을 뽑는다 (병합·인덱스용)."""
    out = {"entity_type": "", "aliases": [], "relations": []}
    m = re.match(r"^---\n(.*?)\n---", md, re.S)
    if m:
        fm = m.group(1)
        tm = re.search(r"^entity_type:\s*(.+)$", fm, re.M)
        if tm:
            out["entity_type"] = tm.group(1).strip()
        am = re.search(r"^aliases:\s*\[(.*)\]\s*$", fm, re.M)
        if am:
            out["aliases"] = [a.strip() for a in am.group(1).split(",") if a.strip()]
    for pm in re.finditer(r"^-\s*(\S+):\s*\[\[(.+?)\]\]", md, re.M):
        if pm.group(1) in PREDICATES:
            out["relations"].append(
                {"predicate": pm.group(1), "target": pm.group(2).strip()}
            )
    return out


def _fact_line(fact: dict, run_stem: str, as_of: str, confidential: bool) -> str:
    lock = " 🔒기밀후보" if confidential else ""
    return (f"- (as_of {as_of} · [[{run_stem}]] · 신뢰도 "
            f"{fact['confidence']}/10{lock}) {fact['text']}")


def _append_to_section(md: str, header: str, new_lines: list) -> str:
    """'## header' 섹션 끝(다음 ## 직전)에 줄들을 추가한다. 섹션이 없으면 말미에 생성."""
    if not new_lines:
        return md
    lines = md.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == f"## {header}")
    except StopIteration:
        return md.rstrip() + f"\n\n## {header}\n\n" + "\n".join(new_lines) + "\n"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    # 섹션 끝의 빈 줄 앞에 삽입
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    return "\n".join(lines[:insert] + new_lines + lines[insert:])


def merge_entity(existing_md: str, entity: dict, run_stem: str,
                 as_of: str, confidential: bool) -> str:
    """기존 노트에 새 추출 결과를 병합한다.

    사용자 큐레이션 존중 원칙: 요약은 덮어쓰지 않고, 사실·관계·별칭만 추가한다.
    """
    md = existing_md
    parsed = parse_note(md)

    # aliases 병합 (frontmatter 한 줄 교체)
    have = {_norm(a) for a in parsed["aliases"]}
    merged = parsed["aliases"] + [
        a for a in entity["aliases"] if _norm(a) not in have
    ]
    if merged != parsed["aliases"]:
        md = re.sub(
            r"^aliases:\s*\[.*\]\s*$",
            "aliases: [" + ", ".join(merged) + "]",
            md, count=1, flags=re.M,
        )
    md = re.sub(r"^updated:\s*.+$", f"updated: {as_of}", md, count=1, flags=re.M)

    fact_lines = [_fact_line(f, run_stem, as_of, confidential)
                  for f in entity["facts"]]
    md = _append_to_section(md, "축적된 사실", fact_lines)

    have_rel = {(r["predicate"], _norm(r["target"])) for r in parsed["relations"]}
    rel_lines = [
        f"- {r['predicate']}: [[{r['target']}]]"
        for r in entity["relations"]
        if (r["predicate"], _norm(r["target"])) not in have_rel
    ]
    md = _append_to_section(md, "관계", rel_lines)
    return md


def render_new_note(entity: dict, run_stem: str, as_of: str,
                    confidential: bool) -> str:
    facts = "\n".join(
        _fact_line(f, run_stem, as_of, confidential) for f in entity["facts"]
    ) or "<!-- 조사가 반영되면 여기에 as_of 날짜·출처 run 링크와 함께 추가됩니다 -->"
    relations = "\n".join(
        f"- {r['predicate']}: [[{r['target']}]]" for r in entity["relations"]
    ) or "<!-- 관계가 확인되면 추가됩니다 -->"
    return _NOTE_TEMPLATE.format(
        etype=entity["entity_type"],
        aliases=", ".join(entity["aliases"]),
        as_of=as_of,
        summary=entity["summary"],
        facts=facts,
        relations=relations,
    )


def entity_path(entity: dict) -> str:
    return f"entities/{entity['entity_type']}/{_safe_filename(entity['name'])}.md"


# ---------------------------------------------------------------- 인덱스


def build_index(vault_files: dict, as_of: str) -> str:
    """entities/**.md 전체에서 검색용 인덱스(JSON)를 재생성한다."""
    items = []
    for path, content in sorted(vault_files.items()):
        if not (path.startswith("entities/") and path.endswith(".md")):
            continue
        parsed = parse_note(str(content))
        items.append({
            "name": path.rsplit("/", 1)[-1][:-3],
            "entity_type": parsed["entity_type"],
            "aliases": parsed["aliases"],
            "path": path,
            "relations": parsed["relations"],
        })
    return json.dumps(
        {"generated_at": as_of, "count": len(items), "entities": items},
        ensure_ascii=False, indent=1,
    )


def known_names_from_index(index_json: str) -> list:
    """추출 프롬프트에 넣을 '정식 명칭 (별칭…)' 목록."""
    try:
        entities = json.loads(index_json).get("entities", [])
    except (ValueError, AttributeError):
        return []
    names = []
    for e in entities:
        label = e.get("name", "")
        if e.get("aliases"):
            label += " (별칭: " + ", ".join(e["aliases"][:4]) + ")"
        names.append(label)
    return names


def match_existing(entity: dict, vault_files: dict) -> str:
    """추출 엔티티와 이름/별칭이 일치하는 기존 노트 경로를 찾는다 (없으면 '')."""
    wanted = {_norm(entity["name"])} | {_norm(a) for a in entity["aliases"]}
    for path, content in vault_files.items():
        if not (path.startswith("entities/") and path.endswith(".md")):
            continue
        name = path.rsplit("/", 1)[-1][:-3]
        have = {_norm(name)} | {_norm(a) for a in parse_note(str(content))["aliases"]}
        if wanted & have:
            return path
    return ""


def apply_extraction(vault_files: dict, entities: list, run_stem: str,
                     as_of: str, confidential: bool) -> tuple:
    """추출 결과를 볼트에 업서트한다.

    반환: (변경/신규 파일 dict {path: content}, 신규 수, 갱신 수)
    vault_files 자체도 갱신한다 (인덱스 재생성이 최신 상태를 보게).
    """
    changed = {}
    created = updated = 0
    for entity in entities:
        path = match_existing(entity, vault_files)
        if path:
            new_md = merge_entity(
                str(vault_files[path]), entity, run_stem, as_of, confidential
            )
            if new_md != vault_files[path]:
                updated += 1
                vault_files[path] = new_md
                changed[path] = new_md
        else:
            path = entity_path(entity)
            new_md = render_new_note(entity, run_stem, as_of, confidential)
            created += 1
            vault_files[path] = new_md
            changed[path] = new_md
    index = build_index(vault_files, as_of)
    vault_files["_index/entities.json"] = index
    changed["_index/entities.json"] = index
    return changed, created, updated
