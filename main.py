# ===============================================
# main.py — fcanews 자동 발송 (짝수시 정시 / /data 기록 유지 / 관리자 리포트 무제한)
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
LAST_SENT_FILE = os.path.join(PERSISTENT_MOUNT, "last_sent_time.txt")
LAST_CHECKED_FILE = os.path.join(PERSISTENT_MOUNT, "last_checked_time.txt")
LOCK_FILE = "/tmp/fcanews.lock"

DISPLAY_PER_CALL = 30
MAX_LOOPS = 5
REQUEST_TIMEOUT = 30
MIN_SEND_THRESHOLD = 3
UA = "Mozilla/5.0 (compatible; fcanewsbot/3.0; +https://t.me/)"
KST = timezone(timedelta(hours=9))
FORCE_HOURS = {0, 6, 12, 18}

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
    except Exception as e:
        print("⚠️ 락 파일 제거 예외:", e)

# ─────────────────────────────────────────────
# 시간 기록 (기사 기준)
# ─────────────────────────────────────────────
def get_last_checked_time():
    if not os.path.exists(LAST_CHECKED_FILE):
        return None
    try:
        with open(LAST_CHECKED_FILE, "r") as f:
            return datetime.fromisoformat(f.read().strip())
    except Exception:
        return None

def mark_checked_time(latest_pub):
    try:
        with open(LAST_CHECKED_FILE, "w") as f:
            f.write(latest_pub.isoformat())
        print(f"🕓 최신 기사 시각 갱신: {latest_pub.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        print("⚠️ 시간 기록 예외:", e)

# ─────────────────────────────────────────────
# 발송 기록 (중복 방지)
# ─────────────────────────────────────────────
def already_sent_this_hour():
    if not os.path.exists(LAST_SENT_FILE):
        return False
    try:
        with open(LAST_SENT_FILE, "r") as f:
            last_sent = datetime.fromisoformat(f.read().strip())
    except Exception:
        return False
    now = datetime.now(KST)
    return last_sent.astimezone(KST).strftime("%Y-%m-%d %H") == now.strftime("%Y-%m-%d %H")

def mark_sent_now():
    now = datetime.now(KST)
    with open(LAST_SENT_FILE, "w") as f:
        f.write(now.isoformat())

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
        return r.status_code == 200
    except Exception as e:
        print("❌ 텔레그램 전송 예외:", e)
        return False

# ─────────────────────────────────────────────
# 뉴스 검색 (최적화 버전)
# ─────────────────────────────────────────────
def search_recent_news(search_keywords, filter_keywords):
    """
    최신 기사만 효율적으로 검색:
    - 30건이 모두 최신 기사일 때만 다음 페이지 호출
    - 이전 기사 등장 시 즉시 종료
    """
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
        "User-Agent": UA,
    }

    last_checked = get_last_checked_time()
    collected, pub_times, loop_reports = [], [], []
    stop_due_to_old = False

    for loop_count in range(1, MAX_LOOPS + 1):
        query = " ".join(search_keywords)
        start = (loop_count - 1) * DISPLAY_PER_CALL + 1
        url = f"{base_url}?query={urllib.parse.quote(query)}&display={DISPLAY_PER_CALL}&start={start}&sort=date"

        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print("❌ 요청 예외:", e)
            break

        if r.status_code != 200:
            print(f"❌ 요청 실패: {r.status_code} {r.text}")
            break

        items = r.json().get("items", [])
        if not items:
            break

        time_filtered = 0
        new_articles = 0

        for item in items:
            title = html.unescape(item.get("title", "")).replace("<b>", "").replace("</b>", "")
            link = (item.get("link") or "").strip()
            pub_raw = item.get("pubDate")
            if not pub_raw:
                continue

            try:
                pub_dt = parsedate_to_datetime(pub_raw).astimezone(KST)
            except Exception:
                continue

            # ✅ 시간 필터: 이전 기사 등장 시 종료 플래그
            if last_checked and pub_dt <= last_checked:
                stop_due_to_old = True
                continue

            new_articles += 1
            pub_times.append(pub_dt)
            time_filtered += 1

            if not any(k.lower() in title.lower() for k in filter_keywords):
                continue
            collected.append((title, link))

        loop_reports.append({
            "call_no": loop_count,
            "fetched": len(items),
            "time_filtered": time_filtered,
        })

        # ✅ 루프 종료 조건
        if stop_due_to_old:
            print(f"⏹️ {loop_count}차에서 이전 기사 등장 → 루프 종료")
            break
        if new_articles < DISPLAY_PER_CALL:
            print(f"⏹️ {loop_count}차에서 신규 기사 부족({new_articles}/{DISPLAY_PER_CALL}) → 루프 종료")
            break

    latest_time = max(pub_times).strftime("%m-%d(%H:%M)") if pub_times else "N/A"
    earliest_time = min(pub_times).strftime("%m-%d(%H:%M)") if pub_times else "N/A"
    return collected, loop_reports, latest_time, earliest_time, pub_times


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
def run_bot():
    now = datetime.now(KST)
    print(f"\n🕒 실행: {now.strftime('%Y-%m-%d %H:%M:%S')} KST")

    # ✅ 짝수시 정시(00분)만 발송
    if now.minute != 0 or now.hour % 2 != 0:
        print("⏸️ 발송 타임이 아님 → 스킵")
        return

    if already_sent_this_hour():
        print("⏹️ 이미 이번 시각에 발송 완료 → 중복 방지")
        return

    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)
    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(search_keywords, filter_keywords)

    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)
    should_send = (sent_count >= 1 if now.hour in FORCE_HOURS else sent_count >= MIN_SEND_THRESHOLD)

    if should_send and found:
        msg = "\n".join([f"{i+1}. <b>{html.escape(t)}</b>\n{l}" for i, (t, l) in enumerate(found)])
        if send_to_telegram(msg):
            mark_sent_now()
            if pub_times:
                mark_checked_time(max(pub_times))
            print("✅ 본 채널 발송 완료")
    else:
        print("⏸️ 본채널 발송 조건 미충족")

    # ✅ 관리자 리포트 (짝수시마다 1회)
    now = datetime.now(KST)
    status_icon = "✅" if should_send and found else "⏸️"
    status_text = "발송" if should_send and found else "보류"
    
    report = []
    # 1️⃣ 1행 — 상태
    report.append(f"{status_icon} {status_text} [<b>{len(found)}</b>건] ({now.strftime('%H:%M:%S 기준')})")
    
    # 2️⃣ 각 호출 결과
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신<b>{r['time_filtered']}</b> / 호출{r['fetched']}")
    
    # 3️⃣ 제목통과 / 최신합계
    report.append(f"제목통과<b>{len(found)}</b> / 최신{sum(r['time_filtered'] for r in loop_reports)}")
    
    # 4️⃣ 최신기사 시간
    report.append(f"(최신기사시간) {latest_time} ~ {earliest_time}")
    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)
    print("📊 관리자 리포트 발송 완료")

# ─────────────────────────────────────────────
# 2시간 루프
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if already_running():
        sys.exit(0)

    print("🚀 fcanews bot 시작 (짝수시 정시 / 2시간 간격)")
    while True:
        try:
            now = datetime.now(KST)
            next_hour = (now.hour + 2) // 2 * 2
            if next_hour >= 24:
                next_hour -= 24
                next_day = now + timedelta(days=1)
            else:
                next_day = now

            target_time = next_day.replace(hour=next_hour, minute=0, second=0, microsecond=0)
            wait_seconds = (target_time - now).total_seconds()
            if wait_seconds > 0:
                print(f"⏰ 다음 실행 시각: {target_time.strftime('%Y-%m-%d %H:%M:%S')} KST ({int(wait_seconds/60)}분 후)")
                time.sleep(wait_seconds)

            run_bot()

        except Exception as e:
            print("❌ 루프 예외 발생:", e)
            time.sleep(60)

        finally:
            clear_lock()
            time.sleep(10)
