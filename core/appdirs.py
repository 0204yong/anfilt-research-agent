"""경로 결정 — 설치판의 세 영역을 한 곳에서만 정한다.

→ docs/22 설치판 아키텍처 2절.

    ① 프로그램      설치 폴더 (업데이트가 통째로 갈아치우는 곳)
    ② 사용자 데이터  %APPDATA%\\ANFILT\\ResearchAgent  (설정·라이선스·로그)
    ③ 볼트          사용자가 고른 폴더 + 그 안의 .research-agent/

②를 ① 바깥에 두는 것이 업데이트가 사용자 데이터를 건드리지 못하게 하는 장치이고,
③의 `.research-agent/` 를 볼트 **안**에 두는 것이 "볼트 폴더 하나만 복사하면
이력·감시까지 이전된다"를 만드는 장치다.

⚠️ `packaging/launcher.py` 에도 같은 규칙의 `data_dir()` 이 있다. 런처는 core 를
import 하지 않으므로(앱보다 먼저 뜬다) 의도적인 중복이다 — 한쪽을 바꾸면 다른
쪽도 바꿔야 한다.
"""
import os
from pathlib import Path

VENDOR = "ANFILT"
APP = "ResearchAgent"

AGENT_DIRNAME = ".research-agent"      # 볼트 안의 앱 전용 폴더 (Obsidian이 무시)
DEFAULT_VAULT_NAME = "리서치에이전트 지식볼트"


def data_dir() -> Path:
    """사용자 데이터 폴더. 없으면 만든다."""
    root = os.getenv("APPDATA")
    if not root:                       # 비 Windows 개발 환경 폴백
        root = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(root) / VENDOR / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.json"


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_vault_path() -> Path:
    """기본 볼트 위치 — 문서 폴더 아래. 사용자가 마법사에서 바꿀 수 있다."""
    docs = Path.home() / "Documents"
    if not docs.exists():              # OneDrive 리다이렉트 등으로 없을 수 있다
        docs = Path.home()
    return docs / DEFAULT_VAULT_NAME


def agent_dir(vault_path) -> Path:
    """볼트 안의 앱 전용 폴더. 없으면 만든다."""
    d = Path(vault_path) / AGENT_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path(vault_path) -> Path:
    return agent_dir(vault_path) / "agent.db"


def conflicts_dir(vault_path) -> Path:
    d = agent_dir(vault_path) / "conflicts"
    d.mkdir(parents=True, exist_ok=True)
    return d
