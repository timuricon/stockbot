import threading
from datetime import datetime
from typing import Optional


class ScanState:
    """Состояние базового скана."""

    def __init__(self):
        self.running: bool = False
        self.cancel_event: threading.Event = threading.Event()
        self.progress: dict = {"done": 0, "total": 0}
        self.interim_passed: list = []
        self.interim_failed: list = []

    def reset(self):
        self.cancel_event.clear()
        self.interim_passed.clear()
        self.interim_failed.clear()
        self.progress["done"] = 0
        self.progress["total"] = 0

    def cancel(self):
        self.cancel_event.set()


class ScanCache:
    """Кэш результатов одного скана."""

    def __init__(self, ttl_minutes: int = 60):
        self.picks: Optional[list] = None
        self.rejects: Optional[list] = None
        self.ts: Optional[datetime] = None
        self.ttl_minutes = ttl_minutes

    def is_valid(self) -> bool:
        if self.ts is None or self.picks is None:
            return False
        age = (datetime.now() - self.ts).total_seconds() / 60
        return age < self.ttl_minutes

    def store(self, picks: list, rejects: list = None):
        self.picks = picks
        self.rejects = rejects
        self.ts = datetime.now()

    def clear(self):
        self.picks = None
        self.rejects = None
        self.ts = None


class SuperstockState:
    """Состояние и кэш superstock скана."""

    def __init__(self):
        self.running: bool = False
        self.progress: dict = {"done": 0, "total": 0}
        self.cache_picks: Optional[list] = None
        self.cache_ts: Optional[datetime] = None
        # Сигнал отмены: ставится кнопкой «Отменить скан» или /stop
        self.cancel_event: threading.Event = threading.Event()


class UnifiedState:
    """Состояние и кэш unified скана."""

    def __init__(self):
        self.running: bool = False
        self.cache_picks: Optional[list] = None
        self.cache_near_misses: Optional[list] = None
        self.cache_ts: Optional[datetime] = None
        # Сигнал отмены unified скана (кнопка «Отменить скан» / /stop)
        self.cancel_event: threading.Event = threading.Event()
        # ET-only скан — отдельный кэш и отдельный сигнал отмены
        self.et_only_running: bool = False
        self.et_only_cache_picks: Optional[list] = None
        self.et_only_cache_ts: Optional[datetime] = None
        self.et_only_cancel_event: threading.Event = threading.Event()
        # для ConversationHandler universe add/remove
        self.pending_action: dict = {}  # {chat_id: "add" | "remove"}

class PortfolioSession:
    """Сессионные данные для диалога ввода портфеля."""

    def __init__(self):
        # {chat_id: {"step": int, "values": [], "data": dict, "holdings": []}}
        self.pb_session: dict = {}
        # {chat_id: {"old_ticker": str, "htype": str, "mode": str, "found": dict}}
        self.replace_session: dict = {}
        # последний загруженный портфель
        self.last_portfolio: dict = {}


class EvaluationState:
    """Состояние фоновой оценки сигналов (может идти десятки минут на free tier)."""

    def __init__(self):
        self.running: bool = False


class AppState:
    """Единый контейнер всех состояний приложения."""

    def __init__(self):
        self.base_scan      = ScanState()
        self.base_cache     = ScanCache(ttl_minutes=60)
        self.superstock     = SuperstockState()
        self.unified        = UnifiedState()
        self.evaluation     = EvaluationState()
        self.portfolio      = PortfolioSession()
        self.ticker_reference: dict = {}

