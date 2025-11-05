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

def contains_any(text: str, keywords):
    tl = text.lower()
    return any(k.lower() in tl for k in keywords)

print(f"🚨 강제 발송 실행 — {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST")

try:
    # 1️⃣ 키워드 불러오기
    search_keywords  = load_keywords("search_keywords.txt")
    include_keywords = load_keywords("filter_keywords.txt")      # 포함(통과) 필터
    exclude_keywords = load_keywords("exclude_keywords.txt")     # 제외 필터

    # 2️⃣ 뉴스 검색 — 실제 규칙(포함+제외) 적용 (통과 기사)
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, include_keywords, exclude_keywords
    )

    # 3️⃣ 필터 없는 검색 — 신규 전체 기사(시간 기준은 동일) → 제외 리스트 산출용
    all_new, _, _, _, _ = search_recent_news(
        search_keywords, [], []   # 포함/제외 필터 비움
    )
    found_links = set(l for _, l in found)
    excluded_list = []
    if exclude_keywords:
        for title, link in all_new:
            if link in found_links:
                continue
            if contains_any(title, exclude_keywords):
                excluded_list.append((title, link))

    # 4️⃣ 집계
    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)
    total_excluded = sum(r.get("title_exclude_hit", 0) for r in loop_reports)

    # 강제 발송: 1건 이상이면 발송
    should_send = sent_count >= 1

    # 5️⃣ 본채널 발송
    if should_send and found:
        message = "\n".join([f"{i+1}. <b>{html.escape(t)}</b>\n{l}" for i, (t, l) in enumerate(found)])
        ok = send_to_telegram(message, chat_id=TELEGRAM_CHAT_ID)
        if ok:
            mark_sent_now()
            if pub_times:
                mark_checked_time(max(pub_times))
            print(f"✅ 본 채널로 {sent_count}건 강제 발송 완료")
        else:
            print("❌ 본 채널 전송 실패")
    else:
        print("⏸️ 발송 조건 미충족 (기사 부족)")

    # 6️⃣ 관리자 리포트 (요청 포맷)
    now = datetime.now(KST)
    status_icon = "✅" if should_send and found else "⏸️"
    status_text = "발송" if should_send and found else "보류"

    report = []
    # 1행 — 상태  예) ✅ 발송 [5건] (14:00:01 기준)
    report.append(f"{status_icon} {status_text} [{sent_count}건] ({now.strftime('%H:%M:%S')} 기준)")

    # 각 호출 결과  예) (1차) 최신6 / 호출30
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신{r['time_filtered']} / 호출{r['fetched']}")

    # 제목통과 / 최신합계 — 제외 카운트 병기  예) 제목통과5(제외0) / 최신6
    report.append(f"제목통과{sent_count}(제외{total_excluded}) / 최신{total_time_filtered}")

    # 최신기사 시간  예) (최신기사시간) 11-05(13:48) ~ 11-05(12:00)
    report.append(f"(최신기사시간) {latest_time} ~ {earliest_time}")

    # 통과 기사 목록
    if found:
        report.append("───────────────────────────────")
        report.append("📌 통과 기사")
        for i, (t, l) in enumerate(found, start=1):
            report.append(f"{i}. <b>{html.escape(t)}</b>\n{l}")

    # 제외된 기사 목록
    if excluded_list:
        report.append("───────────────────────────────")
        report.append("🚫 제외된 기사")
        for i, (t, l) in enumerate(excluded_list, start=1):
            report.append(f"{i}. <b>{html.escape(t)}</b>\n{l}")

    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)
    print("📊 관리자 리포트 발송 완료")

except Exception as e:
    print("❌ 강제 발송 오류:", e)
