"""프로바이더 구성 및 API 키 감지."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from . import edition, keys

load_dotenv()

DEFAULT_PERSONA = (
    "당신은 글로벌 컨설팅 펌의 수석 연구원입니다. "
    "ESG·환경·지속가능성 분야의 국제 전문 자격을 보유하고 있으며, "
    "고객사에 제출할 수준의 정확하고 근거 기반의 조사 자료를 작성합니다. "
    "모든 주장에는 출처를 명시하고, 확인되지 않은 내용은 추정임을 밝힙니다."
)


@dataclass(frozen=True)
class ProviderSpec:
    key: str            # 내부 식별자
    label: str          # UI 표시명
    env_vars: tuple     # API 키 환경변수 (하나라도 있으면 활성)
    default_model: str
    model_env: str      # 모델 오버라이드 환경변수


PROVIDER_SPECS = [
    ProviderSpec(
        key="anthropic",
        label="Claude (Anthropic)",
        env_vars=("ANTHROPIC_API_KEY",),
        default_model="claude-opus-4-8",
        model_env="ANTHROPIC_MODEL",
    ),
    ProviderSpec(
        key="openai",
        label="GPT (OpenAI)",
        env_vars=("OPENAI_API_KEY",),
        default_model="gpt-5",
        model_env="OPENAI_MODEL",
    ),
    ProviderSpec(
        key="gemini",
        label="Gemini (Google)",
        env_vars=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        default_model="gemini-2.5-pro",
        model_env="GEMINI_MODEL",
    ),
]


# 라이트 모드 경량 모델 기본값 (→ docs/14 라이트 모드).
# 오버라이드: <KEY>_LIGHT_MODEL 환경변수 (예: GEMINI_LIGHT_MODEL)
# 주의: gemini-2.5-flash는 신규 사용자에게 404 (2026-07 실측) → 3.5-flash 사용
LIGHT_MODEL_DEFAULTS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-5-mini",
    "gemini": "gemini-3.5-flash",
}


def api_key_for(spec: ProviderSpec) -> str:
    """이 프로바이더의 API 키. 키체인 → 환경변수 순 (→ core/keys.py).

    프로바이더 SDK 가 환경변수에서 암묵적으로 읽게 두지 않고 **여기서 꺼내
    생성자에 넘긴다** — 그래야 화면에서 등록한 키가 재시작 없이 반영되고,
    사용자 키가 `os.environ` 에 올라가지 않는다.
    """
    for var in spec.env_vars:
        val = keys.get(var)
        if val:
            return val
    return ""


def has_key(spec: ProviderSpec) -> bool:
    return bool(api_key_for(spec))


def _model_override(spec: ProviderSpec) -> str:
    """정식판은 설정 화면(config.json)의 모델 오버라이드를 먼저 본다."""
    if edition.is_installed():
        try:
            from . import settings
            val = (settings.load().get("models") or {}).get(spec.key)
            if val:
                return str(val)
        except Exception:
            pass
    return ""


def resolved_model(spec: ProviderSpec) -> str:
    return _model_override(spec) or os.getenv(spec.model_env) or spec.default_model


def resolved_light_model(spec: ProviderSpec) -> str:
    """라이트 모드용 경량 모델 — 설정/env 오버라이드 > 경량 기본값 > 일반 모델."""
    if edition.is_installed():
        try:
            from . import settings
            val = (settings.load().get("light_models") or {}).get(spec.key)
            if val:
                return str(val)
        except Exception:
            pass
    return (
        os.getenv(f"{spec.key.upper()}_LIGHT_MODEL")
        or LIGHT_MODEL_DEFAULTS.get(spec.key)
        or resolved_model(spec)
    )


def key_status() -> dict:
    """{provider_key: bool} — UI에서 키 보유 여부 표시용."""
    return {spec.key: has_key(spec) for spec in PROVIDER_SPECS}
