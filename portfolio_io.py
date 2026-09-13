import json
import os
import shutil
from datetime import datetime
from typing import Optional

from core.config import PORTFOLIO_B_FILE
from core.logging_setup import logger


def load_portfolio(filepath: str = PORTFOLIO_B_FILE) -> dict:
    """Загружает portfolio_b.json."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(data: dict, filepath: str = PORTFOLIO_B_FILE) -> bool:
    """
    Атомарно сохраняет portfolio_b.json: пишем во временный файл, затем
    os.replace (безопасно при сбое питания — старая версия не портится).
    Предыдущая версия сохраняется как .bak — единственная страховка данных.
    """
    tmp_path = filepath + ".tmp"
    bak_path = filepath + ".bak"
    try:
        if os.path.exists(filepath):
            try:
                shutil.copy2(filepath, bak_path)
            except Exception as e:
                logger.warning(f"Portfolio backup failed: {e}")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
        return True
    except Exception as e:
        logger.error(f"Portfolio save error: {e}")
        return False


def portfolio_exists(filepath: str = PORTFOLIO_B_FILE) -> bool:
    return os.path.exists(filepath)


def update_holding_values(data: dict, values: list[float]) -> dict:
    """Подставляет введённые пользователем стоимости в holdings."""
    for i, h in enumerate(data.get("holdings", [])):
        if i < len(values):
            h["current_value"] = values[i]
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    return data


def replace_holding(data: dict, old_ticker: str, new_ticker: str,
                    new_name: str, new_yield: float, htype: str) -> dict:
    """Заменяет тикер в holdings."""
    for h in data.get("holdings", []):
        if h["ticker"] == old_ticker:
            h["ticker"]            = new_ticker
            h["name"]              = new_name
            h["current_yield_pct"] = new_yield
            h["payout_ok"]         = True
            h["approved"]          = False
            h["notes"]             = f"Заменён с {old_ticker} → {new_ticker}"
            h["type"]              = htype or h.get("type", "")
            break
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    return data


def approve_holding(data: dict, ticker: str) -> dict:
    """Переключает флаг approved у тикера."""
    for h in data.get("holdings", []):
        if h["ticker"] == ticker:
            h["approved"] = not h.get("approved", False)
            break
    return data


def set_portfolio_approved(data: dict, approved: bool = True) -> dict:
    """Устанавливает флаг approved на уровне всего портфеля."""
    data["approved"] = approved
    return data
