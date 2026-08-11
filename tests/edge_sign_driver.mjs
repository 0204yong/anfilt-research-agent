// Edge Function 의 서명 코드를 **그대로** 불러 서명 하나를 만든다.
//
// `tests/test_edge_signature.py` 가 부른다. Deno 없이 Node 로 검증하기 위한
// 얇은 껍데기다 — Node 22+ 는 .ts 를 타입만 벗겨 바로 실행한다.
//
// 입력(JSON, stdin): {packText, packVersion, expiresAt, deviceFp, seedB64}
// 출력(JSON, stdout): {signature, packB64, digestMessage}

import { readFileSync } from "node:fs";
import { signPack, utf8ToB64, sha256Hex, signatureMessage }
  from "../supabase/functions/license/sign.ts";

const input = JSON.parse(readFileSync(0, "utf-8"));
const digest = await sha256Hex(input.packText);

process.stdout.write(JSON.stringify({
  signature: await signPack(input.packText, input.packVersion,
                            input.expiresAt, input.deviceFp, input.seedB64),
  packB64: utf8ToB64(input.packText),
  digestMessage: signatureMessage(input.packText, input.packVersion,
                                  input.expiresAt, input.deviceFp, digest),
}));
