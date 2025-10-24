# main.py
from datetime import datetime

now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
print(f"⏰ 자동 실행 성공! (UTC 기준 시간: {now})")
