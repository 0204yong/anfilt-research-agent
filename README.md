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

## 📡 자동 모니터링 (선택)

사이트나 검색 키워드를 **매일 정해진 시각에 점검**해 새로운 내용만 잡아내고,
요약을 지식볼트에 축적한 뒤 **이메일·카카오톡**으로 알려 줍니다.
왼쪽 메뉴 **📡 모니터링** 페이지에서 등록합니다.

| 감시 종류 | 대상 | 새 항목의 정의 |
|---|---|---|
| 특정 페이지 | 목록·공지 페이지 URL | 새로 생긴 링크, 또는 본문 200자 이상 증가 |
| 키워드 검색 | 검색어 | 웹 검색 결과 중 아직 못 본 URL |

준비물:

1. Supabase SQL Editor에서 **`supabase-ra-watch-setup.sql`** 실행
2. `.env`(로컬)와 **GitHub Actions Secrets**(자동 실행)에 알림 설정
   - 이메일: `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` `NOTIFY_EMAIL_TO`
   - 카카오톡(나에게 보내기): `KAKAO_REST_API_KEY` `KAKAO_REFRESH_TOKEN`
3. 정해진 시각 실행은 GitHub Actions가 담당합니다
   (`.github/workflows/watch.yml`, 매시 정각). 앱은 접속 중일 때만 살아 있어
   스케줄 실행을 할 수 없기 때문입니다.

```bash
python watch_run.py --list          # 등록된 감시 목록
python watch_run.py --all --no-notify   # 전부 즉시 실행 (알림 없이 테스트)
```

> 페이지 감시의 **첫 점검은 기준선 수집**이라 알림이 가지 않습니다
> (기존 링크 수십~수백 개가 한꺼번에 '새 소식'이 되는 것을 막기 위해서입니다).

## 📚 지식 비서 (볼트 기반 질의응답)

지식볼트에 쌓인 내용**만**으로 답하는 개인 사서입니다. 왼쪽 메뉴 **📚 지식 비서**.

- 모든 주장에 근거 노트를 `[[노트이름]]` 로 인용하고 `as_of` 날짜를 밝힙니다.
- 볼트에 근거가 없으면 **지어내지 않고** "없다"고 답한 뒤, 무엇이 비었는지와
  어떤 조사를 돌리면 되는지 알려 줍니다 → 버튼 한 번으로 조사 화면에 주제가 채워집니다.
- 조사 결과와 모니터링 수집분이 볼트에 쌓일수록 답변이 좋아집니다.

## 프로젝트 구조

```
app.py                      Streamlit 웹 UI — 조사
ui_common.py                페이지 공통 부트스트랩 (Secrets 브리지 + 비밀번호 게이트)
pages/
  1_📡_모니터링.py           감시 등록·수동 점검·수집 이력
  2_📚_지식_비서.py          볼트 기반 질의응답
watch_run.py                감시 스케줄러 진입점 (GitHub Actions가 호출)
core/
  config.py                 프로바이더 설정, API 키 감지
  discovery.py              레퍼런스 자동 탐색 (LLM 웹 검색)
  filerefs.py               첨부 파일(PDF/Word/PPT/Excel 등) 본문 추출
  webfetch.py               레퍼런스 URL 본문 추출
  pipeline.py               조사 → 토론 → 종합 오케스트레이션
  light.py                  라이트 모드 (경량 모델 1~2회 호출)
  store.py                  Supabase 아카이브·볼트 사본·감시 저장소
  ontology.py               엔티티 추출·노트 업서트·지식 주입
  librarian.py              지식 비서 — 볼트 검색 + 근거 강제 답변
  watch.py                  모니터링 로직 (지문 판별·수집·요약·노트 렌더)
  watch_runner.py           모니터링 실행 배선 (점검→요약→축적→알림)
  notify.py                 이메일(SMTP)·카카오톡 알림
  vault_sync.py             볼트 시드 초기화
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
