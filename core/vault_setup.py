r"""볼트 만들기·가져오기·Obsidian 연동 — 최초 실행 마법사의 실무.

→ docs/23 설치판 구현 계획 4단계 · docs/22 설치판 아키텍처 3절.

Streamlit 비의존 (→ docs/02 파이프라인 설계 원칙). 화면은 `ui_vault.py` 가 그린다.

이 모듈이 하는 판단은 넷이다.

  1. **고른 폴더가 볼트가 되어도 되는가** (`inspect`) — 이미 Obsidian 볼트인가,
     남의 볼트 안인가, 프로그램 폴더 안인가, 경로가 너무 긴가.
  2. **`.obsidian/` 프리셋을 써도 되는가** — 사용자가 이미 쓰던 볼트 설정을
     덮어쓰면 안 된다. 새 볼트일 때만 쓴다.
  3. **zip 을 어떻게 푸는가** — 노트는 폴더로, `*.run.json` 은 sqlite 로.
  4. **Obsidian 이 깔려 있는가** — 없으면 링크 대신 다운로드 안내를 내야 한다.
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from . import appdirs

OBSIDIAN_DIRNAME = ".obsidian"
OBSIDIAN_DOWNLOAD_URL = "https://obsidian.md/download"

# Windows MAX_PATH(260) 여유 계산용. 볼트 안에서 가장 긴 상대경로는 충돌본이다 —
# `.research-agent/conflicts/<타임스탬프> <경로를 _ 로 바꾼 이름>` (→ store_local).
# 실측 최장이 90자 안팎이라 120자를 잡아 둔다 (→ docs/23 0단계 함정 1번).
RESERVED_REL_LEN = 120
MAX_VAULT_PATH_LEN = 259 - 1 - RESERVED_REL_LEN

# 볼트를 두면 안 되는 곳 · 두면 주의해야 하는 곳
_CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud",
                  "네이버클라우드", "nextcloud")


# ------------------------------------------------------------------ 진단


def inspect(path) -> dict:
    """고른 폴더의 상태를 본다. 화면은 이 결과만 보고 안내를 고른다.

    state
      ``new``          아직 없는 폴더 (만들면 된다)
      ``empty``        있지만 노트가 없다
      ``nonempty``     노트가 있지만 Obsidian 볼트는 아니다
      ``vault_root``   이미 Obsidian 볼트의 루트다 (`.obsidian/` 이 있다)
      ``inside_vault`` 남의 Obsidian 볼트 **안쪽** 폴더다

    `blocking` 이 있으면 만들기를 막고, `warnings` 는 알리되 진행은 허용한다.
    """
    p = _norm(path)
    if p is None:
        return {"path": Path(str(path)), "state": "invalid", "notes": 0,
                "vault_root": None, "blocking": ["경로를 해석할 수 없습니다."],
                "warnings": []}

    info = {"path": p, "vault_root": None, "notes": 0, "blocking": [], "warnings": []}

    # --- 상태
    if not p.exists():
        info["state"] = "new"
    elif not p.is_dir():
        info["state"] = "invalid"
        info["blocking"].append("폴더가 아니라 파일입니다.")
        return info
    else:
        info["notes"] = _count_notes(p)
        if (p / OBSIDIAN_DIRNAME).is_dir():
            info["state"] = "vault_root"
            info["vault_root"] = p
        else:
            info["state"] = "nonempty" if info["notes"] else "empty"

    # 조상에 .obsidian 이 있으면 남의 볼트 안이다 (DoD: 기존 볼트의 하위 폴더 설치)
    for parent in p.parents:
        if (parent / OBSIDIAN_DIRNAME).is_dir():
            info["vault_root"] = parent
            if info["state"] in ("new", "empty", "nonempty"):
                info["state"] = "inside_vault"
            break

    # --- 막는 것
    if len(str(p)) > MAX_VAULT_PATH_LEN:
        info["blocking"].append(
            f"경로가 너무 깁니다({len(str(p))}자). {MAX_VAULT_PATH_LEN}자 이하의 "
            "더 짧은 위치를 골라 주세요 — Windows 경로 길이 제한(260자) 때문에 "
            "긴 파일명이 저장되지 않습니다."
        )
    # 검사는 **실경로로도** 한다. 링크(junction)나 subst 로 설치 폴더 안을
    # 가리키게 만들면, 적은 경로만 봐서는 걸러 낼 수 없다.
    real = _real(p)
    install = _install_dir()
    if install and (_is_within(p, install) or _is_within(real, install)):
        info["blocking"].append(
            "프로그램 설치 폴더 안입니다 — **업데이트할 때 통째로 지워집니다.** "
            "문서 폴더처럼 프로그램 바깥의 위치를 골라 주세요."
        )
    if _is_within(p, appdirs.data_dir()) or _is_within(real, appdirs.data_dir()):
        info["blocking"].append("프로그램 설정 폴더 안입니다 — 다른 위치를 골라 주세요.")
    if not _writable(p):
        info["blocking"].append("이 위치에 쓸 권한이 없습니다.")

    # --- 알리는 것
    low = str(p).lower()
    if any(m in low for m in _CLOUD_MARKERS):
        info["warnings"].append(
            "클라우드 동기화 폴더로 보입니다. 노트 동기화에는 좋지만, **두 대 이상의 PC에서 "
            "동시에 열면 조사 이력 DB가 깨질 수 있습니다** — 한 번에 한 PC에서만 쓰세요 "
            "(→ 설계서 21)."
        )
    if info["state"] == "vault_root":
        info["warnings"].append(
            "이미 Obsidian 볼트입니다. 기존 노트는 그대로 두고 "
            "`entities/` · `runs/` 폴더만 더합니다. **Obsidian 설정은 건드리지 않습니다.**"
        )
    elif info["state"] == "inside_vault":
        info["warnings"].append(
            f"기존 Obsidian 볼트(`{info['vault_root']}`) 안쪽 폴더입니다. "
            "노트는 그 볼트에서 바로 보이며, Obsidian 설정은 건드리지 않습니다."
        )
    elif info["state"] == "nonempty":
        info["warnings"].append(
            f"이미 마크다운 {info['notes']}개가 있는 폴더입니다. 기존 파일은 지우지 않고 "
            "볼트 폴더를 더합니다."
        )
    return info


def _count_notes(root: Path, limit: int = 500) -> int:
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        n += sum(1 for f in filenames if f.endswith(".md"))
        if n >= limit:
            break
    return n


def _install_dir():
    """설치 폴더(`version.json` 이 있는 곳). 개발 상태면 None."""
    from . import edition
    marker = edition._VERSION_JSON
    return marker.parent if marker.exists() else None


def _norm(path):
    """→ `appdirs.norm_path`. 볼트 경로 규칙은 거기 한 곳에만 있다."""
    return appdirs.norm_path(path)


def _real(p: Path) -> Path:
    """안전 검사용 실경로. 못 풀면 원래 경로를 그대로 돌려준다."""
    try:
        return p.resolve()
    except OSError:
        return p


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def _writable(p: Path) -> bool:
    """실제로 만들어 볼 수 있는 가장 가까운 조상에 쓰기를 시도한다."""
    probe = p
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if not probe.exists():
        return False
    try:
        t = probe / ".ra-write-test"
        t.write_text("", encoding="utf-8")
        t.unlink()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ 만들기


def create_vault(path, name: str, now_iso: str, with_preset: bool = True) -> dict:
    """폴더를 만들고, 시드 노트를 넣고, 설정에 등록한다.

    시드는 `vault_sync.ensure_vault_seeded` 를 그대로 쓴다 — 1단계에서 검증한
    경로를 마법사가 다시 구현하지 않는다.

    **시드가 실패해도 볼트는 만들어진다.** 시드 노트를 읽으려면 프롬프트 팩이
    필요한데(관계 술어 어휘), 팩이 없다고 볼트조차 못 만들면 사용자는 자기 데이터를
    둘 곳을 잃는다 — 설계서 22 8절이 금지하는 상태다. 실측에서 걸린 실제 경로다.

    반환: {"path", "seeded", "preset", "notes", "seed_error"}
    """
    from . import settings, store as store_mod, vault_sync

    # `inspect` 와 **같은 정규화**를 쓴다 — 진단이 보여 준 경로와 실제로
    # 만드는 경로가 달라지면, 고객은 D: 를 골랐는데 볼트는 C: 에 생긴다.
    p = _norm(path)
    if p is None:
        raise ValueError(f"경로를 해석할 수 없습니다: {path}")
    p.mkdir(parents=True, exist_ok=True)

    preset = write_obsidian_preset(p) if with_preset else False

    settings.add_vault(name or p.name, p)
    store_mod.reset_cache()
    store = store_mod.local_store(p)

    seeded, seed_error = False, None
    try:
        seeded = vault_sync.ensure_vault_seeded(store, now_iso)
    except Exception as e:                          # noqa: BLE001
        seed_error = str(e)

    # Obsidian 목록에 더해 둔다 — 이게 없으면 `obsidian://` 링크가 오류를 낸다
    registered = register_with_obsidian(p)

    return {"path": p, "seeded": seeded, "preset": preset, "registered": registered,
            "seed_error": seed_error, "notes": len(store.vault_list())}


def write_obsidian_preset(vault_root, force: bool = False) -> bool:
    """`.obsidian/` 프리셋을 쓴다 — 그래프 뷰 · 태그 패널이 처음부터 보이도록.

    **이미 `.obsidian/` 이 있으면 아무것도 하지 않는다** (`force` 예외). 사용자가
    쓰던 볼트의 설정·플러그인·작업공간을 덮어쓰는 건 되돌릴 수 없는 손해다.

    형식은 Obsidian 1.5+ 가 쓰는 모양(`core-plugins.json` 은 객체)에 맞췄다.
    틀려도 Obsidian 이 무시하고 기본값으로 되돌릴 뿐이라 치명적이지 않다.
    """
    root = Path(vault_root)
    conf = root / OBSIDIAN_DIRNAME
    if conf.exists() and not force:
        return False
    conf.mkdir(parents=True, exist_ok=True)
    for fname, data in _preset_files().items():
        (conf / fname).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return True


def _preset_files() -> dict:
    return {
        # 노트 링크를 짧은 이름으로 — 시드 노트가 [[EU CBAM]] 형태를 쓴다
        "app.json": {
            "alwaysUpdateLinks": True,
            "newLinkFormat": "shortest",
            "useMarkdownLinks": False,
            "attachmentFolderPath": "attachments",
        },
        "core-plugins.json": {
            "file-explorer": True,
            "global-search": True,
            "switcher": True,
            "graph": True,          # 엔티티 관계를 그림으로 (→ docs/13)
            "backlink": True,
            "outgoing-link": True,
            "tag-pane": True,       # 태그 패널 — 4단계 DoD
            "properties": True,     # 프론트매터(entity_type·aliases·updated)
            "page-preview": True,
            "outline": True,
            "command-palette": True,
            "editor-status": True,
            "word-count": True,
            "file-recovery": True,
            "canvas": False,
            "daily-notes": False,
            "templates": False,
            "bookmarks": False,
            "slides": False,
            "audio-recorder": False,
            "publish": False,
            "sync": False,
        },
        "graph.json": {
            "collapse-filter": False,
            "search": "",
            "showTags": True,
            "showAttachments": False,
            "hideUnresolved": False,
            "showOrphans": True,
            "collapse-color-groups": False,
            # 엔티티 종류별 색 — 볼트를 열자마자 구조가 눈에 들어오게
            "colorGroups": [
                {"query": "tag:#entity/규제기준", "color": {"a": 1, "rgb": 15695665}},
                {"query": "tag:#entity/기관", "color": {"a": 1, "rgb": 5431378}},
                {"query": "tag:#entity/산업", "color": {"a": 1, "rgb": 11621088}},
                {"query": "tag:#entity/이슈", "color": {"a": 1, "rgb": 14701138}},
                {"query": "tag:#entity/지표", "color": {"a": 1, "rgb": 5419488}},
            ],
            "collapse-display": True,
            "showArrow": True,
            "textFadeMultiplier": 0,
            "nodeSizeMultiplier": 1,
            "lineSizeMultiplier": 1,
            "collapse-forces": True,
            "centerStrength": 0.5,
            "repelStrength": 10,
            "linkStrength": 1,
            "linkDistance": 250,
            "scale": 1,
            "close": False,
        },
        "workspace.json": _preset_workspace(),
    }


def _preset_workspace() -> dict:
    """첫 화면 — 왼쪽 파일 탐색기, **오른쪽에 태그 패널을 펼친 채로.**

    `right.collapsed` 를 False 로 두는 것이 4단계 DoD("태그 패널이 보임")의 실체다.
    id 는 Obsidian 이 재사용하는 임의값이라 고정해도 된다.
    """
    return {
        "main": {
            "id": "ra-main", "type": "split", "direction": "vertical",
            "children": [{
                "id": "ra-main-tabs", "type": "tabs",
                "children": [{
                    "id": "ra-main-leaf", "type": "leaf",
                    "state": {"type": "empty", "state": {}},
                }],
            }],
        },
        "left": {
            "id": "ra-left", "type": "split", "direction": "horizontal", "width": 300,
            "children": [{
                "id": "ra-left-tabs", "type": "tabs", "currentTab": 0,
                "children": [
                    {"id": "ra-files", "type": "leaf",
                     "state": {"type": "file-explorer",
                               "state": {"sortOrder": "alphabetical"}}},
                    {"id": "ra-search", "type": "leaf",
                     "state": {"type": "search", "state": {"query": ""}}},
                ],
            }],
        },
        "right": {
            "id": "ra-right", "type": "split", "direction": "horizontal",
            "width": 320, "collapsed": False,
            "children": [{
                "id": "ra-right-tabs", "type": "tabs", "currentTab": 0,
                "children": [
                    {"id": "ra-tags", "type": "leaf",
                     "state": {"type": "tag",
                               "state": {"sortOrder": "frequency",
                                         "useHierarchy": True}}},
                    {"id": "ra-backlink", "type": "leaf",
                     "state": {"type": "backlink", "state": {"collapseAll": False}}},
                    {"id": "ra-outline", "type": "leaf",
                     "state": {"type": "outline", "state": {}}},
                ],
            }],
        },
        "active": "ra-main-leaf",
        "lastOpenFiles": [],
    }


# ------------------------------------------------------------------ Obsidian


def obsidian_uri(vault_path) -> str:
    """`obsidian://open?path=…`.

    ⚠️ **Obsidian 이 이미 아는 볼트에만 통한다.** 처음 만든 폴더에 이 링크를 쓰면
    Obsidian 이 오류 창을 띄운다 (실측 — 1.13.4). 그래서 `register_with_obsidian()`
    으로 먼저 등록한다.
    """
    return "obsidian://open?path=" + quote(str(Path(vault_path).resolve()), safe="")


def _obsidian_registry() -> Path:
    """Obsidian 의 볼트 목록 파일. Obsidian 을 한 번이라도 실행했으면 있다."""
    root = os.getenv("APPDATA") or str(Path.home() / ".config")
    return Path(root) / "obsidian" / "obsidian.json"


def registered_with_obsidian(vault_path) -> bool:
    reg = _read_registry()
    if reg is None:
        return False
    want = str(Path(vault_path).resolve()).casefold()
    return any(
        str(Path(v.get("path", "")).resolve()).casefold() == want
        for v in reg.get("vaults", {}).values()
        if isinstance(v, dict) and v.get("path")
    )


def _read_registry():
    try:
        data = json.loads(_obsidian_registry().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data.get("vaults"), dict) else None


def register_with_obsidian(vault_path) -> bool:
    """볼트를 Obsidian 의 목록에 **더한다**. 성공하면 True.

    이건 남의 프로그램 설정 파일을 건드리는 일이라 규칙을 셋 둔다.

      1. **더하기만 한다.** 기존 항목은 그대로 옮겨 담고, 파싱에 실패하면
         아무것도 쓰지 않는다 — 사용자의 볼트 목록을 날리는 것보다
         링크가 안 되는 편이 훨씬 낫다.
      2. **원자적으로 쓴다.** 중간에 죽어도 반쪽 파일이 남지 않는다.
      3. **실패해도 조용히 False.** 화면은 그때 "Open folder as vault" 안내로
         떨어진다 (→ ui_vault._opened_block).

    Obsidian 이 켜져 있으면 자기 상태로 이 파일을 다시 쓸 수 있어 우리 항목이
    사라질 수 있다. 그건 손해가 아니라 원상복귀이고, 화면이 등록 여부를 매번
    다시 보므로 버튼이 다시 나타난다.
    """
    path = str(Path(vault_path).resolve())
    if registered_with_obsidian(path):
        return True
    reg = _read_registry()
    if reg is None:
        return False
    vault_id = hashlib.sha1(path.casefold().encode("utf-8")).hexdigest()[:16]
    reg["vaults"][vault_id] = {"path": path, "ts": int(time.time() * 1000)}
    target = _obsidian_registry()
    tmp = target.with_name(target.name + ".ra-tmp")
    try:
        tmp.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def obsidian_installed() -> bool:
    """설치 여부 (최선의 추정). 프로토콜 등록 → 실행 파일 순으로 본다."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "obsidian"):
            return True
    except (ImportError, OSError):
        pass
    local = os.getenv("LOCALAPPDATA")
    return bool(local and (Path(local) / "Obsidian" / "Obsidian.exe").exists())


# ------------------------------------------------------------------ 폴더 선택


# ⚠️ 소유자 폼을 **띄우고 활성화한 뒤에** 대화상자를 연다.
#
# 예전에는 `$top.TopMost = $true` 만 주고 `Show()` 를 하지 않았다. **표시되지
# 않은 폼의 TopMost 는 효력이 없어서**, 폴더 선택 창이 브라우저 뒤에 숨었다.
# 사용자에게는 "폴더 선택 창을 여는 중..." 이 영원히 도는 것으로 보인다 —
# 실제로는 창이 떠 있고 화면 뒤에서 클릭을 기다린다 (2026-08-15 실측).
#
# 폼 자체는 보이면 안 되므로 1픽셀짜리를 화면 밖에 둔다. 작업 표시줄에도
# 넣지 않는다. 이 폼의 유일한 일은 **앞으로 나오는 권한을 대화상자에 넘기는 것**이다.
_PICKER_PS = """
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$top = New-Object System.Windows.Forms.Form
$top.Text = 'ANFILT'
$top.TopMost = $true
$top.ShowInTaskbar = $false
$top.FormBorderStyle = 'None'
$top.StartPosition = 'Manual'
$top.Location = New-Object System.Drawing.Point(-32000, -32000)
$top.Size = New-Object System.Drawing.Size(1, 1)
$top.Show()
$top.Activate()
[System.Windows.Forms.Application]::DoEvents()
$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$dlg.Description = '지식볼트로 사용할 폴더를 고르세요'
$dlg.ShowNewFolderButton = $true
$dlg.SelectedPath = '__INIT__'
if ($dlg.ShowDialog($top) -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($dlg.SelectedPath)
}
$top.Close()
$top.Dispose()
"""


def pick_folder(initial=None, timeout: int = 300):
    """OS 폴더 선택 창을 띄운다. 고르지 않았거나 실패하면 None.

    Streamlit 앱은 **사용자 PC 안에서** 돌기 때문에 이게 성립한다 —
    호스팅 체험판(리눅스)에서는 `sys.platform` 이 걸러 내고, 화면 쪽에서도
    설치판에서만 이 버튼을 낸다 (두 겹).

    임베디드 파이썬에는 tkinter 가 없어서 PowerShell 의 FolderBrowserDialog 를
    쓴다. 명령은 **Base64(UTF-16LE)** 로 넘긴다 — 인용부호·한글이 콘솔
    코드페이지를 타지 않게 (→ docs/11 이 프로젝트의 상습 함정).
    """
    if sys.platform != "win32":
        return None
    init = str(Path(initial).expanduser()) if initial else ""
    script = _PICKER_PS.replace("__INIT__", init.replace("'", "''"))
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-STA",
             "-EncodedCommand", enc],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    picked = out.stdout.decode("utf-8", errors="replace").strip()
    return picked or None


# ------------------------------------------------------------------ zip 가져오기


def import_zip(store, data: bytes, now_iso: str, mode: str = "merge") -> dict:
    """체험판에서 받은 볼트 zip 을 이 볼트로 들여온다.

    zip 안에는 두 종류가 섞여 있다.
      - `**/*.md` · `_index/entities.json` → **볼트 폴더**로 전개
      - `runs/*.run.json`                  → **sqlite 조사 이력**으로 적재

    run.json 을 폴더에 쓰지 않는 이유: 정식판의 조사 아카이브는 sqlite 다
    (`.research-agent/agent.db`). 같은 기록을 폴더에도 두면 둘이 어긋날 수 있고,
    어긋났을 때 어느 쪽이 진짜인지 답할 방법이 없다.

    인덱스(`_index/entities.json`)는 zip 것을 쓰지 않고 **다시 만든다** —
    체험판에서 내보낸 뒤 사람이 노트를 고쳤을 수 있기 때문이다.

    mode
      ``merge``   같은 경로만 덮어쓴다 (사용자 편집은 충돌본으로 보존)
      ``replace`` 볼트의 기존 노트를 지우고 zip 내용으로 갈아 끼운다

    반환: {"files", "runs", "skipped", "conflicts"}  (`files` = 쓴 노트 수)
    """
    from . import ontology
    from .vault_render import parse_vault_zip

    files = parse_vault_zip(data)          # 경로 정리·볼트 구조 검증은 여기서
    runs, notes, skipped = {}, {}, []
    for rel, content in files.items():
        if rel.endswith(".run.json"):
            runs[rel] = content
        elif rel.endswith(".md"):
            notes[rel] = content
        elif rel != "_index/entities.json":
            skipped.append(rel)

    if not notes:
        raise ValueError("zip 안에 노트(.md)가 없습니다.")

    if mode == "replace":
        written = store.vault_replace_all(notes, now_iso)
    else:
        # 먼저 한 번 읽어 둔다 — 그래야 사용자가 Obsidian 에서 고친 노트를
        # 덮어쓰지 않고 `.research-agent/conflicts/` 로 비켜 둘 수 있다
        store.vault_list()
        written = store.vault_upsert_many(notes, now_iso)
    conflicts = list(getattr(store, "conflicts", []) or [])

    # 색인은 **합쳐진 볼트 전체**로 다시 만든다. zip 안의 노트만으로 만들면
    # 원래 있던 시드 35개가 색인에서 통째로 사라진다 — 조사·비서가 조용히
    # 아무것도 못 찾게 되는 종류의 고장이다 (실측으로 잡았다).
    store.vault_upsert_many(
        {"_index/entities.json": ontology.build_index(store.vault_list(), now_iso[:10])},
        now_iso,
    )

    loaded = 0
    for rel, raw in sorted(runs.items()):
        try:
            record = json.loads(raw)
        except ValueError:
            skipped.append(f"{rel}: JSON 형식이 아닙니다")
            continue
        if not isinstance(record, dict) or not record.get("run_id") \
                or not record.get("executed_at"):
            skipped.append(f"{rel}: 조사 기록 형식이 아닙니다")
            continue
        try:
            store.save_run(record)
            loaded += 1
        except Exception as e:                      # noqa: BLE001 — 한 건 실패가 전체를 막지 않는다
            skipped.append(f"{rel}: 저장 실패 ({e})")

    return {"files": written, "runs": loaded, "skipped": skipped,
            "conflicts": conflicts}
