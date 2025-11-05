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

    # 2) 실제 규칙(포함→제외)으로 검색 — 최종 통과(=found)
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, include_keywords, exclude_keywords
    )

    # 3) 필터 없는 검색 — 신규 전체 기사에서 “포함 통과 ∧ 제외 히트”만 골라 제외 목록 구성
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

    # 4) 집계/리포트 값 산출
    sent_final = len(found)  # 최종 통과
    total_latest = sum(r["time_filtered"] for r in loop_reports)
    total_excluded = sum(r["title_exclude_hit"] for r in loop_reports)
    total_include_pass = sum(r["title_include_pass"] for r in loop_reports)

    now = datetime.now(KST)
    status_icon = "✅" if sent_final >= 1 else "⏸️"
    status_text = "발송" if sent_final >= 1 else "보류"

    report = []
    # 1) 상태 — 대괄호는 최종 통과 수
    report.append(f"{status_icon} {status_text} [{sent_final}건] ({now.strftime('%H:%M:%S')} 기준)")
    # 2) 집계 — 제목통과는 포함 필터 통과 수
    report.append(f"(제외{total_excluded}) 제목통과 {total_include_pass} / 최신{total_latest}")
    # 3) 호출별
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신{r['time_filtered']} / 호출{r['fetched']}")
    # 4) 최신 시간
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
    print(f"✅ 관리자 미리보기: 최종 {sent_final}건, 제목통과 {total_include_pass}건, 제외 {total_excluded}건")

except Exception as e:
    print("❌ 미리보기 실행 오류:", e)
