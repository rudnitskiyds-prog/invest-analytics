import pandas as pd
import pytest

from core.portfolio.ledger import Ledger, positions, valuation, value_history
from core.portfolio.rebalance import drift_report, rebalance_orders, schedule, to_ics


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "p.sqlite")


def test_ledger_flow(ledger):
    pid = ledger.create_portfolio("Тест", target={"EQMX": 0.5, "GOLD": 0.5})
    ledger.add(pid, "2025-01-10", "DEPOSIT", amount=100_000)
    ledger.add(pid, "2025-01-10", "BUY", "EQMX", 100, 200, fee=10)
    ledger.add(pid, "2025-01-10", "BUY", "GOLD", 1000, 2)
    ledger.add(pid, "2025-06-10", "SELL", "EQMX", 50, 260, fee=5)
    ledger.add(pid, "2025-07-01", "DIVIDEND", "EQMX", amount=150)
    tx = ledger.transactions(pid)
    pos, cash = positions(tx)
    p = {x.secid: x for x in pos}
    assert p["EQMX"].qty == 50
    assert p["EQMX"].avg_cost == pytest.approx(200.1)
    assert p["EQMX"].realized == pytest.approx(50 * 260 - 5 - 50 * 200.1)
    assert cash == pytest.approx(100_000 - 20_010 - 2_000 + 13_000 - 5 + 150)
    q = pd.DataFrame({"price": [300.0, 2.5], "name": ["EQMX", "GOLD"], "lot": [1, 1]}, index=["EQMX", "GOLD"])
    val, c = valuation(tx, q)
    assert val.set_index("Тикер").loc["EQMX", "Стоимость"] == 15_000
    assert ledger.portfolio(pid)["target"] == {"EQMX": 0.5, "GOLD": 0.5}


def test_value_history_twr_ignores_deposits(ledger):
    pid = ledger.create_portfolio("TWR")
    ledger.add(pid, "2025-01-01", "DEPOSIT", amount=1000)
    ledger.add(pid, "2025-01-01", "BUY", "A", 10, 100)
    ledger.add(pid, "2025-01-03", "DEPOSIT", amount=1000)
    idx = pd.date_range("2025-01-01", periods=4)
    prices = pd.DataFrame({"A": [100, 110, 110, 121.0]}, index=idx)
    h = value_history(ledger.transactions(pid), prices)
    assert h["value"].iloc[-1] == pytest.approx(1210 + 1000)
    # TWR = 1.1 × 1 × (2210/2100)
    assert h["twr"].iloc[-1] == pytest.approx(1.1 * 2210 / 2100)


def test_schedule_last_business_day():
    d = schedule("Q", "2026-09-29", 3)
    assert d[0] == pd.Timestamp("2026-09-30")
    assert d[1] == pd.Timestamp("2026-12-31")
    a = schedule("A", "2026-09-29", 2)
    assert a[0] == pd.Timestamp("2026-12-31") and a[1] == pd.Timestamp("2027-12-31")


def test_orders_and_drift():
    values = pd.Series({"A": 70_000.0, "B": 30_000.0})
    target = pd.Series({"A": 0.5, "B": 0.5})
    prices = pd.Series({"A": 100.0, "B": 10.0})
    lots = pd.Series({"A": 10, "B": 1})
    dr = drift_report(values / values.sum(), target, 0.05)
    assert dr["Вне коридора"].all()
    o = rebalance_orders(values, target, prices, lots)
    assert o.loc["A", "Действие"] == "Продать" and o.loc["A", "Бумаг"] == 200
    assert o.loc["B", "Действие"] == "Купить" and o.loc["B", "Бумаг"] == 2000
    ics = to_ics([pd.Timestamp("2026-12-31")], "Тест")
    assert "DTSTART;VALUE=DATE:20261231" in ics
