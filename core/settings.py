"""설정 파일(config.json) — 볼트 목록·활성 볼트·모델 오버라이드.

→ docs/22 설치판 아키텍처 6절. Streamlit 비의존 (→ docs/02 파이프라인 설계 원칙).

**비밀은 여기에 저장하지 않는다.** API 키·PIN·라이선스는 OS 키체인으로 간다
(3단계 `core/keys.py`). 이 파일은 평문이고 사용자가 열어 볼 수 있어야 한다.

쓰기는 원자적으로 한다 — 설정이 반쯤 쓰인 채로 앱이 죽으면 다음 기동에서
볼트 경로를 잃는다.
"""
import json
import os
from pathlib import Path

from . import appdirs

SCHEMA = 1

_DEFAULT = {
    "schema": SCHEMA,
    "vaults": [],            # [{"name": "기본", "path": "..."}]
    "active_vault": 0,
    "models": {},            # {"gemini": "gemini-2.5-pro"} — 비우면 기본값
    "mobile": {"enabled": False, "pin_set": False},
    "update": {"channel": "stable", "auto": True, "last_check": ""},
}


def load() -> dict:
    """설정을 읽는다. 없거나 깨졌으면 기본값 (앱이 뜨는 게 우선)."""
    try:
        raw = json.loads(appdirs.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(_DEFAULT)
    if not isinstance(raw, dict):
        return dict(_DEFAULT)
    merged = dict(_DEFAULT)
    merged.update(raw)
    merged["schema"] = SCHEMA
    if not isinstance(merged.get("vaults"), list):
        merged["vaults"] = []
    return merged


def save(cfg: dict) -> None:
    """원자적 저장 — 임시 파일에 쓰고 교체한다."""
    path = appdirs.config_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 볼트


def vaults(cfg: dict = None) -> list:
    return (cfg or load()).get("vaults") or []


def active_vault(cfg: dict = None) -> dict:
    """현재 볼트 {"name","path"}. 등록된 게 없으면 None (→ 최초 실행 마법사)."""
    cfg = cfg or load()
    items = vaults(cfg)
    if not items:
        return None
    idx = cfg.get("active_vault") or 0
    if not (0 <= idx < len(items)):
        idx = 0
    return items[idx]


def active_vault_path(cfg: dict = None) -> Path:
    v = active_vault(cfg)
    return Path(v["path"]) if v else None


def _same_folder(a, b) -> bool:
    """두 경로가 같은 폴더를 가리키나. 실경로로 견준다(링크·subst 를 편다)."""
    def r(x):
        try:
            return str(Path(x).resolve()).casefold()
        except OSError:
            return str(x).casefold()
    return r(a) == r(b)


def add_vault(name: str, path, make_active: bool = True) -> dict:
    """볼트를 등록한다(이미 있으면 그것을 활성화). 반환: 갱신된 설정."""
    cfg = load()
    # **저장하는 경로는 고객이 고른 그대로**다 (-> appdirs.norm_path).
    # 예전에는 resolve() 를 썼는데, 윈도우에서 그것은 subst 드라이브와
    # 연결된 네트워크 드라이브를 실경로로 펴 버린다. 그래서 고객이 D: 를
    # 골라도 설정에는 다른 드라이브나 네트워크 공유 경로가 적혔다.
    p = str(appdirs.norm_path(path) or Path(path))
    items = cfg.setdefault("vaults", [])
    for i, v in enumerate(items):
        # **견주는 것은 실경로로** 한다 — 같은 폴더를 두 경로로 가리켜도 한 번만
        # 등록되게. 저장은 위에서 이미 고른 그대로 해 두었다.
        if _same_folder(v["path"], p):
            if make_active:
                cfg["active_vault"] = i
            save(cfg)
            return cfg
    items.append({"name": name or Path(p).name, "path": p})
    if make_active:
        cfg["active_vault"] = len(items) - 1
    save(cfg)
    return cfg


def set_active_vault(index: int) -> dict:
    cfg = load()
    if 0 <= index < len(vaults(cfg)):
        cfg["active_vault"] = index
        save(cfg)
    return cfg


def remove_vault(index: int) -> dict:
    """목록에서만 뺀다 — **폴더는 지우지 않는다.** 고객 데이터이기 때문이다."""
    cfg = load()
    items = vaults(cfg)
    if 0 <= index < len(items):
        items.pop(index)
        cfg["active_vault"] = min(cfg.get("active_vault", 0), max(0, len(items) - 1))
        save(cfg)
    return cfg
