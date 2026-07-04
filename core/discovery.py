"""레퍼런스 자동 탐색 — LLM이 웹 검색으로 신뢰할 수 있는 출처를 직접 찾아온다."""
from .providers.base import BaseProvider, parse_json_loose

_DISCOVERY_SYSTEM = (
    "당신은 글로벌 컨설팅 펌의 리서치 사서(research librarian)입니다. "
    "주어진 주제에 대해 신뢰도 높은 1차 출처를 웹 검색으로 발굴하는 것이 임무입니다."
)


def _discovery_prompt(topic: str, instructions: str, count: int) -> str:
    extra = f"\n\n## 추가 지시사항\n{instructions}" if instructions.strip() else ""
    return f"""## 조사 주제
{topic}{extra}

## 작업
위 주제를 조사할 때 근거 자료로 사용할 수 있는 신뢰도 높은 레퍼런스를
웹 검색으로 확인하여 {count}개 추천하세요.

우선순위:
1. 공식 기관·규제기관 (정부 부처, EU 집행위, UN 산하기구 등)
2. 표준·프레임워크 발행기관 (ISSB, GRI, CDP, SBTi, TCFD 등)
3. 국제기구·연구기관의 보고서 (IEA, World Bank, McKinsey, 국책연구원 등)
4. 신뢰도 높은 전문 언론·산업 매체

규칙:
- 반드시 웹 검색으로 실제 존재를 확인한 페이지만 포함하세요.
- url은 검색 결과에 나온 실제 원본 주소를 그대로 쓰세요 (리다이렉트/단축 URL 금지).
- 같은 사이트는 최대 2개까지만 포함하세요.

출력은 아래 형식의 JSON 하나만 (코드 블록·설명 문장 없이):
{{"references": [{{"title": "자료 제목", "url": "https://...", "publisher": "발행 기관", "reason": "이 자료가 유용한 이유 한 문장"}}]}}"""


def discover_references(
    provider: BaseProvider, topic: str, instructions: str = "", count: int = 6
) -> list:
    """웹 검색으로 레퍼런스 후보 목록을 찾는다.

    반환: [{"title", "url", "publisher", "reason"}, ...]
    """
    prompt = _discovery_prompt(topic, instructions, count)
    text = provider.generate(
        prompt, system=_DISCOVERY_SYSTEM, web_search=True, max_tokens=16000
    )
    try:
        data = parse_json_loose(text)
    except ValueError:
        # 검색 도구를 쓰면서 JSON 형식이 깨진 경우 — 재포맷 요청 (검색 없이)
        reformat = (
            "아래 텍스트에 포함된 레퍼런스 목록을 "
            '{"references": [{"title", "url", "publisher", "reason"}]} '
            "형식의 JSON 하나로만 변환하세요. JSON 외 다른 출력 금지.\n\n" + text
        )
        data = parse_json_loose(provider.generate(reformat, max_tokens=8000))

    if isinstance(data, list):
        items = data
    else:
        items = data.get("references", [])

    refs, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        refs.append(
            {
                "title": str(item.get("title", "")).strip() or url,
                "url": url,
                "publisher": str(item.get("publisher", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
            }
        )
    return refs[:count]


def pick_searcher(providers: list) -> BaseProvider:
    """탐색 담당 우선순위: Claude → Gemini → GPT (검색 품질 기준)."""
    priority = {"anthropic": 0, "gemini": 1, "openai": 2}
    return sorted(providers, key=lambda p: priority.get(p.key, 9))[0]


def discover_with_fallback(
    providers: list, topic: str, instructions: str = "", count: int = 6
):
    """우선순위 순서로 탐색을 시도하고, 실패하면 다음 프로바이더로 자동 전환한다.

    반환: (담당 프로바이더 label, 레퍼런스 목록)
    """
    priority = {"anthropic": 0, "gemini": 1, "openai": 2}
    ordered = sorted(providers, key=lambda p: priority.get(p.key, 9))
    errors = []
    for p in ordered:
        try:
            refs = discover_references(p, topic, instructions, count)
            if refs:
                return p.label, refs
            errors.append(f"{p.label}: 결과 없음")
        except Exception as e:
            errors.append(f"{p.label}: {e}")
    raise RuntimeError("모든 프로바이더에서 탐색 실패 — " + " / ".join(errors))
