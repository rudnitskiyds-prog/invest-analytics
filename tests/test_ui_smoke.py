"""Смоук-тест страниц Streamlit в демо-режиме (без сети)."""
import pytest
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

st_testing = pytest.importorskip("streamlit.testing.v1")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Демо-режим только на время теста (не утекает в другие модули) и запрет сети:
    любой HTTP-запрос через requests фиксируется и проваливает тест, даже если страница
    перехватила исключение и показала st.error."""
    monkeypatch.setenv("IP_OFFLINE", "1")
    calls = []

    def send(self, request, **kw):
        calls.append(request.url)
        raise requests.ConnectionError(f"сеть запрещена в тестах: {request.url}")
    monkeypatch.setattr(requests.Session, "send", send)
    yield
    assert calls == [], f"смоук-тест обращался к сети: {calls}"


def run_page(path, **state):
    at = st_testing.AppTest.from_file(str(ROOT / path), default_timeout=120)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def use_tmp_ledger(tmp_path, monkeypatch):
    """Временная БД портфеля для страниц. Ledger() берёт путь из значения по умолчанию
    __init__(path=PORTFOLIO_DB), вычисленного при импорте core.portfolio.ledger, поэтому подмена
    config.PORTFOLIO_DB / IP_DATA_DIR сама по себе не действует; значение по умолчанию подменяется
    через monkeypatch и восстанавливается автоматически."""
    from core import config
    from core.portfolio import ledger as lg
    db = tmp_path / "p.sqlite"
    monkeypatch.setattr(config, "PORTFOLIO_DB", db)
    monkeypatch.setattr(lg, "PORTFOLIO_DB", db)
    monkeypatch.setattr(lg.Ledger.__init__, "__defaults__", (db,))
    return lg.Ledger(db)


def test_method_page():
    at = run_page("pages/7_Методика.py")
    assert not at.exception


def test_backtest_page():
    at = run_page("pages/4_Бэктест.py", bt_req=dict(
        picked=["Лежебока плюс (Спирин)", "Портфель 60/40"], start="2011-01-31", end="2025-12-31",
        capital=1_000_000, rebal="A", band=None, comm=0.0, contrib=0, cfreq="M", tax=0.0,
        log_y=False, same=True))
    assert not at.exception, at.exception
    assert any("5 61" in m.value for m in at.metric)   # 5 618 575 ₽


def test_frontier_page():
    at = run_page("pages/3_Граница_Марковица.py", frontier_req=(
        ("MCFTR", "CORP_CHAIN", "RGBITR", "GOLD_CBR", "RUONIA"), "2011-01-31", "2025-12-31", "M", "arith",
        0.0, 1.0, 2000, False))
    assert not at.exception, at.exception


def test_frontier_page_builds_on_first_open():
    at = run_page("pages/3_Граница_Марковица.py")
    assert not at.exception, at.exception
    assert not any("Построить" in i.value for i in at.info)


def test_security_page_without_demo_data():
    at = run_page("pages/2_Карточка_бумаги.py")   # SBER по умолчанию, в демо-данных его нет
    assert not at.exception, at.exception
    # новая карточка: без живых данных — спокойное пояснение, а не трейсбек
    assert any("живых данных" in i.value for i in at.info)


def test_portfolio_pages_empty(tmp_path, monkeypatch):
    use_tmp_ledger(tmp_path, monkeypatch)
    for page in ["pages/5_Мой_портфель.py", "pages/6_Ребалансировки.py"]:
        at = run_page(page)
        assert not at.exception, (page, at.exception)


def test_portfolio_page_with_data(tmp_path, monkeypatch):
    import pandas as pd
    from core.data import quotes
    L = use_tmp_ledger(tmp_path, monkeypatch)
    pid = L.create_portfolio("Основной", target={"EQMX": 0.6, "GOLD": 0.4})
    L.add(pid, "2024-01-15", "DEPOSIT", amount=100_000)
    L.add(pid, "2024-01-15", "BUY", "EQMX", 300, 150)
    L.add(pid, "2024-01-15", "BUY", "GOLD", 25_000, 1.6)
    L.add(pid, "2025-03-01", "DEPOSIT", amount=20_000)
    idx = pd.bdate_range("2024-01-15", "2025-12-31")
    import numpy as np
    px = pd.DataFrame({"EQMX": 150 * np.exp(np.linspace(0, .2, len(idx))),
                       "GOLD": 1.6 * np.exp(np.linspace(0, .5, len(idx)))}, index=idx)
    monkeypatch.setattr(quotes, "get_quotes", lambda s: pd.DataFrame(
        {"price": [180.0, 2.6], "name": ["EQMX", "GOLD"], "lot": [1, 1]}, index=["EQMX", "GOLD"]))
    monkeypatch.setattr(quotes, "price_history_rub", lambda s, start, end=None: px)
    for page in ["pages/5_Мой_портфель.py", "pages/6_Ребалансировки.py"]:
        at = run_page(page)
        assert not at.exception, (page, at.exception)
        assert not at.error, (page, [e.value for e in at.error])
