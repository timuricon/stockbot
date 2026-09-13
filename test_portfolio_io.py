"""
Тесты portfolio_io: атомарная запись, .bak, целостность данных.
Запуск:  python -m pytest tests/ -v
или без pytest:  python tests/test_portfolio_io.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portfolio.portfolio_io import save_portfolio, load_portfolio  # noqa: E402


def _sample():
    return {
        "currency": "USD",
        "approved": True,
        "holdings": [
            {"ticker": "ARCC", "type": "BDC", "current_value": 1000.0, "approved": True},
            {"ticker": "O",    "type": "REIT", "current_value": 500.0, "approved": False},
        ],
    }


def test_save_and_load_roundtrip(tmp_path):
    fp = str(tmp_path / "portfolio_b.json")
    assert save_portfolio(_sample(), fp) is True
    loaded = load_portfolio(fp)
    assert loaded["holdings"][0]["ticker"] == "ARCC"
    assert loaded["holdings"][1]["current_value"] == 500.0


def test_atomic_write_no_tmp_leftover(tmp_path):
    fp = str(tmp_path / "portfolio_b.json")
    save_portfolio(_sample(), fp)
    assert not os.path.exists(fp + ".tmp")  # tmp переименован, не остался
    assert json.load(open(fp, encoding="utf-8"))["currency"] == "USD"


def test_bak_created_on_second_save(tmp_path):
    fp = str(tmp_path / "portfolio_b.json")
    v1 = _sample()
    save_portfolio(v1, fp)
    v2 = _sample()
    v2["holdings"][0]["current_value"] = 2222.0
    save_portfolio(v2, fp)
    bak = json.load(open(fp + ".bak", encoding="utf-8"))
    assert bak["holdings"][0]["current_value"] == 1000.0  # предыдущая версия
    assert load_portfolio(fp)["holdings"][0]["current_value"] == 2222.0


def test_cyrillic_and_unicode_safe(tmp_path):
    fp = str(tmp_path / "portfolio_b.json")
    data = _sample()
    data["holdings"][0]["notes"] = "Заменён с X → Y, проверка ё"
    assert save_portfolio(data, fp) is True
    loaded = load_portfolio(fp)
    assert loaded["holdings"][0]["notes"] == "Заменён с X → Y, проверка ё"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    fns = [test_save_and_load_roundtrip, test_atomic_write_no_tmp_leftover,
           test_bak_created_on_second_save, test_cyrillic_and_unicode_safe]
    failed = 0
    for fn in fns:
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(Path(td))
                print(f"PASS {fn.__name__}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
