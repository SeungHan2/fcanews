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

# ─────────────────────────────────────────────
# 경로 설정 (Persistent Disk)
# ─────────────────────────────────────────────
# Render Disk를 /data로 마운트했다면 그대로 사용합니다.
PERSISTENT_MOUNT = os.getenv("PERSISTENT_MOUNT", "/data")
os.makedirs(PERSISTENT_MOUNT, exist_ok=True)

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"

# 영구 저장 파일들
SENT_LOG_PATH = os.path.join(PERSISTENT_MOUNT, "sent_log.json")        # 발송 기사 누적
LAST_SENT_TIME_FILE = os.path.join(PERSISTENT_MOUNT, "last_sent_time.txt")  # 같은 시각 중복 방지

# 프로세스 락(동시 실행 방지)은 OS 임시 디렉터리 사용
LOCK_FILE = "/tmp/fcanews.lock"

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
DISPLAY_PER_CALL = 40     # 네이버 한 번 호출당 가져올 수량
MAX_LOOPS = 2             # 네이버 페이징 호출 횟수
REQUEST_TIMEOUT = 30
MIN_SEND_THRESHOLD = 3    # 짝수 시각 발송 최소 개수
UA = "Mozilla/5.0 (compatible; fcanewsbot/1.0; +https://t.me/)"
KST = timezone(timedelta(hours=9))

FORCE_HOURS = {0, 6, 12, 18}   # 강제 발송 타임(무조건 발송)
BOOT_MARGIN_MINUTES = 2        # 부팅 직후 n분 동안 강제발송 무시 (이중발송 예방용)

# ─────────────────────────────────────────────
# 락 파일 관리 (중복 실행 방지 - 프로세스 단위)
# ─────────────────────────────────────────────
def already_running():
    try:
        if os.path.exists(LOCK_FILE):
            mtime = os.path.getmtime(LOCK_FILE)
            if (time.time() - mtime) < 600:  # 10분 이내 락 유지
                print("⚠️ 이미 실행 중인 프로세스 감지 → 종료")
                return True
        with open(LOCK_FILE, "w") as f:
            f.write(datetime.now().isoformat())
        return False
    except Exception as e:
        print("⚠️ 락 파일 처리 중 예외:", e)
        return False

def clear_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            print("🧹 락 파일 제거 완료")
    except Exception as e:
        print("⚠️ 락 파일 제거 예외:", e)

# ─────────────────────────────────────────────
# 동일 시각(YYYY-MM-DD HH:00) 중복 발송 방지
# ─────────────────────────────────────────────
def _current_hour_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:00")

def already_sent_this_hour():
    try:
        if not os.path.exists(LAST_SENT_TIME_FILE):
            return False
        with open(LAST_SENT_TIME_FILE, "r", encoding="utf-8") as f:
            last = f.read().strip()
        return last == _current_hour_str()
    except Exception:
        return False

def mark_sent_now():
    try:
        with open(LAST_SENT_TIME_FILE, "w", encoding="utf-8") as f:
            f.write(_current_hour_str())
    except Exception as e:
        print("⚠️ 발송 시각 기록 예외:", e)

# ─────────────────────────────────────────────
# 파일 입출력
# ─────────────────────────────────────────────
def ensure_persistent_files():
    """영구 파일 기본 생성"""
    if not os.path.exists(SENT_LOG_PATH):
        with open(SENT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        print(f"📝 초기화: {SENT_LOG_PATH} 생성 ([])")

def load_keywords(file_path):
    if not os.path.exists(file_path):
        print(f"⚠️ 키워드 파일 없음: {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def load_sent_log():
    if not os.path.exists(SENT_LOG_PATH):
        return set()
    try:
        with open(SENT_LOG_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception as e:
        print("⚠️ sent_log 읽기 예외:", e)
        return set()

def save_sent_log(sent_ids):
    sent_list = sorted(list(sent_ids))
    # 로그 크기 관리(최근 100개만 유지)
    if len(sent_list) > 100:
        sent_list = sent_list[-100:]
    try:
        with open(SENT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(sent_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ sent_log 저장 예외:", e)

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
            print(f"❌ 요청 실패: {r.status_code} {r.text}")
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
# 유틸: 강제 발송 타임/부팅 직후 스킵
# ─────────────────────────────────────────────
def is_force_time(now_kst: datetime) -> bool:
    return now_kst.hour in FORCE_HOURS

def is_boot_margin(now_kst: datetime) -> bool:
    # 부팅 직후 BOOT_MARGIN_MINUTES 분 동안은 강제 발송을 무시
    return now_kst.minute < BOOT_MARGIN_MINUTES

# ─────────────────────────────────────────────
# 메인 실행 로직
# ─────────────────────────────────────────────
def run_bot():
    now = datetime.now(KST)
    force_cycle = is_force_time(now)

    print(f"🕒 현재 (한국시간) {now.strftime('%Y-%m-%d %H:%M:%S')} | 강제 발송 타임: {force_cycle}")

    # 같은 시각(예: 12:00) 중복 방지
    if already_sent_this_hour():
        print("⏹️ 이미 이번 시각에 발송 완료 → 중복 방지")
        return

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
    should_send = force_cycle or (sent_count >= MIN_SEND_THRESHOLD and now.hour % 2 == 0)

    # 실제 발송
    if should_send and found:
        lines = [f"{i+1}. <b>{html.escape(t)}</b>\n{l}\n" for i, (t, l) in enumerate(found)]
        message = "\n".join(lines)
        ok1 = send_to_telegram(message)
        ok2 = send_to_telegram(
            f"📊 <b>관리자 리포트</b> (기준 {now.strftime('%H:%M:%S KST')})\n"
            f"- {'✅ 발송' if should_send else '⏸️ 보류'}\n"
            f"- 발송기사: <b>{sent_count}개</b>\n"
            f"- 네이버 API 호출: <b>{api_calls}회</b> ({total_fetched}건)\n"
            f"- 중복 제외 통과: <b>{non_duplicate_total}개</b>\n"
            f"- 제목 필터 통과: <b>{filter_pass_total}개</b>\n"
            f"- 호출 상세:\n" + "\n".join(
                [f"  • {r['call_no']}회차: {r['fetched']}건 / 중복 {r['duplicate_skipped']} / 제목 {r['filtered_passed']}"
                 for r in loop_reports]
            ) + f"\n- 기사시간: {first_time} ~ {last_time}",
            chat_id=ADMIN_CHAT_ID
        )

        # 발송 성공 시에만 로그 반영 및 시각 기록
        if ok1:
            for _, link in found:
                sent_before.add(link)
            save_sent_log(sent_before)
            mark_sent_now()
    else:
        print("⏸️ 보류 상태 - 발송 없음")

        # 관리자 리포트는 보류 상태에서도 전송
        send_to_telegram(
            f"📊 <b>관리자 리포트</b> (기준 {now.strftime('%H:%M:%S KST')})\n"
            f"- {'✅ 발송' if should_send else '⏸️ 보류'}\n"
            f"- 발송기사: <b>{sent_count}개</b>\n"
            f"- 네이버 API 호출: <b>{api_calls}회</b> ({total_fetched}건)\n"
            f"- 중복 제외 통과: <b>{non_duplicate_total}개</b>\n"
            f"- 제목 필터 통과: <b>{filter_pass_total}개</b>\n"
            f"- 호출 상세:\n" + "\n".join(
                [f"  • {r['call_no']}회차: {r['fetched']}건 / 중복 {r['duplicate_skipped']} / 제목 {r['filtered_passed']}"
                 for r in loop_reports]
            ) + f"\n- 기사시간: {first_time} ~ {last_time}",
            chat_id=ADMIN_CHAT_ID
        )

    print(f"✅ 처리 완료 ({sent_count}건) | {now.strftime('%H:%M')}")

# ─────────────────────────────────────────────
# 정시 대기 함수
# ─────────────────────────────────────────────
def wait_until_next_even_hour():
    now = datetime.now(KST)
    base = now.replace(minute=0, second=0, microsecond=0)
    add_hours = (2 - (now.hour % 2)) % 2
    if add_hours == 0 and now.minute >= 7:
        add_hours = 2
    next_even_hour = base + timedelta(hours=add_hours)
    sleep_seconds = (next_even_hour - now).total_seconds()
    print(f"🕓 다음 실행 예정: {next_even_hour.strftime('%H:%M')} (대기 {int(sleep_seconds/60)}분)")
    time.sleep(max(0, sleep_seconds))

# ─────────────────────────────────────────────
# Render 루프 (상시 실행)
#  - 부팅 직후 강제발송 타임이어도 BOOT_MARGIN_MINUTES 내에는 스킵
#  - 같은 시각 중복 방지(LAST_SENT_TIME_FILE) 추가
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if already_running():
        sys.exit(0)

    ensure_persistent_files()

    print("🚀 fcanews bot 시작 (Render 상시 루프 모드)")
    now = datetime.now(KST)

    # 안내 로그
    base = now.replace(minute=0, second=0, microsecond=0)
    add_hours = (2 - (now.hour % 2)) % 2
    next_even_hour = base + timedelta(hours=add_hours if now.minute >= 7 else add_hours)
    print(f"⏸️ 초기 기동 모드: 첫 목표 발송은 {next_even_hour.strftime('%Y-%m-%d %H:%M:%S')} 예정")

    try:
        while True:
            current = datetime.now(KST)

            # 부팅 직후 강제 타임 보호
            if is_force_time(current) and is_boot_margin(current):
                print(f"⏸️ 부팅 직후 강제 발송 시간({current.strftime('%H:%M')}) 감지 → 스킵")
                wait_until_next_even_hour()
                continue

            if current.hour % 2 == 0 and current.minute < 7:
                run_bot()
            else:
                print(f"⏳ 대기 중... 현재 {current.strftime('%H:%M')} (짝수시 아님 또는 분>7)")
            wait_until_next_even_hour()
    except KeyboardInterrupt:
        print("🛑 종료 신호 감지 - 종료 중")
    finally:
        clear_lock()
