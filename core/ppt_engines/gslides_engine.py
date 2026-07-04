"""Google Slides 엔진 — Slides API로 프레젠테이션 생성 (편집·협업용).

필요 설정: .env 에 GOOGLE_SERVICE_ACCOUNT_FILE (서비스 계정 JSON 파일 경로)
발급 절차 (설계서 06 문서 참고):
1. Google Cloud Console에서 프로젝트 생성
2. Slides API + Drive API 활성화
3. 서비스 계정 생성 → JSON 키 다운로드 → 경로를 환경변수에 지정

생성된 문서는 '링크가 있는 모든 사용자 편집 가능'으로 공유 설정된다.
(GOOGLE_SLIDES_SHARE_ROLE=reader 로 바꾸면 열람 전용)
"""
import os

from .base import EngineResult, PPTEngine

_SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSlidesEngine(PPTEngine):
    key = "gslides"
    label = "Google Slides"
    requires = "GOOGLE_SERVICE_ACCOUNT_FILE"

    def available(self) -> bool:
        path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        return bool(path) and os.path.exists(path)

    def _clients(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "google-api-python-client가 설치되지 않았습니다. "
                "pip install google-api-python-client google-auth"
            )
        creds = service_account.Credentials.from_service_account_file(
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"), scopes=_SCOPES
        )
        return (
            build("slides", "v1", credentials=creds),
            build("drive", "v3", credentials=creds),
        )

    def generate(self, report: dict, target_pages: int = 12) -> EngineResult:
        slides, drive = self._clients()
        title = report.get("title", "조사 보고서")

        pres = slides.presentations().create(body={"title": title}).execute()
        pid = pres["presentationId"]
        default_slide_id = pres["slides"][0]["objectId"]

        requests_body = [{"deleteObject": {"objectId": default_slide_id}}]
        idx = 0

        def add_slide(layout, texts):
            """texts: {placeholder_type: 내용}. 요청 목록에 슬라이드 생성+텍스트 삽입 추가."""
            nonlocal idx
            idx += 1
            sid = f"slide_{idx}"
            mappings, inserts = [], []
            for i, (ph_type, text) in enumerate(texts.items()):
                oid = f"{sid}_ph{i}"
                mappings.append(
                    {"layoutPlaceholder": {"type": ph_type}, "objectId": oid}
                )
                if text:
                    inserts.append(
                        {"insertText": {"objectId": oid, "text": str(text)[:4500]}}
                    )
            requests_body.append(
                {
                    "createSlide": {
                        "objectId": sid,
                        "slideLayoutReference": {"predefinedLayout": layout},
                        "placeholderIdMappings": mappings,
                    }
                }
            )
            requests_body.extend(inserts)

        # 표지
        add_slide(
            "TITLE",
            {"CENTERED_TITLE": title, "SUBTITLE": report.get("subtitle", "")},
        )
        # 요약
        add_slide(
            "TITLE_AND_BODY",
            {"TITLE": "Executive Summary", "BODY": report.get("executive_summary", "")},
        )
        # 핵심 발견사항
        if report.get("key_findings"):
            add_slide(
                "TITLE_AND_BODY",
                {
                    "TITLE": "핵심 발견사항",
                    "BODY": "\n".join(f"• {x}" for x in report["key_findings"]),
                },
            )
        # 본문 섹션
        for sec in report.get("sections", []):
            bullets = sec.get("bullets") or []
            body = (
                "\n".join(f"• {b}" for b in bullets)
                if bullets
                else sec.get("content", "")
            )
            add_slide("TITLE_AND_BODY", {"TITLE": sec.get("heading", ""), "BODY": body})
        # 데이터 표 (텍스트 형태 — 세부 표 서식은 Slides에서 직접 편집)
        for t in report.get("data_tables", []):
            headers = t.get("headers", [])
            lines = [" | ".join(str(h) for h in headers)]
            for row in t.get("rows", [])[:12]:
                lines.append(
                    " | ".join(
                        str(row[i]) if i < len(row) else "" for i in range(len(headers))
                    )
                )
            add_slide(
                "TITLE_AND_BODY",
                {"TITLE": t.get("title", "데이터"), "BODY": "\n".join(lines)},
            )
        # 제언
        if report.get("recommendations"):
            add_slide(
                "TITLE_AND_BODY",
                {
                    "TITLE": "제언 (Recommendations)",
                    "BODY": "\n".join(f"• {x}" for x in report["recommendations"]),
                },
            )
        # 출처
        if report.get("sources"):
            add_slide(
                "TITLE_AND_BODY",
                {
                    "TITLE": "출처",
                    "BODY": "\n".join(
                        f"• {s.get('title', '')} — {s.get('url', '')}"
                        for s in report["sources"]
                    ),
                },
            )

        slides.presentations().batchUpdate(
            presentationId=pid, body={"requests": requests_body}
        ).execute()

        # 링크 공유 설정 (기본: 편집 가능)
        role = os.getenv("GOOGLE_SLIDES_SHARE_ROLE", "writer")
        drive.permissions().create(
            fileId=pid, body={"type": "anyone", "role": role}
        ).execute()

        url = f"https://docs.google.com/presentation/d/{pid}/edit"
        return EngineResult(
            engine=self.label,
            url=url,
            note="링크가 있는 사람은 누구나 편집할 수 있도록 공유되었습니다. "
            "테마는 Slides에서 '테마 가져오기'로 적용하세요.",
        )
