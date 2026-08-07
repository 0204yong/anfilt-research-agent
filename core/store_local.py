"""로컬 저장소 — 정식판(설치) 경로. 볼트는 폴더, 이력·감시는 sqlite.

→ docs/22 설치판 아키텍처 5절. Streamlit 비의존.

`SupabaseStore` 와 **메서드 이름·반환 모양이 같다.** 호출부(app.py 12곳·pages 6곳·
watch_runner)가 어느 쪽을 받든 그대로 동작해야 하기 때문이다.

핵심 차이 셋
  1. 볼트가 진짜 폴더다 — Obsidian이 여는 그 폴더. 그래서 **원자적 쓰기**와
     **동시 편집 감지**가 필요하다 (Supabase 사본에는 없던 문제).
  2. 전수 읽기가 REST 1회가 아니라 파일 수백 개다 — mtime 증분 캐시를 둔다.
  3. 앱과 작업 스케줄러(watch_run.py)가 같은 DB를 연다 — WAL + busy_timeout.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

from . import appdirs

SCHEMA_VERSION = 1

# 볼트를 훑을 때 건너뛸 폴더. Obsidian 설정·앱 전용 폴더·휴지통·git 은 노트가 아니다.
SKIP_DIRS = {".obsidian", appdirs.AGENT_DIRNAME, ".trash", ".git", "__pycache__"}
VAULT_SUFFIXES = (".md",)
EXTRA_FILES = ("_index/entities.json",)     # 마크다운은 아니지만 볼트의 일부


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_safe_relpath(rel: str) -> bool:
    """zip 가져오기 등 외부 입력이 볼트 밖으로 쓰지 못하게 한다 (zip-slip 방어)."""
    if not rel or rel.startswith(("/", "\\")) or ":" in rel.split("/")[0]:
        return False
    parts = Path(rel.replace("\\", "/")).parts
    return ".." not in parts and not any(p in SKIP_DIRS for p in parts[:-1])


class VaultCache:
    """mtime+size 기준 증분 스냅샷.

    한 화면에서 `vault_list()` 가 여러 번 불린다(주입·추출·내보내기). Supabase일
    땐 REST 1회였지만 로컬에서 매번 수백 파일을 전수 읽으면 체감이 나빠진다.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self._files = {}        # rel -> content
        self._stamp = {}        # rel -> (mtime_ns, size)
        self.errors = []        # 읽기 실패한 파일 (화면에 알린다 — 조용히 빠지면 안 된다)

    def _iter_paths(self):
        if not self.root.exists():
            return
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                p = Path(dirpath) / fn
                rel = _rel(p, self.root)
                if fn.endswith(VAULT_SUFFIXES) or rel in EXTRA_FILES:
                    yield rel, p

    def snapshot(self) -> dict:
        seen, files, errors = set(), {}, []
        for rel, p in self._iter_paths():
            seen.add(rel)
            try:
                st = p.stat()
                stamp = (st.st_mtime_ns, st.st_size)
            except OSError as e:
                errors.append(f"{rel}: {e}")
                continue
            if self._stamp.get(rel) == stamp and rel in self._files:
                files[rel] = self._files[rel]
                continue
            try:
                files[rel] = p.read_text(encoding="utf-8")
                self._stamp[rel] = stamp
            except (OSError, UnicodeDecodeError) as e:
                # utf-8 아닌 파일이 볼트에 섞일 수 있다. 건너뛰되 반드시 보고한다 —
                # 조용히 빠지면 "볼트에 있는데 비서가 모른다"가 된다.
                errors.append(f"{rel}: {e}")
        for gone in set(self._stamp) - seen:
            self._stamp.pop(gone, None)
        self._files = files
        self.errors = errors
        return dict(files)

    def forget(self, rel: str) -> None:
        self._stamp.pop(rel, None)
        self._files.pop(rel, None)


class LocalStore:
    kind = "local"

    def __init__(self, vault_path):
        self.vault_path = Path(vault_path)
        self._cache = VaultCache(self.vault_path)
        self._conn = None

    def is_configured(self) -> bool:
        return bool(self.vault_path)

    @property
    def label(self) -> str:
        return f"로컬 볼트 ({self.vault_path})"

    @property
    def read_errors(self) -> list:
        """직전 스냅샷에서 읽지 못한 파일들 — UI가 사용자에게 알린다."""
        return list(self._cache.errors)

    # ------------------------------------------------- 볼트 (폴더)

    def vault_list(self) -> dict:
        return self._cache.snapshot()

    def vault_is_empty(self) -> bool:
        for rel, _ in self._cache._iter_paths():
            if rel.endswith(".md"):
                return False
        return True

    def vault_upsert_many(self, files: dict, updated_at: str) -> int:
        """파일들을 볼트 폴더에 쓴다. 반환: 실제로 쓴 건수.

        사용자가 Obsidian에서 편집 중인 노트는 **덮어쓰지 않는다** — 앱 버전을
        `.research-agent/conflicts/` 에 남기고 원본을 지킨다. 판정은 사람이 한다
        (→ docs/13 `merge_entity` 원칙과 같은 태도).
        """
        if not files:
            return 0
        written = 0
        self.conflicts = []
        for rel, content in files.items():
            if not _is_safe_relpath(rel):
                self.conflicts.append(f"{rel}: 허용되지 않는 경로 — 건너뜀")
                continue
            target = self.vault_path / rel
            known = self._cache._stamp.get(rel)
            if target.exists() and known is not None:
                try:
                    st = target.stat()
                    if (st.st_mtime_ns, st.st_size) != known:
                        self._park_conflict(rel, content, updated_at)
                        continue
                except OSError:
                    pass
            self._atomic_write(target, content)
            self._cache.forget(rel)
            written += 1
        return written

    def vault_replace_all(self, files: dict, updated_at: str) -> int:
        """볼트 내용을 주어진 파일들로 교체 (zip 가져오기 — 사용자 우선).

        ⚠️ `.research-agent/` 와 `.obsidian/` 은 건드리지 않는다. 조사 이력·감시
        상태·Obsidian 설정은 볼트 노트와 수명이 다르다.
        """
        for dirpath, dirnames, filenames in os.walk(self.vault_path, topdown=True):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                p = Path(dirpath) / fn
                rel = _rel(p, self.vault_path)
                if fn.endswith(VAULT_SUFFIXES) or rel in EXTRA_FILES:
                    try:
                        p.unlink()
                    except OSError:
                        pass
        self._cache = VaultCache(self.vault_path)
        return self.vault_upsert_many(files, updated_at)

    def _atomic_write(self, target: Path, content: str) -> None:
        """임시 파일 → os.replace. Obsidian이 반쪽 파일을 읽는 일을 막는다."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".ra-tmp")
        tmp.write_text(content, encoding="utf-8", newline="\n")
        os.replace(tmp, target)

    def _park_conflict(self, rel: str, content: str, updated_at: str) -> None:
        stamp = str(updated_at or "").replace(":", "").replace("-", "")[:15]
        safe = rel.replace("/", "_")
        dest = appdirs.conflicts_dir(self.vault_path) / f"{stamp} {safe}"
        try:
            self._atomic_write(dest, content)
            self.conflicts.append(rel)
        except OSError as e:
            self.conflicts.append(f"{rel}: 충돌본 저장 실패 ({e})")

    # ------------------------------------------------- sqlite

    def _db(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        path = appdirs.db_path(self.vault_path)
        conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL — 앱과 작업 스케줄러(watch_run.py)가 동시에 열 수 있다
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma busy_timeout=5000")
        conn.execute("pragma foreign_keys=on")
        self._conn = conn
        self._migrate(conn)
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            create table if not exists meta (
              key text primary key, value text not null
            );
            create table if not exists runs (
              run_id text primary key,
              executed_at text not null,
              topic text not null,
              schema_version integer not null default 1,
              record text not null
            );
            create index if not exists runs_executed_idx on runs(executed_at desc);
            create table if not exists watches (
              watch_id text primary key,
              name text not null,
              kind text not null,
              target text not null,
              hours text not null default '08',
              enabled integer not null default 1,
              notify text not null default 'email',
              instructions text not null default '',
              last_snapshot text not null default '',
              last_checked_at text,
              last_status text not null default '',
              created_at text not null
            );
            create index if not exists watches_enabled_idx on watches(enabled);
            create table if not exists watch_seen (
              watch_id text not null,
              fingerprint text not null,
              title text not null default '',
              url text not null default '',
              first_seen_at text not null,
              primary key (watch_id, fingerprint)
            );
            """
        )
        cur = conn.execute("select value from meta where key='schema_version'")
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "insert into meta(key,value) values('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
        # 앞으로 스키마가 올라가면 여기서 순차 마이그레이션한다 (→ docs/22 11절)
        conn.commit()

    # ------------------------------------------------- 실행 아카이브

    def save_run(self, record: dict) -> str:
        conn = self._db()
        conn.execute(
            "insert into runs(run_id,executed_at,topic,schema_version,record) "
            "values(?,?,?,?,?) on conflict(run_id) do update set "
            "executed_at=excluded.executed_at, topic=excluded.topic, "
            "schema_version=excluded.schema_version, record=excluded.record",
            (
                record["run_id"],
                record["executed_at"],
                (record.get("brief") or {}).get("topic", ""),
                int(record.get("schema_version", 1)),
                json.dumps(record, ensure_ascii=False),
            ),
        )
        conn.commit()
        return record["run_id"]

    def list_runs(self, limit: int = 20) -> list:
        rows = self._db().execute(
            "select run_id,executed_at,topic from runs "
            "order by executed_at desc limit ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]

    def load_run(self, run_id: str) -> dict:
        row = self._db().execute(
            "select record from runs where run_id=?", (run_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"저장된 조사를 찾을 수 없습니다: {run_id}")
        return json.loads(row["record"])

    # ------------------------------------------------- 감시

    def watch_list(self, enabled_only: bool = False) -> list:
        q = "select * from watches"
        if enabled_only:
            q += " where enabled=1"
        q += " order by created_at asc"
        return [self._watch_row(r) for r in self._db().execute(q).fetchall()]

    def watch_get(self, watch_id: str) -> dict:
        row = self._db().execute(
            "select * from watches where watch_id=?", (watch_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"감시 대상을 찾을 수 없습니다: {watch_id}")
        return self._watch_row(row)

    @staticmethod
    def _watch_row(row) -> dict:
        d = dict(row)
        # sqlite 에는 bool 이 없다 — 호출부는 True/False 를 기대한다
        d["enabled"] = bool(d.get("enabled"))
        return d

    def watch_save(self, row: dict) -> str:
        cols = ("watch_id", "name", "kind", "target", "hours", "enabled", "notify",
                "instructions", "last_snapshot", "last_checked_at", "last_status",
                "created_at")
        cur = self.watch_get(row["watch_id"]) if self._watch_exists(row["watch_id"]) else {}
        vals = []
        for c in cols:
            v = row.get(c, cur.get(c))
            if c == "enabled":
                v = 1 if (True if v is None else v) else 0
            elif c == "created_at":
                v = v or time.strftime("%Y-%m-%dT%H:%M:%S")
            elif c in ("hours", "notify") and v is None:
                v = "08" if c == "hours" else "email"
            elif c != "last_checked_at" and v is None:
                v = ""
            vals.append(v)
        conn = self._db()
        conn.execute(
            f"insert into watches({','.join(cols)}) "
            f"values({','.join('?' * len(cols))}) "
            f"on conflict(watch_id) do update set "
            + ",".join(f"{c}=excluded.{c}" for c in cols if c != "watch_id"),
            vals,
        )
        conn.commit()
        return row["watch_id"]

    def _watch_exists(self, watch_id: str) -> bool:
        return self._db().execute(
            "select 1 from watches where watch_id=?", (watch_id,)
        ).fetchone() is not None

    def watch_delete(self, watch_id: str) -> None:
        conn = self._db()
        conn.execute("delete from watch_seen where watch_id=?", (watch_id,))
        conn.execute("delete from watches where watch_id=?", (watch_id,))
        conn.commit()

    def watch_mark_checked(self, watch_id: str, checked_at: str, status: str,
                           snapshot: str = None) -> None:
        conn = self._db()
        if snapshot is None:
            conn.execute(
                "update watches set last_checked_at=?, last_status=? where watch_id=?",
                (checked_at, str(status)[:300], watch_id),
            )
        else:
            conn.execute(
                "update watches set last_checked_at=?, last_status=?, last_snapshot=? "
                "where watch_id=?",
                (checked_at, str(status)[:300], snapshot, watch_id),
            )
        conn.commit()

    def watch_seen_fingerprints(self, watch_id: str) -> set:
        rows = self._db().execute(
            "select fingerprint from watch_seen where watch_id=?", (watch_id,)
        ).fetchall()
        return {r["fingerprint"] for r in rows}

    def watch_seen_add(self, watch_id: str, items: list, seen_at: str) -> int:
        if not items:
            return 0
        conn = self._db()
        conn.executemany(
            "insert or ignore into watch_seen"
            "(watch_id,fingerprint,title,url,first_seen_at) values(?,?,?,?,?)",
            [
                (watch_id, it["fingerprint"], str(it.get("title", ""))[:300],
                 str(it.get("url", ""))[:1000], seen_at)
                for it in items
            ],
        )
        conn.commit()
        return len(items)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
