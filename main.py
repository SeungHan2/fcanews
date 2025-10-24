import os
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
import time
from datetime import datetime, timedelta, timezone

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
CALL_LOG_FILE = "call_count.json"
LOCK_FILE = "/tmp/fcanews.lock"

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
NEWS_COUNT = 20
DISPLAY_PER_CALL = 100
MAX_LOOPS = 2
REQUEST_TIMEOUT = 30
MIN_SEND_THRESHOLD = 5
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

def load_call_count():
    if os.path.exists(CALL_LOG_FILE):
        with open(CALL_LOG_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data.get("count", 0), data.get("articles", 0)
            except:
                return 0, 0
    return 0, 0

def save_call_count(count, articles):
    with open(CALL_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"count": count, "articles": articles}, f, ensure_ascii=False, indent=2)

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
    filter_pass_count = 0
    total_fetched = 0
    start = 1
    loop_count = 0
    stop_reason = None

    while len(collected) < NEWS_COUNT and loop_count < MAX_LOOPS:
        loop_count += 1
        query = " ".join(search_keywords)
        url = f"{base_url}?query={urllib.parse.quote(query)}&display={DISPLAY_PER_CALL}&start={start}&sort=date"

        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            stop_reason = f"요청 에러: {e}"
            break

        if r.status_code != 200:
            stop_reason = f"요청 실패: {r.status_code}"
            break

        items = r.json().get("items", [])
        total_fetched += len(items)

        if not items:
            stop_reason = "더 이상 결과 없음"
            break

        for item in items:
            title_raw = html.unescape(item.get("title", ""))
            title_clean = title_raw.replace("<b>", "").replace("</b>", "")
            link = (item.get("link") or "").strip()

            if link in sent_before:
                stop_reason = "이전 발송 기사 감지"
                break

            if any(k.lower() in title_clean.lower() for k in filter_keywords):
                filter_pass_count += 1
                collected.append((title_clean, link))
                if len(collected) >= NEWS_COUNT:
                    stop_reason = "필터 통과 최대치 도달"
                    break

        if stop_reason:
            break

        start += DISPLAY_PER_CALL
        if loop_count >= MAX_LOOPS:
            stop_reason = "호출 최대치 도달"
            break

    return collected, filter_pass_count, stop_reason, loop_count, total_fetched

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
    found, filter_pass_count, stop_reason, api_calls, total_fetched = search_recent_news(
        search_keywords, filter_keywords, sent_before
    )

    should_send = is_force_cycle or len(found) >= MIN_SEND_THRESHOLD

    if should_send and found:
        lines = [f"{i+1}. <b>{html.escape(t)}</b>\n{l}\n" for i, (t, l) in enumerate(found)]
        message = "📰 <b>새 뉴스 요약</b>\n\n" + "\n".join(lines) + "\n✅ 발송 완료!"
        send_to_telegram(message)
        sent_count = len(found)
    elif not found:
        send_to_telegram("🔎 새 뉴스가 없습니다!")
        sent_count = 0
    else:
        sent_count = 0

    if should_send and found:
        for _, link in found:
            sent_before.add(link)
        save_sent_log(sent_before)
    else:
        print("⏸️ 보류 상태 - sent_log.json 갱신 안 함")

    call_count, total_articles = load_call_count()
    call_count += 1
    total_articles += len(found)
    save_call_count(call_count, total_articles)

    admin_msg = (
        "📊 <b>관리자 리포트</b>\n"
        f"🕒 기준시간: {now.strftime('%Y-%m-%d %H:%M:%S (KST)')}\n"
        f"📤 발송여부: {'✅ 발송' if should_send else '⏸️ 보류'}\n"
        f"📰 발송기사: <b>{sent_count}개</b>\n"
        f"📈 네이버 API 호출: <b>{api_calls}회</b> ({total_fetched}건)\n"
        f"🔍 제목 필터 통과: <b>{filter_pass_count}개</b>\n"
        f"🛑 호출 중단 사유: <b>{stop_reason or '없음'}</b>"
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
# Render 루프
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if already_running():
        exit(0)

    print("🚀 fcanews bot (Render 크론형 정시 실행) 시작")

    try:
        while True:
            run_bot()
            wait_until_next_even_hour()
    except KeyboardInterrupt:
        print("🛑 종료 신호 감지 - 종료 중")
    finally:
        clear_lock()