-- 조사 실행 아카이브 (docs/13 지식 볼트와 온톨로지 · 로드맵 2단계)
-- 실행 방법: Supabase 대시보드(unjhdoulorbmpepnkcpr) → SQL Editor → 이 파일 전체 실행
--
-- 접근 모델: 앱(Streamlit 서버)이 service_role 키로만 읽고 쓴다.
-- anon/authenticated는 RLS로 전면 차단 — 조사 아카이브에는 고객사 자료가
-- 섞일 수 있으므로 브라우저 공개용 anon 키로는 절대 열리면 안 된다.

create table if not exists public.ra_runs (
  run_id         text primary key,          -- 앱이 생성 (run-YYYYMMDDHHMMSS-xxxxxx)
  executed_at    timestamptz not null,
  topic          text not null,
  schema_version int  not null default 1,   -- 스키마 변경 후에도 과거 run 복원 가능하게
  record         jsonb not null,            -- brief + params + result + references_meta 전체
  created_at     timestamptz not null default now()
);

create index if not exists ra_runs_executed_at_idx
  on public.ra_runs (executed_at desc);

alter table public.ra_runs enable row level security;

-- 정책을 하나도 만들지 않음 = anon/authenticated 전면 차단.
-- service_role은 RLS를 우회하므로 앱만 접근 가능. 권한도 명시적으로 회수해 이중 잠금.
revoke all on public.ra_runs from anon, authenticated;
