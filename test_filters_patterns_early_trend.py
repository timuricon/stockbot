"""
Unit-тесты чистых функций сканеров: scanners/filters.py, scanners/patterns.py,
scanners/early_trend.py (бэклог: покрытие ≥ 60%, чистые функции — дешёвый ROI).
Сетевые вызовы не выполняются — только синтетические DataFrame.
Запуск:  python -m pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from scanners.filters import (
    passes_exclusion, passes_min_price, passes_ema_filter,
    passes_ema_zone_d, passes_upside, passes_volume_not_drying,
)
from scanners.patterns import (
    calc_atr, calc_volume_growth, detect_all_patterns, detect_cup_handle,
    detect_double_bottom, detect_flag, detect_range_breakout,
    detect_volume_spike, score_growth_signal,
)
from scanners.early_trend import (
    _calc_daily_emas, _calc_rsi, analyze_early_trend,
    detect_golden_cross, detect_volume_contraction,
)


def _df(closes=None, vols=None, highs=None, lows=None, opens=None) -> pd.DataFrame:
    """Синтетические бары: список объёмов обязателен для длины, остальное — по умолчанию."""
    n = len(vols if vols is not None else closes)
    closes = closes if closes is not None else [10.0] * n
    vols   = vols if vols is not None else [1000.0] * n
    highs  = highs if highs is not None else [c + 0.1 for c in closes]
    lows   = lows if lows is not None else [c - 0.1 for c in closes]
    opens  = opens if opens is not None else list(closes)
    return pd.DataFrame({
        "Open": [float(x) for x in opens],
        "High": [float(x) for x in highs],
        "Low":  [float(x) for x in lows],
        "Close": [float(x) for x in closes],
        "Volume": [float(x) for x in vols],
    })


# ── filters.py ────────────────────────────────────────────────────────────────

def test_passes_exclusion():
    assert passes_exclusion("AAPL") is True
    assert passes_exclusion("SPY") is False     # SPY в EXCLUDED_TICKERS


def test_passes_min_price():
    assert passes_min_price(5.0) is True
    assert passes_min_price(4.99) is False


def test_passes_ema_filter():
    assert passes_ema_filter(11.0, 10.0) is True
    assert passes_ema_filter(10.0, 10.0) is False   # строго выше


def test_passes_ema_zone_d():
    assert passes_ema_zone_d(35.0) is True
    assert passes_ema_zone_d(35.1) is False         # зона Г (>35%) отсекается


def test_passes_upside():
    assert passes_upside(25.0, 25.0) is True
    assert passes_upside(24.96, 25.0) is True       # допуск -0.05пп
    assert passes_upside(24.9, 25.0) is False


def test_passes_volume_not_drying():
    vols = [1000.0] * 21
    vols[-1] = 600.0                                # 60% от 20W нормы
    assert passes_volume_not_drying(_df(vols=vols)) is True
    vols[-1] = 400.0                                # 40% — объём иссякает
    assert passes_volume_not_drying(_df(vols=vols)) is False
    assert passes_volume_not_drying(_df(vols=[1000.0] * 5)) is True  # мало данных — не блокируем


# ── patterns.py ───────────────────────────────────────────────────────────────

def test_calc_volume_growth():
    vols = [1000.0] * 11
    vols[-1] = 2000.0
    assert calc_volume_growth(_df(vols=vols)) == 2.0
    assert calc_volume_growth(_df(vols=[1000.0] * 11)) == 1.0
    assert calc_volume_growth(_df(vols=[1000.0] * 5)) is None        # мало баров
    assert calc_volume_growth(None) is None


def test_calc_atr():
    df = pd.DataFrame({
        "Open":   [11.0, 11.0, 12.0],
        "High":   [12.0, 11.5, 13.0],
        "Low":    [10.0, 10.5, 11.0],
        "Close":  [11.0, 11.0, 12.0],
        "Volume": [1000.0, 1000.0, 1000.0],
    })
    # TR: 2.0, затем max(1.0, 0.5, 0.5)=1.0 и max(2.0, 2.0, 0.0)=2.0 → mean=1.5
    assert calc_atr(df, period=2) == pytest.approx(1.5)
    assert calc_atr(_df(vols=[1000.0] * 3), period=14) is None       # мало баров


def test_detect_range_breakout():
    # 12 баров: узкий диапазон 9.8–10.2 (4%), последний бар пробивает на объёме.
    # ATR не считается (12 < 15) — «догонялку» не блокируем.
    df = _df(
        closes=[10.0] * 11 + [10.5],
        highs=[10.2] * 11 + [10.6],
        lows=[9.8] * 11 + [9.9],
        opens=[10.0] * 11 + [10.1],
        vols=[1000.0] * 11 + [2500.0],
    )
    assert detect_range_breakout(df) is True

    # 15 баров: цена улетела на +2$ выше диапазона при ATR ~0.5 — «догонялка»
    df = _df(
        closes=[10.0] * 14 + [12.0],
        highs=[10.2] * 14 + [12.2],
        lows=[9.8] * 14 + [11.0],
        opens=[10.0] * 14 + [11.0],
        vols=[1000.0] * 14 + [2500.0],
    )
    assert detect_range_breakout(df) is False

    # Пробоя нет: закрытие внутри диапазона
    df = _df(vols=[1000.0] * 11 + [2500.0])
    assert detect_range_breakout(df) is False


def test_detect_volume_spike():
    vols = [1000.0] * 12
    vols[-1] = 2500.0
    assert detect_volume_spike(
        _df(closes=[10.0] * 11 + [10.5], opens=[10.0] * 12, vols=vols)
    ) is True
    # Объём есть, но бар красный
    assert detect_volume_spike(
        _df(closes=[10.0] * 11 + [9.5], opens=[10.0] * 12, vols=vols)
    ) is False
    # Объёма нет
    assert detect_volume_spike(_df(vols=[1000.0] * 12)) is False


def test_pattern_guards_on_short_data():
    assert detect_cup_handle(_df(vols=[1000.0] * 25)) is False    # < 30
    assert detect_double_bottom(_df(vols=[1000.0] * 15)) is False # < 20
    assert detect_flag(_df(vols=[1000.0] * 8)) is False           # < 12
    assert detect_all_patterns(None) == []
    assert detect_all_patterns(_df(vols=[1000.0] * 10)) == []     # < 20


def test_score_growth_signal_grades():
    # Combo на 1D и 1W: (4+1) + vol 1 + vol 1 = 7 → medium
    assert score_growth_signal(["Volume Spike"], ["Volume Spike"], 2.0, 2.0) == (7, "medium")

    # Combo 2 паттерна + дополнительный на 1W + сильный объём:
    # (4+3)+(4+2) + (3+2) + 2+2 = 22 → strong
    assert score_growth_signal(
        ["Cup & Handle", "Flag"], ["Cup & Handle", "Flag", "Double Bottom"], 3.0, 3.0
    ) == (22, "strong")

    # Один слабый паттерн только на 1D, без объёма: 2+2=4 → weak
    assert score_growth_signal(["Flag"], [], None, None) == (4, "weak")


# ── early_trend.py ────────────────────────────────────────────────────────────

def test_calc_rsi():
    closes = pd.Series([float(i) for i in range(1, 31)])   # только рост
    assert _calc_rsi(closes) == 100.0
    assert _calc_rsi(pd.Series([1.0, 2.0]), period=14) is None


def test_detect_volume_contraction():
    declining = [2000.0 - i * 100 for i in range(10)]      # объём заметно сужается
    assert detect_volume_contraction(_df(vols=declining))   # возвращает np.bool_
    rising = [1000.0 + i * 100 for i in range(10)]
    assert not detect_volume_contraction(_df(vols=rising))


def test_detect_golden_cross_needs_data():
    assert detect_golden_cross(_df(vols=[1000.0] * 100)) is False  # < 210 баров


def test_calc_daily_emas_requires_200_bars():
    assert _calc_daily_emas(_df(vols=[1000.0] * 150)) is None
    assert _calc_daily_emas(None) is None


def _trending_df(n: int = 250) -> pd.DataFrame:
    """Ровный восходящий ряд: EMA10>EMA30>EMA50>EMA200, объём ≥ 300K."""
    closes = [10.0 + i * 0.05 for i in range(n)]
    return _df(
        closes=closes,
        opens=[c - 0.01 for c in closes],
        vols=[500_000.0] * n,
    )


def test_analyze_early_trend_full_stack():
    result = analyze_early_trend(_trending_df())
    assert result is not None
    assert result["above_ema200"] is True
    assert result["full_stack"] is True
    assert result["score"] >= 8                            # минимум за полный стек
    assert result["emas"]["ema10"] > result["emas"]["ema30"] > result["emas"]["ema50"]


def test_analyze_early_trend_falling_rejected():
    df = _trending_df().iloc[::-1].reset_index(drop=True)  # падающий ряд
    assert analyze_early_trend(df) is None                 # нет стека, цена под EMA200


def test_analyze_early_trend_requires_200_bars():
    assert analyze_early_trend(None) is None
    assert analyze_early_trend(_df(vols=[1000.0] * 150)) is None
