-- 지식볼트 서버 사본 (docs/13 지식 볼트와 온톨로지 · 로드맵 3단계)
-- 실행 방법: Supabase 대시보드(unjhdoulorbmpepnkcpr) → SQL Editor →
--            이 파일의 "내용"을 붙여넣어 실행 (파일명 아님)
--
-- 원본은 어디까지나 마크다운(사용자 Obsidian 볼트)이고, 이 테이블은
-- Streamlit Cloud의 휘발성 파일시스템 때문에 앱이 세션 사이에 볼트를
-- 유지하기 위한 작업 사본이다. zip 가져오기가 이 사본을 덮어쓴다(사용자 우선).
-- 접근 모델은 ra_runs와 동일: service_role만, anon/authenticated 전면 차단.

create table if not exists public.ra_vault (
  path       text primary key,          -- 예: entities/규제·기준/EU CBAM.md
  content    text not null,             -- 마크다운(또는 _index/entities.json)
  updated_at timestamptz not null default now()
);

alter table public.ra_vault enable row level security;

revoke all on public.ra_vault from anon, authenticated;
