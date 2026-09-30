"""
Сверка движка с табл. 4 НИР на месячных данных 2011–2025
(tests/fixtures: индексы ISS MOEX и ряды Банка России на конец месяца).

    python -m scripts.reproduce_nir
"""
import pandas as pd

from core.analytics import metrics as m
from core.analytics.backtest import BacktestConfig, run_backtest
from core.analytics.strategies import STRATEGIES
from core.config import ROOT
from core.data.universe import chain

FIX = ROOT / "tests" / "fixtures"

NIR_TABLE4 = pd.DataFrame({
    "Портфель 60/40": [2.711, .092, .140, -.327, .154, .133, .282, .669, -.001, .044, .075],
    "Постоянный портфель (Браун)": [3.908, .113, .080, -.196, .422, .417, .578, .287, .024, .145, .155],
    "Всепогодный портфель (Далио)": [2.154, .081, .115, -.281, .068, .059, .287, .490, -.009, .032, .118],
    "Портфель 40/40/20": [4.086, .116, .095, -.254, .391, .373, .457, .408, .023, .111, .129],
    "Лежебока плюс (Спирин)": [4.618, .123, .091, -.217, .478, .500, .568, .297, .033, .173, .158],
    "MCFTR": [2.842, .095, .203, -.513, .169, .149, .185, 1.0, 0.0, .042, 0.0],
}, index=["total_return", "cagr", "volatility", "max_drawdown", "sharpe", "sortino", "calmar",
          "beta", "alpha", "treynor", "tracking_error"])


def load_fixture() -> pd.DataFrame:
    a = pd.read_csv(FIX / "iss_monthly.csv", dtype={"m": str})
    b = pd.read_csv(FIX / "cbr_monthly.csv", dtype={"m": str})
    c = pd.read_csv(FIX / "iss_monthly_extra.csv", dtype={"m": str})
    df = a.merge(b, on="m").merge(c, on="m")
    df.index = pd.to_datetime(df.pop("m"), format="%Y%m") + pd.offsets.MonthEnd(0)
    df["CORP_CHAIN"] = chain(df["RUCBITR"], df["RUCBTRNS"], "2018-12-29")
    return df


def run(rf=0.0786, start="2011-01-31", end="2025-12-31") -> tuple[pd.DataFrame, dict]:
    px = load_fixture().loc[start:end]
    eq = {}
    for name, s in STRATEGIES.items():
        if "Баффетт" in name:
            continue
        r = run_backtest(px, BacktestConfig(weights=s["weights"], initial=1_000_000, rebalance="A"))
        eq[name] = r.equity
    eq["MCFTR"] = px["MCFTR"] / px["MCFTR"].iloc[0] * 1_000_000
    tbl = m.metrics_table(eq, eq["MCFTR"], rf, "M")
    return tbl, eq


if __name__ == "__main__":
    tbl, eq = run()
    rows = NIR_TABLE4.index
    pd.set_option("display.width", 200)
    for col in NIR_TABLE4.columns:
        cmp = pd.DataFrame({"НИР": NIR_TABLE4[col], "Платформа": tbl.loc[rows, col].astype(float)})
        cmp["Δ"] = cmp["Платформа"] - cmp["НИР"]
        print(f"\n=== {col}: итог {eq[col].iloc[-1]:,.0f} руб.")
        print(cmp.round(3).to_string())
