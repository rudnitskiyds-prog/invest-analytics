"""Безрисковая ставка рядом по датам в коэффициентах (core/analytics/metrics.py) и в витринах
(core/analytics/screener.py): ставка берётся на НАЧАЛО периода доходности; rf=None в витрине — ряд
core.data.risk_free за окно, ставка недоступна — Шарп/Сортино NaN, витрина строится. Без сети."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from core.analytics import metrics as m
from core.analytics import screener
from core.data import iss, public_data, risk_free

D = pd.to_datetime
RATE = pd.Series([0.10, 0.20], index=D(["2020-01-01", "2020-02-15"]))   # 10 %, с 15.02.2020 — 20 %


def pp(x, n):
    return (1 + x) ** (1 / n) - 1


# ------------------------------------------------------------------ period_starts
def test_period_starts_monthly():
    idx = D(["2020-01-31", "2020-02-29", "2020-03-31"])
    assert list(m.period_starts(idx).strftime("%Y-%m-%d")) == ["2020-01-02", "2020-01-31", "2020-02-29"]


def test_period_starts_daily_and_edges():
    idx = D(["2020-02-13", "2020-02-14", "2020-02-17"])
    assert list(m.period_starts(idx).strftime("%Y-%m-%d")) == ["2020-02-12", "2020-02-13", "2020-02-14"]
    assert list(m.period_starts(D(["2020-05-05"]))) == [pd.Timestamp("2020-05-05")]
    assert len(m.period_starts(pd.DatetimeIndex([]))) == 0


# ------------------------------------------------------------------ per_period_rate
def test_per_period_rate_monthly_start_of_period():
    idx = D(["2020-01-31", "2020-02-29", "2020-03-31"])
    got = m.per_period_rate(RATE, 12, idx)
    assert list(got.index) == list(idx)
    # начала: 02.01 (10 %), 31.01 (10 %), 29.02 (20 %) — на конец февраля уже 20 %, но период начался при 10 %
    np.testing.assert_allclose(got.to_numpy(), [pp(0.10, 12), pp(0.10, 12), pp(0.20, 12)], rtol=0, atol=1e-15)


def test_per_period_rate_daily_start_of_period():
    rate = pd.Series([0.10, 0.20], index=D(["2020-01-01", "2020-02-14"]))
    idx = D(["2020-02-13", "2020-02-14", "2020-02-17", "2020-02-18"])
    got = m.per_period_rate(rate, 252, idx)
    # начала: 12.02, 13.02 (10 %), 14.02 (публикация в день начала — 20 %), 17.02 (20 %)
    np.testing.assert_allclose(got.to_numpy(), [pp(0.10, 252), pp(0.10, 252), pp(0.20, 252), pp(0.20, 252)],
                               rtol=0, atol=1e-15)


def test_per_period_rate_bfill_before_first_date_and_unsorted_dupes():
    rate = pd.Series([0.30, 0.05, 0.07, np.nan], index=D(["2021-01-01", "2020-06-01", "2020-06-01", "2020-07-01"]))
    idx = D(["2020-01-31", "2020-02-29", "2021-01-31", "2021-02-28"])
    got = m.per_period_rate(rate, 12, idx)
    # начала: 02.01.2020, 31.01.2020 (до 01.06.2020 — первое значение, bfill), 29.02.2020, 31.01.2021;
    # дубль 01.06.2020 — последнее значение (7 %); NaN 01.07.2020 отброшен
    np.testing.assert_allclose(got.to_numpy(), [pp(0.07, 12)] * 3 + [pp(0.30, 12)], rtol=0, atol=1e-15)


def test_per_period_rate_scalar_unchanged():
    assert m.per_period_rate(0.12, 12) == pytest.approx(1.12 ** (1 / 12) - 1)


# ------------------------------------------------------------------ Шарп / Сортино с рядом ставок
def test_sharpe_with_rate_series_manual():
    idx = D(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"])
    r = pd.Series([0.02, -0.01, 0.03, 0.015], index=idx)
    rfp = np.array([pp(0.10, 12), pp(0.10, 12), pp(0.20, 12), pp(0.20, 12)])
    ex = r.to_numpy() - rfp
    mean = ex.sum() / 4
    sd = math.sqrt(((ex - mean) ** 2).sum() / 3)
    assert m.sharpe(r, RATE, 12) == pytest.approx(mean / sd * math.sqrt(12), rel=1e-12)
    # «ставка на конец периода» (на 29.02 уже 20 %) дала бы другой результат
    ex_end = r.to_numpy() - np.array([pp(0.10, 12), pp(0.20, 12), pp(0.20, 12), pp(0.20, 12)])
    sharpe_end = ex_end.mean() / ex_end.std(ddof=1) * math.sqrt(12)
    assert abs(m.sharpe(r, RATE, 12) - sharpe_end) > 1e-3


def test_sharpe_constant_series_equals_scalar():
    idx = pd.bdate_range("2023-01-02", periods=60)
    r = pd.Series(np.sin(np.arange(60)) / 100, index=idx)
    flat = pd.Series(0.15, index=D(["2010-01-01"]))
    assert m.sharpe(r, flat, 252) == pytest.approx(m.sharpe(r, 0.15, 252), rel=1e-12)
    assert m.sortino(r, flat, 252) == pytest.approx(m.sortino(r, 0.15, 252), rel=1e-12)


def test_sharpe_zero_volatility_excess_nan():
    # доходность ровно равна ставке за период — избыточная доходность постоянна (σ = 0) -> NaN
    idx = D(["2020-01-31", "2020-02-29", "2020-03-31"])
    r = pd.Series([pp(0.10, 12)] * 3, index=idx)
    assert math.isnan(m.sharpe(r, pd.Series([0.10], index=D(["2019-01-01"])), 12))


def test_sharpe_nan_rate_gives_nan():
    r = pd.Series([0.01, -0.02, 0.03], index=D(["2020-01-31", "2020-02-29", "2020-03-31"]))
    assert math.isnan(m.sharpe(r, float("nan"), 12))


# ------------------------------------------------------------------ annual_rf / rates_at_starts: окно ряда не влияет
def test_rates_at_starts_and_annual_rf_manual():
    rate = pd.Series([0.08, 0.20, 0.17], index=D(["2021-12-15", "2022-02-10", "2022-03-15"]))
    idx = D(["2022-01-31", "2022-02-28", "2022-03-31"])
    # начала: 31.12.2021 (8 %), 31.01.2022 (8 %), 28.02.2022 (20 %); 17 % с 15.03 в расчёт не попадает
    got = m.rates_at_starts(rate, idx)
    assert list(got.index) == list(idx)
    np.testing.assert_allclose(got.to_numpy(), [0.08, 0.08, 0.20], rtol=0, atol=1e-15)
    assert m.annual_rf(rate, idx) == pytest.approx(0.12, abs=1e-15)
    assert m.annual_rf(rate) == pytest.approx(0.15, abs=1e-15)          # без index — среднее значений ряда
    assert m.annual_rf(0.07, idx) == 0.07
    assert m.annual_rf(rate, pd.DatetimeIndex([])) == pytest.approx(0.15)


def _long_rate():
    """Ступенчатый ряд 2014–2026 (публикации раз в ~2 мес.), ставка заметно меняется."""
    idx = pd.date_range("2014-01-10", "2026-06-10", freq="61D")
    return pd.Series(0.06 + 0.1 * (1 + np.sin(np.arange(len(idx)) / 3)) / 2, index=idx)


def _short_slice(rate, start):
    """Как risk_free.rate_series(start): ряд с start, первой точкой — ставка, действующая на start."""
    s = rate.loc[start:]
    v = rate.loc[:start].iloc[-1]
    return pd.concat([pd.Series([v], index=D([start])), s])


@pytest.mark.parametrize("method", ["arith", "cagr"])
def test_compute_all_rate_window_does_not_matter(method):
    idx = pd.date_range("2022-01-31", "2025-12-31", freq="ME")
    k = np.arange(len(idx))
    asset = pd.Series(100 * np.cumprod(1 + 0.01 + 0.04 * np.sin(k * 1.3)), index=idx)
    bench = pd.Series(100 * np.cumprod(1 + 0.008 + 0.03 * np.cos(k * 0.7)), index=idx)
    long = _long_rate()
    short = _short_slice(long, "2021-12-01")
    assert m.annual_rf(long) != pytest.approx(m.annual_rf(short), abs=1e-4)   # средние «всего ряда» различны
    a = m.compute_all(asset, bench, long, "M", return_method=method)
    b = m.compute_all(asset, bench, short, "M", return_method=method)
    for f in ("sharpe", "sortino", "treynor", "alpha", "m2", "martin", "omega"):
        x, y = getattr(a, f), getattr(b, f)
        assert np.isfinite(x), f
        assert x == pytest.approx(y, rel=1e-12, abs=1e-14), f


def test_compute_all_m2_martin_use_rates_at_starts():
    idx = pd.date_range("2022-01-31", "2025-12-31", freq="ME")
    k = np.arange(len(idx))
    asset = pd.Series(100 * np.cumprod(1 + 0.01 + 0.04 * np.sin(k * 1.3)), index=idx)
    bench = pd.Series(100 * np.cumprod(1 + 0.008 + 0.03 * np.cos(k * 0.7)), index=idx)
    long = _long_rate()
    r = m.to_returns(asset)
    rfa = float(m.rates_at_starts(long, r.index).mean())
    rep = m.compute_all(asset, bench, long, "M")
    assert rep.martin == pytest.approx(m.martin(asset, rfa), rel=1e-12)
    rb = m.to_returns(bench)
    assert rep.m2 == pytest.approx(rep.sharpe * m.volatility(rb, 12) + rfa, rel=1e-12)
    cg = m.compute_all(asset, bench, long, "M", return_method="cagr")
    assert cg.sharpe == pytest.approx((cg.cagr - rfa) / cg.volatility, rel=1e-12)
    assert cg.treynor == pytest.approx((cg.cagr - rfa) / cg.beta, rel=1e-12)


# ------------------------------------------------------------------ витрины
def _px(secid: str) -> pd.Series:
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    k = 1 + (sum(map(ord, secid)) % 5) / 1000
    steps = np.where(np.arange(len(idx)) % 3 == 0, -0.004, 0.003) * k
    return pd.Series(100 * np.cumprod(1 + steps), index=idx)


@pytest.fixture
def fake_market(monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("обращение к ISS в тесте")
    monkeypatch.setattr(iss, "get_json", no_net)
    monkeypatch.setattr(iss, "close_series", lambda secid, start="2000-01-01", end=None, **k: _px(secid))
    monkeypatch.setattr(iss, "etfs", lambda: pd.DataFrame({
        "SECID": ["AAA", "BBB"], "SHORTNAME": ["Фонд А", "Фонд Б"], "FUNDTYPE": ["БПИФ", "БПИФ"],
        "PRICE": [1.0, 2.0], "LASTTOPREVPRICE": [0.1, -0.2], "VALTODAY_RUR": [1e6, 2e6]}))
    monkeypatch.setattr(iss, "stocks", lambda board="TQBR": pd.DataFrame({
        "SECID": ["SSS"], "SHORTNAME": ["Акция"], "PRICE": [10.0], "LASTTOPREVPRICE": [0.5],
        "VALTODAY_RUR": [5e6], "CAP": [1e10], "LISTLEVEL": [1]}))
    monkeypatch.setattr(public_data, "etf_commissions", lambda: pd.Series(dtype=float))
    monkeypatch.setattr(public_data, "fund_info", lambda: pd.DataFrame())
    monkeypatch.setattr(public_data, "share_sectors", lambda: pd.Series(dtype=object))
    monkeypatch.setattr(screener, "_multiples_for", lambda cap: None)


def _expected_sharpe(secid, rf):
    px = _px(secid)
    y1 = px.loc[px.index[-1] - pd.Timedelta(days=365):]
    return m.sharpe(m.to_returns(y1), rf, 252)


def test_etf_showcase_rf_none_uses_risk_free_series(fake_market, monkeypatch):
    calls = []
    series = pd.Series([0.12, 0.18], index=[pd.Timestamp.today().normalize() - pd.Timedelta(days=366),
                                            pd.Timestamp.today().normalize() - pd.Timedelta(days=100)])

    def rate_series(start=None, end=None):
        calls.append((start, end))
        return series
    monkeypatch.setattr(risk_free, "rate_series", rate_series)
    df = screener.etf_showcase("IMOEX").set_index("SECID")
    exp_start = (pd.Timestamp.today() - pd.Timedelta(days=366)).date().isoformat()
    assert calls == [(exp_start, None)]
    for sec in ("AAA", "BBB"):
        assert df.loc[sec, "Шарп"] == pytest.approx(_expected_sharpe(sec, series), rel=1e-12)
        assert df.loc[sec, "Шарп"] != pytest.approx(_expected_sharpe(sec, 0.0))


def test_etf_showcase_explicit_rf_not_replaced(fake_market, monkeypatch):
    monkeypatch.setattr(risk_free, "rate_series", lambda *a, **k: pytest.fail("rate_series при явной rf"))
    df = screener.etf_showcase("IMOEX", 0.15).set_index("SECID")
    assert df.loc["AAA", "Шарп"] == pytest.approx(_expected_sharpe("AAA", 0.15), rel=1e-12)


def test_etf_showcase_rf_unavailable_nan_sharpe(fake_market, monkeypatch):
    def unavailable(*a, **k):
        raise risk_free.RiskFreeUnavailable("нет файла")
    monkeypatch.setattr(risk_free, "rate_series", unavailable)
    df = screener.etf_showcase("IMOEX")
    assert len(df) == 2
    assert df["Шарп"].isna().all() and df["Сортино"].isna().all()
    assert df["Волатильность"].notna().all() and df["1М"].notna().all()


def test_shares_showcase_rf_unavailable_nan_sharpe(fake_market, monkeypatch):
    def unavailable(*a, **k):
        raise risk_free.RiskFreeUnavailable("нет файла")
    monkeypatch.setattr(risk_free, "rate_series", unavailable)
    df = screener.shares_showcase("IMOEX", with_dividends=False)
    assert list(df["SECID"]) == ["SSS"]
    assert df["Шарп"].isna().all() and df["Сортино"].isna().all() and df["Волатильность"].notna().all()


def test_shares_showcase_rf_none_uses_series(fake_market, monkeypatch):
    series = pd.Series([0.16], index=[pd.Timestamp.today().normalize() - pd.Timedelta(days=366)])
    monkeypatch.setattr(risk_free, "rate_series", lambda start=None, end=None: series)
    df = screener.shares_showcase("IMOEX", with_dividends=False).set_index("SECID")
    assert df.loc["SSS", "Шарп"] == pytest.approx(_expected_sharpe("SSS", 0.16), rel=1e-12)
