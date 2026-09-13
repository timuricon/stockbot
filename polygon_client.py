import time
import requests
from typing import Optional

from core.config import BASE_URL, POLYGON_API_KEY
from core.logging_setup import logger


def polygon_get(
    endpoint: str,
    params: dict = None,
    retries: int = 3,
    cancel_event=None,
) -> Optional[dict]:
    """
    Generic GET с retry.
    429 → фиксированная пауза 15 сек (не экспонента).
    Другие ошибки → не повторяем, возвращаем None сразу.
    """
    url = BASE_URL + endpoint
    p = {"apiKey": POLYGON_API_KEY}
    if params:
        p.update(params)

    for attempt in range(retries):
        try:
            r = requests.get(url, params=p, timeout=30)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                # Фиксированная пауза — не экспонента
                wait = 15
                logger.warning(f"Rate limited (attempt {attempt+1}/{retries}). Waiting {wait}s...")
                if cancel_event and cancel_event.wait(timeout=wait):
                    return None
                else:
                    time.sleep(wait)
            elif r.status_code == 404:
                return None
            else:
                logger.warning(f"HTTP {r.status_code} for {endpoint}: {r.text[:100]}")
                return None
        except requests.RequestException as e:
            logger.error(f"Request error ({endpoint}): {e}")
            if attempt < retries - 1:
                time.sleep(5)
    return None


def get_grouped_daily(days_back_range: int = 6, cancel_event=None) -> Optional[list]:
    """
    Получает grouped daily bars для всего рынка.
    Перебирает последние days_back_range дней, возвращает первый успешный результат.
    """
    from datetime import datetime, timedelta
    for days_back in range(1, days_back_range):
        date_str = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        data = polygon_get(
            f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}",
            params={"adjusted": "true", "include_otc": "false"},
            cancel_event=cancel_event,
        )
        if data and "results" in data and len(data["results"]) > 100:
            logger.info(f"Got {len(data['results'])} tickers for {date_str}")
            return data["results"]
    return None


def get_ticker_daily_bars(
    ticker: str,
    start_date: str,
    end_date: str,
    limit: int = 1200,
    cancel_event=None,
    retries: int = 2,
) -> Optional[dict]:
    """Дневные бары одного тикера. Retry внутри polygon_get."""
    return polygon_get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}",
        params={"adjusted": "true", "sort": "asc", "limit": limit},
        cancel_event=cancel_event,
        retries=retries,
    )


def get_ticker_weekly_bars(
    ticker: str,
    start_date: str,
    end_date: str,
    limit: int = 200,
    cancel_event=None,
) -> Optional[dict]:
    """Недельные бары одного тикера."""
    return polygon_get(
        f"/v2/aggs/ticker/{ticker}/range/1/week/{start_date}/{end_date}",
        params={"adjusted": "true", "sort": "asc", "limit": limit},
        cancel_event=cancel_event,
    )


def get_reference_tickers_page(cursor: str = None) -> Optional[dict]:
    """Одна страница справочника тикеров."""
    params = {
        "market": "stocks",
        "active":  "true",
        "limit":   1000,
        "sort":    "ticker",
        "order":   "asc",
    }
    if cursor:
        params["cursor"] = cursor
    return polygon_get("/v3/reference/tickers", params)


def get_last_two_daily_bars(ticker: str, start_date: str, end_date: str) -> Optional[dict]:
    """Последние 2 дневных бара для market overview."""
    return polygon_get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}",
        params={"adjusted": "true", "sort": "asc", "limit": 5},
    )
