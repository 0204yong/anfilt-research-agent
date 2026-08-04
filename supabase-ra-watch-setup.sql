-- 자동 모니터링(감시) 저장소 (docs/15 자동 모니터링과 알림)
-- 실행 방법: Supabase 대시보드(koorjatscpkvomjosenc) → SQL Editor →
--            이 파일의 "내용"을 붙여넣어 실행 (파일명 아님)
--
-- 접근 모델은 ra_runs·ra_vault와 동일: service_role만, anon/authenticated 전면 차단.
-- 감시 대상에는 고객사 관련 키워드가 들어갈 수 있으므로 브라우저 공개 키로는
-- 절대 열리면 안 된다.

-- 감시 대상 1건 = 1행
create table if not exists public.ra_watches (
  watch_id        text primary key,          -- watch-YYYYMMDDHHMMSS-xxxxxx
  name            text not null,             -- 표시 이름
  kind            text not null,             -- 'page'(특정 페이지) | 'keyword'(키워드 검색)
  target          text not null,             -- URL 또는 검색 키워드
  hours           text not null default '08',-- KST 실행 시각 CSV (예: '08,18')
  enabled         boolean not null default true,
  notify          text not null default 'email',  -- 'email,kakao' CSV
  instructions    text not null default '',  -- 요약 관점 (선택)
  last_snapshot   text not null default '',  -- page 감시용 직전 본문 (증분 추출)
  last_checked_at timestamptz,
  last_status     text not null default '',
  created_at      timestamptz not null default now()
);

create index if not exists ra_watches_enabled_idx
  on public.ra_watches (enabled);

-- 이미 본 항목의 지문 — '새로운 내용'의 정의는 이 테이블에 없다는 것이다
create table if not exists public.ra_watch_seen (
  watch_id      text not null,
  fingerprint   text not null,               -- 정규화 URL 또는 콘텐츠 해시
  title         text not null default '',
  url           text not null default '',
  first_seen_at timestamptz not null default now(),
  primary key (watch_id, fingerprint)
);

create index if not exists ra_watch_seen_watch_idx
  on public.ra_watch_seen (watch_id, first_seen_at desc);

alter table public.ra_watches    enable row level security;
alter table public.ra_watch_seen enable row level security;

-- 정책을 하나도 만들지 않음 = anon/authenticated 전면 차단.
revoke all on public.ra_watches    from anon, authenticated;
revoke all on public.ra_watch_seen from anon, authenticated;
