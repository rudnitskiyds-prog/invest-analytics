"""Метрики этапа 1 (core/analytics/metrics.py, web/CONTRACT.md): ручные примеры и граничные случаи.

Паритет с JS (web/lib/metrics.js) — tests/web/metrics_stage1.test.mjs по эталону make_reference.py.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from core.analytics import metrics as m

ME = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30", "2020-05-31", "2020-06-30"])


def ser(values, dates=ME):
    return pd.Series(values, index=dates[: len(values)], dtype=float)


# ---------------------------------------------------------------- просадки, UI, Мартин
def test_ulcer_and_episode_by_hand():
    s = ser([100, 120, 90, 108, 120])          # dd = 0, 0, −0.25, −0.1, 0
    assert m.ulcer_index(s) == pytest.approx(math.sqrt((0.0625 + 0.01) / 5), abs=1e-15)
    top = m.top_drawdowns(s)
    assert top == [{"depth": pytest.approx(-0.25), "peak": "2020-02-29", "trough": "2020-03-31",
                    "recovery": "2020-05-31", "daysToTrough": 31, "daysToRecover": 92}]
    assert m.avg_drawdown(s) == pytest.approx(-0.25)


def test_martin_by_hand():
    s = ser([100, 120, 90, 108, 121])
    ui = math.sqrt((0.0625 + 0.01) / 5)
    assert m.martin(s, 0.05) == pytest.approx((m.cagr(s) - 0.05) / ui, rel=1e-12)
    assert m.martin(s) == pytest.approx(m.cagr(s) / ui, rel=1e-12)


def test_no_drawdown_and_empty():
    up = ser([1, 2, 3])
    assert m.ulcer_index(up) == 0
    assert math.isnan(m.martin(up))                  # UI = 0 -> NaN, не ±inf
    assert math.isnan(m.avg_drawdown(up))
    assert m.top_drawdowns(up) == []
    empty = pd.Series([], dtype=float, index=pd.DatetimeIndex([]))
    assert math.isnan(m.ulcer_index(empty))
    assert m.current_drawdown(empty)["peak"] is None
    assert m.top_drawdowns(empty) == []


def test_unrecovered_drawdown_and_current():
    s = ser([100, 120, 90, 100, 120, 110])           # максимум 120 повторён 31.05
    top = m.top_drawdowns(s)
    assert [d["depth"] for d in top] == pytest.approx([-0.25, 110 / 120 - 1])
    assert top[1]["recovery"] is None and top[1]["daysToRecover"] is None
    assert top[1]["peak"] == "2020-05-31" and top[1]["daysToTrough"] == 30
    cur = m.current_drawdown(s)
    assert cur == {"depth": pytest.approx(110 / 120 - 1), "peak": "2020-05-31", "days": 30}
    assert m.avg_drawdown(s) == pytest.approx((-0.25 + 110 / 120 - 1) / 2)


def test_top_drawdowns_order_ties_and_k():
    s = ser([100, 90, 100, 90, 100, 80])
    top = m.top_drawdowns(s)
    assert [d["peak"] for d in top] == ["2020-05-31", "2020-01-31", "2020-03-31"]   # равные — более ранний
    assert len(m.top_drawdowns(s, 1)) == 1


def test_drawdowns_ignore_nan():
    s = ser([100, np.nan, 90, 100])
    assert m.top_drawdowns(s)[0]["depth"] == pytest.approx(-0.1)
    assert m.current_drawdown(s)["depth"] == 0


# ---------------------------------------------------------------- бенчмарк
def test_r_squared_and_capture_by_hand():
    r = ser([0.02, -0.01, 0.03, -0.02])
    rm = ser([0.01, -0.02, 0.02, -0.01])
    cap = m.capture_ratios(r, rm)
    assert cap["up"] == pytest.approx(0.05 / 0.03)
    assert cap["down"] == pytest.approx(1.0)
    assert m.r_squared(r, rm) == pytest.approx(m.correlation(r, rm) ** 2)
    assert m.r_squared(r, r) == pytest.approx(1.0)


def test_capture_no_down_periods_and_alignment():
    r = ser([0.02, -0.01, 0.03])
    rm = ser([0.01, 0.02, 0.03])
    cap = m.capture_ratios(r, rm)
    assert cap["up"] == pytest.approx(0.04 / 0.06) and math.isnan(cap["down"])
    # разные календари: inner join, NaN отбрасываются
    rm2 = pd.Series([0.01, -0.02, 0.5], index=pd.to_datetime(["2020-01-31", "2020-02-29", "2021-01-31"]))
    cap2 = m.capture_ratios(ser([0.02, np.nan, 0.03]), rm2)
    assert cap2["up"] == pytest.approx(2.0) and math.isnan(cap2["down"])


def test_r_squared_constant_benchmark_is_nan():
    assert math.isnan(m.r_squared(ser([0.01, 0.02, 0.03]), ser([0.01, 0.01, 0.01])))


def test_compute_all_new_keys():
    p = ser([100, 110, 99, 108.9])
    b = ser([100, 105, 99.75, 104.7375])
    rep = m.compute_all(p, b, 0, "M").to_dict()
    keys = list(rep)
    assert keys[keys.index("days_to_recover") + 1: keys.index("days_to_recover") + 4] == ["martin", "ulcer_index", "beta"]
    assert keys[-4:] == ["correlation", "r_squared", "up_capture", "down_capture"]
    assert rep["r_squared"] == pytest.approx(1.0)
    assert rep["up_capture"] == pytest.approx(2.0) and rep["down_capture"] == pytest.approx(2.0)
    nob = m.compute_all(p, None, 0, "M").to_dict()
    assert all(math.isnan(nob[k]) for k in ("r_squared", "up_capture", "down_capture"))
    assert len(m.LABELS_RU) == 29 and len(m.PERCENT_FIELDS) == 16
    assert set(m.LABELS_RU) == set(rep) - {"start", "end", "periods_per_year"}


# ---------------------------------------------------------------- волатильность
def test_rolling_volatility_full_windows_only():
    s = ser([100, 110, 99, 108.9])
    rv = m.rolling_volatility(s, 2, 12)
    assert list(rv.index.strftime("%Y-%m-%d")) == ["2020-03-31", "2020-04-30"]
    assert rv.to_numpy() == pytest.approx([math.sqrt(0.02) * math.sqrt(12)] * 2)
    assert m.rolling_volatility(s).empty


def _one_row():
    return {"dates": ["2024-01-02"], "open": [100.0], "high": [110.0], "low": [95.0], "close": [105.0]}


def test_ohlc_estimators_by_hand():
    o = _one_row()
    hl, co = math.log(110 / 95), math.log(105 / 100)
    assert m.parkinson(o, 1) == pytest.approx(math.sqrt(hl ** 2 / (4 * math.log(2))))
    assert m.garman_klass(o, 1) == pytest.approx(math.sqrt(0.5 * hl ** 2 - (2 * math.log(2) - 1) * co ** 2))
    rs = math.log(110 / 105) * math.log(110 / 100) + math.log(95 / 105) * math.log(95 / 100)
    assert m.rogers_satchell(o, 1) == pytest.approx(math.sqrt(rs))
    assert m.parkinson(o, 252) == pytest.approx(m.parkinson(o, 1) * math.sqrt(252))


def test_ohlc_dataframe_equals_dict_and_bad_rows_dropped():
    df = pd.DataFrame({"Open": [100, np.nan, 102, 101], "High": [103, 104, 0, 105],
                       "Low": [99, 100, 98, 98], "Close": [101, 103, 103, 104]},
                      index=pd.bdate_range("2024-01-01", periods=4))
    good = df.iloc[[0, 3]].rename(columns=str.lower)
    for fn in (m.parkinson, m.garman_klass, m.rogers_satchell):
        assert fn(df) == pytest.approx(fn(good))       # NaN-open и high = 0 отброшены
        d = {"dates": [str(x.date()) for x in df.index], **{k.lower(): list(df[k]) for k in df}}
        assert fn(d) == pytest.approx(fn(df))


def test_yang_zhang_by_hand_and_short():
    o = {"dates": ["2024-01-02", "2024-01-03", "2024-01-04"], "open": [100, 102, 101],
         "high": [103, 104, 105], "low": [99, 100, 98], "close": [101, 103, 104]}
    on = np.log([102 / 101, 101 / 103])
    oc = np.log([103 / 102, 104 / 101])

    def rs(h, l_, op, c):
        return math.log(h / c) * math.log(h / op) + math.log(l_ / c) * math.log(l_ / op)
    k = 0.34 / (1.34 + 3 / 1)
    v = np.var(on, ddof=1) + k * np.var(oc, ddof=1) + (1 - k) * (rs(104, 100, 102, 103) + rs(105, 98, 101, 104)) / 2
    assert m.yang_zhang(o, 1) == pytest.approx(math.sqrt(v), rel=1e-12)
    two = {k2: v2[:2] for k2, v2 in o.items()}
    assert math.isnan(m.yang_zhang(two))                       # N = 1 < 2
    assert math.isnan(m.yang_zhang(_one_row()))
    # третья строка испорчена — остаётся 2 строки, NaN
    bad = {**o, "low": [99, 100, np.nan]}
    assert math.isnan(m.yang_zhang(bad))


def test_ohlc_empty_is_nan():
    e = {"dates": [], "open": [], "high": [], "low": [], "close": []}
    for fn in (m.parkinson, m.garman_klass, m.rogers_satchell, m.yang_zhang):
        assert math.isnan(fn(e))


# ---------------------------------------------------------------- доходность по периодам
def test_minus_months_matches_dateoffset():
    for d in pd.date_range("2019-12-25", "2025-03-31", freq="7D").append(
            pd.to_datetime(["2024-02-29", "2024-03-31", "2023-05-31", "2024-12-31"])):
        for k in (1, 6, 12, 36, 60, 120):
            assert m._minus_months(d, k) == d - pd.DateOffset(months=k), (d, k)


def test_period_returns_end_of_month_and_ytd():
    s = pd.Series([90, 95, 100, 105, 110], index=pd.to_datetime(
        ["2023-12-29", "2024-02-28", "2024-02-29", "2024-03-29", "2024-03-31"]), dtype=float)
    p = m.period_returns(s)
    assert list(p) == m.PERIOD_RETURN_KEYS
    assert p["1M"] == {"ret": pytest.approx(0.1), "cagr": None, "from": "2024-02-29"}
    assert p["YTD"]["from"] == "2023-12-29" and p["YTD"]["ret"] == pytest.approx(110 / 90 - 1)
    assert p["1D"]["from"] == "2024-03-29"
    assert p["1W"]["from"] == "2024-02-29"
    assert p["1Y"] is None and p["10Y"] is None
    assert p["ALL"]["cagr"] is None                      # короче года


def test_period_returns_cagr_and_as_of():
    s = pd.Series([100, 121, 133.1], index=pd.to_datetime(["2020-01-31", "2022-01-31", "2023-01-31"]))
    p = m.period_returns(s)
    assert p["3Y"]["cagr"] == pytest.approx(1.331 ** (365.25 / 1096) - 1)
    assert p["ALL"]["cagr"] == pytest.approx(p["3Y"]["cagr"])
    assert p["1Y"] == {"ret": pytest.approx(0.1), "cagr": None, "from": "2022-01-31"}
    q = m.period_returns(s, "2022-06-15")                # конец — последняя точка ≤ as_of
    assert q["ALL"]["ret"] == pytest.approx(0.21)
    assert all(v is None for v in m.period_returns(s, "2019-01-01").values())
    assert all(v is None for v in m.period_returns(pd.Series([], dtype=float)).values())


# ---------------------------------------------------------------- моментум
def test_momentum_by_hand_200_points():
    idx = pd.bdate_range("2023-01-02", periods=260)
    s = pd.Series(np.linspace(100, 359, 260), index=idx)   # +1 в день
    mo = m.momentum(s)
    assert mo["sma50"] == pytest.approx(np.mean(s.iloc[-50:]))
    assert mo["sma200"] == pytest.approx(np.mean(s.iloc[-200:]))
    assert mo["aboveSma50"] is True and mo["aboveSma200"] is True
    assert mo["high52"] == pytest.approx(359) and mo["distHigh52"] == pytest.approx(0)
    T = idx[-1]
    a = s[s.index <= T - pd.DateOffset(months=1)].iloc[-1]
    b6 = s[s.index <= T - pd.DateOffset(months=6)].iloc[-1]
    assert mo["mom6"] == pytest.approx(a / b6 - 1)
    assert mo["mom12"] is None                              # истории меньше 12 мес. + запаса
    sub = [100, 100, 100, min(100, 50 + 250 * mo["mom6"])]
    assert mo["score"] == pytest.approx(sum(sub) / len(sub))


def test_momentum_short_and_falling():
    assert all(v is None for v in m.momentum(pd.Series([], dtype=float)).values())
    s = ser([100, 110, 99, 108.9])
    mo = m.momentum(s)
    assert mo["sma50"] is None and mo["score"] is None and mo["high52"] == 110
    idx = pd.bdate_range("2023-01-02", periods=300)
    fall = pd.Series(np.linspace(300, 100, 300), index=idx)
    f = m.momentum(fall)
    assert f["aboveSma50"] is False and f["score"] == pytest.approx(0)    # clamp снизу


# ---------------------------------------------------------------- помесячная сетка
def test_monthly_grid_monthly_series():
    s = pd.Series([100, 110, 99, 99], index=pd.to_datetime(["2020-11-30", "2020-12-31", "2021-01-31", "2021-12-31"]))
    g = m.monthly_grid(s)
    assert g["years"] == [2020, 2021]
    assert g["cells"][0][10] is None                         # первый месяц — только база
    assert g["cells"][0][11] == pytest.approx(0.1)
    assert g["cells"][1][0] == pytest.approx(-0.1)
    assert g["cells"][1][11] == pytest.approx(0.0)           # пропуск месяцев — от последней цены
    assert g["yearTotal"] == pytest.approx([0.1, -0.1])
    assert g["medianByMonth"][11] == pytest.approx(0.05) and g["medianByMonth"][5] is None


def test_monthly_grid_daily_and_edge():
    d = pd.Series([100, 105, 110], index=pd.to_datetime(["2021-01-05", "2021-01-29", "2021-02-01"]))
    g = m.monthly_grid(d)
    assert g["cells"][0][0] == pytest.approx(0.05) and g["cells"][0][1] == pytest.approx(110 / 105 - 1)
    e = m.monthly_grid(ser([100]))
    assert e == {"years": [], "cells": [], "yearTotal": [], "medianByMonth": [None] * 12}
    gap = pd.Series([1.0, 2.0], index=pd.to_datetime(["2019-12-31", "2021-01-31"]))
    gg = m.monthly_grid(gap)
    assert gg["years"] == [2019, 2020, 2021] and gg["yearTotal"][1] is None


# ---------------------------------------------------------------- полная доходность
def test_total_return_series_by_hand():
    s = pd.Series([110.0, 100.0, 100.0], index=pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-09"]))
    # отсечка в субботу 06.01 -> первая торговая дата ≥ — 08.01
    tr = m.total_return_series(s, [{"exDate": "2024-01-06", "value": 10}])
    assert tr.to_numpy() == pytest.approx([110, 110, 110])
    tr2 = m.total_return_series(s, pd.Series([10.0], index=pd.to_datetime(["2024-01-08"])))
    assert tr2.to_numpy() == pytest.approx([110, 110, 110])
    # на первую дату, вне ряда, без значений — не учитываются
    tr3 = m.total_return_series(s, [{"exDate": "2024-01-05", "value": 5}, {"exDate": "2025-01-01", "value": 5},
                                    {"ex_date": "2024-01-09", "value": 1}, {"exDate": None, "value": 3},
                                    {"exDate": "2024-01-09", "value": None}])
    assert tr3.to_numpy() == pytest.approx([110, 100 / 110 * 110, 100 / 110 * 110 * 101 / 100])
    assert m.total_return_series(s, None).to_numpy() == pytest.approx(s.to_numpy())


# ---------------------------------------------------------------- инвариант 3: формулы в методике
def test_methodology_pages_describe_stage1_metrics():
    """Новые коэффициенты описаны в обеих методиках: Streamlit (pages/7_Методика.py) и веб (web/index.html
    + web/js/tab-method.js)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    st_text = (root / "pages" / "7_Методика.py").read_text(encoding="utf-8")
    web_text = "".join(p.read_text(encoding="utf-8") for p in
                       [root / "web" / "index.html", *sorted((root / "web" / "js").glob("tab-method*.js"))])
    for word in ("язвы", "Мартин", "R^2", "capture", "Паркинсон", "Гарман", "Роджерс", "Янг", "моментум"):
        assert word.lower() in st_text.lower(), f"pages/7_Методика.py: нет «{word}»"
    for word in ("язвы", "Мартин", "R²", "capture", "Паркинсон", "Гарман", "Роджерс", "Янг", "моментум"):
        assert word.lower() in web_text.lower(), f"веб-методика: нет «{word}»"


# ---------------------------------------------------------------- period_start (второй круг)
def test_period_start_end_of_month_and_leap():
    d = ["2023-02-27", "2023-02-28", "2023-03-01", "2024-02-28", "2024-02-29", "2024-03-01", "2024-03-02",
         "2024-03-31", "2024-08-31"]
    assert m.period_start(d, "1M", "2024-03-31") == "2024-02-29"
    assert m.period_start(d, "6M", "2024-08-31") == "2024-02-29"
    assert m.period_start(d, "1Y", "2024-02-29") == "2023-02-28"
    assert m.period_start(d, "YTD", "2024-03-31") == "2023-03-01"
    assert m.period_start(d, "ALL") == "2023-02-27"
    assert m.period_start(d, "10Y") is None
    assert m.period_start([], "1M") is None
    assert m.period_start(pd.DatetimeIndex(d), "1M", "2024-03-31") == "2024-02-29"
    with pytest.raises(ValueError):
        m.period_start(d, "2M")


def test_period_returns_uses_period_start():
    s = pd.Series(np.linspace(100, 200, 600), index=pd.bdate_range("2022-01-03", periods=600))
    for as_of in (None, "2024-02-29", "2023-12-31"):
        pr = m.period_returns(s, as_of)
        for k in m.PERIOD_RETURN_KEYS:
            assert (pr[k]["from"] if pr[k] else None) == m.period_start(s.index, k, as_of), (as_of, k)


# ---------------------------------------------------------------- сплиты (утилита для рядов /history)
def test_adjust_splits_real_iss_format_matches_candles():
    """Формат splits.json ISS (tradedate — первый день в новых акциях). Сырые цены /history
    (GMKN LEGALCLOSEPRICE 15054 до сплита 1:100 от 08.04.2024) после корректировки совпадают по масштабу
    со свечами ISS, которые биржа уже скорректировала (≈ 151)."""
    from core.analytics import metrics as M
    splits = [{"date": "2024-04-08", "before": 1, "after": 100}]
    s = pd.Series([15054.0, 15054.0, 152.96, 164.0],
                  index=pd.to_datetime(["2024-04-04", "2024-04-05", "2024-04-08", "2024-04-09"]))
    adj = M.adjust_splits(s, splits)
    assert adj.tolist() == pytest.approx([150.54, 150.54, 152.96, 164.0])
    # консолидация VTBR 5000:1 от 15.07.2024
    v = pd.Series([0.01992, 92.95], index=pd.to_datetime(["2024-07-12", "2024-07-15"]))
    assert M.adjust_splits(v, [{"date": "2024-07-15", "before": 5000, "after": 1}]).tolist() == pytest.approx([99.6, 92.95])
    # объём делится на тот же множитель
    d = {"dates": ["2024-04-05", "2024-04-08"], "close": [15054.0, 152.96], "volume": [10.0, 1000.0]}
    out = M.adjust_splits(d, splits)
    assert out["close"] == pytest.approx([150.54, 152.96]) and out["volume"] == pytest.approx([1000.0, 1000.0])
