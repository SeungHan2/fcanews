import os
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
from datetime import datetime, timedelta

# ───────────────────────────────────────────────────────────
# 환경 변수 로드 (.env)
# ───────────────────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"
LOG_FILE = "sent_log.json"

NEWS_COUNT = 50             # 한 번에 최대 수집 기사 수
DISPLAY_PER_CALL = 100
MAX_LOOPS = 10
REQUEST_TIMEOUT = 10
MIN_SEND_THRESHOLD = 5       # 5개 미만이면 스킵
UA = "Mozilla/5.0 (compatible; fcanewsbot/1.0; +https://t.me/)"

# GitHub Actions 환경
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "")
IS_TEST_RUN = EVENT_NAME == "workflow_dispatch"

# ───────────────────────────────────────────────────────────
# 유틸 함수
# ───────────────────────────────────────────────────────────
def load_keywords(file_path):
    if not os.path.exists(file_path):
        print(f"⚠️ 키워드 파일이 없습니다: {file_path}")
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
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(sent_ids)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 로그 저장 실패: {e}")

def clear_sent_log():
    """6시간마다 로그 초기화"""
    try:
        if os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
            print("🧹 로그 초기화 완료")
    except Exception as e:
        print(f"⚠️ 로그 초기화 실패: {e}")

# ───────────────────────────────────────────────────────────
# 네이버 뉴스 검색
# ───────────────────────────────────────────────────────────
def search_recent_news(search_keywords, filter_keywords, sent_before):
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": CLIENT_ID, "X-Naver-Client-Secret": CLIENT_SECRET}

    collected = []
    seen_links = set()
    start = 1
    loop_count = 0

    while len(collected) < NEWS_COUNT and loop_count < MAX_LOOPS:
        loop_count += 1
        query = " ".join(search_keywords)
        url = f"{base_url}?query={urllib.parse.quote(query)}&display={DISPLAY_PER_CALL}&start={start}&sort=date"

        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print("❌ 요청 에러:", e)
            break

        if r.status_code != 200:
            print("❌ 요청 실패:", r.status_code, r.text)
            break

        items = r.json().get("items", [])
        if not items:
            break

        for item in items:
            title_raw = html.unescape(item.get("title", ""))
            title_clean = title_raw.replace("<b>", "").replace("</b>", "")
            link = (item.get("link") or "").strip()

            if not link or link in seen_links or link in sent_before:
                continue

            if any(k.lower() in title_clean.lower() for k in filter_keywords):
                collected.append((title_clean, link))
                seen_links.add(link)

            if len(collected) >= NEWS_COUNT:
                break

        start += DISPLAY_PER_CALL
        if start > 1000:
            break

    return collected

# ───────────────────────────────────────────────────────────
# 텔레그램 전송
# ───────────────────────────────────────────────────────────
def send_to_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM 환경변수가 설정되지 않았습니다.")
        return False

    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        r = requests.post(send_url, data=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            print("✅ 텔레그램 전송 완료")
            return True
        else:
            print("❌ 텔레그램 전송 실패:", r.text)
            return False
    except Exception as e:
        print("❌ 텔레그램 전송 예외:", e)
        return False

# ───────────────────────────────────────────────────────────
# 메인 로직
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)

    # 현재 시각
    now = datetime.now()
    hour = now.hour
    is_six_hour_cycle = (hour % 6 == 0)  # 6시간마다 로그 비움

    print(f"🕒 현재 시각: {hour}시 | 6시간 주기 여부: {is_six_hour_cycle} | 테스트 런: {IS_TEST_RUN}")

    sent_before = set() if IS_TEST_RUN else load_sent_log()
    found = search_recent_news(search_keywords, filter_keywords, sent_before)

    # 조건 1: 테스트 런 → 무조건 발송
    # 조건 2: 6시간 주기 → 무조건 발송 후 로그 비움
    # 조건 3: 그 외 → 기사 5개 미만이면 스킵
    if not IS_TEST_RUN and not is_six_hour_cycle and len(found) < MIN_SEND_THRESHOLD:
        print(f"⏸ 기사 {len(found)}개 (<{MIN_SEND_THRESHOLD}), 발송 생략")
        exit(0)

    if not found:
        send_to_telegram("🔎 새 뉴스가 없습니다!")
        exit(0)

    # 공지 메시지
    date_str = now.strftime("%Y.%m.%d(%a) %H시")
    header_msg = f"📢 <b>{date_str} 기준 새 뉴스 {len(found)}개 입니다.</b>\n\n"

    # 뉴스 본문
    body_lines = []
    for i, (title, link) in enumerate(found, start=1):
        line = f"{i}. <b>{html.escape(title)}</b>\n{link}\n"
        body_lines.append(line)
        if not IS_TEST_RUN:
            sent_before.add(link)

    footer_msg = "\n✅ 발송 완료!"
    message = header_msg + "\n".join(body_lines) + footer_msg
    send_to_telegram(message)

    # 로그 관리
    if not IS_TEST_RUN:
        if is_six_hour_cycle:
            clear_sent_log()
        else:
            save_sent_log(sent_before)

    print(f"✅ 전송 완료 ({len(found)}건) | 모드: {'테스트' if IS_TEST_RUN else '정상'}")
