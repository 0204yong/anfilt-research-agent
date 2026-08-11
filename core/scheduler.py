r"""Windows 작업 스케줄러 등록 — 앱이 꺼져 있어도 모니터링이 돈다.

→ docs/15 자동 모니터링과 알림 · docs/23 설치판 구현 계획 8단계. Streamlit 비의존.

체험판은 GitHub Actions 가 매시 깨워 준다. **정식판에는 그 서버가 없다** —
대신 Windows 작업 스케줄러가 매시 `watch_run.py` 를 부른다.

## 왜 두 도구를 섞어 쓰나

  · **등록·삭제·즉시 실행** → `schtasks` (인스톨러가 쓰는 것과 같은 명령)
  · **상태 조회**           → PowerShell `Get-ScheduledTaskInfo`

`schtasks /Query` 의 출력은 **한국어 Windows 에서 한국어로 번역된다.**
필드 이름을 문자열로 맞추면 언어 설정 하나로 깨진다. PowerShell 쪽은
속성 이름이 영어로 고정돼 있어 로케일을 타지 않는다.
"""
import json
import subprocess
import sys
from pathlib import Path

from . import edition

TASK_NAME = "ANFILT 리서치에이전트 모니터링"     # installer.iss 와 같아야 한다
_FLAGS = 0


def _flags() -> int:
    global _FLAGS
    if not _FLAGS:
        _FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return _FLAGS


def available() -> bool:
    """이 환경에서 스케줄 등록이 가능한가 (설치판 · Windows)."""
    return sys.platform == "win32" and edition.install_dir() is not None


def task_command() -> list:
    """스케줄러가 부를 명령. 설치판이 아니면 None."""
    base = edition.install_dir()
    if not base:
        return None
    pythonw = Path(base) / "runtime" / "pythonw.exe"
    script = Path(base) / "app" / "watch_run.py"
    if not (pythonw.exists() and script.exists()):
        return None
    return [str(pythonw), str(script)]


def _run(args: list, timeout: float = 30.0):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=_flags())
    except (OSError, subprocess.SubprocessError):
        return None


def _decode(raw: bytes) -> str:
    """콘솔 출력은 한국어 Windows 에서 cp949 다. utf-8 로 읽으면 깨진다."""
    for enc in ("utf-8", "cp949", "mbcs"):
        try:
            return (raw or b"").decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return (raw or b"").decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 등록


def register(hourly: bool = True) -> tuple:
    """작업을 등록한다(이미 있으면 덮어쓴다). 반환 (성공, 메시지).

    매시 실행이지만 **매시 조사가 돌지는 않는다** — `watch.is_due` 가 걸러서,
    예정 시각이 지났는데 아직 안 돈 감시만 실행한다.
    """
    cmd = task_command()
    if not cmd:
        return False, "설치판에서만 등록할 수 있습니다."
    tr = f'"{cmd[0]}" "{cmd[1]}"'
    r = _run(["schtasks", "/Create", "/F", "/TN", TASK_NAME,
              "/SC", "HOURLY" if hourly else "DAILY", "/MO", "1", "/TR", tr])
    if r is None:
        return False, "작업 스케줄러를 실행하지 못했습니다."
    if r.returncode != 0:
        return False, _decode(r.stderr or r.stdout).strip()[:200] or "등록 실패"
    return True, "등록했습니다 — 매시 확인하고, 예정 시각이 지난 감시만 실행합니다."


def unregister() -> tuple:
    r = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    if r is None:
        return False, "작업 스케줄러를 실행하지 못했습니다."
    if r.returncode != 0:
        return False, _decode(r.stderr or r.stdout).strip()[:200] or "해제 실패"
    return True, "해제했습니다."


def run_now() -> tuple:
    r = _run(["schtasks", "/Run", "/TN", TASK_NAME])
    if r is None or r.returncode != 0:
        return False, "지금 실행하지 못했습니다 — 작업이 등록되어 있는지 확인해 주세요."
    return True, "실행을 시작했습니다 — 결과는 잠시 뒤 아래 목록에 반영됩니다."


# ---------------------------------------------------------------- 상태


def registered() -> bool:
    """등록 여부만. 종료 코드로 보므로 언어 설정을 타지 않는다."""
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME], timeout=15)
    return bool(r and r.returncode == 0)


_PS = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$t = Get-ScheduledTask -TaskName $env:RA_TASK -ErrorAction SilentlyContinue;"
    "if (-not $t) { '{}' | Write-Output; exit }"
    "$i = $t | Get-ScheduledTaskInfo;"
    "@{state=[string]$t.State; last=[string]$i.LastRunTime;"
    " next=[string]$i.NextRunTime; result=[int]$i.LastTaskResult}"
    " | ConvertTo-Json -Compress"
)


def status() -> dict:
    """{"registered","state","last_run","next_run","last_result"}.

    속성 이름이 영어로 고정된 PowerShell 쪽을 쓴다 — `schtasks /Query` 의
    한국어 필드명을 문자열로 맞추면 언어 설정 하나로 깨진다.
    """
    out = {"registered": False, "state": "", "last_run": "", "next_run": "",
           "last_result": None}
    if not available():
        return out
    import os
    env = dict(os.environ, RA_TASK=TASK_NAME)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
            capture_output=True, timeout=25, creationflags=_flags(), env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return out
    try:
        data = json.loads(_decode(r.stdout).strip() or "{}")
    except ValueError:
        return out
    if not data:
        return out
    out.update(registered=True, state=str(data.get("state") or ""),
               last_run=str(data.get("last") or ""),
               next_run=str(data.get("next") or ""),
               last_result=data.get("result"))
    return out
