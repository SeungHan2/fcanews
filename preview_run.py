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
    include_keywords = load_keywords("filter_keywords.txt")     # 포함(통과) 필터
    exclude_keywords = load_keywords("exclude_keywords.txt")    # 제외 필터

    # 2) 실제 규칙(포함+제외)로 검색 — 통과 기사
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, include_keywords, exclude_keywords
    )

    # 3) 필터 없는 검색 — 신규 전체 기사(시간 기준만 동일하게 적용)
    all_new, _, _, _, _ = search_recent_news(
        search_keywords, [], []  # 포함/제외 필터 비우고 전체 신규 목록 수집
    )

    # 4) 제외된 기사 리스트 구성
    #    - all_new 중에서 exclude 키워드에 걸렸고, found에 없는 것만 추림
    found_links = set(l for _, l in found)
    excluded_list = []
    for title, link in all_new:
        if link in found_links:
            continue
        if exclude_keywords and contains_any(title, exclude_keywords):
            excluded_list.append((title, link))

    # 5) 기본 집계
    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)
    total_excluded = sum(r.get("title_exclude_hit", 0) for r in loop_reports)  # main과 동일 집계
    should_send = sent_count >= 1  # 미리보기라 발송 여부는 정보용

    # 6) 관리자 리포트 (main.py와 동일 형식)
    now = datetime.now(KST)
    status_icon = "✅" if should_send and found else "⏸️"
    status_text = "발송" if should_send and found else "보류"

    report = []
    # 1️⃣ 1행 — 상태 (예: ✅ 발송 [5건] (14:00:01 기준))
    report.append(f"{status_icon} {status_text} [{sent_count}건] ({now.strftime('%H:%M:%S')} 기준)")

    # 2️⃣ 각 호출 결과 (예: (1차) 최신6 / 호출30)
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신{r['time_filtered']} / 호출{r['fetched']}")

    # 3️⃣ 제목통과 / 최신합계 — 제외 카운트 병기 (예: 제목통과5(제외0) / 최신6)
    report.append(f"제목통과{sent_count}(제외{total_excluded}) / 최신{total_time_filtered}")

    # 4️⃣ 최신기사 시간
    report.append(f"(최신기사시간) {latest_time} ~ {earliest_time}")

    # 7) 전체 기사 목록(통과)
    if found:
        report.append("───────────────────────────────")
        report.append("📌 통과 기사")
        for i, (t, l) in enumerate(found, start=1):
            report.append(f"{i}. <b>{html.escape(t)}</b>\n{l}")

    # 8) 제외된 기사 목록(요청 사항)
    if excluded_list:
        report.append("───────────────────────────────")
        report.append("🚫 제외된 기사")
        for i, (t, l) in enumerate(excluded_list, start=1):
            report.append(f"{i}. <b>{html.escape(t)}</b>\n{l}")

    # 전송
    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)
    print(f"✅ 관리자 미리보기 {sent_count}건(통과), 제외 {len(excluded_list)}건 표시 완료")

except Exception as e:
    print("❌ 미리보기 실행 오류:", e)
