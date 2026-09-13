"""
Smoke-тест: все модули проекта импортируются (ловит битые импорты
и рассинхронизацию пакетной структуры ДО деплоя на сервер).
Запуск:  python -m pytest tests/ -v
или без pytest:  python tests/test_smoke_imports.py
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODULES = [
    "core.config",
    "core.logging_setup",
    "core.state",
    "core.scheduler",
    "core.utils",
    "data_provider.polygon_client",
    "data_provider.yfinance_client",
    "data_provider.cache",
    "data_provider.bars",
    "data_provider.bars_cache",
    "scanners.filters",
    "scanners.patterns",
    "scanners.early_trend",
    "scanners.unified_scanner",
    "scanners.superstock_scanner",
    "analytics.signal_evaluator",
    "analytics.superstock_history",
    "portfolio.portfolio_io",
    "portfolio.portfolio_manager",
    "portfolio.portfolio_analysis",
    "portfolio.universe",
    "telegram_ui.keyboards",
    "telegram_ui.formatting",
    "telegram_ui.scan_threads",
    "telegram_ui.handlers",
    "telegram_ui.handlers.base",
    "telegram_ui.handlers.scans",
    "telegram_ui.handlers.portfolio",
    "telegram_ui.handlers.conversations",
    # мёртвый handlers.py и base_scanner.py удалены — и не должны импортироваться
]


def test_all_modules_import():
    errors = []
    for mod in MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:
            errors.append(f"{mod}: {type(e).__name__}: {e}")
    assert not errors, "Не импортируются:\n" + "\n".join(errors)


def test_dead_modules_gone():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(root, "handlers.py"))
    assert not os.path.exists(os.path.join(root, "base_scanner.py"))


def test_config_has_no_hardcoded_secrets():
    from core import config
    # ключи должны приходить только из окружения
    import os as _os
    if not _os.getenv("POLYGON_API_KEY"):
        assert config.POLYGON_API_KEY == ""
    if not _os.getenv("TELEGRAM_BOT_TOKEN"):
        assert config.TELEGRAM_TOKEN == ""


if __name__ == "__main__":
    failed = 0
    for fn in [test_all_modules_import, test_dead_modules_gone, test_config_has_no_hardcoded_secrets]:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
