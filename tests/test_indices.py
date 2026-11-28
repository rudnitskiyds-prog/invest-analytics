"""Раздел «Индексы»: core/analytics/indices.py, шаг сборщика indices, структура public/data/indices.json (без сети)."""
from __future__ import annotations

import json

import pandas as pd
import logging

import pytest

from core import config
from core.analytics import metrics as M
from core.analytics import indices as ix
from core.data import universe
from scripts import collect_data as cd

ITEM_KEYS = ["key", "name", "short", "class", "kind", "description", "since", "series", "ret_ytd", "ret_1y",
             "cagr_5y", "cagr_10y", "max_drawdown", "volatility_1y", "etf"]
CLASSES = {"Акции", "Облигации гос.", "Облигации корп.", "Золото", "Денежный рынок"}


AVAIL = [k for k in ix.INDEX_INFO if k not in ("IMOEX", "GLDRUB_TOM")]   # ключи, есть в фикстурах


def _fx(key: str) -> pd.Series:
    return universe._offline_series(key, "2008-01-01", None)


def _load(key: str) -> pd.Series:
    if key in ("IMOEX", "GLDRUB_TOM"):          # их нет в фикстурах
        raise KeyError(key)
    return _fx(key)


def test_build_on_fixtures():
    body, skipped = ix.build(_load, {"EQMX": "ВИМ – Индекс МосБиржи"})
    assert set(skipped) == {"IMOEX", "GLDRUB_TOM"} and "KeyError" in skipped["IMOEX"]
    items = body["items"]
    assert [i["key"] for i in items] == [k for k in ix.INDEX_INFO if k not in skipped]
    for it in items:
        assert [k for k in it if k != "etf_note"] == ITEM_KEYS
        assert it["class"] in CLASSES and it["kind"] in ("price", "total_return")
        assert it["series"][0][1] == 100 and len(it["series"]) <= 121
        assert it["max_drawdown"] <= 0
    assert body["as_of"] == max(i["series"][-1][0] for i in items)
    by = {i["key"]: i for i in items}
    assert by["MOEXEU"]["etf"] == [] and by["MOEXEU"]["kind"] == "price"
    assert by["MCFTR"]["kind"] == "total_return" and by["MCFTR"]["class"] == "Акции"
    assert by["MCFTR"]["etf"][0] == {"secid": "EQMX", "name": "ВИМ – Индекс МосБиржи"}   # имя из справочника
    assert all(f["name"] == f["secid"] for f in by["RGBITR"]["etf"])                      # нет в справочнике — тикер
    assert "RUCBITR" not in by and "RUCBTRNS" not in by


def test_build_item_hand_computed_short_series():
    # 24 месяца ровного роста на 1 % в месяц: ret_1y = 1.01^12 - 1, CAGR 5/10 лет — нет истории, волатильность 0
    idx = pd.date_range("2024-01-31", periods=24, freq="ME")
    s = pd.Series([100 * 1.01 ** i for i in range(24)], index=idx)
    it = ix.build_item("MCFTR", s, {})
    assert it["series"][0] == ["2024-01-31", 100.0] and len(it["series"]) == 24
    assert it["ret_1y"] == pytest.approx(1.01 ** 12 - 1, abs=1e-4)
    assert it["cagr_5y"] is None and it["cagr_10y"] is None
    assert it["max_drawdown"] == 0 and it["volatility_1y"] == pytest.approx(0, abs=1e-6)
    assert it["since"] == "2024-01-31"


def test_build_item_short_history_gives_nulls():
    idx = pd.date_range("2025-07-31", periods=6, freq="ME")
    it = ix.build_item("GOLD_CBR", pd.Series([100, 101, 99, 103, 104, 105.0], index=idx), {})
    assert it["ret_1y"] is None and it["cagr_5y"] is None and it["cagr_10y"] is None
    assert it["volatility_1y"] is None                 # меньше 12 месячных доходностей
    assert it["series"][0][1] == 100


def test_build_item_too_short_raises():
    with pytest.raises(ValueError):
        ix.build_item("MCFTR", pd.Series([1.0], index=pd.to_datetime(["2025-01-31"])), {})


def test_monthly_takes_last_observed_day():
    s = pd.Series([1, 2, 3, 4.0], index=pd.to_datetime(["2025-01-10", "2025-01-29", "2025-02-03", "2025-02-27"]))
    m = ix.monthly(s)
    assert list(m) == [2.0, 4.0] and list(m.index.strftime("%Y-%m-%d")) == ["2025-01-29", "2025-02-27"]


def test_etf_only_exchange_tickers():
    assert ix._etf("SBGB, SBMX", {"SBGB": "A"}) == [{"secid": "SBGB", "name": "A"}, {"secid": "SBMX", "name": "SBMX"}]
    assert ix._etf("", {}) == [] and ix._etf("Тинькофф Индекс МосБиржи", {}) == []


def test_build_skips_key_without_data():
    def load(key):
        if key == "MCFTR":
            return pd.Series(dtype=float)
        return _fx(key)
    body, skipped = ix.build(load, {})
    assert "MCFTR" in skipped and "MCFTR" not in [i["key"] for i in body["items"]]


def test_build_all_failed():
    body, skipped = ix.build(lambda k: (_ for _ in ()).throw(ConnectionError("down")), {})
    assert body == {"as_of": None, "items": []} and len(skipped) == len(ix.INDEX_INFO)


# ---------------------------------------------------------------- шаг сборщика
@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "OUT", tmp_path)
    monkeypatch.setattr(universe, "load_series", lambda key, start="2008-01-01", **kw: _load(key))
    return tmp_path


def test_collector_indices_step(out):
    assert cd.main(["--only", "indices"]) == 0
    d = json.loads((out / "indices.json").read_text(encoding="utf-8"))
    assert list(d) == ["updated", "source", "as_of", "items"] and d["source"] == cd.INDICES_SOURCE
    assert len(d["items"]) == 12 and d["as_of"]
    st = json.loads((out / "status.json").read_text(encoding="utf-8"))["indices"]
    assert st["ok"] and st["rows"] == 12 and st["skipped"] == 2 and st["last"] == d["as_of"]
    # без изменений данных updated не меняется
    first = d["updated"]
    assert cd.main(["--only", "indices"]) == 0
    assert json.loads((out / "indices.json").read_text(encoding="utf-8"))["updated"] == first


def test_collector_indices_below_threshold_keeps_old_file(out, monkeypatch):
    (out / "indices.json").write_text('{"old":1}')
    monkeypatch.setattr(universe, "load_series",
                        lambda key, start="2008-01-01", **kw: _fx(key) if key in AVAIL[:7] else _load("IMOEX"))
    assert cd.main(["--only", "indices"]) == 1                      # 7 < 8
    assert (out / "indices.json").read_text() == '{"old":1}'
    st = json.loads((out / "status.json").read_text(encoding="utf-8"))["indices"]
    assert not st["ok"] and "слишком мало" in st["error"]


def test_collector_indices_threshold_boundary(out, monkeypatch):
    keep = AVAIL[:8]
    monkeypatch.setattr(universe, "load_series",
                        lambda key, start="2008-01-01", **kw: _fx(key) if key in keep else _load("IMOEX"))
    assert cd.main(["--only", "indices"]) == 0


def test_collector_indices_uses_live_series_without_fallback(out, monkeypatch):
    seen = []
    monkeypatch.setattr(universe, "load_series",
                        lambda key, start="2008-01-01", **kw: seen.append(kw) or _load(key))
    cd.main(["--only", "indices"])
    assert seen and all(kw.get("fallback") is False for kw in seen)


def test_default_run_skips_indices_and_tinvest_bonds():
    assert cd.SOURCES["indices"]["default"] is False and cd.SOURCES["tinvest_bonds"]["default"] is False


def test_main_without_only_does_not_run_indices_nor_bonds(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "OUT", tmp_path)
    ran = []
    monkeypatch.setattr(cd, "collect_indices", lambda *a: ran.append("indices") or {"ok": True, "rows": 1, "last": "x"})
    monkeypatch.setattr(cd, "collect_tinvest", lambda name, spec, ctx: ran.append(name) or {"ok": True, "rows": 1, "last": "x"})
    for n, s in list(cd.SOURCES.items()):       # остальные источники — заглушки, чтобы не ходить в сеть
        if n not in ("indices",) and s.get("kind") not in ("tinvest",):
            monkeypatch.setitem(cd.SOURCES, n, {**s, "default": False})
    cd.main([])
    assert "indices" not in ran and "tinvest_bonds" not in ran and "tinvest_shares" in ran


# ---------------------------------------------------------------- файл public/data/indices.json
def test_public_indices_json_structure():
    p = config.ROOT / "public" / "data" / "indices.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert {"updated", "source", "as_of", "items"} <= set(d) and d["items"]
    assert pd.Timestamp(d["as_of"]) == max(pd.Timestamp(i["series"][-1][0]) for i in d["items"])
    keys = [i["key"] for i in d["items"]]
    assert len(keys) == len(set(keys)) and set(keys) <= set(ix.INDEX_INFO)
    for it in d["items"]:
        assert [k for k in it if k != "etf_note"] == ITEM_KEYS, it["key"]
        assert it["class"] in CLASSES and it["kind"] in ("price", "total_return")
        dates = [pd.Timestamp(x[0]) for x in it["series"]]
        assert all(a < b for a, b in zip(dates, dates[1:])), f"{it['key']}: даты не растут"
        assert all(isinstance(x[1], (int, float)) and x[1] > 0 for x in it["series"]), it["key"]
        assert it["series"][0][1] == 100 and len(it["series"]) >= 2
        assert it["max_drawdown"] <= 0
        for f in ("ret_ytd", "ret_1y", "cagr_5y", "cagr_10y", "volatility_1y"):
            assert it[f] is None or isinstance(it[f], (int, float))
        assert isinstance(it["etf"], list) and all(set(f) == {"secid", "name"} for f in it["etf"])
        assert it["description"] and it["short"] and it["since"] <= it["series"][0][0]


# ---------------------------------------------------------------- правки по ревью
def test_returns_use_daily_series_when_ends_mid_month():
    idx = pd.bdate_range("2024-01-01", "2026-11-27")
    raw = pd.Series([100 + 100 * i / (len(idx) - 1) for i in range(len(idx))], index=idx)
    it = ix.build_item("MCFTR", raw, {})
    pr = M.period_returns(raw)
    assert it["ret_1y"] == round(pr["1Y"]["ret"], 4) and it["ret_ytd"] == round(pr["YTD"]["ret"], 4)
    assert it["ret_1y"] == pytest.approx(0.2076, abs=1e-3) and abs(it["ret_1y"] - 0.2262) > 0.01   # не по месячному ряду
    assert it["cagr_5y"] is None                          # истории 2,9 года
    # просадка и ряд — по месячному
    assert it["series"][-1][0] == "2026-11-27"            # последний наблюдаемый день месяца
    long_idx = pd.bdate_range("2014-01-01", "2026-11-27")
    long = pd.Series([100 * 1.0004 ** i for i in range(len(long_idx))], index=long_idx)
    it2 = ix.build_item("MCFTR", long, {})
    assert it2["cagr_5y"] == round(M.period_returns(long)["5Y"]["cagr"], 4)
    assert it2["cagr_10y"] == round(M.period_returns(long)["10Y"]["cagr"], 4)


def test_etf_note_only_with_etf_and_known_key():
    base = pd.Series([100 + i for i in range(30)], index=pd.date_range("2024-01-31", periods=30, freq="ME"), dtype=float)
    it = ix.build_item("RUGBITR1Y", base, {})
    assert it["etf"] and it["etf_note"] == ix.ETF_NOTES["RUGBITR1Y"]
    assert "etf_note" not in ix.build_item("MCFTR", base, {})
    assert "etf_note" not in ix.build_item("MOEXEU", base, {})       # нет БПИФ


def test_collector_writes_skipped_keys(out):
    assert cd.main(["--only", "indices"]) == 0
    st = json.loads((out / "status.json").read_text(encoding="utf-8"))["indices"]
    assert set(st["skipped_keys"]) == {"IMOEX", "GLDRUB_TOM"} and st["skipped"] == 2
    assert all(isinstance(v, str) and v for v in st["skipped_keys"].values())


def test_host_available_bad_bundle_false_with_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(universe, "_HOST_STATE", {})
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("запрос не должен уйти"))
    monkeypatch.setenv("ISS_CA_BUNDLE", str(tmp_path / "nope.pem"))
    with caplog.at_level(logging.WARNING):
        assert universe.host_available("https://iss.example.test/x") is False
    assert any("ISS_CA_BUNDLE" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_tinvest_bonds_stub_valid():
    d = json.loads((config.ROOT / "public" / "data" / "tinvest_bonds.json").read_text(encoding="utf-8"))
    assert {"updated", "source", "data"} <= set(d) and d["data"] == [] and d["source"]
