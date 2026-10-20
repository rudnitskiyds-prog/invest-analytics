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


# ------------------------------------------------------------------ value_history: пропуски цен
def test_value_history_before_first_price(ledger):
    """B куплена 03.01, первая цена — 04.01: 03.01 оценивается по первой цене B (bfill ведущих NaN),
    пропусков нет; пополнение 03.01 учитывается в тот же день."""
    pid = ledger.create_portfolio("До первой цены")
    ledger.add(pid, "2025-01-01", "DEPOSIT", amount=1000)
    ledger.add(pid, "2025-01-01", "BUY", "A", 10, 100)
    ledger.add(pid, "2025-01-03", "DEPOSIT", amount=500)
    ledger.add(pid, "2025-01-03", "BUY", "B", 5, 100)
    idx = pd.date_range("2025-01-01", periods=4)
    prices = pd.DataFrame({"A": [100, 110, 115, 121.0], "B": [float("nan")] * 3 + [104.0]}, index=idx)
    h = value_history(ledger.transactions(pid), prices)
    assert h.attrs["missing_prices"] == []
    assert not h[["value", "twr"]].isna().any().any()
    assert list(h["value"]) == pytest.approx([1000, 1100, 1150 + 520, 1210 + 520])
    # r: 02.01 = 1100/1000; 03.01 = (1670 − 500)/1100; 04.01 = 1730/1670
    assert list(h["twr"]) == pytest.approx([1.0, 1.1, 1.1 * 1170 / 1100, 1.1 * 1170 / 1100 * 1730 / 1670])
    assert list(h["flows"]) == [1000, 0, 500, 0]
    assert h["cash"].iloc[-1] == pytest.approx(0)


def test_value_history_buy_on_saturday(ledger):
    """Покупка в субботу 11.01.2025, цены с понедельника 13.01 → без NaN, missing_prices пуст."""
    pid = ledger.create_portfolio("Суббота")
    ledger.add(pid, "2025-01-11", "DEPOSIT", amount=1000)
    ledger.add(pid, "2025-01-11", "BUY", "A", 10, 100)
    idx = pd.to_datetime(["2025-01-13", "2025-01-14"])
    h = value_history(ledger.transactions(pid), pd.DataFrame({"A": [102.0, 105.0]}, index=idx))
    assert list(h.index) == list(pd.to_datetime(["2025-01-11", "2025-01-13", "2025-01-14"]))
    assert h.attrs["missing_prices"] == []
    assert list(h["value"]) == pytest.approx([1020, 1020, 1050])
    assert h["twr"].iloc[-1] == pytest.approx(1050 / 1020)


def test_value_history_whole_column_nan(ledger):
    """Курс ЦБ не получен: столбец валютной облигации целиком NaN → стоимость и TWR NaN везде."""
    pid = ledger.create_portfolio("FX")
    ledger.add(pid, "2025-01-01", "DEPOSIT", amount=200_000)
    ledger.add(pid, "2025-01-01", "BUY", "RUB1", 100, 600)
    ledger.add(pid, "2025-01-01", "BUY", "USD1", 1, 84_000)
    idx = pd.date_range("2025-01-01", periods=3)
    prices = pd.DataFrame({"RUB1": [600.0, 605, 610], "USD1": float("nan")}, index=idx)
    h = value_history(ledger.transactions(pid), prices)
    assert h["value"].isna().all() and h["twr"].isna().all()
    assert h.attrs["missing_prices"] == ["USD1"]


def test_value_history_nan_price_zero_qty(ledger):
    """C куплена и продана в один день (qty = 0): её NaN-цена не портит сумму."""
    pid = ledger.create_portfolio("Ноль")
    ledger.add(pid, "2025-01-01", "DEPOSIT", amount=1000)
    ledger.add(pid, "2025-01-01", "BUY", "A", 10, 100)
    ledger.add(pid, "2025-01-01", "BUY", "C", 1, 50)
    ledger.add(pid, "2025-01-01", "SELL", "C", 1, 50)
    idx = pd.date_range("2025-01-01", periods=3)
    prices = pd.DataFrame({"A": [100, 110, 121.0], "C": float("nan")}, index=idx)
    h = value_history(ledger.transactions(pid), prices)
    assert h.attrs["missing_prices"] == []
    assert list(h["value"]) == pytest.approx([1000, 1100, 1210])
    assert h["twr"].iloc[-1] == pytest.approx(1.21)


def test_value_history_no_gaps_as_before(ledger):
    pid = ledger.create_portfolio("RUB")
    ledger.add(pid, "2025-01-01", "DEPOSIT", amount=1000)
    ledger.add(pid, "2025-01-01", "BUY", "A", 10, 100)
    ledger.add(pid, "2025-01-03", "DEPOSIT", amount=1000)
    idx = pd.date_range("2025-01-01", periods=4)
    h = value_history(ledger.transactions(pid), pd.DataFrame({"A": [100, 110, 110, 121.0]}, index=idx))
    assert h.attrs["missing_prices"] == []
    assert not h[["value", "twr"]].isna().any().any()
    assert list(h["value"]) == pytest.approx([1000, 1100, 2100, 2210])
    assert list(h["twr"]) == pytest.approx([1.0, 1.1, 1.1, 1.1 * 2210 / 2100])
