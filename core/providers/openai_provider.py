"""OpenAI GPT 프로바이더 — Responses API + 웹 검색 도구."""
from openai import OpenAI

from .base import BaseProvider


class OpenAIProvider(BaseProvider):
    key = "openai"
    label = "GPT (OpenAI)"

    def __init__(self, model: str = "gpt-5"):
        self.client = OpenAI()
        self.model = model

    def generate(self, prompt, system=None, web_search=False, max_tokens=16000) -> str:
        kwargs = {"model": self.model, "input": prompt}
        if system:
            kwargs["instructions"] = system
        if web_search:
            kwargs["tools"] = [{"type": "web_search"}]
        try:
            resp = self.client.responses.create(**kwargs)
            return (resp.output_text or "").strip()
        except Exception:
            # 구형 계정/모델 등에서 Responses API가 막히면 Chat Completions로 대체
            # (이 경로에서는 웹 검색이 지원되지 않음).
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages
            )
            return (resp.choices[0].message.content or "").strip()
