"""LLM 프로바이더 공통 인터페이스."""
import json
import re
from abc import ABC, abstractmethod


class BaseProvider(ABC):
    key: str = ""
    label: str = ""
    model: str = ""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: str = None,
        web_search: bool = False,
        max_tokens: int = 16000,
    ) -> str:
        """프롬프트를 실행하고 텍스트 응답을 반환한다."""

    def generate_json(
        self,
        prompt: str,
        system: str = None,
        schema: dict = None,
        max_tokens: int = 32000,
    ) -> dict:
        """JSON 응답을 생성한다. 기본 구현은 프롬프트 지시 + 관대한 파싱.

        (Claude 프로바이더는 구조화 출력 API로 오버라이드한다.)
        """
        json_prompt = (
            prompt
            + "\n\n반드시 아래 JSON 스키마를 따르는 유효한 JSON 객체 하나만 출력하세요."
            + " 코드 블록 표시나 설명 문장 없이 JSON만 출력합니다.\n\n스키마:\n"
            + json.dumps(schema, ensure_ascii=False, indent=2)
        )
        text = self.generate(json_prompt, system=system, max_tokens=max_tokens)
        return parse_json_loose(text)


def parse_json_loose(text: str):
    """코드펜스/앞뒤 잡음이 섞인 응답에서 JSON 값(객체 또는 배열)을 추출해 파싱한다."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 객체/배열 중 텍스트에서 먼저 시작하는 쪽부터 시도
    candidates = sorted(
        [("{", "}"), ("[", "]")],
        key=lambda pair: (text.find(pair[0]) == -1, text.find(pair[0])),
    )
    for open_ch, close_ch in candidates:
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("응답에서 JSON을 찾지 못했습니다:\n" + text[:500])
