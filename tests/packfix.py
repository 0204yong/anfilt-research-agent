"""시험용 — 팩이 갖춰진 상태를 만든다.

팩 원본은 저장소에 없다. 비공개 저장소 `anfilt-pack` 으로 옮겼다
(→ docs/26 팩을 저장소 밖으로). 실제 앱은 그것을 **서버에서 인출**하지만,
시험은 네트워크를 타지 않아야 하므로 원본을 직접 끼워 넣는다.

    from packfix import install_pack
    install_pack()          # 없으면 안내하고 종료(2)

이걸 부르지 않으면 "라이선스 없는 정식판"을 시험하는 셈이 되어, 정작 보려던
것 대신 팩 부재만 확인하게 된다.
"""
import json
import sys
from pathlib import Path


def install_pack(required: bool = True):
    """팩(시드 포함)을 packs 캐시에 끼운다. 반환: 원본 폴더 또는 None."""
    from core import packs
    src = packs.find_pack_dir()
    if src is None:
        if required:
            print("팩 원본을 찾지 못했습니다 — 아래를 받아 두고 다시 실행하세요:\n"
                  "  git clone https://github.com/0204yong/anfilt-pack.git\n"
                  "다른 곳에 뒀다면 RA_PACK_DIR 환경변수로 알려 주세요.")
            sys.exit(2)
        return None
    seed_dir = src / "vault_seed"
    packs._cache = dict(
        json.loads((src / "prompts" / "pack.json").read_text(encoding="utf-8")),
        seed={p.relative_to(seed_dir).as_posix(): p.read_text(encoding="utf-8")
              for p in sorted(seed_dir.rglob("*.md"))},
    )
    return src


def pack_json_text() -> str:
    """서명 시험용 — 팩 본문을 **바이트 그대로**."""
    from core import packs
    src = packs.find_pack_dir()
    if src is None:
        return ""
    return (src / "prompts" / "pack.json").read_text(encoding="utf-8")


__all__ = ["install_pack", "pack_json_text", "Path"]
