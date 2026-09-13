"""
telegram_ui/handlers/conversations.py
ConversationHandler функции: ввод стоимостей портфеля, поиск тикера.
"""

import asyncio
import json

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from core.logging_setup import logger
from core.state import AppState
from portfolio.portfolio_io import (
    load_portfolio, portfolio_exists, update_holding_values,
)
from portfolio.portfolio_manager import verify_ticker_yield
from portfolio.portfolio_analysis import analyze_portfolio
from telegram_ui.keyboards import (
    BACK_KEYBOARD, UNIVERSE_KEYBOARD,
    build_replace_keyboard, build_approval_keyboard, build_confirm_replace_keyboard,
)
from telegram_ui.formatting import format_approval_screen

# ConversationHandler states
PB_INPUT  = 0
PB_SEARCH = 1


async def pb_start_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    state: AppState = context.application.bot_data["state"]

    if not portfolio_exists():
        await query.edit_message_text(
            "❌ Файл <code>portfolio_b.json</code> не найден.",
            parse_mode="HTML", reply_markup=BACK_KEYBOARD,
        )
        return ConversationHandler.END

    port_data = load_portfolio()
    holdings  = port_data.get("holdings", [])

    if not port_data.get("approved", False):
        state.portfolio.last_portfolio = port_data
        text     = format_approval_screen(holdings)
        keyboard = build_approval_keyboard(holdings)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return ConversationHandler.END

    chat_id = query.message.chat_id
    state.portfolio.pb_session[chat_id] = {
        "step": 0, "values": [], "data": port_data, "holdings": holdings,
    }
    h = holdings[0]
    await query.edit_message_text(
        f"💼 <b>Портфель B — ввод стоимостей</b>\n\n"
        f"Введите стоимость <b>{h['ticker']}</b> ({h.get('name', '')}) в USD.\n"
        f"<i>Позиция 1 из {len(holdings)}. Например: 12500</i>\n\n"
        f"Напишите /pb_cancel для отмены.",
        parse_mode="HTML",
    )
    return PB_INPUT


async def pb_receive_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    state: AppState = context.application.bot_data["state"]
    session = state.portfolio.pb_session.get(chat_id)

    if not session:
        await update.message.reply_text("⚠️ Сессия не найдена. Нажмите кнопку ещё раз.")
        return ConversationHandler.END

    text = update.message.text.strip().replace(",", ".").replace(" ", "")
    try:
        value = float(text)
        if value < 0:
            raise ValueError("negative")
    except ValueError:
        h = session["holdings"][session["step"]]
        await update.message.reply_text(
            f"❌ Нераспознано. Введите число для <b>{h['ticker']}</b>.", parse_mode="HTML",
        )
        return PB_INPUT

    session["values"].append(value)
    session["step"] += 1
    step     = session["step"]
    holdings = session["holdings"]

    if step < len(holdings):
        h      = holdings[step]
        prev_h = holdings[step - 1]
        await update.message.reply_text(
            f"✅ <b>{prev_h['ticker']}</b>: {value:,.0f} USD\n\n"
            f"Введите стоимость <b>{h['ticker']}</b> ({h.get('name', '')}):\n"
            f"<i>Позиция {step + 1} из {len(holdings)}</i>",
            parse_mode="HTML",
        )
        return PB_INPUT

    port_data = update_holding_values(session["data"], session["values"])
    state.portfolio.last_portfolio = port_data
    del state.portfolio.pb_session[chat_id]

    await update.message.reply_text("⏳ Считаю ребалансировку...")

    try:
        # 10+ позиций × 2 запроса yfinance — 60с может не хватить
        report, problem_tickers = await asyncio.wait_for(
            asyncio.to_thread(analyze_portfolio, port_data), timeout=90,
        )
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "⚠️ Анализ не успел выполниться (таймаут 90с). "
            "Попробуйте ещё раз — данные уже загружены в кэш.",
            parse_mode="HTML",
        )
        return ConversationHandler.END
    keyboard = build_replace_keyboard(problem_tickers)
    await update.message.reply_text(report, parse_mode="HTML", reply_markup=keyboard)
    return ConversationHandler.END


async def pb_search_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    state: AppState = context.application.bot_data["state"]
    parts = query.data.split("__")
    if len(parts) != 3:
        await query.edit_message_text("❌ Ошибка формата.", reply_markup=BACK_KEYBOARD)
        return ConversationHandler.END
    _, old_ticker, htype = parts
    chat_id = query.message.chat_id
    state.portfolio.replace_session[chat_id] = {
        "old_ticker": old_ticker, "htype": htype, "mode": "search",
    }
    await query.edit_message_text(
        f"🔍 <b>Поиск альтернативы для {old_ticker}</b>\n\n"
        f"Введите тикер вместо <b>{old_ticker}</b> (например: <code>HTGC</code>)\n\n"
        f"Напишите /pb_cancel для отмены.",
        parse_mode="HTML",
    )
    return PB_SEARCH


async def pb_receive_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    state: AppState = context.application.bot_data["state"]
    session = state.portfolio.replace_session.get(chat_id)

    if not session:
        await update.message.reply_text("⚠️ Сессия поиска не найдена.")
        return ConversationHandler.END

    ticker_input = update.message.text.strip().upper()
    old_ticker   = session["old_ticker"]
    htype        = session["htype"]

    await update.message.reply_text(f"⏳ Проверяю <b>{ticker_input}</b>...", parse_mode="HTML")

    # yfinance без таймаута — обязателен вынос из event loop, иначе виснет весь бот
    try:
        live_yield, name = await asyncio.wait_for(
            asyncio.to_thread(verify_ticker_yield, ticker_input), timeout=45,
        )
    except asyncio.TimeoutError:
        live_yield, name = None, None

    if not live_yield:
        await update.message.reply_text(
            f"⚠️ Не удалось получить yield для <b>{ticker_input}</b>.\n"
            f"Попробуйте другой тикер или /pb_cancel.",
            parse_mode="HTML",
        )
        return PB_SEARCH

    state.portfolio.replace_session[chat_id]["found"] = {
        "ticker": ticker_input, "name": name, "yield_pct": live_yield,
    }
    await update.message.reply_text(
        f"✅ <b>{ticker_input}</b> — {name}\nYield: <b>{live_yield}%</b>\n\n"
        f"Заменить <b>{old_ticker}</b> → <b>{ticker_input}</b>?",
        parse_mode="HTML",
        reply_markup=build_confirm_replace_keyboard(old_ticker, ticker_input, htype),
    )
    return ConversationHandler.END


async def pb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    state: AppState = context.application.bot_data["state"]
    state.portfolio.pb_session.pop(chat_id, None)
    await update.message.reply_text("❌ Отменено. Вернитесь через /start.")
    return ConversationHandler.END


async def handle_portfolio_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает JSON-файл портфеля прямо в чат."""
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".json"):
        return

    state: AppState = context.application.bot_data["state"]
    await update.message.reply_text("📂 Получил файл. Анализирую...")

    try:
        file       = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()
        port_data  = json.loads(file_bytes.decode("utf-8"))
        state.portfolio.last_portfolio = port_data

        loop = asyncio.get_running_loop()
        report, problem_tickers = await loop.run_in_executor(
            None, analyze_portfolio, port_data,
        )
        keyboard = build_replace_keyboard(problem_tickers)
        await update.message.reply_text(report, parse_mode="HTML", reply_markup=keyboard)
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Файл повреждён или не является корректным JSON.")
    except Exception as e:
        logger.error(f"Portfolio file error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")
