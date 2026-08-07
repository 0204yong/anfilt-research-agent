"""저장소 선택 — 에디션에 따라 `SupabaseStore`(체험판) / `LocalStore`(정식판).

→ docs/22 설치판 아키텍처 4·5절.

**모듈 수준 저장 함수는 더 이상 없다.** 예전에는 `store.vault_list()` 가 곧
Supabase 호출이었지만, 이제 "어느 볼트인지"를 정하지 않고는 볼트를 부를 수 없다.
호출부는 이렇게 쓴다.

    from core import store as store_mod
    store = store_mod.active()      # 이후 store.vault_list() 등은 그대로

이름을 `store` 로 받는 덕에 기존 호출부(app.py 12곳·pages 6곳)는 손대지 않는다.

정식판에서 볼트가 아직 등록되지 않았으면 `active()` 는 `None` 을 준다 —
그건 오류가 아니라 **최초 실행 마법사로 보내라는 신호**다 (4단계).
"""
from pathlib import Path

from . import edition
from .runs import record_to_state  # noqa: F401  (호출부 호환 — store.record_to_state)
from .store_local import LocalStore
from .store_supabase import SupabaseStore

_supabase = None
_local = {}          # 볼트 경로 -> LocalStore (전환해도 sqlite 연결을 재사용)


def supabase_store() -> SupabaseStore:
    global _supabase
    if _supabase is None:
        _supabase = SupabaseStore()
    return _supabase


def local_store(vault_path) -> LocalStore:
    key = str(Path(vault_path).resolve()).casefold()
    if key not in _local:
        _local[key] = LocalStore(vault_path)
    return _local[key]


def active():
    """지금 써야 할 저장소. 정식판인데 볼트 미등록이면 None."""
    if edition.is_installed():
        from . import settings          # 지연 import — 체험판은 설정 파일을 안 쓴다
        path = settings.active_vault_path()
        return local_store(path) if path else None
    return supabase_store()


def is_configured() -> bool:
    """저장소를 쓸 수 있는 상태인가 (화면 가드용)."""
    s = active()
    return bool(s and s.is_configured())


def reset_cache() -> None:
    """볼트 전환·설정 변경 후 호출 — 열린 sqlite 연결을 정리한다."""
    global _supabase
    for s in _local.values():
        try:
            s.close()
        except Exception:
            pass
    _local.clear()
    _supabase = None
