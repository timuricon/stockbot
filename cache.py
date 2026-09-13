import json
import os
import time
from datetime import datetime
from typing import Optional

from core.config import (
    TICKER_CACHE_FILE, TICKERS_CACHE_HOURS,
    ALLOWED_EXCHANGES, ALLOWED_TYPES,
)
from core.logging_setup import logger
from data_provider.polygon_client import get_reference_tickers_page


# ── Ticker reference cache ────────────────────────────────────────────────────

def load_ticker_reference() -> dict:
    """
    Загружает справочник тикеров из локального кэша.
    Если кэш устарел — обновляет через Polygon.
    """
    if os.path.exists(TICKER_CACHE_FILE):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(TICKER_CACHE_FILE))
            age_hours = (datetime.now() - mtime).total_seconds() / 3600
            if age_hours < TICKERS_CACHE_HOURS:
                with open(TICKER_CACHE_FILE, "r") as f:
                    data = json.load(f)
                logger.info(
                    f"Loaded ticker reference from cache "
                    f"({len(data)} tickers, {age_hours:.1f}h old)"
                )
                return data
            else:
                logger.info(f"Ticker cache is {age_hours:.1f}h old — refreshing...")
        except Exception as e:
            logger.warning(f"Cache read error: {e}")

    return refresh_ticker_reference()


def refresh_ticker_reference() -> dict:
    """Получает полный список NYSE+NASDAQ тикеров из Polygon и сохраняет в кэш."""
    import urllib.parse as up

    logger.info("Fetching fresh ticker reference from Polygon.io...")
    reference = {}
    cursor = None
    page = 0

    LLP_KW = ["LIMITED PARTNERSHIP", "LP ", "L.P.", "PARTNERS LP"]

    while True:
        data = get_reference_tickers_page(cursor)
        if not data or "results" not in data:
            logger.warning(f"Reference fetch stopped at page {page}")
            break

        for t in data["results"]:
            ticker   = t.get("ticker", "")
            exchange = t.get("primary_exchange", "")
            ttype    = t.get("type", "")
            name     = t.get("name", "").upper()
            sic      = t.get("sic_description", "")

            if not ticker or "." in ticker or "/" in ticker:
                continue
            if exchange not in ALLOWED_EXCHANGES:
                continue
            if ttype not in ALLOWED_TYPES:
                continue
            if any(kw in name for kw in LLP_KW):
                continue

            reference[ticker] = {
                "exchange": exchange,
                "type":     ttype,
                "name":     t.get("name", ""),
                "sic":      sic,
            }

        page += 1
        logger.info(f"  Page {page}: {len(reference)} tickers so far")
        time.sleep(0.5)

        next_url = data.get("next_url", "")
        if not next_url:
            break
        parsed = up.urlparse(next_url)
        qs = up.parse_qs(parsed.query)
        cursor = qs.get("cursor", [None])[0]
        if not cursor:
            break
        time.sleep(0.5)

    logger.info(f"Ticker reference built: {len(reference)} valid tickers")

    try:
        with open(TICKER_CACHE_FILE, "w") as f:
            json.dump(reference, f)
        logger.info(f"Ticker reference saved to {TICKER_CACHE_FILE}")
    except Exception as e:
        logger.warning(f"Cache write error: {e}")

    return reference
