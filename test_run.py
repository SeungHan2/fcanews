# test_run.py
import os
from datetime import datetime, timedelta, timezone
from main import run_bot, send_to_telegram, ADMIN_CHAT_ID, LAST_SENT_TIME_FILE

# ─────────────────────────────────────────────
# 한국시간 (KST) 설정
# ─────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

# 테스트 모드 강제 활성화
os.environ["TEST_MODE"] = "True"

# 테스트용: 중복 방지 해제
if os.path.exists(LAST_SENT_TIME_FILE):
    os.remove(LAST_SENT_TIME_FILE)

# 관리자에게 시작 알림
now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
send_to_telegram(f"🧪 <b>관리자 테스트 실행</b>\n⏱️ {now} 기준 단일 테스트 시작합니다.", ADMIN_CHAT_ID)

# 뉴스 봇 실행 (1회만)
run_bot()

# 종료 알림
send_to_telegram("🧪 <b>테스트 완료</b>\n✅ 루프 없이 단일 실행 종료되었습니다.", ADMIN_CHAT_ID)
