# Отчёт GLM: ТЗ из report4 выполнено (report5 → реализация)

Выполнил оба ТЗ по согласованному плану (report5.md, с твоими уточнениями).
Все правки — в плоской папке `общая`. Верификация: py_compile всех файлов +
полный pytest (43/43) во временном пакетном дереве, собранном из изменённых
файлов корневого репо (корневой репозиторий не тронут).

## ТЗ 1 — growth-сканер удалён (вариант Б)

Удалено: `scanners/growth_scanner.py` (файл), `run_growth_thread` + импорты
(`scan_threads.py`), `GrowthState` + `state.growth` (`state.py`),
`format_growth_pick` (`formatting.py`), `load/save_growth_ticker_cache`
(`cache.py`), `GROWTH_TOP_N` / `GROWTH_DAILY_BARS` / `GROWTH_SECTORS` /
`GROWTH_CACHE_FILE` / `GROWTH_CACHE_TTL_H` (`config.py`),
`"scanners.growth_scanner"` из smoke-теста, growth-упоминания из docs
(project.md, схема_бота.md, roadmap.md, README.md).

Твоя проверка подтверждена grep-ом и чтением кода — сохранил строго больше,
чем просили:
- `get_growth_weekly_bars_df` (bars.py) — НЕ тронута, теперь принимает
  cancel_event; единственный потребитель — `_analyze_momentum` (unified).
- `GROWTH_WEEKLY_BARS` — осталась (bars.py).
- `GROWTH_VOL_PERIOD` и `GROWTH_PATTERN_SCORES` — остались: их импортирует
  `scanners/patterns.py` (calc_volume_growth / score_growth_signal), а его —
  unified_momentum. В твоём ТЗ они фигурировали в списке на удаление —
  не удалил сознательно.
- `GROWTH_MIN_VOL_RATIO` — осталась: импортируется прямо в
  `unified_scanner._analyze_momentum`.
- Бонус-находка: `GROWTH_DAILY_BARS` был импортирован в bars.py, но нигде
  не использовался (реально используется локальная DAILY_BARS_FULL=252) —
  удалил константу вместе с мёртвым импортом.

## ТЗ 2 — cancel_event доведён до конца

- `state.py`: `cancel_event` в `UnifiedState` + отдельный `et_only_cancel_event`
  + `cancel_event` в `SuperstockState`.
- `run_unified_scan(reference, cancel_event=None)`, `run_et_only_scan(...)` —
  проверка `is_set()` в начале каждой итерации ДО Polygon-вызовов,
  лог `[Unified|ET-only] Cancelled at i/total`;
  `run_superstock_scan(...)` — то же, плюс cancel_event уходит в
  `get_grouped_daily` и `analyze_superstock_ticker`.
- Паузы прерываемые: `_interruptible_pause` (cancel_event.wait вместо
  time.sleep) в unified и superstock — отмена срабатывает мгновенно и во время
  13-секундной паузы.
- Проброс через data_provider: `get_daily_bars_df`, `get_growth_weekly_bars_df`,
  `get_ticker_weekly_bars` теперь принимают cancel_event и доводят его до
  polygon_get (429-пауза тоже прерывается). `_analyze_swing`/`_analyze_momentum`
  пробрасывают.
- `scan_threads.py`: clear() перед стартом, передача event, при отмене частичные
  результаты доставляются с пометкой «⏹ Скан прерван вручную».
- `base.py /stop`: ставит все три cancel_event, running-проверка учитывает
  et_only_running, текст безоговорочно честный.
- Кнопка «❌ Отменить скан»: `build_cancel_keyboard(callback_data)` в
  keyboards.py; running-сообщения и сообщения запуска unified/superstock/et_only
  показывают её; обработчики `unified_cancel` / `superstock_cancel` /
  `et_only_cancel` в scans.py. (Кнопку пользователь подтвердил отдельно.)

## Тесты

- `tests/test_cancel_event.py` (новый, 7 тестов): отмена после 2-го тикера →
  `len(results) == 2 < 5` для unified / et_only / superstock; отмена до старта →
  0 итераций; cancel_event=None → обратная совместимость; обе паузы реально
  прерываемые (30с → <5с).
- `tests/test_filters_patterns_early_trend.py` (новый, 16 тестов): чистые
  функции filters (все passes_*), patterns (calc_volume_growth, calc_atr,
  range_breakout вкл. ATR-«догонялку», volume_spike, скоринг/грейды, гарды),
  early_trend (RSI, contraction, golden_cross-гард, полный анализ: полный стек
  на синтетическом росте / отказ на падающем ряде).
- Итог: **43/43 passed** (smoke-импорты, evaluator 10, portfolio_io 4,
  cancel_event 7, filters/patterns/early_trend 16).
- Нюанс: `detect_volume_contraction` возвращает np.bool_, а не bool — в тесте
  сравнение истинностное, не `is True`.

## ⚠️ Обязательная правка при деплое (файла нет в общей)

`telegram_ui/handlers/__init__.py` (в корневом репо) реэкспортирует
`run_growth_thread` из scan_threads — после удаления будет ImportError на
сервере. Нужно убрать строку `    run_growth_thread,` из блока

```python
from telegram_ui.scan_threads import (  # noqa: F401
    run_unified_thread,
    run_superstock_thread,
    run_growth_thread,   # ← УДАЛИТЬ
)
```

В плоской копии __init__.py не хранится, поэтому правку делал только в
временном тестовом дереве (там smoke-тест прошёл).

**Статус деплоя (12.09):** все файлы задеплоены, правка __init__.py на
сервере выполнена (sed по строке run_growth_thread), бот стартовал чисто
в 13:26 и 13:27 CET, Application started без traceback. Первая попытка
деплоя в 13:24 падала именно на этом ImportError — подтверждено journalctl.

## Деплой: изменённые файлы (общая → сервер, с сохранением путей)

| Файл в общей | Путь на сервере |
|---|---|
| state.py, config.py | core/ |
| cache.py, bars.py, polygon_client.py | data_provider/ |
| unified_scanner.py, superstock_scanner.py; **удалить growth_scanner.py** | scanners/ |
| scan_threads.py, formatting.py, keyboards.py | telegram_ui/ |
| base.py, scans.py | telegram_ui/handlers/ |
| test_smoke_imports.py; **новые** test_cancel_event.py, test_filters_patterns_early_trend.py | tests/ |
| project.md, roadmap.md, схема_бота.md, README.md | корень |
| (опционально) удалить growth_tickers_cache.json | корень |

## Открытые вопросы → закрыты (12.09, подтверждение пользователя)

1. **Ротация ключей — ЗАКРЫТА.** Пользователь подтвердил: ключи ротированы,
   деплой выполняется уже с новыми ключами. Security-пункт первого аудита
   снят.
2. **`archive/bot_v14.py` — УДАЛЁН.** Проверка сервера (условие из
   план_работ): файл на сервере отсутствует, запускаться неоткуда. Локальная
   архивная копия удалена 12.09 (в git не отслеживалась — archive/ вне
   индекса, восстановлению не подлежит; это финальная точка по легаси-монолиту).
3. **15.10 — режим ожидания.** Напоминание не заводилось (решение
   пользователя): сверка с предрегистрацией (n≥30 zone A, итог ≥+1.0%,
   дошедших ≥45%, α d10 ≥+0.5%) — ручная, по накоплении свежих оценок
   (автооценка Пн–Пт 20:00 CET идёт).

По сути все пункты отчёта report4, которые можно было закрыть кодом или
действием, закрыты; до 15.10 проект в режиме накопления статистики.

---

# Доп. ТЗ (твоё замечание от 13.09 по логу 13:57) — ВЫПОЛНЕНО

Подтверждаю наблюдение: отменённый скан 13:57 сохранился как обычный снапшот
и через 30 дней попал бы в оценку как «честный пустой скан». Сделал по твоему
предпочтению — вариант 2 (пометка + пропуск, диагностический след остаётся):

**unified_scanner.py**
- `_save_scan_to_analytics(..., partial=False)`: при прерывании в JSON
  добавляется `"partial": true`, в логе — «(partial — скан прерван вручную)».
- `scan_meta["cancelled"]` — флаг отмены теперь и в meta (и в снапшоте внутри
  scan_meta: видно, на каком пороге/состоянии рынка скан был прерван).

**signal_evaluator.py**
- `run_evaluation`: снапшоты с `partial` ловятся ДО возрастного гейта,
  помечаются twin'ом `skipped_partial: True` (по образцу skipped_empty —
  больше не пересматриваются), считаются в новом счётчике `partial_snapshots`.
  Polygon-запросов по ним ноль.
- `aggregate_evaluated_stats`: partial-twin'ы исключены из накопленной
  статистики.
- Отчёт: новая строка «⏹ Прерванных вручную (пропущено): N» — и в ветке
  «есть статистика», и в «нечего оценивать».

**superstock_history.py / scan_threads.py**
- `save_snapshot(picks, partial=False)` — при отмене пишет `"partial": true`
  в snapshot_*.json и latest.json; поток передаёт `partial=interrupted`.
- UI: в «Кандидаты прошлого скана» при partial-снапшоте выводится
  «⏹ Скан был прерван вручную — кандидаты частичные».

**run_et_only_scan — правка не нужна**: ET-only ничего не пишет на диск
(только in-memory кэш state), загрязнять нечему.

**Тесты**: +`test_partial_snapshot_skipped_and_marked` (evaluator: partial
пропущен/помечен/не ре-оценивается/не в cumulative/строка в отчёте);
отменённый unified-тест теперь проверяет `"partial": true` в снапшоте,
полный — отсутствие пометки. Итог: **44/44 passed** (scratch-дерево, pytest).

## Деплой доп. ТЗ (общая → сервер)

| Файл | Путь |
|---|---|
| unified_scanner.py | scanners/ |
| signal_evaluator.py | analytics/ |
| superstock_history.py | analytics/ |
| scan_threads.py | telegram_ui/ |
| scans.py | telegram_ui/handlers/ |
| test_cancel_event.py, test_signal_evaluator.py | tests/ |

После деплоя: `systemctl restart stockbot`. Старый частичный снапшот
`analytics_history/scan_2026-09-12_13-57.json` (создан до фикса, без поля
partial) проще всего пометить вручную, чтобы он не стал одним «ложным
пустым» через месяц:

```bash
cd /opt/stockbot && python3 -c "import json; p='analytics_history/scan_2026-09-12_13-57.json'; d=json.load(open(p)); d['partial']=True; json.dump(d, open(p,'w'), ensure_ascii=False, indent=4)"
```

(Альтернатива — ничего не делать: через 30 дней он честно уйдёт в
skipped_empty, искажение = 1 пустой снапшот.)
