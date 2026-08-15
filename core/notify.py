"""알림 발송 — 이메일(SMTP) · 카카오톡(나에게 보내기).

→ docs/15 자동 모니터링과 알림. Streamlit 비의존 (감시 CLI에서도 그대로 쓴다).

설계 원칙:
- **발송 실패가 감시 실행을 죽이지 않는다.** 모든 함수는 예외를 삼키고
  (성공여부, 설명) 튜플을 돌려준다 — 알림이 안 가도 볼트 축적은 끝나야 한다.
- 채널은 환경변수 유무로 자동 활성화된다 (프로바이더 키 감지와 같은 관례).
- 카카오톡은 **본인에게 보내기(memo API)만** 쓴다. 친구에게 보내기는 카카오
  검수가 필요하지만, 나에게 보내기는 토큰만 있으면 즉시 동작한다.
"""
import base64
import os
import smtplib
import subprocess
import sys
from email.message import EmailMessage
from email.utils import formataddr

import requests

_TIMEOUT = 15

# 자격 증명 저장소에 넣을 값 (설정 파일에 평문으로 두지 않는다)
_SECRET_SETTINGS = {"SMTP_PASSWORD", "KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN",
                    "KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET"}


def conf(name: str) -> str:
    """알림 설정 한 칸. **환경변수 → 설치판 설정** 순으로 본다.

    환경변수를 먼저 보는 이유: 체험판과 GitHub Actions 는 그쪽으로만 받는다.
    설치판에는 `.env` 가 없고 있을 수도 없으므로(→ docs/22) 설정 화면에서
    받아 자격 증명 저장소·설정 파일에 둔다.

    이게 없던 동안 **설치판 고객은 알림을 켤 방법이 아예 없었다** — 화면의
    안내는 GitHub Actions 시크릿 이야기였고, 그건 체험판 사정이다.
    """
    v = os.getenv(name)
    if v:
        return v
    try:
        from . import edition
        if not edition.is_installed():
            return ""
        if name in _SECRET_SETTINGS:
            from . import keys
            return keys.get(name) or ""
        from . import settings
        return str((settings.load().get("notify") or {}).get(name) or "")
    except Exception:                            # noqa: BLE001 — 알림 설정이 앱을 막지 않는다
        return ""


def save_conf(values: dict) -> None:
    """설정 화면에서 받은 값을 저장한다 (비밀은 자격 증명 저장소로)."""
    from . import keys, settings
    cfg = settings.load()
    box = dict(cfg.get("notify") or {})
    for name, value in values.items():
        value = str(value or "").strip()
        if name in _SECRET_SETTINGS:
            if value:
                keys.set(name, value)
            else:
                keys.delete(name)
        elif value:
            box[name] = value
        else:
            box.pop(name, None)
    cfg["notify"] = box
    settings.save(cfg)

# 카카오 텍스트 템플릿 본문 상한(200자) — 넘으면 발송 자체가 400으로 실패한다
KAKAO_TEXT_LIMIT = 190


# ---------------------------------------------------------------- 이메일

# 메일 서비스별 서버·포트. **고객에게 물어볼 것이 아니다** — 서비스마다 정해진
# 값이고, ESG 컨설턴트가 'SMTP 서버'를 알 이유가 없다. 쓰는 메일을 고르게 하고
# 나머지는 우리가 채운다. 목록에 없는 회사 메일만 직접 입력으로 보낸다.
SMTP_PRESETS = {
    "gmail": ("Gmail", "smtp.gmail.com", 587,
              "https://myaccount.google.com/apppasswords",
              "2단계 인증을 켜야 앱 비밀번호 메뉴가 보입니다."),
    "naver": ("네이버", "smtp.naver.com", 587,
              "https://mail.naver.com/option/imap",
              "메일 환경설정 → POP3/IMAP 설정에서 **사용함**으로 바꿔야 합니다."),
    "daum": ("다음·한메일", "smtp.daum.net", 465,
             "https://mail.daum.net",
             "메일 환경설정 → IMAP/SMTP 사용을 켜세요."),
    "outlook": ("Outlook·Hotmail", "smtp-mail.outlook.com", 587,
                "https://account.microsoft.com/security",
                "2단계 인증을 켠 뒤 앱 암호를 만드세요."),
    "worksmobile": ("네이버웍스", "smtp.worksmobile.com", 587,
                    "https://mail.worksmobile.com",
                    "관리자가 IMAP/SMTP 를 허용해야 합니다."),
    "custom": ("직접 입력 (회사 메일 등)", "", 587, "",
               "회사 전산 담당자에게 **SMTP 주소와 포트**를 물어보세요."),
}


def preset_for_host(host: str) -> str:
    """저장된 서버 주소로 어떤 서비스인지 되짚는다 (화면에서 고른 것을 되살린다)."""
    host = str(host or "").strip().lower()
    if not host:
        return "gmail"
    for key, (_, h, _p, _u, _n) in SMTP_PRESETS.items():
        if h and h.lower() == host:
            return key
    return "custom"


def email_configured() -> bool:
    return bool(
        conf("SMTP_HOST") and conf("SMTP_USER")
        and conf("SMTP_PASSWORD")
    )


def email_recipient() -> str:
    return conf("NOTIFY_EMAIL_TO") or conf("SMTP_USER") or ""


def send_email(subject: str, body: str, to: str = None) -> tuple:
    """평문 본문 메일 1통. 반환: (성공, 설명)."""
    if not email_configured():
        return False, "SMTP 미설정 (SMTP_HOST·SMTP_USER·SMTP_PASSWORD 필요)"
    to = to or email_recipient()
    if not to:
        return False, "받는 사람이 없습니다 (NOTIFY_EMAIL_TO)"

    host = conf("SMTP_HOST")
    port = int(conf("SMTP_PORT") or 587)
    user = conf("SMTP_USER")
    password = conf("SMTP_PASSWORD")
    sender_name = conf("NOTIFY_EMAIL_FROM_NAME") or "리서치 에이전트"

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
        conf("KAKAO_ACCESS_TOKEN")
        or (conf("KAKAO_REST_API_KEY") and conf("KAKAO_REFRESH_TOKEN"))
    )


def _kakao_access_token() -> str:
    """리프레시 토큰으로 액세스 토큰을 갱신한다.

    액세스 토큰 수명은 약 6시간이라 매 실행마다 새로 받는 편이 안전하다.
    KAKAO_ACCESS_TOKEN이 직접 주어지면(수동 테스트) 그걸 그대로 쓴다.
    """
    direct = conf("KAKAO_ACCESS_TOKEN")
    if direct:
        return direct
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": conf("KAKAO_REST_API_KEY"),
            "refresh_token": conf("KAKAO_REFRESH_TOKEN"),
            **({"client_secret": conf("KAKAO_CLIENT_SECRET")}
               if conf("KAKAO_CLIENT_SECRET") else {}),
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


# ------------------------------------------------------------ 윈도우 알림

# 화면 오른쪽 아래 토스트. **설정이 하나도 없다** — 계정도 비밀번호도 외부
# 서비스도 필요 없고, 인터넷이 끊겨도 뜬다. 설치판은 고객 PC 에서 도니까
# 이게 성립한다 (체험판은 리눅스 서버라 아래 `toast_configured` 가 걸러 낸다).
#
# 한계는 분명하다 — **PC 앞에 있어야 보인다.** 그래서 기본이지 전부는 아니다.
#
# BurntToast 같은 모듈을 깔지 않는다. 고객 PC 에 무언가를 설치하게 만들면
# 그 순간 "설정이 없다"는 장점이 사라진다. 윈도우에 이미 있는 API 만 쓴다.
_TOAST_PS = """
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@"
<toast><visual><binding template="ToastGeneric">
<text>__TITLE__</text><text>__BODY__</text>
</binding></visual></toast>
"@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('__APPID__').Show($toast)
"""

# 토스트는 등록된 앱 이름(AUMID)으로만 뜬다. 우리 앱은 파이썬으로 돌아 별도
# 등록이 없으므로, 윈도우에 기본으로 있는 PowerShell 의 것을 빌린다.
TOAST_APP_ID = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
                "\\WindowsPowerShell\\v1.0\\powershell.exe")


def _xml_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def toast_configured() -> bool:
    """윈도우에서만. 설정할 것이 없으므로 플랫폼만 본다."""
    return sys.platform == "win32"


def send_toast(title: str, body: str) -> tuple:
    if not toast_configured():
        return False, "윈도우 알림은 이 환경에서 쓸 수 없습니다"
    script = (_TOAST_PS
              .replace("__TITLE__", _xml_escape(title)[:120])
              .replace("__BODY__", _xml_escape(body)[:300])
              .replace("__APPID__", TOAST_APP_ID))
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:                       # noqa: BLE001
        return False, f"윈도우 알림 실패: {e}"
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", errors="replace").strip()[:200]
        return False, f"윈도우 알림 실패: {err or f'종료코드 {out.returncode}'}"
    return True, "윈도우 알림을 띄웠습니다"


# ---------------------------------------------------------------- 공통 진입점

CHANNELS = {
    "toast": ("🔔 윈도우 알림", toast_configured),
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
        if ch == "toast":
            ok, note = send_toast(subject, short or subject)
        elif ch == "email":
            ok, note = send_email(subject, body)
        elif ch == "kakao":
            ok, note = send_kakao(short or subject, link_url)
        else:
            ok, note = False, f"알 수 없는 채널: {ch}"
        results.append((ch, ok, note))
    return results
