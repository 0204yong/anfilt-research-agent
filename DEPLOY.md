# 배포 가이드 — ANFILT 원커맨드 자동 배포

> **2026-07-04부터 배포는 전부 자동입니다.** 수정 후 Claude에게 **"배포해줘"**(또는 `/deploy`)
> 라고 하면 끝. 드래그앤드롭·수동 업로드는 더 이상 필요 없습니다.

## 아키텍처

```
홈페이지  C:\ANFILT_AI\Company_Homepage ──git push──▶ GitHub ──▶ Netlify 자동 빌드·배포
                                                                  anfilt-homepage.netlify.app
앱        C:\ANFILT_AI\Research Agent   ──git push──▶ GitHub ──▶ Streamlit Cloud 자동 재배포
                                                                  anfilt-research-agent-….streamlit.app
```

| 항목 | 홈페이지 | 앱 |
|---|---|---|
| 저장소 | github.com/0204yong/anfilt-homepage (비공개) | github.com/0204yong/anfilt-research-agent (공개) |
| 호스팅 | Netlify (GitHub 연동, netlify.toml) | Streamlit Community Cloud (Python 3.14) |
| 소스의 진실 | `concept-c.html` 한 파일 | `app.py` + `core/` |
| 접근 | 공개 | **Invite only** (0204yongko@gmail.com) |
| 반영 시간 | push 후 ~1분 | push 후 ~1-3분 |

- 홈페이지의 `dist/`는 이제 git에 없습니다 — Netlify가 빌드 때 `concept-c.html → dist/index.html`
  복사를 직접 수행합니다 (`netlify.toml` 참고). **수정은 항상 `concept-c.html`에.**
- 배포 절차의 상세(시크릿 사전검사 포함)는 `.claude/skills/deploy/SKILL.md`.

## 배포 방법

**방법 1 — Claude에게 (권장)**: 수정 요청 후 "배포해줘". Claude가 시크릿 검사 → 커밋 →
푸시 → 라이브 검증까지 수행하고 결과를 보고합니다.

**방법 2 — 수동 스크립트**: PowerShell에서
```powershell
C:\ANFILT_AI\deploy.ps1 -m "변경 요약"
```

## git으로 배포되지 않는 것 (별도 관리)

| 항목 | 위치 |
|---|---|
| 앱 API 키 (Streamlit Secrets) | share.streamlit.io → 앱 ⋮ → Settings → Secrets |
| 앱 열람 권한 (Invite only 이메일) | share.streamlit.io → 앱 ⋮ → Settings → Sharing |
| 로컬 실행용 키 | `C:\ANFILT_AI\Research Agent\.env` (git 제외) |

## 로컬 실행 (배포 없이)

```bash
cd "C:\ANFILT_AI\Research Agent"
streamlit run app.py
```

## 문제가 생기면

- 홈페이지 빌드 실패: https://app.netlify.com/projects/anfilt-homepage/deploys 에서 로그 확인
- 앱 빌드 실패: share.streamlit.io → 앱 → Manage app 로그 확인
- 설계 배경·이력: `docs/`(옵시디언 볼트)의 "10 배포와 홈페이지 연동", "11 원커맨드 배포"
