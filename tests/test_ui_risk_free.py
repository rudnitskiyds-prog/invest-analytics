"""Интерфейс Streamlit без ручной безрисковой ставки: в боковой панели нет поля и кнопок КБД, есть подпись
«доходность коротких ОФЗ»; страницы подписывают среднюю ставку за период; ставка недоступна —
предупреждение RF_UNAVAILABLE, страница не падает. Демо-режим (IP_OFFLINE=1), сети нет, ряд ставки —
синтетический risk_free.json во временной папке."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent

st_testing = pytest.importorskip("streamlit.testing.v1")

RF_ROWS = [["2002-12-30", 0.12], ["2011-01-11", 0.06], ["2015-01-12", 0.14], ["2020-01-09", 0.055],
           ["2024-01-09", 0.15], ["2025-12-30", 0.17]]


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """Демо-режим, запрет сети, папка public/data -> tmp_path (без risk_free.json), чистые кэши Streamlit."""
    import streamlit as st
    from core import config
    from core.data import risk_free
    monkeypatch.setenv("IP_OFFLINE", "1")
    pub = tmp_path / "public_data"
    pub.mkdir()
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", pub)
    calls = []

    def send(self, request, **kw):
        calls.append(request.url)
        raise requests.ConnectionError(f"сеть запрещена в тестах: {request.url}")
    monkeypatch.setattr(requests.Session, "send", send)
    st.cache_data.clear()
    risk_free._live.cache_clear()
    yield pub
    st.cache_data.clear()
    assert calls == [], f"тест обращался к сети: {calls}"


@pytest.fixture
def rf_file(offline):
    (offline / "risk_free.json").write_text(json.dumps(
        {"id": "risk_free", "unit": "доля годовых", "splice_date": "2011-01-11", "data": RF_ROWS}),
        encoding="utf-8")
    return offline


def run_page(path, **state):
    at = st_testing.AppTest.from_file(str(ROOT / path), default_timeout=120)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


BT_REQ = dict(picked=["Лежебока плюс (Спирин)", "Портфель 60/40"], start="2011-01-31", end="2025-12-31",
              capital=1_000_000, rebal="A", band=None, comm=0.0, contrib=0, cfreq="M", tax=0.0,
              log_y=False, same=True)
FR_REQ = (("MCFTR", "CORP_CHAIN", "RGBITR", "GOLD_CBR"), "2011-01-31", "2025-12-31", "M", "arith",
          0.0, 1.0, 2000, False)


def captions(at) -> list[str]:
    return [c.value for c in at.caption]


def warnings(at) -> list[str]:
    return [w.value for w in at.warning]


# ------------------------------------------------------------------ боковая панель
@pytest.mark.parametrize("page", ["app.py", "pages/4_Бэктест.py"])
def test_sidebar_has_no_manual_rate(rf_file, page):
    at = run_page(page, bt_req=BT_REQ)
    assert not at.exception, at.exception
    labels = [w.label for w in at.sidebar.number_input]
    assert "Безрисковая ставка, % годовых" not in labels
    assert not any("Безрисковая ставка" in lb for lb in labels)
    buttons = [b.label for b in at.button]
    assert "КБД 15 лет" not in buttons and "Сброс" not in buttons
    side = [c.value for c in at.sidebar.caption]
    assert any("доходность коротких ОФЗ" in c for c in side), side
    # текущая ставка — последняя точка ряда: 17,00% на 30.12.2025
    assert any("17,00%" in c and "30.12.2025" in c for c in side), side


def test_sidebar_rate_unavailable_note(offline):
    at = run_page("pages/4_Бэктест.py", bt_req=BT_REQ)
    assert not at.exception, at.exception
    side = [c.value for c in at.sidebar.caption]
    assert any("доходность коротких ОФЗ" in c and "недоступна" in c for c in side), side


# ------------------------------------------------------------------ подписи «в среднем … за период»
def _mean_text(start, end):
    import pandas as pd
    from core.data import risk_free
    from ui.common import fmt_pct
    s = pd.Series({pd.Timestamp(d): v for d, v in RF_ROWS})
    return f"в среднем {fmt_pct(risk_free.time_mean(s, start, end), 2)} за период"


def test_backtest_caption_mean_rate(rf_file):
    at = run_page("pages/4_Бэктест.py", bt_req=BT_REQ)
    assert not at.exception, at.exception
    exp = _mean_text("2011-01-31", "2025-12-31")
    caps = captions(at)
    assert any("доходность коротких ОФЗ" in c and exp in c for c in caps), (exp, caps)
    from ui.common import RF_UNAVAILABLE
    assert RF_UNAVAILABLE not in warnings(at)
    assert not any("Rf =" in c for c in caps)


def test_frontier_caption_mean_rate(rf_file):
    at = run_page("pages/3_Граница_Марковица.py", frontier_req=FR_REQ)
    assert not at.exception, at.exception
    exp = _mean_text("2011-01-31", "2025-12-31")
    caps = captions(at)
    assert any(exp in c and "CML" in c for c in caps), (exp, caps)


def test_backtest_before_2011_mentions_whole_ofz_market(rf_file):
    req = dict(BT_REQ, start="2005-01-31", end="2012-12-31")
    at = run_page("pages/4_Бэктест.py", bt_req=req)
    assert not at.exception, at.exception
    assert any("до 2011 — доходность всего рынка ОФЗ" in c for c in captions(at)), captions(at)


def _portfolio(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    from core import config
    from core.data import quotes
    from core.portfolio import ledger as lg
    db = tmp_path / "p.sqlite"
    monkeypatch.setattr(config, "PORTFOLIO_DB", db)
    monkeypatch.setattr(lg, "PORTFOLIO_DB", db)
    monkeypatch.setattr(lg.Ledger.__init__, "__defaults__", (db,))
    L = lg.Ledger(db)
    pid = L.create_portfolio("Основной", target={"EQMX": 0.6, "GOLD": 0.4})
    L.add(pid, "2024-01-15", "DEPOSIT", amount=100_000)
    L.add(pid, "2024-01-15", "BUY", "EQMX", 300, 150)
    L.add(pid, "2024-01-15", "BUY", "GOLD", 25_000, 1.6)
    idx = pd.bdate_range("2024-01-15", "2025-12-31")
    px = pd.DataFrame({"EQMX": 150 * np.exp(np.linspace(0, .2, len(idx))),
                       "GOLD": 1.6 * np.exp(np.linspace(0, .5, len(idx)))}, index=idx)
    monkeypatch.setattr(quotes, "get_quotes", lambda s: pd.DataFrame(
        {"price": [180.0, 2.6], "name": ["EQMX", "GOLD"], "lot": [1, 1]}, index=["EQMX", "GOLD"]))
    monkeypatch.setattr(quotes, "price_history_rub", lambda s, start, end=None: px)


def test_portfolio_caption_mean_rate(rf_file, tmp_path, monkeypatch):
    _portfolio(tmp_path, monkeypatch)
    at = run_page("pages/5_Мой_портфель.py")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    caps = captions(at)
    assert any("доходность коротких ОФЗ, в среднем" in c and "за период" in c for c in caps), caps


# ------------------------------------------------------------------ ставка недоступна
@pytest.mark.parametrize("page,state", [
    ("pages/4_Бэктест.py", {"bt_req": BT_REQ}),
    ("pages/3_Граница_Марковица.py", {"frontier_req": FR_REQ}),
])
def test_rate_unavailable_warns_and_page_works(offline, page, state):
    from ui.common import RF_UNAVAILABLE
    at = run_page(page, **state)
    assert not at.exception, at.exception
    assert RF_UNAVAILABLE in warnings(at), warnings(at)
    assert RF_UNAVAILABLE in captions(at) or any(RF_UNAVAILABLE in c for c in captions(at))


def test_backtest_rate_unavailable_still_shows_result(offline):
    at = run_page("pages/4_Бэктест.py", bt_req=BT_REQ)
    assert not at.exception, at.exception
    assert any("5 61" in mt.value for mt in at.metric)   # итог стратегии от ставки не зависит


def test_portfolio_rate_unavailable(offline, tmp_path, monkeypatch):
    from ui.common import RF_UNAVAILABLE
    _portfolio(tmp_path, monkeypatch)
    at = run_page("pages/5_Мой_портфель.py")
    assert not at.exception, at.exception
    assert RF_UNAVAILABLE in warnings(at), warnings(at)


def test_method_page_describes_risk_free(rf_file):
    at = run_page("pages/7_Методика.py")
    assert not at.exception, at.exception
    text = " ".join(md.value for md in at.markdown)
    assert "RUGBITR1Y" in text and "RGBEY" in text
    assert "7,86" not in text
