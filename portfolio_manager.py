from typing import Optional

from core.config import TICKER_ALTERNATIVES
from core.logging_setup import logger
from data_provider.yfinance_client import get_dividend_yield, get_full_info, YF_AVAILABLE
from portfolio.portfolio_io import (
    load_portfolio, save_portfolio, portfolio_exists,
    replace_holding, approve_holding, set_portfolio_approved,
)


def build_approval_screen_data(holdings: list) -> tuple[bool, int]:
    """
    Возвращает (all_approved, pending_count).
    Логика без Telegram — только данные.
    """
    all_approved = all(h.get("approved", False) for h in holdings)
    pending      = sum(1 for h in holdings if not h.get("approved", False))
    return all_approved, pending


def get_alternatives_for_type(htype: str, current_tickers: set) -> list[dict]:
    """Возвращает список альтернатив для типа инструмента (исключая уже имеющиеся)."""
    alts = TICKER_ALTERNATIVES.get(htype, [])
    return [a for a in alts if a["ticker"] not in current_tickers][:3]


def verify_ticker_yield(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """
    Проверяет тикер через yfinance.
    Возвращает (yield_pct, name) или (None, None) если не найден.
    """
    if not YF_AVAILABLE:
        return None, None
    try:
        info      = get_full_info(ticker) or {}
        live_yield = get_dividend_yield(ticker)
        name       = info.get("longName") or info.get("shortName") or ticker
        return live_yield, name
    except Exception as e:
        logger.debug(f"verify_ticker_yield {ticker}: {e}")
        return None, None


def do_replace_ticker(
    portfolio_data: dict,
    old_ticker: str,
    new_ticker: str,
    new_name: str,
    new_yield: float,
) -> dict:
    """
    Выполняет замену тикера и сохраняет файл.
    Возвращает обновлённый portfolio_data.
    """
    htype = next(
        (h["type"] for h in portfolio_data.get("holdings", []) if h["ticker"] == old_ticker),
        "",
    )
    updated = replace_holding(portfolio_data, old_ticker, new_ticker, new_name, new_yield, htype)
    save_portfolio(updated)
    return updated


def do_approve_ticker(portfolio_data: dict, ticker: str) -> dict:
    """Переключает approved и сохраняет."""
    updated = approve_holding(portfolio_data, ticker)
    save_portfolio(updated)
    return updated


def do_finalize_approval(portfolio_data: dict) -> dict:
    """Устанавливает approved=True на уровне портфеля и сохраняет."""
    updated = set_portfolio_approved(portfolio_data, True)
    save_portfolio(updated)
    return updated
