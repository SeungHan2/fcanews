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
    # 1) 키워드
    search_keywords  = load_keywords("search_keywords.txt")
    include_keywords = load_keywords("filter_keywords.txt")
    exclude_keywords = load_keywords("exclude_keywords.txt")

    # 2) 실제 규칙(포함→제외) 적용 — 최종 통과
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, include_keywords, exclude_keywords
    )

    # 3) 제외 목록(포함 통과 ∧ 제외 히트)
    all_new, _, _, _, _ = search_recent_news(search_keywords, [], [])
    found_links = set(l for _, l in found)
    excluded_list = []
    for title, link in all_new:
        if link in found_links:
            continue
        inc_ok = (not include_keywords) or contains_any(title, include_keywords)
        exc_hit = exclude_keywords and contains_any(title, exclude_keywords)
        if inc_ok and exc_hit:
            excluded_list.append((title, link))

    # 4) 집계
    sent_final = len(found)
    total_latest = sum(r["time_filtered"] for r in loop_reports)
    total_excluded = sum(r["title_exclude_hit"] for r in loop_reports)
    total_include_pass = sum(r["title_include_pass"] for r in loop_reports)

    # 강제: 1건 이상이면 발송
    if sent_final >= 1:
        message = "\n".join([f"{i+1}. <b>{html.escape(t)}</b>\n{l}" for i, (t, l) in enumerate(found)])
        ok = send_to_telegram(message, chat_id=TELEGRAM_CHAT_ID)
        if ok:
            mark_sent_now()
            if pub_times:
                mark_checked_time(max(pub_times))
            print(f"✅ 본 채널로 {sent_final}건 강제 발송 완료")
        else:
            print("❌ 본 채널 전송 실패")
    else:
        print("⏸️ 발송 조건 미충족 (기사 부족)")

    # 5) 관리자 리포트 — 새 포맷
    now = datetime.now(KST)
    status_icon = "✅" if sent_final >= 1 else "⏸️"
    status_text = "발송" if sent_final >= 1 else "보류"

    report = []
    report.append(f"{status_icon} {status_text} [{sent_final}건] ({now.strftime('%H:%M:%S')} 기준)")
    report.append(f"(제외{total_excluded}) 제목통과 {total_include_pass} / 최신{total_latest}")
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신{r['time_filtered']} / 호출{r['fetched']}")
    report.append(f"(최신) {latest_time} ~ {earliest_time}")

    # 통과 기사
    if found:
        report.append("───────────────────────────────")
        report.append("📌 통과 기사")
        for i, (t, l) in enumerate(found, start=1):
            report.append(f"{i}. <b>{html.escape(t)}</b>\n{l}")

    # 제외된 기사(포함 통과 후 제외된 것만)
    if excluded_list:
        report.append("───────────────────────────────")
        report.append("🚫 제외된 기사")
        for i, (t, l) in enumerate(excluded_list, start=1):
            report.append(f"{i}. <b>{html.escape(t)}</b>\n{l}")

    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)
    print("📊 관리자 리포트 발송 완료")

except Exception as e:
    print("❌ 강제 발송 오류:", e)
