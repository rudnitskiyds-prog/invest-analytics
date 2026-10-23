"""Раздел «Портфели и стратегии»: core/analytics/portfolios.py, производный STRATEGIES, сборщик
(источник portfolios) и universe.load_series(fallback=False).

Сеть запрещена: демо-режим IP_OFFLINE=1 (месячные ряды tests/fixtures), либо синтетические ряды через
monkeypatch universe.load_series; public/data — копия во временном каталоге без portfolios.json
(иначе source="auto" читал бы файл сборщика из репозитория).
"""
from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

from core import config
from core.analytics import portfolios as pf
from core.analytics import ranking
from core.analytics.strategies import STRATEGIES
from core.data import public_data, universe

ROOT = Path(__file__).resolve().parent.parent
REAL_PUBLIC = ROOT / "public" / "data"
SECTOR_NO_DEMO = ["sector-oil-gas", "sector-finance", "sector-metals-mining", "sector-consumer",
                  "sector-chemicals", "sector-transport", "sector-it"]


# ====================================================================== общие фикстуры
@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    """Демо-режим, запрет сети, public/data — копия без portfolios.json, чистые кэши рядов."""
    monkeypatch.setenv("IP_OFFLINE", "1")
    calls = []

    def send(self, request, **kw):
        calls.append(request.url)
        raise requests.ConnectionError(f"сеть запрещена в тестах: {request.url}")
    monkeypatch.setattr(requests.Session, "send", send)

    pub = tmp_path / "pub"
    pub.mkdir()
    for f in REAL_PUBLIC.glob("*.json"):
        if f.name != "portfolios.json":
            shutil.copy(f, pub / f.name)
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", pub)
    monkeypatch.setattr(universe, "FALLBACK_USED", set())
    pf.clear_cache()
    yield pub
    pf.clear_cache()
    assert calls == [], f"тест обращался к сети: {calls}"


@pytest.fixture
def pub(_env):
    return _env


@pytest.fixture(scope="module")
def live_table():
    """Таблица живого расчёта на демо-данных (один раз на модуль — расчёт 18 портфелей)."""
    mp = pytest.MonkeyPatch()
    mp.setenv("IP_OFFLINE", "1")
    tmp = Path(__import__("tempfile").mkdtemp())
    for f in REAL_PUBLIC.glob("*.json"):
        if f.name != "portfolios.json":
            shutil.copy(f, tmp / f.name)
    mp.setattr(config, "PUBLIC_DATA_DIR", tmp)
    pf.clear_cache()
    try:
        df = pf.portfolios_table(source="live")
        reports = {pid: pf.portfolio_report(pid, source="live") for pid in df.index}
    finally:
        pf.clear_cache()
        mp.undo()
        shutil.rmtree(tmp, ignore_errors=True)
    return df, reports


REPORT_META = ("source", "updated", "stale", "stale_date")


def _strip(rep: dict) -> dict:
    """Отчёт без полей происхождения (source, updated, stale, stale_date)."""
    return {k: v for k, v in rep.items() if k not in REPORT_META}


def demo(key: str) -> pd.Series:
    return universe._offline_frame()[key].dropna()


# ====================================================================== каталог и STRATEGIES
EXPECTED_STRATEGIES = {
    "Лежебока плюс (Спирин)": {"MCFTR": 0.30, "CORP_CHAIN": 0.30, "GOLD_CBR": 0.30, "RUONIA": 0.10},
    "Портфель 60/40": {"MCFTR": 0.60, "RGBITR": 0.40},
    "Постоянный портфель (Браун)": {"MCFTR": 0.25, "RUGBITR10Y": 0.25, "GOLD_CBR": 0.25, "RUONIA": 0.25},
    "Всепогодный портфель (Далио)": {"MCFTR": 0.30, "RUGBITR10Y": 0.40, "RUGBITR5+": 0.15, "GOLD_CBR": 0.075,
                                     "MEEUTR": 0.075},
    "Портфель 40/40/20": {"MCFTR": 0.40, "CORP_CHAIN": 0.40, "GOLD_CBR": 0.20},
    "Завещание Баффетта 90/10": {"MCFTR": 0.90, "RUGBITR1Y": 0.10},
}


def _parse_js_strategies() -> dict:
    """web/lib/strategies.js -> {имя: {weights, rebalance}} (простой разбор объектного литерала)."""
    text = (ROOT / "web" / "lib" / "strategies.js").read_text(encoding="utf-8")
    body = text.split("export const STRATEGIES = {", 1)[1].split("\n};", 1)[0]
    out = {}
    for m in re.finditer(r"'([^']+)':\s*\{\s*weights:\s*\{([^}]*)\},\s*rebalance:\s*'([^']+)'", body):
        name, wtxt, reb = m.groups()
        w = {}
        for k, v in re.findall(r"'?([A-Z0-9_+]+)'?\s*:\s*([0-9.]+)", wtxt):
            w[k] = float(v)
        out[name] = {"weights": w, "rebalance": reb}
    return out


def test_strategies_six_expected_dalio_with_meeutr():
    assert list(STRATEGIES) == list(EXPECTED_STRATEGIES)
    for name, w in EXPECTED_STRATEGIES.items():
        assert STRATEGIES[name]["weights"] == w, name
        assert STRATEGIES[name]["rebalance"] == "A", name
        assert STRATEGIES[name]["note"], name
    assert "MOEXEU" not in STRATEGIES["Всепогодный портфель (Далио)"]["weights"]
    assert "MEEUTR" in STRATEGIES["Всепогодный портфель (Далио)"]["note"]


def test_strategies_match_web_js():
    js = _parse_js_strategies()
    assert list(js) == list(STRATEGIES)
    for name, s in STRATEGIES.items():
        assert js[name]["weights"] == pytest.approx(s["weights"]), name
        assert list(js[name]["weights"]) == list(s["weights"]), name
        assert js[name]["rebalance"] == s["rebalance"], name


def test_strategies_derived_from_catalog():
    by_strategy = {p["strategy"]: p for p in pf.PORTFOLIOS.values() if p.get("strategy")}
    assert set(by_strategy) == set(STRATEGIES)
    for name, s in STRATEGIES.items():
        assert s["weights"] == by_strategy[name]["weights"]
        assert s["weights"] is not by_strategy[name]["weights"]   # копия, правка в бэктесте не портит каталог


def test_catalog_shape():
    assert len(pf.PORTFOLIOS) == 18
    groups = [p["group"] for p in pf.PORTFOLIOS.values()]
    assert groups.count("Ленивые портфели") == 10 and groups.count("Тематические") == 8
    for pid, p in pf.PORTFOLIOS.items():
        assert re.fullmatch(r"[a-z0-9-]+", pid), pid
        assert p["group"] in pf.GROUPS
        assert p["rebalance"] in pf.REBALANCE_LABELS, pid
        assert math.isclose(sum(p["weights"].values()), 1.0, abs_tol=1e-12), pid
        assert all(w > 0 for w in p["weights"].values()), pid
        assert p["name"] and p["description"] and p["rules"], pid


def test_asset_keys_known_and_replicate_present():
    known = set(universe.CATALOG) | set(pf.EXTRA_ASSETS)
    for pid, p in pf.PORTFOLIOS.items():
        for k in [*p["weights"], p["benchmark"]]:
            assert k in known, f"{pid}: {k} нет в CATALOG/EXTRA_ASSETS"
        for k in p["weights"]:
            assert k in pf.REPLICATE, f"{pid}: у актива {k} нет записи REPLICATE"
        assert [r["asset"] for r in p["replicate"]] == list(p["weights"])
    assert set(pf.EXTRA_ASSETS).isdisjoint(universe.CATALOG)


def test_asset_short_name_covers_catalog():
    for k in [*universe.CATALOG, *pf.EXTRA_ASSETS]:
        s = pf.asset_short_name(k)
        assert s and s != k, f"{k}: нет короткого названия"
    assert pf.asset_short_name("MCFTR") == "Акции (MCFTR)"
    assert pf.asset_short_name("MEEUTR") == "Электроэнергетика (MEEUTR)"
    assert pf.asset_short_name("НЕТ_ТАКОГО") == "НЕТ_ТАКОГО"
    assert pf.asset_name("MEEUTR") == "Индекс электроэнергетики полной доходности"
    assert pf.asset_name("MEOGTR") == pf.EXTRA_ASSETS["MEOGTR"]


def test_unknown_portfolio_raises_keyerror():
    with pytest.raises(KeyError):
        pf.portfolio_values("нет-такого", source="live")
    with pytest.raises(KeyError):
        pf.portfolio_report("нет-такого", source="live")
    with pytest.raises(ValueError):
        pf.portfolios_table(source="bad")
    with pytest.raises(ValueError):
        pf.risk_return_points("3Y", source="live")


# ====================================================================== MEEUTR в демо-данных
def test_meeutr_fixture():
    s = demo("MEEUTR")
    assert s.index[0] == pd.Timestamp("2013-12-31") and s.index[-1] == pd.Timestamp("2025-12-31")
    assert s.iloc[0] == pytest.approx(1032.39) and s.iloc[-1] == pytest.approx(3185.85)
    assert len(s) == 145 and (s > 0).all()


# ====================================================================== стоимость портфеля (демо)
@pytest.mark.parametrize("pid", ["lazy-plus", "60-40", "all-weather-dalio", "dividend", "sector-power"])
def test_values_start_at_latest_first_date(pid):
    v = pf.portfolio_values(pid, source="live")
    first = max(demo(k).index[0] for k in pf.PORTFOLIOS[pid]["weights"])
    assert v.index[0] == first
    assert v.iloc[0] == pytest.approx(100_000.0)
    assert v.name == pid and v.attrs["freq"] == "M"
    assert pf.portfolio_since(pid, source="live") == str(first.date())


def test_values_dalio_since_meeutr():
    v = pf.portfolio_values("all-weather-dalio", source="live")
    assert v.index[0] == pd.Timestamp("2013-12-31")


def test_values_single_asset_is_scaled_index():
    """100 % RGBITR без ребалансировки: стоимость = 100 000 × RGBITR_t / RGBITR_0."""
    v = pf.portfolio_values("ofz-only", source="live")
    r = demo("RGBITR")
    exp = r / r.iloc[0] * 100_000
    pd.testing.assert_series_equal(v, exp.loc[v.index], check_names=False, rtol=1e-12)


def test_values_60_40_by_hand():
    """60/40, ребалансировка в конце декабря: до первой ребалансировки — дрейф долей,
    после — снова 60/40 от стоимости на дату ребалансировки."""
    v = pf.portfolio_values("60-40", source="live")
    mc, rg = demo("MCFTR"), demo("RGBITR")
    d0, d1, d2 = pd.Timestamp("2010-12-31"), pd.Timestamp("2011-12-31"), pd.Timestamp("2012-06-30")
    v1 = 100_000 * (0.6 * mc[d1] / mc[d0] + 0.4 * rg[d1] / rg[d0])
    v2 = v1 * (0.6 * mc[d2] / mc[d1] + 0.4 * rg[d2] / rg[d1])
    assert v[d1] == pytest.approx(v1, rel=1e-12)
    assert v[d2] == pytest.approx(v2, rel=1e-12)


def test_values_start_end_slice():
    v = pf.portfolio_values("60-40", start="2015-01-01", end="2016-12-31", source="live")
    assert v.index[0] >= pd.Timestamp("2015-01-01") and v.index[-1] <= pd.Timestamp("2016-12-31")
    assert v.iloc[0] == pytest.approx(100_000.0)


# ====================================================================== синтетика: точные значения
SYN_DATES = pd.date_range("2020-12-31", "2022-06-30", freq="ME")


def _syn_series():
    mc = pd.Series(100.0, index=SYN_DATES)
    mc[mc.index >= "2021-12-31"] = 200.0
    mc[pd.Timestamp("2022-06-30")] = 100.0
    rg = pd.Series(100.0, index=SYN_DATES)
    return {"MCFTR": mc, "RGBITR": rg}


@pytest.fixture
def synthetic(monkeypatch):
    data = _syn_series()

    def fake(key, start="2008-01-01", end=None, total_return=False, fallback=True):
        if key not in data:
            raise KeyError(f"{key}: нет в синтетике")
        return data[key].loc[start:end].rename(key)
    monkeypatch.setattr(universe, "load_series", fake)
    pf.clear_cache()
    return data


def test_synthetic_60_40_values_and_rebalance_log(synthetic):
    """100 000: 60 000 акции + 40 000 ОФЗ. Акции ×2 к 31.12.2021 -> 160 000, доли до ребалансировки
    75/25; после — 96 000 / 64 000. Акции /2 к 30.06.2022 -> 48 000 + 64 000 = 112 000."""
    v = pf.portfolio_values("60-40", source="live")
    assert v[pd.Timestamp("2021-12-31")] == pytest.approx(160_000)
    assert v.iloc[-1] == pytest.approx(112_000)
    rep = pf.portfolio_report("60-40", source="live")
    assert rep["final_value"] == pytest.approx(112_000)
    assert rep["since"] == "2020-12-31" and rep["last_date"] == "2022-06-30"
    assert rep["freq"] == "M"
    log = rep["rebalance_log"]
    assert [x["date"] for x in log] == ["2021-12-31"]
    assert log[0]["before"] == pytest.approx({"MCFTR": 0.75, "RGBITR": 0.25})
    assert log[0]["after"] == pytest.approx({"MCFTR": 0.6, "RGBITR": 0.4})
    assert rep["current_weights"] == pytest.approx({"MCFTR": 48 / 112, "RGBITR": 64 / 112})
    # истории меньше 5 и 10 лет — коэффициенты за эти окна None
    assert rep["metrics"]["5Y"] is None and rep["metrics"]["10Y"] is None
    assert rep["metrics"]["1Y"] is not None and rep["metrics"]["MAX"] is not None
    assert rep["drawdowns"]["max"] == pytest.approx(112 / 160 - 1)


def test_synthetic_table_rank_none_short_history(synthetic):
    """Синтетика 1,5 года: в таблице только портфели с данными, ранг None (история < 5 лет)."""
    df = pf.portfolios_table(source="live")
    assert set(df.index) == {"60-40", "ofz-only"}
    assert set(df.attrs["skipped"]) == set(pf.PORTFOLIOS) - {"60-40", "ofz-only"}
    assert df["rank"].isna().all()
    assert (df["rank_window"] == "5Y").all()
    assert list(df.columns) == [*pf.TABLE_COLUMNS, "stale"]
    assert not df["stale"].any()
    assert df.attrs["source"] == "live" and df.attrs["updated"] is None


# ====================================================================== секторные портфели в демо
def test_sector_without_demo_skipped(live_table):
    df, _ = live_table
    skipped = df.attrs["skipped"]
    assert set(skipped) == set(SECTOR_NO_DEMO)
    for pid in SECTOR_NO_DEMO:
        assert pf.PORTFOLIOS[pid]["weights"] and list(pf.PORTFOLIOS[pid]["weights"])[0] in skipped[pid]
    assert "sector-power" in df.index        # MEEUTR есть в демо-данных
    assert len(df) == 11


@pytest.mark.parametrize("pid", SECTOR_NO_DEMO)
def test_sector_without_demo_report_unavailable(pid):
    with pytest.raises(pf.PortfolioUnavailable):
        pf.portfolio_report(pid, source="live")
    with pytest.raises(pf.PortfolioUnavailable):
        pf.portfolio_values(pid, source="live")
    assert pf.portfolio_since(pid, source="live") is None


# ====================================================================== комиссия
def _rows(*pairs):
    return [{"weight": w, "funds": ([{"fee": f}] if f != "nofund" else [])} for w, f in pairs]


def test_portfolio_fee_normalized_by_coverage():
    # 0,6 × 0,5 % + 0,4 × (нет фонда) -> 0,5 %, покрытие 0,6
    fee, cov = pf.portfolio_fee("60-40", _rows((0.6, 0.005), (0.4, "nofund")))
    assert fee == pytest.approx(0.005) and cov == pytest.approx(0.6)
    # 0,5 × 1 % + 0,3 × 0,2 % + 0,2 × (комиссия неизвестна) -> (0,005 + 0,0006) / 0,8 = 0,7 %
    fee, cov = pf.portfolio_fee("60-40", _rows((0.5, 0.01), (0.3, 0.002), (0.2, None)))
    assert fee == pytest.approx(0.007) and cov == pytest.approx(0.8)
    # все комиссии известны — обычное средневзвешенное
    fee, cov = pf.portfolio_fee("60-40", _rows((0.6, 0.01), (0.4, 0.005)))
    assert fee == pytest.approx(0.008) and cov == pytest.approx(1.0)


def test_portfolio_fee_none_without_funds():
    assert pf.portfolio_fee("x", _rows((1.0, "nofund"))) == (None, 0.0)
    assert pf.portfolio_fee("x", _rows((0.5, None), (0.5, "nofund"))) == (None, 0.0)
    assert pf.portfolio_fee("x", []) == (None, 0.0)


def test_replicate_and_fee_from_public_data(monkeypatch):
    """Комиссия первого фонда актива: EQMX 0,5 % (MCFTR), у SBGB (RGBITR) нет -> 0,5 %, покрытие 0,6."""
    monkeypatch.setattr(public_data, "etf_commissions", lambda: pd.Series({"EQMX": 0.5, "SBMX": 0.9}))
    monkeypatch.setattr(public_data, "fund_info", lambda: pd.DataFrame(
        {"name": ["Фонд EQMX"], "issuer": ["УК"], "aum_rub": [1e9], "trade_status": ["торгуется"]},
        index=["EQMX"]))
    rows = pf.replicate("60-40")
    assert [r["asset"] for r in rows] == ["MCFTR", "RGBITR"]
    assert [r["weight"] for r in rows] == [0.6, 0.4]
    eq = rows[0]["funds"][0]
    assert eq == {"secid": "EQMX", "name": "Фонд EQMX", "issuer": "УК", "fee": pytest.approx(0.005),
                  "aum_rub": 1e9, "trade_status": "торгуется"}
    assert rows[0]["funds"][1]["fee"] == pytest.approx(0.009) and rows[0]["funds"][1]["name"] is None
    assert rows[1]["funds"][0]["fee"] is None
    fee, cov = pf.portfolio_fee("60-40")
    assert fee == pytest.approx(0.005) and cov == pytest.approx(0.6)


def test_replicate_no_fund_sector(monkeypatch):
    monkeypatch.setattr(public_data, "etf_commissions", lambda: pd.Series(dtype=float))
    rows = pf.replicate("sector-power")
    assert rows[0]["asset"] == "MEEUTR" and rows[0]["funds"] == []
    assert pf.portfolio_fee("sector-power") == (None, 0.0)


# ====================================================================== журнал ребалансировок
def test_rebalance_log_before_sums_to_one(live_table):
    _, reports = live_table
    for pid, rep in reports.items():
        p = pf.PORTFOLIOS[pid]
        log = rep["rebalance_log"]
        if p["rebalance"] == "none":
            assert log == [], pid
            continue
        assert log, pid
        for x in log:
            assert sum(x["before"].values()) == pytest.approx(1.0, abs=1e-6), (pid, x["date"])
            assert x["after"] == pytest.approx(p["weights"]), pid
            assert pd.Timestamp(x["date"]).month == 12, pid      # rebalance "A" — конец декабря
        assert sum(rep["current_weights"].values()) == pytest.approx(1.0, abs=1e-6)


def test_report_fields(live_table):
    _, reports = live_table
    rep = reports["all-weather-dalio"]
    assert rep["since"] == "2013-12-31" and rep["initial"] == 100_000
    assert rep["assets_short"]["MEEUTR"] == "Электроэнергетика (MEEUTR)"
    assert rep["benchmark"] == "MCFTR" and rep["benchmark_short"] == "Акции (MCFTR)"
    assert set(rep["metrics"]) == set(pf.WINDOWS)
    assert rep["metrics"]["5Y"]["from"] == "2020-12-31"
    assert 0 < rep["fee_coverage"] < 1          # у MEEUTR нет фонда (7,5 %)
    assert rep["fee_coverage"] <= 0.925 + 1e-9
    json.dumps(rep, allow_nan=False)             # NaN заменены на None


# ====================================================================== ранг
def test_rank_window_5y_and_range(live_table):
    df, reports = live_table
    assert (df["rank_window"] == "5Y").all()
    last = pd.Timestamp("2025-12-31")
    for pid, row in df.iterrows():
        short = pd.Timestamp(row["since"]) > last - pd.DateOffset(years=5)
        if short:
            assert pd.isna(row["rank"]), pid
        else:
            assert 0 <= row["rank"] <= 100, pid
    assert pd.isna(df.loc["dividend", "rank"])          # IRDIVTR с 2021-06
    assert df["rank"].notna().sum() == 10


def test_rank_uses_5y_metrics(live_table):
    """Ранг = ranking.rank_items по коэффициентам за 5 лет (а не за 1 год, как колонки таблицы)."""
    df, reports = live_table
    items = {pid: {"class": "portfolio", **{k: reports[pid]["metrics"]["5Y"].get(k) for k in ranking.RANK_METRICS}}
             for pid in df.index if reports[pid]["metrics"]["5Y"]}
    ranking.rank_items(items)
    for pid, it in items.items():
        assert df.loc[pid, "rank"] == pytest.approx(it["score"]), pid


# ====================================================================== риск — доходность
def test_risk_return_drops_short_history():
    p1 = pf.risk_return_points("1Y", source="live")
    p5 = pf.risk_return_points("5Y", source="live")
    p10 = pf.risk_return_points("10Y", source="live")
    assert "dividend" in p1.index
    assert "dividend" not in p5.index and "dividend" not in p10.index
    assert "sector-power" in p10.index           # MEEUTR с 2013-12, 12 лет
    for p in (p1, p5, p10):
        assert not set(SECTOR_NO_DEMO) & set(p.index)
        assert list(p.columns) == ["name", "group", "ret", "volatility", "from", "to"]


def test_risk_return_ofz_1y_by_hand():
    pts = pf.risk_return_points("1Y", source="live")
    r = demo("RGBITR").loc["2024-12-31":"2025-12-31"]
    ret = (r.iloc[-1] / r.iloc[0]) ** (365.25 / 365) - 1
    vol = r.pct_change().dropna().std(ddof=1) * math.sqrt(12)
    assert pts.loc["ofz-only", "ret"] == pytest.approx(ret, rel=1e-12)
    assert pts.loc["ofz-only", "volatility"] == pytest.approx(vol, rel=1e-12)
    assert pts.loc["ofz-only", "from"] == "2024-12-31" and pts.loc["ofz-only", "to"] == "2025-12-31"


def test_window_start():
    d = pd.date_range("2020-01-31", "2025-12-31", freq="ME")
    assert pf.window_start(d, "MAX") == "2020-01-31"
    assert pf.window_start(d, "1Y") == "2024-12-31"
    assert pf.window_start(d, "10Y") is None


# ====================================================================== файл сборщика
def test_file_source_without_file(pub):
    assert not (pub / "portfolios.json").exists()
    with pytest.raises(FileNotFoundError):
        pf.portfolios_table(source="file")
    with pytest.raises(FileNotFoundError):
        pf.portfolio_report("60-40", source="file")
    with pytest.raises(FileNotFoundError):
        pf.portfolio_values("60-40", source="file")
    with pytest.raises(FileNotFoundError):
        pf.risk_return_points("1Y", source="file")
    # auto без файла — живой расчёт
    assert pf.portfolio_values("ofz-only", source="auto").iloc[0] == pytest.approx(100_000)


def test_build_file_matches_live(pub, live_table):
    body, info = pf.build_file(fallback=False)
    assert info["n"] == 11 and set(info["skipped"]) == set(SECTOR_NO_DEMO)
    text = json.dumps({"updated": "2026-01-01T00:00:00+00:00", **body}, ensure_ascii=False, allow_nan=False)
    (pub / "portfolios.json").write_text(text, encoding="utf-8")
    assert body["last"] == "2025-12-31" and body["groups"] == list(pf.GROUPS)

    live, reports = live_table
    f = pf.portfolios_table(source="file")
    assert f.attrs["updated"] == "2026-01-01T00:00:00+00:00"
    assert set(f.attrs["skipped"]) == set(SECTOR_NO_DEMO)
    pd.testing.assert_frame_equal(f.sort_index(), live.sort_index(), rtol=1e-6, check_dtype=False)
    auto = pf.portfolios_table(source="auto")
    pd.testing.assert_frame_equal(auto, f)

    for pid in ("lazy-plus", "all-weather-dalio", "sector-power"):
        rf = pf.portfolio_report(pid, source="file")
        assert rf["source"] == "file" and rf["updated"] == "2026-01-01T00:00:00+00:00"
        assert rf["stale"] is False and rf["stale_date"] is None
        assert reports[pid]["source"] == "live" and reports[pid]["updated"] is None
        assert _strip(rf) == _strip(json.loads(json.dumps(reports[pid])))
        v_file = pf.portfolio_values(pid, source="file")
        v_live = pf.portfolio_values(pid, source="live")
        assert v_file.attrs["freq"] == "M"
        pd.testing.assert_series_equal(v_file, v_live, check_names=False, atol=0.01, rtol=0, check_freq=False)
        assert pf.portfolio_since(pid, source="file") == pf.portfolio_since(pid, source="live")
    for per in pf.RISK_RETURN_PERIODS:
        a = pf.risk_return_points(per, source="file").sort_index()
        b = pf.risk_return_points(per, source="live").sort_index()
        pd.testing.assert_frame_equal(a, b, rtol=1e-6)

    # в файле нет портфеля — PortfolioUnavailable (а не тихий живой расчёт)
    with pytest.raises(pf.PortfolioUnavailable):
        pf.portfolio_report("sector-oil-gas", source="file")
    with pytest.raises(pf.PortfolioUnavailable):
        pf.portfolio_values("sector-oil-gas", source="file")
    # срез из файла пересчитывается к 100 000 на первую точку
    v = pf.portfolio_values("60-40", start="2015-01-01", source="file")
    assert v.iloc[0] == pytest.approx(100_000) and v.index[0] >= pd.Timestamp("2015-01-01")


def test_monthly_points_daily_series():
    idx = pd.bdate_range("2024-01-03", "2024-03-05")
    v = pd.Series(np.arange(len(idx), dtype=float) + 100.0, index=idx)
    pts = pf._monthly_points(v)
    assert [d for d, _ in pts] == ["2024-01-03", "2024-01-31", "2024-02-29", "2024-03-05"]
    assert pts[0][1] == 100.0 and pts[-1][1] == float(v.iloc[-1])


# ====================================================================== universe.load_series(fallback=False)
def test_load_series_fallback_false_raises(monkeypatch):
    monkeypatch.delenv("IP_OFFLINE", raising=False)

    def boom(*a, **k):
        raise ConnectionError("ISS недоступен")
    monkeypatch.setattr(universe, "_load_live", boom)
    with pytest.raises(ConnectionError, match="ISS недоступен"):
        universe.load_series("MCFTR", "2015-01-01", fallback=False)
    assert "MCFTR" not in universe.FALLBACK_USED
    s = universe.load_series("MCFTR", "2015-01-01")            # по умолчанию — демо-ряд
    assert len(s) and "MCFTR" in universe.FALLBACK_USED
    with pytest.raises(ConnectionError):                        # нет демо-ряда — ошибка и с fallback
        universe.load_series("MEOGTR", "2015-01-01")


def test_build_file_without_fallback_skips_all(monkeypatch):
    """Живые источники недоступны: build_file(fallback=False) не подменяет демо-рядами — всё в skipped;
    с fallback=True отчёт строится по демо-рядам и предупреждает об этом."""
    monkeypatch.delenv("IP_OFFLINE", raising=False)

    def boom(*a, **k):
        raise ConnectionError("ISS недоступен")
    monkeypatch.setattr(universe, "_load_live", boom)
    body, info = pf.build_file(fallback=False)
    assert info["n"] == 0 and set(info["skipped"]) == set(pf.PORTFOLIOS)
    assert body["items"] == {} and body["last"] is None
    assert "ConnectionError" in info["skipped"]["60-40"]
    pf.clear_cache()
    rep, _ = pf._compute_report("60-40", fallback=True)
    assert any("демо-данные" in w for w in rep["warnings"])


# ====================================================================== сборщик: --only portfolios
def _fake_body(n: int, tag: str = "a") -> tuple[dict, dict]:
    items = {f"p{i}": {"row": {"name": f"П{i}", "tag": tag}, "report": {"id": f"p{i}"}} for i in range(n)}
    body = {"initial": 100000.0, "groups": list(pf.GROUPS), "items": items,
            "series": {k: [["2025-12-31", 100000.0]] for k in items}, "risk_return": {"1Y": []},
            "skipped": {"sector-it": "нет данных"}, "last": "2025-12-31"}
    return body, {"n": n, "skipped": body["skipped"]}


@pytest.fixture
def collector(tmp_path, monkeypatch):
    from scripts import collect_data as cd
    out = tmp_path / "out"
    monkeypatch.setattr(cd, "OUT", out)
    state = {"build": lambda fallback=False: _fake_body(12), "fallback": [], "previous": [], "now": iter(
        [f"2026-10-23T00:00:{i:02d}+00:00" for i in range(60)])}

    def build_file(fallback=True, previous=None):
        state["fallback"].append(fallback)
        state["previous"].append(previous)
        return state["build"](fallback)
    monkeypatch.setattr(pf, "build_file", build_file)
    monkeypatch.setattr(cd, "_now", lambda: next(state["now"]))
    return cd, out, state


def test_collector_portfolios_registered():
    from scripts import collect_data as cd
    spec = cd.SOURCES["portfolios"]
    assert spec["kind"] == "portfolios" and spec["default"] is False and spec["min_rows"] == 10


def test_collector_writes_and_keeps_updated(collector):
    cd, out, state = collector
    assert cd.main(["--only", "portfolios"]) == 0
    assert state["fallback"] == [False]                     # без подмены демо-данными
    assert state["previous"] == [{}]                        # прежнего файла нет
    js = json.loads((out / "portfolios.json").read_text(encoding="utf-8"))
    body, _ = _fake_body(12)
    assert {k: js[k] for k in body} == body
    assert js["source"] == cd.PORTFOLIOS_SOURCE and js["updated"]
    st = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert st["portfolios"]["ok"] and st["portfolios"]["rows"] == 12
    assert st["portfolios"]["last"] == "2025-12-31" and st["portfolios"]["skipped"] == 1
    assert not list(out.glob(".*.tmp"))
    first = js["updated"]

    # то же содержимое — updated прежний
    assert cd.main(["--only", "portfolios"]) == 0
    assert json.loads((out / "portfolios.json").read_text(encoding="utf-8"))["updated"] == first
    assert state["previous"][-1]["updated"] == first        # сборщику передан прежний файл

    # содержимое изменилось — updated новый
    state["build"] = lambda fb: _fake_body(12, tag="b")
    assert cd.main(["--only", "portfolios"]) == 0
    js2 = json.loads((out / "portfolios.json").read_text(encoding="utf-8"))
    assert js2["updated"] != first and js2["items"]["p0"]["row"]["tag"] == "b"


def test_collector_failure_keeps_old_file(collector):
    cd, out, state = collector
    assert cd.main(["--only", "portfolios"]) == 0
    before = (out / "portfolios.json").read_text(encoding="utf-8")

    def boom(fb):
        raise RuntimeError("ISS недоступен")
    state["build"] = boom
    assert cd.main(["--only", "portfolios"]) == 1
    assert (out / "portfolios.json").read_text(encoding="utf-8") == before
    st = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert not st["portfolios"]["ok"] and "ISS недоступен" in st["portfolios"]["error"]
    assert st["portfolios"]["last"] == "2025-12-31"          # прежняя дата остаётся


def test_collector_too_few_portfolios(collector):
    cd, out, state = collector
    assert cd.main(["--only", "portfolios"]) == 0
    before = (out / "portfolios.json").read_text(encoding="utf-8")
    state["build"] = lambda fb: _fake_body(9)                  # < min_rows = 10
    assert cd.main(["--only", "portfolios"]) == 1
    assert (out / "portfolios.json").read_text(encoding="utf-8") == before
    st = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert not st["portfolios"]["ok"] and "9 < 10" in st["portfolios"]["error"]


def test_collector_first_run_too_few_writes_nothing(collector):
    cd, out, state = collector
    state["build"] = lambda fb: _fake_body(3)
    assert cd.main(["--only", "portfolios"]) == 1
    assert not (out / "portfolios.json").exists()


def test_collector_atomic_write(collector, monkeypatch):
    """Сбой на замене файла: прежний файл цел, временный .tmp удалён."""
    cd, out, state = collector
    assert cd.main(["--only", "portfolios"]) == 0
    before = (out / "portfolios.json").read_text(encoding="utf-8")
    state["build"] = lambda fb: _fake_body(12, tag="new")
    real_replace = Path.replace

    def bad_replace(self, target):
        if Path(target).name == "portfolios.json":
            raise OSError("диск заполнен")
        return real_replace(self, target)
    monkeypatch.setattr(Path, "replace", bad_replace)
    assert cd.main(["--only", "portfolios"]) == 1
    assert (out / "portfolios.json").read_text(encoding="utf-8") == before
    assert not (out / ".portfolios.json.tmp").exists()


# ====================================================================== benchmark_values
def test_benchmark_values_demo_same_dates():
    v = pf.portfolio_values("lazy-plus", source="live")
    b = pf.benchmark_values("lazy-plus", source="live")
    assert list(b.index) == list(v.index)
    assert b.iloc[0] == pytest.approx(100_000)
    mc = demo("MCFTR")
    exp = mc.loc[v.index] / mc[v.index[0]] * 100_000
    np.testing.assert_allclose(b.values, exp.values, rtol=1e-12)
    assert b.attrs["aligned"] is True and b.attrs["key"] == "MCFTR"
    assert b.attrs["from"] == "2010-12-31" and b.attrs["to"] == "2025-12-31"
    assert b.attrs["source"] == "live" and b.attrs["freq"] == "M"
    # срез по start — те же даты, что у portfolio_values с тем же start
    v2 = pf.portfolio_values("dividend", start="2023-01-01", source="live")
    b2 = pf.benchmark_values("dividend", start="2023-01-01", source="live")
    assert list(b2.index) == list(v2.index) and b2.iloc[0] == pytest.approx(100_000)


def test_benchmark_shorter_than_portfolio(synthetic):
    """Бенчмарк MCFTR только с 31.03.2021 по 31.03.2022 — сравнение на общем отрезке, aligned False."""
    synthetic["MEEUTR"] = pd.Series(np.linspace(100, 118, len(SYN_DATES)), index=SYN_DATES)
    synthetic["MCFTR"] = synthetic["MCFTR"].loc["2021-03-31":"2022-03-31"]
    pf.clear_cache()
    v = pf.portfolio_values("sector-power", source="live")
    assert len(v) == 19
    b = pf.benchmark_values("sector-power", source="live")
    assert b.index[0] == pd.Timestamp("2021-03-31") and b.index[-1] == pd.Timestamp("2022-03-31")
    assert len(b) == 13 and b.iloc[0] == pytest.approx(100_000)
    assert set(b.index) <= set(v.index)
    assert b[pd.Timestamp("2021-12-31")] == pytest.approx(200_000)   # 100 -> 200
    assert b.attrs["aligned"] is False
    assert (b.attrs["from"], b.attrs["to"]) == ("2021-03-31", "2022-03-31")
    assert (b.attrs["portfolio_from"], b.attrs["portfolio_to"]) == ("2020-12-31", "2022-06-30")


def test_benchmark_too_short_or_missing(synthetic):
    synthetic["MEEUTR"] = pd.Series(np.linspace(100, 118, len(SYN_DATES)), index=SYN_DATES)
    synthetic["MCFTR"] = synthetic["MCFTR"].loc["2021-03-31":"2021-04-30"]      # 2 точки
    pf.clear_cache()
    assert pf.benchmark_values("sector-power", source="live") is None
    del synthetic["MCFTR"]                                                       # ряда нет
    pf.clear_cache()
    assert pf.benchmark_values("sector-power", source="live") is None
    assert pf.portfolio_values("sector-power", source="live").iloc[-1] == pytest.approx(118_000)


def test_benchmark_values_file_matches_live(pub):
    body, _ = pf.build_file(fallback=False)
    (pub / "portfolios.json").write_text(json.dumps({"updated": "u1", **body}, ensure_ascii=False),
                                         encoding="utf-8")
    assert set(body["series_benchmark"]) == set(body["series"])
    for pid in ("lazy-plus", "all-weather-dalio", "dividend", "ofz-only"):
        bf = pf.benchmark_values(pid, source="file")
        bl = pf.benchmark_values(pid, source="live")
        vf = pf.portfolio_values(pid, source="file")
        assert list(bf.index) == list(vf.index), pid
        assert bf.attrs["source"] == "file" and bf.attrs["updated"] == "u1" and bf.attrs["aligned"] is True
        np.testing.assert_allclose(bf.values, bl.values, atol=0.01, rtol=0, err_msg=pid)
    bf = pf.benchmark_values("dividend", start="2023-01-01", source="file")
    assert bf.iloc[0] == pytest.approx(100_000) and bf.index[0] >= pd.Timestamp("2023-01-01")


# ====================================================================== build_file(previous=...)
def _fail_keys(monkeypatch, keys):
    real = getattr(universe.load_series, "_orig", universe.load_series)

    def load(key, *a, **k):
        if key in keys:
            raise ConnectionError(f"{key}: ISS недоступен")
        return real(key, *a, **k)
    load._orig = real
    monkeypatch.setattr(universe, "load_series", load)
    pf.clear_cache()


def test_build_file_carries_stale_from_previous(monkeypatch):
    prev, info0 = pf.build_file(fallback=False)
    assert info0 == {"n": 11, "stale": 0, "skipped": info0["skipped"]}
    prev = json.loads(json.dumps({"updated": "u0", **prev}))
    _fail_keys(monkeypatch, {"MEEUTR"})
    body, info = pf.build_file(fallback=False, previous=prev)
    lost = {"all-weather-dalio", "sector-power"}
    assert info["n"] == 9 and info["stale"] == 2
    # перенесённые — в body["stale"] (с причиной), а не в skipped
    assert not (lost & set(info["skipped"]))
    assert set(body["stale"]) == lost and "MEEUTR" in body["stale"]["sector-power"]["reason"]
    assert body["stale"]["sector-power"]["stale_date"] == "2025-12-31"
    for pid in lost:
        it = body["items"][pid]
        assert it["stale"] is True and it["stale_date"] == "2025-12-31"
        assert it["report"] == prev["items"][pid]["report"]
        assert body["series"][pid] == prev["series"][pid]
        assert body["series_benchmark"][pid] == prev["series_benchmark"][pid]
        for per in pf.RISK_RETURN_PERIODS:
            assert [p for p in body["risk_return"][per] if p["id"] == pid] == \
                   [p for p in prev["risk_return"][per] if p["id"] == pid]
    for pid in set(body["items"]) - lost:
        assert not body["items"][pid].get("stale")
    # секторов без демо-данных не было и в прежнем файле — не появляются
    assert set(body["items"]) == set(prev["items"])
    assert list(body["items"]) == [p for p in pf.PORTFOLIOS if p in body["items"]]
    # ранг — по полному набору (свежие + перенесённые): совпадает с прежним
    for pid, it in body["items"].items():
        assert it["row"]["rank"] == prev["items"][pid]["row"]["rank"], pid
    json.dumps(body, allow_nan=False)

    # повторный сбой: stale_date прежнего переноса сохраняется
    prev2 = json.loads(json.dumps(body))
    prev2["items"]["sector-power"]["stale_date"] = "2025-11-28"
    body2, info2 = pf.build_file(fallback=False, previous=prev2)
    assert body2["items"]["sector-power"]["stale_date"] == "2025-11-28"
    assert info2["n"] == 9 and info2["stale"] == 2


def test_build_file_stale_visible_in_table_and_report(monkeypatch, pub):
    prev, _ = pf.build_file(fallback=False)
    _fail_keys(monkeypatch, {"MEEUTR"})
    body, _ = pf.build_file(fallback=False, previous=json.loads(json.dumps(prev)))
    (pub / "portfolios.json").write_text(json.dumps({"updated": "u2", **body}, ensure_ascii=False),
                                         encoding="utf-8")
    df = pf.portfolios_table(source="file")
    assert df.loc["sector-power", "stale"] and not df.loc["lazy-plus", "stale"]
    rep = pf.portfolio_report("sector-power", source="file")
    assert rep["stale"] is True and rep["stale_date"] == "2025-12-31" and rep["source"] == "file"
    assert pf.portfolio_values("sector-power", source="file").attrs["stale"] is True


def test_collector_min_rows_counts_fresh_only(monkeypatch, tmp_path):
    """Прежний файл с 11 портфелями; сейчас посчитано 8 (+3 перенесённых) < min_rows 10 — сбой,
    файл не трогается."""
    from scripts import collect_data as cd
    monkeypatch.setattr(cd, "OUT", tmp_path / "out")
    assert cd.main(["--only", "portfolios"]) == 0
    before = (tmp_path / "out" / "portfolios.json").read_text(encoding="utf-8")
    assert len(json.loads(before)["items"]) == 11
    _fail_keys(monkeypatch, {"MEEUTR", "IRDIVTR"})     # Далио, электроэнергетика, дивидендный
    assert cd.main(["--only", "portfolios"]) == 1
    assert (tmp_path / "out" / "portfolios.json").read_text(encoding="utf-8") == before
    st = json.loads((tmp_path / "out" / "status.json").read_text(encoding="utf-8"))
    assert "8 < 10" in st["portfolios"]["error"]
    # один портфель недоступен (10 свежих) — запись с переносом
    _fail_keys(monkeypatch, {"IRDIVTR"})
    assert cd.main(["--only", "portfolios"]) == 0
    js = json.loads((tmp_path / "out" / "portfolios.json").read_text(encoding="utf-8"))
    assert js["items"]["dividend"]["stale"] is True and len(js["items"]) == 11
    assert js["items"]["sector-power"].get("stale") is not True
    assert st["portfolios"]["last"] == "2025-12-31"


# ====================================================================== кэш рядов
def test_series_cache_skips_fallback_and_keys_by_day(monkeypatch):
    monkeypatch.delenv("IP_OFFLINE", raising=False)
    calls = []
    live = pd.Series([1.0, 2.0, 3.0], index=pd.date_range("2024-01-31", periods=3, freq="ME"))
    state = {"fail": True}

    def load_live(key, start, end, total_return):
        calls.append(key)
        if state["fail"]:
            raise ConnectionError("ISS недоступен")
        return live
    monkeypatch.setattr(universe, "_load_live", load_live)
    pf.clear_cache()

    s1 = pf._series("MCFTR", True)                  # сбой -> демо-ряд, не кэшируется
    assert len(s1) == len(demo("MCFTR")) and "MCFTR" in pf._DEMO_KEYS
    pf._series("MCFTR", True)
    assert calls == ["MCFTR", "MCFTR"]
    state["fail"] = False                           # источник ожил — сразу живой ряд
    s3 = pf._series("MCFTR", True)
    assert list(s3) == [1.0, 2.0, 3.0] and "MCFTR" not in pf._DEMO_KEYS
    pf._series("MCFTR", True)
    assert calls == ["MCFTR"] * 3                   # живой ряд — из кэша

    import datetime as real_dt
    import types
    tomorrow = real_dt.date.today() + real_dt.timedelta(days=1)
    fake_dt = types.SimpleNamespace(date=types.SimpleNamespace(today=lambda: tomorrow))
    monkeypatch.setattr(pf, "dt", fake_dt)            # подмена только в модуле portfolios
    pf._series("MCFTR", True)
    assert calls == ["MCFTR"] * 4                   # новый день — новый ключ кэша
    pf._series("MCFTR", True)
    assert calls == ["MCFTR"] * 4


def test_series_cache_separate_for_offline(monkeypatch):
    calls = []

    def load_live(key, start, end, total_return):
        calls.append(key)
        return pd.Series([5.0, 6.0], index=pd.date_range("2024-01-31", periods=2, freq="ME"))
    monkeypatch.setattr(universe, "_load_live", load_live)
    pf.clear_cache()
    off = pf._series("MCFTR", True)                 # IP_OFFLINE=1 — демо-ряд
    assert len(off) == len(demo("MCFTR")) and calls == []
    monkeypatch.delenv("IP_OFFLINE")
    on = pf._series("MCFTR", True)
    assert list(on) == [5.0, 6.0] and calls == ["MCFTR"]
