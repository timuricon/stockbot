"""
Superstock History — персистентность результатов superstock скана.

Логика:
- После каждого скана сохраняем снапшот в superstock_history/
- При следующем запуске показываем прошлых кандидатов с обновлёнными ценами
- Кандидат помечается как устаревший если:
    * цена упала под 30W EMA (ema30_val из снапшота)
    * цена перегрелась выше 35% от EMA
    * цена достигла или превысила consensus_tp

Формат файла: superstock_history/snapshot_YYYY-MM-DD_HH-MM.json
"""

import json
import os
from datetime import datetime
from typing import Optional

from core.logging_setup import logger

HISTORY_DIR = "superstock_history"
LATEST_FILE = os.path.join(HISTORY_DIR, "latest.json")


# ── I/O ───────────────────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)


def save_snapshot(picks: list[dict], partial: bool = False) -> None:
    """
    Сохраняет результаты скана в файл снапшота и перезаписывает latest.json.
    partial=True — скан был прерван вручную: пометка остаётся в снапшоте,
    чтобы «Кандидаты прошлого скана» честно показывали частичность.
    """
    _ensure_dir()
    ts = datetime.now()
    filename = f"snapshot_{ts.strftime('%Y-%m-%d_%H-%M')}.json"
    filepath = os.path.join(HISTORY_DIR, filename)

    snapshot = {
        "timestamp": ts.isoformat(),
        "total": len(picks),
        "picks": picks,
    }
    if partial:
        snapshot["partial"] = True

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        with open(LATEST_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        logger.info(f"[SS History] Saved {len(picks)} picks → {filepath}")
    except Exception as e:
        logger.error(f"[SS History] Save error: {e}")


def load_latest() -> Optional[dict]:
    """
    Загружает последний снапшот.
    Возвращает dict {"timestamp", "total", "picks"[, "partial"]} или None.
    """
    if not os.path.exists(LATEST_FILE):
        return None
    try:
        with open(LATEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[SS History] Load error: {e}")
        return None


def get_history_stats() -> dict:
    """Возвращает статистику для отображения в боте."""
    _ensure_dir()
    files = [
        f for f in os.listdir(HISTORY_DIR)
        if f.startswith("snapshot_") and f.endswith(".json")
    ]
    latest = load_latest()
    return {
        "snapshots": len(files),
        "last_ts": latest["timestamp"][:16].replace("T", " ") if latest else None,
        "last_count": latest["total"] if latest else 0,
    }


# ── Статус кандидата ──────────────────────────────────────────────────────────

def _candidate_status(pick: dict, current_price: float) -> str:
    """
    Определяет актуальность кандидата на основе текущей цены.

    Возвращает:
        "active"  — условия входа сохраняются
        "expired" — цена упала под 30W EMA
        "hot"     — перегрев >35% над EMA (ждать отката)
        "target"  — цена достигла/превысила consensus_tp
    """
    ema30_val = pick.get("ema30_val", 0)
    consensus = pick.get("consensus_tp")

    if ema30_val > 0:
        if current_price <= ema30_val:
            return "expired"
        ema_pct = (current_price - ema30_val) / ema30_val * 100
        if ema_pct > 35:
            return "hot"

    if consensus and current_price >= float(consensus):
        return "target"

    return "active"


# ── Обновление цен ────────────────────────────────────────────────────────────

def refresh_picks(picks: list[dict]) -> list[dict]:
    """
    Обновляет цены кандидатов через yfinance.
    Добавляет поля: current_price, price_change_pct, status, refreshed_at.
    Если yfinance недоступен — возвращает исходные данные со статусом "unknown".
    """
    from data_provider.yfinance_client import get_current_price, YF_AVAILABLE

    refreshed = []
    for pick in picks:
        entry = dict(pick)
        ticker = pick.get("ticker", "")
        entry_price = float(pick.get("price", 0))

        current = get_current_price(ticker) if (YF_AVAILABLE and ticker) else None

        if current and current > 0:
            change_pct = (
                round((current - entry_price) / entry_price * 100, 1)
                if entry_price > 0 else 0
            )
            entry["current_price"] = round(current, 2)
            entry["price_change_pct"] = change_pct
            entry["status"] = _candidate_status(pick, current)
        else:
            entry["current_price"] = None
            entry["price_change_pct"] = None
            entry["status"] = "unknown"

        entry["refreshed_at"] = datetime.now().strftime("%H:%M")
        refreshed.append(entry)

    return refreshed
