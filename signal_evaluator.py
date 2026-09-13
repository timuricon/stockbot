"""
Signal Evaluator — автоматическая оценка качества сигналов unified scanner.

Логика:
1. Читает снапшоты из analytics_history/*.json
2. Снапшоты младше MIN_SNAPSHOT_AGE_DAYS дней пропускаются — им нужно время
   на фактический P&L (d5/d10/d20). Пропущенные считаются отдельной строкой
   отчёта, а не «обработанными».
3. Для каждого сигнала подтягивает цены через Polygon (+5, +10, +20 торговых
   дней) с паузой PACING_SECONDS между запросами — free tier Polygon
   ограничен 5 запросами/мин, без паузы оценка вешает квоту.
   Для каждой даты сигнала отдельно тянется бенчмарк BENCHMARK_TICKER (QQQ)
   за те же окна → α = сигнал − рынок, доля обогнавших рынок.
4. Пустые снапшоты (top_picks=[] от сканов без сигналов) помечаются
   skipped_empty и больше не пересматриваются — но без запросов к Polygon.
   Частичные снапшоты (partial=true — скан прерван вручную) пропускаются
   и помечаются skipped_partial: диагностический след остаётся, в статистику
   качества сигналов они не попадают.
   Повтор сигнала по тому же тикеру ближе DEDUP_DAYS дней (тот же сетап)
   в статистику попадает один раз.
5. Сохраняет enriched снапшот рядом с оригиналом: *_evaluated.json
6. evaluate_and_format() — (stats, текст) для Telegram: сначала пробует
   новый прогон, если нечего оценивать — показывает накопленную статистику.

Не перезаписывает снапшоты, которые уже оценены.
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from core.logging_setup import logger
from data_provider.polygon_client import get_ticker_daily_bars

ANALYTICS_DIR    = "analytics_history"
EVAL_SUFFIX      = "_evaluated"
EVAL_HORIZONS    = [5, 10, 20]   # торговых дней
MIN_SNAPSHOT_AGE_DAYS = 30       # сигнал должен «отработать»
PACING_SECONDS   = 13           # free tier Polygon: 5 запросов/мин
BENCHMARK_TICKER = "QQQ"         # рынок для сравнения (α = сигнал − рынок)
DEDUP_DAYS       = 7             # тот же тикер ближе N дней = тот же сетап

_last_fetch_ts = 0.0


# ── Загрузка / сохранение ─────────────────────────────────────────────────────

def _list_snapshots() -> list[str]:
    """Возвращает пути к необработанным снапшотам (без _evaluated)."""
    if not os.path.exists(ANALYTICS_DIR):
        return []
    files = []
    for fname in sorted(os.listdir(ANALYTICS_DIR)):
        if not fname.endswith(".json"):
            continue
        if EVAL_SUFFIX in fname:
            continue
        # Пропускаем если уже есть evaluated-версия
        eval_path = _eval_path(os.path.join(ANALYTICS_DIR, fname))
        if os.path.exists(eval_path):
            continue
        files.append(os.path.join(ANALYTICS_DIR, fname))
    return files


def _list_evaluated() -> list[str]:
    """Возвращает пути к уже оценённым снапшотам."""
    if not os.path.exists(ANALYTICS_DIR):
        return []
    return [
        os.path.join(ANALYTICS_DIR, f)
        for f in sorted(os.listdir(ANALYTICS_DIR))
        if f.endswith(".json") and EVAL_SUFFIX in f
    ]


def _eval_path(snapshot_path: str) -> str:
    """scan_2026-05-21_16-45.json → scan_2026-05-21_16-45_evaluated.json"""
    base, ext = os.path.splitext(snapshot_path)
    return f"{base}{EVAL_SUFFIX}{ext}"


def _load_snapshot(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[Evaluator] Cannot read {path}: {e}")
        return None


def _save_evaluated(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[Evaluator] Cannot save {path}: {e}")


# ── Получение цен ─────────────────────────────────────────────────────────────

def _throttle() -> None:
    """Пауза между запросами к Polygon, чтобы не упираться в 5 req/min."""
    global _last_fetch_ts
    now = time.monotonic()
    wait = PACING_SECONDS - (now - _last_fetch_ts)
    if wait > 0:
        time.sleep(wait)
    _last_fetch_ts = time.monotonic()


def _fetch_closes_after(
    ticker: str,
    signal_date: str,
    days_needed: int,
) -> list[float]:
    """
    Возвращает список close-цен начиная с торгового дня после signal_date.
    Запрашивает days_needed * 1.5 дней (с запасом на выходные/праздники).
    retries=2: одна неудача не должна навсегда помечать сигнал как no_data.
    """
    try:
        _throttle()
        start = (
            datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        end = (
            datetime.strptime(signal_date, "%Y-%m-%d")
            + timedelta(days=int(days_needed * 1.5) + 10)
        ).strftime("%Y-%m-%d")

        data = get_ticker_daily_bars(ticker, start, end, limit=50, retries=2)
        if not data or "results" not in data:
            return []
        return [float(r["c"]) for r in data["results"]]
    except Exception as e:
        logger.debug(f"[Evaluator] fetch_closes {ticker}: {e}")
        return []


def _fetch_benchmark_pnl(signal_date: str, days_needed: int) -> Optional[dict]:
    """
    P&L бенчмарка (QQQ) за те же окна, что и сигналы этой даты.
    Возвращает {"d5": x, "d10": y, "d20": z} или None.
    База — закрытие бенчмарка в день сигнала, горизонт — h торговых дней после.
    """
    try:
        _throttle()
        end = (
            datetime.strptime(signal_date, "%Y-%m-%d")
            + timedelta(days=int(days_needed * 1.5) + 10)
        ).strftime("%Y-%m-%d")
        data = get_ticker_daily_bars(BENCHMARK_TICKER, signal_date, end, limit=50, retries=2)
        if not data or "results" not in data:
            return None
        closes = [float(r["c"]) for r in data["results"]]
        if len(closes) < 2:
            return None
        entry = closes[0]  # закрытие в день сигнала
        rest = closes[1:]
        return {f"d{h}": _pnl_at_horizon(entry, rest, h) for h in EVAL_HORIZONS}
    except Exception as e:
        logger.debug(f"[Evaluator] benchmark fetch {signal_date}: {e}")
        return None


def _pnl_at_horizon(
    entry_price: float,
    closes: list[float],
    horizon: int,
) -> Optional[float]:
    """P&L в % на горизонте horizon торговых баров. None если данных нет."""
    if len(closes) < horizon:
        return None
    exit_price = closes[horizon - 1]
    return round((exit_price - entry_price) / entry_price * 100, 2)


def _hit_target(
    entry_price: float,
    closes: list[float],
    target: Optional[float],
) -> bool:
    """True если цена хоть раз достигла target в рамках имеющихся данных."""
    if target is None or target <= entry_price or not closes:
        return False
    return any(c >= target for c in closes)


# ── Оценка одного сигнала ─────────────────────────────────────────────────────

def _evaluate_signal(signal: dict, signal_date: str, benchmark: Optional[dict] = None) -> dict:
    """
    Дополняет один сигнал метриками P&L (и P&L бенчмарка для α).
    Возвращает исходный dict + поле 'evaluation'.
    """
    ticker       = signal.get("ticker", "")
    entry_price  = float(signal.get("price", 0))
    swing        = signal.get("swing") or {}
    target       = swing.get("target")

    evaluation: dict = {
        "evaluated_at": datetime.now().isoformat(),
        "signal_date":  signal_date,
        "entry_price":  entry_price,
        "horizons":     {},
        "benchmark":    benchmark,   # {"d5": x, ...} P&L бенчмарка за те же окна
        "hit_target":   False,
        "target":       target,
        "status":       "pending",   # pending | evaluated | no_data
    }

    if not ticker or entry_price <= 0:
        evaluation["status"] = "no_data"
        return {**signal, "evaluation": evaluation}

    max_horizon = max(EVAL_HORIZONS)
    closes      = _fetch_closes_after(ticker, signal_date, max_horizon)

    if not closes:
        evaluation["status"] = "no_data"
        return {**signal, "evaluation": evaluation}

    for h in EVAL_HORIZONS:
        pnl = _pnl_at_horizon(entry_price, closes, h)
        evaluation["horizons"][f"d{h}"] = pnl

    evaluation["hit_target"] = _hit_target(entry_price, closes, target)
    evaluation["status"]     = "evaluated"

    # Максимальное движение за весь период (для анализа потенциала)
    evaluation["max_move_pct"] = round(
        (max(closes) - entry_price) / entry_price * 100, 2
    )
    evaluation["min_move_pct"] = round(
        (min(closes) - entry_price) / entry_price * 100, 2
    )

    return {**signal, "evaluation": evaluation}


# ── Накопление статистики ─────────────────────────────────────────────────────

def _new_acc() -> dict:
    return {
        "total_signals":    0,
        "signals_seen":     0,   # до дедупа
        "evaluated_ok":     0,
        "no_data_count":    0,
        "hit_target_count": 0,
        "pnl_by_horizon":   {f"d{h}": [] for h in EVAL_HORIZONS},
        "alpha_by_horizon": {f"d{h}": [] for h in EVAL_HORIZONS},
        "benchmark_by_horizon": {f"d{h}": [] for h in EVAL_HORIZONS},
        "last_seen":        {},  # ticker -> signal_date (для дедупа)
        "ticker_details":   [],
        # Разбивка по EMA-зоне входа (только swing-сигналы, поле swing.ema_zone)
        "by_zone": {
            z: {
                "count": 0,
                "hit_target": 0,
                "pnl":   {f"d{h}": [] for h in EVAL_HORIZONS},
                "alpha": {f"d{h}": [] for h in EVAL_HORIZONS},
            }
            for z in ("A", "B", "C")
        },
        # Для симуляции лимит-выхода: (max_move_pct, pnl на крайнем доступном горизонте)
        "exit_pairs": [],
    }


def _accumulate(acc: dict, evaluated_signals: list, signal_date: str) -> None:
    for ev_signal in evaluated_signals:
        acc["signals_seen"] += 1
        ticker = ev_signal.get("ticker", "?")

        # Дедуп: тот же тикер в пределах DEDUP_DAYS — тот же незакрытый сетап,
        # раздувает выборку (пример: MRK 19 и 22 июня)
        prev = acc["last_seen"].get(ticker)
        if prev and signal_date:
            try:
                gap = (datetime.fromisoformat(signal_date)
                       - datetime.fromisoformat(prev)).days
                if 0 <= gap < DEDUP_DAYS:
                    continue
            except Exception:
                pass
        if signal_date:
            acc["last_seen"][ticker] = signal_date

        acc["total_signals"] += 1
        ev = ev_signal.get("evaluation", {})
        if ev.get("status") == "evaluated":
            acc["evaluated_ok"] += 1
            if ev.get("hit_target"):
                acc["hit_target_count"] += 1
            bench = ev.get("benchmark") or {}
            for h in EVAL_HORIZONS:
                key = f"d{h}"
                pnl = ev["horizons"].get(key)
                bp  = bench.get(key)
                if pnl is not None:
                    acc["pnl_by_horizon"][key].append(pnl)
                if pnl is not None and bp is not None:
                    acc["alpha_by_horizon"][key].append(pnl - bp)
                    acc["benchmark_by_horizon"][key].append(bp)

            # Зона входа: только swing-сигналы (у ET/Momentum зон нет)
            zone = (ev_signal.get("swing") or {}).get("ema_zone")
            if zone in acc["by_zone"]:
                zbucket = acc["by_zone"][zone]
                zbucket["count"] += 1
                if ev.get("hit_target"):
                    zbucket["hit_target"] += 1
                for h in EVAL_HORIZONS:
                    key = f"d{h}"
                    pnl = ev["horizons"].get(key)
                    bp  = bench.get(key)
                    if pnl is not None:
                        zbucket["pnl"][key].append(pnl)
                    if pnl is not None and bp is not None:
                        zbucket["alpha"][key].append(pnl - bp)

            # Пара для симуляции лимит-выхода: дошёл ли max_move до уровня,
            # иначе — финальный P&L на самом длинном доступном горизонте
            mm = ev.get("max_move_pct")
            if mm is not None:
                fallback = (ev["horizons"].get("d20")
                            or ev["horizons"].get("d10")
                            or ev["horizons"].get("d5"))
                if fallback is not None:
                    acc["exit_pairs"].append((mm, fallback, zone))

            acc["ticker_details"].append({
                "ticker":      ev_signal.get("ticker", "?"),
                "signal_date": signal_date,
                "entry_price": ev.get("entry_price", 0),
                "target":      ev.get("target"),
                "hit_target":  ev.get("hit_target", False),
                "pnl_d5":      ev["horizons"].get("d5"),
                "pnl_d10":     ev["horizons"].get("d10"),
                "pnl_d20":     ev["horizons"].get("d20"),
                "max_move":    ev.get("max_move_pct"),
                "n_modules":   ev_signal.get("n_modules", 1),
                "combo_label": ev_signal.get("combo_label", ""),
            })
        else:
            acc["no_data_count"] += 1


def _finalize_stats(acc: dict, extra: dict) -> dict:
    ok = acc["evaluated_ok"]
    stats: dict = {
        "total_signals":     acc["total_signals"],
        "signals_seen":      acc["signals_seen"],
        "signals_evaluated": ok,
        "no_data":           acc["no_data_count"],
        "hit_target_count":  acc["hit_target_count"],
        "hit_target_pct":    (
            round(acc["hit_target_count"] / ok * 100, 1) if ok else None
        ),
        "avg_pnl":           {},
        "win_rate":          {},
        "avg_benchmark":     {},
        "alpha":             {},
        "beat_benchmark_pct": {},
        "ticker_details": acc["ticker_details"],
    }
    for h in EVAL_HORIZONS:
        key  = f"d{h}"
        vals = acc["pnl_by_horizon"][key]
        if vals:
            stats["avg_pnl"][key]  = round(sum(vals) / len(vals), 2)
            stats["win_rate"][key] = round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)
        else:
            stats["avg_pnl"][key]  = None
            stats["win_rate"][key] = None

        bench_vals = acc["benchmark_by_horizon"][key]
        alpha_vals = acc["alpha_by_horizon"][key]
        if bench_vals:
            stats["avg_benchmark"][key]     = round(sum(bench_vals) / len(bench_vals), 2)
            stats["alpha"][key]             = round(sum(alpha_vals) / len(alpha_vals), 2)
            stats["beat_benchmark_pct"][key] = round(
                sum(1 for a in alpha_vals if a > 0) / len(alpha_vals) * 100, 1
            )
        else:
            stats["avg_benchmark"][key]      = None
            stats["alpha"][key]              = None
            stats["beat_benchmark_pct"][key] = None

    # Разбивка по зонам входа
    zone_breakdown: dict = {}
    for zone, data in acc["by_zone"].items():
        if data["count"] == 0:
            continue
        zone_stats = {
            "count":          data["count"],
            "hit_target_pct": round(data["hit_target"] / data["count"] * 100, 1),
        }
        for h in EVAL_HORIZONS:
            key        = f"d{h}"
            pnl_vals   = data["pnl"][key]
            alpha_vals = data["alpha"][key]
            zone_stats[key] = {
                "avg_pnl":   round(sum(pnl_vals) / len(pnl_vals), 2) if pnl_vals else None,
                "avg_alpha": round(sum(alpha_vals) / len(alpha_vals), 2) if alpha_vals else None,
            }
        zone_breakdown[zone] = zone_stats
    stats["zone_breakdown"] = zone_breakdown

    # Симуляция лимит-выхода: лимит на +X% за окно 20д, иначе закрытие на d20
    def _calc_exit_rules(triples):
        rules = []
        if not triples:
            return rules
        from statistics import median
        for level in (5, 10, 15, 20):
            outcomes = [level if mm >= level else fb for mm, fb, _ in triples]
            reached  = sum(1 for mm, _, _ in triples if mm >= level)
            rules.append({
                "level":       level,
                "reached_pct": round(reached / len(triples) * 100, 1),
                "expectancy":  round(sum(outcomes) / len(outcomes), 2),
                "median":      round(median(outcomes), 2),
                "n":           len(triples),
            })
        return rules

    stats["exit_rules"]   = _calc_exit_rules(acc["exit_pairs"])
    # Отдельно по зоне A: единственная зона с подтверждённым положительным α
    stats["exit_rules_A"] = _calc_exit_rules(
        [t for t in acc["exit_pairs"] if t[2] == "A"]
    )

    stats.update(extra)
    return stats


def _snapshot_age_days(signal_date: str) -> Optional[int]:
    if not signal_date:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(signal_date)).days
    except Exception:
        return None


# ── Основной прогон ───────────────────────────────────────────────────────────

def run_evaluation(progress_fn=None) -> dict:
    """
    Оценивает все необработанные снапшоты старше MIN_SNAPSHOT_AGE_DAYS.
    progress_fn(done, total) вызывается после каждого сигнала (для Telegram).
    Возвращает сводную статистику для вывода в Telegram.
    """
    stats = {
        "snapshots_processed": 0,
        "skipped_fresh":       0,
        "empty_snapshots":     0,
        "partial_snapshots":   0,
        "next_ready_date":     None,
    }
    snapshots = _list_snapshots()
    if not snapshots:
        # Полная форма статистики — вызывающий код читает одни и те же ключи
        return _finalize_stats(_new_acc(), stats)

    acc = _new_acc()
    fresh_dates: list[str] = []

    # Первый проход: загрузка и сортировка снапшотов по возрасту/пустоте
    to_evaluate: list[tuple[str, dict, str]] = []
    for snap_path in snapshots:
        snap = _load_snapshot(snap_path)
        if not snap:
            continue
        if snap.get("partial"):
            # Скан прерван вручную: помечаем twin'ом и не пересматриваем —
            # диагностический след остаётся, в статистику сигналов не попадает
            result = {**snap, "evaluation_run": datetime.now().isoformat(), "skipped_partial": True}
            _save_evaluated(_eval_path(snap_path), result)
            stats["partial_snapshots"] += 1
            logger.info(f"[Evaluator] {snap_path} — partial (скан прерван вручную), помечен")
            continue
        signal_date = snap.get("timestamp", "")[:10]
        age_days = _snapshot_age_days(signal_date)
        if age_days is not None and age_days < MIN_SNAPSHOT_AGE_DAYS:
            stats["skipped_fresh"] += 1
            fresh_dates.append(signal_date)
            logger.info(f"[Evaluator] Skip {snap_path} — too fresh ({age_days}d)")
            continue
        if not snap.get("top_picks", []):
            # Пустой скан — помечаем и не пересматриваем, без запросов к Polygon
            result = {**snap, "evaluation_run": datetime.now().isoformat(), "skipped_empty": True}
            _save_evaluated(_eval_path(snap_path), result)
            stats["empty_snapshots"] += 1
            logger.info(f"[Evaluator] {snap_path} — пустой скан (0 сигналов), помечен")
            continue
        to_evaluate.append((snap_path, snap, signal_date))

    if fresh_dates:
        earliest = min(fresh_dates)
        try:
            ready = datetime.fromisoformat(earliest) + timedelta(days=MIN_SNAPSHOT_AGE_DAYS)
            stats["next_ready_date"] = ready.strftime("%d.%m.%Y")
        except Exception:
            pass

    total = sum(len(snap.get("top_picks", [])) for _, snap, _ in to_evaluate)
    done  = 0

    # Второй проход: оценка (запросы к Polygon с паузой _throttle).
    # Бенчмарк fetch'ится один раз на дату сигнала — все сигналы даты делят окна.
    bench_cache: dict[str, Optional[dict]] = {}
    for snap_path, snap, signal_date in to_evaluate:
        stats["snapshots_processed"] += 1
        if signal_date not in bench_cache:
            bench_cache[signal_date] = _fetch_benchmark_pnl(
                signal_date, max(EVAL_HORIZONS)
            )
        evaluated = [
            _evaluate_signal(signal, signal_date, bench_cache[signal_date])
            for signal in snap.get("top_picks", [])
        ]
        done += len(evaluated)
        if progress_fn:
            try:
                progress_fn(done, total)
            except Exception:
                pass

        _accumulate(acc, evaluated, signal_date)

        result = {**snap, "top_picks": evaluated, "evaluation_run": datetime.now().isoformat()}
        _save_evaluated(_eval_path(snap_path), result)
        logger.info(f"[Evaluator] Saved {_eval_path(snap_path)}")

    return _finalize_stats(acc, stats)


# ── Накопленная статистика по уже оценённым снапшотам ────────────────────────

def aggregate_evaluated_stats() -> dict:
    """Суммарная статистика по всем *_evaluated.json (без запросов к Polygon)."""
    acc = _new_acc()
    files_used = 0
    for path in _list_evaluated():
        snap = _load_snapshot(path)
        if not snap or snap.get("skipped_empty") or snap.get("skipped_partial"):
            continue
        picks = snap.get("top_picks", [])
        evaluated = [p for p in picks if isinstance(p, dict) and p.get("evaluation")]
        if not evaluated:
            continue
        files_used += 1
        _accumulate(acc, evaluated, snap.get("timestamp", "")[:10])

    return _finalize_stats(acc, {
        "snapshots_processed": files_used,
        "skipped_fresh":       0,
        "empty_snapshots":     0,
        "next_ready_date":     None,
        "cumulative":          True,
    })


# ── Готовый отчёт для Telegram ────────────────────────────────────────────────

def evaluate_and_format(progress_fn=None) -> tuple[dict, str]:
    """
    Прогоняет оценку; если оценивать нечего — показывает накопленную
    статистику по уже оценённым снапшотам.
    Возвращает (stats, text): stats нужен вызывающему коду, чтобы понять,
    был ли реально выполнен хоть один прогон.
    """
    stats = run_evaluation(progress_fn=progress_fn)
    if stats["snapshots_processed"] == 0 and stats["signals_evaluated"] == 0:
        cum = aggregate_evaluated_stats()
        if cum["signals_evaluated"] > 0 or cum["no_data"] > 0:
            return cum, format_evaluation_report(cum)
    return stats, format_evaluation_report(stats)


# ── Форматирование для Telegram ───────────────────────────────────────────────

def format_evaluation_report(stats: dict) -> str:
    """Форматирует сводную статистику для отправки в Telegram."""
    processed = stats["snapshots_processed"]
    n         = stats["signals_evaluated"]

    header_extra = ""
    if stats.get("cumulative"):
        header_extra = (
            f"\n\n<i>Накопленная статистика по всем {processed} оценённым снапшотам "
            f"(новых снапшотов для оценки сейчас нет).</i>"
        )

    if processed == 0 and n == 0:
        lines = [
            "📊 <b>Оценка сигналов</b>\n",
            "❌ Пока нечего оценивать.",
        ]
        if stats.get("skipped_fresh"):
            lines.append(
                f"⏭ Снапшотов слишком свежих (младше {MIN_SNAPSHOT_AGE_DAYS} дней): "
                f"<b>{stats['skipped_fresh']}</b>"
            )
        if stats.get("partial_snapshots"):
            lines.append(f"⏹ Прерванных вручную (пропущено): <b>{stats['partial_snapshots']}</b>")
        if stats.get("next_ready_date"):
            lines.append(f"📅 Первые будут готовы примерно: <b>{stats['next_ready_date']}</b>")
        lines.append(
            "\nОценка запускается автоматически по будням в 20:00 CET, "
            "результаты придут сюда."
        )
        return "\n".join(lines)

    lines  = [
        "📊 <b>ОЦЕНКА СИГНАЛОВ UNIFIED SCANNER</b>\n",
    ]
    if not stats.get("cumulative"):
        lines.append(f"📁 Снапшотов оценено: <b>{processed}</b>")
        if stats.get("skipped_fresh"):
            lines.append(f"⏭ Слишком свежих (пропущено): <b>{stats['skipped_fresh']}</b>")
        if stats.get("empty_snapshots"):
            lines.append(f"📭 Пустых сканов (0 сигналов): <b>{stats['empty_snapshots']}</b>")
        if stats.get("partial_snapshots"):
            lines.append(f"⏹ Прерванных вручную (пропущено): <b>{stats['partial_snapshots']}</b>")
        if stats.get("next_ready_date"):
            lines.append(f"📅 Следующие созреют примерно: <b>{stats['next_ready_date']}</b>")
    lines += [
        f"✅ Сигналов оценено: <b>{n}</b>",
        f"❓ Нет данных: <b>{stats['no_data']}</b>",
    ]

    if n == 0:
        if stats.get("next_ready_date"):
            lines.append(f"\n📅 Первые результаты примерно: <b>{stats['next_ready_date']}</b>")
        else:
            lines.append("\n⚠️ Нет данных для статистики. Попробуйте позже.")
        lines.append(header_extra)
        return "\n".join(lines)

    target = stats.get("hit_target_pct")
    if target is not None:
        lines.append(f"🎯 Достигли таргета: <b>{stats['hit_target_count']}</b> ({target}%)")

    lines.append("\n<b>Средний P&L и Win Rate по горизонтам:</b>")
    has_benchmark = any(
        stats.get("avg_benchmark", {}).get(f"d{h}") is not None for h in EVAL_HORIZONS
    )
    if has_benchmark:
        lines.append(f"<i>Бенчмарк: {BENCHMARK_TICKER} за те же окна. "
                     f"α = сигнал − рынок; «обогнали» — доля сигналов, обогнавших рынок.</i>")
    for h in EVAL_HORIZONS:
        key      = f"d{h}"
        avg_pnl  = stats["avg_pnl"].get(key)
        win_rate = stats["win_rate"].get(key)
        if avg_pnl is not None:
            pnl_icon = "🟢" if avg_pnl > 0 else "🔴"
            line = f"  {pnl_icon} +{h}д: avg {avg_pnl:+.1f}% | win {win_rate:.0f}%"
            if has_benchmark:
                avg_b = stats.get("avg_benchmark", {}).get(key)
                alpha = stats.get("alpha", {}).get(key)
                beat  = stats.get("beat_benchmark_pct", {}).get(key)
                if avg_b is not None:
                    line += (f" | {BENCHMARK_TICKER}: {avg_b:+.1f}%"
                             f" | α: {alpha:+.1f}% | обогнали: {beat:.0f}%")
            lines.append(line)
        else:
            lines.append(f"  ⚪ +{h}д: нет данных")

    # ── Разбивка по зоне входа (swing-сигналы) ────────────────────────────────
    zone_bd = stats.get("zone_breakdown") or {}
    if zone_bd:
        lines.append("\n<b>📍 P&L по зоне входа (относительно 30W EMA):</b>")
        zone_labels = {
            "A": "🟢 A (у EMA, 0–8%)",
            "B": "🟡 B (умеренный перегрев, 8–20%)",
            "C": "🟠 C (перегрев, >20%)",
        }
        for zone in ("A", "B", "C"):
            z = zone_bd.get(zone)
            if not z:
                continue
            pnl_parts, alpha_parts = [], []
            for h in EVAL_HORIZONS:
                d = z.get(f"d{h}") or {}
                ap = d.get("avg_pnl")
                if ap is not None:
                    icon = "🟢" if ap > 0 else "🔴"
                    pnl_parts.append(f"{icon}{h}д {ap:+.1f}%")
                aa = d.get("avg_alpha")
                if aa is not None:
                    alpha_parts.append(f"{h}д {aa:+.1f}%")
            line = f"  {zone_labels[zone]}, n={z['count']}: " + " | ".join(pnl_parts)
            if alpha_parts:
                line += f"\n      α: " + " | ".join(alpha_parts)
            lines.append(line)

        a, c = zone_bd.get("A"), zone_bd.get("C")
        if a and c and a["count"] >= 5 and c["count"] >= 5:
            for h in (10, 20):
                da = (a.get(f"d{h}") or {}).get("avg_alpha")
                dc = (c.get(f"d{h}") or {}).get("avg_alpha")
                if da is not None and dc is not None and dc - da < -2:
                    lines.append(
                        "⚠️ Зона C заметно слабее A по α — кандидат на фильтр перегретых."
                    )
                    break

    # ── Симуляция лимит-выхода ────────────────────────────────────────────────
    exit_rules = stats.get("exit_rules") or []
    if exit_rules:
        lines.append("\n<b>🎯 Лимит-выход: продать при +X% (окно 20 дней):</b>")
        lines.append(
            "<i>Лимит исполняется, если цена коснулась уровня (тогда результат ровно +X%), "
            "иначе закрытие по цене d20. «Касание = исполнение» — оптимистично "
            "(без гэпов и спреда).</i>"
        )
        for r in exit_rules:
            lines.append(
                f"  +{r['level']}%: дошли {r['reached_pct']}% | "
                f"средний итог {r['expectancy']:+.2f}% | медиана {r['median']:+.2f}% (n={r['n']})"
            )
        rules_a = stats.get("exit_rules_A") or []
        if rules_a and rules_a[0]["n"] >= 5:
            lines.append("  <b>Только зона A:</b>")
            for r in rules_a:
                lines.append(
                    f"  +{r['level']}%: дошли {r['reached_pct']}% | "
                    f"средний итог {r['expectancy']:+.2f}% | медиана {r['median']:+.2f}% (n={r['n']})"
                )
        lines.append("<i>Сравнение — «удержание до d20» из таблицы выше.</i>")

    # ── Детали по тикерам ────────────────────────────────────────────────────
    ticker_details = stats.get("ticker_details", [])
    if ticker_details:
        lines.append("\n<b>📋 Детали по сигналам:</b>")
        # Сортируем: сначала достигшие таргета, потом по P&L d5; топ-20
        sorted_details = sorted(
            ticker_details,
            key=lambda x: (not x.get("hit_target", False), -(x.get("pnl_d5") or -99)),
        )[:20]
        for td in sorted_details:
            ticker      = td["ticker"]
            entry       = td["entry_price"]
            signal_date = td["signal_date"]
            hit         = td["hit_target"]
            pnl5        = td.get("pnl_d5")
            pnl10       = td.get("pnl_d10")
            pnl20       = td.get("pnl_d20")
            max_move    = td.get("max_move")
            combo       = td.get("combo_label", "")

            target_icon = "🎯" if hit else "  "
            combo_str   = f"[{combo}] " if combo else ""

            pnl_parts = []
            for label, val in [("5д", pnl5), ("10д", pnl10), ("20д", pnl20)]:
                if val is not None:
                    icon = "🟢" if val > 0 else "🔴"
                    pnl_parts.append(f"{icon}{label}: {val:+.1f}%")
                else:
                    pnl_parts.append(f"⚪{label}: н/д")
            pnl_str = " | ".join(pnl_parts)

            max_str = f" | макс {max_move:+.1f}%" if max_move is not None else ""

            lines.append(
                f"{target_icon} <b>${ticker}</b> {combo_str}| {signal_date} | вход ${entry}\n"
                f"   {pnl_str}{max_str}"
            )

    lines.append("\n<i>⚠️ Прошлые результаты не гарантируют будущих. DYOR.</i>")
    lines.append(header_extra)
    return "\n".join(lines)
