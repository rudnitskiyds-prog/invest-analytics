import numpy as np
import pandas as pd
import pytest

from core.analytics import metrics as m
from core.analytics.backtest import BacktestConfig, run_backtest, period_end_dates, xirr
from core.analytics.frontier import estimate_inputs, efficient_frontier


def make_prices(seed=0, years=10, mu=(0.12, 0.08, 0.06), vol=(0.20, 0.07, 0.15)):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2011-01-03", periods=252 * years)
    n = len(mu)
    corr = np.array([[1, 0.3, -0.1], [0.3, 1, 0.1], [-0.1, 0.1, 1]])[:n, :n]
    cov = np.outer(vol, vol) * corr / 252
    r = rng.multivariate_normal(np.array(mu) / 252, cov, len(idx))
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=["EQ", "BD", "GD"][:n])


def test_cagr_and_calmar_exact():
    idx = pd.to_datetime(["2020-01-01", "2021-01-01", "2022-01-01"])
    p = pd.Series([100, 50, 121], index=idx)
    assert m.total_return(p) == pytest.approx(0.21)
    assert m.max_drawdown(p) == pytest.approx(-0.5)
    assert m.cagr(p) == pytest.approx(1.21 ** (365.25 / 731) - 1, rel=1e-6)
    assert m.calmar(p) == pytest.approx(m.cagr(p) / 0.5)


def test_beta_alpha_self_benchmark():
    px = make_prices()
    rep = m.compute_all(px["EQ"], px["EQ"], rf=0.07, freq="M")
    assert rep.beta == pytest.approx(1.0)
    assert rep.alpha == pytest.approx(0.0, abs=1e-12)
    assert rep.tracking_error == pytest.approx(0.0, abs=1e-12)
    assert rep.correlation == pytest.approx(1.0)
    assert rep.m2 == pytest.approx(rep.sharpe * rep.volatility + 0.07)


def test_sharpe_formula():
    px = make_prices()["EQ"]
    r = m.to_returns(m.resample_prices(px, "M"))
    rf = 0.05
    rfp = (1 + rf) ** (1 / 12) - 1
    exp = (r - rfp).mean() / (r - rfp).std() * np.sqrt(12)
    assert m.sharpe(r, rf, 12) == pytest.approx(exp)


def test_omega_schwager():
    r = pd.Series([0.02, -0.01, 0.03, -0.02], index=pd.date_range("2020-01-31", periods=4, freq="ME"))
    assert m.schwager(r) == pytest.approx(0.05 / 0.03)
    assert m.omega(r, 0.0, 12) == pytest.approx(0.05 / 0.03)


def test_backtest_no_rebalance_equals_buy_and_hold():
    px = make_prices()
    cfg = BacktestConfig(weights={"EQ": 0.5, "BD": 0.5}, initial=1e6, rebalance="none")
    res = run_backtest(px, cfg)
    bh = 5e5 * px["EQ"] / px["EQ"].iloc[0] + 5e5 * px["BD"] / px["BD"].iloc[0]
    assert np.allclose(res.equity.values, bh.values)


def test_backtest_annual_rebalance_restores_weights():
    px = make_prices()
    res = run_backtest(px, BacktestConfig(weights={"EQ": 0.6, "BD": 0.4}, rebalance="A"))
    for d in res.rebalance_dates:
        assert res.weights.loc[d, "EQ"] == pytest.approx(0.6)
    # ребалансировка в последний торговый день декабря
    assert all(d.month == 12 for d in res.rebalance_dates)
    assert len(res.rebalance_dates) == 9


def test_backtest_commission_reduces_value():
    px = make_prices()
    a = run_backtest(px, BacktestConfig(weights={"EQ": .6, "BD": .4}, rebalance="M"))
    b = run_backtest(px, BacktestConfig(weights={"EQ": .6, "BD": .4}, rebalance="M", commission=0.001))
    assert b.final_value < a.final_value and b.costs > 0


def test_contributions_tracked():
    px = make_prices(years=2)
    res = run_backtest(px, BacktestConfig(weights={"EQ": .5, "BD": .5}, contribution=10000,
                                          contribution_freq="M"))
    assert res.invested.iloc[-1] == pytest.approx(1e6 + 10000 * 23)


def test_frontier_properties():
    px = make_prices(years=15)
    inp = estimate_inputs(px, "M", rf=0.05)
    fr = efficient_frontier(inp, n_points=20, n_random=2000)
    assert fr.min_var.sum() == pytest.approx(1, abs=1e-6)
    assert (fr.max_sharpe >= -1e-9).all()
    mv = fr.point(fr.min_var)
    # ни один случайный портфель не лучше границы по риску
    assert fr.random["vol"].min() >= mv["vol"] - 1e-6
    ms = fr.point(fr.max_sharpe)
    assert fr.random["sharpe"].max() <= ms["sharpe"] + 1e-6
    # граница монотонна
    f = fr.frontier.sort_values("ret")
    assert (np.diff(f["vol"].values) >= -1e-6).all()


def test_xirr_simple():
    cf = [(pd.Timestamp("2020-01-01"), -100), (pd.Timestamp("2021-01-01"), 110)]
    assert xirr(cf) == pytest.approx(0.1, abs=2e-3)
