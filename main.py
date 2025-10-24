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
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")       # 뉴스 발송 채팅방
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")             # 관리자용 1:1 리포트 채팅방

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"
LOG_FILE = "sent_log.json"

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
NEWS_COUNT = 20
DISPLAY_PER_CALL = 30
MAX_LOOPS = 5
REQUEST_TIMEOUT = 30
MIN_SEND_THRESHOLD = 5
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
    """sent_log.json 파일이 없으면 자동 생성"""
    if not os.path.exists(LOG_FILE):
        print("📄 sent_log.json 없음 → 새로 생성 예정")
        save_sent_log(set())
        return set()
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_sent_log(sent_ids):
    os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
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

    total_fetched = 0
    collected = []
    seen_links = set()
    start = 1

    for loop_count in range(MAX_LOOPS):
        if len(collected) >= NEWS_COUNT:
            break

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
        total_fetched += len(items)
        if not items:
            break

        for item in items:
            title_raw = html.unescape(item.get("title", ""))
            title_clean = title_raw.replace("<b>", "").replace("</b>", "")
            link = (item.get("link") or "").strip()

            # 이전 뉴스 발견 시 즉시 중단
            if link in sent_before:
                print("⏹ 이전 뉴스 등장 → 검색 중단")
                return collected, total_fetched

            if link in seen_links:
                continue
            seen_links.add(link)

            # 제목 필터 조건
            if any(k.lower() in title_clean.lower() for k in filter_keywords):
                collected.append((title_clean, link))
                if len(collected) >= NEWS_COUNT:
                    break

        start += DISPLAY_PER_CALL

    return collected, total_fetched

# ─────────────────────────────────────────────
# 텔레그램 전송
# ─────────────────────────────────────────────
def send_to_telegram(chat_id, message):
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
    before_count = len(sent_before)

    found, total_fetched = search_recent_news(search_keywords, filter_keywords, sent_before)
    after_filter_count = len(found)

    # 발송 조건 판단
    if not IS_TEST_RUN and not is_six_hour_cycle and len(found) < MIN_SEND_THRESHOLD:
        send_to_telegram(ADMIN_CHAT_ID,
            f"📊 [관리자 리포트]\n"
            f"- 전체 호출 결과: {total_fetched}건\n"
            f"- 필터 통과: {after_filter_count}건\n"
            f"- 누적 저장된 링크: {before_count}건\n"
            f"- 결과: 기사 {len(found)}개로 <{MIN_SEND_THRESHOLD} 미만> → 발송 생략"
        )
        print(f"⏸ 기사 {len(found)}개 (<{MIN_SEND_THRESHOLD}), 발송 생략")
        exit(0)

    if not found:
        send_to_telegram(TELEGRAM_CHAT_ID, "🔎 새 뉴스가 없습니다!")
        send_to_telegram(ADMIN_CHAT_ID, f"📊 [관리자 리포트]\n- 새 뉴스 없음\n- 이전 누적: {before_count}건")
        exit(0)

    # 메시지 구성
    header = f"📢 <b>새 뉴스 {len(found)}개</b>\n\n"
    lines = [f"{i+1}. <b>{html.escape(t)}</b>\n{l}\n" for i, (t, l) in enumerate(found)]
    footer = "\n✅ 발송 완료!"
    message = header + "\n".join(lines) + footer

    send_to_telegram(TELEGRAM_CHAT_ID, message)

    # 로그 관리
    if not IS_TEST_RUN:
        if is_six_hour_cycle:
            clear_sent_log()
        else:
            for _, link in found:
                sent_before.add(link)
            save_sent_log(sent_before)

    # 관리자용 리포트 발송 (테스트 런 포함)
    current_total = len(sent_before)
    send_to_telegram(ADMIN_CHAT_ID,
        f"📊 [관리자 리포트]\n"
        f"- 전체 호출 결과: {total_fetched}건\n"
        f"- 제목 필터 통과: {after_filter_count}건\n"
        f"- 이전 누적: {before_count}건 → 현재 누적: {current_total}건\n"
        f"- 발송된 기사: {len(found)}건\n"
        f"- 모드: {'🧪 테스트' if IS_TEST_RUN else '✅ 정상'}"
    )

    print(f"✅ 전송 완료 ({len(found)}건) | {'테스트' if IS_TEST_RUN else '정상'} 모드")
