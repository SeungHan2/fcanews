# ===============================================
# main.py — fcanews Final Version (Render 재시작 방지)
# ===============================================
import os
import sys
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ─────────────────────────────────────────────
# 환경 / 기본 설정
# ─────────────────────────────────────────────
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

PERSISTENT_MOUNT = os.getenv("PERSISTENT_MOUNT", "/data")
os.makedirs(PERSISTENT_MOUNT, exist_ok=True)

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"
LAST_CHECKED_TIME_FILE = os.path.join(PERSISTENT_MOUNT, "last_checked_time.txt")
LOCK_FILE = "/tmp/fcanews.lock"

DISPLAY_PER_CALL = 30
MAX_LOOPS = 5
REQUEST_TIMEOUT = 30
MIN_SEND_THRESHOLD = 3
UA = "Mozilla/5.0 (compatible; fcanewsbot/3.0; +https://t.me/)"
KST = timezone(timedelta(hours=9))
FORCE_HOURS = {0, 6, 12, 18}

# 실행 허용 구간 설정
WAIT_BEFORE_SEC = 0   # 정시 전 대기 (초)
WAIT_AFTER_MIN = 3    # 정시 이후 허용 분

# ─────────────────────────────────────────────
# 락 파일 관리
# ─────────────────────────────────────────────
def already_running():
    if os.path.exists(LOCK_FILE):
        mtime = os.path.getmtime(LOCK_FILE)
        if (time.time() - mtime) < 600:
            print("⚠️ 이미 실행 중인 프로세스 감지 → 종료")
            return True
    with open(LOCK_FILE, "w") as f:
        f.write(datetime.now().isoformat())
    return False

def clear_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            print("🧹 락 파일 제거 완료")
    except Exception as e:
        print("⚠️ 락 파일 제거 예외:", e)

# ─────────────────────────────────────────────
# 시간 기록
# ─────────────────────────────────────────────
def get_last_checked_time():
    if not os.path.exists(LAST_CHECKED_TIME_FILE):
        return None
    try:
        with open(LAST_CHECKED_TIME_FILE, "r", encoding="utf-8") as f:
            return datetime.fromisoformat(f.read().strip())
    except Exception:
        return None

def mark_checked_time(latest_pub):
    try:
        with open(LAST_CHECKED_TIME_FILE, "w", encoding="utf-8") as f:
            f.write(latest_pub.isoformat())
    except Exception as e:
        print("⚠️ 시간 기록 예외:", e)

# ─────────────────────────────────────────────
# 파일 로드
# ─────────────────────────────────────────────
def load_keywords(file_path):
    if not os.path.exists(file_path):
        print(f"⚠️ 키워드 파일 없음: {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

# ─────────────────────────────────────────────
# 텔레그램 발송
# ─────────────────────────────────────────────
def send_to_telegram(message, chat_id=None):
    chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        print("⚠️ TELEGRAM 환경변수 없음")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            print(f"✅ 텔레그램 전송 완료 ({chat_id})")
            return True
        print("❌ 텔레그램 전송 실패:", r.text)
        return False
    except Exception as e:
        print("❌ 텔레그램 전송 예외:", e)
        return False

# ─────────────────────────────────────────────
# 뉴스 검색
# ─────────────────────────────────────────────
def search_recent_news(search_keywords, filter_keywords):
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
        "User-Agent": UA,
    }

    last_checked = get_last_checked_time()
    collected, pub_times, loop_reports = [], [], []
    start, loop_count, stop_due_to_time = 1, 0, False

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
        if not items:
            break

        time_filtered = 0
        for item in items:
            title_raw = html.unescape(item.get("title", ""))
            title_clean = title_raw.replace("<b>", "").replace("</b>", "")
            link = (item.get("link") or "").strip()
            pub_raw = item.get("pubDate")
            if not pub_raw:
                continue
            try:
                pub_dt = parsedate_to_datetime(pub_raw).astimezone(KST)
            except Exception:
                continue
            if last_checked and pub_dt <= last_checked:
                stop_due_to_time = True
                continue
            pub_times.append(pub_dt)
            time_filtered += 1
            if not any(k.lower() in title_clean.lower() for k in filter_keywords):
                continue
            collected.append((title_clean, link))

        loop_reports.append({
            "call_no": loop_count,
            "fetched": len(items),
            "time_filtered": time_filtered,
        })
        if stop_due_to_time:
            break
        start += DISPLAY_PER_CALL

    latest_time = max(pub_times).strftime("%m-%d(%H:%M)") if pub_times else "N/A"
    earliest_time = min(pub_times).strftime("%m-%d(%H:%M)") if pub_times else "N/A"
    return collected, loop_reports, latest_time, earliest_time, pub_times

# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
def run_bot():
    now = datetime.now(KST)
    current_hour = now.hour
    print(f"🕒 현재 {now.strftime('%Y-%m-%d %H:%M:%S')} KST")

    # 실행 시각 검사
    if not (current_hour % 2 == 0 and 0 <= now.minute <= WAIT_AFTER_MIN):
        print("⏸️ 비정시 실행 → 관리자 리포트만 발송")
        send_to_telegram(f"⚙️ 비정시 실행 감지 ({now.strftime('%H:%M')})", chat_id=ADMIN_CHAT_ID)
        return

    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(search_keywords, filter_keywords)

    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)
    should_send = (sent_count >= 1 if current_hour in FORCE_HOURS else sent_count >= MIN_SEND_THRESHOLD)

    if should_send and found:
        message = "\n".join([f"{i+1}. <b>{html.escape(t)}</b>\n{l}\n" for i, (t, l) in enumerate(found)])
        ok = send_to_telegram(message)
        if ok and pub_times:
            latest_pub = max(pub_times)
            mark_checked_time(latest_pub)
            print(f"🕓 최신 기사 시각 갱신 완료 → {latest_pub.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print("⏸️ 본채널 발송 조건 미충족 → 관리자 리포트만 발송")

# ─────────────────────────────────────────────
# 관리자 메시지 포맷 (요청 버전)
# ─────────────────────────────────────────────
    report = []
    
    # 1️⃣ 헤더 줄
    status_icon = "✅" if should_send and found else "⏸️"
    status_text = "발송" if should_send and found else "보류"
    report.append(f"{status_icon} <b>{status_text}</b> [<b>{sent_count}</b>건] ({now.strftime('%H:%M:%S KST')} 기준)")
    
    # 2️⃣ 루프별 통계
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신<b>{r['time_filtered']}</b> / 호출{r['fetched']}")
    
    # 3️⃣ 제목 통과 통계
    report.append(f"제목통과 <b>{sent_count}</b> / (최신<b>{total_time_filtered}</b>)")
    
    # 4️⃣ 최신 기사 시간 범위
    report.append(f"(최신기사시간) {latest_time} ~ {earliest_time}")
    
    # 5️⃣ 전송
    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)


# ─────────────────────────────────────────────
# 실행 엔트리
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if already_running():
        sys.exit(0)
    print("🚀 fcanews bot 시작 (정시±3분 제어 / Render 재시작 방지)")

    run_bot()
    clear_lock()
    print("✅ 작업 종료 (Render suspend 대기)")

    # ─────────────────────────────────────────────
    # Render 자동 재시작 방지: 프로세스를 유지
    # ─────────────────────────────────────────────
    while True:
        now = datetime.now(KST)
        print(f"⏳ Render 유지 중... ({now.strftime('%H:%M:%S')})")
        time.sleep(60)
