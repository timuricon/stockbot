"""
Тесты signal_evaluator — логика возрастного гейта, счётчиков и отчёта.
Запуск:  python -m pytest tests/ -v
или без pytest:  python tests/test_signal_evaluator.py
"""
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics.signal_evaluator as se  # noqa: E402


def _make_signal(ticker, zone=None):
    sig = {
        "ticker": ticker, "price": 10.0, "n_modules": 2,
        "combo_label": "SW+MOM",
        "swing": {"target": 13.0},
    }
    if zone:
        sig["swing"]["ema_zone"] = zone
    return sig


def _write_snap(path, days_ago, picks):
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": ts, "scan_meta": {}, "top_picks": picks}, f)


def _setup_history(tmp_path, monkeypatch):
    """Чистая analytics_history в tmp_path + стаб Polygon без сети."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(se, "PACING_SECONDS", 0)

    def fake_bars(ticker, *args, **kwargs):
        # QQQ растёт вдвое медленнее сигналов — чтобы α был ненулевой;
        # ZONA растёт в полтора раза быстрее — для разбивки по зонам
        if ticker == se.BENCHMARK_TICKER:
            step = 0.5
        elif ticker == "ZONA":
            step = 1.5
        else:
            step = 1.0
        return {"results": [{"c": 10 + i * step} for i in range(30)]}

    monkeypatch.setattr(se, "get_ticker_daily_bars", fake_bars)
    os.makedirs(se.ANALYTICS_DIR, exist_ok=True)
    return os.path.join(str(tmp_path), se.ANALYTICS_DIR)


def test_fresh_snapshots_skipped_and_counted(tmp_path, monkeypatch):
    """Главный регресс: свежие снапшоты → skipped_fresh, НЕ 'обработано'."""
    hist = _setup_history(tmp_path, monkeypatch)
    _write_snap(os.path.join(hist, "scan_2026-09-01_13-00.json"), 9,
                [_make_signal("AAA"), _make_signal("BBB")])
    _write_snap(os.path.join(hist, "scan_2026-09-05_13-00.json"), 5, [])

    stats = se.run_evaluation()

    assert stats["snapshots_processed"] == 0
    assert stats["skipped_fresh"] == 2
    assert stats["signals_evaluated"] == 0
    # свежие снапшоты НЕ расходуются
    assert not os.path.exists(os.path.join(hist, "scan_2026-09-01_13-00_evaluated.json"))


def test_old_snapshots_evaluated_empty_marked(tmp_path, monkeypatch):
    hist = _setup_history(tmp_path, monkeypatch)
    _write_snap(os.path.join(hist, "scan_2026-07-01_13-00.json"), 71,
                [_make_signal("OLD1"), _make_signal("OLD2")])
    _write_snap(os.path.join(hist, "scan_2026-07-10_13-00.json"), 62, [])

    stats = se.run_evaluation()

    assert stats["snapshots_processed"] == 1
    assert stats["empty_snapshots"] == 1
    assert stats["signals_evaluated"] == 2
    assert stats["hit_target_count"] == 2
    # пустой скан помечен и не пересматривается
    empty_twin = os.path.join(hist, "scan_2026-07-10_13-00_evaluated.json")
    with open(empty_twin, encoding="utf-8") as f:
        assert json.load(f).get("skipped_empty") is True
    # повторный прогон: нечего оценивать
    stats2 = se.run_evaluation()
    assert stats2["snapshots_processed"] == 0


def test_partial_snapshot_skipped_and_marked(tmp_path, monkeypatch):
    """Прерванный вручную скан (partial) не попадает в статистику сигналов:
    помечается skipped_partial и больше не пересматривается."""
    hist = _setup_history(tmp_path, monkeypatch)
    path = os.path.join(hist, "scan_2026-06-01_13-00.json")
    _write_snap(path, 101, [_make_signal("PART")])
    # помечаем как partial — как это делает run_unified_scan при отмене
    with open(path, encoding="utf-8") as f:
        snap = json.load(f)
    snap["partial"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)

    stats = se.run_evaluation()

    assert stats["snapshots_processed"] == 0
    assert stats["partial_snapshots"] == 1
    assert stats["signals_evaluated"] == 0
    # помечен twin'ом — оригинал с диагностикой остаётся рядом
    twin = os.path.join(hist, "scan_2026-06-01_13-00_evaluated.json")
    with open(twin, encoding="utf-8") as f:
        marked = json.load(f)
    assert marked.get("skipped_partial") is True
    assert marked.get("partial") is True
    # повторный прогон: partial больше не в списке
    stats2 = se.run_evaluation()
    assert stats2["partial_snapshots"] == 0
    # и в накопленную статистику не просачивается
    cum = se.aggregate_evaluated_stats()
    assert cum["signals_evaluated"] == 0

    text = se.format_evaluation_report(stats)
    assert "Прерванных вручную" in text


def test_report_lines(tmp_path, monkeypatch):
    hist = _setup_history(tmp_path, monkeypatch)
    _write_snap(os.path.join(hist, "scan_2026-09-01_13-00.json"), 9,
                [_make_signal("AAA")])
    _write_snap(os.path.join(hist, "scan_2026-07-01_13-00.json"), 71,
                [_make_signal("OLD1")])
    _write_snap(os.path.join(hist, "scan_2026-07-10_13-00.json"), 62, [])

    stats = se.run_evaluation()
    text = se.format_evaluation_report(stats)
    assert "Слишком свежих" in text
    assert "Пустых сканов" in text
    assert stats["next_ready_date"] in text
    assert "Сигналов оценено: <b>1</b>" in text


def test_cumulative_fallback(tmp_path, monkeypatch):
    """Когда оценивать нечего — показывается накопленная статистика."""
    hist = _setup_history(tmp_path, monkeypatch)
    _write_snap(os.path.join(hist, "scan_2026-07-01_13-00.json"), 71,
                [_make_signal("OLD1")])
    se.run_evaluation()

    stats, text = se.evaluate_and_format()
    assert stats.get("cumulative") is True
    assert stats["signals_evaluated"] == 1
    assert "копленная" in text  # «Накопленная»


def test_preexisting_twin_not_reprocessed(tmp_path, monkeypatch):
    hist = _setup_history(tmp_path, monkeypatch)
    _write_snap(os.path.join(hist, "scan_2026-06-01_13-00.json"), 101,
                [_make_signal("PREV")])
    with open(os.path.join(hist, "scan_2026-06-01_13-00_evaluated.json"), "w",
              encoding="utf-8") as f:
        json.dump({"timestamp": "2026-06-01T13:00:00", "top_picks": [
            {**_make_signal("PREV"), "evaluation": {
                "status": "evaluated", "entry_price": 10, "hit_target": True,
                "horizons": {"d5": 1.5, "d10": 3.0, "d20": 5.0},
                "max_move_pct": 6.0,
            }},
        ]}, f)

    stats = se.run_evaluation()
    assert stats["snapshots_processed"] == 0  # уже оценён

    cum = se.aggregate_evaluated_stats()
    assert cum["signals_evaluated"] == 1
    assert cum["hit_target_count"] == 1


def test_benchmark_alpha_in_stats_and_report(tmp_path, monkeypatch):
    """Бенчмарк QQQ сохраняется в twin и попадает в отчёт (α, обогнали)."""
    hist = _setup_history(tmp_path, monkeypatch)
    _write_snap(os.path.join(hist, "scan_2026-07-01_13-00.json"), 71,
                [_make_signal("OLD1")])

    stats = se.run_evaluation()
    text = se.format_evaluation_report(stats)

    # сигнал: entry 10 → d5 close 14 = +40%; QQQ: base 10, d5 close 12.5 = +25%
    assert stats["avg_benchmark"]["d5"] == 25.0
    assert stats["alpha"]["d5"] == 15.0
    assert stats["beat_benchmark_pct"]["d5"] == 100.0
    assert "QQQ: +25.0%" in text
    assert "α: +15.0%" in text
    assert "обогнали: 100%" in text
    # benchmark персистится в twin — накопленная статистика тоже с ним
    twin = os.path.join(hist, "scan_2026-07-01_13-00_evaluated.json")
    with open(twin, encoding="utf-8") as f:
        ev = json.load(f)["top_picks"][0]["evaluation"]
    assert ev["benchmark"]["d5"] == 25.0

    stats2, text2 = se.evaluate_and_format()
    assert stats2["alpha"]["d5"] == 15.0  # cumulative без новых запросов
    assert "α:" in text2


def test_dedup_same_setup_within_7_days(tmp_path, monkeypatch):
    """Тот же тикер через 3 дня — тот же сетап: в статистику идёт один раз."""
    hist = _setup_history(tmp_path, monkeypatch)
    _write_snap(os.path.join(hist, "scan_a_2026-06-19_13-00.json"), 83,
                [_make_signal("DUP")])
    _write_snap(os.path.join(hist, "scan_b_2026-06-22_13-00.json"), 80,
                [_make_signal("DUP")])

    stats = se.run_evaluation()
    assert stats["signals_seen"] == 2      # оба сигнала оценивались
    assert stats["total_signals"] == 1     # но в статистике один
    assert stats["signals_evaluated"] == 1


def test_dedup_resets_after_window(tmp_path, monkeypatch):
    """Тот же тикер через 10 дней — уже новый сетап, считаем оба."""
    hist = _setup_history(tmp_path, monkeypatch)
    _write_snap(os.path.join(hist, "scan_a_2026-06-01_13-00.json"), 101,
                [_make_signal("DUP")])
    _write_snap(os.path.join(hist, "scan_b_2026-06-11_13-00.json"), 91,
                [_make_signal("DUP")])

    stats = se.run_evaluation()
    assert stats["total_signals"] == 2
    assert stats["signals_evaluated"] == 2


def test_zone_breakdown(tmp_path, monkeypatch):
    """Разбивка по EMA-зонам: A быстрее C, ET-only без swing не попадает в зоны."""
    hist = _setup_history(tmp_path, monkeypatch)
    sig_a = _make_signal("ZONA", zone="A")
    sig_c = _make_signal("ZONC", zone="C")
    et_only = {  # нет swing-блока — в зоны не попадает, в общую статистику да
        "ticker": "ETON", "price": 10.0, "n_modules": 1, "combo_label": "ET",
    }
    _write_snap(os.path.join(hist, "scan_2026-07-01_13-00.json"), 71,
                [sig_a, sig_c, et_only])

    stats = se.run_evaluation()
    zb = stats["zone_breakdown"]

    assert zb["A"]["count"] == 1
    assert zb["C"]["count"] == 1
    # ZONA растёт быстрее ZONC → зона A в плюсе относительно C
    assert zb["A"]["d5"]["avg_pnl"] > zb["C"]["d5"]["avg_pnl"]
    # α для зоны A положительный (обогнала QQQ), для C тоже, но меньше
    assert zb["A"]["d5"]["avg_alpha"] > zb["C"]["d5"]["avg_alpha"]
    # ET-only: в общей статистике, но не в зонах
    assert stats["total_signals"] == 3
    assert stats["signals_evaluated"] == 3
    assert sum(z["count"] for z in zb.values()) == 2

    text = se.format_evaluation_report(stats)
    assert "📍 P&L по зоне входа" in text
    assert "Зона A" in text or "A (у EMA" in text
    assert "n=1" in text
    # предупреждение не должно срабатывать при n<5
    assert "кандидат на фильтр" not in text


def test_exit_rules(tmp_path, monkeypatch):
    """Лимит-выход: дошёл до уровня → ровно +level, не дошёл → pnl d20."""
    hist = _setup_history(tmp_path, monkeypatch)
    with open(os.path.join(hist, "scan_2026-07-01_13-00_evaluated.json"),
              "w", encoding="utf-8") as f:
        json.dump({"timestamp": "2026-07-01T13:00:00", "top_picks": [
            {"ticker": "LIM", "price": 10.0, "n_modules": 2, "swing": {"ema_zone": "A"},
             "evaluation": {"status": "evaluated", "entry_price": 10, "hit_target": False,
                            "max_move_pct": 12.0,
                            "horizons": {"d5": 1.0, "d10": 2.0, "d20": -4.0}}},
            {"ticker": "NOLIM", "price": 10.0, "n_modules": 2, "swing": {"ema_zone": "B"},
             "evaluation": {"status": "evaluated", "entry_price": 10, "hit_target": False,
                            "max_move_pct": 3.0,
                            "horizons": {"d5": 1.0, "d10": 1.5, "d20": -2.0}}},
        ]}, f)

    stats = se.aggregate_evaluated_stats()
    rules = {r["level"]: r for r in stats["exit_rules"]}
    assert rules[5]["reached_pct"] == 50.0    # до +5 дошёл только LIM (12)
    assert rules[5]["expectancy"] == 1.5      # исходы: [5, -2]
    assert rules[10]["reached_pct"] == 50.0
    assert rules[10]["expectancy"] == 4.0     # исходы: [10, -2]
    assert rules[10]["median"] == 4.0
    assert rules[20]["expectancy"] == -3.0    # исходы: [-4, -2]

    # Кросс с зоной A: только LIM
    rules_a = {r["level"]: r for r in stats["exit_rules_A"]}
    assert rules_a[10]["n"] == 1
    assert rules_a[10]["reached_pct"] == 100.0
    assert rules_a[10]["expectancy"] == 10.0
    assert rules_a[20]["expectancy"] == -4.0

    text = se.format_evaluation_report(stats)
    assert "Лимит-выход" in text
    assert "дошли 50.0%" in text
    # подвыборка зоны A малая (n<5) — в отчёт не выводится, чтобы не шуметь
    assert "Только зона A" not in text


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    class _FakeMonkeypatch:
        def chdir(self, p):
            os.chdir(p)
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    fns = [test_fresh_snapshots_skipped_and_counted, test_old_snapshots_evaluated_empty_marked,
           test_partial_snapshot_skipped_and_marked,
           test_report_lines, test_cumulative_fallback, test_preexisting_twin_not_reprocessed,
           test_benchmark_alpha_in_stats_and_report,
           test_dedup_same_setup_within_7_days, test_dedup_resets_after_window,
           test_zone_breakdown, test_exit_rules]
    failed = 0
    orig_cwd = os.getcwd()
    for fn in fns:
        with tempfile.TemporaryDirectory() as td:
            mp = _FakeMonkeypatch()
            try:
                fn(Path(td), mp)
                print(f"PASS {fn.__name__}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {fn.__name__}: {e}")
            finally:
                os.chdir(orig_cwd)  # иначе Windows не удалит tmp (он = CWD)
    sys.exit(1 if failed else 0)
