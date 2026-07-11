"""Supabase 조사 실행 아카이브 — ra_runs 1행 = 실행 1건.

로드맵 2단계 (→ docs/13 지식 볼트와 온톨로지). Streamlit 비의존
(→ docs/02 파이프라인 설계 원칙). supabase-py 대신 PostgREST REST를
requests로 직접 호출한다 (기존 의존성만 사용).

키는 서버측 전용 service_role만 쓴다 — anon 키는 RLS가 전면 차단하므로
동작하지 않으며, 그래야 고객사 자료가 브라우저 공개 키로 새지 않는다.
테이블 생성은 사용자가 `supabase-ra-runs-setup.sql`을 SQL Editor에서 실행(관례).
"""
import os

import requests

_TIMEOUT = 15


def _config() -> tuple:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    return url, key


def is_configured() -> bool:
    url, key = _config()
    return bool(url and key)


def _request(method: str, path: str, **kwargs):
    url, key = _config()
    if not (url and key):
        raise RuntimeError(
            "Supabase 미설정 — SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 필요"
        )
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **kwargs.pop("headers", {}),
    }
    resp = requests.request(
        method, f"{url}/rest/v1/{path}", headers=headers,
        timeout=_TIMEOUT, **kwargs,
    )
    if not resp.ok:
        # 본문에 PostgREST 오류 설명(JSON)이 담겨 온다 — 사용자에게 그대로 노출
        raise RuntimeError(f"Supabase {resp.status_code}: {resp.text[:300]}")
    return resp


def save_run(record: dict) -> str:
    """레코드 1건 업서트(run_id 기준 — 재시도해도 중복 행이 안 생긴다)."""
    row = {
        "run_id": record["run_id"],
        "executed_at": record["executed_at"],
        "topic": (record.get("brief") or {}).get("topic", ""),
        "schema_version": record.get("schema_version", 1),
        "record": record,
    }
    _request(
        "POST", "ra_runs", json=row,
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    return record["run_id"]


def list_runs(limit: int = 20) -> list:
    """최근 실행 요약 목록: [{run_id, executed_at, topic}] (최신순)."""
    resp = _request(
        "GET",
        f"ra_runs?select=run_id,executed_at,topic"
        f"&order=executed_at.desc&limit={int(limit)}",
    )
    return resp.json()


def load_run(run_id: str) -> dict:
    """run_id의 전체 레코드(jsonb)를 반환. 없으면 KeyError."""
    resp = _request(
        "GET", f"ra_runs?run_id=eq.{run_id}&select=record&limit=1"
    )
    rows = resp.json()
    if not rows:
        raise KeyError(f"저장된 조사를 찾을 수 없습니다: {run_id}")
    return rows[0]["record"]


# ------------------------------------------------- 지식볼트 서버 사본 (ra_vault)
# 원본은 마크다운(사용자 Obsidian 볼트). 이 테이블은 Streamlit Cloud의 휘발성
# 파일시스템 때문에 세션 사이에 볼트를 유지하는 작업 사본이다 (→ docs/13 3단계).


def vault_list() -> dict:
    """볼트 전체를 {path: content}로 반환."""
    resp = _request("GET", "ra_vault?select=path,content&order=path.asc")
    return {row["path"]: row["content"] for row in resp.json()}


def vault_is_empty() -> bool:
    resp = _request("GET", "ra_vault?select=path&limit=1")
    return not resp.json()


def vault_upsert_many(files: dict, updated_at: str) -> int:
    """여러 파일을 path 기준으로 업서트. 반환: 반영 건수."""
    if not files:
        return 0
    rows = [
        {"path": p, "content": c, "updated_at": updated_at}
        for p, c in files.items()
    ]
    _request(
        "POST", "ra_vault", json=rows,
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    return len(rows)


def vault_replace_all(files: dict, updated_at: str) -> int:
    """볼트 서버 사본 전체를 주어진 파일들로 교체한다 (zip 가져오기 — 사용자 우선)."""
    _request("DELETE", "ra_vault?path=like.*")
    return vault_upsert_many(files, updated_at)


# ------------------------------------------------- 레코드 → 화면 상태 복원


def record_to_state(record: dict) -> tuple:
    """아카이브 레코드를 (brief, params, result)로 복원한다.

    필드를 명시적으로 골라 담는다 — 과거/미래 schema_version의 여분 키가
    dataclass 생성자를 깨지 않게 (schema_version 필드의 존재 이유).
    """
    from .pipeline import (
        AgentFinding, DiscussionTurn, PipelineResult, ResearchBrief,
    )

    b = record.get("brief") or {}
    brief = ResearchBrief(
        topic=b.get("topic", ""),
        keywords=b.get("keywords") or [],
        reference_urls=b.get("reference_urls") or [],
        reference_texts=b.get("reference_texts") or {},
        instructions=b.get("instructions", ""),
        persona=b.get("persona", ""),
    )
    r = record.get("result") or {}
    result = PipelineResult(
        findings=[
            AgentFinding(
                provider_key=f.get("provider_key", ""),
                provider_label=f.get("provider_label", ""),
                model=f.get("model", ""),
                text=f.get("text", ""),
                error=f.get("error", ""),
            )
            for f in r.get("findings") or []
        ],
        discussion=[
            DiscussionTurn(
                round_no=t.get("round_no", 0),
                provider_key=t.get("provider_key", ""),
                provider_label=t.get("provider_label", ""),
                text=t.get("text", ""),
                error=t.get("error", ""),
            )
            for t in r.get("discussion") or []
        ],
        scorecard=r.get("scorecard") or {},
        report=r.get("report") or {},
        moderator_label=r.get("moderator_label", ""),
        anon_map=r.get("anon_map") or {},
    )
    return brief, record.get("params") or {}, result
