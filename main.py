import os
import time
import requests
import urllib.parse
from dotenv import load_dotenv
import html
import json
import email.utils
from bs4 import BeautifulSoup

# ───────────────────────────────────────────────────────────
# 환경변수 로드 (.env)
# ───────────────────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Telegraph API: https://api.telegra.ph
# access_token은 최초 1회 createAccount 후 발급/보관
TELEGRAPH_ACCESS_TOKEN = os.getenv("TELEGRAPH_ACCESS_TOKEN")  # ★ 추가

SEARCH_KEYWORDS_FILE = "search_keywords.txt"
FILTER_KEYWORDS_FILE = "filter_keywords.txt"
LOG_FILE = "sent_log.json"

NEWS_COUNT = 10           # 한 번 실행에 최대 전송 수
DISPLAY_PER_CALL = 100
MAX_LOOPS = 10
REQUEST_TIMEOUT = 10
PAUSE_BETWEEN_MSGS = 0.5  # 텔레그램/텔레그래프 호출 간 짧은 휴식

UA = "Mozilla/5.0 (compatible; fcanewsbot/1.0; +https://t.me/)"

# ───────────────────────────────────────────────────────────
# 유틸
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

def parse_pubdate(pubdate_str):
    try:
        return email.utils.parsedate_to_datetime(pubdate_str)
    except Exception:
        return None

# ───────────────────────────────────────────────────────────
# 네이버 뉴스 검색
# ───────────────────────────────────────────────────────────
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
        query = " ".join(search_keywords)  # 네이버는 공백 AND
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

# ───────────────────────────────────────────────────────────
# 기사 본문 추출 (간단한 휴리스틱)
# ───────────────────────────────────────────────────────────
def extract_article(url):
    """
    가능한 범용적으로 제목, 본문 텍스트를 추출.
    - 강건성을 위해 여러 후보를 시도
    - 실패 시 None 반환
    """
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200 or not r.text:
            return None, None
        soup = BeautifulSoup(r.text, "lxml")

        # 제목 후보
        title = None
        for sel in ["meta[property='og:title']", "meta[name='title']"]:
            m = soup.select_one(sel)
            if m and m.get("content"):
                title = m["content"].strip()
                break
        if not title and soup.title and soup.title.text:
            title = soup.title.text.strip()

        # 본문 후보: article, #articleBody, .newsct_article, .article_body 등 흔한 패턴
        candidates = [
            "article",
            "#articleBody",
            ".article_body",
            ".newsct_article",
            ".news_body",
            ".content",
            ".post-content",
            "div[itemprop='articleBody']",
        ]
        body_text = ""
        for css in candidates:
            node = soup.select_one(css)
            if node:
                # 스크립트/스타일 제거
                for bad in node(["script", "style", "noscript"]):
                    bad.extract()
                text = node.get_text("\n").strip()
                # 너무 짧으면 무시
                if len(text) >= 300:
                    body_text = text
                    break

        # 그래도 빈 경우 기사 전체에서 p를 모아 시도
        if not body_text:
            ps = soup.find_all("p")
            text = "\n".join(p.get_text(" ").strip() for p in ps if p.get_text().strip())
            if len(text) >= 300:
                body_text = text

        if not title:
            title = "기사 제목"
        if not body_text:
            return title, None
        return title, body_text
    except Exception as e:
        print(f"❌ 본문 추출 실패: {url} / {e}")
        return None, None

# ───────────────────────────────────────────────────────────
# Telegraph 페이지 생성
# ───────────────────────────────────────────────────────────
def create_telegraph_page(title, body_text, source_url):
    """
    Telegraph에 기사 재게시
    - 본문은 단락으로 쪼개어 <p> 노드 배열로 구성
    - 맨 앞에 원문 링크를 넣어둠(메시지에는 링크 1개만 보낼 것이므로, 원문은 페이지 내부 링크로 제공)
    """
    if not TELEGRAPH_ACCESS_TOKEN:
        raise RuntimeError("TELEGRAPH_ACCESS_TOKEN 이 설정되지 않았습니다.")

    # 본문 노드 구성
    nodes = []
    # 원문 링크 단락
    nodes.append({
        "tag": "p",
        "children": [
            {"tag": "a", "attrs": {"href": source_url}, "children": ["원문 보기"]},
            " · 이 페이지는 fcanews 봇이 자동 변환했습니다."
        ]
    })

    # 본문을 너무 길면 일부만 (예: 6000자) — 텔레그래프가 너무 긴 콘텐츠를 싫어함
    MAX_CHARS = 6000
    body_trim = body_text[:MAX_CHARS]

    # 단락 분리
    for para in [p.strip() for p in body_trim.split("\n") if p.strip()]:
        nodes.append({"tag": "p", "children": [para]})

    payload = {
        "access_token": TELEGRAPH_ACCESS_TOKEN,
        "title": title[:120],                 # 제목 제한
        "author_name": "FCAnyang NewsBot",
        "return_content": False,
        "content": json.dumps(nodes, ensure_ascii=False)
    }
    try:
        r = requests.post("https://api.telegra.ph/createPage", data=payload, timeout=REQUEST_TIMEOUT)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegraph 오류: {data}")
        return data["result"]["url"]
    except Exception as e:
        raise RuntimeError(f"Telegraph 업로드 실패: {e}")

# ───────────────────────────────────────────────────────────
# 텔레그램 전송 (메시지당 링크 1개 유지)
# ───────────────────────────────────────────────────────────
def send_to_telegram_single(title, url, is_telegraph=True):
    """
    메시지 한 건 전송.
    - 즉시 보기 유도: 메시지에 URL 1개만 포함
    - telegraph URL인 경우 미리보기 켬 (disable_web_page_preview=False)
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM 환경변수가 설정되지 않았습니다.")
        return False

    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if is_telegraph:
        # 제목 + 텔레그래프 링크 1개만
        text = f"📰 <b>{html.escape(title)}</b>\n{url}"
        disable_preview = False  # 즉시 보기/미리보기 활성화
    else:
        # 폴백: 원문 링크 1개만
        text = f"📰 <b>{html.escape(title)}</b>\n{url}"
        disable_preview = False  # 그래도 카드 미리보기라도 보이게

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview
    }
    try:
        r = requests.post(send_url, data=payload, timeout=REQUEST_TIMEOUT)
        ok = r.status_code == 200
        if not ok:
            print("❌ 텔레그램 전송 실패:", r.text)
        return ok
    except Exception as e:
        print("❌ 텔레그램 전송 예외:", e)
        return False

# ───────────────────────────────────────────────────────────
# 메인
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    search_keywords = load_keywords(SEARCH_KEYWORDS_FILE)
    filter_keywords = load_keywords(FILTER_KEYWORDS_FILE)

    if not CLIENT_ID or not CLIENT_SECRET:
        print("⚠️ NAVER API 환경변수가 없습니다.")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ TELEGRAM 환경변수가 없습니다.")
    if not TELEGRAPH_ACCESS_TOKEN:
        print("⚠️ TELEGRAPH_ACCESS_TOKEN 이 없습니다. (즉시 보기 변환이 비활성화됩니다)")

    if not search_keywords:
        print("⚠️ 검색 키워드가 없습니다.")
        exit()
    if not filter_keywords:
        print("⚠️ 필터링 키워드가 없습니다.")
        exit()

    sent_before = load_sent_log()
    found = search_recent_news(search_keywords, filter_keywords, sent_before)

    # 새 항목만
    new_items = [(t, l) for (t, l) in found if l not in sent_before]

    if not new_items:
        # 종합 메시지(링크 없음) — 즉시 보기 고려 없이 안내만
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": "🔎 새 뉴스가 없습니다!",
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                },
                timeout=REQUEST_TIMEOUT
            )
        print("🔎 새 뉴스가 없습니다!")
        exit(0)

    # 각 기사 → Telegraph 업로드 → 텔레그램 전송 (메시지당 링크 1개)
    sent_count = 0
    for title, link in new_items:
        telegraph_url = None
        used_telegraph = False

        if TELEGRAPH_ACCESS_TOKEN:
            # 1) 본문 추출
            art_title, art_body = extract_article(link)
            if art_title and art_body:
                # 2) Telegraph 생성
                try:
                    telegraph_url = create_telegraph_page(art_title or title, art_body, link)
                    used_telegraph = True
                except Exception as e:
                    print(f"⚠️ Telegraph 실패, 원문 링크로 전송합니다: {e}")

        # 3) 텔레그램 전송 (메시지에 URL 1개만)
        if used_telegraph and telegraph_url:
            ok = send_to_telegram_single(title, telegraph_url, is_telegraph=True)
        else:
            ok = send_to_telegram_single(title, link, is_telegraph=False)

        if ok:
            sent_before.add(link)
            sent_count += 1
            time.sleep(PAUSE_BETWEEN_MSGS)  # 속도 완화

    # 로그 저장
    save_sent_log(sent_before)
    print(f"✅ 전송 완료: {sent_count}건")
