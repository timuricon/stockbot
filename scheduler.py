"""
Scheduler — автозапуск unified скана и оценки сигналов по расписанию.

Расписание (Europe/Berlin):
- Unified скан: каждый будний день в 13:00 (за ~2.5ч до открытия NYSE).
- Оценка сигналов: каждый будний день в 20:00.

Результаты отправляются OWNER_ID и сохраняются в кэш как при ручном запуске.

Использует APScheduler AsyncIOScheduler — работает в том же event loop
что и python-telegram-bot, без дополнительных потоков.
"""

import asyncio
import logging
import threading
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.config import OWNER_ID
from core.logging_setup import logger

# Избегаем verbose логов от apscheduler
logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def _run_scheduled_scan(app, state) -> None:
    """
    Корутина, запускаемая планировщиком.
    Запускает unified скан в отдельном потоке (как при ручном запуске),
    отправляет результаты OWNER_ID.
    """
    from telegram_ui.handlers import run_unified_thread

    if state.unified.running:
        logger.info("[Scheduler] Скан уже запущен — пропускаю.")
        await app.bot.send_message(
            chat_id=OWNER_ID,
            text="⏩ <b>Автоскан пропущен</b> — скан уже идёт.",
            parse_mode="HTML",
        )
        return

    logger.info("[Scheduler] Запускаю автоматический unified скан...")
    await app.bot.send_message(
        chat_id=OWNER_ID,
        text=(
            "🕐 <b>Автоматический Unified скан запущен</b>\n\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')} CET\n"
            "⏱ ~25–30 минут. Результаты отправлю сюда."
        ),
        parse_mode="HTML",
    )

    loop = asyncio.get_running_loop()
    threading.Thread(
        target=run_unified_thread,
        args=(OWNER_ID, app, loop, state),
        daemon=True,
        name="scheduled_unified_scan",
    ).start()


async def _run_scheduled_evaluation(app, state) -> None:
    """
    Ежедневная оценка сигналов в фоне. Молчит, если оценивать нечего.
    Пропускается, если ещё идёт unified скан (общая квота Polygon 5 req/min).
    """
    from telegram_ui.scan_threads import run_evaluation_thread

    if state.unified.running:
        logger.info("[Scheduler] Оценка пропущена — идёт unified скан.")
        return
    if state.evaluation.running:
        logger.info("[Scheduler] Оценка уже идёт — пропускаю.")
        return

    logger.info("[Scheduler] Запускаю автоматическую оценку сигналов...")
    loop = asyncio.get_running_loop()
    threading.Thread(
        target=run_evaluation_thread,
        args=(OWNER_ID, app, loop, state),
        kwargs={"notify_noop": False},
        daemon=True,
        name="scheduled_evaluation",
    ).start()


def setup_scheduler(app, state) -> AsyncIOScheduler:
    """
    Создаёт и возвращает настроенный планировщик.
    Вызывается из main.py после инициализации app и state.

    Расписание:
    - Unified скан: Пн–Пт в 13:00 CET (Europe/Berlin).
    - Оценка сигналов: Пн–Пт в 20:00 CET — у старых снапшотов уже есть
      30 дней фактов, скан к этому времени обычно завершён.
    """
    scheduler = AsyncIOScheduler(timezone="Europe/Berlin")

    scheduler.add_job(
        _run_scheduled_scan,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=13,
            minute=0,
            timezone="Europe/Berlin",
        ),
        args=[app, state],
        id="unified_scan_daily",
        name="Unified Scan — автозапуск",
        replace_existing=True,
        misfire_grace_time=300,  # если бот был офлайн — запустить в течение 5 минут
    )

    scheduler.add_job(
        _run_scheduled_evaluation,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=20,
            minute=0,
            timezone="Europe/Berlin",
        ),
        args=[app, state],
        id="evaluation_daily",
        name="Signal Evaluation — автозапуск",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    logger.info(
        "[Scheduler] Настроено: unified скан Пн–Пт 13:00 CET, "
        "оценка сигналов Пн–Пт 20:00 CET"
    )
    return scheduler
