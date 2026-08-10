-- 라이선스와 기기 활성화 (→ docs/20 라이선스와 복제 방지 · docs/23 7단계)
--
-- Supabase SQL Editor 에 통째로 붙여 넣어 실행한다. 기존 테이블은 건드리지 않는다.
--
-- ⚠️ 이 두 테이블은 **Edge Function(service_role)만** 읽고 쓴다.
--    RLS 를 켜고 정책을 하나도 만들지 않는 것이 곧 "아무도 직접 못 본다" 이다 —
--    anon 키로 라이선스 목록을 훑는 경로를 남기지 않는다.

create table if not exists public.ra_licenses (
  license_key text primary key,                    -- ANF-XXXX-XXXX-XXXX
  email       text not null,
  company     text,
  seats       int  not null default 1 check (seats >= 1),
  status      text not null default 'active',      -- active|suspended|revoked
  expires_at  timestamptz,                         -- 비우면 무기한
  note        text,
  created_at  timestamptz not null default now()
);

create table if not exists public.ra_activations (
  license_key   text not null
                references public.ra_licenses(license_key) on delete cascade,
  device_fp     text not null,                     -- SHA-256 해시. 원본은 저장하지 않는다
  device_label  text default '',                   -- 사용자가 알아볼 이름 (해제 화면용)
  seat_no       int  not null default 1,
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  app_version   text default '',
  primary key (license_key, device_fp)
);

create index if not exists ra_activations_key_idx
  on public.ra_activations(license_key);
create index if not exists ra_activations_seen_idx
  on public.ra_activations(last_seen_at desc);

alter table public.ra_licenses    enable row level security;
alter table public.ra_activations enable row level security;

-- 서명 키 회전·팩 버전 기록 (선택). Edge Function 이 어떤 팩을 내줬는지 남긴다.
create table if not exists public.ra_pack_releases (
  pack_version text primary key,
  released_at  timestamptz not null default now(),
  note         text
);
alter table public.ra_pack_releases enable row level security;

-- 좌석 사용 현황 — 관리 화면이 쓴다 (service_role 로 조회).
create or replace view public.ra_license_usage as
select l.license_key, l.email, l.company, l.seats, l.status, l.expires_at,
       count(a.device_fp) as used,
       max(a.last_seen_at) as last_seen_at
from public.ra_licenses l
left join public.ra_activations a using (license_key)
group by l.license_key;
