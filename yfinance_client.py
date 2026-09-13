from typing import Optional

from core.logging_setup import logger

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False


def get_current_price(ticker: str) -> Optional[float]:
    """Текущая цена через yfinance (15-мин задержка)."""
    if not YF_AVAILABLE:
        return None
    try:
        price = yf.Ticker(ticker).fast_info.last_price
        if price and price > 0:
            return float(price)
    except Exception as e:
        logger.debug(f"yfinance price failed for {ticker}: {e}")
    return None


def get_analyst_consensus(ticker: str) -> Optional[float]:
    """Медианный консенсус-таргет аналитиков."""
    if not YF_AVAILABLE:
        return None
    try:
        info = yf.Ticker(ticker).analyst_price_targets
        if info is not None and hasattr(info, "median"):
            median = float(info.median)
            if median > 0:
                return median
        raw = yf.Ticker(ticker).info
        median = raw.get("targetMedianPrice") or raw.get("targetMeanPrice")
        if median and float(median) > 0:
            return float(median)
    except Exception as e:
        logger.debug(f"analyst consensus failed for {ticker}: {e}")
    return None


def get_fast_info(ticker: str) -> Optional[object]:
    """fast_info объект для pre-filter."""
    if not YF_AVAILABLE:
        return None
    try:
        return yf.Ticker(ticker).fast_info
    except Exception as e:
        logger.debug(f"fast_info failed for {ticker}: {e}")
    return None


def get_full_info(ticker: str) -> Optional[dict]:
    """Полный info dict."""
    if not YF_AVAILABLE:
        return None
    try:
        return yf.Ticker(ticker).info or {}
    except Exception as e:
        logger.debug(f"full_info failed for {ticker}: {e}")
    return None


def get_quarterly_financials(ticker: str):
    """Квартальная финансовая отчётность."""
    if not YF_AVAILABLE:
        return None
    try:
        return yf.Ticker(ticker).quarterly_financials
    except Exception as e:
        logger.debug(f"quarterly_financials failed for {ticker}: {e}")
    return None


def get_quarterly_income_stmt(ticker: str):
    """Квартальный income statement."""
    if not YF_AVAILABLE:
        return None
    try:
        return yf.Ticker(ticker).quarterly_income_stmt
    except Exception as e:
        logger.debug(f"quarterly_income_stmt failed for {ticker}: {e}")
    return None


def get_dividend_yield(ticker: str) -> Optional[float]:
    """
    Текущий dividend yield в процентах (например, 9.5).
    Для CEF предпочитает расчёт через dividendRate / currentPrice.
    """
    if not YF_AVAILABLE:
        return None
    try:
        info = yf.Ticker(ticker).info
        div_rate = info.get("dividendRate") or 0
        price    = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        if div_rate and price and price > 0:
            calc_yield = round((div_rate / price) * 100, 2)
            if 0.1 < calc_yield < 50:
                return calc_yield

        dy = info.get("dividendYield") or info.get("yield")
        if dy and dy > 0:
            result = round(float(dy) * 100, 2)
            if 0.1 < result < 50:
                return result
            elif 0.1 < float(dy) < 50:
                return round(float(dy), 2)
    except Exception as e:
        logger.debug(f"yfinance yield failed for {ticker}: {e}")
    return None
