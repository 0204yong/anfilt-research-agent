# 멀티 LLM 리서치 에이전트

여러 LLM(Claude · GPT · Gemini)이 **동일한 주제/키워드/레퍼런스**를 받아 병렬로 조사하고,
서로의 결과를 **토론(교차 검토)** 한 뒤, 진행자 LLM이 종합한 최종 결과를
**PPT / Word / Excel 보고서**로 만들어 주는 프로그램입니다.

## 동작 방식

```
[입력] 주제 + 검색 키워드 + 레퍼런스 URL
   │      (⑤ 레퍼런스 자동 탐색: 주제만 있으면 LLM이 출처를 직접 찾아줌)
   ▼
① 조사 단계 ─ Claude, GPT, Gemini가 병렬로 독립 조사
   │            (키워드가 있으면 각자 자체 웹 검색 사용,
   │             레퍼런스 URL은 본문을 추출해 모두에게 동일하게 제공)
   ▼
② 토론 단계 ─ 서로의 결과를 익명(연구원 A/B/C)으로 교차 검토
   │            오류 지적 · 근거 비교 · 입장 수정 (0~3라운드)
   ▼
③ 종합 단계 ─ 진행자 LLM이 전체를 평가해 최종 보고서 생성
   │            · 종합 모드: 모든 결과를 교차 검증해 통합
   │            · 베스트 모드: 가장 우수한 결과 중심으로 구성
   ▼
④ 보고서 출력 ─ PPT(.pptx) / Word(.docx) / Excel(.xlsx) 다운로드
```

## 설치

Python 3.10 이상이 필요합니다.

```bash
pip install -r requirements.txt
```

## API 키 설정

`.env.example`을 `.env`로 복사한 뒤 보유한 키를 입력합니다.

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
```

- 키가 없는 프로바이더는 자동으로 비활성화됩니다 (**최소 1개** 필요).
- 토론이 성립하려면 2개 이상을 권장합니다.
- 모델을 바꾸고 싶으면 `ANTHROPIC_MODEL`, `OPENAI_MODEL`, `GEMINI_MODEL`을 설정하세요.

## 실행

```bash
streamlit run app.py
```

브라우저가 열리면:

1. **조사 주제** 입력 (필수)
2. **검색 키워드**(웹 검색용) 또는 **레퍼런스 URL**(원문 제공용) 입력
   - **파일 첨부**도 가능 — PDF·Word(docx)·PPT(pptx)·Excel(xlsx)·텍스트(txt/md/csv)
     파일을 올리면 본문을 추출해 모든 LLM에게 레퍼런스 원문으로 제공합니다
   - URL을 모르면 **🔎 레퍼런스 찾기** 버튼 클릭 — LLM이 웹 검색으로
     신뢰할 수 있는 출처(공식 기관·표준기구·연구기관 등)를 찾아오고,
     체크된 항목이 조사 시 레퍼런스 원문으로 자동 포함됩니다.
3. 사이드바에서 참여 LLM · 토론 라운드 · **보고서 분량(1~20장)** · 종합 방식 · 보고서 형식 선택
   - 분량은 PPT 슬라이드 기준이며, 목표 장수에 맞춰 본문 섹션 수·데이터 표 수·
     섹션별 서술 분량이 자동 조절됩니다 (Word/Excel 분량도 비례 변동)
   - 1장 = 원페이저(요약·발견·제언을 한 장에 압축), 2~7장 = 컴팩트 구성,
     8장 이상 = 표준 구성(표지·요약·발견·본문·표·제언·출처)
4. **조사 시작** 클릭 → 진행 상황 확인 → 결과 탭에서 보고서 확인 및 다운로드

## 프로젝트 구조

```
app.py                      Streamlit 웹 UI
core/
  config.py                 프로바이더 설정, API 키 감지
  discovery.py              레퍼런스 자동 탐색 (LLM 웹 검색)
  filerefs.py               첨부 파일(PDF/Word/PPT/Excel 등) 본문 추출
  webfetch.py               레퍼런스 URL 본문 추출
  pipeline.py               조사 → 토론 → 종합 오케스트레이션
  providers/
    base.py                 공통 인터페이스
    anthropic_provider.py   Claude (웹 검색 + 구조화 출력)
    openai_provider.py      GPT (Responses API + 웹 검색)
    gemini_provider.py      Gemini (Google Search 그라운딩)
  reports/
    pptx_builder.py         PPT 생성
    docx_builder.py         Word 생성
    xlsx_builder.py         Excel 생성
```

## 외부 디자인 엔진 (선택)

다운로드 탭의 **"🎨 외부 디자인 엔진"** 에서 시각적 완성도가 높은 PPT를 만들 수 있습니다.
`.env`에 키를 넣으면 자동 활성화됩니다:

| 엔진 | 필요 설정 | 결과물 |
|---|---|---|
| **Gamma** | `GAMMA_API_KEY` (Pro 이상 플랜) | AI 디자인 덱 — 웹 링크 + PPTX |
| **Canva** | `CANVA_ACCESS_TOKEN` (Connect API) | 로컬 PPT를 Canva로 가져와 편집 링크 제공 |
| **Google Slides** | `GOOGLE_SERVICE_ACCOUNT_FILE` (GCP 서비스 계정 JSON 경로) | 공유·공동편집 가능한 슬라이드 링크 |

> ⚠️ 외부 엔진 사용 시 보고서 내용이 해당 서비스 서버로 전송됩니다.
> NDA·민감 자료는 기본 로컬 PPT 생성을 사용하세요.
> 자세한 키 발급 절차는 `docs/`(옵시디언 볼트)의 "06 PPT 디자인 엔진" 문서 참고.

## 비용 관련 참고

- 실행 1회당 (참여 LLM 수) × (1 + 토론 라운드 수) + 1(종합) 번의 LLM 호출이 발생합니다.
- 웹 검색 사용 시 프로바이더별 검색 요금이 추가될 수 있습니다.
- 토론 라운드를 늘리면 품질이 좋아질 수 있지만 비용과 시간이 비례해 증가합니다.
