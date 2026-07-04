"""프로바이더 팩토리 — API 키가 있는 프로바이더만 생성한다."""
from ..config import PROVIDER_SPECS, has_key, resolved_model
from .base import BaseProvider


def build_providers(selected_keys: list = None) -> list:
    """활성화 가능한 프로바이더 인스턴스 목록을 반환한다.

    selected_keys 가 주어지면 그 목록에 포함된 프로바이더만 생성.
    """
    providers = []
    for spec in PROVIDER_SPECS:
        if selected_keys is not None and spec.key not in selected_keys:
            continue
        if not has_key(spec):
            continue
        model = resolved_model(spec)
        if spec.key == "anthropic":
            from .anthropic_provider import AnthropicProvider
            providers.append(AnthropicProvider(model=model))
        elif spec.key == "openai":
            from .openai_provider import OpenAIProvider
            providers.append(OpenAIProvider(model=model))
        elif spec.key == "gemini":
            from .gemini_provider import GeminiProvider
            providers.append(GeminiProvider(model=model))
    return providers
