"""지식볼트 서버 사본의 시드 초기화 — app·페이지·감시 CLI가 공유한다.

app.py 안에만 있던 `_load_seed_files`/`_ensure_vault_seeded`를 꺼낸 모듈이다.
감시 실행(watch_run.py)은 Streamlit 없이 돌기 때문에 여기에 있어야 한다
(→ docs/02 파이프라인 설계 원칙: Streamlit 비의존).
"""
from pathlib import Path

from . import ontology

SEED_DIR = Path(__file__).resolve().parent.parent / "vault_seed"


def _seed_dir():
    """시드 폴더. **이 저장소에는 없다** — 팩과 함께 비공개 저장소로 옮겼다
    (→ docs/26 팩을 저장소 밖으로). 개발 중 그 저장소를 받아 뒀으면 거기서 읽는다.
    """
    if SEED_DIR.exists():
        return SEED_DIR
    from . import packs
    dev = packs.dev_pack_dir()
    return (dev / "vault_seed") if dev is not None else None


def load_seed_files() -> dict:
    """시드 온톨로지를 {path: content}로 읽는다.

    평소에는 이 함수까지 오지 않는다 — 시드는 **팩 안에** 실려 온다
    (`packs.seed()`). 여기는 팩에 시드가 없는 개발 상태의 폴백이다.
    """
    d = _seed_dir()
    if d is None or not d.exists():
        return {}
    return {
        p.relative_to(d).as_posix(): p.read_text(encoding="utf-8")
        for p in d.rglob("*.md")
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
