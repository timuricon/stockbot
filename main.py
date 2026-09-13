"""
NYSE/NASDAQ Trading Bot — точка входа.
Инициализирует AppState, регистрирует хендлеры, запускает polling.
"""

import logging

from telegram import Update
from telegram.ext import Application

from core.config import TELEGRAM_TOKEN
from core.logging_setup import setup_logging
from core.state import AppState
from core.scheduler import setup_scheduler
from telegram_ui.handlers import register_handlers


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("Установи переменную окружения TELEGRAM_BOT_TOKEN")

    setup_logging()
    logger = logging.getLogger("trading_bot")
    logger.info("Starting NYSE/NASDAQ Trading Bot...")

    state = AppState()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(60)
        .build()
    )

    register_handlers(app, state)

    # Планировщик стартует внутри event loop через post_init
    scheduler = setup_scheduler(app, state)

    async def post_init(application: Application) -> None:
        scheduler.start()
        logger.info("✅ Планировщик запущен.")

    async def post_shutdown(application: Application) -> None:
        scheduler.shutdown(wait=False)
        logger.info("Планировщик остановлен.")

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    logger.info("✅ Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
