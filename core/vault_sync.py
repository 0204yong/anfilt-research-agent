"""지식볼트 서버 사본의 시드 초기화 — app·페이지·감시 CLI가 공유한다.

app.py 안에만 있던 `_load_seed_files`/`_ensure_vault_seeded`를 꺼낸 모듈이다.
감시 실행(watch_run.py)은 Streamlit 없이 돌기 때문에 여기에 있어야 한다
(→ docs/02 파이프라인 설계 원칙: Streamlit 비의존).
"""
from pathlib import Path

from . import ontology

SEED_DIR = Path(__file__).resolve().parent.parent / "vault_seed"


def load_seed_files() -> dict:
    """repo에 포함된 시드 온톨로지(vault_seed/)를 {path: content}로 읽는다."""
    if not SEED_DIR.exists():
        return {}
    return {
        p.relative_to(SEED_DIR).as_posix(): p.read_text(encoding="utf-8")
        for p in SEED_DIR.rglob("*.md")
    }


def ensure_vault_seeded(store_obj, now_iso: str) -> bool:
    """볼트가 비어 있으면 시드 온톨로지로 초기화한다 (콜드스타트 방지).

    저장소를 **인자로 받는다** — 어느 볼트인지 정하지 않고 시드를 넣을 수 없게
    (→ docs/22 설치판 아키텍처 4절). 저장소가 없으면(볼트 미등록) 아무것도 안 한다.

    반환: 시드를 넣었으면 True.
    """
    if store_obj is None or store_obj.vault_is_empty() is False:
        return False
    # 시드는 **팩을 거쳐** 얻는다. ESG 온톨로지 35노트도 이 제품의 값어치라
    # 라이선스 팩에 실린다 (→ docs/20). 팩에 없으면 동봉 폴더로 떨어진다
    # (체험판·개발 상태).
    from . import packs
    seed = packs.seed()
    if not seed:
        return False
    seed["_index/entities.json"] = ontology.build_index(seed, now_iso[:10])
    store_obj.vault_upsert_many(seed, now_iso)
    return True
