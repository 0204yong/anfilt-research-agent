"""Canva 엔진 — 로컬 생성 PPTX를 Canva로 가져와 편집 가능한 디자인으로 변환.

동작: 로컬 python-pptx로 PPT 생성 → Canva Design Import API로 업로드
→ Canva 편집 링크 반환. 사용자는 Canva에서 브랜드 키트·템플릿을 적용해
디자인을 다듬을 수 있다.

필요 설정: .env 에 CANVA_ACCESS_TOKEN
- Canva Connect API의 OAuth 액세스 토큰 (scope: design:content:write)
- https://www.canva.dev/docs/connect/ 에서 앱 등록 후 발급
- 액세스 토큰은 만료(약 4시간)되므로 장기 운영 시 리프레시 토큰 자동 갱신
  로직을 추가해야 한다 (설계서 참고).
"""
import base64
import json
import os
import time

import requests

from .base import EngineResult, PPTEngine

_BASE = "https://api.canva.com/rest/v1"
_POLL_INTERVAL = 3
_POLL_TIMEOUT = 180
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class CanvaEngine(PPTEngine):
    key = "canva"
    label = "Canva"
    requires = "CANVA_ACCESS_TOKEN"

    def available(self) -> bool:
        return bool(os.getenv("CANVA_ACCESS_TOKEN"))

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {os.getenv('CANVA_ACCESS_TOKEN', '')}"}

    def generate(self, report: dict, target_pages: int = 12) -> EngineResult:
        from ..reports import build_pptx

        pptx_bytes = build_pptx(report, target_pages)
        title = report.get("title", "조사 보고서")
        metadata = {
            "title_base64": base64.b64encode(title.encode("utf-8")).decode("ascii"),
            "mime_type": _PPTX_MIME,
        }
        resp = requests.post(
            f"{_BASE}/imports",
            headers={
                **self._auth(),
                "Content-Type": "application/octet-stream",
                "Import-Metadata": json.dumps(metadata),
            },
            data=pptx_bytes,
            timeout=120,
        )
        if resp.status_code == 401:
            raise RuntimeError(
                "Canva 액세스 토큰이 만료되었거나 유효하지 않습니다. "
                "CANVA_ACCESS_TOKEN을 갱신하세요."
            )
        resp.raise_for_status()
        job = resp.json().get("job", {})
        job_id = job.get("id")
        if not job_id:
            raise RuntimeError(f"Canva 응답에 job id가 없습니다: {resp.text[:300]}")

        deadline = time.time() + _POLL_TIMEOUT
        while time.time() < deadline:
            time.sleep(_POLL_INTERVAL)
            poll = requests.get(
                f"{_BASE}/imports/{job_id}", headers=self._auth(), timeout=30
            )
            poll.raise_for_status()
            job = poll.json().get("job", {})
            status = str(job.get("status", "")).lower()
            if status == "success":
                break
            if status == "failed":
                raise RuntimeError(f"Canva 가져오기 실패: {job.get('error', job)}")
        else:
            raise RuntimeError("Canva 가져오기가 3분 내에 완료되지 않았습니다.")

        designs = (job.get("result") or {}).get("designs", [])
        if not designs:
            raise RuntimeError(f"Canva 결과에 디자인이 없습니다: {job}")
        urls = designs[0].get("urls", {})
        edit_url = urls.get("edit_url") or urls.get("view_url") or ""
        return EngineResult(
            engine=self.label,
            url=edit_url,
            pptx_bytes=pptx_bytes,
            note="Canva 편집 링크에서 브랜드 키트·템플릿을 적용해 디자인을 다듬으세요.",
        )
