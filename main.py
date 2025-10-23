import os
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
from datetime import datetime

# ─────────────────────────────────────────────
# 환경 변수 로드
# ─────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"
LOG_FILE = "sent_log.json"

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
NEWS_COUNT = 20             # 최대 수집 기사 수
DISPLAY_PER_CALL = 100      # 네이버 API 한 번에 요청할 기사 수
MAX_LOOPS = 5               # 최대 5회 반복 호출 (100x5=500개까지)
REQUEST_TIMEOUT = 30        # 요청 타임아웃(초)
MIN_SEND_THRESHOLD = 5      # 5개 미만이면 스킵
UA = "Mozilla/5.0 (compatible; fcanewsbot/1.0; +https://t.me/)"

EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "")
IS_TEST_RUN = EVENT_NAME == "workflow_dispatch"

# ─────────────────────────────────────────────
# 파일 입출력 유틸
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
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(sent_ids)), f, ensure_ascii=False, indent=2)

def clear_sent_log():
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
        print("🧹 로그 초기화 완료")

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
    start = 1
    loop_count = 0
    stop_search = False

    while len(collected) < NEWS_COUNT and loop_count < MAX_LOOPS and not stop_search:
        loop_count += 1
        query = " ".join(search_keywords)
        url = f"{base_url}?query={urllib.parse.quote(query)}&display={DISPLAY_PER_CALL}&start={start}&sort=date"

        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print(f"❌ 요청 에러: {e}")
            break

        if r.status_code != 200:
            print(f"❌ 요청 실패: {r.status_code} {r.text}")
            break

        items = r.json().get("items", [])
        if not items:
            break

        for item in items:
            title_raw = html.unescape(item.get("title", ""))
            title_clean = title_raw.replace("<b>", "").replace("</b>", "")
            link = (item.get("link") or "").strip()

            # 이전 발송 기사 등장 시 즉시 중단
            if link in sent_before:
                stop_search = True
                print("⏹ 이전 뉴스 등장, 검색 중단")
                break

            # 제목에 필터 키워드 포함 시 저장
            if any(k.lower() in title_clean.lower() for k in filter_keywords):
                collected.append((title_clean, link))
                if len(collected) >= NEWS_COUNT:
                    break

        start += DISPLAY_PER_CALL

    return collected

# ─────────────────────────────────────────────
# 텔레그램 전송
# ─────────────────────────────────────────────
def send_to_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM 환경변수가 없습니다.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            print("✅ 텔레그램 전송 완료")
            return True
        else:
            print("❌ 텔레그램 전송 실패:", r.text)
            return False
    except Exception as e:
        print("❌ 텔레그램 전송 예외:", e)
        return False

# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
if __name__ == "__main__":
    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)

    now = datetime.now()
    hour = now.hour
    is_six_hour_cycle = (hour % 6 == 0)

    print(f"🕒 현재 {hour}시 | 테스트 런: {IS_TEST_RUN} | 6시간 주기: {is_six_hour_cycle}")

    sent_before = set() if IS_TEST_RUN else load_sent_log()
    found = search_recent_news(search_keywords, filter_keywords, sent_before)

    # 발송 조건 판단
    if not IS_TEST_RUN and not is_six_hour_cycle and len(found) < MIN_SEND_THRESHOLD:
        print(f"⏸ 기사 {len(found)}개 (<{MIN_SEND_THRESHOLD}), 발송 생략")
        exit(0)

    if not found:
        send_to_telegram("🔎 새 뉴스가 없습니다!")
        exit(0)

    # 메시지 구성
    date_str = now.strftime("%Y.%m.%d(%a) %H시")
    header = f"📢 <b>{date_str} 기준 새 뉴스 {len(found)}개 입니다.</b>\n\n"
    lines = [f"{i+1}. <b>{html.escape(t)}</b>\n{l}\n" for i, (t, l) in enumerate(found)]
    footer = "\n✅ 발송 완료!"
    message = header + "\n".join(lines) + footer

    send_to_telegram(message)

    # 로그 관리
    if not IS_TEST_RUN:
        if is_six_hour_cycle:
            clear_sent_log()
        else:
            for _, link in found:
                sent_before.add(link)
            save_sent_log(sent_before)

    print(f"✅ 전송 완료 ({len(found)}건) | {'테스트' if IS_TEST_RUN else '정상'} 모드")
