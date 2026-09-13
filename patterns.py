from typing import Optional
import numpy as np
import pandas as pd

from core.config import GROWTH_VOL_PERIOD, GROWTH_PATTERN_SCORES


def calc_volume_growth(df: pd.DataFrame) -> Optional[float]:
    """Отношение последнего бара к SMA(GROWTH_VOL_PERIOD) по объёму."""
    if df is None or len(df) < GROWTH_VOL_PERIOD + 1:
        return None
    try:
        sma_vol = df["Volume"].iloc[-(GROWTH_VOL_PERIOD + 1):-1].mean()
        if sma_vol <= 0:
            return None
        return round(df["Volume"].iloc[-1] / sma_vol, 2)
    except Exception:
        return None


def calc_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    ATR за последние period баров.
    True Range = max(H-L, |H-Cprev|, |L-Cprev|).
    """
    if df is None or len(df) < period + 1:
        return None
    try:
        high  = df["High"].iloc[-(period + 1):]
        low   = df["Low"].iloc[-(period + 1):]
        close = df["Close"].iloc[-(period + 1):]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1).iloc[1:]  # убираем первый NaN
        return float(tr.mean())
    except Exception:
        return None


def _breakout_within_atr(
    df: pd.DataFrame,
    breakout_level: float,
    atr_multiplier: float = 0.5,
) -> bool:
    """
    True если цена пробила уровень, но не ушла дальше ATR*multiplier.
    Фильтрует «догонялки» — вход когда пробой уже давно случился.
    """
    atr = calc_atr(df)
    if atr is None or atr <= 0:
        return True  # нет данных — не блокируем
    last_close = float(df["Close"].iloc[-1])
    return last_close <= breakout_level + atr * atr_multiplier


def detect_range_breakout(df: pd.DataFrame) -> bool:
    """
    Пробой узкого диапазона (≤5%) на повышенном объёме.
    ATR-фильтр: цена не улетела дальше 0.5 ATR от уровня пробоя.
    """
    if len(df) < 12:
        return False
    try:
        rng  = df.iloc[-12:-2]
        high = float(rng["High"].max())
        low  = float(rng["Low"].min())
        if low <= 0:
            return False
        if (high - low) / low > 0.05:
            return False
        last     = df.iloc[-1]
        prev_vol = float(df["Volume"].iloc[-12:-2].mean())
        broke_out = (
            float(last["Close"]) > high
            and float(last["Volume"]) > prev_vol * 1.5
        )
        if not broke_out:
            return False
        return _breakout_within_atr(df, breakout_level=high)
    except Exception:
        return False


def detect_flag(df: pd.DataFrame) -> bool:
    if len(df) < 12:
        return False
    try:
        impulse  = df.iloc[-10:-7]
        flag     = df.iloc[-7:-1]
        imp_move = (
            (float(impulse["Close"].iloc[-1]) - float(impulse["Open"].iloc[0]))
            / float(impulse["Open"].iloc[0])
        )
        if imp_move < 0.05:
            return False
        flag_high = float(flag["High"].max())
        flag_low  = float(flag["Low"].min())
        base      = float(flag["Close"].iloc[0])
        if base <= 0:
            return False
        return (flag_high - flag_low) / base < imp_move * 0.5
    except Exception:
        return False


def detect_double_bottom(df: pd.DataFrame) -> bool:
    if len(df) < 20:
        return False
    try:
        closes = df["Close"].values
        lows   = df["Low"].values
        window = lows[-20:]
        min1_idx = int(np.argmin(window[:10]))
        min2_idx = int(np.argmin(window[10:])) + 10
        if abs(min2_idx - min1_idx) < 5:
            return False
        min1 = window[min1_idx]
        min2 = window[min2_idx]
        if min1 <= 0:
            return False
        diff    = abs(min1 - min2) / min1
        current = closes[-1]
        return diff < 0.03 and current > max(min1, min2) * 1.02
    except Exception:
        return False


def detect_volume_spike(df: pd.DataFrame) -> bool:
    if len(df) < 12:
        return False
    try:
        sma_vol = float(df["Volume"].iloc[-11:-1].mean())
        last    = df.iloc[-1]
        return (
            float(last["Volume"]) >= sma_vol * 2.0
            and float(last["Close"]) > float(last["Open"])
        )
    except Exception:
        return False


def detect_cup_handle(df: pd.DataFrame) -> bool:
    """
    Cup & Handle.
    ATR-фильтр: цена у края чаши, не улетела выше чем ATR*0.5 от cup_high.
    """
    if len(df) < 30:
        return False
    try:
        cup    = df.iloc[-30:-5]
        handle = df.iloc[-5:]
        cup_high    = float(cup["High"].max())
        cup_low     = float(cup["Low"].min())
        depth       = cup_high - cup_low
        if depth <= 0 or cup_low <= 0:
            return False
        depth_pct = depth / cup_high
        if depth_pct < 0.10 or depth_pct > 0.50:
            return False
        handle_low  = float(handle["Low"].min())
        handle_drop = (cup_high - handle_low) / depth
        last_close  = float(df["Close"].iloc[-1])
        pattern_ok  = handle_drop < 0.50 and last_close > cup_high * 0.97
        if not pattern_ok:
            return False
        return _breakout_within_atr(df, breakout_level=cup_high)
    except Exception:
        return False


def detect_all_patterns(df: Optional[pd.DataFrame]) -> list[str]:
    """Запускает все паттерны, возвращает список найденных."""
    if df is None or len(df) < 20:
        return []
    found = []
    if detect_cup_handle(df):     found.append("Cup & Handle")
    if detect_range_breakout(df): found.append("Range Breakout")
    if detect_double_bottom(df):  found.append("Double Bottom")
    if detect_flag(df):           found.append("Flag")
    if detect_volume_spike(df):   found.append("Volume Spike")
    return found


def score_growth_signal(
    patterns_1d: list[str],
    patterns_1w: list[str],
    vol_ratio_1d: Optional[float],
    vol_ratio_1w: Optional[float],
) -> tuple[int, str]:
    """
    Скоринг growth сигнала.
    Возвращает (score, grade): grade = 'strong' | 'medium' | 'weak'.
    """
    score = 0
    combo = set(patterns_1d) & set(patterns_1w)

    for p in combo:
        score += 4 + GROWTH_PATTERN_SCORES.get(p, 0)
    for p in patterns_1w:
        if p not in combo:
            score += 3 + GROWTH_PATTERN_SCORES.get(p, 0)
    for p in patterns_1d:
        if p not in combo:
            score += 2 + GROWTH_PATTERN_SCORES.get(p, 0)

    if vol_ratio_1d is not None:
        score += 2 if vol_ratio_1d >= 3.0 else (1 if vol_ratio_1d >= 1.5 else 0)
    if vol_ratio_1w is not None:
        score += 2 if vol_ratio_1w >= 3.0 else (1 if vol_ratio_1w >= 1.5 else 0)

    grade = "strong" if score >= 10 else ("medium" if score >= 6 else "weak")
    return score, grade
