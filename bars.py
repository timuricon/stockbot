from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from core.config import GROWTH_WEEKLY_BARS
from core.logging_setup import logger
from data_provider.bars_cache import (
    is_cache_fresh, get_cached_bars, get_last_cached_date,
    merge_and_save, _save_cache,
)
from data_provider.polygon_client import (
    get_ticker_daily_bars,
    get_ticker_weekly_bars,
)

# Минимум баров для early_trend (требует 200 дневных)
DAILY_BARS_FULL = 252   # ~1 год торговых дней


def _raw_to_df(raw_bars: list) -> pd.DataFrame:
    """Конвертирует raw Polygon bars в DataFrame с нужными колонками."""
    df = pd.DataFrame(raw_bars)
    df.rename(columns={"o": "Open", "h": "High", "l": "Low",
                        "c": "Close", "v": "Volume", "t": "Timestamp"}, inplace=True)
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def get_weekly_bars(ticker: str, cancel_event=None) -> Optional[pd.DataFrame]:
    """
    Недельные бары для Swing модуля.
    Строим из дневных adjusted баров Polygon.
    Использует дисковый кэш.
    """
    interval = "1d_weekly"

    if is_cache_fresh(ticker, interval):
        cached = get_cached_bars(ticker, interval)
        if cached and len(cached) >= 100:
            logger.debug(f"[BarsCache] Weekly hit: {ticker}")
            return _build_weekly_df(cached)

    end_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    last_date = get_last_cached_date(ticker, interval)
    if last_date:
        start_date = (
            datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        logger.debug(f"[BarsCache] Weekly partial: {ticker} from {start_date}")
    else:
        start_date = (datetime.now() - timedelta(weeks=165)).strftime("%Y-%m-%d")
        logger.debug(f"[BarsCache] Weekly full load: {ticker}")

    if start_date > end_date:
        logger.debug(f"[BarsCache] Weekly skip {ticker}: cache up to date")
        cached = get_cached_bars(ticker, interval)
        if cached and len(cached) >= 100:
            return _build_weekly_df(cached)
        return None

    data = get_ticker_daily_bars(
        ticker, start_date, end_date,
        limit=1200,
        cancel_event=cancel_event,
        retries=2,
    )

    if not data or "results" not in data:
        cached = get_cached_bars(ticker, interval)
        if cached and len(cached) >= 100:
            return _build_weekly_df(cached)
        return None

    new_bars = data["results"]
    old_bars = get_cached_bars(ticker, interval) or []
    if old_bars:
        all_bars = merge_and_save(ticker, interval, old_bars, new_bars)
    else:
        _save_cache(ticker, interval, new_bars)
        all_bars = new_bars

    if len(all_bars) < 100:
        return None

    return _build_weekly_df(all_bars)


def _build_weekly_df(raw_bars: list) -> Optional[pd.DataFrame]:
    """Агрегирует дневные бары в недельные."""
    try:
        df = pd.DataFrame(raw_bars)
        df.rename(columns={"o": "Open", "h": "High", "l": "Low",
                            "c": "Close", "v": "Volume", "t": "Timestamp"}, inplace=True)
        df["Date"] = pd.to_datetime(df["Timestamp"], unit="ms")
        df.set_index("Date", inplace=True)
        df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)

        weekly = df.resample("W-MON", label="left", closed="left").agg({
            "Open":   "first",
            "High":   "max",
            "Low":    "min",
            "Close":  "last",
            "Volume": "sum",
        }).dropna()

        now = pd.Timestamp.now(tz="UTC").tz_localize(None)
        current_week_start = now - pd.tseries.offsets.Week(weekday=0)
        weekly = weekly[weekly.index < current_week_start.normalize()]

        if len(weekly) < 20:
            return None

        return weekly[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logger.debug(f"_build_weekly_df error: {e}")
        return None


def get_daily_bars_df(ticker: str, cancel_event=None) -> Optional[pd.DataFrame]:
    """
    Дневные бары для Early Trend (200+ баров) и Momentum (60 баров).
    Использует кэш — при повторном запросе не идёт в Polygon.
    """
    interval = "1d"

    if is_cache_fresh(ticker, interval):
        cached = get_cached_bars(ticker, interval)
        if cached and len(cached) >= 20:
            logger.debug(f"[BarsCache] Daily hit: {ticker}")
            return _raw_to_df(cached).tail(DAILY_BARS_FULL)

    end = datetime.now().strftime("%Y-%m-%d")
    last_date = get_last_cached_date(ticker, interval)
    if last_date:
        start = (
            datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        logger.debug(f"[BarsCache] Daily partial: {ticker} from {start}")
    else:
        start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        logger.debug(f"[BarsCache] Daily full load: {ticker}")

    if start > end:
        logger.debug(f"[BarsCache] Daily skip {ticker}: cache up to date")
        cached = get_cached_bars(ticker, interval)
        if cached and len(cached) >= 20:
            return _raw_to_df(cached).tail(DAILY_BARS_FULL)
        return None

    try:
        data = get_ticker_daily_bars(
            ticker, start, end, limit=DAILY_BARS_FULL, cancel_event=cancel_event
        )
        if not data or "results" not in data:
            cached = get_cached_bars(ticker, interval)
            if cached and len(cached) >= 20:
                return _raw_to_df(cached).tail(DAILY_BARS_FULL)
            return None

        new_bars = data["results"]
        old_bars = get_cached_bars(ticker, interval) or []
        if old_bars:
            all_bars = merge_and_save(ticker, interval, old_bars, new_bars)
        else:
            _save_cache(ticker, interval, new_bars)
            all_bars = new_bars

        if len(all_bars) < 20:
            return None

        return _raw_to_df(all_bars).tail(DAILY_BARS_FULL)

    except Exception as e:
        logger.debug(f"get_daily_bars_df {ticker}: {e}")
        cached = get_cached_bars(ticker, interval)
        if cached and len(cached) >= 20:
            return _raw_to_df(cached).tail(DAILY_BARS_FULL)
        return None


def get_growth_weekly_bars_df(ticker: str, cancel_event=None) -> Optional[pd.DataFrame]:
    """Недельные бары для Momentum модуля unified скана (бывш. growth скан)."""
    interval = "1w"

    if is_cache_fresh(ticker, interval):
        cached = get_cached_bars(ticker, interval)
        if cached and len(cached) >= 20:
            logger.debug(f"[BarsCache] Weekly(growth) hit: {ticker}")
            return _raw_to_df(cached).tail(GROWTH_WEEKLY_BARS)

    try:
        end = datetime.now().strftime("%Y-%m-%d")
        last_date = get_last_cached_date(ticker, interval)
        if last_date:
            start = (
                datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
            ).strftime("%Y-%m-%d")
        else:
            start = (datetime.now() - timedelta(weeks=GROWTH_WEEKLY_BARS + 4)).strftime("%Y-%m-%d")

        if start > end:
            logger.debug(f"[BarsCache] Growth weekly skip {ticker}: cache up to date")
            cached = get_cached_bars(ticker, interval)
            if cached and len(cached) >= 20:
                return _raw_to_df(cached).tail(GROWTH_WEEKLY_BARS)
            return None

        data = get_ticker_weekly_bars(
            ticker, start, end, limit=GROWTH_WEEKLY_BARS, cancel_event=cancel_event
        )
        if not data or "results" not in data:
            cached = get_cached_bars(ticker, interval)
            if cached and len(cached) >= 20:
                return _raw_to_df(cached).tail(GROWTH_WEEKLY_BARS)
            return None

        new_bars = data["results"]
        old_bars = get_cached_bars(ticker, interval) or []
        if old_bars:
            all_bars = merge_and_save(ticker, interval, old_bars, new_bars)
        else:
            _save_cache(ticker, interval, new_bars)
            all_bars = new_bars

        if len(all_bars) < 20:
            return None

        return _raw_to_df(all_bars).tail(GROWTH_WEEKLY_BARS)

    except Exception as e:
        logger.debug(f"get_growth_weekly_bars_df {ticker}: {e}")
        return None
