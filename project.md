NYSE/NASDAQ Trading Bot — Project Documentation

Overview

Telegram-бот для сканирования акций NYSE/NASDAQ, анализа технических и фундаментальных сигналов, формирования торговых сетапов и управления «Портфелем B». Использует данные Polygon.io (основной) и yfinance (цены, консенсус, фундаментал). Развёрнут на Linux-сервере (Германия), управляется через Telegram.

Architecture

Data Layer

- Источник данных: Polygon.io (основной), yfinance (цены, консенсус аналитиков, фундаментал)
- Дисковый кэш баров: bars_cache/ — дневные и недельные бары, инкрементальное обновление
- Кэш тикеров: tickers_cache.json (TTL 7 дней), tickers_universe.json (TTL 7 дней)
- Pre-filter кэш: prefilter_cache.json (TTL 4 часа)
- Обработка rate-limit: фиксированная пауза 15с на 429, retry x3
- Нормализация баров, EMA/SMA, недельные и дневные агрегаты

Scanning Engine

Unified Scanner (основной):
- Три модуля: Swing (0-40 баллов) + Momentum (0-30) + Early Trend (0-30)
- Combo бонус: x2 модуля +20, x3 модуля +40
- Нормализация: raw/140 × 100, порог ≥ 60/100
- Near-misses: топ-10 кандидатов с баллом < 60
- Сохранение снапшотов → analytics_history/
- Pre-filter: топ-300 по dollar volume из universe

Superstock Scanner:
- Фундаментальный скоринг: выручка +25% YoY, EPS ускорение 2+ кв., float < 100M
- Скоринг 0-100 по 6 категориям: фундаментал, оценка, структура, качество, техника, катализатор
- История кандидатов: superstock_history/ с обновлением цен через yfinance
- Статусы кандидатов: active / expired / hot / target

Signal Evaluator:
- Оценка качества сигналов по снапшотам analytics_history/
- P&L на горизонтах +5/+10/+20 торговых дней через Polygon
- Бенчмарк: QQQ за те же окна → α (сигнал − рынок) и доля обогнавших рынок
- Минимальный возраст снапшота для оценки: 30 дней
- Дедуп: сигнал по тому же тикеру ближе 7 дней = тот же сетап (не раздувает выборку)
- Пропущенные «свежие» снапшоты и пустые сканы (0 сигналов) считаются
  отдельными строками отчёта, не как «обработанные»
- Пустые снапшоты помечаются skipped_empty и не пересматриваются
- Пауза 13с между запросами Polygon (free tier 5 req/min), retries=2
- Автозапуск: Пн–Пт 20:00 CET, в фоне, без таймаута; при нечего оценивать
  показывает накопленную статистику по всем *_evaluated.json
- Сохранение результатов: *_evaluated.json (включая benchmark для α)

Portfolio B Module:
- Загрузка portfolio_b.json
- Расчёт взвешенного yield с live-данными yfinance
- Выявление отклонений от целевых долей (tolerance настраиваемый)
- Рекомендации по докупке/продаже
- Подбор альтернатив по типу инструмента (BDC, CEF, HY_BOND, REIT, DIVIDEND)
- Экран согласования состава портфеля

Telegram Interface:
- python-telegram-bot, AsyncIO
- Inline-кнопки, меню, chunk-разбиение сообщений
- Разбитые handlers: base.py / scans.py / portfolio.py / conversations.py
- Фоновые потоки сканов: scan_threads.py (+ run_evaluation_thread)
- APScheduler: unified скан Пн–Пт 13:00 CET, оценка сигналов Пн–Пт 20:00 CET

Infrastructure:
- Linux сервер (host: crypto-bot), Германия; путь /opt/stockbot
- systemd unit: trading_bot.service (ключи только через Environment/EnvironmentFile)
- Логирование: bot.log, RotatingFileHandler 5МБ × 3, уровень INFO
- Зависимости: см. requirements.txt (requests, pandas, numpy, python-telegram-bot, yfinance, APScheduler)
- Версионирование: git (локальный репозиторий)

Project Structure

/opt/stockbot/
├── main.py                    # Точка входа, post_init планировщика
├── core/
│   ├── config.py              # Все константы
│   ├── state.py               # AppState, ScanState, ScanCache и др.
│   ├── utils.py               # calc_ema, calc_rr_ratio, calc_trend_structure и др.
│   ├── logging_setup.py       # FileHandler + StreamHandler, защита от дублирования
│   └── scheduler.py           # APScheduler, автозапуск unified скана
├── data_provider/
│   ├── polygon_client.py      # Все запросы к Polygon API
│   ├── yfinance_client.py     # Цена, консенсус, дивиденды, фундаментал
│   ├── cache.py               # Справочник тикеров
│   ├── bars.py                # Загрузка баров с инкрементальным кэшем
│   └── bars_cache.py          # Дисковый кэш баров (bars_cache/)
├── scanners/
│   ├── filters.py             # EMA, price, volume, exclusion фильтры
│   ├── patterns.py            # Cup&Handle, Flag, Double Bottom, Range Breakout, Volume Spike
│   ├── early_trend.py         # EMA-стек 10/30/50/200, Golden Cross, Pullback, пробой EMA200
│   ├── unified_scanner.py     # Основной скан: Swing + Momentum + ET (+ ET-only режим)
│   └── superstock_scanner.py  # Фундаментал + EMA + float
├── analytics/
│   ├── signal_evaluator.py    # Оценка сигналов по снапшотам
│   └── superstock_history.py  # История кандидатов superstock
├── portfolio/
│   ├── portfolio_io.py        # load/save/replace/approve
│   ├── portfolio_analysis.py  # Расчёт ребалансировки
│   ├── portfolio_manager.py   # Бизнес-логика замены тикеров
│   └── universe.py            # Universe тикеров для unified скана
├── telegram_ui/
│   ├── keyboards.py           # Все InlineKeyboardMarkup
│   ├── formatting.py          # Все форматтеры сообщений
│   ├── scan_threads.py        # Фоновые потоки сканов
│   └── handlers/
│       ├── __init__.py        # register_handlers()
│       ├── base.py            # start, stop, error_handler
│       ├── scans.py           # Кнопки сканов + superstock history
│       ├── portfolio.py       # Все pb_* кнопки
│       └── conversations.py   # ConversationHandler (ввод портфеля, поиск тикера)
├── analytics_history/         # Снапшоты unified скана + evaluated версии
├── superstock_history/        # Снапшоты superstock кандидатов
├── bars_cache/                # Дисковый кэш баров
└── bot.log                    # Лог бота

Signal Lifecycle

1. Ticker Universe → grouped daily → фильтрация → топ-300 по dollar volume
2. Bar Processing → дневные/недельные бары → EMA/SMA → проверка истории
3. Signal Generation → Swing + Momentum + ET → combo scoring → нормализация
4. Ranking → сортировка по n_modules DESC, score DESC
5. Output → Telegram chunks + сохранение снапшота

Risk Management

- Ограничение universe по ликвидности (dollar volume)
- Исключение перегретых инструментов (зона D > 35% над EMA)
- Проверка float/cap в superstock
- Проверка объёмов (drying, up_weeks)
- Защитные проверки данных (минимум баров, цена > MIN_PRICE)
- Стоп-уровень и R/R расчёт для каждого сигнала

Known Limitations

- Нет реального торгового движка
- Unit-тесты: базовые (evaluator, portfolio_io, smoke-импорты); покрытие неполное
- Бесплатный Polygon (5 req/min): скан 30–90+ минут, оценка сигналов — десятки минут
- consensus_tp зависит от yfinance (15-минутная задержка данных)
- Near-misses ограничены топ-10

Future Improvements

- Watchlist / алерты: уведомление при входе тикера в зону A или пробое EMA
  (после накопления статистики оценённых сигналов)
- Расширение unit-тестов (цель ≥ 60% по scanners и data_provider)
- Penalty-система на основе evaluated снапшотов
- Весовые коэффициенты в Unified Score на основе backtesting
- Backtesting engine
- Real-trading engine

✅ Реализованные улучшения (хронология):

- Динамический апсайд (ATR-based) в Swing модуле
- EMA скоринг (4-уровневая система) вместо бинарной зоны
- Top-10 near-misses для диагностики фильтров
- Unified Scanner: три модуля + combo scoring
- Дисковый кэш баров с инкрементальным обновлением
- Signal Evaluator: оценка P&L по снапшотам
- Superstock History: персистентность кандидатов с обновлением цен
- APScheduler: автозапуск unified скана Пн-Пт 13:00 CET
- Разбиение handlers.py на модули
- Установка yfinance: consensus_tp теперь работает
- Фикс HTTP 400: защита от start > end при запросе баров
- Логирование в файл bot.log

✅ Сентябрь 2026 (рефакторинг и стабилизация):

- ET-only режим (кнопка + скан top-10 Early Trend, порог 8)
- Динамический порог: при below_ema > 40% порог 60 → 52 (−8)
- Суперсток: save_snapshot подключён к скану — «Кандидаты прошлого скана» работает
- Оценка сигналов: честный отчёт (свежие/пустые снапшоты отдельно), фоновый
  запуск без таймаута, автозапуск Пн–Пт 20:00 CET, pacing 13с + retries=2,
  накопленная статистика при повторных запросах
- Портфель: блокирующие yfinance-вызовы вынесены из event loop (бот больше
  не виснет при вводе тикера), таймаут ребаланса обрабатывается, атомарная
  запись portfolio_b.json (+ .bak), pb_edit сохраняет одобрения на диск
- Логирование: INFO + ротация 5МБ×3 (было DEBUG без ротации — 9МБ/день и
  утечка токена в лог)
- Секреты убраны из кода — только переменные окружения (старые токены
  скомпрометированы, ротировать!)
- git-репозиторий, requirements.txt, README, структура пакетов восстановлена

✅ Сентябрь 2026 (аудит report4):

- Growth-сканер удалён как мёртвый код (недостижим из меню, дублировал
  Momentum-модуль unified). Общие компоненты Momentum (patterns.py,
  GROWTH_*-константы, get_growth_weekly_bars_df) сохранены
- Отмена сканов: cancel_event протянут через unified / et_only / superstock
  сканы (проверка между тикерами до Polygon-вызовов, прерываемые паузы);
  кнопка «❌ Отменить скан» в UI и /stop реально останавливают сканы,
  частичные результаты доставляются с пометкой
- Unit-тесты: cancel_event (раннее прерывание), чистые функции
  filters / patterns / early_trend
