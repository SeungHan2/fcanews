import os
import html
from datetime import datetime, timezone, timedelta
from main import (
    load_keywords,
    search_recent_news,
    send_to_telegram,
    ADMIN_CHAT_ID,
    KST,
)

# ─────────────────────────────────────────────
# 관리자 미리보기 시작
# ─────────────────────────────────────────────
print(f"🕒 {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST | 관리자 미리보기 시작")

try:
    # 1️⃣ 키워드 불러오기
    search_keywords = load_keywords("search_keywords.txt")
    filter_keywords = load_keywords("filter_keywords.txt")

    # 2️⃣ 뉴스 검색 (시간 필터 + 제목 필터)
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, filter_keywords
    )

    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)

    # 3️⃣ 관리자 리포트 생성
    report_lines = []
    report_lines.append(f"🧪 <b>관리자 미리보기</b>")
    report_lines.append(f"🕓 기준 시각: {datetime.now(KST).strftime('%m-%d %H:%M:%S')}")
    report_lines.append(f"🔹 시간 필터 통과: {total_time_filtered}건")
    report_lines.append(f"🔹 제목 필터 통과: {sent_count}건\n")

    for r in loop_reports:
        report_lines.append(f"({r['call_no']}차) 최신{r['time_filtered']} / 호출{r['fetched']}")

    report_lines.append(f"(제목 통과) 발송 {sent_count} / 최신 {total_time_filtered}")
    report_lines.append(f"【{latest_time} ~ {earliest_time}】")  # ← 시간 필터 통과 기사들의 범위

    # 4️⃣ 기사 미리보기 (최대 10개)
    if found:
        preview_lines = [
            f"• <a href='{l}'>{html.escape(t)}</a>"
            for t, l in found[:10]
        ]
        report_lines.append("\n".join(preview_lines))
    else:
        report_lines.append("⚠️ 현재 발송 후보 기사 없음")

    # 5️⃣ 관리자 채널로 전송
    send_to_telegram("\n".join(report_lines), chat_id=ADMIN_CHAT_ID)
    print("✅ 관리자 미리보기 전송 완료")

except Exception as e:
    print("❌ 관리자 미리보기 중 예외 발생:", e)
