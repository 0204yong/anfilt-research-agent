r"""체험판이 팩을 인출할 때 쓰는 공유 토큰을 만든다 (→ docs/26 팩을 저장소 밖으로).

    python packaging\make_trial_token.py

**같은 값을 두 곳에 넣는다.**

  1. Supabase → Edge Functions → Secrets → `RA_TRIAL_TOKEN`
  2. Streamlit Cloud → 앱 → Settings → Secrets → `RA_TRIAL_TOKEN = "..."`

저장소·채팅·메일에 남기지 마세요. 이 토큰 하나면 누구나 팩(프롬프트·시드)을
받아 갈 수 있습니다 — 라이선스 키와 같은 급으로 다루세요.

새로 만들어 갈아 끼우면 옛 토큰은 즉시 무효가 됩니다. 두 곳을 **동시에**
바꿔야 하므로, 체험판이 잠깐 팩을 못 받을 수 있습니다(캐시가 있으면 버팁니다).
"""
import secrets

print("=" * 68)
print("RA_TRIAL_TOKEN (Supabase 시크릿 · Streamlit Secrets 두 곳에 같은 값)")
print("=" * 68)
print(secrets.token_urlsafe(32))
print()
print("이 창을 닫으면 다시 볼 수 없습니다. 두 곳에 먼저 넣으세요.")
