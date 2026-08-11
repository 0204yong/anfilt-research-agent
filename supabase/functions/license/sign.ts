// 팩 서명 — 클라이언트(`core/licensing.py`)와 **바이트 하나까지 맞아야** 하는 부분.
//
// → docs/20 라이선스와 복제 방지 · docs/23 7단계
//
// 이 파일이 index.ts 에서 떨어져 나온 이유는 하나다: **시험할 수 있게 하려고.**
// 여기가 어긋나면 증상은 "모든 고객의 활성화 실패" 인데, 배포 전에는 서버를
// 띄우지 않으면 알 수 없다. Deno 전용 API 를 쓰지 않으므로 Node 에서도 그대로
// 불러 검증할 수 있다 (`tests/test_edge_signature.py`).

export function b64ToBytes(b64: string): Uint8Array {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

export function bytesToB64(b: Uint8Array): string {
  let s = "";
  for (const x of b) s += String.fromCharCode(x);
  return btoa(s);
}

export function utf8ToB64(text: string): string {
  return bytesToB64(new TextEncoder().encode(text));
}

/** 32바이트 시드를 PKCS#8 로 감싼다 (RFC 8410 고정 헤더 16바이트 + 시드). */
export async function importSeed(seedB64: string): Promise<CryptoKey> {
  const seed = b64ToBytes(seedB64);
  if (seed.length < 32) throw new Error("서명 키는 32바이트 시드여야 합니다");
  const pkcs8 = new Uint8Array([
    0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70,
    0x04, 0x22, 0x04, 0x20, ...seed.slice(0, 32),
  ]);
  return await crypto.subtle.importKey("pkcs8", pkcs8, { name: "Ed25519" },
    false, ["sign"]);
}

export async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * 서명 대상 문자열. 클라이언트의 `licensing.signature_message()` 와 **같아야** 한다.
 *
 *     sha256(팩 본문) | 팩 버전 | 만료 | 기기 지문
 *
 * 기기 지문이 들어가는 것이 핵심이다 — 남의 기기용 팩은 검증에서 걸린다.
 */
export function signatureMessage(packJson: string, packVersion: string,
                                 expiresAt: string, deviceFp: string,
                                 digest: string): string {
  return `${digest}|${packVersion}|${expiresAt}|${deviceFp}`;
}

/** 팩을 **보낸 바이트 그대로** 서명한다 (JSON 재직렬화 금지 — docs/23 7단계). */
export async function signPack(packJson: string, packVersion: string,
                               expiresAt: string, deviceFp: string,
                               seedB64: string): Promise<string> {
  const digest = await sha256Hex(packJson);
  const msg = new TextEncoder().encode(
    signatureMessage(packJson, packVersion, expiresAt, deviceFp, digest),
  );
  const key = await importSeed(seedB64);
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, key, msg);
  return bytesToB64(new Uint8Array(sig));
}
