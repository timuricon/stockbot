"""
telegram_ui/handlers/portfolio.py
Обработчик кнопок портфеля (pb_*).
"""

import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.config import TICKER_ALTERNATIVES
from core.logging_setup import logger
from core.state import AppState
from data_provider.yfinance_client import get_dividend_yield
from portfolio.portfolio_io import (
    load_portfolio, portfolio_exists, save_portfolio,
)
from portfolio.portfolio_manager import (
    get_alternatives_for_type,
    do_replace_ticker, do_approve_ticker, do_finalize_approval,
)
from portfolio.portfolio_analysis import format_portfolio_details
from telegram_ui.keyboards import (
    BACK_KEYBOARD,
    build_replace_keyboard, build_approval_keyboard,
    build_alternatives_keyboard, build_confirm_replace_keyboard,
)
from telegram_ui.formatting import format_approval_screen


async def portfolio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data         = query.data
    state: AppState  = context.application.bot_data["state"]
    port_session = state.portfolio
    logger.info(f"portfolio_handler: {data}")

    # ── View ──────────────────────────────────────────────────────────────────
    if data == "pb_view":
        if not portfolio_exists():
            await query.edit_message_text(
                "❌ Файл <code>portfolio_b.json</code> не найден.",
                parse_mode="HTML", reply_markup=BACK_KEYBOARD,
            )
            return
        try:
            port_data = load_portfolio()
        except Exception as e:
            await query.edit_message_text(
                f"❌ Ошибка чтения: {str(e)[:100]}", parse_mode="HTML", reply_markup=BACK_KEYBOARD,
            )
            return

        holdings      = port_data.get("holdings", [])
        currency      = port_data.get("currency", "USD")
        approved      = port_data.get("approved", False)
        last_updated  = port_data.get("last_updated", "—")
        tgt_yield     = port_data.get("target_annual_yield_pct", 8.0)
        current_total = sum(h.get("current_value", 0) for h in holdings)

        TYPE_EMOJI = {"BDC": "🟣", "CEF": "🔴", "HY_BOND": "🟡", "REIT": "🔵", "DIVIDEND": "🟢"}
        status_str = "✅ Одобрен" if approved else "⏳ Ожидает"

        lines = [
            "📋 <b>Текущий состав Портфеля B</b>",
            f"📅 {last_updated}  |  {status_str}  |  🎯 {tgt_yield}%",
        ]
        if current_total > 0:
            lines.append(f"💵 Стоимость: <b>{current_total:,.0f} {currency}</b>")
        lines.append("")

        weighted_yield = 0.0
        for h in holdings:
            emoji      = TYPE_EMOJI.get(h.get("type", ""), "⚪")
            ticker     = h.get("ticker", "?")
            htype      = h.get("type", "")
            name       = h.get("name", "")
            target_pct = h.get("target_pct", 0)
            cur_val    = h.get("current_value", 0)
            yld        = h.get("current_yield_pct", 0)
            payout     = "✅" if h.get("payout_ok", True) else "🚨"
            appr       = "✅" if h.get("approved", False) else "⬜"
            cur_pct    = (cur_val / current_total * 100) if current_total > 0 else 0
            weighted_yield += (cur_pct / 100) * yld
            val_str = (
                f"{cur_val:,.0f} {currency} ({cur_pct:.1f}%)"
                if cur_val > 0 else f"цель {target_pct}%"
            )
            lines.append(
                f"{appr} {emoji} <b>{ticker}</b> [{htype}] {payout}\n"
                f"   {name}\n   💰 {val_str} | yield ~{yld:.1f}%"
            )

        if current_total > 0 and weighted_yield > 0:
            y_emoji = "✅" if weighted_yield >= tgt_yield else "🔴"
            lines.append(f"\n{y_emoji} <b>Взвешенный yield: {weighted_yield:.1f}%</b>")
        lines.append("\n<i>⚠️ Не является инвестиционным советом. DYOR.</i>")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=BACK_KEYBOARD)

    # ── Edit ──────────────────────────────────────────────────────────────────
    elif data == "pb_edit":
        if not portfolio_exists():
            await query.edit_message_text(
                "❌ Файл не найден.", parse_mode="HTML", reply_markup=BACK_KEYBOARD,
            )
            return
        port_data = load_portfolio()
        for h in port_data.get("holdings", []):
            h["approved"] = True
        # Сохраняем на диск: дальше pb_approve__ перечитывает файл,
        # несохранённое in-memory состояние молча терялось бы
        save_portfolio(port_data)
        port_session.last_portfolio = port_data
        text     = format_approval_screen(port_data["holdings"])
        keyboard = build_approval_keyboard(port_data["holdings"])
        await query.edit_message_text(
            "✏️ <b>Редактирование состава портфеля</b>\n\n" + text,
            parse_mode="HTML", reply_markup=keyboard,
        )

    # ── Approve toggle ────────────────────────────────────────────────────────
    elif data.startswith("pb_approve__"):
        ticker = data.split("__")[1]
        if not portfolio_exists():
            await query.answer("❌ Файл не найден")
            return
        port_data = load_portfolio()
        port_data = do_approve_ticker(port_data, ticker)
        port_session.last_portfolio = port_data
        text     = format_approval_screen(port_data["holdings"])
        keyboard = build_approval_keyboard(port_data["holdings"])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            if "not modified" not in str(e).lower():
                raise

    elif data == "pb_noop":
        await query.answer("Сначала одобрите все тикеры.")

    # ── Finalize approval ─────────────────────────────────────────────────────
    elif data == "pb_approval_done":
        port_data = port_session.last_portfolio or (load_portfolio() if portfolio_exists() else {})
        if port_data:
            port_data = do_finalize_approval(port_data)
            port_session.last_portfolio = port_data
        await query.edit_message_text(
            "✅ <b>Портфель одобрен!</b>\n\nНажмите <b>💼 Портфель B</b> для ребалансировки.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Главное меню", callback_data="back")]]),
        )

    # ── Replace on approval screen ────────────────────────────────────────────
    elif data.startswith("pb_replace_approval__"):
        parts = data.split("__")
        if len(parts) != 3:
            await query.answer("Ошибка формата.")
            return
        _, old_ticker, htype = parts
        chat_id         = query.message.chat_id
        port_data       = port_session.last_portfolio or {}
        current_tickers = {h["ticker"] for h in port_data.get("holdings", [])}
        alts            = get_alternatives_for_type(htype, current_tickers)
        port_session.replace_session[chat_id] = {
            "old_ticker": old_ticker, "htype": htype, "mode": "approval",
        }
        await query.edit_message_text(
            f"🔄 <b>Замена {old_ticker} [{htype}]</b>\n\n<b>Готовые варианты:</b>",
            parse_mode="HTML",
            reply_markup=build_alternatives_keyboard(old_ticker, htype, alts),
        )

    elif data == "pb_reshow_approval":
        port_data = port_session.last_portfolio
        if not port_data or not port_data.get("holdings"):
            await query.edit_message_text(
                "⚠️ Данные не найдены.", parse_mode="HTML", reply_markup=BACK_KEYBOARD,
            )
            return
        text     = format_approval_screen(port_data["holdings"])
        keyboard = build_approval_keyboard(port_data["holdings"])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            if "not modified" not in str(e).lower():
                raise

    elif data == "pb_details":
        if not port_session.last_portfolio:
            await query.edit_message_text(
                "⚠️ Нет данных. Сначала запустите ребалансировку.",
                parse_mode="HTML", reply_markup=BACK_KEYBOARD,
            )
            return
        await query.edit_message_text(
            format_portfolio_details(port_session.last_portfolio),
            parse_mode="HTML", reply_markup=BACK_KEYBOARD,
        )

    # ── Replace (from report) ─────────────────────────────────────────────────
    elif data.startswith("pb_replace__"):
        parts = data.split("__")
        if len(parts) != 3:
            await query.answer("Ошибка формата.")
            return
        _, old_ticker, htype = parts
        chat_id         = query.message.chat_id
        current_tickers = {h["ticker"] for h in port_session.last_portfolio.get("holdings", [])}
        alts            = get_alternatives_for_type(htype, current_tickers)
        if not alts:
            await query.answer("Нет доступных альтернатив.")
            return
        port_session.replace_session[chat_id] = {"old_ticker": old_ticker, "htype": htype}
        await query.edit_message_text(
            f"🔄 <b>Замена {old_ticker} [{htype}]</b>\n\nВыберите альтернативу:",
            parse_mode="HTML",
            reply_markup=build_alternatives_keyboard(old_ticker, htype, alts),
        )

    # ── Confirm replace ───────────────────────────────────────────────────────
    elif data.startswith("pb_confirm_replace__"):
        parts = data.split("__")
        if len(parts) != 3:
            await query.answer("Ошибка формата.")
            return
        _, old_ticker, new_ticker = parts
        chat_id   = query.message.chat_id
        port_data = port_session.last_portfolio

        if not port_data:
            await query.edit_message_text(
                "⚠️ Данные не найдены. Запустите ребалансировку заново.",
                parse_mode="HTML", reply_markup=BACK_KEYBOARD,
            )
            return

        htype = next(
            (h["type"] for h in port_data.get("holdings", []) if h["ticker"] == old_ticker), "",
        )
        search_found = (port_session.replace_session.get(chat_id) or {}).get("found")
        if search_found and search_found["ticker"] == new_ticker:
            new_alt = search_found
        else:
            new_alt = next(
                (a for a in TICKER_ALTERNATIVES.get(htype, []) if a["ticker"] == new_ticker), None,
            )
        if not new_alt:
            # yfinance без таймаута — вынос из event loop обязателен
            try:
                live_y = await asyncio.wait_for(
                    asyncio.to_thread(get_dividend_yield, new_ticker), timeout=30,
                )
            except asyncio.TimeoutError:
                live_y = None
            new_alt = {"ticker": new_ticker, "name": new_ticker, "yield_pct": live_y or 0.0}

        port_data = do_replace_ticker(
            port_data, old_ticker, new_ticker,
            new_alt.get("name", new_ticker), new_alt.get("yield_pct", 0.0),
        )
        port_session.last_portfolio = port_data
        port_session.replace_session.pop(chat_id, None)

        if not port_data.get("approved", False):
            text     = format_approval_screen(port_data["holdings"])
            keyboard = build_approval_keyboard(port_data["holdings"])
            await query.edit_message_text(
                f"✅ {old_ticker} → {new_ticker} | yield ~{new_alt.get('yield_pct', 0)}%\n\n" + text,
                parse_mode="HTML", reply_markup=keyboard,
            )
        else:
            await query.edit_message_text(
                f"✅ <b>Замена выполнена</b>\n\n"
                f"{old_ticker} → {new_ticker} ({new_alt.get('name', new_ticker)})\n"
                f"Новый yield: ~{new_alt.get('yield_pct', 0)}%\n\n"
                "<i>Запустите ребалансировку снова.</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Главное меню", callback_data="back")]]),
            )
