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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass


def build(version: str = "") -> dict:
    pack = json.loads((ROOT / "core" / "prompts" / "pack.json")
                      .read_text(encoding="utf-8"))
    seed_dir = ROOT / "vault_seed"
    seed = {
        p.relative_to(seed_dir).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(seed_dir.rglob("*.md"))
    }
    if not seed:
        raise SystemExit("vault_seed/ 에서 시드 노트를 찾지 못했습니다.")
    pack["seed"] = seed
    if version:
        pack["pack_version"] = version
    elif (ROOT / "packaging" / "VERSION").exists():
        pack["pack_version"] = (ROOT / "packaging" / "VERSION") \
            .read_text(encoding="utf-8").strip()
    return pack


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
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n저장: {args.out}")
        print("  supabase secrets set RA_PACK_JSON=\"$(cat %s)\"" % args.out)
        print("  ⚠️ 넣은 뒤 이 파일을 지우세요 — 저장소에 남기면 안 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
