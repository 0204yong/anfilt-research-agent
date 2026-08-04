"""지식 비서 — 지식볼트에 있는 내용**만**으로 답한다.

→ docs/16 지식 비서. Streamlit 비의존.

개념: 사용자의 개인 도서관(Obsidian 볼트)을 통째로 읽고 있는 사서. 사용자가
직접 노트를 뒤지는 대신 사서에게 물으면 된다. 대신 사서는 **도서관에 없는 것을
지어내지 않는다** — 근거가 없으면 "볼트에 없습니다"라고 답하고 무엇이 비었는지
알려준다(그 자체가 다음 조사 주제가 된다).

검색은 임베딩 없이 어휘 매칭으로 한다:
- 볼트는 노트 수백 개 규모라 전수 스캔이 충분히 빠르다(REST 왕복 1회).
- 임베딩을 쓰면 인덱스 재생성·비용·의존성이 늘고, 사용자가 Obsidian에서 노트를
  고쳤을 때 인덱스가 조용히 낡는다. 마크다운이 원본이라는 원칙과 충돌한다.
- 엔티티 인덱스(_index/entities.json)의 별칭·관계가 동의어/1홉 확장을 대신한다.
"""
import json
import re

MAX_NOTES = 8              # 답변 근거로 넣을 노트 수
MAX_NOTE_CHARS = 6_000     # 노트당 발췌 상한
MAX_CONTEXT_CHARS = 30_000  # 전체 컨텍스트 상한 (경량 모델 기준 안전선)
CONFIDENTIAL_MARK = "🔒기밀후보"

_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).casefold()


def query_terms(query: str) -> list:
    """검색어를 2글자 이상 토큰으로 자른다 (한글·영문·숫자)."""
    raw = re.split(r"[^0-9A-Za-z가-힣]+", str(query))
    out = []
    for w in raw:
        w = w.strip()
        if len(w) >= 2 and _norm(w) not in {_norm(x) for x in out}:
            out.append(w)
    return out


def note_name(path: str) -> str:
    """[[위키링크]]에 쓰이는 이름 = 확장자 뺀 파일명."""
    return str(path).rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _kind(path: str) -> str:
    if path.startswith("entities/"):
        return "엔티티"
    if path.startswith("watch/"):
        return "모니터링"
    if path.startswith("runs/"):
        return "조사 기록"
    return "노트"


def _recency_bonus(path: str) -> float:
    """runs/·watch/ 노트는 파일명 앞의 날짜가 최신일수록 조금 더 우대한다."""
    m = _DATE_RE.search(note_name(path))
    if not m:
        return 0.0
    try:
        from datetime import date
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        days = (date.today() - d).days
    except ValueError:
        return 0.0
    if days < 0:
        return 0.0
    return max(0.0, 3.0 - days / 60.0)   # 최근 60일 안이면 최대 +3


def _index_entities(vault_files: dict) -> list:
    try:
        return json.loads(vault_files.get("_index/entities.json", "{}")).get(
            "entities", []
        )
    except (ValueError, AttributeError):
        return []


def search(vault_files: dict, query: str, limit: int = MAX_NOTES) -> list:
    """질문과 관련된 노트를 점수순으로 고른다.

    반환: [{path, name, kind, score, why}] — why는 UI에 보여줄 매칭 사유.
    """
    terms = query_terms(query)
    if not terms:
        return []
    nq = _norm(query)
    norm_terms = [_norm(t) for t in terms]

    # 엔티티 인덱스: 질문에 이름·별칭이 등장하면 그 노트를 강하게 끌어올린다
    index = _index_entities(vault_files)
    boosted = {}          # path -> 사유
    by_key = {}
    for e in index:
        for term in [e.get("name", "")] + (e.get("aliases") or []):
            k = _norm(term)
            if len(k) >= 2:
                by_key.setdefault(k, e)
                if k in nq:
                    boosted[e["path"]] = f"엔티티 '{term}' 일치"

    scored = []
    for path, content in vault_files.items():
        if not str(path).endswith(".md"):
            continue
        text = str(content)
        n_name, n_text = _norm(note_name(path)), _norm(text)
        score, hits = 0.0, []
        for term, nt in zip(terms, norm_terms):
            in_name = nt in n_name
            count = n_text.count(nt)
            if in_name:
                score += 8
            if count:
                score += min(count, 5)
            if in_name or count:
                hits.append(term)
        if path in boosted:
            score += 12
        if not score:
            continue
        if path.startswith("entities/"):
            score += 2       # 사람이 큐레이션하는 정본
        score += _recency_bonus(path)
        why = boosted.get(path) or ("키워드 " + ", ".join(hits[:4]))
        scored.append({
            "path": path, "name": note_name(path), "kind": _kind(path),
            "score": round(score, 1), "why": why,
        })

    scored.sort(key=lambda r: (-r["score"], r["path"]))
    picked = scored[:limit]

    # 관계 1홉 확장 — 직접 매칭된 엔티티가 가리키는 엔티티도 근거 후보로 넣는다
    have = {r["path"] for r in picked}
    for r in list(picked):
        if not r["path"].startswith("entities/") or len(picked) >= limit + 3:
            continue
        entry = next((e for e in index if e.get("path") == r["path"]), None)
        for rel in (entry or {}).get("relations") or []:
            target = by_key.get(_norm(rel.get("target", "")))
            if target and target["path"] not in have and target["path"] in vault_files:
                have.add(target["path"])
                picked.append({
                    "path": target["path"], "name": target["name"],
                    "kind": "엔티티", "score": 0.0,
                    "why": f"{r['name']} 의 관계({rel.get('predicate')})",
                })
            if len(picked) >= limit + 3:
                break
    return picked


# ---------------------------------------------------------------- 답변

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "has_basis": {"type": "boolean"},
        "answer": {"type": "string"},
        "used_notes": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "suggested_research": {"type": "string"},
    },
    "required": ["has_basis", "answer", "used_notes", "gaps", "suggested_research"],
    "additionalProperties": False,
}

SYSTEM = (
    "당신은 사용자의 개인 지식볼트(Obsidian)를 전부 읽고 있는 전담 사서입니다. "
    "ESG·지속가능성 실무 맥락을 이해하며, 볼트에 적힌 내용만을 근거로 답합니다. "
    "볼트에 없는 것은 모른다고 말합니다 — 일반 상식으로 메우지 않습니다."
)


def build_context(vault_files: dict, picked: list,
                  include_confidential: bool = False) -> tuple:
    """(컨텍스트 문자열, 실제로 넣은 노트 목록)."""
    parts, used, total = [], [], 0
    for r in picked:
        md = str(vault_files.get(r["path"], ""))
        if not md.strip():
            continue
        if not include_confidential:
            md = "\n".join(l for l in md.split("\n") if CONFIDENTIAL_MARK not in l)
        body = md[:MAX_NOTE_CHARS]
        if total + len(body) > MAX_CONTEXT_CHARS:
            break
        total += len(body)
        parts.append(f"### 노트: [[{r['name']}]]\n경로: {r['path']}\n\n{body}")
        used.append(r)
    return "\n\n---\n\n".join(parts), used


def _answer_prompt(query: str, context: str, history: list = None) -> str:
    hist = ""
    if history:
        turns = "\n".join(
            f"- {'질문' if h.get('role') == 'user' else '답변'}: "
            f"{str(h.get('content', ''))[:400]}"
            for h in history[-4:]
        )
        hist = f"\n\n## 직전 대화 (맥락 참고용)\n{turns}"
    return f"""## 지식볼트 발췌 (이것이 유일한 근거다)
{context or "(관련 노트를 찾지 못했습니다)"}

## 사용자 질문
{query}{hist}

## 작업
위 **지식볼트 발췌만을 근거로** 한국어로 답하라.

절대 규칙:
1. 발췌에 없는 사실을 쓰지 마라. 모델이 알고 있는 일반 지식으로 빈칸을 메우면
   안 된다. 근거가 부족하면 has_basis=false로 두고 그 사실을 답에 밝혀라.
2. 모든 주장 뒤에 근거 노트를 `[[노트이름]]` 형태로 붙여라.
3. 사실에 `(as_of YYYY-MM-DD)`가 적혀 있으면 **날짜를 함께 밝혀라** — 볼트 내용은
   과거 시점의 자동 축적본이라 낡았을 수 있다.
4. 볼트 노트는 대부분 '자동 생성·미검증'이다. 확정 사실처럼 단정하지 말고,
   중요한 수치·규제 요건은 원문 확인이 필요하다고 덧붙여라.
5. 노트끼리 내용이 어긋나면 숨기지 말고 "노트 간 불일치"로 함께 제시하라.

출력 필드:
- has_basis: 볼트 근거로 실질적인 답이 가능했으면 true.
- answer: 마크다운 답변. 질문에 바로 답하고, 필요하면 항목별로 정리하라.
  근거가 없으면 무엇이 없는지 설명하라 (지어내지 말 것).
- used_notes: 실제로 근거로 쓴 노트 이름들 ([[]] 없이 이름만).
- gaps: 볼트에 없어서 답하지 못한 부분 (없으면 빈 배열).
- suggested_research: gaps를 메우려면 어떤 조사를 돌리면 되는지 한 줄 주제
  (필요 없으면 빈 문자열)."""


def ask(provider, query: str, vault_files: dict, limit: int = MAX_NOTES,
        include_confidential: bool = False, history: list = None) -> dict:
    """볼트 기반 질의응답. 반환: 답변 dict + 근거 노트 목록."""
    picked = search(vault_files, query, limit=limit)
    context, used = build_context(vault_files, picked, include_confidential)

    if not context:
        return {
            "has_basis": False,
            "answer": (
                "지식볼트에서 이 질문과 관련된 노트를 찾지 못했습니다.\n\n"
                "아직 이 주제가 볼트에 쌓이지 않았을 수 있습니다 — "
                "조사를 한 번 돌리거나, 관련 사이트를 모니터링에 등록해 두면 "
                "다음부터는 여기서 바로 답할 수 있습니다."
            ),
            "used_notes": [],
            "gaps": [query],
            "suggested_research": query,
            "notes": [],
            "searched": len([p for p in vault_files if str(p).endswith(".md")]),
        }

    raw = provider.generate_json(
        _answer_prompt(query, context, history),
        system=SYSTEM,
        schema=ANSWER_SCHEMA,
    )
    if not isinstance(raw, dict):
        raise ValueError("답변 응답이 JSON 객체가 아닙니다")
    return {
        "has_basis": bool(raw.get("has_basis")),
        "answer": str(raw.get("answer", "")).strip(),
        "used_notes": [str(n).strip() for n in (raw.get("used_notes") or [])],
        "gaps": [str(g).strip() for g in (raw.get("gaps") or []) if str(g).strip()],
        "suggested_research": str(raw.get("suggested_research", "")).strip(),
        "notes": used,
        "searched": len([p for p in vault_files if str(p).endswith(".md")]),
    }
