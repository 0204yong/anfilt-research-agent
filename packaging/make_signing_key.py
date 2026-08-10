r"""팩 서명용 Ed25519 키쌍을 만든다 (→ docs/20 라이선스와 복제 방지).

    python packaging\make_signing_key.py

  · **공개키**는 클라이언트에 심는다 (`build.ps1 -PubKey ...` 또는
    `core/licensing.py` 의 `PUBKEY_B64`).
  · **개인키는 저장소에 절대 넣지 않는다.** Supabase 시크릿에만 둔다:

        supabase secrets set RA_PACK_SIGNING_KEY=<아래 출력>

개인키가 유출되면 누구나 유효한 팩을 만들 수 있다 — 그러면 라이선스 전체가
의미를 잃는다. 파일로 남기지 않고 **화면에만** 찍는 이유다.
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    print("=" * 68)
    print("공개키 (클라이언트에 심는다 — 저장소에 들어가도 안전)")
    print("=" * 68)
    print(base64.b64encode(public).decode("ascii"))
    print()
    print("=" * 68)
    print("개인키 (Supabase 시크릿에만. 저장소·채팅·메일에 남기지 말 것)")
    print("=" * 68)
    print(base64.b64encode(seed).decode("ascii"))
    print()
    print("  supabase secrets set RA_PACK_SIGNING_KEY=<위 값>")
    print()
    print("이 창을 닫으면 개인키는 다시 볼 수 없습니다. 잃어버리면 새로 만들고")
    print("클라이언트의 공개키도 함께 갈아야 합니다(구버전은 활성화가 막힙니다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
