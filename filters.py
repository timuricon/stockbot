from typing import Optional
import pandas as pd

from core.config import MIN_PRICE, EXCLUDED_TICKERS
from core.logging_setup import logger
from data_provider.yfinance_client import get_fast_info, YF_AVAILABLE


def passes_exclusion(ticker: str) -> bool:
    """Тикер не в списке исключений."""
    return ticker not in EXCLUDED_TICKERS


def passes_reference(ticker: str, reference: dict) -> bool:
    """Тикер есть в справочнике NYSE/NASDAQ."""
    return ticker in reference


def passes_min_price(price: float) -> bool:
    return price >= MIN_PRICE


def passes_ema_filter(price: float, ema_val: float) -> bool:
    """Цена выше 30W EMA."""
    return price > ema_val


def passes_ema_zone_d(ema_pct: float) -> bool:
    """Исключает зону Г (>35% над EMA)."""
    return ema_pct <= 35.0


def passes_upside(upside_pct: float, min_upside: float) -> bool:
    return upside_pct >= (min_upside - 0.05)


def passes_volume_not_drying(bars: pd.DataFrame) -> bool:
    """Объём последней недели >= 55% от 20W нормы."""
    if len(bars) < 21:
        return True  # не можем проверить — пропускаем
    try:
        last_vol  = float(bars["Volume"].iloc[-1])
        avg_20w   = float(bars["Volume"].iloc[-21:-1].mean())
        if avg_20w > 0 and last_vol < avg_20w * 0.55:
            return False
        return True
    except Exception:
        return True


def prefilter_superstock(ticker: str, ticker_info: dict) -> Optional[str]:
    """
    Быстрая проверка через yfinance fast_info ДО медленных Polygon запросов.
    Возвращает причину отсева или None если тикер прошёл.
    """
    if not YF_AVAILABLE:
        return None
    try:
        fi = get_fast_info(ticker)
        if fi is None:
            return None

        avg_vol = getattr(fi, "three_month_average_volume", None)
        if avg_vol is not None and avg_vol < 100_000:
            return f"Avg volume {avg_vol:.0f} < 100K"

        shares = getattr(fi, "shares", None)
        if shares is not None and shares > 100_000_000:
            return f"Shares {shares/1e6:.0f}M > 100M"

        market_cap = getattr(fi, "market_cap", None)
        if market_cap is not None and market_cap < 50_000_000:
            return f"Market cap ${market_cap/1e6:.0f}M < $50M"

        price = getattr(fi, "last_price", None) or ticker_info.get("price", 0)
        if price and price < MIN_PRICE:
            return f"Price ${price:.2f} < ${MIN_PRICE}"

        return None
    except Exception as e:
        logger.debug(f"prefilter_superstock {ticker}: {e}")
        return None
