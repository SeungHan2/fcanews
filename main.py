import os
import sys
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
import time
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ─────────────────────────────────────────────
# 실시간 로그
# ─────────────────────────────────────────────
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ─────────────────────────────────────────────
# 환경변수
# ─────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

PERSISTENT_MOUNT = os.getenv("PERSISTENT_MOUNT", "/data")
os.makedirs(PERSISTENT_MOUNT, exist_ok=True)

# ─────────────────────────────────────────────
# 설정/경로
# ─────────────────────────────────────────────
SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"
LAST_CHECKED_TIME_FILE = os.path.join(PERSISTENT_MOUNT, "last_checked_time.txt")
LOCK_FILE = "/tmp/fcanews.lock"

DISPLAY_PER_CALL = 30
MAX_LOOPS = 5
REQUEST_TIMEOUT = 30
MIN_SEND_THRESHOLD = 3
UA = "Mozilla/5.0 (compatible; fcanewsbot/2.1; +https://t.me/)"
KST = timezone(timedelta(hours=9))
FORCE_HOURS = {0, 6, 12, 18}  # 강제 발송 시각(1건 이상이면 발송)

# ─────────────────────────────────────────────
# 락 파일
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
# 시간 기록 (기준은 ‘최신 기사’)
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
# 파일/키워드
# ─────────────────────────────────────────────
def load_keywords(file_path):
    if not os.path.exists(file_path):
        print(f"⚠️ 키워드 파일 없음: {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

# ─────────────────────────────────────────────
# 텔레그램
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
# 뉴스 검색: 시간 필터 → 제목 필터
# ─────────────────────────────────────────────
def search_recent_news(search_keywords, filter_keywords):
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
        "User-Agent": UA,
    }

    last_checked = get_last_checked_time()  # 기준 시각 (직전 루프에서 확인한 최신 기사)
    collected = []          # 제목 필터 통과 기사 (발송 후보)
    pub_times = []          # 시간 필터 통과 기사들의 pubDate (시간범위 계산용)
    loop_reports = []       # 관리자 리포트(호출별 통계)
    start = 1
    loop_count = 0
    stop_due_to_time = False

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
        if not items:
            break

        time_filtered = 0  # 시간 필터 통과 수

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

            # ① 시간 필터: 지난 기준시각 이후만
            if last_checked and pub_dt <= last_checked:
                # 이 호출 구간에서 과거 기사 등장 → 이후 페이지는 볼 필요 없음
                stop_due_to_time = True
                continue

            # 최신 기사 집합(시간범위용)으로 기록
            pub_times.append(pub_dt)
            time_filtered += 1

            # ② 제목 필터
            if not any(k.lower() in title_clean.lower() for k in filter_keywords):
                continue

            # 발송 후보로 적재
            collected.append((title_clean, link))

        loop_reports.append({
            "call_no": loop_count,
            "fetched": fetched,
            "time_filtered": time_filtered,
        })

        if stop_due_to_time:
            print("🕓 이전 기준시각보다 오래된 기사 감지 → 호출 중단")
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
    TEST_MODE = os.getenv("TEST_MODE") == "True"
    current_hour = now.hour

    print(f"🕒 현재 {now.strftime('%Y-%m-%d %H:%M:%S')} KST")

    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)

    found, loop_reports, latest_time, earliest_time, pub_times = search_recent_news(
        search_keywords, filter_keywords
    )

    sent_count = len(found)
    total_time_filtered = sum(r["time_filtered"] for r in loop_reports)
    should_send = (sent_count >= 1 if current_hour in FORCE_HOURS else sent_count >= MIN_SEND_THRESHOLD)

    # 본 채널 발송
    if should_send and found:
        message = "\n".join([f"{i+1}. <b>{html.escape(t)}</b>\n{l}\n" for i, (t, l) in enumerate(found)])
        if not TEST_MODE:
            ok = send_to_telegram(message)
            if ok and pub_times:
                # ⚠️ ‘최신 기사’ 기준으로 시간 갱신 (발송 기준 아님)
                mark_checked_time(max(pub_times))
        else:
            print("🧪 테스트 모드: 본 채널 발송 스킵")
            # 테스트 모드에서는 시간 기준을 업데이트하지 않음 (미리보기/검증용)

    # 관리자 리포트 (시간 필터 기준 통계)
    report = []
    report.append(f"✅ 발송 [{sent_count}건] ({now.strftime('%H:%M:%S KST')} 기준)" if should_send
                  else f"⏸️ 보류 [{sent_count}건] ({now.strftime('%H:%M:%S KST')} 기준)")
    for r in loop_reports:
        report.append(f"({r['call_no']}차) 최신{r['time_filtered']} / 호출{r['fetched']}")
    report.append(f"(제목 통과) 발송 {sent_count} / 최신 {total_time_filtered}")
    report.append(f"【{latest_time} ~ {earliest_time}】")  # ← 시간 필터 통과 기사들의 범위
    send_to_telegram("\n".join(report), chat_id=ADMIN_CHAT_ID)

# ─────────────────────────────────────────────
# 루프
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if already_running():
        sys.exit(0)

    print("🚀 fcanews bot 시작 (시간 필터 + 개선 리포트 / 중복필터 제거)")
    try:
        while True:
            now = datetime.now(KST)
            if now.hour % 2 == 0 and now.minute < 7:
                run_bot()
                time.sleep(420)  # 동일 시각 중복 실행 방지(7분 대기)
            else:
                print(f"⏳ 대기 중... 현재 {now.strftime('%H:%M')}")
                time.sleep(60)
    except KeyboardInterrupt:
        print("🛑 종료 신호 감지")
    finally:
        clear_lock()
