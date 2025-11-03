# ===============================================
# preview_run.py — fcanews 미리보기 (기록 없음 / 관리자 채널만)
# ===============================================
from datetime import datetime
import html
from main import (
    load_keywords,
    search_recent_news,
    send_to_telegram,
    ADMIN_CHAT_ID,
    KST,
)

print(f"👀 미리보기 실행 시작 — {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST")

try:
    search_keywords = load_keywords("search_keywords.txt")
    filter_keywords = load_keywords("filter_keywords.txt")

    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, filter_keywords
    )

    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)
    should_send = sent_count >= 1

    # ✅ 관리자 리포트 (main.py와 동일 형식)
    now = datetime.now(KST)
    status_icon = "✅" if should_send and found else "⏸️"
    status_text = "발송" if should_send and found else "보류"
    report = []
    report.append(f"{status_icon} <b>{status_text}</b> [{sent_count}건] ({now.strftime('%H:%M')})")
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신{r['time_filtered']} / 호출{r['fetched']}")
    report.append(f"(최신기사시간) {latest_time}~{earliest_time}")

    # ✅ 전체 기사 목록 표시 (제한 없음)
    if found:
        report.append("───────────────────────────────")
        for i, (t, l) in enumerate(found):
            report.append(f"{i+1}. <b>{html.escape(t)}</b>\n{l}")

    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)
    print(f"✅ 관리자 미리보기 {sent_count}건 발송 완료")

except Exception as e:
    print("❌ 미리보기 실행 오류:", e)
