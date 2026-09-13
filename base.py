"""
telegram_ui/handlers/base.py
Базовые команды: /start, /stop, error_handler.
"""

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from core.config import OWNER_ID
from core.logging_setup import logger
from core.state import AppState
from telegram_ui.keyboards import MAIN_KEYBOARD


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>NYSE/NASDAQ Trading Bot</b>\n\n"
        "Сканирую <b>топ-140 тикеров</b> по объёму.\n"
        "<i>Free tier Polygon (5 запросов/мин): скан может идти 30–90 минут.</i>\n\n"
        "<b>Фильтры:</b>\n"
        "• NYSE и NASDAQ • Цена > 30W EMA\n"
        "• Потенциал ≥ 25% • Только лонг\n\n"
        "Выбери действие:",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: AppState = context.application.bot_data["state"]
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ У тебя нет прав для этой команды.")
        return

    # Реальная cooperative-отмена: сканы проверяют cancel_event между тикерами
    # и завершаются после текущей итерации (паузы прерываемые).
    state.unified.cancel_event.set()
    state.unified.et_only_cancel_event.set()
    state.superstock.cancel_event.set()

    running = (
        state.unified.running
        or state.unified.et_only_running
        or state.superstock.running
    )
    msg = (
        "🛑 Останавливаю бот. Запущенным сканам отправлен сигнал отмены — "
        "они завершатся после текущего тикера."
        if running else "🛑 Бот останавливается..."
    )
    await update.message.reply_text(msg)
    await asyncio.sleep(1)
    await context.application.updater.stop()
    await context.application.stop()
    await context.application.shutdown()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"ERROR: {type(context.error).__name__}: {context.error}")
