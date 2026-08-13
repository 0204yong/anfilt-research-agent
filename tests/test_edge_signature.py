"""배포 전 검증 — **Edge Function 의 서명을 클라이언트가 받아들이는가.**

실행:  python tests/test_edge_signature.py      (Node 22+ 필요)

여기가 어긋나면 증상은 **"모든 고객의 활성화 실패"** 인데, 서버를 띄우기 전에는
드러나지 않는다. 그래서 실제 서명 코드(`supabase/functions/license/sign.ts`)를
Node 로 그대로 불러, 파이썬 클라이언트가 검증에 성공하는지 본다.

Deno 와 파이썬은 문자열·바이트 처리가 미묘하게 다르다. 그 미묘함이 실제로
문제가 되는지 **추측하지 않고 확인한다** — 한글·이모지·따옴표가 든 팩으로.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

_SANDBOX = Path(tempfile.mkdtemp(prefix="ra-edge-"))
os.environ["APPDATA"] = str(_SANDBOX / "appdata")
os.environ["RA_EDITION"] = "installed"
os.environ["RA_LICENSE_SERVER"] = "http://127.0.0.1:1"   # 시험 모드(공개키 우회 허용)

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from core import licensing  # noqa: E402

_fails, _checks = [], 0


def check(cond, label):
    global _checks
    _checks += 1
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        _fails.append(label)


def node_sign(pack_text, version, expires, fp, seed_b64):
    """실제 sign.ts 를 Node 로 불러 서명 하나를 받아 온다."""
    proc = subprocess.run(
        ["node", str(ROOT / "tests" / "edge_sign_driver.mjs")],
        input=json.dumps({
            "packText": pack_text, "packVersion": version,
            "expiresAt": expires, "deviceFp": fp, "seedB64": seed_b64,
        }).encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-800:])
    return json.loads(proc.stdout.decode("utf-8"))


# ------------------------------------------------------------------

if shutil.which("node") is None:
    print("Node 가 없어 건너뜁니다 (배포 전 반드시 한 번은 돌려야 합니다).")
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    sys.exit(0)

priv = Ed25519PrivateKey.generate()
seed = priv.private_bytes(encoding=serialization.Encoding.Raw,
                          format=serialization.PrivateFormat.Raw,
                          encryption_algorithm=serialization.NoEncryption())
seed_b64 = base64.b64encode(seed).decode("ascii")
pub_b64 = base64.b64encode(priv.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw)).decode("ascii")
os.environ[licensing.PUBKEY_ENV] = pub_b64

FP = "a" * 64
EXPIRES = "2026-09-09T00:00:00+00:00"

print("== Edge Function 서명 ↔ 파이썬 클라이언트 검증")

try:
    out = node_sign('{"pack_version":"1.0","prompts":{}}', "1.0", EXPIRES, FP, seed_b64)
except RuntimeError as e:
    print(f"  FAIL Node 실행 실패:\n{e}")
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    sys.exit(1)

check(licensing.verify_signature('{"pack_version":"1.0","prompts":{}}', "1.0",
                                 EXPIRES, FP, out["signature"]) is True,
      "**서버가 만든 서명을 클라이언트가 받아들인다** (기본형)")

# 서명 대상 문자열이 양쪽에서 같은가 — 어긋나면 위와 같이 통째로 실패한다
py_msg = licensing.signature_message('{"pack_version":"1.0","prompts":{}}',
                                     "1.0", EXPIRES, FP)
check(py_msg.decode("utf-8") == out["digestMessage"],
      "서명 대상 문자열이 양쪽에서 동일하다")

# 진짜 팩으로 — 한글·이모지·따옴표·중괄호가 전부 들어 있다.
# 팩 원본은 저장소 밖(비공개 anfilt-pack)에 있다 (→ docs/26).
from core import packs  # noqa: E402

_pack_dir = packs.find_pack_dir()          # 에디션과 무관하게 원본만 찾는다
if _pack_dir is None:
    print("  팩 원본을 찾지 못해 실제 팩 검증을 건너뜁니다 "
          "(git clone anfilt-pack 후 다시 실행하세요).")
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    sys.exit(0)
real = (_pack_dir / "prompts" / "pack.json").read_text(encoding="utf-8")
out2 = node_sign(real, "0.1.0", EXPIRES, FP, seed_b64)
check(licensing.verify_signature(real, "0.1.0", EXPIRES, FP,
                                 out2["signature"]) is True,
      f"**실제 팩({len(real)//1024}KB · 한글 포함)도 검증 통과**")
check(base64.b64decode(out2["packB64"]).decode("utf-8") == real,
      "pack_b64 를 디코딩하면 원본 바이트가 그대로 나온다")

# 비ASCII 가 몰린 최악의 경우
nasty = json.dumps({
    "pack_version": "1.0", "prompts": {"k": "한글 · 이모지 🚀 · \"따옴표\" · {중괄호}"
                                       " · 탭\t줄바꿈\n · 백슬래시 \\"},
}, ensure_ascii=False)
out3 = node_sign(nasty, "1.0", EXPIRES, FP, seed_b64)
check(licensing.verify_signature(nasty, "1.0", EXPIRES, FP,
                                 out3["signature"]) is True,
      "한글·이모지·제어문자가 섞여도 통과")

# 기기 지문이 다르면 반드시 실패해야 한다 (바인딩의 핵심)
check(licensing.verify_signature(real, "0.1.0", EXPIRES, "b" * 64,
                                 out2["signature"]) is False,
      "**다른 기기 지문으로는 검증이 실패한다** (바인딩이 실제로 걸린다)")
check(licensing.verify_signature(real, "0.1.0", "2099-01-01T00:00:00+00:00",
                                 FP, out2["signature"]) is False,
      "만료 시각을 바꾸면 검증이 실패한다")
check(licensing.verify_signature(real + " ", "0.1.0", EXPIRES, FP,
                                 out2["signature"]) is False,
      "팩을 한 바이트만 바꿔도 검증이 실패한다")

# 다른 키로 서명한 것은 거부
other = Ed25519PrivateKey.generate()
other_seed = base64.b64encode(other.private_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PrivateFormat.Raw,
    encryption_algorithm=serialization.NoEncryption())).decode("ascii")
out4 = node_sign(real, "0.1.0", EXPIRES, FP, other_seed)
check(licensing.verify_signature(real, "0.1.0", EXPIRES, FP,
                                 out4["signature"]) is False,
      "다른 서명 키로 만든 팩은 거부된다")

print(f"\n{'=' * 60}")
if _fails:
    print(f"실패 {len(_fails)}/{_checks}")
    for f in _fails:
        print(f"  - {f}")
else:
    print(f"전부 통과 — {_checks}항목")
shutil.rmtree(_SANDBOX, ignore_errors=True)
sys.exit(1 if _fails else 0)
