"""
Локальный дисковый кэш исторических баров.

Структура:
  bars_cache/
    NVDA_1d.json  → {"updated": "2026-05-08", "bars": [...raw polygon results...]}
    NVDA_1w.json  → {"updated": "2026-05-08", "bars": [...]}

Логика:
- Первый запрос: грузим полную историю из Polygon, сохраняем на диск.
- Повторный запрос в тот же день: читаем с диска, не идём в Polygon.
- Следующий день: грузим только недостающие бары (from=last_date+1).
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from core.logging_setup import logger

BARS_CACHE_DIR = "bars_cache"


def _ensure_dir() -> None:
    os.makedirs(BARS_CACHE_DIR, exist_ok=True)


def _cache_path(ticker: str, interval: str) -> str:
    """bars_cache/NVDA_1d.json"""
    return os.path.join(BARS_CACHE_DIR, f"{ticker}_{interval}.json")


def _load_cache(ticker: str, interval: str) -> Optional[dict]:
    """Читает кэш с диска. Возвращает dict или None."""
    path = _cache_path(ticker, interval)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.debug(f"[BarsCache] Read error {ticker}_{interval}: {e}")
        return None


def _save_cache(ticker: str, interval: str, bars: list) -> None:
    """Сохраняет бары на диск."""
    _ensure_dir()
    path = _cache_path(ticker, interval)
    try:
        with open(path, "w") as f:
            json.dump({
                "updated": datetime.now().strftime("%Y-%m-%d"),
                "bars":    bars,
            }, f)
    except Exception as e:
        logger.debug(f"[BarsCache] Write error {ticker}_{interval}: {e}")


def is_cache_fresh(ticker: str, interval: str) -> bool:
    """True если кэш обновлён сегодня."""
    cached = _load_cache(ticker, interval)
    if not cached:
        return False
    return cached.get("updated") == datetime.now().strftime("%Y-%m-%d")


def get_cached_bars(ticker: str, interval: str) -> Optional[list]:
    """Возвращает список raw bars из кэша или None."""
    cached = _load_cache(ticker, interval)
    if cached and cached.get("bars"):
        return cached["bars"]
    return None


def get_last_cached_date(ticker: str, interval: str) -> Optional[str]:
    """
    Возвращает дату последнего бара в кэше (YYYY-MM-DD) или None.
    Используется чтобы запрашивать только новые бары.
    """
    cached = _load_cache(ticker, interval)
    if not cached or not cached.get("bars"):
        return None
    try:
        # Бары от Polygon хранят timestamp в ms в поле "t"
        last_bar = cached["bars"][-1]
        ts_ms    = last_bar.get("t", 0)
        if ts_ms:
            return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def merge_and_save(ticker: str, interval: str, old_bars: list, new_bars: list) -> list:
    """
    Объединяет старые и новые бары (дедупликация по timestamp),
    сохраняет на диск, возвращает итоговый список.
    """
    existing_ts = {b["t"] for b in old_bars}
    for bar in new_bars:
        if bar["t"] not in existing_ts:
            old_bars.append(bar)
            existing_ts.add(bar["t"])

    # Сортируем по времени
    old_bars.sort(key=lambda x: x["t"])
    _save_cache(ticker, interval, old_bars)
    return old_bars


def clear_cache(ticker: str = None) -> int:
    """
    Очищает кэш: если ticker задан — удаляет файлы только по нему,
    иначе удаляет всю папку bars_cache/.
    Возвращает количество удалённых файлов.
    """
    _ensure_dir()
    count = 0
    for fname in os.listdir(BARS_CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        if ticker is None or fname.startswith(f"{ticker}_"):
            try:
                os.remove(os.path.join(BARS_CACHE_DIR, fname))
                count += 1
            except Exception:
                pass
    return count


def get_cache_stats() -> dict:
    """Статистика кэша для отображения в боте."""
    _ensure_dir()
    files = [f for f in os.listdir(BARS_CACHE_DIR) if f.endswith(".json")]
    total_size = sum(
        os.path.getsize(os.path.join(BARS_CACHE_DIR, f))
        for f in files
    )
    return {
        "files":      len(files),
        "size_mb":    round(total_size / 1024 / 1024, 1),
        "tickers":    len(set(f.rsplit("_", 1)[0] for f in files)),
    }
