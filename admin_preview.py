# admin_preview.py
import html
from datetime import datetime, timedelta, timezone
from main import (
    load_keywords,
    load_sent_log,
    search_recent_news,
    send_to_telegram,
    ADMIN_CHAT_ID,
)

# ───────────────────────────────
# 한국시간 (KST) 설정
# ───────────────────────────────
KST = timezone(timedelta(hours=9))
now = datetime.now(KST)
print(f"🕒 {now.strftime('%Y-%m-%d %H:%M:%S')} KST | 관리자 미리보기 시작")

# ───────────────────────────────
# 키워드/로그 로드
# ───────────────────────────────
search_keywords = load_keywords("search_keywords.txt")
filter_keywords = load_keywords("filter_keywords.txt")
sent_before = load_sent_log()

# ───────────────────────────────
# 뉴스 검색 실행
# ───────────────────────────────
found, loop_reports, total_fetched, latest_time, earliest_time, detected_prev = search_recent_news(
    search_keywords, filter_keywords, sent_before
)

# ───────────────────────────────
# 리포트 요약 생성
# ───────────────────────────────
total_title_filtered = sum(r["title_filtered"] for r in loop_reports)
total_dup_filtered = sum(r["duplicate_filtered"] for r in loop_reports)
api_calls = len(loop_reports)
sent_count = len(found)

report = (
    f"🧪 <b>관리자 미리보기</b>\n"
    f"📊 {now.strftime('%H:%M:%S')} KST 기준\n"
    f"- 키워드 호출 : {total_fetched}건 ({api_calls}회)\n"
    f"- 중복 제외 : {total_dup_filtered}건\n"
    f"- 제목 필터 제외 : {total_title_filtered}건\n"
    f"- 최종 발송 후보 : {sent_count}건\n"
    f"- 기사 시간 범위 : {latest_time} ~ {earliest_time}\n\n"
)

if sent_count > 0:
    report += "📰 <b>발송 후보 기사 목록</b>\n" + "\n".join(
        [f"• <a href='{link}'>{html.escape(title)}</a>" for title, link in found]
    )
else:
    report += "✅ 발송 후보 없음"

# ───────────────────────────────
# 관리자 계정으로 전송 (본 채널로는 X)
# ───────────────────────────────
send_to_telegram(report, ADMIN_CHAT_ID)

print("✅ 관리자 계정으로 미리보기 리포트 전송 완료 (본 채널 발송 없음)")
