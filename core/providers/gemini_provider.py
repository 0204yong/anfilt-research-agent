"""Google Gemini 프로바이더 — Google Search 그라운딩 지원."""
import os

from google import genai
from google.genai import types

from .base import BaseProvider


class GeminiProvider(BaseProvider):
    key = "gemini"
    label = "Gemini (Google)"

    def __init__(self, model: str = "gemini-2.5-pro"):
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def generate(self, prompt, system=None, web_search=False, max_tokens=16000) -> str:
        # Gemini 2.5 계열은 내부 추론(thinking) 토큰이 출력 한도에서 차감되므로,
        # 한도가 너무 작으면 본문이 비어서 돌아온다 — 하한선을 둔다.
        config_kwargs = {"max_output_tokens": max(max_tokens, 8192)}
        if system:
            config_kwargs["system_instruction"] = system
        if web_search:
            config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        try:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception:
            if not web_search:
                raise
            # 검색 그라운딩이 거부되는 환경에서는 도구 없이 재시도
            config_kwargs.pop("tools", None)
            resp = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        return self._extract_text(resp)

    @staticmethod
    def _extract_text(resp) -> str:
        if resp.text:
            return resp.text.strip()
        # resp.text가 비면 후보의 파트에서 직접 텍스트를 수집
        parts = []
        for cand in resp.candidates or []:
            content = getattr(cand, "content", None)
            for part in (getattr(content, "parts", None) or []):
                if getattr(part, "text", None) and not getattr(part, "thought", False):
                    parts.append(part.text)
        if parts:
            return "\n".join(parts).strip()
        reason = ""
        if resp.candidates:
            reason = f" (finish_reason: {resp.candidates[0].finish_reason})"
        return f"[Gemini가 빈 응답을 반환했습니다{reason}]"
