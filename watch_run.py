"""자동 모니터링 실행기 (스케줄러용 CLI).

    python watch_run.py            # 지금 실행할 차례인 감시만 (매시 정각 호출용)
    python watch_run.py --all      # 활성 감시 전부 (시각 조건 무시)
    python watch_run.py --id watch-... --id watch-...
    python watch_run.py --list     # 등록된 감시 목록만 출력
    python watch_run.py --no-notify

Streamlit 없이 도는 진입점이다 — GitHub Actions가 매시 정각에 이 스크립트를
호출한다(→ docs/15). 앱은 접속 중일 때만 살아 있으므로 '매일 정해진 시간'은
앱 바깥의 스케줄러가 담당해야 한다.

환경변수는 .env(로컬) 또는 GitHub Actions Secrets(클라우드)에서 온다.
"""
import argparse
import sys
from pathlib import Path

# ⚠️ **자기 폴더를 sys.path 에 넣는다.** 설치판의 임베디드 파이썬은 `._pth` 로
# `sys.path` 를 고정하므로, 보통의 파이썬과 달리 **스크립트 폴더가 자동으로
# 들어가지 않는다.** 앱은 `streamlit run` 이 대신 넣어 줘서 멀쩡했지만, 작업
# 스케줄러가 부르는 이 진입점은 `No module named 'core'` 로 매시 조용히 죽었다
# (8단계 실측). 콘솔이 없는 `pythonw.exe` 라 아무 흔적도 남지 않았다.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import store as store_mod, watch as W  # noqa: E402  (load_dotenv 이후)
from core.watch_runner import run_due_watches  # noqa: E402


def _print_list(store) -> int:
    watches = store.watch_list()
    if not watches:
        print("등록된 감시가 없습니다.")
        return 0
    print(f"감시 {len(watches)}건:")
    for w in watches:
        state = "ON " if w.get("enabled") else "OFF"
        print(
            f"  [{state}] {w['watch_id']}  {w['name']}\n"
            f"        {w['kind']} · {w['target']}\n"
            f"        시각 {w.get('hours')} KST · 알림 {w.get('notify')} · "
            f"최근 {w.get('last_checked_at') or '없음'} — {w.get('last_status') or ''}"
        )
    return 0


class _Tee:
    """화면과 파일에 동시에 쓴다.

    작업 스케줄러는 이 스크립트를 **`pythonw.exe`(콘솔 없음)** 로 부른다.
    그대로 두면 실행 결과가 어디에도 남지 않아, 문제가 생겨도 "그냥 안 됐다"
    말고는 알 길이 없다 (→ docs/23 8단계).
    """

    def __init__(self, stream, fh):
        self._s, self._f = stream, fh

    def write(self, text):
        try:
            if self._s:
                self._s.write(text)
        except Exception:                   # noqa: BLE001 — pythonw 는 stdout 이 없다
            pass
        self._f.write(text)
        self._f.flush()

    def flush(self):
        try:
            if self._s:
                self._s.flush()
        except Exception:                   # noqa: BLE001
            pass
        self._f.flush()


def _open_log():
    """`%APPDATA%\\ANFILT\\ResearchAgent\\logs\\watch.log`. 실패하면 None."""
    try:
        from core import appdirs
        path = appdirs.logs_dir() / "watch.log"
        # 무한정 커지지 않게 — 1MB 넘으면 한 번 밀어 둔다
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix(".log.1"))
        return open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="자동 모니터링 실행기")
    ap.add_argument("--all", action="store_true", help="시각 조건 무시하고 전부 실행")
    ap.add_argument("--id", action="append", default=[], help="특정 감시만 실행")
    ap.add_argument("--list", action="store_true", help="감시 목록 출력 후 종료")
    ap.add_argument("--no-notify", action="store_true", help="알림 발송 생략")
    ap.add_argument("--vault", help="볼트 폴더 (설치판. 생략하면 설정의 활성 볼트)")
    args = ap.parse_args(argv)

    # 저장소 결정 — 설치판은 볼트 폴더, 체험판(호스팅)은 Supabase
    if args.vault:
        store = store_mod.local_store(args.vault)
    else:
        store = store_mod.active()
    if store is None or not store.is_configured():
        print("❌ 저장소 미설정 — 설치판은 --vault 또는 설정의 활성 볼트가, "
              "호스팅은 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 필요합니다",
              file=sys.stderr)
        return 2

    if args.list:
        return _print_list(store)

    # 스케줄러가 부르는 경로다 — 라이선스가 없으면 **스택트레이스 대신** 이유를
    # 남기고 조용히 끝낸다. 매시 도는 작업이 매시 예외를 뱉으면 로그를 못 읽는다.
    from core import packs
    if not packs.is_available():
        print("· 프로그램 구성요소가 준비되지 않아 건너뜁니다 "
              "(설정에서 라이선스를 활성화하세요).", file=sys.stderr)
        return 0

    force_ids = list(args.id)
    if args.all:
        force_ids = [w["watch_id"] for w in store.watch_list()
                     if w.get("enabled", True)]

    now = W.now_kst()
    print(f"⏰ {now.isoformat(timespec='seconds')} (KST) 감시 실행")
    try:
        results = run_due_watches(
            store, now=now, force_ids=force_ids or None,
            send_notify=not args.no_notify,
        )
    except Exception as e:
        print(f"❌ 실행 실패: {e}", file=sys.stderr)
        return 2

    if not results:
        print("· 이번 시각에 실행할 감시가 없습니다.")
        return 0

    errors = 0
    for r in results:
        icon = "🔔" if r.has_news else ("🌱" if r.baseline else "·")
        print(f"{icon} {r.name}: {r.status}")
        if r.note_path:
            print(f"    볼트: {r.note_path}")
        if r.error:
            errors += 1
    # 개별 감시의 실패는 종료 코드로 올리지 않는다 — 스케줄러가 매시 도는데
    # 한 사이트의 일시적 오류로 워크플로가 빨갛게 되면 신호가 무뎌진다.
    print(f"완료 — 실행 {len(results)}건, 경고 {errors}건")
    return 0


if __name__ == "__main__":
    _log = _open_log()
    if _log is None:
        raise SystemExit(main())
    _out, _err = sys.stdout, sys.stderr
    sys.stdout = _Tee(_out, _log)
    sys.stderr = _Tee(_err, _log)
    try:
        _code = main()
    finally:
        sys.stdout, sys.stderr = _out, _err
        _log.close()
    raise SystemExit(_code)
