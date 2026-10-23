"""Безрисковая ставка платформы (core/data/risk_free.py): ряд доходности коротких ОФЗ RUGBITR1Y,
до 11.01.2011 — RGBEY, до 30.12.2002 — первое значение. Сеть не нужна: файл сборщика — во временной
папке, ISS — подменённый get_json."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from core import config
from core.analytics import ranking
from core.data import iss, risk_free as rf

# Маленький файл сборщика (доли годовых). Нуль и None отбрасываются — действует прежняя публикация.
ROWS = [["2002-12-30", 0.10], ["2003-01-10", 0.12], ["2003-01-12", 0.0], ["2003-01-13", None],
        ["2011-01-11", 0.05], ["2011-01-20", 0.06]]


def _write_file(d, rows=ROWS, **extra):
    js = {"id": "risk_free", "unit": "доля годовых", "splice_date": "2011-01-11", "source": "тест",
          "data": rows, **extra}
    (d / "risk_free.json").write_text(json.dumps(js), encoding="utf-8")


@pytest.fixture
def no_iss(monkeypatch):
    """Любое обращение к ISS — провал теста."""
    calls = []

    def fail(path, params=None, ttl=None, **kw):
        calls.append(path)
        raise AssertionError(f"обращение к ISS в тесте: {path}")
    monkeypatch.setattr(iss, "get_json", fail)
    rf._live.cache_clear()
    yield calls
    rf._live.cache_clear()


@pytest.fixture
def data_dir(tmp_path, monkeypatch, no_iss):
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", tmp_path)
    monkeypatch.setenv("IP_OFFLINE", "1")
    return tmp_path


@pytest.fixture
def with_file(data_dir):
    _write_file(data_dir)
    return data_dir


def _pairs(s: pd.Series):
    return [(d.date().isoformat(), round(float(v), 12)) for d, v in s.items()]


# ------------------------------------------------------------------ rate_series
def test_rate_series_full_drops_zero_and_none(with_file):
    s = rf.rate_series()
    assert s.name == "RF" and isinstance(s.index, pd.DatetimeIndex)
    assert _pairs(s) == [("2002-12-30", 0.10), ("2003-01-10", 0.12), ("2011-01-11", 0.05), ("2011-01-20", 0.06)]


def test_rate_series_start_between_publications(with_file):
    # 05.01.2003 — между 30.12.2002 (10 %) и 10.01.2003 (12 %): первой точкой ставится start со ставкой 10 %
    s = rf.rate_series("2003-01-05", "2003-01-31")
    assert _pairs(s) == [("2003-01-05", 0.10), ("2003-01-10", 0.12)]
    # 15.01.2003 — нуль 12.01 отброшен, действует 12 % с 10.01
    assert _pairs(rf.rate_series("2003-01-15", "2003-02-01")) == [("2003-01-15", 0.12)]


def test_rate_series_start_on_publication_not_duplicated(with_file):
    s = rf.rate_series("2003-01-10", None)
    assert _pairs(s) == [("2003-01-10", 0.12), ("2011-01-11", 0.05), ("2011-01-20", 0.06)]


def test_rate_series_start_before_2002_bfill(with_file):
    s = rf.rate_series("2000-01-01", "2003-01-05")
    assert _pairs(s) == [("2000-01-01", 0.10), ("2002-12-30", 0.10)]


def test_rate_series_end_cut(with_file):
    assert _pairs(rf.rate_series(None, "2003-01-09")) == [("2002-12-30", 0.10)]
    assert _pairs(rf.rate_series("2011-01-12", "2011-01-19")) == [("2011-01-12", 0.05)]


def test_rate_series_start_after_end_empty(with_file):
    assert rf.rate_series("2011-01-20", "2011-01-01").empty


def test_rate_series_values_are_fractions(with_file):
    s = rf.rate_series()
    assert (s > 0).all() and (s < 1).all()


# ------------------------------------------------------------------ mean_rate / time_mean
def test_mean_rate_calendar_days(with_file):
    # 05..09.01.2003 — 10 % (5 дней), 10..14.01 — 12 % (5 дней; нуль 12.01 отброшен)
    assert rf.mean_rate("2003-01-05", "2003-01-14") == pytest.approx(0.11, abs=1e-12)
    # 01..10.01.2011 — 12 % (10 дн.), 11..19.01 — 5 % (9 дн.), 20..31.01 — 6 % (12 дн.)
    assert rf.mean_rate("2011-01-01", "2011-01-31") == pytest.approx((1.2 + 0.45 + 0.72) / 31, abs=1e-12)


def test_mean_rate_before_first_date_bfill(with_file):
    assert rf.mean_rate("2002-12-25", "2002-12-31") == pytest.approx(0.10, abs=1e-12)


def test_mean_rate_single_day_and_empty_interval(with_file):
    assert rf.mean_rate("2011-01-15", "2011-01-15") == pytest.approx(0.05)
    assert math.isnan(rf.mean_rate("2011-01-15", "2011-01-14"))


def test_time_mean_empty_series_nan():
    assert math.isnan(rf.time_mean(pd.Series(dtype=float), "2020-01-01", "2020-12-31"))


def test_ranking_mean_rate_is_time_mean():
    rate = pd.Series([0.07, 0.21], index=pd.to_datetime(["2016-01-01", "2022-01-01"]))
    # 2021-12-30..2022-01-02: 2 дня по 7 %, 2 дня по 21 %
    assert ranking.mean_rate(rate, "2021-12-30", "2022-01-02") == pytest.approx(0.14, abs=1e-12)
    assert ranking.mean_rate(rate, "2021-12-30", "2022-01-02") == rf.time_mean(rate, "2021-12-30", "2022-01-02")


# ------------------------------------------------------------------ describe / current
def test_describe_before_data_and_rgbey(with_file):
    d = rf.describe("2000-01-01", "2003-01-14")
    assert d["uses_rgbey"] is True and d["before_data"] is True
    assert d["first_date"] == "2002-12-30" and d["splice_date"] == "2011-01-11"
    assert d["last"] == pytest.approx(0.12) and d["last_date"] == "2003-01-10"
    assert d["label"].startswith(rf.LABEL)
    assert "до 11.01.2011 — " + rf.LABEL_RGBEY in d["label"]
    assert "до 30.12.2002 — первое известное значение" in d["label"]
    # 2000-01-01..2003-01-09 — 10 % (1105 дн.), 10..14.01.2003 — 12 % (5 дн.)
    days = (pd.Timestamp("2003-01-14") - pd.Timestamp("2000-01-01")).days + 1
    assert d["mean"] == pytest.approx((0.10 * (days - 5) + 0.12 * 5) / days, abs=1e-12)


def test_describe_short_ofz_only(with_file):
    d = rf.describe("2011-01-11", "2011-01-31")
    assert d["uses_rgbey"] is False and d["before_data"] is False
    assert d["label"] == rf.LABEL
    assert d["last"] == pytest.approx(0.06) and d["last_date"] == "2011-01-20"
    assert d["mean"] == pytest.approx((0.05 * 9 + 0.06 * 12) / 21, abs=1e-12)
    assert d["source"] == "тест"


def test_describe_whole_series(with_file):
    d = rf.describe()
    assert d["uses_rgbey"] is True and d["before_data"] is False
    assert "первое известное значение" not in d["label"]
    assert d["last_date"] == "2011-01-20"


def test_current(with_file):
    assert rf.current() == (pytest.approx(0.06), "2011-01-20")


def test_file_without_splice_date_no_rgbey(data_dir):
    """Склейки нет (RGBEY пуст, splice_date = None): ряд только из коротких ОФЗ, подписи про RGBEY нет."""
    _write_file(data_dir, splice_date=None)
    d = rf.describe("2003-01-01", "2003-01-31")
    assert d["splice_date"] is None and d["uses_rgbey"] is False and d["before_data"] is False
    assert d["label"] == rf.LABEL
    d0 = rf.describe("2000-01-01", "2003-01-31")
    assert d0["uses_rgbey"] is False and d0["before_data"] is True
    assert "RGBEY" not in d0["label"] and "до 30.12.2002 — первое известное значение" in d0["label"]
    assert "RGBEY" not in rf.describe()["label"]


# ------------------------------------------------------------------ недоступность
def test_offline_without_file_raises(data_dir, no_iss):
    with pytest.raises(rf.RiskFreeUnavailable, match="IP_OFFLINE"):
        rf.rate_series("2020-01-01", "2020-12-31")
    for fn in (rf.current, lambda: rf.mean_rate("2020-01-01", "2020-02-01"), rf.describe):
        with pytest.raises(rf.RiskFreeUnavailable):
            fn()
    assert no_iss == []


@pytest.mark.parametrize("rows", [[], [["2020-01-01", 0.0], ["2020-02-01", -0.01]]])
def test_offline_empty_or_zero_file_raises(data_dir, rows):
    _write_file(data_dir, rows=rows)
    with pytest.raises(rf.RiskFreeUnavailable):
        rf.rate_series()


def test_broken_file_offline_raises(data_dir):
    (data_dir / "risk_free.json").write_text("{не json", encoding="utf-8")
    with pytest.raises(rf.RiskFreeUnavailable):
        rf.current()


def test_file_present_online_does_not_touch_iss(with_file, monkeypatch, no_iss):
    monkeypatch.delenv("IP_OFFLINE", raising=False)
    assert rf.current()[1] == "2011-01-20"
    assert no_iss == []


# ------------------------------------------------------------------ сборка с ISS (build)
SHORT = [("2010-12-30", None), ("2011-01-11", 6.5), ("2011-01-12", 0.0), ("2011-01-13", 6.6)]
LONG = [("2002-12-30", 13.59), ("2009-01-15", 0.0), ("2010-12-30", 8.0), ("2011-01-11", 8.1), ("2012-03-02", 0.0)]


class FakeISS:
    """get_json по пути ISS: RUGBITR1Y (engine stock, YIELD) и RGBEY (engine state, CLOSE); фильтр from/till."""

    def __init__(self, short=SHORT, long=LONG):
        self.short, self.long, self.calls = short, long, []

    def __call__(self, path, params=None, ttl=None, **kw):
        params = params or {}
        self.calls.append((path, dict(params)))
        if path == "/history/engines/stock/markets/index/securities/RUGBITR1Y":
            rows, field = self.short, "YIELD"
        elif path == "/history/engines/state/markets/index/securities/RGBEY":
            rows, field = self.long, "CLOSE"
        else:
            raise AssertionError(f"неожиданный путь ISS: {path}")
        assert params["history.columns"] == f"TRADEDATE,{field}"
        f, t = params.get("from"), params.get("till")
        rows = [r for r in rows if (not f or r[0] >= f) and (not t or r[0] <= t)]
        if params.get("start", 0):
            rows = []
        return {"history": {"columns": ["TRADEDATE", field], "data": [list(r) for r in rows]},
                "history.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"], "data": [[0, len(rows), 100]]}}


def test_index_history_engine_and_field(monkeypatch):
    fake = FakeISS()
    monkeypatch.setattr(iss, "get_json", fake)
    s = iss.index_history("RGBEY", "2000-01-01", "2010-12-31", engine="state", field="CLOSE")
    assert list(s.index.strftime("%Y-%m-%d")) == ["2002-12-30", "2009-01-15", "2010-12-30"]
    y = iss.index_history("RUGBITR1Y", "2000-01-01", "2011-12-31", engine="stock", field="YIELD")
    assert list(y.index.strftime("%Y-%m-%d")) == ["2011-01-11", "2011-01-12", "2011-01-13"]   # None отброшен
    assert y.iloc[0] == pytest.approx(6.5)


def test_build_splices_rgbey_and_short_ofz(monkeypatch):
    fake = FakeISS()
    monkeypatch.setattr(iss, "get_json", fake)
    s = rf.build()
    assert s.name == "RF"
    assert _pairs(s) == [("2002-12-30", 0.1359), ("2010-12-30", 0.08), ("2011-01-11", 0.065), ("2011-01-13", 0.066)]
    assert s.attrs == {"splice_date": "2011-01-11", "rgbey_first": "2002-12-30", "rgbey_till": "2010-12-30"}
    # RGBEY запрашивается только до склейки
    long_calls = [p for path, p in fake.calls if "RGBEY" in path]
    assert long_calls and all(p["till"] == "2011-01-11" for p in long_calls)
    meta = rf.file_meta(s)
    assert meta["splice_date"] == "2011-01-11"
    assert [x["id"] for x in meta["sources"]] == ["RGBEY", "RUGBITR1Y"]
    assert meta["sources"][0]["from"] == "2002-12-30" and meta["sources"][0]["till"] == "2010-12-30"
    assert meta["sources"][1]["from"] == "2011-01-11"


def test_build_without_short_yield_raises(monkeypatch):
    monkeypatch.setattr(iss, "get_json", FakeISS(short=[("2011-01-11", None), ("2011-01-12", 0.0)]))
    with pytest.raises(rf.RiskFreeUnavailable, match="RUGBITR1Y"):
        rf.build()


def test_build_without_rgbey(monkeypatch):
    monkeypatch.setattr(iss, "get_json", FakeISS(long=[]))
    s = rf.build()
    assert _pairs(s) == [("2011-01-11", 0.065), ("2011-01-13", 0.066)]
    assert s.attrs == {"splice_date": None, "rgbey_first": None, "rgbey_till": None}
    meta = rf.file_meta(s)
    assert meta["splice_date"] is None
    assert [x["id"] for x in meta["sources"]] == ["RUGBITR1Y"] and meta["sources"][0]["from"] == "2011-01-11"


def test_build_rgbey_only_zeros_no_splice(monkeypatch):
    monkeypatch.setattr(iss, "get_json", FakeISS(long=[("2009-01-15", 0.0)]))
    s = rf.build()
    assert _pairs(s) == [("2011-01-11", 0.065), ("2011-01-13", 0.066)]
    assert s.attrs["splice_date"] is None


def test_online_without_file_builds_from_iss(data_dir, monkeypatch):
    monkeypatch.delenv("IP_OFFLINE", raising=False)
    fake = FakeISS()
    monkeypatch.setattr(iss, "get_json", fake)
    rf._live.cache_clear()
    s = rf.rate_series("2011-01-12", None)
    assert _pairs(s) == [("2011-01-12", 0.065), ("2011-01-13", 0.066)]
    d = rf.describe("2005-01-01", "2011-01-13")
    assert d["splice_date"] == "2011-01-11" and d["uses_rgbey"] is True and d["before_data"] is False
    assert rf.current() == (pytest.approx(0.066), "2011-01-13")


def test_online_without_file_iss_down_raises(data_dir, monkeypatch):
    monkeypatch.delenv("IP_OFFLINE", raising=False)

    def down(*a, **k):
        raise iss.ISSError("ISS недоступен")
    monkeypatch.setattr(iss, "get_json", down)
    rf._live.cache_clear()
    with pytest.raises(rf.RiskFreeUnavailable, match="ISS недоступен"):
        rf.rate_series()


# ------------------------------------------------------------------ сборщик: источник risk_free
def test_collect_risk_free_writes_meta(tmp_path, monkeypatch):
    from scripts import collect_data as cd
    idx = pd.bdate_range("2002-12-30", periods=6000)
    s = pd.Series(np.linspace(0.12, 0.08, len(idx)), index=idx, name="RF")
    s.attrs.update({"splice_date": "2011-01-11", "rgbey_first": "2002-12-30", "rgbey_till": "2010-12-30"})
    monkeypatch.setattr(cd, "OUT", tmp_path)
    monkeypatch.setattr(rf, "build", lambda end=None: s)
    res = cd.collect("risk_free", cd.SOURCES["risk_free"])
    assert res["ok"] and res["rows"] == 6000
    js = json.loads((tmp_path / "risk_free.json").read_text(encoding="utf-8"))
    assert js["id"] == "risk_free" and js["unit"] == "доля годовых"
    assert js["splice_date"] == "2011-01-11" and js["first"] == "2002-12-30"
    assert [x["id"] for x in js["sources"]] == ["RGBEY", "RUGBITR1Y"]
    assert js["data"][0] == ["2002-12-30", 0.12]


def test_collect_risk_free_too_few_rows(tmp_path, monkeypatch):
    from scripts import collect_data as cd
    s = pd.Series([0.1, 0.11], index=pd.to_datetime(["2020-01-01", "2020-01-02"]))
    monkeypatch.setattr(cd, "OUT", tmp_path)
    monkeypatch.setattr(rf, "build", lambda end=None: s)
    with pytest.raises(ValueError, match="мало строк"):
        cd.collect("risk_free", cd.SOURCES["risk_free"])
    assert not (tmp_path / "risk_free.json").exists()
