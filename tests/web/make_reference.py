"""
Генератор эталонов для тестов JS-движка (tests/web/*.test.mjs): результаты Python-кода core/
на тех же входах сохраняются в tests/fixtures/web_reference.json.

    python -m tests.web.make_reference

Запускается вручную. Перегенерировать, когда:
  * меняется Python-методика в core/analytics (metrics.py, frontier.py, backtest.py) или
    core/data/universe.chain, ui/common.annual_returns — и JS-порт меняется вместе с ней;
  * меняются фикстуры tests/fixtures/*.csv;
  * в этом файле добавляются новые эталонные случаи.
Если после перегенерации меняются цифры сверки с табл. 4 НИР — это изменение методики
(инвариант 2 CLAUDE.md), а не «починка эталона».

Сеть не нужна: данные — только месячные фикстуры и синтетика с фиксированным seed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from core.analytics import metrics as m
from core.analytics.backtest import BacktestConfig, run_backtest
from core.analytics.frontier import estimate_inputs, max_sharpe, min_variance
from core.analytics.strategies import BENCHMARKS, STRATEGIES
from core.data.universe import chain
from scripts.reproduce_nir import load_fixture

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "web_reference.json"

RF = 0.0786
START, END = "2011-01-31", "2025-12-31"
FRONTIER_ASSETS = ["MCFTR", "CORP_CHAIN", "RGBITR", "GOLD_CBR"]
FRONTIER_BOUNDS = [(0.0, 1.0), (0.05, 0.6)]


def clean(x):
    """NaN/inf -> None (JSON без NaN), numpy -> python."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return None if not math.isfinite(x) else x
    if isinstance(x, pd.Timestamp):
        return str(x.date())
    return x


def series_json(s: pd.Series) -> dict:
    return {"dates": [str(d.date()) for d in s.index], "values": [clean(v) for v in s.values]}


def frame_json(df: pd.DataFrame) -> dict:
    return {"dates": [str(d.date()) for d in df.index],
            "cols": {c: [clean(v) for v in df[c].values] for c in df.columns}}


def annual_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Копия ui/common.annual_returns (ui/common импортирует streamlit — не тянем его сюда)."""
    y = prices.resample("YE").last()
    first = prices.iloc[[0]]
    y = pd.concat([first, y])
    r = y.pct_change().dropna(how="all")
    r.index = r.index.year
    return r[~r.index.duplicated(keep="last")]


def metrics_block(fx: pd.DataFrame) -> dict:
    """compute_all по каждому столбцу фикстуры на окне НИР (бенчмарк MCFTR, rf=7,86 %, M)."""
    px = fx.loc[START:END]
    bench = px["MCFTR"]
    out = {}
    for c in px.columns:
        s = px[c].dropna()
        if len(s) < 3:
            continue
        out[c] = clean(m.compute_all(s, bench, RF, "M").to_dict())
    # без бенчмарка и с rf = 0
    out["__MCFTR_nobench"] = clean(m.compute_all(px["MCFTR"], None, 0.0, "M").to_dict())
    # квартальная частота
    out["__GOLD_CBR_Q"] = clean(m.compute_all(px["GOLD_CBR"], bench, RF, "Q").to_dict())
    # cagr-режим числителя Шарпа/Трейнора
    out["__RGBITR_cagr"] = clean(m.compute_all(px["RGBITR"], bench, RF, "M",
                                               return_method="cagr").to_dict())
    return out


def rf_series_case(fx: pd.DataFrame) -> dict:
    """Ряд ставок вместо числа: ставка меняется ступенькой, первая дата ряда позже начала."""
    px = fx.loc[START:END]
    rf = pd.Series([0.07, 0.10, 0.16, 0.12],
                   index=pd.to_datetime(["2011-06-30", "2014-12-31", "2022-02-28", "2023-06-30"]))
    rep = m.compute_all(px["MCFTR"], px["RGBITR"], rf, "M").to_dict()
    return {"rf": series_json(rf), "asset": "MCFTR", "bench": "RGBITR", "report": clean(rep)}


def backtest_block(fx: pd.DataFrame) -> dict:
    px = fx.loc[START:END]
    out = {}
    eq = {}
    for name, s in STRATEGIES.items():
        r = run_backtest(px, BacktestConfig(weights=s["weights"], initial=1_000_000, rebalance="A"))
        eq[name] = r.equity
        out[name] = {"final": float(r.equity.iloc[-1]),
                     "rebalance_dates": [str(d.date()) for d in r.rebalance_dates]}
    eq["MCFTR"] = px["MCFTR"] / px["MCFTR"].iloc[0] * 1_000_000
    tbl = {k: clean(m.compute_all(v, eq["MCFTR"], RF, "M").to_dict()) for k, v in eq.items()}
    # вариант с комиссией, коридором, пополнениями и налогом
    cfg = BacktestConfig(weights={"MCFTR": 0.6, "RGBITR": 0.4}, initial=1_000_000, rebalance="Q",
                         band=0.03, commission=0.001, contribution=10_000, contribution_freq="M",
                         tax_rate=0.13)
    r = run_backtest(px, cfg)
    extra = {"cfg": {"weights": cfg.weights, "initial": cfg.initial, "rebalance": cfg.rebalance,
                     "band": cfg.band, "commission": cfg.commission,
                     "contribution": cfg.contribution, "contributionFreq": cfg.contribution_freq,
                     "taxRate": cfg.tax_rate},
             "final": float(r.equity.iloc[-1]), "invested": float(r.invested.iloc[-1]),
             "costs": r.costs, "taxes": r.taxes, "turnover": r.turnover,
             "rebalance_dates": [str(d.date()) for d in r.rebalance_dates],
             "n_trades": len(r.trades)}
    return {"strategies": out, "metrics": tbl, "complex": clean(extra)}


def frontier_block(fx: pd.DataFrame) -> dict:
    px = fx.loc[START:END, FRONTIER_ASSETS]
    inp = estimate_inputs(px, "M", RF)
    out = {"assets": FRONTIER_ASSETS, "rf": RF, "start": START, "end": END,
           "mu": clean(list(inp.mu.values)), "cov": clean(inp.cov.values.tolist()), "cases": []}
    for lo, hi in FRONTIER_BOUNDS:
        wmv = min_variance(inp, lo, hi)
        wms = max_sharpe(inp, lo, hi)
        out["cases"].append({"wMin": lo, "wMax": hi,
                             "minVar": clean(dict(zip(FRONTIER_ASSETS, wmv))),
                             "maxSharpe": clean(dict(zip(FRONTIER_ASSETS, wms)))})
    inp_c = estimate_inputs(px, "M", RF, mu_method="cagr")
    out["mu_cagr"] = clean(list(inp_c.mu.values))
    inp_q = estimate_inputs(px, "Q", RF)
    out["mu_q"] = clean(list(inp_q.mu.values))
    out["cov_q"] = clean(inp_q.cov.values.tolist())
    return out


def synthetic_daily() -> pd.DataFrame:
    """Рабочие дни 2019-12-25…2022-01-12 (через границы лет/кварталов), пропуски и пустая неделя."""
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2019-12-25", "2022-01-12")
    a = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(idx)))
    b = 50 * np.cumprod(1 + rng.normal(0.0002, 0.006, len(idx)))
    df = pd.DataFrame({"A": a, "B": b}, index=idx)
    df.iloc[5:9, 0] = np.nan                                     # пропуски в A
    df.loc["2020-03-01":"2020-04-15", "B"] = np.nan              # B пуст весь апрель 1-й половины
    df = df.drop(pd.bdate_range("2021-05-03", "2021-05-14"))     # две недели без торгов
    df.iloc[-3:, 1] = np.nan                                     # хвост B пуст
    return df


def resample_block() -> dict:
    df = synthetic_daily()
    out = {"input": frame_json(df)}
    for f in ["W", "M", "Q", "A"]:
        out[f] = frame_json(m.resample_prices(df, f))
    out["series_A_M"] = series_json(m.resample_prices(df["A"], "M"))
    return out


def chain_block(fx: pd.DataFrame) -> dict:
    cases = []
    cases.append({"name": "fixture", "old": series_json(fx["RUCBITR"]), "new": series_json(fx["RUCBTRNS"]),
                  "switch": "2018-12-29", "result": series_json(chain(fx["RUCBITR"], fx["RUCBTRNS"], "2018-12-29"))})
    # старый ряд без точки на якоре; переключение после конца нового
    old = pd.Series([10.0, 11.0, 12.0, 13.0], index=pd.to_datetime(["2020-01-01", "2020-01-03", "2020-01-08", "2020-01-10"]))
    new = pd.Series([100.0, 105.0, 110.0], index=pd.to_datetime(["2020-01-06", "2020-01-07", "2020-01-09"]))
    for sw in ["2020-01-07", "2020-01-02", "2030-01-01"]:
        cases.append({"name": f"synthetic {sw}", "old": series_json(old), "new": series_json(new),
                      "switch": sw, "result": series_json(chain(old, new, sw))})
    return cases


def annual_block(fx: pd.DataFrame) -> dict:
    cols = ["MCFTR", "RUCBTRNS", "IRDIVTR", "GOLD_CBR"]
    df = fx[cols]
    r = annual_returns(df)
    df2 = synthetic_daily()
    r2 = annual_returns(df2)
    return {
        "fixture": {"input": frame_json(df), "years": [int(y) for y in r.index],
                    "cols": {c: [clean(v) for v in r[c].values] for c in cols}},
        "daily": {"input": frame_json(df2), "years": [int(y) for y in r2.index],
                  "cols": {c: [clean(v) for v in r2[c].values] for c in df2.columns}},
    }


def labels_block() -> dict:
    """LABELS_RU (порядок строк) и PERCENT_FIELDS — справочники должны совпадать с JS."""
    return {"labels_ru": [[k, v] for k, v in m.LABELS_RU.items()],
            "percent_fields": sorted(m.PERCENT_FIELDS)}


def synthetic_ohlc() -> pd.DataFrame:
    """Дневные OHLC 2014-02-25…2026-03-16 (рабочие дни, через 29.02 и концы месяцев), seed 11.
    Плохие строки: NaN в open, high = 0, low < 0 — оценки по OHLC их отбрасывают, close остаётся.
    Две длинные просадки (одна не восстановлена к концу ряда)."""
    rng = np.random.default_rng(11)
    idx = pd.bdate_range("2014-02-25", "2026-03-16")
    drift = np.full(len(idx), 0.0004)
    drift[800:1100] = -0.0025          # глубокая просадка с восстановлением
    drift[-260:] = -0.0012             # невосстановленная просадка в конце
    c = 100 * np.cumprod(1 + drift + rng.normal(0, 0.011, len(idx)))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.004, len(idx)))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, len(idx))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, len(idx))))
    o[10] = np.nan
    h[20] = 0.0
    lo[30] = -1.0
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c}, index=idx)


def ohlc_json(df: pd.DataFrame) -> dict:
    return {"dates": [str(d.date()) for d in df.index],
            **{k: [clean(v) for v in df[k].values] for k in ("open", "high", "low", "close")}}


def stage1_block(fx: pd.DataFrame) -> dict:
    """Новые функции этапа 1 (web/CONTRACT.md): паритет JS ↔ Python до 1e-9."""
    df = synthetic_ohlc()
    s = df["close"]
    oj = ohlc_json(df)
    ohlc = {}
    for name, part in {"full": df, "last_year": df.iloc[-252:], "rows3": df.iloc[-3:],
                       "rows2": df.iloc[-2:], "rows1": df.iloc[-1:]}.items():
        pj = ohlc_json(part)
        ohlc[name] = {"input": pj, **{f"{fn}_{n}": clean(getattr(m, fn)(pj, n))
                                      for fn in ("parkinson", "garman_klass", "rogers_satchell", "yang_zhang")
                                      for n in (252, 12)}}

    mc = fx["MCFTR"].dropna()
    short = s.iloc[-120:]                          # < 200 точек: SMA200 и mom12 недоступны
    tiny = s.iloc[-30:]                            # < 50 точек
    gap = pd.concat([s.loc["2016-01-01":"2016-12-31"], s.loc["2018-03-01":"2019-06-30"]])   # год без точек
    flat = pd.Series(100.0, index=pd.bdate_range("2024-01-01", periods=40))
    rising = pd.Series(np.linspace(100, 140, 300), index=pd.bdate_range("2023-01-02", periods=300))
    series = {"daily": s, "mcftr_m": mc, "short": short, "tiny": tiny, "gap": gap, "flat": flat, "rising": rising}

    per_series = {}
    for k, x in series.items():
        per_series[k] = {
            "input": series_json(x),
            "ulcer": clean(m.ulcer_index(x)), "martin_0": clean(m.martin(x)), "martin_5": clean(m.martin(x, 0.05)),
            "rolling_21_252": series_json(m.rolling_volatility(x)),
            "rolling_5_12": series_json(m.rolling_volatility(x, 5, 12)),
            "period": clean(m.period_returns(x)),
            "momentum": clean(m.momentum(x)), "grid": clean(m.monthly_grid(x)),
            "top5": clean(m.top_drawdowns(x)), "top2": clean(m.top_drawdowns(x, 2)),
            "current": clean(m.current_drawdown(x)), "avg": clean(m.avg_drawdown(x)),
        }

    as_of = {}
    for d in ["2024-02-29", "2025-03-31", "2024-03-30", "2023-12-31", "2014-03-03", "2010-01-01", "2030-01-01"]:
        as_of[d] = clean(m.period_returns(s, d))

    divs = [{"exDate": "2015-07-04", "value": 3.0},       # суббота -> понедельник 06.07
            {"exDate": "2019-12-31", "value": 1.5}, {"exDate": "2019-12-31", "value": 0.5},   # две в один день
            {"exDate": "2014-02-25", "value": 7.0},       # = первая дата ряда — не учитывается
            {"exDate": "2010-01-01", "value": 5.0},       # раньше ряда
            {"exDate": "2030-01-01", "value": 9.0},       # позже ряда
            {"ex_date": "2021-06-15", "value": 2.0},      # snake_case
            {"exDate": None, "value": 1.0}, {"exDate": "2022-01-10", "value": None}]
    tr = series_json(m.total_return_series(s, divs))

    r, rm = m.to_returns(fx["IRDIVTR"].dropna()), m.to_returns(fx["MCFTR"].dropna())
    rg = m.to_returns(fx["GOLD_CBR"].dropna())
    up_only = rm[rm > 0]
    bench = {
        "r": series_json(r), "rm": series_json(rm), "rg": series_json(rg), "rm_up": series_json(up_only),
        "cap_div": clean(m.capture_ratios(r, rm)), "r2_div": clean(m.r_squared(r, rm)),
        "cap_gold": clean(m.capture_ratios(rg, rm)), "r2_gold": clean(m.r_squared(rg, rm)),
        "cap_up_only": clean(m.capture_ratios(r, up_only)),
    }
    # period_start: даты дневного ряда и концов месяцев; конец месяца, 29.02, as_of между датами / до начала ряда
    eom = ["2023-02-28", "2023-03-31", "2023-08-31", "2023-12-29", "2024-01-31", "2024-02-29", "2024-03-29",
           "2024-03-31", "2024-08-30", "2024-08-31", "2025-02-28"]
    date_sets = {"daily": [str(d.date()) for d in s.index], "eom": eom}
    pstart = []
    for name, ds in date_sets.items():
        for a in [None, "2024-02-29", "2024-03-31", "2024-08-31", "2025-02-28", "2025-03-01", "2014-01-01"]:
            for k in m.PERIOD_RETURN_KEYS:
                pstart.append({"dates": name, "period": k, "as_of": a, "result": m.period_start(ds, k, a)})
    return {"ohlc": ohlc, "series": per_series, "as_of": as_of, "divs": divs, "tr": tr, "bench": bench,
            "period_start": {"dates": {"eom": eom}, "cases": pstart}}


def main():
    fx = load_fixture()
    ref = {
        "_note": "Эталоны Python core/ для tests/web. Генератор: python -m tests.web.make_reference",
        "pandas": pd.__version__, "numpy": np.__version__,
        "params": {"rf": RF, "start": START, "end": END, "freq": "M", "benchmark": "MCFTR"},
        "demo_frame": frame_json(fx),
        "metrics": metrics_block(fx),
        "rf_series": rf_series_case(fx),
        "backtest": backtest_block(fx),
        "frontier": frontier_block(fx),
        "resample": resample_block(),
        "chain": chain_block(fx),
        "annual_returns": annual_block(fx),
        "labels": labels_block(),
        "stage1": stage1_block(fx),
        "strategies": {k: {"weights": v["weights"], "rebalance": v["rebalance"]} for k, v in STRATEGIES.items()},
        "benchmarks": BENCHMARKS,
    }
    OUT.write_text(json.dumps(ref, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"Записано: {OUT} ({OUT.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
