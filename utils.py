import pandas as pd
import numpy as np


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """
    EMA с SMA-инициализацией — как в TradingView.
    Первое значение EMA = SMA первых period баров.
    """
    if len(series) < period:
        return series.ewm(span=period, adjust=False).mean()

    result = series.copy().astype(float)
    sma_seed = series.iloc[:period].mean()
    k = 2.0 / (period + 1)
    ema_values = [float("nan")] * (period - 1) + [sma_seed]
    for i in range(period, len(series)):
        ema_values.append(series.iloc[i] * k + ema_values[-1] * (1 - k))
    result[:] = ema_values
    return result


def calc_resistance(highs: pd.Series) -> float:
    """52-week high as resistance level."""
    last_52 = highs.tail(52)
    return float(last_52.max())


def calc_rr_ratio(
    price: float, target: float, ema_val: float, stop_pct: float = 0.03
) -> tuple[float, float]:
    """
    Рассчитывает Risk/Reward.
    Стоп = EMA * (1 - stop_pct).
    Возвращает (rr_ratio, stop_level).
    """
    stop = ema_val * (1 - stop_pct)
    if stop >= price:
        stop = price * (1 - stop_pct)
    risk = price - stop
    reward = target - price
    if risk <= 0:
        return 0.0, stop
    rr = round(reward / risk, 2)
    return rr, round(stop, 2)


def calc_trend_structure(bars: pd.DataFrame, lookback: int = 8) -> bool:
    """Higher Highs + Higher Lows на недельных барах."""
    if len(bars) < lookback:
        return False
    try:
        recent = bars.tail(lookback)
        half   = lookback // 2
        prev_h = float(recent["High"].iloc[:half].max())
        curr_h = float(recent["High"].iloc[half:].max())
        prev_l = float(recent["Low"].iloc[:half].min())
        curr_l = float(recent["Low"].iloc[half:].min())
        return (curr_h > prev_h) and (curr_l > prev_l)
    except Exception:
        return False


def calc_volume_on_up_weeks(bars: pd.DataFrame, lookback: int = 10) -> bool:
    """Объём выше на растущих неделях (признак накопления)."""
    if len(bars) < lookback:
        return False
    try:
        recent = bars.tail(lookback).copy()
        recent["up_week"] = recent["Close"] > recent["Open"]
        up_bars   = recent[recent["up_week"]]
        down_bars = recent[~recent["up_week"]]

        # Минимум 3 растущих недели — иначе нет смысла считать
        if len(up_bars) < 3:
            return False

        up_vol_avg = float(up_bars["Volume"].mean())
        if len(down_bars) == 0:
            return True
        down_vol_avg = float(down_bars["Volume"].mean())
        if down_vol_avg == 0:
            return True
        return (up_vol_avg / down_vol_avg) >= 1.05  # чуть мягче
    except Exception:
        return False