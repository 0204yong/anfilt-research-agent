"""프로바이더 팩토리 — API 키가 있는 프로바이더만 생성한다."""
from ..config import (
    PROVIDER_SPECS,
    api_key_for,
    has_key,
    resolved_light_model,
    resolved_model,
)
from .base import BaseProvider


def build_providers(selected_keys: list = None, light: bool = False) -> list:
    """활성화 가능한 프로바이더 인스턴스 목록을 반환한다.

    selected_keys 가 주어지면 그 목록에 포함된 프로바이더만 생성.
    light=True 면 경량 모델(→ config.LIGHT_MODEL_DEFAULTS)로 생성한다.
    """
    providers = []
    for spec in PROVIDER_SPECS:
        if selected_keys is not None and spec.key not in selected_keys:
            continue
        if not has_key(spec):
            continue
        model = resolved_light_model(spec) if light else resolved_model(spec)
        api_key = api_key_for(spec)
        if spec.key == "anthropic":
            from .anthropic_provider import AnthropicProvider
            providers.append(AnthropicProvider(model=model, api_key=api_key))
        elif spec.key == "openai":
            from .openai_provider import OpenAIProvider
            providers.append(OpenAIProvider(model=model, api_key=api_key))
        elif spec.key == "gemini":
            from .gemini_provider import GeminiProvider
            providers.append(GeminiProvider(model=model, api_key=api_key))
    return providers


def verify_key(spec, api_key: str = None) -> tuple:
    """키가 실제로 통하는지 **최소 호출 1회**로 확인한다. 반환: (성공, 설명).

    오타난 키로 20분짜리 조사를 시작했다가 실패하는 경로를 막는 장치다
    (→ docs/23 설치판 구현 계획 3단계 DoD). 가장 싼 경량 모델로 짧게 부른다.
    """
    api_key = api_key or api_key_for(spec)
    if not api_key:
        return False, "키가 없습니다"
    model = resolved_light_model(spec)
    try:
        if spec.key == "anthropic":
            from .anthropic_provider import AnthropicProvider
            provider = AnthropicProvider(model=model, api_key=api_key)
        elif spec.key == "openai":
            from .openai_provider import OpenAIProvider
            provider = OpenAIProvider(model=model, api_key=api_key)
        elif spec.key == "gemini":
            from .gemini_provider import GeminiProvider
            provider = GeminiProvider(model=model, api_key=api_key)
        else:
            return False, f"알 수 없는 프로바이더: {spec.key}"
        text = provider.generate("OK 라고만 답하세요.", max_tokens=16)
    except Exception as e:
        # SDK 예외 문구에 키가 섞여 나오는 경우가 있어 앞부분만 보여준다
        return False, f"{type(e).__name__}: {str(e)[:200]}"
    if not str(text).strip():
        return False, "응답이 비었습니다 (모델 이름을 확인하세요)"
    return True, f"정상 — {model}"
