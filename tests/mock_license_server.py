"""활성화 서버의 시험용 대역 — Edge Function 과 **같은 계약**을 구현한다.

`tests/test_licensing.py` 가 쓴다. 배포물이 아니다.

Edge Function(`supabase/functions/license/index.ts`)과 다른 점은 저장소뿐이다
(Supabase 대신 메모리). 좌석 계산·거절 사유·서명 대상은 같게 맞춘다 —
여기서만 통과하고 실제 서버에서 막히면 시험의 의미가 없다.
"""
import base64
import hashlib
import http.server
import json
import threading


class LicenseServer:
    def __init__(self, pack: dict, licenses: dict, priv_seed: bytes):
        self.pack_text = json.dumps(pack, ensure_ascii=False)
        self.pack_version = str(pack.get("pack_version") or "0")
        self.licenses = licenses          # key -> {"seats","status","expires_at"}
        self.activations = {}             # key -> {fp: {"seat_no","label"}}
        self.calls = []                   # 무엇이 오갔는지 시험이 들여다본다
        self.fail_next = 0                # >0 이면 그만큼 500 을 낸다 (장애 흉내)
        self._priv = priv_seed
        self._httpd = None
        self.port = 0

    # ------------------------------------------------------------ 서명
    def _sign(self, expires_at: str, fp: str) -> str:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        digest = hashlib.sha256(self.pack_text.encode("utf-8")).hexdigest()
        msg = f"{digest}|{self.pack_version}|{expires_at}|{fp}".encode("utf-8")
        key = Ed25519PrivateKey.from_private_bytes(self._priv)
        return base64.b64encode(key.sign(msg)).decode("ascii")

    # ------------------------------------------------------------ 처리
    def handle(self, action: str, body: dict):
        self.calls.append((action, dict(body)))
        if self.fail_next > 0:
            self.fail_next -= 1
            return 500, {"ok": False, "reason": "server_error"}

        key = str(body.get("license_key") or "").upper()
        fp = str(body.get("device_fp") or "")
        if not key or len(fp) < 32:
            return 403, {"ok": False, "reason": "invalid"}
        lic = self.licenses.get(key)
        if not lic:
            return 403, {"ok": False, "reason": "invalid"}
        if lic.get("status") in ("revoked", "suspended"):
            return 403, {"ok": False, "reason": lic["status"]}

        seats = self.activations.setdefault(key, {})
        if action == "deactivate":
            seats.pop(fp, None)
            return 200, {"ok": True}

        existing = seats.get(fp)
        if action == "refresh" and not existing:
            return 403, {"ok": False, "reason": "not_activated"}
        if not existing:
            if len(seats) >= int(lic.get("seats", 1)):
                return 403, {"ok": False, "reason": "seats_full"}
            taken = {v["seat_no"] for v in seats.values()}
            n = 1
            while n in taken:
                n += 1
            seats[fp] = {"seat_no": n, "label": body.get("device_label", "")}
            existing = seats[fp]

        expires_at = lic.get("pack_expires") or "2099-01-01T00:00:00+00:00"
        return 200, {
            "ok": True,
            "pack_b64": base64.b64encode(
                self.pack_text.encode("utf-8")).decode("ascii"),
            "pack_version": self.pack_version,
            "expires_at": expires_at,
            "refresh_after": lic.get("refresh_after")
            or "2098-01-01T00:00:00+00:00",
            "signature": self._sign(expires_at, fp),
            "seat_no": existing["seat_no"],
            "seats": int(lic.get("seats", 1)),
            "used": len(seats),
        }

    # ------------------------------------------------------------ 기동
    def start(self) -> str:
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                       # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    body = {}
                action = self.path.rstrip("/").split("/")[-1]
                if action not in ("activate", "refresh", "deactivate"):
                    status, out = 400, {"ok": False, "reason": "bad_request"}
                else:
                    status, out = server.handle(action, body)
                data = json.dumps(out).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
