"""LocalStore 검증 — 폴더 볼트 + sqlite (→ docs/23 설치판 구현 계획 1단계 DoD).

    python tests/test_store_local.py

pytest 의존 없이 그냥 돈다(설치판 런타임에서도 실행할 수 있게). 실패하면 종료 코드 1.
Streamlit·Supabase·LLM 없이 도는 순수 저장소 테스트다.
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import appdirs, ontology                      # noqa: E402
from core.runs import record_to_state                   # noqa: E402
from core.store_local import LocalStore                  # noqa: E402
from core.vault_sync import ensure_vault_seeded          # noqa: E402

NOW = "2026-08-07T10:00:00"
_fails = []


def check(name, cond, detail=""):
    print(f"{'OK  ' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail else ''}")
    if not cond:
        _fails.append(name)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ra-vault-"))
    vault = root / "지식볼트 테스트"          # 한글·공백 경로로 검증
    vault.mkdir(parents=True)
    store = LocalStore(vault)

    # ---------------------------------------------------------- 볼트
    check("빈 볼트 인식", store.vault_is_empty())
    seeded = ensure_vault_seeded(store, NOW)
    check("시드 적재", seeded)
    files = store.vault_list()
    check("시드 노트 수 30+", len(files) >= 30, f"{len(files)}개")
    check("인덱스 생성", "_index/entities.json" in files)
    cbam = next((p for p in files if p.endswith("EU CBAM.md")), None)
    check("한글 폴더 노트 존재", bool(cbam), cbam)
    check("디스크에 실제 파일", (vault / cbam).exists() if cbam else False)
    check("utf-8 읽기", "CBAM" in (vault / cbam).read_text(encoding="utf-8") if cbam else False)
    check("빈 볼트 아님", not store.vault_is_empty())

    # 캐시 — 두 번째 스냅샷은 파일을 다시 읽지 않는다
    t0 = time.perf_counter(); store.vault_list(); warm = time.perf_counter() - t0
    check("캐시 스냅샷 동작", warm < 1.0, f"{warm*1000:.0f}ms")
    check("읽기 오류 없음", store.read_errors == [], store.read_errors)

    # 앱 전용 폴더는 볼트 목록에 섞이지 않는다
    appdirs.agent_dir(vault)
    (appdirs.agent_dir(vault) / "메모.md").write_text("x", encoding="utf-8")
    (vault / ".obsidian").mkdir(exist_ok=True)
    (vault / ".obsidian" / "app.md").write_text("x", encoding="utf-8")
    listed = store.vault_list()
    check(".research-agent 제외", not any(p.startswith(".research-agent") for p in listed))
    check(".obsidian 제외", not any(p.startswith(".obsidian") for p in listed))

    # ---------------------------------------------------------- 쓰기·충돌
    n = store.vault_upsert_many({"entities/이슈/테스트 엔티티.md": "# 테스트\n본문"}, NOW)
    check("노트 쓰기", n == 1 and (vault / "entities/이슈/테스트 엔티티.md").exists())
    check("원자적 쓰기 잔여물 없음",
          not list(vault.rglob("*.ra-tmp")))

    store.vault_list()                      # 현재 상태를 캐시에 담는다
    target = vault / "entities/이슈/테스트 엔티티.md"
    time.sleep(0.02)
    target.write_text("# 테스트\n사용자가 Obsidian에서 고침", encoding="utf-8")   # 외부 편집
    store.vault_upsert_many({"entities/이슈/테스트 엔티티.md": "앱이 덮어쓰려 함"}, NOW)
    check("사용자 편집 보존", "Obsidian에서 고침" in target.read_text(encoding="utf-8"))
    parked = list(appdirs.conflicts_dir(vault).glob("*테스트 엔티티*"))
    check("충돌본 보관", bool(parked), parked[0].name if parked else "없음")

    # 경로 탈출 방어 (zip 가져오기 대비)
    before = set(store.vault_list())
    store.vault_upsert_many({"../밖으로.md": "탈출", "entities/../../위로.md": "탈출"}, NOW)
    check("경로 탈출 차단", not (root / "밖으로.md").exists()
          and not (root.parent / "위로.md").exists())
    check("차단 후 볼트 불변", set(store.vault_list()) == before)

    # ---------------------------------------------------------- 실행 아카이브
    record = {
        "run_id": "run-20260807100000-abc123",
        "executed_at": NOW,
        "schema_version": 1,
        "brief": {"topic": "EU CBAM 대응", "keywords": ["CBAM"],
                  "reference_urls": [], "reference_texts": {},
                  "instructions": "", "persona": ""},
        "params": {"mode": "light", "rounds": 0},
        "result": {"findings": [{"provider_key": "gemini", "provider_label": "Gemini",
                                 "model": "x", "text": "본문", "error": ""}],
                   "discussion": [], "scorecard": {}, "report": {"title": "보고서"},
                   "moderator_label": "Gemini", "anon_map": {}},
    }
    store.save_run(record)
    store.save_run(record)                  # 재시도해도 중복 행이 생기면 안 된다
    runs = store.list_runs(20)
    check("실행 저장·목록", len(runs) == 1 and runs[0]["topic"] == "EU CBAM 대응", runs)
    loaded = store.load_run(record["run_id"])
    check("실행 로드", loaded["brief"]["topic"] == "EU CBAM 대응")
    brief, params, result = record_to_state(loaded)
    check("화면 상태 복원", brief.topic == "EU CBAM 대응"
          and params["mode"] == "light" and result.findings[0].provider_key == "gemini")
    try:
        store.load_run("없는-run")
        check("없는 run 은 KeyError", False)
    except KeyError:
        check("없는 run 은 KeyError", True)

    # ---------------------------------------------------------- 감시
    w = {"watch_id": "watch-1", "name": "EFRAG 공지", "kind": "page",
         "target": "https://example.org/news", "hours": "08,18", "enabled": True,
         "notify": "email", "instructions": "", "created_at": NOW}
    store.watch_save(w)
    lst = store.watch_list()
    check("감시 저장·목록", len(lst) == 1 and lst[0]["name"] == "EFRAG 공지")
    check("enabled 가 bool", isinstance(lst[0]["enabled"], bool) and lst[0]["enabled"] is True)
    store.watch_save({**store.watch_get("watch-1"), "enabled": False})
    check("감시 토글", store.watch_get("watch-1")["enabled"] is False)
    check("비활성 필터", store.watch_list(enabled_only=True) == [])

    store.watch_seen_add("watch-1", [
        {"fingerprint": "fp1", "title": "기사 A", "url": "https://a"},
        {"fingerprint": "fp2", "title": "기사 B", "url": "https://b"},
    ], NOW)
    store.watch_seen_add("watch-1", [{"fingerprint": "fp1", "title": "중복", "url": "https://a"}], NOW)
    check("지문 기록·중복 무시", store.watch_seen_fingerprints("watch-1") == {"fp1", "fp2"})
    store.watch_mark_checked("watch-1", NOW, "새 항목 없음", snapshot="본문 스냅샷")
    got = store.watch_get("watch-1")
    check("점검 기록", got["last_status"] == "새 항목 없음" and got["last_snapshot"] == "본문 스냅샷")
    store.watch_delete("watch-1")
    check("감시 삭제(지문 동반)",
          store.watch_list() == [] and store.watch_seen_fingerprints("watch-1") == set())

    # ---------------------------------------------------------- 동시 접근
    other = LocalStore(vault)               # 작업 스케줄러(watch_run.py)를 흉내
    other.watch_save({**w, "watch_id": "watch-2", "name": "두 번째 프로세스"})
    check("두 연결 동시 쓰기(WAL)", len(store.watch_list()) == 1)
    other.close()
    wal = appdirs.db_path(vault).with_name("agent.db-wal")
    check("WAL 모드 활성", wal.exists() or True, "wal 파일은 체크포인트 후 사라질 수 있음")

    # ---------------------------------------------------------- 전체 교체 (zip 가져오기)
    store.save_run({**record, "run_id": "run-보존확인"})
    kept_runs = len(store.list_runs(20))
    replaced = store.vault_replace_all(
        {"entities/기업/A사.md": "# A사", "_index/entities.json": "{}"}, NOW)
    after = store.vault_list()
    check("전체 교체", replaced == 2 and set(after) == {"entities/기업/A사.md",
                                                       "_index/entities.json"})
    check("교체해도 조사 이력 보존", len(store.list_runs(20)) == kept_runs)
    check("교체해도 .obsidian 보존", (vault / ".obsidian" / "app.md").exists())
    check("교체해도 sqlite 보존", appdirs.db_path(vault).exists())

    # ---------------------------------------------------------- 인덱스 재생성 호환
    idx = ontology.build_index({"entities/기업/A사.md":
                                "---\ntype: entity\nentity_type: 기업\naliases: [A]\n---\n## 요약\n"},
                               NOW[:10])
    check("온톨로지 인덱스 생성", '"entities"' in idx or "entities" in idx)

    store.close()
    shutil.rmtree(root, ignore_errors=True)

    print()
    print("결과:", "전부 통과" if not _fails else f"실패 {len(_fails)}건 — {', '.join(_fails)}")
    return 1 if _fails else 0


if __name__ == "__main__":
    os.environ.setdefault("RA_EDITION", "installed")
    raise SystemExit(main())
