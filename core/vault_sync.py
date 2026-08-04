"""지식볼트 서버 사본의 시드 초기화 — app·페이지·감시 CLI가 공유한다.

app.py 안에만 있던 `_load_seed_files`/`_ensure_vault_seeded`를 꺼낸 모듈이다.
감시 실행(watch_run.py)은 Streamlit 없이 돌기 때문에 여기에 있어야 한다
(→ docs/02 파이프라인 설계 원칙: Streamlit 비의존).
"""
from pathlib import Path

from . import ontology, store

SEED_DIR = Path(__file__).resolve().parent.parent / "vault_seed"


def load_seed_files() -> dict:
    """repo에 포함된 시드 온톨로지(vault_seed/)를 {path: content}로 읽는다."""
    if not SEED_DIR.exists():
        return {}
    return {
        p.relative_to(SEED_DIR).as_posix(): p.read_text(encoding="utf-8")
        for p in SEED_DIR.rglob("*.md")
    }


def ensure_vault_seeded(now_iso: str) -> bool:
    """서버 볼트가 비어 있으면 시드 온톨로지로 초기화한다 (콜드스타트 방지).

    반환: 시드를 넣었으면 True.
    """
    if not store.vault_is_empty():
        return False
    seed = load_seed_files()
    if not seed:
        return False
    seed["_index/entities.json"] = ontology.build_index(seed, now_iso[:10])
    store.vault_upsert_many(seed, now_iso)
    return True
