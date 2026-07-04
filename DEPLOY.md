# 배포 가이드 — Research Agent를 회사 홈페이지에 붙이기

Research Agent는 **파이썬(Streamlit) 서버 앱**이라 Netlify(정적 호스트)에서 직접 못 돌립니다.
그래서 구조는 이렇게 나뉩니다:

```
[앱]  Streamlit Community Cloud (무료·파이썬 실행)  →  https://…streamlit.app
                                                          │  (내부용 링크)
[홈페이지]  anfilt-homepage.netlify.app  ──"ANFILT Product ▸ Research Agent"──┘
```

> 접근 정책: **내부용**. 앱은 초대한 이메일만 열 수 있도록 잠그고,
> 실행 링크는 공개 페이지에 노출하지 않습니다.

---

## A. 앱 배포 — Streamlit Community Cloud (약 5분, 무료)

### A-1. 준비 (완료됨)
- 이 폴더는 GitHub **비공개** 저장소로 푸시되어 있습니다:
  `https://github.com/0204yong/anfilt-research-agent` (branch `main`).
- `.env`(실제 키)는 `.gitignore`로 **커밋에서 제외**되어 유출되지 않습니다.

### A-2. 배포
1. <https://share.streamlit.io> 접속 → GitHub 계정(**0204yong**)으로 로그인.
2. **Create app → Deploy a public app from GitHub** (뒤에서 비공개로 잠급니다).
3. 저장소 `anfilt-research-agent` 선택 → Branch `main` → **Main file path: `app.py`**.
4. **Advanced settings**:
   - **Python version: 3.12** (또는 3.13). ← 로컬의 3.14는 클라우드 미지원일 수 있어 지정 권장.
   - **Secrets** 칸에 아래를 붙여넣기 (보유한 키만, 최소 1개):
     ```toml
     GOOGLE_API_KEY = "실제_Gemini_키"
     ANTHROPIC_API_KEY = "실제_Claude_키"   # 크레딧 충전 후
     # OPENAI_API_KEY = "실제_GPT_키"
     ```
5. **Deploy** 클릭 → 몇 분 후 `https://<이름>.streamlit.app` 주소가 생성됩니다.

### A-3. 내부용으로 잠그기 (중요)
- 앱 화면 우하단 **Manage app → Settings → Sharing**
- **"Who can view this app"** 을 **Invite only** 로 바꾸고, 접근을 허용할
  이메일(대표님 + 지정 인원)을 추가합니다.
- 이제 링크를 알아도 초대받지 않은 사람은 열 수 없습니다.

> 이렇게 얻은 `https://<이름>.streamlit.app` 주소가 **내부 실행 링크**입니다.

---

## B. 홈페이지에 제품 등록 (이미 코드 반영됨)

`concept-c.html`(= 배포본 `dist/index.html`)에 이미 추가되어 있습니다:
- 내비게이션에 **제품(ANFILT Product)** 메뉴
- **Research Agent** 제품 소개 섹션 (`#product`)

### B-1. 실행 링크 연결
제품 섹션 스크립트 상단의 한 줄만 바꾸면 됩니다. `concept-c.html`에서
`APP_URL` 을 찾아 A-3에서 얻은 주소를 넣으세요:

```js
var APP_URL = "";   // ← 여기에 https://<이름>.streamlit.app 붙여넣기
```

- **비워두면**: 버튼이 "도입 문의"로 표시되고 상담 폼(#contact)으로 연결됩니다(공개 안전).
- **채우면**: 버튼이 "Research Agent 실행 ↗"으로 바뀌어 내부 링크로 연결됩니다.

> 홈페이지 코드의 기존 철학(키 채우면 동작, 비우면 안전 폴백)과 동일한 방식입니다.

### B-2. 재배포 (Netlify)
정적 사이트라 빌드가 없습니다. 둘 중 하나:

**방법 1 — 드래그앤드롭 (가장 쉬움)**
1. <https://app.netlify.com> → `anfilt-homepage` 사이트 → **Deploys** 탭.
2. `dist/` 폴더(루트에 `index.html` + `assets/`)를 통째로 드래그해 업로드.

**방법 2 — 스크립트 (`deploy.js`)**
```bash
cd "C:\ANFILT_AI\Company_Homepage"
# dist 폴더를 dist.zip 으로 압축한 뒤:
NF_TOKEN=<네틀리파이_토큰> NF_SITE_ID=<사이트_ID> node deploy.js
```
토큰은 Netlify → User settings → Applications → Personal access tokens 에서 발급.

---

## C. 로컬 실행 (배포 없이 바로 쓰기)

키가 든 `.env`가 이미 있으니 로컬에서는 그대로 실행됩니다:
```bash
cd "C:\ANFILT_AI\Research Agent"
pip install -r requirements.txt
streamlit run app.py
```

Google Slides 디자인 엔진(서비스계정 파일 경로 필요)은 로컬에서만 권장합니다.

---

## 보안 체크리스트
- [ ] `.env` 가 GitHub에 올라가지 않았는지 (`git status`에 안 보여야 함).
- [ ] Streamlit 앱을 **Invite only** 로 잠갔는지.
- [ ] 공개 홈페이지에 실제 앱 URL을 노출할지 여부 결정 (내부용이면 `APP_URL` 비워두고 상담 폼으로).
- [ ] 채팅/문서에 노출됐던 키는 필요 시 재발급 (Gemini·Claude).
