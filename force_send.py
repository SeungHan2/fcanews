# ===============================================
# force_send.py — fcanews 강제 발송 (기록 포함 / 본 채널 + 관리자)
# ===============================================
from datetime import datetime
import html
from main import (
    load_keywords,
    search_recent_news,
    send_to_telegram,
    mark_sent_now,
    mark_checked_time,
    TELEGRAM_CHAT_ID,
    ADMIN_CHAT_ID,
    KST,
)

print(f"🚨 강제 발송 실행 — {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST")

try:
    # 1️⃣ 키워드 불러오기
    search_keywords = load_keywords("search_keywords.txt")
    filter_keywords = load_keywords("filter_keywords.txt")

    # 2️⃣ 뉴스 검색
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, filter_keywords
    )

    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)
    should_send = sent_count >= 1

    # 3️⃣ 본채널 발송
    if should_send and found:
        message = "\n".join([f"{i+1}. <b>{html.escape(t)}</b>\n{l}" for i, (t, l) in enumerate(found)])
        ok = send_to_telegram(message, chat_id=TELEGRAM_CHAT_ID)
        if ok:
            mark_sent_now()
            if pub_times:
                mark_checked_time(max(pub_times))
            print(f"✅ 본 채널로 {sent_count}건 강제 발송 완료")
    else:
        print("⏸️ 발송 조건 미충족 (기사 부족)")

    # 4️⃣ 관리자 리포트 (main.py 동일 형식)
    now = datetime.now(KST)
    status_icon = "✅" if should_send and found else "⏸️"
    status_text = "발송" if should_send and found else "보류"
    
    report = []
    # 1️⃣ 1행 — 상태
    report.append(f"{status_icon} {status_text} [<b>{len(found)}</b>건] ({now.strftime('%H:%M:%S 기준')})")
    
    # 2️⃣ 각 호출 결과
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신<b>{r['time_filtered']}</b> / 호출{r['fetched']}")
    
    # 3️⃣ 제목통과 / 최신합계
    report.append(f"제목통과<b>{len(found)}</b> / 최신{sum(r['time_filtered'] for r in loop_reports)}")
    
    # 4️⃣ 최신기사 시간
    report.append(f"(최신기사시간) {latest_time} ~ {earliest_time}")


    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)
    print("📊 관리자 리포트 발송 완료")

except Exception as e:
    print("❌ 강제 발송 오류:", e)
