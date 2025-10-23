
import os
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json

# 환경변수 로드 (.env)
load_dotenv()

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KEYWORDS_FILE = "keywords.txt"
LOG_FILE = "sent_log.json"
NEWS_COUNT = 10

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

def search_naver_news(keywords, display=20):
    query = " ".join(keywords)
    base_url = "https://openapi.naver.com/v1/search/news.json"
    encoded_query = urllib.parse.quote(query)
    url = f"{base_url}?query={encoded_query}&display={display}&sort=date"
    headers = {"X-Naver-Client-Id": CLIENT_ID, "X-Naver-Client-Secret": CLIENT_SECRET}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print("❌ 요청 실패:", response.status_code, response.text)
        return []
    data = response.json()
    results = []
    for item in data.get("items", []):
        title_raw = html.unescape(item["title"])
        title_clean = title_raw.replace("<b>", "").replace("</b>", "")
        link = item["link"]
        if any(k.lower() in title_clean.lower() for k in keywords):
            results.append((title_clean, link))
        if len(results) >= display:
            break
    return results

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
    news_list = search_naver_news(keywords)
    new_items = [(title, link) for title, link in news_list if link not in sent_before]
    for _, link in new_items:
        sent_before.add(link)
    save_sent_log(sent_before)

    count = len(new_items)
    if count == 0:
        message = "🔎 새 뉴스가 없습니다."
    else:
        message_lines = [f"🕓 <b>6시간 새 뉴스 {count}</b>\n"]
        for i, (title, link) in enumerate(new_items[:NEWS_COUNT], start=1):
            message_lines.append(f"{i}. {html.escape(title)}\n{link}\n")
        message = "\n".join(message_lines)

    print(message)
    send_to_telegram(message)
