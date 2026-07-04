"""Gamma 엔진 — Generate API로 디자인 완성형 프레젠테이션 생성.

필요 설정: .env 에 GAMMA_API_KEY (Gamma Pro 이상 플랜에서 발급)
문서: https://developers.gamma.app/
"""
import os
import time

import requests

from .base import EngineResult, PPTEngine, report_to_markdown

_BASE = "https://public-api.gamma.app/v0.2"
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 300


class GammaEngine(PPTEngine):
    key = "gamma"
    label = "Gamma"
    requires = "GAMMA_API_KEY"

    def available(self) -> bool:
        return bool(os.getenv("GAMMA_API_KEY"))

    def _headers(self) -> dict:
        return {
            "X-API-KEY": os.getenv("GAMMA_API_KEY", ""),
            "Content-Type": "application/json",
        }

    def generate(self, report: dict, target_pages: int = 12) -> EngineResult:
        markdown = report_to_markdown(report, target_pages)
        payload = {
            "inputText": markdown,
            "format": "presentation",
            # 우리 파이프라인이 이미 내용을 확정했으므로 원문 유지 모드 사용
            "textMode": "preserve",
            "cardSplit": "inputTextBreaks",  # '---' 기준으로 카드 분할
            "numCards": max(1, min(target_pages, 30)),
            "exportAs": "pptx",
            "textOptions": {"language": "ko"},
            "additionalInstructions": (
                "글로벌 컨설팅 펌의 고객사 제출용 보고서 스타일. "
                "전문적이고 신뢰감 있는 디자인, 데이터 시각화 강조."
            ),
        }
        resp = requests.post(
            f"{_BASE}/generations", json=payload, headers=self._headers(), timeout=60
        )
        if resp.status_code == 401 or resp.status_code == 403:
            raise RuntimeError("Gamma API 키가 유효하지 않습니다. GAMMA_API_KEY를 확인하세요.")
        resp.raise_for_status()
        generation_id = resp.json().get("generationId")
        if not generation_id:
            raise RuntimeError(f"Gamma 응답에 generationId가 없습니다: {resp.text[:300]}")

        # 완료까지 폴링
        deadline = time.time() + _POLL_TIMEOUT
        data = {}
        while time.time() < deadline:
            time.sleep(_POLL_INTERVAL)
            poll = requests.get(
                f"{_BASE}/generations/{generation_id}",
                headers=self._headers(),
                timeout=30,
            )
            poll.raise_for_status()
            data = poll.json()
            status = str(data.get("status", "")).lower()
            if status == "completed":
                break
            if status in ("failed", "error"):
                raise RuntimeError(f"Gamma 생성 실패: {data}")
        else:
            raise RuntimeError("Gamma 생성이 5분 내에 완료되지 않았습니다.")

        gamma_url = data.get("gammaUrl") or data.get("url") or ""
        pptx_bytes = self._download_export(data)
        return EngineResult(
            engine=self.label,
            url=gamma_url,
            pptx_bytes=pptx_bytes,
            note="Gamma 웹 링크에서 디자인을 편집할 수 있습니다."
            + ("" if pptx_bytes else " (PPTX 내보내기 URL을 찾지 못해 링크만 제공)"),
        )

    @staticmethod
    def _download_export(data: dict) -> bytes:
        """응답에서 PPTX 내보내기 URL을 찾아 다운로드 (필드명 방어적 처리)."""
        candidates = []
        for k, v in data.items():
            if isinstance(v, str) and v.startswith("http") and (
                "export" in k.lower() or "pptx" in k.lower() or v.lower().endswith(".pptx")
            ):
                candidates.append(v)
        exports = data.get("exports") or data.get("exportUrls") or {}
        if isinstance(exports, dict):
            candidates.extend(
                v for v in exports.values() if isinstance(v, str) and v.startswith("http")
            )
        for url in candidates:
            try:
                r = requests.get(url, timeout=120)
                if r.ok and r.content[:2] == b"PK":
                    return r.content
            except Exception:
                continue
        return b""
