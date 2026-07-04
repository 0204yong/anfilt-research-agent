"""프로바이더 구성 및 API 키 감지."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

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


def has_key(spec: ProviderSpec) -> bool:
    return any(os.getenv(v) for v in spec.env_vars)


def resolved_model(spec: ProviderSpec) -> str:
    return os.getenv(spec.model_env) or spec.default_model


def key_status() -> dict:
    """{provider_key: bool} — UI에서 키 보유 여부 표시용."""
    return {spec.key: has_key(spec) for spec in PROVIDER_SPECS}
