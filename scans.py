"""
telegram_ui/handlers/scans.py
Обработчики кнопок: market, unified scan, superstock, universe, evaluate, help, back.
"""

import asyncio
import threading
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.logging_setup import logger
from core.state import AppState
from data_provider.polygon_client import get_last_two_daily_bars
from portfolio.universe import (
    load_universe, add_ticker, remove_ticker,
    get_universe_stats, rebuild_universe,
)
from analytics.superstock_history import load_latest, refresh_picks, get_history_stats
from telegram_ui.keyboards import (
    MAIN_KEYBOARD, BACK_KEYBOARD, UNIFIED_RESULT_KEYBOARD, UNIVERSE_KEYBOARD,
    build_unified_keyboard, build_superstock_keyboard, build_cancel_keyboard,
)
from telegram_ui.formatting import (
    format_market_overview, format_unified_pick, format_near_miss,
    format_superstock_pick, format_et_only_pick,
)
from telegram_ui.scan_threads import (
    run_unified_thread, run_superstock_thread, run_et_only_thread,
    run_evaluation_thread,
)


# ── Market overview ───────────────────────────────────────────────────────────

def _get_ticker_price(ticker: str) -> Optional[dict]:
    end_date   = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    data = get_last_two_daily_bars(ticker, start_date, end_date)
    if not data or "results" not in data or len(data["results"]) < 2:
        if data and "results" in data and len(data["results"]) == 1:
            r = data["results"][-1]
            return {"price": r["c"], "prev_price": r["o"],
                    "chg_pct": ((r["c"] - r["o"]) / r["o"]) * 100}
        return None
    prev = data["results"][-2]
    last = data["results"][-1]
    chg  = ((last["c"] - prev["c"]) / prev["c"]) * 100
    return {"price": last["c"], "prev_price": prev["c"], "chg_pct": chg}


def _get_market_overview() -> str:
    indices = {
        "S&P 500 (SPY)":      "SPY",
        "NASDAQ 100 (QQQ)":   "QQQ",
        "Dow Jones (DIA)":    "DIA",
        "Russell 2000 (IWM)": "IWM",
    }
    indices_data = {name: _get_ticker_price(etf) for name, etf in indices.items()}
    uvxy_data    = _get_ticker_price("UVXY")
    return format_market_overview(indices_data, uvxy_data)


# ── Главный обработчик кнопок ─────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data
    state: AppState = context.application.bot_data["state"]
    logger.info(f"button_handler: {data}")

    # ── Market ────────────────────────────────────────────────────────────────
    if data == "market":
        await query.edit_message_text("⏳ Получаю данные рынка...", parse_mode="HTML")
        loop = asyncio.get_running_loop()
        try:
            text = await asyncio.wait_for(
                loop.run_in_executor(None, _get_market_overview), timeout=55
            )
        except asyncio.TimeoutError:
            text = "⚠️ Превышено время ожидания."
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=BACK_KEYBOARD,
            disable_web_page_preview=True,
        )

    # ── Unified Scan ──────────────────────────────────────────────────────────
    elif data == "unified_scan":
        if state.unified.running:
            await query.edit_message_text(
                "⏳ <b>Unified скан уже идёт...</b>\n\nНапишу когда закончу.",
                parse_mode="HTML", reply_markup=build_cancel_keyboard("unified_cancel"),
            )
        elif state.unified.cache_picks is not None:
            picks = state.unified.cache_picks
            ts    = state.unified.cache_ts
            age_h = round((datetime.now() - ts).total_seconds() / 3600, 1)
            await query.edit_message_text(
                f"🔍 <b>Unified скан</b> от {ts.strftime('%d.%m %H:%M')} (кэш {age_h}ч)\n"
                f"Найдено сигналов: <b>{len(picks)}</b>",
                parse_mode="HTML",
                reply_markup=build_unified_keyboard(
                    has_cache=True,
                    has_near_misses=bool(state.unified.cache_near_misses),
                ),
            )
        else:
            await query.edit_message_text(
                "🔍 <b>Unified скан</b>\n\n"
                "Объединяет три модуля:\n"
                "📊 <b>Swing</b> — 30W EMA, апсайд, R/R\n"
                "⚡ <b>Early Trend</b> — дневной EMA-стек, пробой 200D\n"
                "🌱 <b>Momentum</b> — паттерны 1D+1W, объём\n\n"
                "Акции с 2-3 модулями идут в топ списка.\n"
                "⏱ ~25–30 минут на бесплатном Polygon.",
                parse_mode="HTML",
                reply_markup=build_unified_keyboard(has_cache=False),
            )

    elif data == "unified_run":
        if state.unified.running:
            await query.answer("Скан уже запущен!", show_alert=True)
            return
        await query.edit_message_text(
            "🔍 <b>Unified скан запущен!</b>\n\n"
            "Анализирую топ-300 тикеров из universe по трём модулям.\n"
            "⏱ ~25–30 минут. Напишу когда закончу.",
            parse_mode="HTML", reply_markup=build_cancel_keyboard("unified_cancel"),
        )
        loop = asyncio.get_running_loop()
        threading.Thread(
            target=run_unified_thread,
            args=(query.message.chat_id, context.application, loop, state),
            daemon=True,
        ).start()

    elif data == "unified_cancel":
        state.unified.cancel_event.set()
        await query.edit_message_text(
            "⏹ <b>Останавливаю Unified скан...</b>\n\n"
            "Скан завершится после текущего тикера — "
            "частичные результаты пришлю сюда.",
            parse_mode="HTML", reply_markup=BACK_KEYBOARD,
        )

    elif data == "unified_near_misses":
        near_misses = state.unified.cache_near_misses
        cache_ts    = state.unified.cache_ts
        if cache_ts is None:
            await query.edit_message_text(
                "❌ <b>Нет данных о близких к прохождению</b>\n\nЗапустите Unified скан сначала.",
                parse_mode="HTML", reply_markup=UNIFIED_RESULT_KEYBOARD,
            )
            return
        if not near_misses:
            await query.edit_message_text(
                "📊 <b>Нет акций близких к прохождению</b>\n\n"
                f"Скан от {cache_ts.strftime('%d.%m %H:%M')}\n\n"
                "Все акции либо прошли фильтр (≥60 баллов), либо провалили все три модуля.\n\n"
                "Попробуйте запустить скан снова или пересобрать universe.",
                parse_mode="HTML", reply_markup=UNIFIED_RESULT_KEYBOARD,
            )
            return
        ts_str = cache_ts.strftime("%d.%m %H:%M")
        header = (
            f"📈 <b>ТОП-10 БЛИЗКИХ К ПРОХОЖДЕНИЮ</b> — {ts_str}\n\n"
            "Акции с баллом &lt; 60, но прошедшие хотя бы один модуль.\n"
            "Сортировка по баллу убыванию.\n\n"
        )
        lines = [header]
        for i, nm in enumerate(near_misses, 1):
            try:
                lines.append(format_near_miss(nm, i))
                lines.append("")
            except Exception as e:
                logger.error(f"Error formatting near_miss {i}: {e}")
                continue
        if len(lines) == 1:
            await query.edit_message_text(
                "❌ <b>Ошибка формирования данных</b>\n\nПопробуйте снова.",
                parse_mode="HTML", reply_markup=UNIFIED_RESULT_KEYBOARD,
            )
            return
        await query.message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=UNIFIED_RESULT_KEYBOARD,
        )

    # ── Universe ──────────────────────────────────────────────────────────────
    elif data == "universe":
        stats   = get_universe_stats()
        age_str = f"{stats['age_days']}д" if stats['age_days'] is not None else "—"
        await query.edit_message_text(
            f"📋 <b>Universe тикеров</b>\n\n"
            f"Всего тикеров: <b>{stats['total']}</b>\n"
            f"Добавлено вручную: <b>{stats['manual']}</b>\n"
            f"Обновлён: <b>{stats['updated']}</b> (возраст {age_str})\n\n"
            f"<i>Universe — список акций для Unified скана.\n"
            f"Обновляется автоматически раз в 7 дней из Polygon.\n"
            f"Можно добавлять/удалять тикеры вручную.</i>",
            parse_mode="HTML", reply_markup=UNIVERSE_KEYBOARD,
        )

    elif data == "universe_rebuild":
        await query.edit_message_text(
            "🔄 <b>Пересобираю universe из Polygon...</b>\n\nЭто займёт несколько секунд.",
            parse_mode="HTML",
        )
        loop = asyncio.get_running_loop()
        try:
            tickers = await asyncio.wait_for(
                loop.run_in_executor(None, rebuild_universe), timeout=60,
            )
            await query.edit_message_text(
                f"✅ <b>Universe пересобран</b>\n\n"
                f"Тикеров: <b>{len(tickers)}</b>\n"
                f"Источник: tickers_cache.json (только CS, NYSE/NASDAQ)",
                parse_mode="HTML", reply_markup=UNIVERSE_KEYBOARD,
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ Ошибка: {str(e)[:100]}", parse_mode="HTML", reply_markup=UNIVERSE_KEYBOARD,
            )

    elif data == "universe_add":
        state.unified.pending_action[query.message.chat_id] = "add"
        await query.edit_message_text(
            "➕ <b>Добавить тикеры в universe</b>\n\n"
            "Введите тикер (например: <code>NBIS</code>) "
            "или несколько через пробел: <code>NBIS AHRT GEO</code>\n\n"
            "Напишите /cancel для отмены.",
            parse_mode="HTML",
        )

    elif data == "universe_remove":
        state.unified.pending_action[query.message.chat_id] = "remove"
        await query.edit_message_text(
            "➖ <b>Удалить тикеры из universe</b>\n\n"
            "Введите тикер (например: <code>MPW</code>) "
            "или несколько через пробел: <code>MPW XYZ</code>\n\n"
            "Напишите /cancel для отмены.",
            parse_mode="HTML",
        )

    # ── Superstock ────────────────────────────────────────────────────────────
    elif data == "superstock":
        if state.superstock.running:
            done  = state.superstock.progress["done"]
            total = state.superstock.progress["total"]
            await query.edit_message_text(
                f"⏳ <b>Superstock скан идёт...</b>\nПроверено: {done}/{total}",
                parse_mode="HTML", reply_markup=build_cancel_keyboard("superstock_cancel"),
            )
            return

        hist        = get_history_stats()
        has_history = hist["last_ts"] is not None

        if state.superstock.cache_picks is not None:
            picks = state.superstock.cache_picks
            ts    = state.superstock.cache_ts
            age_h = round((datetime.now() - ts).total_seconds() / 3600, 1)
            await query.edit_message_text(
                f"🚀 <b>Superstock скан</b> от {ts.strftime('%d.%m %H:%M')} (кэш {age_h}ч)\n"
                f"Кандидатов: <b>{len(picks)}</b>",
                parse_mode="HTML",
                reply_markup=build_superstock_keyboard(has_cache=True, has_history=has_history),
            )
        else:
            history_note = (
                f"\n\n📋 Последний скан: <b>{hist['last_ts']}</b> — {hist['last_count']} кандидатов"
                if has_history else ""
            )
            await query.edit_message_text(
                "🚀 <b>Superstock скан</b>\n\n"
                "<b>Обязательные фильтры:</b>\n"
                "✅ Выручка +25%+ YoY | ✅ EPS ускорение 2+ кв.\n"
                "✅ Float &lt; 100M | ✅ Цена > 30W EMA\n\n"
                "⚠️ <i>Скан занимает 25–40 минут.</i>" + history_note,
                parse_mode="HTML",
                reply_markup=build_superstock_keyboard(has_cache=False, has_history=has_history),
            )

    elif data == "superstock_run":
        if state.superstock.running:
            await query.answer("Скан уже запущен!", show_alert=True)
            return
        await query.edit_message_text(
            "🚀 <b>Superstock скан запущен!</b>\n⏱ ~25–40 минут.",
            parse_mode="HTML", reply_markup=build_cancel_keyboard("superstock_cancel"),
        )
        loop = asyncio.get_running_loop()
        threading.Thread(
            target=run_superstock_thread,
            args=(query.message.chat_id, context.application, loop, state),
            daemon=True,
        ).start()

    elif data == "superstock_cancel":
        state.superstock.cancel_event.set()
        await query.edit_message_text(
            "⏹ <b>Останавливаю Superstock скан...</b>\n\n"
            "Скан завершится после текущего тикера — "
            "частичные результаты пришлю сюда.",
            parse_mode="HTML", reply_markup=BACK_KEYBOARD,
        )

    elif data == "superstock_history":
        await _handle_superstock_history(query, state)

    # ── ET-only scan ──────────────────────────────────────────────────────────
    elif data == "et_only_scan":
        if state.unified.et_only_running:
            await query.edit_message_text(
                "⏳ <b>ET-only скан уже идёт...</b>\n\nНапишу когда закончу.",
                parse_mode="HTML", reply_markup=build_cancel_keyboard("et_only_cancel"),
            )
            return

        if state.unified.et_only_cache_picks is not None:
            picks = state.unified.et_only_cache_picks
            ts    = state.unified.et_only_cache_ts
            age_h = round((datetime.now() - ts).total_seconds() / 3600, 1)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Запустить новый скан", callback_data="et_only_run")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back")],
            ])
            await query.edit_message_text(
                f"⚡ <b>ET-only скан</b> от {ts.strftime('%d.%m %H:%M')} (кэш {age_h}ч)\n"
                f"Найдено сигналов: <b>{len(picks)}</b>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Запустить скан", callback_data="et_only_run")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back")],
            ])
            await query.edit_message_text(
                "⚡ <b>ET-only скан</b>\n\n"
                "Ищет акции с сильным Early Trend сигналом:\n"
                "• Пробой EMA и закрепление над ней\n"
                "• Тест EMA и отскок\n"
                "• Консолидация перед пробоем\n\n"
                "Swing фильтры <b>не применяются</b> — список для агрессивных входов.\n"
                "⏱ ~25–30 минут.",
                parse_mode="HTML", reply_markup=keyboard,
            )

    elif data == "et_only_run":
        if state.unified.et_only_running:
            await query.answer("Скан уже запущен!", show_alert=True)
            return
        await query.edit_message_text(
            "⚡ <b>ET-only скан запущен!</b>\n\n"
            "Swing фильтры не применяются — список для агрессивных входов.\n"
            "⏱ ~25–30 минут. Напишу когда закончу.",
            parse_mode="HTML", reply_markup=build_cancel_keyboard("et_only_cancel"),
        )
        loop = asyncio.get_running_loop()
        threading.Thread(
            target=run_et_only_thread,
            args=(query.message.chat_id, context.application, loop, state),
            daemon=True,
        ).start()

    elif data == "et_only_cancel":
        state.unified.et_only_cancel_event.set()
        await query.edit_message_text(
            "⏹ <b>Останавливаю ET-only скан...</b>\n\n"
            "Скан завершится после текущего тикера — "
            "частичные результаты пришлю сюда.",
            parse_mode="HTML", reply_markup=BACK_KEYBOARD,
        )

    # ── Evaluate signals ──────────────────────────────────────────────────────
    elif data == "evaluate_signals":
        if state.evaluation.running:
            await query.answer("Оценка уже идёт — результат придёт сообщением.", show_alert=True)
            return
        await query.edit_message_text(
            "📈 <b>Оценка сигналов запущена</b>\n\n"
            "• Оцениваются только снапшоты старше 30 дней\n"
            "• Free tier Polygon: пауза 13с между запросами — может занять "
            "десятки минут, прогресс буду присылать\n\n"
            "⏳ Результат отправлю сюда.",
            parse_mode="HTML", reply_markup=BACK_KEYBOARD,
        )
        loop = asyncio.get_running_loop()
        threading.Thread(
            target=run_evaluation_thread,
            args=(query.message.chat_id, context.application, loop, state),
            daemon=True,
            name="signal_evaluation",
        ).start()

    # ── Help ──────────────────────────────────────────────────────────────────
    elif data == "help":
        await query.edit_message_text(
            "ℹ️ <b>Как работает бот</b>\n\n"
            "<b>🔍 Unified скан</b> — три модуля в одном:\n"
            "  • Swing: 30W EMA, апсайд ≥25%, R/R ≥1.5\n"
            "  • Early Trend: дневной EMA-стек, пробой 200D\n"
            "  • Momentum: паттерны 1D+1W, объём vs SMA10\n"
            "  Акции с 2-3 модулями идут в топ. Порог ≥ 60/100.\n\n"
            "<b>🚀 Superstock скан</b> — фундаментал:\n"
            "  Выручка +25% YoY + EPS ускорение + float &lt;100M\n\n"
            "<b>📋 Universe</b> — список акций для скана.\n"
            "  Можно добавлять/удалять тикеры вручную.\n\n"
            "<b>💼 Портфель B</b> — ребалансировка + yield мониторинг\n\n"
            "📡 Данные: Polygon.io + yfinance\n"
            "⚠️ <i>Не является инвестиционным советом. DYOR.</i>",
            parse_mode="HTML", reply_markup=BACK_KEYBOARD,
        )

    # ── Back ──────────────────────────────────────────────────────────────────
    elif data == "back":
        await query.edit_message_text(
            "👋 <b>NYSE/NASDAQ Trading Bot</b>\n\nВыбери действие:",
            parse_mode="HTML", reply_markup=MAIN_KEYBOARD,
        )


# ── Superstock history helper ─────────────────────────────────────────────────

async def _handle_superstock_history(query, state: AppState) -> None:
    STATUS_ICON  = {"active": "🟢", "expired": "🔴", "hot": "🟠", "target": "✅", "unknown": "⚪"}
    STATUS_LABEL = {
        "active": "в силе", "expired": "упал под EMA",
        "hot": "перегрет >35%", "target": "достиг таргета", "unknown": "нет данных",
    }
    STATUS_ORDER = {"active": 0, "target": 1, "hot": 2, "expired": 3, "unknown": 4}

    await query.edit_message_text("⏳ <b>Обновляю цены кандидатов...</b>", parse_mode="HTML")

    loop = asyncio.get_running_loop()
    try:
        snapshot = await asyncio.wait_for(
            loop.run_in_executor(None, load_latest), timeout=10,
        )
    except Exception:
        snapshot = None

    if not snapshot or not snapshot.get("picks"):
        await query.edit_message_text(
            "❌ <b>История пуста</b>\n\nЗапустите Superstock скан сначала.",
            parse_mode="HTML",
            reply_markup=build_superstock_keyboard(has_cache=False, has_history=False),
        )
        return

    try:
        refreshed = await asyncio.wait_for(
            loop.run_in_executor(None, refresh_picks, snapshot["picks"]), timeout=60,
        )
    except asyncio.TimeoutError:
        refreshed = [
            {**p, "current_price": None, "price_change_pct": None,
             "status": "unknown", "refreshed_at": "—"}
            for p in snapshot["picks"]
        ]

    ts_str    = snapshot["timestamp"][:16].replace("T", " ")
    active_n  = sum(1 for p in refreshed if p.get("status") == "active")
    expired_n = sum(1 for p in refreshed if p.get("status") == "expired")
    hot_n     = sum(1 for p in refreshed if p.get("status") == "hot")
    target_n  = sum(1 for p in refreshed if p.get("status") == "target")
    partial_note = (
        "\n⏹ <i>Скан был прерван вручную — кандидаты частичные.</i>"
        if snapshot.get("partial") else ""
    )

    await query.edit_message_text(
        f"📋 <b>КАНДИДАТЫ SUPERSTOCK</b> — скан от {ts_str}\n\n"
        f"🟢 Актуальны: <b>{active_n}</b>  "
        f"🔴 Упали: <b>{expired_n}</b>  "
        f"🟠 Перегреты: <b>{hot_n}</b>  "
        f"✅ Таргет: <b>{target_n}</b>\n\n"
        f"<i>Цены обновлены (15мин задержка yfinance)</i>"
        + partial_note,
        parse_mode="HTML",
    )

    sorted_picks = sorted(refreshed, key=lambda p: STATUS_ORDER.get(p.get("status", "unknown"), 4))
    keyboard     = build_superstock_keyboard(
        has_cache=state.superstock.cache_picks is not None,
        has_history=True,
    )

    CHUNK = 3
    for idx in range(0, len(sorted_picks), CHUNK):
        chunk = sorted_picks[idx:idx + CHUNK]
        lines = []
        for j, p in enumerate(chunk):
            status      = p.get("status", "unknown")
            icon        = STATUS_ICON.get(status, "⚪")
            label       = STATUS_LABEL.get(status, "")
            cur_price   = p.get("current_price")
            entry_price = float(p.get("price", 0))
            chg         = p.get("price_change_pct")

            base       = format_superstock_pick(p, idx + j + 1)
            base_lines = base.split("\n")
            if cur_price:
                chg_str    = f" ({chg:+.1f}%)" if chg is not None else ""
                price_line = (
                    f"   {icon} Сейчас: <b>${cur_price}</b>{chg_str} "
                    f"[{label}] | Вход был: ${entry_price}"
                )
            else:
                price_line = f"   {icon} Вход был: ${entry_price} | Цена недоступна [{label}]"
            base_lines.insert(1, price_line)
            lines.append("\n".join(base_lines))
            lines.append("")

        is_last = (idx + CHUNK >= len(sorted_picks))
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=keyboard if is_last else None,
        )
        await asyncio.sleep(0.3)


# ── Universe text input handlers ──────────────────────────────────────────────

async def handle_universe_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовый ввод тикеров для add/remove universe.
    Поддерживает несколько тикеров через пробел или запятую."""
    chat_id = update.message.chat_id
    state: AppState = context.application.bot_data["state"]
    action = state.unified.pending_action.get(chat_id)

    if not action:
        return

    raw_text = update.message.text.strip().upper()
    tickers  = [t for t in raw_text.replace(",", " ").split() if t]

    if not tickers:
        return

    results = []
    for ticker in tickers:
        if action == "add":
            ok, msg = add_ticker(ticker)
        elif action == "remove":
            ok, msg = remove_ticker(ticker)
        else:
            return
        results.append(msg)

    state.unified.pending_action.pop(chat_id, None)
    await update.message.reply_text("\n".join(results), parse_mode="HTML", reply_markup=UNIVERSE_KEYBOARD)


async def universe_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    state: AppState = context.application.bot_data["state"]
    state.unified.pending_action.pop(chat_id, None)
    await update.message.reply_text("❌ Отменено.", reply_markup=UNIVERSE_KEYBOARD)
