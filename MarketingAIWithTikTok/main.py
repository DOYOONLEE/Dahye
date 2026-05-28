import requests
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apify_client import ApifyClient
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# 1. 설정
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")
SEARCH_QUERY = os.getenv("SEARCH_QUERY", "skincare")
SCRAPE_CANDIDATE_COUNT = int(os.getenv("SCRAPE_CANDIDATE_COUNT", "50"))
REPORT_ITEM_COUNT = int(os.getenv("REPORT_ITEM_COUNT", "5"))
KST = ZoneInfo("Asia/Seoul")

def require_env(name, value):
    if not value:
        raise RuntimeError(f"{name} 환경변수가 설정되어 있지 않습니다.")
    return value

def get_report_base_time():
    now_kst = datetime.now(KST)
    target_date = now_kst.date() - timedelta(days=1)
    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        23,
        59,
        tzinfo=KST,
    )

def get_number(item, *keys):
    for key in keys:
        value = item
        for part in key.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0

def get_tiktok_data(base_time):
    client = ApifyClient(require_env("APIFY_TOKEN", APIFY_TOKEN))
    run_input = {
        "searchQueries": [SEARCH_QUERY],
        "resultsPerPage": SCRAPE_CANDIDATE_COUNT,
        "searchSection": "/video",
        "videoSearchSorting": "MOST_LIKED",
        "newestPostDate": base_time.isoformat(),
    }
    run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
    
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    items.sort(
        key=lambda item: get_number(item, "playCount", "stats.playCount", "statsV2.playCount"),
        reverse=True,
    )
    return items[:REPORT_ITEM_COUNT]

def send_telegram(message):
    telegram_token = require_env("TELEGRAM_TOKEN", TELEGRAM_TOKEN)
    chat_id = require_env("TELEGRAM_CHAT_ID", CHAT_ID)
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    requests.post(url, data=payload)

def analyze_tiktok_data(items):
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key or openai_api_key.startswith("your_"):
        return [
            "AI 인사이트를 생성하려면 OPENAI_API_KEY 환경변수를 설정하세요."
            for _ in items
        ]

    summaries = []
    for i, item in enumerate(items, 1):
        title = item.get("title") or item.get("text") or item.get("desc") or "제목 없음"
        author = item.get("authorMeta", {}).get("name") or item.get("author", {}).get("uniqueId") or "알 수 없음"
        play_count = item.get("playCount") or item.get("stats", {}).get("playCount") or "알 수 없음"
        like_count = item.get("diggCount") or item.get("stats", {}).get("diggCount") or "알 수 없음"
        summaries.append(
            f"{i}. 제목: {title}\n"
            f"작성자: {author}\n"
            f"조회수: {play_count}\n"
            f"좋아요: {like_count}"
        )

    client = OpenAI()
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=(
                "당신은 TikTok 뷰티/스킨케어 콘텐츠 트렌드를 분석하는 마케팅 전략가입니다. "
                "각 영상에 대해 한국어 한 줄 인사이트를 작성하세요. "
                "출력은 반드시 1부터 시작하는 번호 목록 5줄만 작성하고, 각 줄은 35자 이내로 간결하게 쓰세요."
            ),
            input="\n\n".join(summaries),
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
        )
    except Exception as error:
        return [f"AI 인사이트 생성 실패: {error.__class__.__name__}" for _ in items]

    insights = []
    for line in response.output_text.splitlines():
        line = line.strip()
        if line:
            insights.append(line.split(".", 1)[-1].strip())

    return (insights + ["인사이트 없음"] * len(items))[:len(items)]

# 2. 메인 로직
base_time = get_report_base_time()
data = get_tiktok_data(base_time)
insights = analyze_tiktok_data(data)
message = (
    "오늘의 스킨케어 트렌드 Top 5\n"
    f"기준: {base_time.strftime('%Y-%m-%d %H:%M')} KST\n"
    f"검색어: {SEARCH_QUERY}\n\n"
)
for i, item in enumerate(data, 1):
    title = item.get("title") or item.get("text") or item.get("desc") or "제목 없음"
    url = item.get("webVideoUrl") or item.get("url") or item.get("videoUrl") or "URL 없음"
    insight = insights[i - 1]
    play_count = get_number(item, "playCount", "stats.playCount", "statsV2.playCount")
    message += f"{i}. {title}\n조회수: {play_count:,}\n인사이트: {insight}\n{url}\n\n"

send_telegram(message)
