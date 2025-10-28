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
PERSISTENT_MOUNT = os.getenv("PERSISTENT_MOUNT", "/data")
os.makedirs(PERSISTENT_MOUNT, exist_ok=True)

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"

SENT_LOG_PATH = os.path.join(PERSISTENT_MOUNT, "sent_log.json")
LAST_SENT_TIME_FILE = os.path.join(PERSISTENT_MOUNT, "last_sent_time.txt")
LOCK_FILE = "/tmp/fcanews.lock"

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
DISPLAY_PER_CALL = 30
MAX_LOOPS = 5
REQUEST_TIMEOUT = 30
MIN_SEND_THRESHOLD = 3
UA = "Mozilla/5.0 (compatible; fcanewsbot/2.0; +https://t.me/)"
KST = timezone(timedelta(hours=9))

FORCE_HOURS = {0, 6, 12, 18}
BOOT_MARGIN_MINUTES = 2

# ─────────────────────────────────────────────
# 락 파일 관리
# ─────────────────────────────────────────────
def already_running():
    try:
        if os.path.exists(LOCK_FILE):
            mtime = os.path.getmtime(LOCK_FILE)
            if (time.time() - mtime) < 600:
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
# 동일 시각 중복 방지
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
    if len(sent_list) > 100:
        sent_list = sent_list[-100:]
    try:
        with open(SENT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(sent_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ sent_log 저장 예외:", e)

# ─────────────────────────────────────────────
# 뉴스 검색
# ─────────────────────────────────────────────
def search_recent_news(search_keywords, filter_keywords, sent_before):
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
        "User-Agent": UA,
    }

    collected = []
    pub_times = []
    total_fetched = 0
    loop_reports = []
    start = 1
    loop_count = 0
    detected_prev = False

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
            break

        title_filtered = 0
        duplicate_filtered = 0

        for item in items:
            title_raw = html.unescape(item.get("title", ""))
            title_clean = title_raw.replace("<b>", "").replace("</b>", "")
            link = (item.get("link") or "").strip()
            pub_raw = item.get("pubDate")
            if pub_raw:
                try:
                    pub_dt = parsedate_to_datetime(pub_raw).astimezone(KST)
                    pub_times.append(pub_dt)
                except Exception:
                    pass

            if any(k.lower() in title_clean.lower() for k in filter_keywords):
                title_filtered += 1
                if link in sent_before:
                    duplicate_filtered += 1
                    detected_prev = True
                else:
                    collected.append((title_clean, link))

        loop_reports.append({
            "call_no": loop_count,
            "fetched": fetched,
            "title_filtered": title_filtered,
            "duplicate_filtered": duplicate_filtered,
        })

        if detected_prev:
            print("✅ 이전 발송 기사 감지됨 → 호출 중단")
            break

        start += DISPLAY_PER_CALL

    if pub_times:
        latest_time = max(pub_times).strftime("%m-%d %H:%M")
        earliest_time = min(pub_times).strftime("%m-%d %H:%M")
    else:
        latest_time = earliest_time = "N/A"

    return collected, loop_reports, total_fetched, latest_time, earliest_time, detected_prev

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
# 메인 실행
# ─────────────────────────────────────────────
def run_bot():
    now = datetime.now(KST)
    print(f"🕒 현재 {now.strftime('%Y-%m-%d %H:%M:%S')} KST")

    if already_sent_this_hour():
        print("⏹️ 이미 이번 시각에 발송 완료 → 중복 방지")
        return

    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)
    sent_before = load_sent_log()

    found, loop_reports, total_fetched, latest_time, earliest_time, detected_prev = search_recent_news(
        search_keywords, filter_keywords, sent_before
    )

    total_title_filtered = sum(r["title_filtered"] for r in loop_reports)
    total_dup_filtered = sum(r["duplicate_filtered"] for r in loop_reports)
    api_calls = len(loop_reports)
    sent_count = len(found)

    should_send = sent_count >= MIN_SEND_THRESHOLD

    if should_send and found:
        lines = [f"{i+1}. <b>{html.escape(t)}</b>\n{l}\n" for i, (t, l) in enumerate(found)]
        message = "\n".join(lines)
        ok = send_to_telegram(message)
        if ok:
            for _, link in found:
                sent_before.add(link)
            save_sent_log(sent_before)
            mark_sent_now()

    report = (
        f"📊 {now.strftime('%H:%M:%S KST')} 기준\n"
        f"- {'✅ 발송' if should_send else '⏸️ 보류'}\n"
        f"- 키워드 호출 : <b>{total_fetched}</b>건 ({api_calls}회)\n"
        f"- 제목으로 필터링 후 : <b>{total_title_filtered}</b>건 (합계)\n"
        f"- 중복 필터링 후 : <b>{sent_count}</b>건 (=최종 발송)\n"
        f"- 이전 발송 기사 감지 : <b>{'SUCCESS' if detected_prev else 'FAIL'}</b>\n"
        f"- 호출 상세:\n" + "\n".join(
            [f"  • {r['call_no']}회차: {r['fetched']}건 / 제목 {r['title_filtered']} / 중복 {r['duplicate_filtered']}"
             for r in loop_reports]
        ) + f"\n- 기사시간: {latest_time} ~ {earliest_time}"
    )

    send_to_telegram(report, chat_id=ADMIN_CHAT_ID)
    print(f"✅ 처리 완료 ({sent_count}건)")

# ─────────────────────────────────────────────
# 대기 함수
# ─────────────────────────────────────────────
def wait_until_next_even_hour():
    now = datetime.now(KST)
    base = now.replace(minute=0, second=0, microsecond=0)
    add_hours = (2 - (now.hour % 2)) % 2
    if add_hours == 0 and now.minute >= 7:
        add_hours = 2
    next_even_hour = base + timedelta(hours=add_hours)
    sleep_seconds = (next_even_hour - now).total_seconds()
    if sleep_seconds < 60:
        sleep_seconds = 60
    print(f"🕓 다음 실행 예정: {next_even_hour.strftime('%H:%M')} (대기 {int(sleep_seconds/60)}분)")
    time.sleep(sleep_seconds)

# ─────────────────────────────────────────────
# 루프
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if already_running():
        sys.exit(0)

    ensure_persistent_files()

    print("🚀 fcanews bot 시작 (Render 상시 루프 모드)")

    try:
        while True:
            current = datetime.now(KST)
            if current.hour % 2 == 0 and current.minute < 7:
                run_bot()
            else:
                print(f"⏳ 대기 중... 현재 {current.strftime('%H:%M')}")
            wait_until_next_even_hour()
    except KeyboardInterrupt:
        print("🛑 종료 신호 감지 - 종료 중")
    finally:
        clear_lock()
