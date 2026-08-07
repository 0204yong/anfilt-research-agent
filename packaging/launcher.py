r"""설치판 런처 — Streamlit 서버를 조용히 띄우고 창을 연다.

    <설치폴더>\runtime\pythonw.exe  <설치폴더>\launcher.py

사용자에게는 하나의 프로그램으로 보여야 한다 (→ docs/22 설치판 아키텍처 1절).
그래서 이 파일이 하는 일은 넷이다.

1. **단일 인스턴스** — 이미 떠 있으면 그 창만 다시 열고 끝낸다. 두 번 실행해
   서버가 둘 뜨면 같은 sqlite/볼트를 두 프로세스가 쓰게 된다.
2. **빈 포트 탐색** — 8501 고정은 충돌한다(다른 Streamlit 앱, 사내 툴).
3. **헤드리스 기동** — 콘솔 창 없이 띄우고 로그는 파일로 남긴다(지원 문의 대응).
4. **앱 모드 창** — Edge/Chrome의 `--app=` 을 쓰면 주소창 없는 창이 뜬다.
   pywebview 같은 추가 의존성 없이 네이티브 창에 가까운 모양이 된다.

Streamlit 비의존이어야 한다 — 이 파일은 서버를 띄우는 쪽이지 앱의 일부가 아니다.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / "runtime"
APP_DIR = BASE / "app"

APP_NAME = "ResearchAgent"
VENDOR = "ANFILT"

READY_TIMEOUT = 60          # 초 — 콜드스타트(임베디드 런타임 첫 import)를 넉넉히 본다
READY_POLL = 0.25


# ---------------------------------------------------------------- 경로

def data_dir() -> Path:
    """사용자 데이터 폴더 — 프로그램 폴더 **바깥**이라 업데이트가 건드리지 않는다."""
    root = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    d = Path(root) / VENDOR / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_file() -> Path:
    return data_dir() / "runtime_state.json"


def log_path() -> Path:
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "server.log"


# ---------------------------------------------------------------- 포트

def free_port(preferred: int = 8501) -> int:
    """preferred 부터 훑고, 다 막혀 있으면 OS가 주는 임의 포트를 쓴다."""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def health_ok(port: int, timeout: float = 1.0) -> bool:
    url = f"http://127.0.0.1:{port}/_stcore/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ---------------------------------------------------------------- 단일 인스턴스

def running_instance() -> int:
    """이미 떠 있으면 그 포트를, 아니면 0을 반환한다.

    pid 대신 **헬스 응답**으로 판정한다 — pid는 재사용되지만 우리 서버가
    실제로 응답하는지는 이 방법만 확실하다.
    """
    try:
        state = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    port = int(state.get("port") or 0)
    return port if port and health_ok(port) else 0


def write_state(port: int, pid: int) -> None:
    state_file().write_text(
        json.dumps({"port": port, "pid": pid, "started_at": time.time()}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- 창

def _browser_candidates() -> list:
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


def open_window(port: int) -> None:
    """주소창 없는 앱 모드 창을 연다. 없으면 기본 브라우저로 폴백."""
    url = f"http://127.0.0.1:{port}"
    profile = data_dir() / "browser-profile"
    for exe in _browser_candidates():
        if exe.exists():
            subprocess.Popen([
                str(exe), f"--app={url}",
                f"--user-data-dir={profile}",   # 사용자 기본 프로필과 섞이지 않게
                "--no-first-run", "--no-default-browser-check",
            ])
            return
    import webbrowser
    webbrowser.open(url)


# ---------------------------------------------------------------- 서버

def start_server(port: int) -> subprocess.Popen:
    python = RUNTIME / "python.exe"
    if not python.exists():
        raise SystemExit(f"런타임을 찾을 수 없습니다: {python}")
    main = APP_DIR / "app.py"
    if not main.exists():
        raise SystemExit(f"앱을 찾을 수 없습니다: {main}")

    cmd = [
        str(python), "-m", "streamlit", "run", str(main),
        "--server.port", str(port),
        "--server.address", "127.0.0.1",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--global.developmentMode", "false",
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"       # 한글 로그가 깨지지 않게
    env["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"   # 배포판은 핫리로드 불필요

    # CREATE_NO_WINDOW — 콘솔 창을 띄우지 않는다. 로그는 파일로 받는다.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    logf = open(log_path(), "a", encoding="utf-8", buffering=1)
    logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 기동 (port {port}) =====\n")
    return subprocess.Popen(
        cmd, cwd=str(APP_DIR), env=env,
        stdout=logf, stderr=subprocess.STDOUT,
        creationflags=flags,
    )


def wait_ready(proc: subprocess.Popen, port: int) -> bool:
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return False                    # 서버가 먼저 죽었다 — 로그를 봐야 한다
        if health_ok(port):
            return True
        time.sleep(READY_POLL)
    return False


def main() -> int:
    existing = running_instance()
    if existing:
        open_window(existing)               # 두 번째 실행 = "창 다시 열기"
        return 0

    port = free_port()
    proc = start_server(port)
    if not wait_ready(proc, port):
        try:
            proc.terminate()
        except OSError:
            pass
        tail = ""
        try:
            tail = log_path().read_text(encoding="utf-8", errors="replace")[-1500:]
        except OSError:
            pass
        sys.stderr.write("서버 기동 실패\n" + tail)
        return 1

    write_state(port, proc.pid)
    open_window(port)
    proc.wait()                             # 창을 닫아도 서버는 여기서 유지된다
    try:
        state_file().unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
