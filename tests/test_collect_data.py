"""Сборщик данных: формат файлов и устойчивость к падению источника (без сети)."""
import json

import pandas as pd

from scripts import collect_data as cd


def test_collect_writes_files_and_keeps_old_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "OUT", tmp_path)
    idx = pd.bdate_range("2010-01-01", periods=3500)
    good = pd.Series(range(3500), index=idx, dtype=float)
    specs = {
        "a": {**cd.SOURCES["cbr_gold"], "load": lambda: good},
        "b": {**cd.SOURCES["cbr_gold"], "load": lambda: good},
    }
    monkeypatch.setattr(cd, "SOURCES", specs)
    assert cd.main([]) == 0
    a = json.loads((tmp_path / "a.json").read_text())
    assert a["data"][0] == ["2010-01-01", 0.0] and len(a["data"]) == 3500
    assert a["last"] == idx[-1].strftime("%Y-%m-%d")

    # источник «b» упал: старый файл остаётся, статус фиксирует ошибку и прежнюю дату
    def boom():
        raise ConnectionError("403 Forbidden")
    specs["b"] = {**specs["b"], "load": boom}
    before = (tmp_path / "b.json").read_text()
    assert cd.main([]) == 0
    assert (tmp_path / "b.json").read_text() == before
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["a"]["ok"] and not st["b"]["ok"]
    assert "403" in st["b"]["error"] and st["b"]["last"] == a["last"]

    # все источники упали — код возврата 1 (GitHub пришлёт уведомление)
    specs["a"] = {**specs["a"], "load": boom}
    assert cd.main([]) == 1


def test_too_short_answer_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "OUT", tmp_path)
    short = pd.Series([1.0, 2.0], index=pd.bdate_range("2025-01-01", periods=2))
    monkeypatch.setattr(cd, "SOURCES", {"x": {**cd.SOURCES["cbr_gold"], "load": lambda: short}})
    assert cd.main([]) == 1
    assert not (tmp_path / "x.json").exists()
