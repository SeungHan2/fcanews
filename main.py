import os
import time
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
import email.utils
from bs4 import BeautifulSoup
from datetime import datetime

# ───────────────────────────────────────────────────────────
# 환경변수 로드 (.env)
# ───────────────────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"
LOG_FILE = "sent_log.json"

NEWS_COUNT = 10
DISPLAY_PER_CALL = 100
MAX_LOOPS = 10
REQUEST_TIMEOUT = 10
PAUSE_BETWEEN_MSGS = 0.5

UA = "Mozilla/5.0 (compatible; fcanewsbot/1.0; +https://t.me/)"

# ───────────────────────────────────────────────────────────
# 유틸 함수들
# ───────────────────────────────────────────────────────────
def load_keywords(file_path):
    if not os.path.exists(file_path):
        print(f"⚠️ 키워드 파일이 없습니다: {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def load_sent_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except Exception:
                return set()
    return set()

def save_sent_log(sent_ids):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_ids), f, ensure_ascii=False, indent=2)

# ───────────────────────────────────────────────────────────
# 네이버 뉴스 검색
# ───────────────────────────────────────────────────────────
def search_recent_news(search_keywords, filter_keywords, sent_before):
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET
    }

    collected = []
    start = 1
    loop_count = 0
    stop_search = False

    while len(collected) < NEWS_COUNT and loop_count < MAX_LOOPS and not stop_search:
        loop_count += 1
        query = " ".join(search_keywords)
        url = f"{base_url}?query={urllib.parse.quote(query)}&display={DISPLAY_PER_CALL}&start={start}&sort=date"
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print("❌ 요청 에러:", e)
            break

        if response.status_code != 200:
            print("❌ 요청 실패:", response.status_code, response.text)
            break

        data = response.json()
        items = data.get("items", [])
        if not items:
            break

        for item in items:
            title_raw = html.unescape(item["title"])
            title_clean = title_raw.replace("<b>", "").replace("</b>", "")
            link = item["link"]

            if link in sent_before:
                stop_search = True
                break

            if any(k.lower() in title_clean.lower() for k in filter_keywords):
                collected.append((title_clean, link))

        start += DISPLAY_PER_CALL
        if start > 1000:
            break

    return collected[:NEWS_COUNT]

# ───────────────────────────────────────────────────────────
# 텔레그램 전송 함수
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
        "disable_web_page_preview": False
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
# 메인
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)

    sent_before = load_sent_log()
    found = search_recent_news(search_keywords, filter_keywords, sent_before)
    new_items = [(t, l) for (t, l) in found if l not in sent_before]

    # 새 뉴스가 없을 때
    if not new_items:
        send_to_telegram("🔎 새 뉴스가 없습니다!")
        exit(0)

    # ✅ 공지 메시지 (현재 시각 기준)
    now = datetime.now()
    date_str = now.strftime("%Y.%m.%d(%a) %H시")
    header_msg = f"📢 <b>{date_str} 기준 새 뉴스 {len(new_items)}개 입니다.</b>"
    send_to_telegram(header_msg)
    time.sleep(1.0)

    # ✅ 각 뉴스 전송
    sent_count = 0
    for title, link in new_items:
        viewer_url = f"https://fcanews-viewer.onrender.com/view?url={urllib.parse.quote(link)}"
        message = f"📰 <b>{html.escape(title)}</b>\n{viewer_url}"

        if send_to_telegram(message):
            sent_before.add(link)
            sent_count += 1
            time.sleep(PAUSE_BETWEEN_MSGS)

    # ✅ 마지막 공지
    send_to_telegram("✅ 발송 완료!")

    # 로그 저장
    save_sent_log(sent_before)
    print(f"✅ 전송 완료: {sent_count}건")
