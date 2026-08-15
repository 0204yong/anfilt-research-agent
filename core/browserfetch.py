"""브라우저로 페이지 읽기 — 자바스크립트가 그리는 목록과 방화벽 뒤를 위해.

→ docs/15 자동 모니터링과 알림. Streamlit 비의존.

## 왜 필요한가

`requests.get` 은 **서버가 처음 내려 준 HTML** 만 본다. 요즘 관공서 포털은
껍데기만 내려 주고 목록은 자바스크립트가 나중에 그린다. 실측(2026-08-15,
ESG Finance Hub):

    requests 로 목록 페이지   → HTTP 200 · 그런데 **본문 547자** (메뉴뿐)
    requests 로 목록 API      → HTTP 403 (방화벽)
    헤드리스 Edge 로 같은 주소 → **112,490자** · 기사 제목이 그대로 들어 있다

헤더를 브라우저와 한 글자까지 맞춰도 API 는 403 이었다. 헤더가 아니라
**접속 방식(TLS 지문)** 을 보고 거르기 때문이다 — 헤더로는 못 넘는다.
넘는 방법은 하나뿐이다: **진짜 브라우저로 열기.**

## 왜 무겁지 않은가

Playwright 나 Selenium 을 들이면 설치본이 150MB 씩 늘어난다. 그럴 필요가
없다 — **이 프로그램은 이미 Edge 를 쓴다.** 런처가 앱 창을 Edge 의 `--app=`
모드로 띄운다(→ packaging/launcher.py). 같은 실행 파일에 `--dump-dom` 을
주면 다 그려진 DOM 을 표준출력으로 뱉는다. 새로 깔 것이 없다.

`--dump-dom` 은 CDP·웹소켓·드라이버가 필요 없다. 브라우저를 붙잡고
대화하는 대신 **한 번 열고 결과만 받는다** — 실패해도 프로세스가 죽을 뿐이다.

## 한계 (숨기지 말 것)

- **호스팅 체험판에는 브라우저가 없다.** `available()` 이 False 를 돌려주고
  감시는 예전처럼 requests 로 돈다. 이 파일은 정식판 전용 길이다.
- 한 장에 3~5초. requests 의 1~2초보다 느리다. 그래서 **감시마다 켜고 끈다.**
- 쪽 번호가 주소에 없는 목록은 여전히 첫 장만 본다. 그건 다른 문제다.
"""
import os
import subprocess
import tempfile
from pathlib import Path

from . import appdirs

# 다 그려질 때까지 브라우저 안 시계를 빨리 돌린다 (실시간 대기가 아니다)
VIRTUAL_TIME_MS = 15_000
TIMEOUT_SEC = 60
MIN_HTML = 500          # 이보다 짧으면 못 받은 것으로 본다

# requests 로 읽었는데 이만큼도 안 나오면 "자바스크립트로 그리는 쪽" 을 의심한다.
# ESG Finance Hub 의 껍데기가 547자였다 — 목록 한 장이면 최소 수천 자는 된다.
THIN_TEXT = 1_200
THIN_LINKS = 12


def _candidates() -> list:
    """설치본에 실제로 있는 브라우저들. 런처와 같은 목록이다.

    (packaging/launcher.py 와 겹치지만 그쪽은 설치본 루트에 홀로 놓이는
     파일이라 core 를 import 하지 않는다 — 겹침을 없애려면 런처가 앱 폴더에
     의존하게 되고, 그건 앱이 깨지면 창도 안 뜬다는 뜻이다.)
    """
    pf = os.getenv("ProgramFiles", r"C:\Program Files")
    pf86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.getenv("LOCALAPPDATA", "")
    return [
        Path(pf86) / "Microsoft/Edge/Application/msedge.exe",
        Path(pf) / "Microsoft/Edge/Application/msedge.exe",
        Path(pf) / "Google/Chrome/Application/chrome.exe",
        Path(pf86) / "Google/Chrome/Application/chrome.exe",
        Path(local) / "Google/Chrome/Application/chrome.exe",
    ]


def browser_path() -> str:
    for exe in _candidates():
        try:
            if exe.exists():
                return str(exe)
        except OSError:                              # 경로가 이상해도 죽지 않는다
            continue
    return ""


def available() -> bool:
    return bool(browser_path())


def _profile_dir() -> Path:
    """앱 창이 쓰는 프로필과 **다른** 자리를 쓴다.

    크로미움은 한 프로필을 두 프로세스가 잡는 것을 거부한다. 같은 자리를
    쓰면 앱 창이 떠 있는 동안 감시가 통째로 실패한다.
    """
    try:
        return appdirs.data_dir() / "fetch-profile"
    except Exception:                                # noqa: BLE001
        return Path(tempfile.gettempdir()) / "ra-fetch-profile"


def get_html(url: str, timeout: int = TIMEOUT_SEC) -> str:
    """다 그려진 DOM 을 돌려준다. 못 하면 RuntimeError.

    표준출력을 파이프로 받지 않고 **파일로 받는다.** 100KB 넘는 DOM 을
    파이프로 빨아들이다 창이 안 뜬 채로 서로 기다리는 일을 피한다.
    """
    exe = browser_path()
    if not exe:
        raise RuntimeError(
            "이 PC 에서 Edge·Chrome 을 찾지 못했습니다 — 브라우저로 읽기를 쓸 수 없습니다"
        )
    profile = _profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="ra-dom-")) / "page.html"
    args = [
        exe, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        "--disable-background-networking", "--mute-audio",
        f"--user-data-dir={profile}",
        f"--virtual-time-budget={VIRTUAL_TIME_MS}",
        "--dump-dom", url,
    ]
    try:
        with open(out, "wb") as fh:
            proc = subprocess.run(
                args, stdout=fh, stderr=subprocess.DEVNULL, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        html = out.read_text(encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"브라우저가 {timeout}초 안에 페이지를 못 열었습니다") from None
    finally:
        try:
            out.unlink(missing_ok=True)
            out.parent.rmdir()
        except OSError:
            pass

    # 종료 코드는 믿지 않는다 — 크로미움은 잡스러운 오류에도 0 이 아닌 값을
    # 뱉으면서 DOM 은 멀쩡히 찍어 준다. 받은 내용으로 판단한다.
    if len(html) < MIN_HTML:
        raise RuntimeError(
            f"브라우저가 빈 화면을 돌려줬습니다 (종료 코드 {proc.returncode}, {len(html)}자)"
        )
    return html


def looks_thin(text: str, links: list) -> bool:
    """requests 로 읽은 결과가 '껍데기' 로 보이는가 — 켜라고 권할 근거."""
    return len(str(text or "")) < THIN_TEXT and len(links or []) < THIN_LINKS
