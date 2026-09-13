"""
Early Trend Scanner — ранние тренды по дневным EMA.

Жёсткие фильтры:
- EMA10 > EMA30 > EMA50 (ранний стек)
- Close > EMA200
- Avg volume ≥ 300K

Скоринг (0-30):
- EMA-стек полный (10>30>50>200): +8  (частичный: +3)
- Volume spike: +5
- Golden Cross (15 дней): +7
- Pullback to EMA + отскок: +6
- EMA200 breakout (10 дней): +4
- Volume contraction перед пробоем: +3
- Double Bottom: +3
- Flag: +3
"""

from typing import Optional

import numpy as np
import pandas as pd

from core.logging_setup import logger
from core.utils import calc_ema
from scanners.patterns import detect_double_bottom, detect_flag, detect_volume_spike


# ── EMA helpers ───────────────────────────────────────────────────────────────

def _calc_daily_emas(df: pd.DataFrame) -> Optional[dict]:
    """
    Считает EMA 10, 30, 50, 200 по дневным барам.
    Возвращает dict с последними значениями или None если баров недостаточно.
    """
    if df is None or len(df) < 200:
        return None
    try:
        return {
            "ema10":  float(calc_ema(df["Close"], 10).iloc[-1]),
            "ema30":  float(calc_ema(df["Close"], 30).iloc[-1]),
            "ema50":  float(calc_ema(df["Close"], 50).iloc[-1]),
            "ema200": float(calc_ema(df["Close"], 200).iloc[-1]),
        }
    except Exception as e:
        logger.debug(f"_calc_daily_emas error: {e}")
        return None


def _calc_sma_volume(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    """SMA объёма за period баров."""
    if df is None or len(df) < period:
        return None
    try:
        return float(df["Volume"].iloc[-period:].mean())
    except Exception:
        return None


# ── Паттерны ──────────────────────────────────────────────────────────────────

def detect_golden_cross(df: pd.DataFrame, lookback: int = 15) -> bool:
    """
    EMA50 пересекла EMA200 снизу вверх в последние lookback баров.
    Текущее положение: EMA50 > EMA200.
    """
    if df is None or len(df) < 210:
        return False
    try:
        ema50  = calc_ema(df["Close"], 50)
        ema200 = calc_ema(df["Close"], 200)
        if ema50.iloc[-1] <= ema200.iloc[-1]:
            return False
        diffs = (ema50 - ema200).iloc[-(lookback + 1):]
        for i in range(1, len(diffs)):
            if diffs.iloc[i - 1] <= 0 and diffs.iloc[i] > 0:
                return True
        return False
    except Exception:
        return False


def detect_pullback_to_ema(df: pd.DataFrame, emas: dict) -> Optional[str]:
    """
    Откат к EMA и отскок от неё в последних 5 барах.
    Возвращает имя EMA ('EMA10'/'EMA30'/'EMA50') или None.
    """
    if df is None or len(df) < 10 or emas is None:
        return None
    try:
        recent     = df.iloc[-5:]
        last_close = float(df["Close"].iloc[-1])
        prev_close = float(df["Close"].iloc[-2])
        for ema_name, ema_val in [
            ("EMA10", emas["ema10"]),
            ("EMA30", emas["ema30"]),
            ("EMA50", emas["ema50"]),
        ]:
            low_min      = float(recent["Low"].min())
            in_zone      = low_min <= ema_val * 1.015
            closes_above = all(
                float(recent["Close"].iloc[i]) > ema_val * 0.985
                for i in range(len(recent))
            )
            bouncing = last_close > prev_close
            if in_zone and closes_above and bouncing:
                return ema_name
        return None
    except Exception:
        return None


def detect_ema_breakout(df: pd.DataFrame, emas: dict) -> bool:
    """
    Свежий пробой EMA200: цена была ниже, теперь выше. Пробой в последние 10 баров.
    """
    if df is None or len(df) < 210 or emas is None:
        return False
    try:
        ema200  = calc_ema(df["Close"], 200)
        closes  = df["Close"].iloc[-10:]
        ema200w = ema200.iloc[-10:]
        for i in range(1, len(closes)):
            if closes.iloc[i - 1] <= ema200w.iloc[i - 1] and closes.iloc[i] > ema200w.iloc[i]:
                return True
        return False
    except Exception:
        return False


def detect_volume_contraction(df: pd.DataFrame, lookback: int = 8) -> bool:
    """
    Объём сужается в последние lookback баров — признак здоровой консолидации
    перед пробоем. Отличает накопление от вялого дрейфа.

    Метод: линейная регрессия по объёму, отрицательный наклон = сужение.
    Последний бар исключаем (он может быть уже пробойным).
    """
    if df is None or len(df) < lookback + 2:
        return False
    try:
        vols = df["Volume"].iloc[-(lookback + 1):-1].values.astype(float)
        if vols.mean() <= 0:
            return False
        x     = np.arange(len(vols), dtype=float)
        slope = np.polyfit(x, vols, 1)[0]
        # Нормализуем наклон к среднему объёму — избегаем ложных срабатываний
        # на акциях с изначально низким объёмом
        normalized_slope = slope / vols.mean()
        return normalized_slope < -0.02  # объём падает >2% в среднем за бар
    except Exception:
        return False


# ── RSI ───────────────────────────────────────────────────────────────────────

def _calc_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    """Упрощённый RSI."""
    if closes is None or len(closes) < period + 1:
        return None
    try:
        delta    = closes.diff().dropna()
        gains    = delta.clip(lower=0)
        losses   = (-delta).clip(lower=0)
        avg_gain = gains.iloc[-period:].mean()
        avg_loss = losses.iloc[-period:].mean()
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    except Exception:
        return None


# ── Основная функция анализа ──────────────────────────────────────────────────

def analyze_early_trend(df_daily: pd.DataFrame) -> Optional[dict]:
    """
    Анализирует дневные бары на предмет раннего тренда.

    Жёсткие фильтры:
    - EMA10 > EMA30 > EMA50
    - Close > EMA200
    - Avg volume ≥ 300K

    Скоринг (0-30):
    - EMA-стек полный (10>30>50>200): +8  (частичный: +3)
    - Volume spike: +5
    - Golden Cross (15 дней): +7
    - Pullback to EMA + отскок: +6
    - EMA200 breakout (10 дней): +4
    - Volume contraction перед пробоем: +3
    - Double Bottom: +3
    - Flag: +3

    Возвращает dict с результатами или None если жёсткие фильтры не прошли.
    """
    if df_daily is None or len(df_daily) < 200:
        return None

    emas = _calc_daily_emas(df_daily)
    if emas is None:
        return None

    current_price = float(df_daily["Close"].iloc[-1])

    # ── Жёсткие фильтры ──────────────────────────────────────────────────────
    ema_stack_partial = (
        emas["ema10"] > emas["ema30"]
        and emas["ema30"] > emas["ema50"]
    )
    above_ema200 = current_price > emas["ema200"]

    if not ema_stack_partial or not above_ema200:
        return None

    sma_vol = _calc_sma_volume(df_daily, period=20)
    if sma_vol is not None and sma_vol < 300_000:
        return None

    # ── Скоринг ───────────────────────────────────────────────────────────────
    score    = 0
    patterns = []

    full_stack = emas["ema50"] > emas["ema200"]
    if full_stack:
        score += 8
        patterns.append("EMA-стек полный")
    else:
        score += 3

    if detect_volume_spike(df_daily):
        score += 5
        patterns.append("Volume Spike")

    if detect_golden_cross(df_daily):
        score += 7
        patterns.append("Golden Cross")

    pullback_ema = detect_pullback_to_ema(df_daily, emas)
    if pullback_ema:
        score += 6
        patterns.append(f"Pullback→{pullback_ema}")

    if detect_ema_breakout(df_daily, emas):
        score += 4
        patterns.append("Пробой EMA200")

    if detect_volume_contraction(df_daily):
        score += 3
        patterns.append("Vol Contraction")

    if detect_double_bottom(df_daily):
        score += 3
        patterns.append("Double Bottom")

    if detect_flag(df_daily):
        score += 3
        patterns.append("Flag")

    # ── Вспомогательные данные ────────────────────────────────────────────────
    high_52w      = float(df_daily["High"].tail(252).max())
    pct_from_high = ((high_52w - current_price) / high_52w * 100) if high_52w > 0 else None

    rsi       = _calc_rsi(df_daily["Close"], period=14)
    vol_ratio = None
    if sma_vol and sma_vol > 0:
        last_vol  = float(df_daily["Volume"].iloc[-1])
        vol_ratio = round(last_vol / sma_vol, 2)

    return {
        "score":              min(score, 30),
        "patterns":           patterns,
        "emas":               {k: round(v, 2) for k, v in emas.items()},
        "full_stack":         full_stack,
        "above_ema200":       above_ema200,
        "pct_from_52w_high":  round(pct_from_high, 1) if pct_from_high else None,
        "rsi":                round(rsi, 1) if rsi else None,
        "vol_ratio_daily":    vol_ratio,
    }
