// 관리 조작 — 발급 · 좌석 해제 · 정지/해지 (→ docs/27 관리 화면)
//
// 홈페이지의 `/admin/license/` 화면이 여기로 온다. **테이블에 직접 붙지 않는다.**
//
// `ra_licenses` 는 RLS 를 켜고 정책을 하나도 두지 않았다 — 그것이 곧
// "anon 키로는 아무도 못 본다" 였다(→ supabase-ra-license-setup.sql).
// 관리 화면을 만들자고 그 문을 열면(관리자 UID 정책 추가) 라이선스 목록으로
// 가는 **두 번째 경로**가 생긴다. 문은 하나로 두고, 그 문을 지키는 편이 낫다.
//
// 자격은 `RA_ADMIN_TOKEN` 하나다. 브라우저 세션에만 남고(sessionStorage),
// 무차별 대입은 index.ts 의 응답 시간 바닥(2초)이 함께 막는다.
//
// ⚠️ 이 토큰은 **발급 권한**이다 — 체험판 토큰(`RA_TRIAL_TOKEN`)보다 무겁다.
//    새면 남이 라이선스를 찍어낼 수 있다. 둘을 절대 같은 값으로 두지 말 것.

import type { SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";

/** 사람이 불러 주고 받아 적는 키다 — 헷갈리는 글자를 뺀다.
 *  0/O·1/I/L 이 없으면 전화로 불러 줘도 되묻지 않는다. 32글자 × 12자리 = 60비트. */
const ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";

function newKey(): string {
  const raw = crypto.getRandomValues(new Uint8Array(12));
  const chars = [...raw].map((b) => ALPHABET[b % ALPHABET.length]);
  // ANF-XXXX-XXXX-XXXX — 클라이언트의 normalize_key() 가 만드는 모양 그대로.
  return "ANF-" + [0, 4, 8].map((i) => chars.slice(i, i + 4).join("")).join("-");
}

/** 갱신을 허용하는 칸. 화이트리스트로 두는 이유는 단순하다 — 화면이 실수로
 *  (혹은 남이 일부러) `license_key` 나 `created_at` 을 보내도 먹히지 않게. */
const EDITABLE = new Set(["email", "company", "seats", "status", "expires_at", "note"]);
const STATUSES = new Set(["active", "suspended", "revoked"]);

type Json = Record<string, unknown>;

export class AdminError extends Error {}

function need(body: Json, name: string): string {
  const v = String(body[name] ?? "").trim();
  if (!v) throw new AdminError(`${name} 가 비었습니다`);
  return v;
}

/** 만료일: 빈 값이면 무기한(null). `YYYY-MM-DD` 는 그 날 끝까지로 읽는다 —
 *  "2026-12-31 까지"라고 적은 사람이 그날 아침에 잠기면 안 된다. */
function parseExpiry(raw: unknown): string | null {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const d = /^\d{4}-\d{2}-\d{2}$/.test(s) ? new Date(`${s}T23:59:59Z`) : new Date(s);
  if (isNaN(d.getTime())) throw new AdminError(`만료일을 읽을 수 없습니다: ${s}`);
  return d.toISOString();
}

// ---------------------------------------------------------------- 조작

/** 목록 — 라이선스와 활성화 기기를 함께 준다.
 *
 * `ra_license_usage` 뷰를 쓰지 않고 두 번 읽어 JS 에서 붙인다. 화면이
 * 기기 목록(해제 버튼)까지 필요하므로 어차피 활성화 행이 있어야 하고,
 * 그러면 뷰는 같은 것을 한 번 더 세는 셈이다. 요청 한 번에 화면이 완성된다.
 */
async function opList(db: SupabaseClient) {
  const { data: lics, error: e1 } = await db.from("ra_licenses")
    .select("*").order("created_at", { ascending: false });
  if (e1) throw new AdminError(e1.message);
  const { data: acts, error: e2 } = await db.from("ra_activations")
    .select("*").order("seat_no", { ascending: true });
  if (e2) throw new AdminError(e2.message);

  const byKey = new Map<string, Json[]>();
  for (const a of acts ?? []) {
    const list = byKey.get(a.license_key) ?? [];
    // 기기 지문 전체는 화면에 필요 없다. 해제는 이 값으로 하므로 그대로 두되,
    // 사람이 보는 자리에는 앞 8자만 쓴다(→ 화면 쪽에서 자른다).
    list.push(a);
    byKey.set(a.license_key, list);
  }
  return {
    licenses: (lics ?? []).map((l) => ({
      ...l, devices: byKey.get(l.license_key) ?? [],
    })),
  };
}

/** 발급. 키 충돌은 사실상 없지만(60비트), 있으면 조용히 덮어쓰는 대신 다시 뽑는다. */
async function opIssue(db: SupabaseClient, body: Json) {
  const row = {
    email: need(body, "email"),
    company: String(body.company ?? "").trim() || null,
    seats: Math.max(1, Math.min(999, Number(body.seats ?? 1) || 1)),
    status: "active",
    expires_at: parseExpiry(body.expires_at),
    note: String(body.note ?? "").trim() || null,
  };
  for (let i = 0; i < 5; i++) {
    const license_key = newKey();
    const { data, error } = await db.from("ra_licenses")
      .insert({ ...row, license_key }).select().maybeSingle();
    if (!error) return { license: { ...data, devices: [] } };
    if (error.code !== "23505") throw new AdminError(error.message);   // 중복만 재시도
  }
  throw new AdminError("키를 뽑지 못했습니다 (충돌 반복)");
}

/** 수정 — 좌석 수·상태·만료·메모. 좌석을 줄여도 이미 쓰는 기기를 쫓아내지 않는다.
 *  그건 해제 버튼으로 사람이 고를 일이지, 서버가 임의로 고를 일이 아니다.
 *  (초과 상태에서는 새 기기의 활성화만 막힌다 — index.ts 의 seats_full) */
async function opUpdate(db: SupabaseClient, body: Json) {
  const key = need(body, "license_key").toUpperCase();
  const patch: Json = {};
  for (const [k, v] of Object.entries((body.patch ?? {}) as Json)) {
    if (!EDITABLE.has(k)) continue;
    if (k === "expires_at") patch[k] = parseExpiry(v);
    else if (k === "seats") patch[k] = Math.max(1, Math.min(999, Number(v) || 1));
    else if (k === "status") {
      const s = String(v);
      if (!STATUSES.has(s)) throw new AdminError(`알 수 없는 상태: ${s}`);
      patch[k] = s;
    } else patch[k] = String(v ?? "").trim() || null;
  }
  if (!Object.keys(patch).length) throw new AdminError("바꿀 내용이 없습니다");

  const { data, error } = await db.from("ra_licenses")
    .update(patch).eq("license_key", key).select().maybeSingle();
  if (error) throw new AdminError(error.message);
  if (!data) throw new AdminError("그런 라이선스가 없습니다");
  return { license_key: key, patch };
}

/** 좌석 해제 — 고객이 PC 를 바꿨을 때. 기기 쪽 캐시는 만료(최대 30일)까지 살아 있다.
 *  즉시 끊어야 하면 라이선스를 `suspended` 로 두는 것이 맞다(→ 화면 안내 문구). */
async function opRelease(db: SupabaseClient, body: Json) {
  const key = need(body, "license_key").toUpperCase();
  const fp = need(body, "device_fp");
  const { data, error } = await db.from("ra_activations")
    .delete().eq("license_key", key).eq("device_fp", fp).select();
  if (error) throw new AdminError(error.message);
  if (!data?.length) throw new AdminError("그런 기기가 없습니다 (이미 해제됐을 수 있습니다)");
  return { released: data.length };
}

/** 삭제 — **한 번도 쓰지 않은 키**만.
 *
 * 원칙은 "지우지 않는다"(해지로 남긴다)가 맞다. 예외를 하나 둔 이유는,
 * 이메일을 잘못 적고 발급 버튼을 누른 키가 목록에 영원히 남기 때문이다.
 * 그건 감사 기록이 아니라 오타다. 대신 **활성화된 기기가 하나라도 있으면
 * 거절한다** — 고객이 쓰고 있는 계약은 지우는 것이 아니라 해지하는 것이다.
 */
async function opDelete(db: SupabaseClient, body: Json) {
  const key = need(body, "license_key").toUpperCase();
  const { data: acts, error: e1 } = await db.from("ra_activations")
    .select("device_fp").eq("license_key", key);
  if (e1) throw new AdminError(e1.message);
  if (acts?.length) {
    throw new AdminError(
      `기기 ${acts.length}대가 활성화돼 있어 지울 수 없습니다. ` +
      `쓰지 않는 계약이면 '해지' 하세요 (기록이 남습니다).`);
  }
  const { data, error } = await db.from("ra_licenses")
    .delete().eq("license_key", key).select();
  if (error) throw new AdminError(error.message);
  if (!data?.length) throw new AdminError("그런 라이선스가 없습니다");
  return { deleted: key };
}

export async function handleAdmin(db: SupabaseClient, body: Json) {
  const op = String(body.op ?? "");
  switch (op) {
    case "list":    return await opList(db);
    case "issue":   return await opIssue(db, body);
    case "update":  return await opUpdate(db, body);
    case "release": return await opRelease(db, body);
    case "delete":  return await opDelete(db, body);
    default: throw new AdminError(`알 수 없는 작업: ${op}`);
  }
}
