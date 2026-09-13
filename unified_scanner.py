"""
Unified Scanner — объединяет Swing, Momentum и Early Trend в один скан.

Архитектура:
1. Pre-filter: grouped daily → пересечение с universe → топ-300 по dollar volume
2. Для каждого кандидата: один набор баров → три модуля анализа
3. Скоринг с combo-бонусами
4. Единый список отсортированный по final_score (≥ 60/100)
5. Топ 20 результатов

Скоринг модулей:
- Swing:       0-40 баллов
- Momentum:    0-30 баллов
- Early Trend: 0-30 баллов
- Combo x2:    +20 баллов
- Combo x3:    +40 баллов
Итого макс: 140 → нормализуем к 100.

Проходной порог: нормализованный балл ≥ 60.
"""

import json
import os
import time
from datetime import datetime
from typing import Optional

from core.config import (
    EXCLUDED_TICKERS, MIN_PRICE, EMA_PERIOD,
    MIN_UPSIDE_PCT, EXCH_MAP,
)
from core.logging_setup import logger
from core.utils import (
    calc_ema, calc_resistance, calc_rr_ratio,
    calc_trend_structure, calc_volume_on_up_weeks,
)
from data_provider.bars import get_weekly_bars, get_daily_bars_df, get_growth_weekly_bars_df
from data_provider.polygon_client import get_grouped_daily
from data_provider.yfinance_client import get_current_price, get_analyst_consensus
from portfolio.universe import load_universe
from scanners.filters import passes_exclusion, passes_min_price, passes_volume_not_drying
from scanners.patterns import (
    calc_volume_growth, detect_all_patterns, score_growth_signal,
)
from scanners.early_trend import analyze_early_trend

# ── Константы ─────────────────────────────────────────────────────────────────

UNIFIED_MAX_PICKS         = 20
UNIFIED_NEAR_MISSES_COUNT = 10
UNIFIED_PREFILTER_TOP     = 300

# Минимальный дневной dollar volume для попадания в pre-filter
UNIFIED_MIN_DOLLAR_VOLUME = 1_000_000  # $1M
UNIFIED_MIN_SCORE         = 60
UNIFIED_MAX_SCORE         = 140
UNIFIED_PAUSE_SEC         = 13.0

# Динамический порог: если below_ema превышает этот % — снижаем порог
DYNAMIC_THRESHOLD_MARKET_PCT = 40.0
DYNAMIC_THRESHOLD_DROP       = 8   # на сколько пунктов снижать (60 → 52)
PREFILTER_CACHE_FILE      = "prefilter_cache.json"
PREFILTER_CACHE_TTL_HOURS = 4


# ── Pre-filter cache ──────────────────────────────────────────────────────────

def _load_prefilter_cache() -> list:
    """Загружает кэшированный список кандидатов если он свежий."""
    try:
        if not os.path.exists(PREFILTER_CACHE_FILE):
            return []
        with open(PREFILTER_CACHE_FILE, "r") as f:
            data = json.load(f)
        ts  = datetime.fromisoformat(data.get("ts", "2000-01-01"))
        age = (datetime.now() - ts).total_seconds() / 3600
        if age < PREFILTER_CACHE_TTL_HOURS:
            tickers = data.get("tickers", [])
            logger.info(f"[Unified] Pre-filter cache hit: {len(tickers)} candidates ({age:.1f}h old)")
            return tickers
    except Exception as e:
        logger.debug(f"[Unified] Pre-filter cache read error: {e}")
    return []


def _save_prefilter_cache(tickers: list) -> None:
    try:
        with open(PREFILTER_CACHE_FILE, "w") as f:
            json.dump({"ts": datetime.now().isoformat(), "tickers": tickers}, f)
    except Exception as e:
        logger.debug(f"[Unified] Pre-filter cache write error: {e}")


# ── Pre-filter ────────────────────────────────────────────────────────────────

def _get_candidates(universe_set: set[str]) -> list[dict]:
    """
    Grouped daily → фильтрация по universe → топ-UNIFIED_PREFILTER_TOP
    по dollar volume. Результат кэшируется на PREFILTER_CACHE_TTL_HOURS часов.
    """
    cached = _load_prefilter_cache()
    if cached:
        return cached

    raw = get_grouped_daily()
    if not raw:
        return []

    candidates  = []
    low_volume  = 0
    for t in raw:
        ticker = t.get("T", "")
        if not ticker or ticker not in universe_set:
            continue
        if ticker in EXCLUDED_TICKERS:
            continue
        price  = t.get("c", 0) or 0
        volume = t.get("v", 0) or 0
        if price < MIN_PRICE:
            continue
        dollar_volume = price * volume
        if dollar_volume < UNIFIED_MIN_DOLLAR_VOLUME:
            low_volume += 1
            continue
        candidates.append({
            "ticker":        ticker,
            "price":         round(price, 2),
            "dollar_volume": dollar_volume,
        })

    candidates.sort(key=lambda x: x["dollar_volume"], reverse=True)
    top = candidates[:UNIFIED_PREFILTER_TOP]
    logger.info(
        f"[Unified] Pre-filter: {len(candidates)} from universe "
        f"(отсеяно по ликвидности < ${UNIFIED_MIN_DOLLAR_VOLUME/1e6:.1f}M: {low_volume}) → "
        f"top {len(top)} by dollar volume"
    )
    _save_prefilter_cache(top)
    return top


# ── Swing диагностика ─────────────────────────────────────────────────────────

# Глобальные счётчики причин отсева swing — сбрасываются в начале каждого скана
_swing_diag: dict[str, int] = {}


def _interruptible_pause(cancel_event, seconds: float) -> None:
    """Пауза, прерываемая сигналом отмены: при set() возвращаемся сразу."""
    if cancel_event is not None:
        cancel_event.wait(seconds)
    else:
        time.sleep(seconds)


def _reset_swing_diag() -> None:
    global _swing_diag
    _swing_diag = {
        "no_ref":        0,  # тикер не в reference
        "no_bars":       0,  # нет недельных баров
        "min_price":     0,  # цена ниже минимума
        "below_ema":     0,  # цена ≤ 30W EMA
        "zone_d":        0,  # EMA зона D (>35%)
        "low_upside":    0,  # апсайд < MIN_UPSIDE_PCT
        "no_trend":      0,  # нет HH/HL структуры
        "vol_drying":    0,  # объём иссыхает
        "no_up_weeks":   0,  # объём не подтверждает рост
        "low_rr":        0,  # R/R < 1.5
        "passed":        0,  # прошёл все фильтры
    }


def _log_swing_diag_summary() -> None:
    """Выводит итоговую статистику причин отсева swing."""
    total = sum(_swing_diag.values())
    lines = ["[Swing диагностика] Причины отсева:"]
    for reason, count in sorted(_swing_diag.items(), key=lambda x: -x[1]):
        if count > 0:
            pct = count / total * 100 if total else 0
            lines.append(f"  {reason:15s}: {count:4d} ({pct:.1f}%)")
    logger.info("\n".join(lines))


# ── Swing модуль ──────────────────────────────────────────────────────────────

def _analyze_swing(
    ticker: str,
    ticker_price: float,
    reference: dict,
    cancel_event=None,
) -> Optional[dict]:
    """
    Swing анализ на недельных барах.
    Возвращает dict с score (0-40) и деталями или None если не прошёл фильтры.
    Все причины отсева логируются в _swing_diag.
    """
    ref = reference.get(ticker)
    if not ref:
        _swing_diag["no_ref"] += 1
        return None

    df = get_weekly_bars(ticker, cancel_event=cancel_event)
    if df is None or len(df) < 20:
        _swing_diag["no_bars"] += 1
        logger.debug(f"[Swing] {ticker}: no_bars (df={'None' if df is None else len(df)})")
        return None

    current_price = get_current_price(ticker) or ticker_price
    if not passes_min_price(current_price):
        _swing_diag["min_price"] += 1
        logger.debug(f"[Swing] {ticker}: min_price (price={current_price:.2f})")
        return None

    closed_bars = df.iloc[:-1] if len(df) > 20 else df
    ema30     = calc_ema(closed_bars["Close"], EMA_PERIOD)
    ema30_val = float(ema30.iloc[-1])

    if current_price <= ema30_val:
        _swing_diag["below_ema"] += 1
        logger.debug(
            f"[Swing] {ticker}: below_ema "
            f"(price={current_price:.2f} ema={ema30_val:.2f})"
        )
        return None

    ema_pct = ((current_price - ema30_val) / ema30_val) * 100

    if ema_pct > 35:
        _swing_diag["zone_d"] += 1
        logger.debug(f"[Swing] {ticker}: zone_d (ema_pct={ema_pct:.1f}%)")
        return None

    if ema_pct <= 8:    ema_zone = "A"
    elif ema_pct <= 20: ema_zone = "B"
    else:               ema_zone = "C"

    resistance = calc_resistance(closed_bars["High"])
    if resistance < current_price:
        resistance = current_price
    target     = max(resistance * 1.015, current_price * (1 + MIN_UPSIDE_PCT / 100))
    upside_pct = ((target - current_price) / current_price) * 100

    if upside_pct < MIN_UPSIDE_PCT - 0.05:
        _swing_diag["low_upside"] += 1
        logger.debug(
            f"[Swing] {ticker}: low_upside "
            f"(upside={upside_pct:.1f}% resistance={resistance:.2f} price={current_price:.2f})"
        )
        return None

    if not calc_trend_structure(closed_bars):
        _swing_diag["no_trend"] += 1
        logger.debug(f"[Swing] {ticker}: no_trend (HH/HL не найден)")
        return None

    if not passes_volume_not_drying(closed_bars):
        _swing_diag["vol_drying"] += 1
        logger.debug(f"[Swing] {ticker}: vol_drying")
        return None

    if not calc_volume_on_up_weeks(closed_bars):
        _swing_diag["no_up_weeks"] += 1
        logger.debug(f"[Swing] {ticker}: no_up_weeks")
        return None

    rr, stop_lvl = calc_rr_ratio(current_price, target, ema30_val)
    if rr < 1.5:
        _swing_diag["low_rr"] += 1
        logger.debug(f"[Swing] {ticker}: low_rr (rr={rr:.2f})")
        return None

    # ── Объём vs 10W ──────────────────────────────────────────────────────────
    vol_chg = None
    if len(closed_bars) >= 11:
        last_vol   = float(closed_bars["Volume"].iloc[-1])
        avg_vol_10 = float(closed_bars["Volume"].iloc[-11:-1].mean())
        if avg_vol_10 > 0:
            vol_chg = round(((last_vol - avg_vol_10) / avg_vol_10) * 100, 1)

    # Консенсус
    consensus_tp = round(get_analyst_consensus(ticker) or 0, 2) or None

    # ── Скоринг Swing (0-40) ──────────────────────────────────────────────────
    score = 0

    if ema_zone == "A":   score += 12
    elif ema_zone == "B": score += 8
    else:                 score += 4

    if rr >= 3.0:   score += 10
    elif rr >= 2.5: score += 8
    elif rr >= 2.0: score += 5
    else:           score += 2

    if 25 <= upside_pct <= 60:   score += 10
    elif upside_pct > 60:        score += 6

    if vol_chg is not None:
        if vol_chg >= 50:   score += 8
        elif vol_chg >= 20: score += 4

    if consensus_tp and current_price < consensus_tp * 0.85:
        score = min(40, score + 4)

    _swing_diag["passed"] += 1

    exchange = ref.get("exchange", "")
    sic      = ref.get("sic", "").strip()
    sector   = sic.title() if sic and sic != "N/A" else ref.get("name", "")[:25]

    return {
        "score":             min(score, 40),
        "ema_zone":          ema_zone,
        "ema_pct":           round(ema_pct, 1),
        "ema30_val":         round(ema30_val, 2),
        "target":            round(target, 2),
        "upside_pct":        round(upside_pct, 1),
        "resistance":        round(resistance, 2),
        "rr_ratio":          rr,
        "stop_level":        stop_lvl,
        "volume_change_pct": vol_chg,
        "consensus_tp":      consensus_tp,
        "exchange":          EXCH_MAP.get(exchange, exchange),
        "sector":            sector,
    }


# ── Momentum модуль ───────────────────────────────────────────────────────────

def _analyze_momentum(ticker: str, cancel_event=None) -> Optional[dict]:
    """
    Momentum анализ: паттерны на 1D + 1W, объём.
    Возвращает dict с score (0-30) или None если не прошёл.
    """
    df_1d = get_daily_bars_df(ticker, cancel_event=cancel_event)
    df_1w = get_growth_weekly_bars_df(ticker, cancel_event=cancel_event)

    if df_1d is None and df_1w is None:
        return None

    vol_ratio_1d = calc_volume_growth(df_1d)
    vol_ratio_1w = calc_volume_growth(df_1w)

    from core.config import GROWTH_MIN_VOL_RATIO
    vol_ok = (
        (vol_ratio_1d is not None and vol_ratio_1d >= GROWTH_MIN_VOL_RATIO) or
        (vol_ratio_1w is not None and vol_ratio_1w >= GROWTH_MIN_VOL_RATIO)
    )
    if not vol_ok:
        return None

    patterns_1d = detect_all_patterns(df_1d)
    patterns_1w = detect_all_patterns(df_1w)

    if not patterns_1d and not patterns_1w:
        return None

    raw_score, grade = score_growth_signal(patterns_1d, patterns_1w, vol_ratio_1d, vol_ratio_1w)
    combo = list(set(patterns_1d) & set(patterns_1w))

    normalized = min(int(raw_score * 1.5), 30)

    return {
        "score":        normalized,
        "grade":        grade,
        "patterns_1d":  patterns_1d,
        "patterns_1w":  patterns_1w,
        "combo":        combo,
        "vol_ratio_1d": vol_ratio_1d,
        "vol_ratio_1w": vol_ratio_1w,
    }


# ── Основной скан ─────────────────────────────────────────────────────────────

def run_unified_scan(
    reference: dict, cancel_event=None
) -> tuple[list[dict], list[dict], dict]:
    """
    Главная функция unified скана.
    reference — dict из load_ticker_reference().
    cancel_event — threading.Event; set() прерывает скан после текущего тикера.
    Возвращает (results, near_misses, scan_meta):
      - results: топ-пики прошедшие эффективный порог
      - near_misses: топ-10 кандидатов ниже порога
      - scan_meta: {"effective_threshold", "threshold_lowered", "below_ema_pct"}
    """
    universe     = load_universe()
    universe_set = {t["ticker"] for t in universe}
    logger.info(f"[Unified] Universe: {len(universe_set)} tickers")

    if not universe_set:
        logger.error("[Unified] Universe пуст — запустите rebuild через бота")
        return [], [], {}

    candidates = _get_candidates(universe_set)
    if not candidates:
        logger.error("[Unified] No candidates after pre-filter")
        return [], [], {}

    _reset_swing_diag()

    all_results = []  # все кандидаты с хотя бы одним модулем, независимо от порога
    total       = len(candidates)

    diag = {
        "no_bars":       0,
        "swing_fail":    0,
        "momentum_fail": 0,
        "et_fail":       0,
        "all_fail":      0,
        "low_score":     0,
        "passed":        0,
    }

    for i, tkr_info in enumerate(candidates):
        if cancel_event is not None and cancel_event.is_set():
            logger.info(f"[Unified] Cancelled at {i}/{total}")
            break

        ticker = tkr_info["ticker"]
        try:
            ticker_price  = tkr_info.get("price", 0)

            swing_result  = _analyze_swing(ticker, ticker_price, reference, cancel_event)
            moment_result = _analyze_momentum(ticker, cancel_event)
            df_daily      = get_daily_bars_df(ticker, cancel_event=cancel_event)
            et_result     = analyze_early_trend(df_daily)

            if df_daily is None:
                diag["no_bars"] += 1
            if swing_result is None:
                diag["swing_fail"] += 1
            if moment_result is None:
                diag["momentum_fail"] += 1
            if et_result is None:
                diag["et_fail"] += 1

            passed_modules = [
                m for m in [swing_result, moment_result, et_result] if m is not None
            ]

            if not passed_modules:
                diag["all_fail"] += 1
                logger.debug(f"[Unified {i+1}/{total}] ❌ {ticker}: no modules passed")
                _interruptible_pause(cancel_event, UNIFIED_PAUSE_SEC)
                continue

            n_modules   = len(passed_modules)
            combo_bonus = 40 if n_modules == 3 else (20 if n_modules == 2 else 0)
            active = []
            if swing_result:   active.append("Swing")
            if moment_result:  active.append("Momentum")
            if et_result:      active.append("ET")
            combo_label = "ВСЕ 3" if n_modules == 3 else "+".join(active)

            raw_score = (
                (swing_result["score"]  if swing_result  else 0) +
                (moment_result["score"] if moment_result else 0) +
                (et_result["score"]     if et_result     else 0) +
                combo_bonus
            )
            normalized_score = round((raw_score / UNIFIED_MAX_SCORE) * 100)
            current_price    = get_current_price(ticker) or ticker_price

            all_results.append({
                "ticker":         ticker,
                "price":          round(current_price, 2),
                "score":          normalized_score,
                "raw_score":      raw_score,
                "n_modules":      n_modules,
                "combo_label":    combo_label,
                "active_modules": active,
                "swing":          swing_result,
                "momentum":       moment_result,
                "early_trend":    et_result,
            })
            logger.debug(
                f"[Unified {i+1}/{total}] {ticker}: "
                f"score={normalized_score} modules={n_modules} combo={combo_label}"
            )

        except Exception as e:
            logger.warning(f"[Unified {i+1}/{total}] Error {ticker}: {e}")

        _interruptible_pause(cancel_event, UNIFIED_PAUSE_SEC)

    # ── Динамический порог ────────────────────────────────────────────────────
    # Если рынок в широкой коррекции (below_ema > 40% свечей) — снижаем порог
    # на DYNAMIC_THRESHOLD_DROP пунктов, чтобы не получать пустой скан неделями.
    total_swing  = sum(_swing_diag.values())
    below_ema_pct = (
        _swing_diag.get("below_ema", 0) / total_swing * 100 if total_swing else 0
    )

    effective_threshold = UNIFIED_MIN_SCORE
    threshold_lowered   = False
    if below_ema_pct > DYNAMIC_THRESHOLD_MARKET_PCT:
        effective_threshold = UNIFIED_MIN_SCORE - DYNAMIC_THRESHOLD_DROP
        threshold_lowered   = True
        logger.info(
            f"[Unified] Рынок в коррекции (below_ema={below_ema_pct:.1f}%) — "
            f"порог снижен {UNIFIED_MIN_SCORE} → {effective_threshold}"
        )

    # ── Разделение на top_picks / near_misses по эффективному порогу ──────────
    results     = [r for r in all_results if r["score"] >= effective_threshold]
    near_misses = [r for r in all_results if r["score"] < effective_threshold]

    diag["passed"]    = len(results)
    diag["low_score"] = len(near_misses)
    diag["all_fail"]  = total - len(all_results)

    # Помечаем результаты, прошедшие только благодаря сниженному порогу
    if threshold_lowered:
        for r in results:
            r["threshold_lowered"] = r["score"] < UNIFIED_MIN_SCORE

    # ── Итоговая диагностика ──────────────────────────────────────────────────
    logger.info(
        f"[Unified] Done: {total} scanned | passed={diag['passed']} | "
        f"no_bars={diag['no_bars']} | all_fail={diag['all_fail']} | "
        f"low_score={diag['low_score']} | threshold={effective_threshold}\n"
        f"  Module stats: swing_fail={diag['swing_fail']} "
        f"momentum_fail={diag['momentum_fail']} et_fail={diag['et_fail']}"
    )

    # Детальная статистика swing — ключевая для диагностики
    _log_swing_diag_summary()

    # ── Сортировка и подготовка результатов ───────────────────────────────────
    results.sort(key=lambda x: (-x["n_modules"], -x["score"]))

    near_misses.sort(key=lambda x: -x["score"])
    top_near_misses = near_misses[:UNIFIED_NEAR_MISSES_COUNT]

    logger.info(
        f"[Unified] near_misses collected: {len(near_misses)} total, "
        f"returning top {len(top_near_misses)} (min score in top: {top_near_misses[0]['score'] if top_near_misses else 'N/A'})"
    )

    cancelled = bool(cancel_event is not None and cancel_event.is_set())

    scan_meta = {
        "effective_threshold": effective_threshold,
        "threshold_lowered":   threshold_lowered,
        "below_ema_pct":       round(below_ema_pct, 1),
        "cancelled":           cancelled,
    }

    final_top_picks = results[:UNIFIED_MAX_PICKS]

    # ── Сохранение снапшота для аналитики ─────────────────────────────────────
    def _save_scan_to_analytics(
        top_data: list[dict], misses_data: list[dict],
        meta: dict, folder: str = "analytics_history",
        partial: bool = False,
    ) -> None:
        """
        Сохраняет результаты скана в analytics_history/ для signal_evaluator.
        partial=True — скан был прерван вручную: снапшот сохраняется как
        диагностический след, но signal_evaluator такие пропускает
        (см. run_evaluation: skipped_partial).
        """
        try:
            if not os.path.exists(folder):
                os.makedirs(folder)

            filename = f"scan_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.json"
            filepath = os.path.join(folder, filename)

            snapshot = {
                "timestamp": datetime.now().isoformat(),
                "total_passed_threshold": len(top_data),
                "total_near_misses_collected": len(misses_data),
                "scan_meta": meta,
                "top_picks": top_data,
                "near_misses_top10": misses_data,
            }
            if partial:
                snapshot["partial"] = True

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=4)

            logger.info(
                f"[Unified Analytics] Snapshot saved to {filepath}"
                + (" (partial — скан прерван вручную)" if partial else "")
            )
        except Exception as e:
            logger.error(f"[Unified Analytics] Failed to save snapshot: {e}")

    _save_scan_to_analytics(
        final_top_picks, top_near_misses, scan_meta, partial=cancelled
    )

    return final_top_picks, top_near_misses, scan_meta

# ── ET-only скан ──────────────────────────────────────────────────────────────

ET_ONLY_TOP_N   = 10
ET_ONLY_MIN_SCORE = 8   # минимальный балл ET чтобы попасть в список


def run_et_only_scan(reference: dict, cancel_event=None) -> list[dict]:
    """
    Скан только по модулю Early Trend — без требований Swing и Momentum.
    cancel_event — threading.Event; set() прерывает скан после текущего тикера.
    Возвращает топ-ET_ONLY_TOP_N тикеров отсортированных по ET score.

    Используется как дополнительный список для более агрессивных входов:
    пробой EMA и закрепление, тест EMA и отскок, консолидация перед пробоем.
    """
    universe     = load_universe()
    universe_set = {t["ticker"] for t in universe}

    candidates = _get_candidates(universe_set)
    if not candidates:
        logger.error("[ET-only] No candidates after pre-filter")
        return []

    results = []
    total   = len(candidates)

    for i, tkr_info in enumerate(candidates):
        if cancel_event is not None and cancel_event.is_set():
            logger.info(f"[ET-only] Cancelled at {i}/{total}")
            break

        ticker = tkr_info["ticker"]
        try:
            df_daily = get_daily_bars_df(ticker, cancel_event=cancel_event)
            et       = analyze_early_trend(df_daily)

            if et is None or et["score"] < ET_ONLY_MIN_SCORE:
                _interruptible_pause(cancel_event, UNIFIED_PAUSE_SEC)
                continue

            # Дополнительно берём текущую цену и swing данные если есть
            ticker_price  = tkr_info.get("price", 0)
            current_price = get_current_price(ticker) or ticker_price

            # Swing — опционально, только для контекста (не фильтр)
            swing = _analyze_swing(ticker, ticker_price, reference, cancel_event)

            result = {
                "ticker":       ticker,
                "price":        round(current_price, 2),
                "et_score":     et["score"],
                "patterns":     et["patterns"],
                "full_stack":   et["full_stack"],
                "above_ema200": et["above_ema200"],
                "rsi":          et.get("rsi"),
                "emas":         et["emas"],
                "pct_from_52w_high": et.get("pct_from_52w_high"),
                "swing":        swing,  # None если не прошёл swing фильтры
            }
            results.append(result)
            logger.info(
                f"[ET-only {i+1}/{total}] ✅ {ticker} "
                f"ET={et['score']} patterns={et['patterns']}"
            )

        except Exception as e:
            logger.warning(f"[ET-only {i+1}/{total}] Error {ticker}: {e}")

        _interruptible_pause(cancel_event, UNIFIED_PAUSE_SEC)

    results.sort(key=lambda x: -x["et_score"])
    top = results[:ET_ONLY_TOP_N]
    logger.info(f"[ET-only] Done: {len(results)} found, returning top {len(top)}")
    return top
