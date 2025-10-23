import os
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
from datetime import datetime, timedelta, timezone
import email.utils

# 환경변수 로드 (.env)
load_dotenv()

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KEYWORDS_FILE = "keywords.txt"
LOG_FILE = "sent_log.json"
NEWS_COUNT = 10
DISPLAY_PER_CALL = 100
MAX_LOOPS = 10
TIME_WINDOW_HOURS = 6

KST = timezone(timedelta(hours=9))


def load_keywords(file_path):
    if not os.path.exists(file_path):
        print(f"⚠️ 키워드 파일이 없습니다: {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_sent_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent_log(sent_ids):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_ids), f, ensure_ascii=False, indent=2)


def parse_pubdate(pubdate_str):
    try:
        dt = email.utils.parsedate_to_datetime(pubdate_str)
        return dt.astimezone(KST)
    except Exception:
        return None


def search_recent_news(keywords):
    """
    네이버 뉴스 API에서 최대 10회 반복으로 뉴스 검색
    - 100개씩 불러오고 필터링
    - 6시간 이내 기사만 유지
    - 제목에 키워드 포함
    """
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET
    }

    collected = []
    start = 1
    loop_count = 0
    now_kst = datetime.now(KST)
    cutoff_time = now_kst - timedelta(hours=TIME_WINDOW_HOURS)

    while len(collected) < NEWS_COUNT and loop_count < MAX_LOOPS:
        loop_count += 1
        url = f"{base_url}?query={urllib.parse.quote(' '.join(keywords))}&display={DISPLAY_PER_CALL}&start={start}&sort=date"
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
            pubdate = parse_pubdate(item.get("pubDate", ""))

            if not pubdate or pubdate < cutoff_time:
                continue  # 6시간 이전 기사 제외

            if any(k.lower() in title_clean.lower() for k in keywords):
                collected.append((title_clean, link, pubdate))

        start += DISPLAY_PER_CALL
        if start > 1000:
            break

    collected.sort(key=lambda x: x[2], reverse=True)
    return collected[:NEWS_COUNT]


def send_to_telegram(message):
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
    keywords = load_keywords(KEYWORDS_FILE)
    if not keywords:
        print("⚠️ 키워드가 없습니다.")
        exit()

    sent_before = load_sent_log()
    news_list = search_recent_news(keywords)

    # 중복 제거
    new_items = [(t, l) for (t, l, _) in news_list if l not in sent_before]
    for _, link in new_items:
        sent_before.add(link)
    save_sent_log(sent_before)

    count = len(new_items)

    if count == 0:
        message = "🔎 새 뉴스가 없습니다!"
    else:
        message_lines = [f"🕓 <b>6시간 새 뉴스 {count}</b>\\n"]
        for i, (title, link) in enumerate(new_items, start=1):
            message_lines.append(f"{i}. {html.escape(title)}\\n{link}\\n")
        message = "\\n".join(message_lines)

    print(message)
    send_to_telegram(message)
