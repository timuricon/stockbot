import logging
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)
_configured = False


def setup_logging(log_file: str = "bot.log") -> None:
    """
    INFO-уровень + ротация (5 МБ × 3 файла). DEBUG-уровень библиотек (httpx,
    httpcore и т.п.) в лог попадать не должен: в DEBUG httpx пишет полные URL
    getUpdates — вместе с токеном бота.
    """
    global _configured
    if _configured:
        return
    _configured = True

    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
    ]
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=handlers,
        force=True,
    )
    # Шумные HTTP-клиенты (и утечка токена в DEBUG-логах)
    for noisy in ("httpx", "httpcore", "urllib3", "apscheduler", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
