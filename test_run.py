# test_run.py
import os
from datetime import datetime
from fcanews_main_fixed_v2 import run_bot, send_to_telegram, ADMIN_CHAT_ID

# 테스트 모드 설정
os.environ["TEST_MODE"] = "True"

# 관리자 알림
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
send_to_telegram(f"🧪 <b>관리자 테스트 실행</b>\n⏱️ {now} 기준 단일 테스트 시작합니다.", ADMIN_CHAT_ID)

# 실제 뉴스 봇 동작 (1회만)
run_bot()

# 종료 알림
send_to_telegram("🧪 <b>테스트 완료</b>\n✅ 루프 없이 단일 실행 종료되었습니다.", ADMIN_CHAT_ID)
