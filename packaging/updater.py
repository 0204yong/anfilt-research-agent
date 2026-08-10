r"""교체 프로세스 — 앱을 멈추고 `app\` 을 갈아 끼운 뒤 되살린다.

    <설치폴더>\runtime\pythonw.exe  <설치폴더>\updater.py --payload <zip> --kind delta

→ docs/19 두 제품 구성과 자동 업데이트 4.5절 · docs/23 6단계.

**왜 별도 프로세스인가**: 돌고 있는 앱이 자기 소스를 갈아치우면 import 된 모듈과
디스크가 어긋난다. 그래서 앱은 여기까지 데려다 주고 물러난다.

**이 파일은 표준 라이브러리만 쓴다.** `app\` 이 지금 막 갈리는 중이라 거기서
아무것도 import 할 수 없다. 런처와 겹치는 함수가 몇 개 있는 건 그래서다.

## 순서 — 어느 단계에서 죽어도 되살아나게

    1. zip 검사 (전개 전에 통째로 훑는다 — 반쯤 풀린 앱을 만들지 않는다)
    2. 앱 종료
    3. app\  →  app.bak\            ← 롤백 지점
    4. 새 app\ 전개
    5. 재시작 → 자가진단(헬스)
    6. 통과하면 app.bak\ 삭제, 실패하면 app.bak\ 을 되돌리고 이전 버전으로 재시작

3~4 사이에서 전원이 나가도 다음 실행 때 `app\` 이 없고 `app.bak\` 이 있으므로
런처가 그것을 보고 되돌린다 (`launcher.recover_from_interrupted_update`).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

READY_TIMEOUT = 90
VENDOR, APP_NAME = "ANFILT", "ResearchAgent"


# ---------------------------------------------------------------- 공통


def data_dir() -> Path:
    root = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    d = Path(root) / VENDOR / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        logs = data_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "update.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line)


def state_file() -> Path:
    return data_dir() / "runtime_state.json"


def read_state() -> dict:
    try:
        return json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def health_ok(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/_stcore/health", timeout=timeout
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def stop_app() -> int:
    """돌고 있는 앱을 멈춘다. 반환: 멈춘 포트(없으면 0).

    **우리 서버인지 헬스로 확인한 뒤** 죽인다 — pid 는 재사용되므로
    상태 파일만 믿으면 남의 프로세스를 죽인다 (5단계와 같은 규칙).
    """
    state = read_state()
    port, pid = int(state.get("port") or 0), int(state.get("pid") or 0)
    log(f"멈출 대상: pid={pid} port={port} health={health_ok(port) if port else '-'}")
    if not (port and pid):
        log("실행 중인 앱을 찾지 못했습니다")
        return 0

    # ⚠️ `/T` 를 쓰면 안 된다. 이 프로세스는 앱이 띄운 **자식**이라
    # 프로세스 트리를 죽이면 자기 자신까지 죽는다 — 실측에서 업데이트가
    # "압축 검사 통과" 직후 조용히 멈췄던 원인이다.
    #
    # 그리고 **헬스가 아니라 프로세스가 사라졌는지로 판정한다.** 서버의 작업
    # 디렉터리가 `app\` 이라, 살아 있는 한 폴더 이름을 바꿀 수 없다.
    # 헬스는 죽었는데 프로세스가 남아 있는 순간이 실제로 있었다 (실측).
    for attempt in range(3):
        if not pid_alive(pid):
            break
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"], capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log(f"taskkill({attempt}) rc={r.returncode} "
            f"{(r.stdout or b'').decode('utf-8', 'replace').strip()[:120]}")
        for _ in range(40):
            if not pid_alive(pid):
                break
            time.sleep(0.25)

    log(f"종료 확인: pid {pid} " + ("아직 살아 있음" if pid_alive(pid) else "사라짐"))
    return port


def pid_alive(pid: int) -> bool:
    """pid 가 아직 있는가. `tasklist` 는 느리지만 의존성이 없다."""
    if not pid:
        return False
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return str(pid) in (r.stdout or b"").decode("utf-8", "replace")


def rename_with_retry(src: Path, dst: Path, timeout: float = 30.0) -> None:
    """폴더 이름을 바꾼다. 잠겨 있으면 풀릴 때까지 다시 시도한다.

    **서버의 작업 디렉터리가 바로 `app\\` 다** (`.streamlit\\config.toml` 을 거기서
    찾기 때문에 옮길 수 없다). Windows 는 프로세스가 쓰는 폴더의 이름을 못 바꾸고,
    종료 신호를 보낸 뒤 핸들이 실제로 풀리기까지 잠깐 걸린다 — 헬스가 죽었다고
    바로 이름을 바꾸면 `WinError 32` 가 난다 (실측으로 잡았다).

    백신·탐색기가 잠깐 쥐고 있는 경우도 같은 방법으로 넘어간다.
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            last = e
            time.sleep(0.4)
    raise last if last else OSError(f"이름을 바꾸지 못했습니다: {src}")


def start_app(install_dir: Path) -> None:
    subprocess.Popen(
        [str(install_dir / "runtime" / "pythonw.exe"),
         str(install_dir / "launcher.py")],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0),
    )


def wait_healthy(timeout: int = READY_TIMEOUT) -> bool:
    """자가진단 — 새 앱이 실제로 응답하는지 본다. 이게 롤백의 판단 근거다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = read_state()
        port = int(state.get("port") or 0)
        if port and health_ok(port):
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- zip


def safe_members(zf: zipfile.ZipFile) -> list:
    """전개 **전에** 통째로 검사한다. 반쯤 풀린 앱을 만들지 않기 위해서다.

    `..` 나 절대경로가 든 zip 은 폴더 밖에 파일을 쓴다 (zip-slip).
    1단계 볼트 가져오기에서 막은 것과 같은 공격이다.
    """
    members = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.endswith("/"):
            continue
        parts = Path(name).parts
        if not parts or ".." in parts or name.startswith("/") or ":" in parts[0]:
            raise ValueError(f"허용되지 않는 경로가 들어 있습니다: {info.filename}")
        members.append(info)
    if not members:
        raise ValueError("빈 압축 파일입니다.")
    if not any(m.filename.replace("\\", "/").endswith("app.py") for m in members):
        # app.py 가 없으면 앱이 아니다 — 엉뚱한 zip 으로 앱을 지우지 않는다
        raise ValueError("앱 압축 파일이 아닙니다 (app.py 가 없습니다).")
    return members


def extract(payload: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(payload) as zf:
        members = safe_members(zf)
        for info in members:
            zf.extract(info, target)


# ---------------------------------------------------------------- 델타 적용


def apply_delta(install_dir: Path, payload: Path) -> int:
    app = install_dir / "app"
    bak = install_dir / "app.bak"

    with zipfile.ZipFile(payload) as zf:      # ① 먼저 검사
        safe_members(zf)
    log(f"압축 검사 통과: {payload.name}")

    stop_app()                                 # ②
    log("앱을 멈췄습니다")

    if bak.exists():
        shutil.rmtree(bak, ignore_errors=True)
    if app.exists():
        rename_with_retry(app, bak)            # ③ 롤백 지점
        log("app -> app.bak")

    try:
        extract(payload, app)                  # ④
        log("새 앱 전개 완료")
    except Exception as e:                     # noqa: BLE001
        log(f"전개 실패: {e} — 되돌립니다")
        rollback(install_dir)
        return 2

    start_app(install_dir)                     # ⑤
    if not wait_healthy():
        log("자가진단 실패 — 되돌립니다")
        rollback(install_dir)
        return 3

    shutil.rmtree(bak, ignore_errors=True)     # ⑥
    log("업데이트 완료")
    return 0


def rollback(install_dir: Path) -> None:
    app, bak = install_dir / "app", install_dir / "app.bak"
    stop_app()
    if bak.exists():
        if app.exists():
            shutil.rmtree(app, ignore_errors=True)
        try:
            rename_with_retry(bak, app)
            log("app.bak -> app (롤백)")
        except OSError as e:
            log(f"롤백 실패: {e}")
    start_app(install_dir)


def apply_installer(payload: Path) -> int:
    """전체 인스톨러 — 무인 설치에 넘기고 우리는 빠진다.

    per-user 설치(`PrivilegesRequired=lowest`)라 UAC 창이 뜨지 않는다
    (→ docs/22 2절). 인스톨러가 자기 방식으로 앱을 멈추고 갈아 끼운다.
    """
    stop_app()
    subprocess.Popen([str(payload), "/VERYSILENT", "/NORESTART", "/CURRENTUSER"])
    log("전체 설치 파일을 실행했습니다")
    return 0


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", required=True)
    ap.add_argument("--kind", default="delta", choices=["delta", "installer"])
    ap.add_argument("--install-dir", required=True)
    args = ap.parse_args(argv)

    payload = Path(args.payload)
    install_dir = Path(args.install_dir)

    # ⚠️ **자기 작업 디렉터리에서 나온다.** 이 프로세스는 앱이 띄웠고, 앱 서버의
    # 작업 디렉터리는 `app\` 이다(거기서 `.streamlit\config.toml` 을 찾는다).
    # 그대로 두면 우리가 이름을 바꾸려는 폴더를 우리가 붙잡고 있어
    # `WinError 32` 가 난다 — 프로세스를 다 죽여도 안 풀리던 원인이다(실측).
    try:
        os.chdir(data_dir())
    except OSError:
        pass

    log(f"업데이트 시작 — kind={args.kind} payload={payload.name} cwd={os.getcwd()}")

    if not payload.exists():
        log("받은 파일이 없습니다")
        return 1
    try:
        if args.kind == "installer":
            return apply_installer(payload)
        return apply_delta(install_dir, payload)
    except Exception as e:                     # noqa: BLE001
        log(f"예기치 못한 오류: {e} — 되돌립니다")
        try:
            rollback(install_dir)
        except Exception:                      # noqa: BLE001
            pass
        return 4
    finally:
        try:
            payload.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
