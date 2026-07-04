---
tags: [engines, gamma, canva, google-slides]
updated: 2026-07-04
---

# 06 PPT 디자인 엔진

파일: `core/ppt_engines/` — 시각적 완성도가 높은 PPT를 외부 서비스로 생성.
**키만 넣으면 작동하는 수준으로 구현 완료, 실키 검증은 대기 상태.**

## 설계

```python
class PPTEngine:
    key / label / requires(필요 환경변수)
    available() -> bool          # 키 존재 확인
    generate(report, target_pages) -> EngineResult(url, pptx_bytes, note)
```

- 레지스트리: `list_engines()` → [Gamma, Canva, GoogleSlides]
- UI: 다운로드 탭 하단 "🎨 외부 디자인 엔진" — 키 없으면 안내 표시,
  있으면 실행 버튼 → 결과 링크/PPTX 다운로드 (세션 캐시)
- ⚠️ **보안**: 보고서 내용이 외부 서버로 전송됨 — NDA 자료는 로컬 엔진 사용
  (UI에 경고 문구 표시됨)

## Gamma — `gamma_engine.py`

| 항목 | 내용 |
|---|---|
| 키 | `GAMMA_API_KEY` — Pro 이상 플랜, Account Settings > API Keys |
| API | `POST https://public-api.gamma.app/v0.2/generations` (X-API-KEY 헤더) |
| 입력 | `report_to_markdown()` — 보고서 JSON → `---` 카드 구분 마크다운 |
| 옵션 | `textMode: preserve` (내용 유지), `cardSplit: inputTextBreaks`, `numCards`=목표 장수, `exportAs: pptx`, 한국어 |
| 흐름 | 생성 요청 → generationId 폴링(5초 간격, 5분 한도) → gammaUrl + PPTX 다운로드 |
| 방어 | export URL 필드명이 바뀔 수 있어 응답에서 url 계열 필드를 스캔해 PK 시그니처 확인 후 다운로드 |

**첫 실키 테스트 시 확인할 것**: 응답의 PPTX export 필드명, 크레딧 차감량,
numCards 상한 (현재 30으로 클램프).

## Canva — `canva_engine.py`

접근 방식이 다름: 텍스트→디자인 생성이 아니라 **로컬 PPTX를 Canva로
가져오기(Design Import API)** → 편집 가능한 Canva 디자인으로 변환.
사용자가 Canva에서 브랜드 키트·템플릿을 적용해 다듬는 워크플로.

| 항목 | 내용 |
|---|---|
| 키 | `CANVA_ACCESS_TOKEN` — Connect API OAuth 토큰 (scope: design:content:write) |
| API | `POST https://api.canva.com/rest/v1/imports` (octet-stream + Import-Metadata 헤더) → job 폴링 → design edit_url |
| 발급 | https://www.canva.dev/docs/connect/ 앱 등록 → OAuth 플로 → 토큰 |

⚠️ **알려진 제약**: 액세스 토큰이 약 4시간 만료. 장기 운영 시
리프레시 토큰 자동 갱신 구현 필요 → [[09 향후 고도화 백로그]].
대안: Canva 공식 MCP 서버(`mcp.canva.com/mcp`) 연동 (AI 어시스턴트향).

## Google Slides — `gslides_engine.py`

| 항목 | 내용 |
|---|---|
| 키 | `GOOGLE_SERVICE_ACCOUNT_FILE` — 서비스 계정 JSON 파일 **경로** |
| 라이브러리 | google-api-python-client + google-auth |
| 흐름 | presentations.create → 기본 슬라이드 삭제 → batchUpdate로 표지(TITLE)+본문(TITLE_AND_BODY) 슬라이드 생성 (placeholderIdMappings로 텍스트 삽입) → Drive 권한을 anyone/writer로 설정 → edit 링크 반환 |
| 표 | 텍스트 라인으로 삽입 (세부 표 서식은 Slides에서 직접) |

### 서비스 계정 발급 절차
1. [Google Cloud Console](https://console.cloud.google.com) → 프로젝트 생성
2. API 라이브러리에서 **Google Slides API**, **Google Drive API** 활성화
3. IAM → 서비스 계정 생성 → 키(JSON) 다운로드
4. JSON 파일 경로를 `.env`의 `GOOGLE_SERVICE_ACCOUNT_FILE`에 지정
5. (선택) `GOOGLE_SLIDES_SHARE_ROLE=reader` 로 열람 전용 공유

## 도구 자동 추천 (미구현 아이디어)

보고서 특성 기반 엔진 라우터 — [[09 향후 고도화 백로그]] 참고:
디자인 제안서→Gamma / 브랜드 적용→Canva / 협업→Slides / 민감자료→로컬.

## 검토했지만 보류한 도구 (2026-07 기준)

| 도구 | 사유 |
|---|---|
| Beautiful.ai | API가 얼리액세스 단계, 일반 공개 아님 |
| Genspark | Slides API "출시 예정", 공개 문서 없음 |
| Skywork | 공개 API 확인 안 됨 |

공개 API가 열리면 `PPTEngine` 구현체 하나 추가로 연동 가능.

관련: [[05 보고서 생성]] · [[07 설치와 실행]]
