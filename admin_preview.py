# ===============================================
# preview_admin.py — 관리자 리포트 미리보기 (main.py 동일 형식)
# ===============================================
import os
import html
from datetime import datetime
from main import (
    load_keywords,
    search_recent_news,
    send_to_telegram,
    ADMIN_CHAT_ID,
    KST,
)

print(f"🕒 {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST | 관리자 리포트 미리보기 시작")

try:
    # 1️⃣ 키워드 로드
    search_keywords = load_keywords("search_keywords.txt")
    filter_keywords = load_keywords("filter_keywords.txt")

    # 2️⃣ 뉴스 검색
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, filter_keywords
    )

    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)

    # 3️⃣ 관리자 리포트 (main.py 동일 포맷)
    report = []
    now = datetime.now(KST)
    current_hour = now.hour

    # 본 발송 조건과 동일한 기준을 반영
    from main import MIN_SEND_THRESHOLD, FORCE_HOURS
    should_send = (sent_count >= 1 if current_hour in FORCE_HOURS else sent_count >= MIN_SEND_THRESHOLD)

    status_icon = "✅" if should_send and found else "⏸️"
    status_text = "발송" if should_send and found else "보류"
    report.append(f"{status_icon} <b>{status_text}</b> [<b>{sent_count}</b>건] ({now.strftime('%H:%M:%S KST')} 기준)")

    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신<b>{r['time_filtered']}</b> / 호출{r['fetched']}")

    report.append(f"(제목통과) <b>{sent_count}</b> / 최신<b>{total_time_filtered}</b>")
    report.append(f"(최신기사시간) {latest_time} ~ {earliest_time}")

    # 4️⃣ 기사 목록 (미리보기용, 최대 10개)
    if found:
        preview_lines = [
            f"{i+1}. <b>{html.escape(t)}</b>\n{l}"
            for i, (t, l) in enumerate(found[:10])
        ]
        report.append("\n".join(preview_lines))
    else:
        report.append("⚠️ 현재 발송 후보 기사 없음")

    # 5️⃣ 관리자 채널로 전송
    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)
    print("✅ 관리자 리포트 미리보기 전송 완료")

except Exception as e:
    print("❌ 관리자 미리보기 중 예외 발생:", e)
