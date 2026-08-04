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

from dotenv import load_dotenv

load_dotenv()

from core import store, watch as W  # noqa: E402  (load_dotenv 이후에 import)
from core.watch_runner import run_due_watches  # noqa: E402


def _print_list() -> int:
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="자동 모니터링 실행기")
    ap.add_argument("--all", action="store_true", help="시각 조건 무시하고 전부 실행")
    ap.add_argument("--id", action="append", default=[], help="특정 감시만 실행")
    ap.add_argument("--list", action="store_true", help="감시 목록 출력 후 종료")
    ap.add_argument("--no-notify", action="store_true", help="알림 발송 생략")
    args = ap.parse_args(argv)

    if not store.is_configured():
        print("❌ Supabase 미설정 — SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 필요",
              file=sys.stderr)
        return 2

    if args.list:
        return _print_list()

    force_ids = list(args.id)
    if args.all:
        force_ids = [w["watch_id"] for w in store.watch_list()
                     if w.get("enabled", True)]

    now = W.now_kst()
    print(f"⏰ {now.isoformat(timespec='seconds')} (KST) 감시 실행")
    try:
        results = run_due_watches(
            now=now, force_ids=force_ids or None, send_notify=not args.no_notify,
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
    raise SystemExit(main())
