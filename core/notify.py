"""알림 발송 — 이메일(SMTP) · 카카오톡(나에게 보내기).

→ docs/15 자동 모니터링과 알림. Streamlit 비의존 (감시 CLI에서도 그대로 쓴다).

설계 원칙:
- **발송 실패가 감시 실행을 죽이지 않는다.** 모든 함수는 예외를 삼키고
  (성공여부, 설명) 튜플을 돌려준다 — 알림이 안 가도 볼트 축적은 끝나야 한다.
- 채널은 환경변수 유무로 자동 활성화된다 (프로바이더 키 감지와 같은 관례).
- 카카오톡은 **본인에게 보내기(memo API)만** 쓴다. 친구에게 보내기는 카카오
  검수가 필요하지만, 나에게 보내기는 토큰만 있으면 즉시 동작한다.
"""
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

import requests

_TIMEOUT = 15

# 카카오 텍스트 템플릿 본문 상한(200자) — 넘으면 발송 자체가 400으로 실패한다
KAKAO_TEXT_LIMIT = 190


# ---------------------------------------------------------------- 이메일


def email_configured() -> bool:
    return bool(
        os.getenv("SMTP_HOST") and os.getenv("SMTP_USER")
        and os.getenv("SMTP_PASSWORD")
    )


def email_recipient() -> str:
    return os.getenv("NOTIFY_EMAIL_TO") or os.getenv("SMTP_USER") or ""


def send_email(subject: str, body: str, to: str = None) -> tuple:
    """평문 본문 메일 1통. 반환: (성공, 설명)."""
    if not email_configured():
        return False, "SMTP 미설정 (SMTP_HOST·SMTP_USER·SMTP_PASSWORD 필요)"
    to = to or email_recipient()
    if not to:
        return False, "받는 사람이 없습니다 (NOTIFY_EMAIL_TO)"

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT") or 587)
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender_name = os.getenv("NOTIFY_EMAIL_FROM_NAME") or "리서치 에이전트"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, user))
    msg["To"] = to
    msg.set_content(body)

    try:
        # 465는 암시적 SSL, 그 외(587 등)는 STARTTLS — 메일 서버 관례
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=_TIMEOUT) as s:
                s.starttls()
                s.login(user, password)
                s.send_message(msg)
    except Exception as e:
        return False, f"메일 발송 실패: {e}"
    return True, f"메일 발송 완료 → {to}"


# ---------------------------------------------------------------- 카카오톡


def kakao_configured() -> bool:
    return bool(
        os.getenv("KAKAO_ACCESS_TOKEN")
        or (os.getenv("KAKAO_REST_API_KEY") and os.getenv("KAKAO_REFRESH_TOKEN"))
    )


def _kakao_access_token() -> str:
    """리프레시 토큰으로 액세스 토큰을 갱신한다.

    액세스 토큰 수명은 약 6시간이라 매 실행마다 새로 받는 편이 안전하다.
    KAKAO_ACCESS_TOKEN이 직접 주어지면(수동 테스트) 그걸 그대로 쓴다.
    """
    direct = os.getenv("KAKAO_ACCESS_TOKEN")
    if direct:
        return direct
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": os.getenv("KAKAO_REST_API_KEY"),
            "refresh_token": os.getenv("KAKAO_REFRESH_TOKEN"),
            **({"client_secret": os.getenv("KAKAO_CLIENT_SECRET")}
               if os.getenv("KAKAO_CLIENT_SECRET") else {}),
        },
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        raise RuntimeError(f"토큰 갱신 실패 {resp.status_code}: {resp.text[:200]}")
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"토큰 응답에 access_token 없음: {resp.text[:200]}")
    return token


def send_kakao(text: str, link_url: str = "") -> tuple:
    """카카오톡 '나에게 보내기' 1건. 반환: (성공, 설명)."""
    if not kakao_configured():
        return False, "카카오 미설정 (KAKAO_REST_API_KEY·KAKAO_REFRESH_TOKEN 필요)"
    import json as _json

    text = str(text)
    if len(text) > KAKAO_TEXT_LIMIT:
        text = text[: KAKAO_TEXT_LIMIT - 1] + "…"
    template = {
        "object_type": "text",
        "text": text,
        # link는 필수 필드다 — 열 곳이 없으면 카카오 도움말로 채운다
        "link": {
            "web_url": link_url or "https://developers.kakao.com",
            "mobile_web_url": link_url or "https://developers.kakao.com",
        },
    }
    if link_url:
        template["button_title"] = "원문 열기"

    try:
        token = _kakao_access_token()
        resp = requests.post(
            "https://kapi.kakao.com/v2/api/talk/memo/default/send",
            headers={"Authorization": f"Bearer {token}"},
            data={"template_object": _json.dumps(template, ensure_ascii=False)},
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            return False, f"카카오 발송 실패 {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, f"카카오 발송 실패: {e}"
    return True, "카카오톡 발송 완료"


# ---------------------------------------------------------------- 공통 진입점

CHANNELS = {
    "email": ("📧 이메일", email_configured),
    "kakao": ("💬 카카오톡", kakao_configured),
}


def channel_status() -> dict:
    """{channel: bool} — UI에서 설정 여부 표시용 (key_status와 같은 관례)."""
    return {key: check() for key, (_, check) in CHANNELS.items()}


def notify(channels, subject: str, body: str, short: str = "",
           link_url: str = "") -> list:
    """선택된 채널로 발송하고 [(채널, 성공, 설명)]을 반환한다 (예외 없음).

    body는 이메일용 전문, short는 카카오톡용 짧은 요약(없으면 subject 사용).
    """
    results = []
    for ch in channels or []:
        if ch == "email":
            ok, note = send_email(subject, body)
        elif ch == "kakao":
            ok, note = send_kakao(short or subject, link_url)
        else:
            ok, note = False, f"알 수 없는 채널: {ch}"
        results.append((ch, ok, note))
    return results
