"""외부 PPT 디자인 엔진 레지스트리."""
from .base import EngineResult, PPTEngine, report_to_markdown
from .canva_engine import CanvaEngine
from .gamma_engine import GammaEngine
from .gslides_engine import GoogleSlidesEngine


def list_engines() -> list:
    """사용 가능한 외부 엔진 인스턴스 목록 (키 유무와 무관하게 전체 반환)."""
    return [GammaEngine(), CanvaEngine(), GoogleSlidesEngine()]


__all__ = [
    "EngineResult",
    "PPTEngine",
    "list_engines",
    "report_to_markdown",
    "GammaEngine",
    "CanvaEngine",
    "GoogleSlidesEngine",
]
