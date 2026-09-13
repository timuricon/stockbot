"""
Scan Threads — фоновые потоки для запуска сканов.
Вызываются из handlers и scheduler.
"""

import asyncio
import time
import threading
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

from core.logging_setup import logger
from core.state import AppState
from data_provider.cache import load_ticker_reference
from analytics.signal_evaluator import evaluate_and_format
from analytics.superstock_history import save_snapshot as save_superstock_snapshot
from scanners.superstock_scanner import run_superstock_scan
from scanners.unified_scanner import run_unified_scan
from telegram_ui.keyboards import BACK_KEYBOARD, UNIFIED_RESULT_KEYBOARD, build_superstock_keyboard
from telegram_ui.formatting import (
    format_unified_pick, format_near_miss,
    format_superstock_pick, format_et_only_pick,
)


def run_unified_thread(chat_id: int, app: Application, loop, state: AppState) -> None:
    """Поток unified скана. Запускает скан и отправляет результаты в чат."""
    state.unified.running = True
    state.unified.cancel_event.clear()   # сброс сигнала отмены перед стартом
    state.unified.cache_near_misses = []
    try:
        reference = state.ticker_reference or load_ticker_reference()
        state.ticker_reference = reference

        picks, near_misses, scan_meta = run_unified_scan(
            reference, cancel_event=state.unified.cancel_event
        )
        interrupted = state.unified.cancel_event.is_set()
        state.unified.cache_picks = picks
        state.unified.cache_near_misses = near_misses or []
        state.unified.cache_ts = datetime.now()

        threshold = scan_meta.get("effective_threshold", 60)
        threshold_lowered = scan_meta.get("threshold_lowered", False)
        below_ema_pct = scan_meta.get("below_ema_pct", 0)

        threshold_note = ""
        if threshold_lowered:
            threshold_note = (
                f"\n⚠️ <i>Рынок в коррекции (below_ema {below_ema_pct:.0f}%) — "
                f"порог снижен до {threshold}/100</i>"
            )
        cancel_note = (
            "\n⏹ <i>Скан прерван вручную — показаны частичные результаты.</i>"
            if interrupted else ""
        )

        if not picks:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "❌ <b>Unified скан завершён — сигналов не найдено.</b>\n\n"
                        "Возможные причины:\n"
                        "• Рынок в коррекции\n"
                        f"• Нет акций с баллом ≥ {threshold}\n"
                        "• Universe требует обновления"
                        + threshold_note + cancel_note
                    ),
                    parse_mode="HTML",
                    reply_markup=UNIFIED_RESULT_KEYBOARD,
                ),
                loop,
            ).result(timeout=30)
            return

        combo3 = [p for p in picks if p["n_modules"] == 3]
        combo2 = [p for p in picks if p["n_modules"] == 2]
        single = [p for p in picks if p["n_modules"] == 1]
        ts_str = state.unified.cache_ts.strftime("%d.%m %H:%M")

        header = (
            f"🔍 <b>UNIFIED СКАН</b> — {ts_str}\n\n"
            f"Найдено: <b>{len(picks)}</b> сигналов\n"
            f"⚡ Все 3 модуля: <b>{len(combo3)}</b>\n"
            f"🔥 2 модуля: <b>{len(combo2)}</b>\n"
            f"✅ 1 модуль: <b>{len(single)}</b>\n"
            f"📊 Близких (ниже порога): <b>{len(near_misses)}</b>\n\n"
            f"<i>Swing + Momentum + Early Trend | Порог ≥ {threshold}/100</i>"
            + threshold_note + cancel_note
        )
        asyncio.run_coroutine_threadsafe(
            app.bot.send_message(chat_id=chat_id, text=header, parse_mode="HTML"),
            loop,
        ).result(timeout=30)

        CHUNK = 4
        for idx in range(0, len(picks), CHUNK):
            chunk = picks[idx:idx + CHUNK]
            lines = []
            for j, p in enumerate(chunk):
                lines.append(format_unified_pick(p, idx + j + 1))
                lines.append("")
            is_last = (idx + CHUNK >= len(picks))
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                    reply_markup=UNIFIED_RESULT_KEYBOARD if is_last else None,
                ),
                loop,
            ).result(timeout=30)
            time.sleep(0.3)

    except Exception as e:
        logger.error(f"Unified thread error: {e}", exc_info=True)
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка unified скана: {str(e)[:150]}",
                ),
                loop,
            ).result(timeout=10)
        except Exception:
            pass
    finally:
        state.unified.running = False


def run_superstock_thread(chat_id: int, app: Application, loop, state: AppState) -> None:
    """Поток superstock скана."""
    state.superstock.running = True
    state.superstock.cancel_event.clear()   # сброс сигнала отмены перед стартом
    try:
        reference = state.ticker_reference or load_ticker_reference()
        state.ticker_reference = reference

        picks = run_superstock_scan(
            reference, cancel_event=state.superstock.cancel_event
        )
        interrupted = state.superstock.cancel_event.is_set()
        state.superstock.cache_picks = picks
        state.superstock.cache_ts = datetime.now()
        # Персистентность: без этого кнопка «Кандидаты прошлого скана» всегда пуста.
        # Прерванный скан сохраняется с пометкой partial — это частичные данные.
        save_superstock_snapshot(picks, partial=interrupted)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Главное меню", callback_data="back")]
        ])
        cancel_note = (
            "\n⏹ <i>Скан прерван вручную — показаны частичные результаты.</i>"
            if interrupted else ""
        )

        if not picks:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "❌ <b>Superstock скан завершён — кандидатов не найдено.</b>\n\n"
                        "Жёсткие фильтры не прошёл ни один тикер:\n"
                        "• Выручка +25% YoY\n• Float &lt; 100M\n"
                        "• Цена > 30W EMA\n• EPS ускорение 2+ кв."
                        + cancel_note
                    ),
                    parse_mode="HTML",
                    reply_markup=keyboard,
                ),
                loop,
            ).result(timeout=30)
            return

        top_picks    = [p for p in picks if p["score"] >= 85]
        strong_picks = [p for p in picks if 70 <= p["score"] < 85]
        radar_picks  = [p for p in picks if 55 <= p["score"] < 70]
        ts_str       = state.superstock.cache_ts.strftime("%d.%m %H:%M")

        header = (
            f"🚀 <b>SUPERSTOCK СКАН</b> — {ts_str}\n\n"
            f"Найдено: <b>{len(picks)}</b> | "
            f"🚀 {len(top_picks)} | ⭐ {len(strong_picks)} | 👀 {len(radar_picks)}\n\n"
            "<i>Методология: Stine + O'Neil + Fisher + Lynch</i>"
            + cancel_note
        )
        asyncio.run_coroutine_threadsafe(
            app.bot.send_message(chat_id=chat_id, text=header, parse_mode="HTML"),
            loop,
        ).result(timeout=30)

        main_picks = (top_picks + strong_picks)[:20]
        CHUNK = 3
        for idx in range(0, len(main_picks), CHUNK):
            chunk = main_picks[idx:idx + CHUNK]
            lines = []
            for j, p in enumerate(chunk):
                lines.append(format_superstock_pick(p, idx + j + 1))
                lines.append("")
            is_last = (idx + CHUNK >= len(main_picks)) and not radar_picks
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                    reply_markup=keyboard if is_last else None,
                ),
                loop,
            ).result(timeout=30)
            time.sleep(0.3)

        if radar_picks:
            radar_lines = ["👀 <b>НА РАДАР</b>\n"]
            for p in radar_picks[:10]:
                rev   = p.get("rev_yoy_pct")
                rev_s = f"+{rev:.0f}% YoY" if rev and rev > 0 else "н/д"
                psr   = p.get("psr")
                psr_s = f" | PSR {psr:.1f}" if psr else ""
                radar_lines.append(
                    f"👀 <b>${p['ticker']}</b> | Балл {p['score']}/100 | "
                    f"${p['price']} | {rev_s}{psr_s}"
                )
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(radar_lines),
                    parse_mode="HTML",
                    reply_markup=keyboard,
                ),
                loop,
            ).result(timeout=30)

    except Exception as e:
        logger.error(f"Superstock thread error: {e}", exc_info=True)
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка: {str(e)[:150]}",
                ),
                loop,
            ).result(timeout=10)
        except Exception:
            pass
    finally:
        state.superstock.running = False


def run_evaluation_thread(
    chat_id: int, app: Application, loop, state: AppState,
    notify_noop: bool = True,
) -> None:
    """
    Поток оценки сигналов. Оценка на free tier Polygon идёт долго
    (пауза 13с между запросами), поэтому выполняется в фоне без таймаута.
    notify_noop=False (автозапуск по расписанию) — молчать, если оценивать
    нечего, чтобы не спамить ежедневными пустыми отчётами.
    """
    state.evaluation.running = True
    try:
        last_notified = [0]

        def progress(done: int, total: int) -> None:
            if not total or done - last_notified[0] < 10:
                return
            last_notified[0] = done
            try:
                asyncio.run_coroutine_threadsafe(
                    app.bot.send_message(
                        chat_id=chat_id,
                        text=f"⏳ Оценка сигналов: {done}/{total}...",
                    ),
                    loop,
                ).result(timeout=10)
            except Exception:
                pass

        stats, report = evaluate_and_format(progress_fn=progress)
        if (
            not notify_noop
            and stats["snapshots_processed"] == 0
            and stats["signals_evaluated"] == 0
        ):
            logger.info("[Evaluator] Нечего оценивать — автотчёт не отправляю.")
            return

        asyncio.run_coroutine_threadsafe(
            app.bot.send_message(
                chat_id=chat_id,
                text=report,
                parse_mode="HTML",
                reply_markup=BACK_KEYBOARD,
            ),
            loop,
        ).result(timeout=30)

    except Exception as e:
        logger.error(f"Evaluation thread error: {e}", exc_info=True)
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка оценки сигналов: {str(e)[:150]}",
                ),
                loop,
            ).result(timeout=10)
        except Exception:
            pass
    finally:
        state.evaluation.running = False


def run_et_only_thread(chat_id: int, app: Application, loop, state: AppState) -> None:
    """Поток ET-only скана. Результаты кэшируются в state.unified.et_only_cache_*."""
    state.unified.et_only_running = True
    state.unified.et_only_cancel_event.clear()   # сброс сигнала отмены перед стартом
    try:
        reference = state.ticker_reference or load_ticker_reference()
        state.ticker_reference = reference

        from scanners.unified_scanner import run_et_only_scan
        picks = run_et_only_scan(
            reference, cancel_event=state.unified.et_only_cancel_event
        )
        interrupted = state.unified.et_only_cancel_event.is_set()
        state.unified.et_only_cache_picks = picks
        state.unified.et_only_cache_ts = datetime.now()

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Запустить новый скан", callback_data="et_only_run")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="back")],
        ])
        cancel_note = (
            "\n⏹ <i>Скан прерван вручную — показаны частичные результаты.</i>"
            if interrupted else ""
        )

        if not picks:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "❌ <b>ET-only скан завершён — сигналов не найдено.</b>\n\n"
                        "Нет акций с EMA-стеком и паттернами раннего тренда."
                        + cancel_note
                    ),
                    parse_mode="HTML",
                    reply_markup=keyboard,
                ),
                loop,
            ).result(timeout=30)
            return

        ts_str = state.unified.et_only_cache_ts.strftime("%d.%m %H:%M")
        full_stack_n = sum(1 for p in picks if p.get("full_stack"))
        with_swing_n = sum(1 for p in picks if p.get("swing"))

        header = (
            f"⚡ <b>ET-ONLY СКАН</b> — {ts_str}\n\n"
            f"Найдено: <b>{len(picks)}</b> сигналов\n"
            f"✅ Полный EMA-стек (10>30>50>200): <b>{full_stack_n}</b>\n"
            f"🟢 + Swing подтверждение: <b>{with_swing_n}</b>\n\n"
            f"<i>Список для агрессивных входов — Swing фильтры не применялись.</i>\n"
            f"<i>⚠️ Выше риск — обязательно используй стоп-лосс. DYOR.</i>"
            + cancel_note
        )
        asyncio.run_coroutine_threadsafe(
            app.bot.send_message(chat_id=chat_id, text=header, parse_mode="HTML"),
            loop,
        ).result(timeout=30)

        CHUNK = 4
        for idx in range(0, len(picks), CHUNK):
            chunk = picks[idx:idx + CHUNK]
            lines = []
            for j, p in enumerate(chunk):
                lines.append(format_et_only_pick(p, idx + j + 1))
                lines.append("")
            is_last = (idx + CHUNK >= len(picks))
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                    reply_markup=keyboard if is_last else None,
                ),
                loop,
            ).result(timeout=30)
            time.sleep(0.3)

    except Exception as e:
        logger.error(f"ET-only thread error: {e}", exc_info=True)
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка ET-only скана: {str(e)[:150]}",
                ),
                loop,
            ).result(timeout=10)
        except Exception:
            pass
    finally:
        state.unified.et_only_running = False
