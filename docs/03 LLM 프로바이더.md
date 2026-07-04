---
tags: [providers, llm]
updated: 2026-07-04
---

# 03 LLM 프로바이더

파일: `core/providers/` · 설정: `core/config.py`

## 공통 인터페이스 (`base.py`)

```python
class BaseProvider:
    generate(prompt, system, web_search, max_tokens) -> str
    generate_json(prompt, system, schema, max_tokens) -> dict
```

- `generate_json` 기본 구현: "JSON만 출력" 지시 + `parse_json_loose()`
  (코드펜스 제거 → 전체 파싱 → 객체/배열 슬라이스 순으로 시도)
- Claude만 구조화 출력 API로 오버라이드

## 프로바이더별 상세

### Claude — `anthropic_provider.py`

| 항목 | 값 |
|---|---|
| 모델 (기본) | `claude-opus-4-8` (`ANTHROPIC_MODEL`로 변경) |
| 키 | `ANTHROPIC_API_KEY` |
| 웹 검색 | 서버 도구 `web_search_20260209` + `web_fetch_20260209` (max_uses 8) |
| 특이 처리 | ① 항상 스트리밍 실행(`messages.stream`) 후 `get_final_message()` — 긴 출력 타임아웃 방지 ② `thinking: {type: "adaptive"}` ③ `stop_reason == "pause_turn"` 시 assistant content를 붙여 재개 (최대 5회) ④ `refusal` 처리 |
| 구조화 출력 | `output_config.format = json_schema` — 종합 단계 신뢰성 핵심 |

⚠️ **현재 상태**: 키는 유효하나 계정 크레딧 0 → 모든 호출이
`credit balance is too low` 400. Plans & Billing 충전 즉시 작동.

### GPT — `openai_provider.py`

| 항목 | 값 |
|---|---|
| 모델 (기본) | `gpt-5` (`OPENAI_MODEL`로 변경) |
| 키 | `OPENAI_API_KEY` |
| 웹 검색 | Responses API `tools=[{"type": "web_search"}]` |
| 폴백 | Responses 실패 시 Chat Completions (웹 검색 없음) |

⚠️ **현재 상태**: 키 미등록, 실호출 미검증. 키 등록 후 responses API의
파라미터 호환(모델별 tools 지원)을 한 번 확인할 것.

### Gemini — `gemini_provider.py`

| 항목 | 값 |
|---|---|
| 모델 (기본) | `gemini-2.5-pro` (`GEMINI_MODEL`로 변경) |
| 키 | `GOOGLE_API_KEY` 또는 `GEMINI_API_KEY` |
| SDK | `google-genai` (신형 — `from google import genai`) |
| 웹 검색 | `types.Tool(google_search=types.GoogleSearch())` 그라운딩 |
| 폴백 | 그라운딩 거부 환경에서는 도구 없이 재시도 |

✅ **현재 상태**: 실키로 전 기능 검증 완료.

#### 해결한 버그: 빈 응답 (2026-07-04)

- **증상**: 짧은 max_tokens에서 `resp.text`가 빈 문자열
- **원인**: Gemini 2.5는 내부 추론(thinking) 토큰이 `max_output_tokens`에서
  차감됨 → 한도가 작으면 추론만 하다 본문 없이 종료
- **해결**: ① `max_output_tokens = max(요청값, 8192)` 하한
  ② `_extract_text()` — text가 비면 candidates의 parts에서 직접 수집
  (thought 파트 제외), 그래도 없으면 finish_reason 포함 안내 문자열

## 프로바이더 팩토리 (`providers/__init__.py`)

`build_providers(selected_keys)` — 키가 있는 프로바이더만 인스턴스화.
새 프로바이더 추가 절차:
1. `config.PROVIDER_SPECS`에 spec 추가
2. `providers/`에 BaseProvider 구현체 작성
3. 팩토리 분기 추가 — UI는 자동 반영됨

## 장애 격리 설계

- 조사/토론 실패는 per-provider로 잡아서 기록만 하고 진행
- 진행자·탐색 담당은 **성공한/우선순위** 프로바이더에서 자동 선택
  - 진행자: Claude → GPT → Gemini (`pick_moderator`)
  - 레퍼런스 탐색: Claude → Gemini → GPT + 실패 시 자동 폴백
    (`discover_with_fallback`) — Claude 크레딧 부족 상황에서 실검증됨

관련: [[02 파이프라인 설계]] · [[08 작업 이력]]
