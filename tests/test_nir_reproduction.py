"""Движок бэктеста воспроизводит табл. 4 НИР на данных ISS MOEX / ЦБ РФ (фикстуры)."""
import pytest

from scripts.reproduce_nir import NIR_TABLE4, run

TOL = {"total_return": 0.03, "cagr": 0.002, "volatility": 0.003, "max_drawdown": 0.004,
       "beta": 0.005, "tracking_error": 0.002, "sharpe": 0.02, "calmar": 0.015, "alpha": 0.003}


@pytest.fixture(scope="module")
def table():
    return run()[0]


@pytest.mark.parametrize("col", list(NIR_TABLE4.columns))
def test_matches_thesis(table, col):
    for k, tol in TOL.items():
        assert table.loc[k, col] == pytest.approx(NIR_TABLE4.loc[k, col], abs=tol), (col, k)


def test_final_values(table):
    _, eq = run()
    assert eq["Лежебока плюс (Спирин)"].iloc[-1] == pytest.approx(5_618_413, rel=1e-3)
    assert eq["MCFTR"].iloc[-1] == pytest.approx(3_842_075, rel=1e-4)
