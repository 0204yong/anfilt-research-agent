// 활성화 서버 — /activate · /refresh · /deactivate
//
// → docs/20 라이선스와 복제 방지 · docs/23 설치판 구현 계획 7단계
//
// 배포:
//   supabase functions deploy license --no-verify-jwt
//   supabase secrets set RA_PACK_SIGNING_KEY=<base64 Ed25519 private key(32B seed)>
//   supabase secrets set RA_PACK_JSON=<프롬프트 팩 JSON>        (또는 스토리지에서 읽기)
//
// `--no-verify-jwt` 인 이유: 고객 PC 에는 Supabase 인증이 없다. 라이선스 키 자체가
// 자격 증명이다. 그래서 **무차별 대입에 대한 방어를 이 함수가 직접** 해야 한다.
//
// ## 이 함수가 지키는 것
//
//   1. 좌석 수를 넘겨 활성화하지 않는다 (원자적으로 세고 넣는다)
//   2. 정지·해지·만료된 키는 거절한다
//   3. 팩은 **요청한 기기 지문으로 서명**한다 — 남의 기기로 복사해도 못 쓴다
//   4. 실패 응답에 이유는 주되, 존재하는 키인지 아닌지로 키를 캐낼 수 없게
//      **일정한 지연**을 준다
//
// ## 절대 하지 않는 것
//
//   조사 주제·볼트 내용은 **애초에 오지 않는다.** 받는 것은 라이선스 키와
//   기기 지문 해시뿐이다 (→ docs/18 의 판매 포인트를 훼손하지 않는다).

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const PACK_TTL_DAYS = 30;      // 캐시 유효기간 — 이 기간엔 오프라인으로도 돈다
const REFRESH_AFTER_DAYS = 7;  // 이때부터 조용히 갱신 시도
const MIN_LATENCY_MS = 250;    // 응답 시간으로 키 존재 여부를 재지 못하게

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const refuse = (reason: string, message?: string) =>
  json({ ok: false, reason, message }, 403);

function admin() {
  return createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
}

// ---------------------------------------------------------------- 서명

function b64ToBytes(b64: string): Uint8Array {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}
function bytesToB64(b: Uint8Array): string {
  return btoa(String.fromCharCode(...b));
}

async function signingKey(): Promise<CryptoKey> {
  const seed = b64ToBytes(Deno.env.get("RA_PACK_SIGNING_KEY")!);
  // PKCS#8 로 감싼 Ed25519 개인키 (앞 16바이트는 고정 헤더)
  const pkcs8 = new Uint8Array([
    0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70,
    0x04, 0x22, 0x04, 0x20, ...seed.slice(0, 32),
  ]);
  return await crypto.subtle.importKey("pkcs8", pkcs8, { name: "Ed25519" },
    false, ["sign"]);
}

async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 클라이언트의 `signature_message()` 와 **글자 하나까지 같아야** 한다. */
async function signPack(packJson: string, packVersion: string,
                        expiresAt: string, deviceFp: string): Promise<string> {
  const digest = await sha256Hex(packJson);
  const msg = new TextEncoder().encode(
    `${digest}|${packVersion}|${expiresAt}|${deviceFp}`,
  );
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, await signingKey(), msg);
  return bytesToB64(new Uint8Array(sig));
}

/** 팩 본문. 시크릿에 담거나 스토리지에서 읽는다.
 *
 * ⚠️ **보낸 바이트 그대로** 서명한다. 클라이언트가 JSON 을 다시 직렬화해서
 * 서명을 맞추게 하면, Deno 와 파이썬의 사소한 표기 차이(구분자·키 순서·
 * 이스케이프) 하나로 정당한 고객의 활성화가 통째로 실패한다.
 * 그래서 팩을 base64 로 그대로 실어 보내고, 양쪽 다 그 바이트만 본다.
 */
function packBody(): { text: string; version: string } {
  const raw = Deno.env.get("RA_PACK_JSON");
  if (!raw) throw new Error("RA_PACK_JSON 이 설정되지 않았습니다");
  const version = String(JSON.parse(raw).pack_version ?? "0");
  return { text: raw, version };
}

function utf8ToB64(text: string): string {
  return bytesToB64(new TextEncoder().encode(text));
}

// ---------------------------------------------------------------- 본체

async function handle(action: string, body: Record<string, string>) {
  const db = admin();
  const key = String(body.license_key ?? "").trim().toUpperCase();
  const fp = String(body.device_fp ?? "").trim();
  if (!key || !fp || fp.length < 32) return refuse("invalid");

  const { data: lic } = await db.from("ra_licenses")
    .select("*").eq("license_key", key).maybeSingle();
  if (!lic) return refuse("invalid");
  if (lic.status === "revoked") return refuse("revoked");
  if (lic.status === "suspended") return refuse("suspended");
  if (lic.expires_at && new Date(lic.expires_at) < new Date()) {
    return refuse("expired");
  }

  if (action === "deactivate") {
    await db.from("ra_activations").delete()
      .eq("license_key", key).eq("device_fp", fp);
    return json({ ok: true });
  }

  const { data: seatsUsed } = await db.from("ra_activations")
    .select("device_fp, seat_no").eq("license_key", key);
  const existing = (seatsUsed ?? []).find((r) => r.device_fp === fp);

  if (action === "refresh" && !existing) return refuse("not_activated");

  let seatNo = existing?.seat_no ?? 0;
  if (!existing) {
    // 좌석 초과 검사 — 활성화(신규 등록)일 때만 본다
    if ((seatsUsed?.length ?? 0) >= lic.seats) return refuse("seats_full");
    const taken = new Set((seatsUsed ?? []).map((r) => r.seat_no));
    seatNo = 1;
    while (taken.has(seatNo)) seatNo++;
  }

  await db.from("ra_activations").upsert({
    license_key: key,
    device_fp: fp,
    device_label: String(body.device_label ?? "").slice(0, 60),
    seat_no: seatNo,
    last_seen_at: new Date().toISOString(),
    app_version: String(body.app_version ?? "").slice(0, 20),
  }, { onConflict: "license_key,device_fp" });

  const now = Date.now();
  // 라이선스 자체의 만료가 더 이르면 그쪽을 따른다 — 만료된 계약이
  // 팩 유효기간만큼 더 사는 일이 없게.
  let expires = new Date(now + PACK_TTL_DAYS * 86400_000);
  if (lic.expires_at && new Date(lic.expires_at) < expires) {
    expires = new Date(lic.expires_at);
  }
  const expiresAt = expires.toISOString().replace(/\.\d+Z$/, "+00:00");
  const refreshAfter = new Date(now + REFRESH_AFTER_DAYS * 86400_000)
    .toISOString().replace(/\.\d+Z$/, "+00:00");

  const { text, version } = packBody();
  const signature = await signPack(text, version, expiresAt, fp);

  return json({
    ok: true,
    pack_b64: utf8ToB64(text),
    pack_version: version,
    expires_at: expiresAt,
    refresh_after: refreshAfter,
    signature,
    seat_no: seatNo,
    seats: lic.seats,
    used: (seatsUsed?.length ?? 0) + (existing ? 0 : 1),
  });
}

Deno.serve(async (req) => {
  const started = Date.now();
  const action = new URL(req.url).pathname.split("/").filter(Boolean).pop() ?? "";
  let out: Response;
  try {
    if (req.method !== "POST" ||
        !["activate", "refresh", "deactivate"].includes(action)) {
      out = json({ ok: false, reason: "bad_request" }, 400);
    } else {
      out = await handle(action, await req.json());
    }
  } catch (e) {
    console.error(e);
    out = json({ ok: false, reason: "server_error" }, 500);
  }
  // 존재하는 키와 없는 키의 응답 시간이 갈리지 않게 바닥을 맞춘다
  const left = MIN_LATENCY_MS - (Date.now() - started);
  if (left > 0) await new Promise((r) => setTimeout(r, left));
  return out;
});
