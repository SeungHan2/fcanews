import os
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
import email.utils

# 환경변수 로드 (.env)
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

def load_keywords(file_path):
    """텍스트 파일에서 키워드 목록 불러오기"""
    if not os.path.exists(file_path):
        print(f"⚠️ 키워드 파일이 없습니다: {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def load_sent_log():
    """이전 발송 기록 불러오기"""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_sent_log(sent_ids):
    """발송 기록 저장"""
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_ids), f, ensure_ascii=False, indent=2)

def parse_pubdate(pubdate_str):
    """네이버 pubDate 문자열 파싱 (datetime으로 변환)"""
    try:
        return email.utils.parsedate_to_datetime(pubdate_str)
    except Exception:
        return None

def search_recent_news(search_keywords, filter_keywords, sent_before):
    """
    네이버 뉴스 API 반복 요청
    - search_keywords : API 검색용 (AND 조건)
    - filter_keywords : 제목 필터링용 (OR 조건)
    - 이전에 보낸 기사(sent_before)에 도달하면 중단
    """
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
        query = " ".join(search_keywords)  # 네이버는 공백 AND 검색
        url = f"{base_url}?query={urllib.parse.quote(query)}&display={DISPLAY_PER_CALL}&start={start}&sort=date"
        response = requests.get(url, headers=headers)

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

            # 이전 리포트 기사 발견 → 중단
            if link in sent_before:
                stop_search = True
                break

            # 제목에 필터 키워드 중 하나라도 포함되면 저장
            if any(k.lower() in title_clean.lower() for k in filter_keywords):
                collected.append((title_clean, link))

        start += DISPLAY_PER_CALL
        if start > 1000:
            break

    return collected[:NEWS_COUNT]

def send_to_telegram(message):
    """텔레그램 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM 환경변수가 설정되지 않았습니다.")
        return

    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    response = requests.post(send_url, data=payload)
    if response.status_code == 200:
        print("✅ 텔레그램 전송 완료")
    else:
        print("❌ 텔레그램 전송 실패:", response.text)

if __name__ == "__main__":
    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)

    if not search_keywords:
        print("⚠️ 검색 키워드가 없습니다.")
        exit()
    if not filter_keywords:
        print("⚠️ 필터링 키워드가 없습니다.")
        exit()

    sent_before = load_sent_log()
    news_list = search_recent_news(search_keywords, filter_keywords, sent_before)

    # 중복 제외 후 로그 업데이트
    new_items = [(t, l) for (t, l) in news_list if l not in sent_before]
    for _, link in new_items:
        sent_before.add(link)
    save_sent_log(sent_before)

    count = len(new_items)
    if count == 0:
        message = "🔎 새 뉴스가 없습니다!"
    else:
        message_lines = [f"🕓 <b>새 뉴스 {count}</b>\n"]
        for i, (title, link) in enumerate(new_items, start=1):
            message_lines.append(f"{i}. {html.escape(title)}\n{link}\n")
        message = "\n".join(message_lines)

    print(message)
    send_to_telegram(message)
