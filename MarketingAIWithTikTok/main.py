import requests
import os
from apify_client import ApifyClient
from openai import OpenAI

# 1. 설정
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")

def require_env(name, value):
    if not value:
        raise RuntimeError(f"{name} 환경변수가 설정되어 있지 않습니다.")
    return value

def get_tiktok_data():
    client = ApifyClient(require_env("APIFY_TOKEN", APIFY_TOKEN))
    run_input = {
        "searchQueries": ["skincare"],
        "resultsPerPage": 5,
        "searchSection": "/video",
        "videoSearchSorting": "MOST_LIKED",
    }
    run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
    
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    return items

def send_telegram(message):
    telegram_token = require_env("TELEGRAM_TOKEN", TELEGRAM_TOKEN)
    chat_id = require_env("TELEGRAM_CHAT_ID", CHAT_ID)
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    requests.post(url, data=payload)

def analyze_tiktok_data(items):
    if not os.getenv("OPENAI_API_KEY"):
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

    insights = []
    for line in response.output_text.splitlines():
        line = line.strip()
        if line:
            insights.append(line.split(".", 1)[-1].strip())

    return (insights + ["인사이트 없음"] * len(items))[:len(items)]

# 2. 메인 로직
data = get_tiktok_data()
insights = analyze_tiktok_data(data)
message = "오늘의 스킨케어 트렌드 Top 5:\n\n"
for i, item in enumerate(data, 1):
    title = item.get("title") or item.get("text") or item.get("desc") or "제목 없음"
    url = item.get("webVideoUrl") or item.get("url") or item.get("videoUrl") or "URL 없음"
    insight = insights[i - 1]
    message += f"{i}. {title}\n인사이트: {insight}\n{url}\n\n"

send_telegram(message)
