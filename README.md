# claudebot — Telegram-бот: портфель + сканер акций NYSE/NASDAQ

Бот на `python-telegram-bot` (v20+, async) с данными Polygon.io и yfinance.

**Основные функции:**
- **Портфель B** — ведение портфеля доходности (докупки, замены тикеров, одобрение, ребаланс-анализ) — основной сценарий.
- **Unified сканер** — Swing + Momentum + Early Trend сигналы по топ-140 тикерам по объёму (+ ET-only режим), ежедневно Пн–Пт 13:00 CET.
- **Оценка сигналов** — сверка сигналов с фактическими ценами через 5/10/20 дней (накапливается в `analytics_history/`).

## Структура

```
main.py                  — точка входа (запуск: python main.py)
core/                    — config, логирование, состояние, планировщик, utils
data_provider/           — polygon_client, yfinance_client, cache, bars, bars_cache
scanners/                — unified_scanner (+superstock), filters, patterns, early_trend
analytics/               — signal_evaluator (оценка сигналов), superstock_history
portfolio/               — portfolio_io/manager/analysis, universe
telegram_ui/             — keyboards, formatting, scan_threads
telegram_ui/handlers/    — base, scans, portfolio, conversations (__init__ регистрирует хендлеры)
docs/                    — материалы, архив старых заметок
```

Локальная папка зеркалирует сервер `/opt/stockbot` (Linux). Деплой — ручное копирование изменённых файлов с сохранением путей + `restart.sh`.

## Запуск

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows (на Linux: source .venv/bin/activate)
pip install -r requirements.txt

# Обязательные переменные окружения (ключи НЕ хранятся в коде):
set POLYGON_API_KEY=...       # на Linux: export POLYGON_API_KEY=...
set TELEGRAM_BOT_TOKEN=...

python main.py
```

На сервере переменные задаются через systemd-юнит — два способа:

**Способ 1 (рекомендуется): файл окружения `/etc/stockbot.env`**

```bash
# 1. Создать файл с ключами (реальные значения, без кавычек и export):
sudo tee /etc/stockbot.env > /dev/null <<'EOF'
POLYGON_API_KEY=ваш_новый_ключ_polygon
TELEGRAM_BOT_TOKEN=ваш_новый_токен_бота
EOF

# 2. Права: читать может только root
sudo chown root:root /etc/stockbot.env
sudo chmod 600 /etc/stockbot.env

# 3. Подключить файл к юниту (override, исходный юнит не меняется):
sudo systemctl edit trading_bot
#    в открывшемся редакторе добавить секцию:
#    [Service]
#    EnvironmentFile=/etc/stockbot.env

# 4. Применить:
sudo systemctl daemon-reload
sudo systemctl restart trading_bot

# 5. Проверить, что переменные подхватились и бот живой:
sudo systemctl show trading_bot -p EnvironmentFiles
journalctl -u trading_bot -n 20 --no-pager
```

**Способ 2: прямо в юните** — `sudo systemctl edit trading_bot` и добавить
`[Service]` → `Environment="POLYGON_API_KEY=..."` и `Environment="TELEGRAM_BOT_TOKEN=..."`.

⚠️ Порядок при миграции: сначала создать env-файл с **новыми** (ротированными)
ключами, потом деплоить новый `core/config.py` — иначе бот не стартует
(`ValueError: Установи переменную окружения TELEGRAM_BOT_TOKEN`).

## Тесты

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
# или без pytest:
python tests/test_signal_evaluator.py
python tests/test_portfolio_io.py
python tests/test_smoke_imports.py
```

Запускать перед каждым деплоем: smoke-тест ловит битые импорты,
тесты evaluator — регресс главной починенной фичи.

## Документация

- [project.md](project.md) — описание проекта и логики сканера
- [схема_бота.md](схема_бота.md) — архитектура и меню
- [roadmap.md](roadmap.md) — планы
- [docs/план_работ_2026-09.md](docs/план_работ_2026-09.md) — план рефакторинга сентябрь 2026

## Известные ограничения

- **Polygon free tier: 5 запросов/мин.** Скан/оценка при 429 спят по 15 c — «зависания» на десятки минут это норма тарифа, а не баг. Полный скан ~140 тикеров занимает заметное время.
- Оценка сигналов имеет смысл только для снапшотов старше 30 дней (нужно время на P&L d5/d10/d20).
- Секреты: только через переменные окружения. Токены из старых логов/коммитов считать скомпрометированными — ротировать.
