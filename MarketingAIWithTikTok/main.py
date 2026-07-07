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
REPORT_ITEM_COUNT = int(os.getenv("REPORT_ITEM_COUNT", "5"))
RECENT_UPLOAD_CANDIDATE_COUNT = int(os.getenv("RECENT_UPLOAD_CANDIDATE_COUNT", "30"))
KST = ZoneInfo("Asia/Seoul")

def require_env(name, value):
    if not value:
        raise RuntimeError(f"{name} 환경변수가 설정되어 있지 않습니다.")
    return value

def get_report_window():
    now_kst = datetime.now(KST)
    end_time = now_kst.replace(second=0, microsecond=0)
    start_time = end_time - timedelta(days=1)
    return start_time, end_time

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

def get_value(item, *keys):
    for key in keys:
        value = item
        for part in key.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value not in (None, ""):
            return value
    return None

def parse_datetime(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo("UTC"))
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return parse_datetime(int(text))
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None

def get_created_at(item):
    value = get_value(
        item,
        "createTimeISO",
        "createTime",
        "create_time",
        "timestamp",
        "publishedAt",
        "video.createTime",
    )
    created_at = parse_datetime(value)
    if created_at and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    return created_at

def sort_by_views(items):
    return sorted(
        items,
        key=lambda item: get_number(item, "playCount", "stats.playCount", "statsV2.playCount"),
        reverse=True,
    )

def get_tiktok_data(
    sorting="LATEST",
    candidate_count=None,
):
    client = ApifyClient(require_env("APIFY_TOKEN", APIFY_TOKEN))
    run_input = {
        "searchQueries": [SEARCH_QUERY],
        "resultsPerPage": candidate_count or RECENT_UPLOAD_CANDIDATE_COUNT,
        "searchSection": "/video",
        "videoSearchSorting": sorting,
    }

    run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
    return list(client.dataset(get_default_dataset_id(run)).iterate_items())

def get_default_dataset_id(run):
    if isinstance(run, dict):
        return run["defaultDatasetId"]

    for attr in ("default_dataset_id", "defaultDatasetId"):
        value = getattr(run, attr, None)
        if value:
            return value

    run_data = getattr(run, "data", None)
    if isinstance(run_data, dict) and run_data.get("defaultDatasetId"):
        return run_data["defaultDatasetId"]

    raise RuntimeError(f"Apify run 결과에서 defaultDatasetId를 찾을 수 없습니다: {type(run).__name__}")

def get_recent_upload_top_viewed(start_time, end_time):
    items = get_tiktok_data(
        sorting="LATEST",
        candidate_count=RECENT_UPLOAD_CANDIDATE_COUNT,
    )
    filtered_items = []
    for item in items:
        created_at = get_created_at(item)
        if not created_at:
            continue
        created_at_kst = created_at.astimezone(KST)
        if start_time <= created_at_kst <= end_time:
            filtered_items.append(item)
    return sort_by_views(filtered_items)[:REPORT_ITEM_COUNT]

def send_telegram(message):
    telegram_token = require_env("TELEGRAM_TOKEN", TELEGRAM_TOKEN)
    chat_id = require_env("TELEGRAM_CHAT_ID", CHAT_ID)
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    for chunk in split_message(message):
        payload = {"chat_id": chat_id, "text": chunk}
        response = requests.post(url, data=payload, timeout=30)
        response.raise_for_status()

def split_message(message, limit=3900):
    chunks = []
    current = ""
    for block in message.split("\n\n"):
        next_block = f"{block}\n\n"
        if len(current) + len(next_block) > limit:
            if current:
                chunks.append(current.strip())
            current = next_block
        else:
            current += next_block
    if current:
        chunks.append(current.strip())
    return chunks

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

def format_items(items):
    if not items:
        return "조건에 맞는 영상이 없습니다.\n\n"

    insights = analyze_tiktok_data(items)
    text = ""
    for i, item in enumerate(items, 1):
        title = item.get("title") or item.get("text") or item.get("desc") or "제목 없음"
        url = item.get("webVideoUrl") or item.get("url") or item.get("videoUrl") or "URL 없음"
        insight = insights[i - 1]
        play_count = get_number(item, "playCount", "stats.playCount", "statsV2.playCount")
        created_at = get_created_at(item)
        created_at_text = ""
        if created_at:
            created_at_text = f"\n업로드: {created_at.astimezone(KST).strftime('%Y-%m-%d %H:%M')} KST"
        text += f"{i}. {title}\n조회수: {play_count:,}{created_at_text}\n인사이트: {insight}\n{url}\n\n"
    return text

def build_message():
    window_start, window_end = get_report_window()
    rising_data = get_recent_upload_top_viewed(window_start, window_end)

    message = (
        "최근 24시간 스킨케어 급상승 영상 Top 5\n"
        f"실행 기준: {window_end.strftime('%Y-%m-%d %H:%M')} KST\n"
        f"기간: {window_start.strftime('%Y-%m-%d %H:%M')} ~ {window_end.strftime('%Y-%m-%d %H:%M')} KST\n\n"
        f"검색어/해시태그: {SEARCH_QUERY}\n"
        f"후보 수: 최근 업로드 {RECENT_UPLOAD_CANDIDATE_COUNT}개\n\n"
        "※ Apify 날짜 필터는 사용하지 않고, LATEST 후보를 코드에서 최근 24시간으로 재검증합니다.\n\n"
    )
    message += format_items(rising_data)
    return message

# 2. 메인 로직
message = build_message()
send_telegram(message)
