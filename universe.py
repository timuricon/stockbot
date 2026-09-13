"""
Управление universe тикеров для unified скана.
tickers_universe.json — контролируемый список акций для сканирования.

Стартовая база: tickers_cache.json (только CS, NYSE/NASDAQ).
Обновление: раз в неделю через Polygon reference API.
Ручное управление: add_ticker / remove_ticker через кнопки бота.
"""

import json
import os
from datetime import datetime
from typing import Optional

from core.config import TICKER_CACHE_FILE, ALLOWED_EXCHANGES
from core.logging_setup import logger

UNIVERSE_FILE        = "tickers_universe.json"
UNIVERSE_TTL_DAYS    = 7
UNIVERSE_MAX_TICKERS = 6000


def _load_raw() -> dict:
    """Загружает файл universe целиком."""
    if not os.path.exists(UNIVERSE_FILE):
        return {}
    try:
        with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[Universe] Load error: {e}")
        return {}


def _save_raw(data: dict) -> bool:
    try:
        with open(UNIVERSE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"[Universe] Save error: {e}")
        return False


def load_universe() -> list[dict]:
    """
    Возвращает список тикеров из universe.
    Если файла нет или он устарел — пересобирает из tickers_cache.json.
    """
    raw = _load_raw()

    if raw.get("tickers"):
        updated = raw.get("updated", "2000-01-01")
        try:
            age_days = (datetime.now() - datetime.fromisoformat(updated)).days
        except Exception:
            age_days = 999

        if age_days < UNIVERSE_TTL_DAYS:
            logger.info(
                f"[Universe] Loaded {len(raw['tickers'])} tickers "
                f"(age {age_days}d)"
            )
            return raw["tickers"]
        else:
            logger.info(f"[Universe] Cache is {age_days}d old — rebuilding...")

    return rebuild_universe()


def rebuild_universe() -> list[dict]:
    """
    Пересобирает universe из tickers_cache.json.
    Берёт только реальные акции (type=CS) на NYSE/NASDAQ.
    Сохраняет в tickers_universe.json.
    """
    if not os.path.exists(TICKER_CACHE_FILE):
        logger.error(f"[Universe] {TICKER_CACHE_FILE} not found — cannot build universe")
        return []

    try:
        with open(TICKER_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as e:
        logger.error(f"[Universe] Failed to read ticker cache: {e}")
        return []

    tickers = []
    for ticker, info in cache.items():
        # Только реальные акции, не ETF
        if info.get("type") != "CS":
            continue
        if info.get("exchange") not in ALLOWED_EXCHANGES:
            continue
        # Отсеиваем тикеры с точками и слэшами (preferred shares, warrants)
        if "." in ticker or "/" in ticker:
            continue

        tickers.append({
            "ticker":   ticker,
            "name":     info.get("name", ""),
            "exchange": info.get("exchange", ""),
            "sector":   info.get("sic", ""),
            "added":    datetime.now().strftime("%Y-%m-%d"),
            "manual":   False,
        })

    # Ограничиваем размер
    tickers = tickers[:UNIVERSE_MAX_TICKERS]

    raw = {
        "updated": datetime.now().isoformat(),
        "count":   len(tickers),
        "tickers": tickers,
    }
    _save_raw(raw)
    logger.info(f"[Universe] Rebuilt: {len(tickers)} tickers saved to {UNIVERSE_FILE}")
    return tickers


def get_ticker_set() -> set[str]:
    """Возвращает множество тикеров для быстрой проверки принадлежности."""
    return {t["ticker"] for t in load_universe()}


def add_ticker(ticker: str, name: str = "") -> tuple[bool, str]:
    """
    Добавляет тикер вручную.
    Возвращает (success, message).
    """
    ticker = ticker.upper().strip()
    if not ticker or "." in ticker or "/" in ticker:
        return False, f"Некорректный тикер: {ticker}"

    raw = _load_raw()
    tickers = raw.get("tickers", [])

    # Проверяем дубликат
    existing = [t for t in tickers if t["ticker"] == ticker]
    if existing:
        return False, f"${ticker} уже есть в universe"

    tickers.append({
        "ticker":   ticker,
        "name":     name or ticker,
        "exchange": "MANUAL",
        "sector":   "",
        "added":    datetime.now().strftime("%Y-%m-%d"),
        "manual":   True,
    })

    raw["tickers"] = tickers
    raw["count"]   = len(tickers)
    _save_raw(raw)
    logger.info(f"[Universe] Added manually: {ticker}")
    return True, f"✅ ${ticker} добавлен в universe ({len(tickers)} тикеров всего)"


def remove_ticker(ticker: str) -> tuple[bool, str]:
    """
    Удаляет тикер из universe.
    Возвращает (success, message).
    """
    ticker = ticker.upper().strip()
    raw    = _load_raw()
    tickers = raw.get("tickers", [])

    before = len(tickers)
    tickers = [t for t in tickers if t["ticker"] != ticker]

    if len(tickers) == before:
        return False, f"${ticker} не найден в universe"

    raw["tickers"] = tickers
    raw["count"]   = len(tickers)
    _save_raw(raw)
    logger.info(f"[Universe] Removed: {ticker}")
    return True, f"✅ ${ticker} удалён из universe ({len(tickers)} тикеров всего)"


def get_universe_stats() -> dict:
    """Статистика для отображения в боте."""
    raw = _load_raw()
    tickers = raw.get("tickers", [])
    updated = raw.get("updated", "—")
    manual_count = sum(1 for t in tickers if t.get("manual"))

    try:
        age_days = (datetime.now() - datetime.fromisoformat(updated)).days
    except Exception:
        age_days = None

    return {
        "total":    len(tickers),
        "manual":   manual_count,
        "updated":  updated[:10] if updated != "—" else "—",
        "age_days": age_days,
    }
