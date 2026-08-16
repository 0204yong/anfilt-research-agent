r"""활성화 서버가 내줄 **완성된 팩**을 만든다 (→ docs/20 라이선스와 복제 방지).

    python packaging\make_pack.py                 # 화면에 요약만
    python packaging\make_pack.py --out pack.json # 파일로

`core/prompts/pack.json`(프롬프트·스키마·구성표)에 **ESG 시드 온톨로지 35노트**를
`seed` 로 합친다. 시드도 이 제품의 값어치라 패키지에서 빼고 활성화 때 내려보낸다.

출력을 Supabase 시크릿에 넣는다.

    supabase secrets set RA_PACK_JSON="$(cat pack.json)"

⚠️ 이 파일은 **팔지 않은 사람에게 가면 안 되는 것**이다. 저장소·채팅·메일에
남기지 말고, 넣은 뒤 지운다.

팩 버전은 릴리스마다 올린다 — 어렵게 한 번 빼내도 다음 패치면 낡는다
(docs/20 의 L3). `--version` 으로 지정하지 않으면 `packaging/VERSION` 을 쓴다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


def pack_dir() -> Path:
    """팩 원본이 있는 곳 — **이 저장소가 아니다.**

    앱 저장소는 체험판 배포 때문에 공개다. 팩이 거기 있으면 누구나 받아 가고,
    그러면 라이선스가 지킬 것이 없다 (→ docs/26 팩을 저장소 밖으로).
    원본은 비공개 저장소 `anfilt-pack` 에 있다.
    """
    env = os.environ.get("RA_PACK_DIR")
    for cand in ([Path(env)] if env else []) + [
        ROOT.parent / "anfilt-pack",              # 나란히 받은 경우
        Path(r"C:\ANFILT_AI\anfilt-pack"),        # 이 PC 의 기본 위치
    ]:
        if (cand / "prompts" / "pack.json").exists():
            return cand
    raise SystemExit(
        "팩 원본을 찾지 못했습니다. 비공개 저장소를 받아 두세요:\n"
        "  git clone https://github.com/0204yong/anfilt-pack.git\n"
        "다른 곳에 뒀다면 RA_PACK_DIR 로 알려 주세요."
    )


def build(version: str = "") -> dict:
    src = pack_dir()
    pack = json.loads((src / "prompts" / "pack.json").read_text(encoding="utf-8"))
    seed_dir = src / "vault_seed"
    seed = {
        p.relative_to(seed_dir).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(seed_dir.rglob("*.md"))
    }
    if not seed:
        raise SystemExit(f"{seed_dir} 에서 시드 노트를 찾지 못했습니다.")
    pack["seed"] = seed
    # **앱 버전과 팩 버전은 다른 것이다.**
    #
    # 예전에는 여기서 `packaging/VERSION`(앱 버전)으로 무조건 덮어썼다. 그러면
    # 팩을 갈아도 버전이 앱 버전에 묶여 영원히 안 움직인다 — 팩 회전(→ docs/20 L3)은
    # **앱을 다시 내지 않고** 프롬프트만 가는 장치인데 그 표식이 사라지는 셈이다.
    #
    # 실제로 2026-08-15 팩 0.2.0 을 올리고도 '구성요소 0.1.0' 이 떠서, 업로드가
    # 됐는지 안 됐는지 판별할 수 없었다. 고객 지원에서도 같은 일이 난다.
    if version:
        pack["pack_version"] = version
    elif not str(pack.get("pack_version") or "").strip():
        # 원본이 버전을 안 적었을 때만 앱 버전을 빌린다
        vf = ROOT / "packaging" / "VERSION"
        if vf.exists():
            pack["pack_version"] = vf.read_text(encoding="utf-8").strip()
    return pack


# 서버가 찾는 오브젝트 이름. 여기와 supabase/functions/license/index.ts 의
# `RA_PACK_OBJECT` 기본값이 **같아야** 한다.
PACK_OBJECT = "pack.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="쓸 파일 경로 (생략하면 요약만 출력)")
    ap.add_argument("--version", default="", help="팩 버전 (생략 시 packaging/VERSION)")
    args = ap.parse_args()

    pack = build(args.version)
    text = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))

    print(f"pack_version : {pack.get('pack_version')}")
    print(f"prompts      : {len(pack.get('prompts') or {})}")
    print(f"schemas      : {len(pack.get('schemas') or {})}")
    print(f"config       : {len(pack.get('config') or {})}")
    print(f"seed         : {len(pack.get('seed') or {})} 노트")
    print(f"크기         : {len(text.encode('utf-8')) / 1024:.0f} KB")

    if args.out:
        # **파일 이름은 언제나 pack.json 이어야 한다.** 서버가 그 이름 하나만
        # 찾는다 (`RA_PACK_OBJECT` 기본값, → supabase/functions/license/index.ts).
        #
        # 예전에는 `--out pack-0.3.0.json` 처럼 아무 이름으로 굽고 "올릴 때
        # 이름을 바꾸세요" 라고 안내했다. 2026-08-16 에 그대로 사고가 났다 —
        # 버킷에 `pack-0.3.0.json` 만 남아 서버가 팩을 못 찾았고, 새 활성화가
        # 통째로 막혔다. 사람에게 이름 바꾸기를 시키는 절차는 언젠가 반드시
        # 어긋난다. 그러니 **바꿀 것이 없게** 굽는다.
        out = Path(args.out)
        if out.is_dir():
            out = out / PACK_OBJECT
        elif out.name != PACK_OBJECT:
            print(f"\n⚠️ 이름을 {out.name} → {PACK_OBJECT} 로 바로잡습니다 "
                  f"(서버는 이 이름만 찾습니다).")
            out = out.with_name(PACK_OBJECT)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"\n저장: {out}")
        print("  → Supabase 대시보드 → Storage → license-packs 버킷에")
        print(f"     **{PACK_OBJECT} 를 그대로** 올립니다 (비공개 버킷).")
        print("     같은 이름이 이미 있으면 먼저 지우세요 — Supabase 는 덮어쓰지 않고")
        print("     'pack (1).json' 을 새로 만듭니다.")
        print("  ⚠️ 올린 뒤 이 파일을 지우세요 — 저장소·동기화 폴더에 남기면 안 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
