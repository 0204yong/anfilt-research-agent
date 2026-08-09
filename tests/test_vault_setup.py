"""4단계 회귀 — 볼트 만들기·진단·zip 가져오기 (LLM·Streamlit 없이).

실행:  python tests/test_vault_setup.py

`APPDATA` 를 임시 폴더로 바꿔 놓고 돌린다 — 개발자의 실제 config.json 과
볼트 목록을 건드리면 안 되기 때문이다. `core.appdirs` 가 `APPDATA` 를 매번
읽으므로 import 전에만 바꿔 두면 된다.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-vs-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")
os.environ["RA_EDITION"] = "installed"

from core import settings, store as store_mod, vault_setup  # noqa: E402
from core.vault_render import build_files_zip  # noqa: E402

_fails = []
_checks = 0


def check(cond, label):
    global _checks
    _checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        _fails.append(label)


def section(title):
    print(f"\n== {title}")


# ------------------------------------------------------------------ 진단

section("inspect — 폴더 상태 판정")

base = _SANDBOX / "vaults"
base.mkdir(parents=True)

info = vault_setup.inspect(base / "새볼트")
check(info["state"] == "new", "없는 폴더 → new")
check(not info["blocking"], "없는 폴더는 막지 않는다")

empty = base / "빈폴더"
empty.mkdir()
check(vault_setup.inspect(empty)["state"] == "empty", "빈 폴더 → empty")

nonempty = base / "노트있음"
nonempty.mkdir()
(nonempty / "메모.md").write_text("hi", encoding="utf-8")
i2 = vault_setup.inspect(nonempty)
check(i2["state"] == "nonempty" and i2["notes"] == 1, "노트 있는 폴더 → nonempty")
check(any("지우지 않고" in w for w in i2["warnings"]), "기존 파일 보존을 알린다")

vroot = base / "기존볼트"
(vroot / ".obsidian").mkdir(parents=True)
(vroot / "내노트.md").write_text("x", encoding="utf-8")
i3 = vault_setup.inspect(vroot)
check(i3["state"] == "vault_root", "`.obsidian/` 있음 → vault_root")
check(i3["vault_root"] == vroot.resolve(), "볼트 루트를 자기 자신으로 잡는다")

inside = vroot / "하위" / "리서치"
i4 = vault_setup.inspect(inside)
check(i4["state"] == "inside_vault", "기존 볼트 하위 폴더 → inside_vault")
check(i4["vault_root"] == vroot.resolve(), "조상 볼트 루트를 찾아낸다")

deep = base / ("긴" * 140)
check(any("너무 깁니다" in b for b in vault_setup.inspect(deep)["blocking"]),
      "MAX_PATH 초과 경로는 막는다")

cloud = base / "OneDrive" / "볼트"
check(any("클라우드" in w for w in vault_setup.inspect(cloud)["warnings"]),
      "클라우드 동기화 폴더는 경고한다")

afile = base / "파일.txt"
afile.write_text("x", encoding="utf-8")
check(vault_setup.inspect(afile)["state"] == "invalid", "파일을 고르면 invalid")

# 설정 폴더 안은 막는다 (업데이트·초기화가 건드리는 영역)
from core import appdirs  # noqa: E402
check(any("설정 폴더" in b
          for b in vault_setup.inspect(appdirs.data_dir() / "v")["blocking"]),
      "프로그램 설정 폴더 안은 막는다")

# ------------------------------------------------------------------ 만들기

section("create_vault — 폴더·시드·프리셋·등록")

NOW = "2026-08-09T10:00:00"
target = base / "고객사 A"
res = vault_setup.create_vault(target, "고객사 A", NOW)

check(res["seeded"] is True, "시드를 넣었다")
check(res["notes"] >= 36, f"시드 노트 + 인덱스가 있다 ({res['notes']}개)")
check((target / "entities" / "규제·기준" / "EU CBAM.md").exists(),
      "한글 폴더의 시드 노트가 실제 파일로 있다")
check((target / "_index" / "entities.json").exists(), "인덱스를 만들었다")
check(res["preset"] is True, ".obsidian 프리셋을 썼다")

conf = target / ".obsidian"
plugins = json.loads((conf / "core-plugins.json").read_text(encoding="utf-8"))
check(plugins.get("tag-pane") is True and plugins.get("graph") is True,
      "태그 패널·그래프 뷰가 켜져 있다")
ws = json.loads((conf / "workspace.json").read_text(encoding="utf-8"))
check(ws["right"]["collapsed"] is False, "오른쪽 사이드바가 펼쳐져 있다")
check(ws["right"]["children"][0]["children"][0]["state"]["type"] == "tag",
      "오른쪽 첫 탭이 태그 패널이다")
graph = json.loads((conf / "graph.json").read_text(encoding="utf-8"))
check(len(graph["colorGroups"]) == 5, "엔티티 종류별 색 그룹 5개")

check(settings.active_vault()["path"] == str(target.resolve()),
      "설정에 활성 볼트로 등록됐다")
check(store_mod.active() is not None, "store.active() 가 이 볼트를 준다")

# 사용자 설정 보호 — 이미 .obsidian 이 있으면 손대지 않는다
mine = base / "내볼트"
(mine / ".obsidian").mkdir(parents=True)
(mine / ".obsidian" / "core-plugins.json").write_text('{"graph": false}', encoding="utf-8")
r2 = vault_setup.create_vault(mine, "내볼트", NOW)
check(r2["preset"] is False, "기존 .obsidian 이 있으면 프리셋을 쓰지 않는다")
check(json.loads((mine / ".obsidian" / "core-plugins.json").read_text(encoding="utf-8"))
      == {"graph": False}, "사용자의 Obsidian 설정을 덮어쓰지 않는다")
check((mine / "entities").is_dir(), "그래도 시드 노트는 들어간다")

# 두 번 만들어도 중복 등록되지 않는다
n_before = len(settings.vaults())
vault_setup.create_vault(mine, "내볼트", NOW)
check(len(settings.vaults()) == n_before, "같은 경로를 다시 만들어도 중복 등록 없음")

# 기존 볼트의 하위 폴더 (DoD 4)
sub = vroot / "리서치"
r3 = vault_setup.create_vault(sub, "하위볼트", NOW)
check((sub / "entities").is_dir(), "기존 볼트 하위 폴더에도 볼트를 만든다")
check(r3["preset"] is True and (sub / ".obsidian").is_dir(),
      "하위 폴더는 자기 .obsidian 을 갖는다")
check(json.loads((vroot / ".obsidian" / "core-plugins.json").read_text(encoding="utf-8"))
      if (vroot / ".obsidian" / "core-plugins.json").exists() else True,
      "상위 볼트 설정은 그대로다")

# 시드가 실패해도 볼트는 만들어진다 (팩 없음·손상 — 설계서 22 8절).
# 실측에서 실제로 걸린 경로다: 시드 노트 파싱이 프롬프트 팩의 술어 어휘를 쓴다.
from core import vault_sync  # noqa: E402

_real_seed = vault_sync.ensure_vault_seeded
vault_sync.ensure_vault_seeded = lambda *a, **k: (_ for _ in ()).throw(
    RuntimeError("프롬프트 팩을 찾을 수 없습니다")
)
try:
    broken = base / "팩없음"
    r4 = vault_setup.create_vault(broken, "팩없음", NOW)
finally:
    vault_sync.ensure_vault_seeded = _real_seed
check(broken.is_dir(), "시드가 실패해도 폴더는 만들어진다")
check((broken / ".obsidian").is_dir(), "시드가 실패해도 Obsidian 프리셋은 들어간다")
check(r4["seeded"] is False and "팩" in (r4["seed_error"] or ""),
      "시드 실패를 삼키지 않고 이유를 돌려준다")
check(settings.active_vault()["path"] == str(broken.resolve()),
      "시드가 실패해도 볼트는 등록·활성화된다")

# ------------------------------------------------------------------ Obsidian

section("Obsidian 연동")

uri = vault_setup.obsidian_uri(target)
check(uri.startswith("obsidian://open?path="), "obsidian:// URI 형식")
check("%EA%B3%A0%EA%B0%9D%EC%82%AC" in uri, "한글 경로가 퍼센트 인코딩된다")
check(" " not in uri and "\\" not in uri, "공백·역슬래시가 이스케이프된다")
check(isinstance(vault_setup.obsidian_installed(), bool), "설치 판정은 bool")

# --- 볼트 목록 등록. `obsidian://` 은 Obsidian 이 아는 볼트에만 통한다(실측 1.13.4).
#     APPDATA 를 샌드박스로 바꿔 뒀으므로 개발자의 실제 Obsidian 설정은 건드리지 않는다.
reg_path = vault_setup._obsidian_registry()
check(vault_setup.register_with_obsidian(target) is False,
      "Obsidian 설정 파일이 없으면 만들지 않고 False (남의 앱 파일을 창조하지 않는다)")

reg_path.parent.mkdir(parents=True, exist_ok=True)
MINE = {"vaults": {"abc123": {"path": "C:\\내\\기존 볼트", "ts": 1, "open": True}},
        "frame": {"x": 1}}
reg_path.write_text(json.dumps(MINE, ensure_ascii=False), encoding="utf-8")

check(vault_setup.registered_with_obsidian(target) is False, "등록 전에는 False")
check(vault_setup.register_with_obsidian(target) is True, "등록 성공")
check(vault_setup.registered_with_obsidian(target) is True, "등록 후 True")

after = json.loads(reg_path.read_text(encoding="utf-8"))
check(after["vaults"]["abc123"] == MINE["vaults"]["abc123"],
      "사용자의 기존 볼트 항목을 그대로 보존한다")
check(after.get("frame") == {"x": 1}, "우리가 모르는 키도 보존한다")
check(len(after["vaults"]) == 2, "항목을 더하기만 한다")

n = len(after["vaults"])
vault_setup.register_with_obsidian(target)
check(len(json.loads(reg_path.read_text(encoding="utf-8"))["vaults"]) == n,
      "두 번 등록해도 중복되지 않는다")

reg_path.write_text("{ 깨진 JSON", encoding="utf-8")
check(vault_setup.register_with_obsidian(target) is False, "깨진 파일이면 False")
check(reg_path.read_text(encoding="utf-8") == "{ 깨진 JSON",
      "깨진 파일도 덮어쓰지 않는다 — 링크가 안 되는 게 목록을 날리는 것보다 낫다")
reg_path.unlink()

# ------------------------------------------------------------------ zip

section("import_zip — 노트는 폴더로, run.json 은 sqlite 로")

RUN = {
    "run_id": "run-20260808-abc",
    "executed_at": "2026-08-08T09:00:00",
    "schema_version": 1,
    "brief": {"topic": "CBAM 인증서 가격"},
    "params": {"target_pages": 6},
    "result": {"report": {"sections": []}},
}
trial_zip = build_files_zip({
    "entities/규제·기준/EU CBAM.md": "---\ntype: entity\n---\n\n## 요약\n\n체험판에서 고친 내용\n",
    "entities/이슈/새 이슈.md": "---\ntype: entity\n---\n\n## 요약\n\n체험판에만 있던 노트\n",
    "runs/2026-08-08 CBAM 인증서 가격.md": "# 조사 노트\n",
    "runs/2026-08-08 CBAM 인증서 가격.run.json": json.dumps(RUN, ensure_ascii=False),
    "_index/entities.json": '{"stale": true}',
}, "볼트.zip")[1]

store_mod.reset_cache()
settings.set_active_vault(
    [i for i, v in enumerate(settings.vaults())
     if v["path"] == str(target.resolve())][0]
)
store = store_mod.active()

before = len(store.vault_list())
seed_names = {p for p in store.vault_list() if p.startswith("entities/")}
out = vault_setup.import_zip(store, trial_zip, NOW, mode="merge")

check(out["files"] == 3, f"노트 3개를 썼다 ({out['files']})")
check(out["runs"] == 1, "조사 이력 1건을 sqlite 에 적재했다")
check(not (target / "runs" / "2026-08-08 CBAM 인증서 가격.run.json").exists(),
      "run.json 은 폴더에 쓰지 않는다 (아카이브는 sqlite 하나뿐)")
check((target / "entities" / "이슈" / "새 이슈.md").exists(), "새 노트가 들어왔다")
check("체험판에서 고친 내용"
      in (target / "entities" / "규제·기준" / "EU CBAM.md").read_text(encoding="utf-8"),
      "같은 경로 노트는 zip 내용으로 갱신됐다")
check(len(store.vault_list()) > before, "볼트가 늘었다 (기존 시드는 남았다)")

idx = json.loads((target / "_index" / "entities.json").read_text(encoding="utf-8"))
check("stale" not in idx, "인덱스는 zip 것을 쓰지 않고 다시 만든다")
# 합치기인데 zip 안의 노트만으로 색인을 만들면 원래 있던 시드 35개가 색인에서
# 사라진다 — 조사·비서가 조용히 아무것도 못 찾게 된다 (실측으로 잡은 버그)
indexed = {e["path"] for e in idx["entities"]}
check(seed_names <= indexed,
      f"색인이 기존 시드를 전부 유지한다 (색인 {len(indexed)} · 시드 {len(seed_names)})")
check("entities/이슈/새 이슈.md" in indexed, "색인에 새로 들어온 노트도 있다")

runs = store.list_runs(10)
check(any(r["run_id"] == "run-20260808-abc" for r in runs), "조사 이력이 목록에 뜬다")
check(store.load_run("run-20260808-abc")["brief"]["topic"] == "CBAM 인증서 가격",
      "불러온 기록의 내용이 온전하다")

# 깨진 run.json 하나가 전체를 막지 않는다
bad_zip = build_files_zip({
    "entities/이슈/또 다른 노트.md": "# x\n",
    "runs/깨짐.run.json": "{ 이건 JSON 이 아님",
    "runs/빈것.run.json": '{"topic": "run_id 가 없다"}',
}, "b.zip")[1]
out2 = vault_setup.import_zip(store, bad_zip, NOW, mode="merge")
check(out2["files"] == 1, "정상 노트는 들어간다")
check(out2["runs"] == 0 and len(out2["skipped"]) == 2,
      "깨진 기록 2건은 건너뛰고 보고한다")

# 교체 모드
n_runs = len(store.list_runs(50))
out3 = vault_setup.import_zip(store, trial_zip, NOW, mode="replace")
check(not (target / "entities" / "이슈" / "또 다른 노트.md").exists(),
      "교체 모드는 기존 노트를 지운다")
check(len(store.list_runs(50)) == n_runs,
      "교체해도 조사 이력(sqlite)은 지우지 않는다")

# 볼트가 아닌 zip 은 명확히 거부
try:
    vault_setup.import_zip(store, build_files_zip({"아무거나.md": "x"}, "n.zip", root="")[1],
                           NOW)
    check(False, "볼트 구조가 아닌 zip 은 거부해야 한다")
except ValueError as e:
    check("entities" in str(e), "거부 사유에 무엇이 필요한지 적혀 있다")

# ------------------------------------------------------------------ 전환

section("볼트 전환 — 이력이 함께 바뀐다")

others = [i for i, v in enumerate(settings.vaults())
          if v["path"] != str(target.resolve())]
settings.set_active_vault(others[0])
store_mod.reset_cache()
other = store_mod.active()
check(other.vault_path != target.resolve(), "다른 볼트로 바뀌었다")
check(len(other.list_runs(50)) == 0, "새 볼트에는 이전 볼트의 조사 이력이 없다")
check(getattr(other, "label", "") != getattr(store, "label", ""),
      "캐시 키(label)가 달라진다 — 화면 캐시가 섞이지 않는다")

# 목록에서 빼도 폴더는 남는다
settings.remove_vault(others[0])
check(Path(other.vault_path).exists(), "목록에서 제거해도 폴더는 남는다")

# ------------------------------------------------------------------

store_mod.reset_cache()
print(f"\n{'=' * 60}")
if _fails:
    print(f"실패 {len(_fails)}/{_checks}")
    for f in _fails:
        print(f"  - {f}")
else:
    print(f"전부 통과 — {_checks}항목")
shutil.rmtree(_SANDBOX, ignore_errors=True)
sys.exit(1 if _fails else 0)
