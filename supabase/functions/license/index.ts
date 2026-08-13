// 활성화 서버 — /activate · /refresh · /deactivate
//
// → docs/20 라이선스와 복제 방지 · docs/23 설치판 구현 계획 7단계
//
// 배포 (자세한 절차는 docs/24 라이선스 서버 배포):
//   supabase functions deploy license --no-verify-jwt
//   supabase secrets set RA_PACK_SIGNING_KEY=<base64 Ed25519 개인키(32바이트 시드)>
//   팩은 비공개 스토리지 버킷 `license-packs/pack.json` 에 올린다
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

// 서명은 별도 파일로 뺐다 — 클라이언트와 바이트가 맞는지 **배포 전에** 시험하기
// 위해서다 (`tests/test_edge_signature.py` 가 Node 로 이 파일을 그대로 부른다).
import { signPack, utf8ToB64 } from "./sign.ts";

const PACK_TTL_DAYS = 30;      // 캐시 유효기간 — 이 기간엔 오프라인으로도 돈다
const REFRESH_AFTER_DAYS = 7;  // 이때부터 조용히 갱신 시도
// 응답 시간으로 키 존재 여부를 재지 못하게 하는 **바닥**.
//
// 250ms 로는 부족했다. 실측(2026-08-13) 결과 없는 키는 0.4초, 있는 키는 팩을
// 읽고 서명하느라 1.0~2.0초였다 — 시간만 재면 존재하는 키를 골라낼 수 있다.
// 바닥을 느린 쪽 위로 올려 그 차이를 지운다.
//
// 활성화는 기기당 한 번, 갱신은 7일에 한 번이다. 2초는 사람이 기다리는
// 화면이 아니므로 정직하게 느리게 만드는 편이 낫다.
const MIN_LATENCY_MS = 2000;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const refuse = (reason: string, message?: string) =>
  json({ ok: false, reason, message }, 403);

/** 설정이 빠졌을 때 **무엇이 빠졌는지** 말하게 하는 오류.
 *
 * `Deno.env.get(...)!` 는 컴파일러를 달랠 뿐 런타임에는 아무 것도 보장하지
 * 않는다. 값이 없으면 createClient 가 "supabaseKey is required" 로 던지고,
 * 우리 catch 는 그것을 `server_error` 한 마디로 뭉갠다 — 배포자는 원인을
 * 짐작해야 한다. 이름을 말하게 한다. 이건 비밀이 아니라 설정 이름이다.
 */
class ConfigError extends Error {
  constructor(public missing: string) { super(`설정 없음: ${missing}`); }
}

function needEnv(...names: string[]): string {
  for (const n of names) {
    const v = Deno.env.get(n);
    if (v) return v;
  }
  throw new ConfigError(names.join(" 또는 "));
}

function admin() {
  return createClient(
    needEnv("SUPABASE_URL"),
    // 새 API 키 체계로 옮긴 프로젝트는 legacy service_role 이 없을 수 있다.
    needEnv("SUPABASE_SERVICE_ROLE_KEY", "SB_SECRET_KEY", "SUPABASE_SECRET_KEY"),
    { auth: { persistSession: false } },
  );
}

/** 팩 본문.
 *
 * ⚠️ **보낸 바이트 그대로** 서명한다. 클라이언트가 JSON 을 다시 직렬화해서
 * 서명을 맞추게 하면, Deno 와 파이썬의 사소한 표기 차이(구분자·키 순서·
 * 이스케이프) 하나로 정당한 고객의 활성화가 통째로 실패한다.
 * 그래서 팩을 base64 로 그대로 실어 보내고, 양쪽 다 그 바이트만 본다.
 *
 * 팩은 **비공개 스토리지 버킷**(`license-packs/pack.json`)에서 읽는다.
 * 환경변수에 담지 않는 이유: 프롬프트·시드를 합치면 50KB 가 넘어 시크릿에
 * 넣기에 부담스럽고, 무엇보다 **팩을 갈 때마다 함수를 다시 배포해야** 한다.
 * 스토리지에 두면 파일 하나만 갈아 끼우면 되고, 그게 팩 회전(L3)의 전제다.
 *
 * `RA_PACK_JSON` 이 있으면 그쪽을 쓴다 — 로컬 시험용 우회다.
 */
let _packCache: { text: string; version: string; at: number } | null = null;
const PACK_CACHE_MS = 60_000;

async function packBody(): Promise<{ text: string; version: string }> {
  const override = Deno.env.get("RA_PACK_JSON");
  if (override) {
    return { text: override, version: String(JSON.parse(override).pack_version ?? "0") };
  }
  if (_packCache && Date.now() - _packCache.at < PACK_CACHE_MS) {
    return { text: _packCache.text, version: _packCache.version };
  }
  const bucket = Deno.env.get("RA_PACK_BUCKET") || "license-packs";
  const object = Deno.env.get("RA_PACK_OBJECT") || "pack.json";
  const { data, error } = await admin().storage.from(bucket).download(object);
  if (error || !data) {
    // 팩 미업로드는 배포 절차를 빠뜨린 것이다 — 짐작하게 두지 않는다.
    throw new ConfigError(`스토리지 ${bucket}/${object} (${error?.message ?? "없음"})`);
  }
  const text = await data.text();
  const version = String(JSON.parse(text).pack_version ?? "0");
  _packCache = { text, version, at: Date.now() };
  return { text, version };
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

  const { text, version } = await packBody();
  const signature = await signPack(text, version, expiresAt, fp,
                                   needEnv("RA_PACK_SIGNING_KEY"));

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
    // 배포자가 고칠 수 있는 오류는 **무엇을 고쳐야 하는지** 말해 준다.
    // 고객 정보가 아니라 서버 설정 이름이므로 드러나도 해가 없고,
    // 이게 없으면 로그를 볼 수 있는 사람만 배포를 끝낼 수 있다.
    // 반대로 그 밖의 예외는 **아무 것도 말하지 않는다** — 공개 엔드포인트로
    // 서버 내부 사정을 흘리지 않는다. 그건 로그에만 남는다.
    out = (e instanceof ConfigError)
      ? json({ ok: false, reason: "config_error", missing: e.missing }, 500)
      : json({ ok: false, reason: "server_error" }, 500);
  }
  // 존재하는 키와 없는 키의 응답 시간이 갈리지 않게 바닥을 맞춘다.
  // 바닥을 넘긴 요청(콜드 스타트 등)은 여전히 튄다 — 완전한 상수 시간은
  // 아니다. 무차별 대입의 진짜 방벽은 키 자체의 경우의 수이고, 이건
  // 그 위에 얹는 겹이다.
  const left = MIN_LATENCY_MS - (Date.now() - started);
  if (left > 0) await new Promise((r) => setTimeout(r, left));
  return out;
});
