import os
import sys
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ─────────────────────────────────────────────
# 실시간 로그 출력 (버퍼링 방지)
# ─────────────────────────────────────────────
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ─────────────────────────────────────────────
# 환경 변수 로드
# ─────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"
LOG_FILE = "sent_log.json"
LOCK_FILE = "/tmp/fcanews.lock"

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
DISPLAY_PER_CALL = 40     # 필요 시 조정 가능
MAX_LOOPS = 2              # 호출 페이징 회수
REQUEST_TIMEOUT = 30
MIN_SEND_THRESHOLD = 5     # 짝수시 발송 최소 기준
UA = "Mozilla/5.0 (compatible; fcanewsbot/1.0; +https://t.me/)"
KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────────
# 락 파일 관리 (중복 실행 방지)
# ─────────────────────────────────────────────
def already_running():
    if os.path.exists(LOCK_FILE):
        mtime = os.path.getmtime(LOCK_FILE)
        if (time.time() - mtime) < 600:  # 10분 이내 락 유지
            print("⚠️ 이미 실행 중인 프로세스 감지 → 종료")
            return True
    with open(LOCK_FILE, "w") as f:
        f.write(datetime.now().isoformat())
    return False

def clear_lock():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
        print("🧹 락 파일 제거 완료")

# ─────────────────────────────────────────────
# 파일 입출력
# ─────────────────────────────────────────────
def load_keywords(file_path):
    if not os.path.exists(file_path):
        print(f"⚠️ 키워드 파일 없음: {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def load_sent_log():
    if not os.path.exists(LOG_FILE):
        return set()
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_sent_log(sent_ids):
    sent_list = sorted(list(sent_ids))
    if len(sent_list) > 100:
        sent_list = sent_list[-100:]
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(sent_list, f, ensure_ascii=False, indent=2)

# ─────────────────────────────────────────────
# 네이버 뉴스 검색 (최대 루프까지 전체 수집: 최대 발송 제한 없음)
# ─────────────────────────────────────────────
def search_recent_news(search_keywords, filter_keywords, sent_before):
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
        "User-Agent": UA,
    }

    collected = []           # 제목 필터 통과 기사(중복 제외) 전부 수집
    pub_times = []           # 호출된 모든 기사들의 pubDate
    total_fetched = 0
    loop_reports = []        # 각 호출별 통계
    start = 1
    loop_count = 0

    while loop_count < MAX_LOOPS:
        loop_count += 1
        query = " ".join(search_keywords)
        url = f"{base_url}?query={urllib.parse.quote(query)}&display={DISPLAY_PER_CALL}&start={start}&sort=date"

        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print(f"❌ 요청 예외: {e}")
            break

        if r.status_code != 200:
            print(f"❌ 요청 실패: {r.status_code}")
            break

        items = r.json().get("items", [])
        fetched = len(items)
        total_fetched += fetched
        if not items:
            print("ℹ️ 더 이상 결과 없음")
            break

        duplicate_skipped = 0
        filtered_passed = 0

        for item in items:
            title_raw = html.unescape(item.get("title", ""))
            title_clean = title_raw.replace("<b>", "").replace("</b>", "")
            link = (item.get("link") or "").strip()

            # 발행 시간 수집 (전체 기사 기준)
            pub_raw = item.get("pubDate")
            if pub_raw:
                try:
                    pub_dt = parsedate_to_datetime(pub_raw).astimezone(KST)
                    pub_times.append(pub_dt)
                except Exception:
                    pass

            # 이전 발송 중복 제외 (조기 중단하지 않고 계속 검사)
            if link in sent_before:
                duplicate_skipped += 1
                continue

            # 제목 필터 통과만 수집
            if any(k.lower() in title_clean.lower() for k in filter_keywords):
                filtered_passed += 1
                collected.append((title_clean, link))

        loop_reports.append(
            {
                "call_no": loop_count,
                "fetched": fetched,
                "duplicate_skipped": duplicate_skipped,
                "filtered_passed": filtered_passed,
            }
        )

        # 최적화: 1회차에서 중복이 한 건이라도 있으면 이후 호출 실익이 낮음 → 중단
        if loop_count == 1 and duplicate_skipped > 0:
            print("⏹️ 1회차에서 중복 발견 → 이후 호출 생략")
            break

        start += DISPLAY_PER_CALL

    # 기사 시간 범위 (전체 호출된 기사 기준)
    if pub_times:
        first_time = min(pub_times).strftime("%m-%d %H:%M")
        last_time = max(pub_times).strftime("%m-%d %H:%M")
    else:
        first_time = last_time = "N/A"

    return collected, loop_reports, total_fetched, first_time, last_time

# ─────────────────────────────────────────────
# 텔레그램 전송
# ─────────────────────────────────────────────
def send_to_telegram(message, chat_id=None):
    chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        print("⚠️ TELEGRAM 환경변수가 없습니다.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            print(f"✅ 텔레그램 전송 완료 ({chat_id})")
            return True
        else:
            print("❌ 텔레그램 전송 실패:", r.text)
            return False
    except Exception as e:
        print("❌ 텔레그램 전송 예외:", e)
        return False

# ─────────────────────────────────────────────
# 메인 실행 로직
# ─────────────────────────────────────────────
def run_bot():
    now = datetime.now(KST)
    hour = now.hour
    is_force_cycle = hour in [0, 6, 12, 18]

    print(f"🕒 현재 (한국시간) {now.strftime('%Y-%m-%d %H:%M:%S')} | 강제 발송 타임: {is_force_cycle}")

    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)
    sent_before = load_sent_log()

    # 뉴스 검색 (최대 발송 제한 없음)
    found, loop_reports, total_fetched, first_time, last_time = search_recent_news(
        search_keywords, filter_keywords, sent_before
    )

    # 통계
    filter_pass_total = sum(r["filtered_passed"] for r in loop_reports)
    duplicate_total = sum(r["duplicate_skipped"] for r in loop_reports)
    api_calls = len(loop_reports)
    non_duplicate_total = total_fetched - duplicate_total

    # 발송 판단: 짝수시 & 최소 개수 or 강제 타임
    sent_count = len(found)
    should_send = is_force_cycle or (sent_count >= MIN_SEND_THRESHOLD and hour % 2 == 0)

    # 실제 발송
    if should_send and found:
        lines = [f"{i+1}. <b>{html.escape(t)}</b>\n{l}\n" for i, (t, l) in enumerate(found)]
        message = "\n".join(lines)
        send_to_telegram(message)
        for _, link in found:
            sent_before.add(link)
        save_sent_log(sent_before)
    else:
        print("⏸️ 보류 상태 - 발송 없음")

    # 호출 상세 요약
    loop_summary = "\n".join(
        [f"  • {r['call_no']}회차: {r['fetched']}건 / 중복 {r['duplicate_skipped']} / 제목 {r['filtered_passed']}"
         for r in loop_reports]
    )

    # 관리자 리포트 (요청 포맷)
    admin_msg = (
        f"📊 <b>관리자 리포트</b> (기준 {now.strftime('%H:%M:%S KST')})\n"
        f"- {'✅ 발송' if should_send else '⏸️ 보류'}\n"
        f"- 발송기사: <b>{sent_count}개</b>\n"
        f"- 네이버 API 호출: <b>{api_calls}회</b> ({total_fetched}건)\n"
        f"- 중복 제외 통과: <b>{non_duplicate_total}개</b>\n"
        f"- 제목 필터 통과: <b>{filter_pass_total}개</b>\n"
        f"- 호출 상세:\n{loop_summary}\n"
        f"- 기사시간: {first_time} ~ {last_time}"
    )
    send_to_telegram(admin_msg, chat_id=ADMIN_CHAT_ID)

    print(f"✅ 전송 완료 ({sent_count}건) | {now.strftime('%H:%M')}")

# ─────────────────────────────────────────────
# 정시 대기 함수
# ─────────────────────────────────────────────
def wait_until_next_even_hour():
    now = datetime.now(KST)
    next_even_hour = (now.replace(minute=0, second=0, microsecond=0)
                      + timedelta(hours=2 - (now.hour % 2)))
    sleep_seconds = (next_even_hour - now).total_seconds()
    print(f"🕓 다음 실행 예정: {next_even_hour.strftime('%H:%M')} (대기 {int(sleep_seconds/60)}분)")
    time.sleep(sleep_seconds)

# ─────────────────────────────────────────────
# Render 루프 (배포 직후 발송 방지: 짝수시만 run_bot)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if already_running():
        exit(0)

    print("🚀 fcanews bot 시작 (Render 상시 루프 모드)")
    now = datetime.now(KST)
    next_even_hour = (now.replace(minute=0, second=0, microsecond=0)
                      + timedelta(hours=2 - (now.hour % 2)))
    print(f"⏸️ 초기 기동 모드: 첫 발송은 {next_even_hour.strftime('%Y-%m-%d %H:%M:%S')} 예정")

    try:
        while True:
            current = datetime.now(KST)
            if current.hour % 2 == 0 and current.minute < 7:
                run_bot()
            else:
                print(f"⏳ 대기 중... 현재 {current.strftime('%H:%M')} (짝수시 아님)")
            wait_until_next_even_hour()
    except KeyboardInterrupt:
        print("🛑 종료 신호 감지 - 종료 중")
    finally:
        clear_lock()
