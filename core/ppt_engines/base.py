"""외부 PPT 디자인 엔진 공통 인터페이스.

각 엔진은 종합된 보고서 JSON을 받아 디자인이 적용된 PPT를 생성한다.
API 키가 없으면 available()이 False를 반환하고 UI에서 비활성화된다.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EngineResult:
    engine: str          # 엔진 label
    url: str = ""        # 웹에서 열기/편집 링크
    pptx_bytes: bytes = b""  # PPTX 파일 (받을 수 있는 경우)
    note: str = ""       # 사용자 안내 메모


class PPTEngine(ABC):
    key: str = ""        # 내부 식별자
    label: str = ""      # UI 표시명
    requires: str = ""   # 필요한 환경변수 안내 (UI 표시용)

    @abstractmethod
    def available(self) -> bool:
        """API 키/인증 정보가 설정되어 있는지."""

    @abstractmethod
    def generate(self, report: dict, target_pages: int = 12) -> EngineResult:
        """보고서 JSON → 디자인 PPT 생성. 실패 시 RuntimeError."""


def report_to_markdown(report: dict, target_pages: int = 12) -> str:
    """보고서 JSON → 카드(---) 구분 마크다운. Gamma 등 텍스트 기반 엔진 입력용."""
    cards = []
    title = report.get("title", "조사 보고서")
    subtitle = report.get("subtitle", "")
    cards.append(f"# {title}\n\n{subtitle}")

    if report.get("executive_summary"):
        cards.append("## Executive Summary\n\n" + report["executive_summary"])

    if report.get("key_findings"):
        cards.append(
            "## 핵심 발견사항\n\n"
            + "\n".join(f"- {x}" for x in report["key_findings"])
        )

    for sec in report.get("sections", []):
        body = sec.get("content", "")
        bullets = sec.get("bullets") or []
        if bullets:
            body += "\n\n" + "\n".join(f"- {b}" for b in bullets)
        cards.append(f"## {sec.get('heading', '')}\n\n{body}")

    for t in report.get("data_tables", []):
        headers = t.get("headers", [])
        rows = t.get("rows", [])
        if not headers:
            continue
        md = f"## {t.get('title', '데이터')}\n\n"
        md += "| " + " | ".join(str(h) for h in headers) + " |\n"
        md += "|" + "---|" * len(headers) + "\n"
        for row in rows[:20]:
            cells = [str(row[i]) if i < len(row) else "" for i in range(len(headers))]
            md += "| " + " | ".join(cells) + " |\n"
        cards.append(md.rstrip())

    if report.get("recommendations"):
        cards.append(
            "## 제언 (Recommendations)\n\n"
            + "\n".join(f"- {x}" for x in report["recommendations"])
        )

    if report.get("sources"):
        cards.append(
            "## 출처\n\n"
            + "\n".join(
                f"- {s.get('title', '')} — {s.get('url', '')}"
                for s in report["sources"]
            )
        )
    return "\n\n---\n\n".join(cards)
