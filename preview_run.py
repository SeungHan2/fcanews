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

def contains_any(text: str, keywords):
    tl = text.lower()
    return any(k.lower() in tl for k in keywords)

print(f"👀 미리보기 실행 시작 — {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST")

try:
    # 1) 키워드 로드
    search_keywords  = load_keywords("search_keywords.txt")
    include_keywords = load_keywords("filter_keywords.txt")     # 포함(통과)
    exclude_keywords = load_keywords("exclude_keywords.txt")    # 제외

    # 2) 실제 규칙(포함+제외)으로 검색 — 통과 기사
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, include_keywords, exclude_keywords
    )

    # 3) 필터 없는 검색 — 신규 전체 기사 → 제외 목록 산출
    all_new, _, _, _, _ = search_recent_news(
        search_keywords, [], []
    )
    found_links = set(l for _, l in found)
    excluded_list = []
    if exclude_keywords:
        for title, link in all_new:
            if link in found_links:
                continue
            if contains_any(title, exclude_keywords):
                excluded_list.append((title, link))

    # 4) 집계/리포트
    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)
    total_excluded = sum(r.get("title_exclude_hit", 0) for r in loop_reports)

    now = datetime.now(KST)
    status_icon = "✅" if sent_count >= 1 else "⏸️"
    status_text = "발송" if sent_count >= 1 else "보류"

    report = []
    # 1) 상태
    report.append(f"{status_icon} {status_text} [{sent_count}건] ({now.strftime('%H:%M:%S')} 기준)")
    # 2) 집계
    report.append(f"(제외{total_excluded}) 제목통과 {sent_count} / 최신{total_time_filtered}")
    # 3) 호출별
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신{r['time_filtered']} / 호출{r['fetched']}")
    # 4) 최신 시간
    report.append(f"(최신) {latest_time} ~ {earliest_time}")

    # 5) 통과 기사 목록
    if found:
        report.append("───────────────────────────────")
        report.append("📌 통과 기사")
        for i, (t, l) in enumerate(found, start=1):
            report.append(f"{i}. <b>{html.escape(t)}</b>\n{l}")

    # 6) 제외된 기사 목록
    if excluded_list:
        report.append("───────────────────────────────")
        report.append("🚫 제외된 기사")
        for i, (t, l) in enumerate(excluded_list, start=1):
            report.append(f"{i}. <b>{html.escape(t)}</b>\n{l}")

    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)
    print(f"✅ 관리자 미리보기: 통과 {sent_count}건, 제외 {len(excluded_list)}건")

except Exception as e:
    print("❌ 미리보기 실행 오류:", e)
