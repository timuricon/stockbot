"""
Тесты cooperative-отмены сканов: cancel_event прерывает основной цикл
до обработки следующего тикера, часть результатов сохраняется.
Дополнительно: пауза между тикерами прерываемая (_interruptible_pause).
Запуск:  python -m pytest tests/ -v
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import scanners.unified_scanner as us
import scanners.superstock_scanner as ss


def _candidates(n: int) -> list[dict]:
    return [
        {"ticker": f"T{i}", "price": 10.0, "dollar_volume": 1_000_000 + i}
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _fast_pause(monkeypatch):
    """Сканируем с паузой ~0 — тесты не должны ждать 13 секунд за итерацию."""
    monkeypatch.setattr(us, "UNIFIED_PAUSE_SEC", 0.0)


def _patch_unified_modules(monkeypatch):
    """Все три модуля проходят, бары и цены мгновенные — без сети."""
    monkeypatch.setattr(
        us, "load_universe", lambda: [{"ticker": f"T{i}"} for i in range(5)]
    )
    monkeypatch.setattr(us, "_get_candidates", lambda universe_set: _candidates(5))
    monkeypatch.setattr(us, "get_daily_bars_df", lambda ticker, cancel_event=None: object())
    monkeypatch.setattr(us, "analyze_early_trend", lambda df: {"score": 30})
    monkeypatch.setattr(us, "get_current_price", lambda ticker: 10.0)
    monkeypatch.setattr(
        us, "_analyze_swing",
        lambda ticker, price, ref, cancel_event=None: {"score": 30},
    )
    monkeypatch.setattr(
        us, "_analyze_momentum",
        lambda ticker, cancel_event=None: {"score": 20},
    )


# ── run_unified_scan ──────────────────────────────────────────────────────────

def test_unified_scan_cancel_stops_early(monkeypatch, tmp_path):
    _patch_unified_modules(monkeypatch)
    monkeypatch.chdir(tmp_path)  # снапшот analytics_history/ пишется во временную папку

    calls = {"n": 0}

    def momentum_canceler(ticker, cancel_event=None):
        calls["n"] += 1
        if calls["n"] == 2:
            cancel_event.set()   # отмена в конце обработки 2-го тикера
        return {"score": 20}

    monkeypatch.setattr(us, "_analyze_momentum", momentum_canceler)

    picks, near_misses, meta = us.run_unified_scan({}, cancel_event=threading.Event())

    assert len(picks) + len(near_misses) == 2   # 3-й тикер не обрабатывался
    assert calls["n"] == 2
    # Отменённый скан сохраняется как partial — evaluator такие пропускает
    assert meta["cancelled"] is True
    snaps = list((tmp_path / "analytics_history").glob("scan_*.json"))
    assert len(snaps) == 1
    snap = json.loads(snaps[0].read_text(encoding="utf-8"))
    assert snap["partial"] is True


def test_unified_scan_without_cancel_processes_all(monkeypatch, tmp_path):
    """Обратная совместимость: cancel_event=None — скан идёт до конца."""
    _patch_unified_modules(monkeypatch)
    monkeypatch.chdir(tmp_path)

    picks, near_misses, meta = us.run_unified_scan({})
    assert len(picks) + len(near_misses) == 5
    # Полный скан — без partial-пометки, в статистику попадает как обычно
    assert meta["cancelled"] is False
    snaps = list((tmp_path / "analytics_history").glob("scan_*.json"))
    assert len(snaps) == 1
    snap = json.loads(snaps[0].read_text(encoding="utf-8"))
    assert "partial" not in snap


def test_unified_scan_cancelled_before_start(monkeypatch, tmp_path):
    """Отмена до первого тикера — ни один кандидат не обрабатывается."""
    _patch_unified_modules(monkeypatch)
    monkeypatch.chdir(tmp_path)

    processed = {"n": 0}

    def counting_momentum(ticker, cancel_event=None):
        processed["n"] += 1
        return {"score": 20}

    monkeypatch.setattr(us, "_analyze_momentum", counting_momentum)

    cancel_event = threading.Event()
    cancel_event.set()
    picks, near_misses, meta = us.run_unified_scan({}, cancel_event=cancel_event)

    assert picks == [] and near_misses == []
    assert processed["n"] == 0


# ── run_et_only_scan ──────────────────────────────────────────────────────────

def test_et_only_scan_cancel_stops_early(monkeypatch):
    monkeypatch.setattr(
        us, "load_universe", lambda: [{"ticker": f"T{i}"} for i in range(5)]
    )
    monkeypatch.setattr(us, "_get_candidates", lambda universe_set: _candidates(5))
    monkeypatch.setattr(
        us, "_analyze_swing",
        lambda ticker, price, ref, cancel_event=None: None,
    )
    monkeypatch.setattr(us, "get_current_price", lambda ticker: 10.0)
    monkeypatch.setattr(
        us, "analyze_early_trend",
        lambda df: {"score": 20, "patterns": ["P"], "full_stack": True,
                    "above_ema200": True, "emas": {}},
    )

    calls = {"n": 0}

    def daily_canceler(ticker, cancel_event=None):
        calls["n"] += 1
        if calls["n"] == 2:
            cancel_event.set()
        return object()

    monkeypatch.setattr(us, "get_daily_bars_df", daily_canceler)

    picks = us.run_et_only_scan({}, cancel_event=threading.Event())

    assert len(picks) == 2
    assert calls["n"] == 2


# ── run_superstock_scan ───────────────────────────────────────────────────────

def test_superstock_scan_cancel_stops_early(monkeypatch):
    reference = {f"T{i}": {"exchange": "XNAS"} for i in range(5)}
    monkeypatch.setattr(
        ss, "get_grouped_daily",
        lambda cancel_event=None: [
            {"T": f"T{i}", "c": 10.0, "v": 100_000} for i in range(5)
        ],
    )
    monkeypatch.setattr(ss, "prefilter_superstock", lambda ticker, info: None)

    calls = {"n": 0}

    def analyze_canceler(ticker_info, reference, cancel_event=None):
        calls["n"] += 1
        if calls["n"] == 2:
            cancel_event.set()
        return {"ticker": ticker_info["ticker"], "score": 90, "rev_yoy_pct": 30}

    monkeypatch.setattr(ss, "analyze_superstock_ticker", analyze_canceler)

    results = ss.run_superstock_scan(reference, cancel_event=threading.Event())

    assert len(results) == 2
    assert calls["n"] == 2


# ── Прерываемая пауза ─────────────────────────────────────────────────────────

def test_interruptible_pause_returns_early_on_cancel():
    ev = threading.Event()
    threading.Timer(0.2, ev.set).start()

    t0 = time.monotonic()
    us._interruptible_pause(ev, 30)   # «спим» 30с — но отмену ставят через 0.2с
    elapsed = time.monotonic() - t0

    assert elapsed < 5   # без прерываемой паузы было бы ~30с


def test_superstock_pause_also_interruptible():
    ev = threading.Event()
    threading.Timer(0.2, ev.set).start()

    t0 = time.monotonic()
    ss._interruptible_pause(ev, 30)
    elapsed = time.monotonic() - t0

    assert elapsed < 5
