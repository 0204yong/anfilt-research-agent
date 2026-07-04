"""Anthropic Claude 프로바이더 — 웹 검색/페치 서버 도구 및 구조화 출력 지원."""
import json

import anthropic

from .base import BaseProvider

_MAX_CONTINUATIONS = 5  # pause_turn 재개 상한


class AnthropicProvider(BaseProvider):
    key = "anthropic"
    label = "Claude (Anthropic)"

    def __init__(self, model: str = "claude-opus-4-8"):
        self.client = anthropic.Anthropic()
        self.model = model

    def _run(self, params: dict) -> "anthropic.types.Message":
        # 긴 출력 대비 스트리밍으로 실행하고 완성 메시지만 회수한다.
        with self.client.messages.stream(**params) as stream:
            msg = stream.get_final_message()
        # 서버 도구(웹 검색)가 반복 한도에 걸리면 pause_turn 으로 멈춘다 — 이어서 재개.
        continuations = 0
        while msg.stop_reason == "pause_turn" and continuations < _MAX_CONTINUATIONS:
            params = dict(params)
            params["messages"] = list(params["messages"]) + [
                {"role": "assistant", "content": msg.content}
            ]
            with self.client.messages.stream(**params) as stream:
                msg = stream.get_final_message()
            continuations += 1
        return msg

    def generate(self, prompt, system=None, web_search=False, max_tokens=16000) -> str:
        params = {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            params["system"] = system
        if web_search:
            params["tools"] = [
                {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
                {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 8},
            ]
        msg = self._run(params)
        if msg.stop_reason == "refusal":
            return "[Claude가 이 요청에 대한 응답을 거부했습니다]"
        return "\n".join(b.text for b in msg.content if b.type == "text").strip()

    def generate_json(self, prompt, system=None, schema=None, max_tokens=32000) -> dict:
        params = {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if system:
            params["system"] = system
        msg = self._run(params)
        text = next(b.text for b in msg.content if b.type == "text")
        return json.loads(text)
