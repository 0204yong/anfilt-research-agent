---
name: deploy
description: ANFILT 홈페이지와 Research Agent 앱을 명령 한 번으로 전체 배포. 사용자가 "/deploy", "배포해줘", "배포" 라고 하면 이 절차를 실행한다. 두 git 저장소를 커밋·푸시하면 Netlify(홈페이지)와 Streamlit Cloud(앱)가 자동 재배포되고, 라이브 검증까지 수행한다.
---

# ANFILT 원커맨드 배포

## 아키텍처 (2026-07-04 구축)

```
홈페이지: C:\ANFILT_AI\Company_Homepage  ── git push ──▶ GitHub(0204yong/anfilt-homepage)
                                                            └─▶ Netlify 자동 빌드·배포
                                                                (netlify.toml: concept-c.html → dist/index.html)
앱:       C:\ANFILT_AI\Research Agent    ── git push ──▶ GitHub(0204yong/anfilt-research-agent)
                                                            └─▶ Streamlit Cloud 자동 재배포
```

- 홈페이지 소스의 진실 = `concept-c.html` 하나. `dist/`는 gitignore — Netlify가 빌드 때 생성.
- 앱 라이브: https://anfilt-research-agent-qgb4tpo5e2dvckjh6pmpmi.streamlit.app (Invite only)
- 홈페이지 라이브: https://anfilt-homepage.netlify.app

## 배포 절차 (순서대로 실행)

### 0. 시크릿 사전 검사 (필수 — 실패 시 배포 중단)

두 저장소 각각에서 (패턴은 실제 키 형태만 매치 — 문서 속 패턴 설명은 안 걸림):
```bash
git add -A
git ls-files -z | xargs -0 grep -lIE "sk-ant-api[0-9]{2}-[A-Za-z0-9_-]{20,}|AIzaSy[A-Za-z0-9_-]{30,}|AQ\.Ab8[A-Za-z0-9_-]{10,}|sk-proj-[A-Za-z0-9_-]{20,}|whsec_[A-Za-z0-9]{20,}|nfp_[A-Za-z0-9]{20,}" 2>/dev/null
```
매치가 나오면 **커밋·푸시를 하지 말고 즉시 중단**, 해당 파일을 사용자에게 보고.
`.env`/`secrets.toml`이 스테이징됐는지도 확인 (`git ls-files | grep -E "^\.env$|secrets\.toml$"` → 없어야 정상).

### 1. 홈페이지 배포

```bash
cd /c/ANFILT_AI/Company_Homepage
git add -A
git diff --cached --quiet || git commit -m "<변경 요약>

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```
변경이 없으면 커밋을 건너뛰고 "홈페이지 변경 없음"으로 보고.

### 2. 앱 배포

```bash
cd "/c/ANFILT_AI/Research Agent"
git add -A
git diff --cached --quiet || git commit -m "<변경 요약>

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```

### 3. 라이브 검증 (푸시 후 ~90초 대기)

- 홈페이지: `curl -s https://anfilt-homepage.netlify.app/` 가 HTTP 200이고
  본문에 이번 변경의 마커(또는 최소한 `id="product"`)가 있는지 확인.
  Netlify 빌드는 보통 20~60초. 실패 시 배포 로그 확인:
  https://app.netlify.com/projects/anfilt-homepage/deploys
- 앱: `curl -s -o /dev/null -w "%{http_code}" <앱URL>` 이 200인지 확인
  (Invite only라 내용은 로그인 페이지지만 200이면 서버 정상).
  Streamlit 재배포는 1~3분. 코드 변경이 큰 경우 의존성 재설치로 더 걸릴 수 있음.

### 4. 보고

커밋 해시, 무엇이 배포됐는지, 검증 결과를 요약해 보고한다.

## 주의사항

- **절대 커밋 금지**: `.env`, `.streamlit/secrets.toml`, 서비스계정 JSON, 토큰류.
  (.gitignore가 막고 있지만 0단계 검사로 이중 확인)
- 홈페이지 수정은 `concept-c.html`에만 한다. `dist/`는 로컬 잔재이므로 수정 금지
  (배포는 Netlify 빌드가 담당). 루트 `index.html`은 옛 디자인 시안 페이지로 배포와 무관.
- Streamlit Secrets(API 키) 변경이 필요한 경우는 git으로 배포되지 않는다 —
  사용자가 share.streamlit.io → 앱 → Settings → Secrets에서 직접 수정해야 함을 안내.
- force push 금지. 히스토리 문제가 있으면 사용자와 상의.

## 수동 배포 (Claude 없이)

`C:\ANFILT_AI\deploy.ps1` 실행 — 동일한 절차를 스크립트로 수행.
