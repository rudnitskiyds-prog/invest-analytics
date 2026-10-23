"""Смоук-тест страницы «Портфели и стратегии» (pages/8_Портфели.py) в демо-режиме, без сети.

public/data — копия во временном каталоге без portfolios.json: страница считает портфели вживую
по месячным демо-рядам (как при первом запуске до сборщика).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import requests

from core import config

ROOT = Path(__file__).resolve().parent.parent
PAGE = str(ROOT / "pages" / "8_Портфели.py")

st_testing = pytest.importorskip("streamlit.testing.v1")
pa = pytest.importorskip("pyarrow")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """Демо-режим, запрет сети, public/data — копия без portfolios.json (возвращается её путь)."""
    monkeypatch.setenv("IP_OFFLINE", "1")
    calls = []

    def send(self, request, **kw):
        calls.append(request.url)
        raise requests.ConnectionError(f"сеть запрещена в тестах: {request.url}")
    monkeypatch.setattr(requests.Session, "send", send)
    pub = tmp_path / "pub"
    pub.mkdir()
    for f in (ROOT / "public" / "data").glob("*.json"):
        if f.name != "portfolios.json":
            shutil.copy(f, pub / f.name)
    monkeypatch.setattr(config, "PUBLIC_DATA_DIR", pub)
    import streamlit as st
    from core.analytics import portfolios as pf
    st.cache_data.clear()
    pf.clear_cache()
    yield pub
    st.cache_data.clear()
    pf.clear_cache()
    assert calls == [], f"страница обращалась к сети: {calls}"


def run(p: str | None = None, **state):
    at = st_testing.AppTest.from_file(PAGE, default_timeout=180)
    if p is not None:
        at.query_params["p"] = p
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def rendered(df_el):
    """Отображаемые значения таблицы (после Styler.format), как их видит пользователь."""
    sty = df_el.proto.arrow_data.styler
    if sty.display_values:
        return pa.ipc.open_stream(sty.display_values).read_pandas()
    return df_el.value


def no_none_text(df) -> None:
    for c in df.columns:
        vals = [str(v) for v in df[c].tolist()]
        bad = [v for v in vals if v in ("None", "nan", "NaN", "<NA>") or "e+300" in v or "e+299" in v]
        assert not bad, f"колонка «{c}»: {bad}"


def test_listing():
    at = run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    table = rendered(at.dataframe[0])
    names = list(table["Портфель"])
    assert "Лежебока плюс (Спирин)" in names and "Электроэнергетика" in names
    assert len(names) == 11                      # 7 отраслевых индексов нет в демо-данных
    no_none_text(table)
    assert "?p=all-weather-dalio" in list(table["Ссылка"])
    # рейтинг за 5 лет: у дивидендного (история с 2021) — прочерк
    row = table[table["Портфель"] == "Дивидендный"].iloc[0]
    assert row["Рейтинг (5 лет)"] == "—"
    assert any("Нет данных" in e.label for e in at.expander)
    assert at.get("plotly_chart"), "нет диаграммы риск — доходность"


@pytest.mark.parametrize("group,expect,absent", [
    ("Ленивые портфели", "Портфель 60/40", "Электроэнергетика"),
    ("Тематические", "Электроэнергетика", "Портфель 60/40"),
])
def test_listing_group_filter(group, expect, absent):
    at = run()
    at.get("button_group")[0].set_value(group).run()
    assert not at.exception, at.exception
    assert not at.error
    names = list(rendered(at.dataframe[0])["Портфель"])
    assert expect in names and absent not in names
    no_none_text(rendered(at.dataframe[0]))


def test_listing_columns_toggle_no_none():
    """Все колонки таблицы (включая скрытые по умолчанию) — без «None»."""
    at = run()
    ms = at.multiselect[0]
    ms.set_value(list(ms.options)).run()
    assert not at.exception, at.exception
    table = rendered(at.dataframe[0])
    assert "Бенчмарк" in table.columns and "Комиссия известна для доли" in table.columns
    no_none_text(table)


@pytest.mark.parametrize("slug,title", [("lazy-plus", "Лежебока плюс (Спирин)"),
                                        ("all-weather-dalio", "Всепогодный портфель (Далио)"),
                                        ("ofz-only", "Только ОФЗ"),
                                        ("dividend", "Дивидендный"),
                                        ("sector-power", "Электроэнергетика")])
def test_portfolio_page(slug, title):
    at = run(slug)
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert title in text or any(title in t.value for t in at.title), title
    assert at.dataframe, "нет таблиц на странице портфеля"
    for d in at.dataframe:
        no_none_text(rendered(d))
    if slug == "all-weather-dalio":
        assert "MEEUTR" in text


def test_portfolio_page_sector_without_demo_data():
    at = run("sector-oil-gas")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert any("нет данных" in i.value for i in at.info)


def test_unknown_slug_warns_and_shows_listing():
    at = run("нет-такого")
    assert not at.exception, at.exception
    assert any("не найден" in w.value for w in at.warning)
    assert "p" not in at.query_params
    assert at.dataframe and "Портфель" in rendered(at.dataframe[0]).columns


def test_open_in_backtest_fills_session_state():
    at = run("lazy-plus")
    btn = [b for b in at.button if b.label == "Открыть в бэктесте"]
    assert btn
    btn[0].click().run()
    assert not at.exception, at.exception
    pre = at.session_state["bt_prefill"]
    # имя совпадает со стратегией бэктеста — к нему добавлена пометка раздела
    assert pre["name"] == "Лежебока плюс (Спирин) (из раздела «Портфели»)"
    assert pre["weights"] == pytest.approx({"MCFTR": 0.30, "CORP_CHAIN": 0.30, "GOLD_CBR": 0.30, "RUONIA": 0.10})
    assert at.session_state["custom_portfolios"][pre["name"]] == pre["weights"]


def test_open_in_backtest_sector_keeps_name():
    at = run("sector-power")
    [b for b in at.button if b.label == "Открыть в бэктесте"][0].click().run()
    assert not at.exception, at.exception
    pre = at.session_state["bt_prefill"]
    assert pre == {"name": "Электроэнергетика", "weights": {"MEEUTR": 1.0}}
    assert at.session_state["custom_portfolios"]["Электроэнергетика"] == {"MEEUTR": 1.0}


def test_backtest_dalio_with_meeutr_demo():
    """Бэктест стратегии Далио (MEEUTR вместо MOEXEU) на демо-данных: история с 31.12.2013."""
    at = st_testing.AppTest.from_file(str(ROOT / "pages" / "4_Бэктест.py"), default_timeout=180)
    at.session_state["bt_req"] = dict(
        picked=["Всепогодный портфель (Далио)"], start="2011-01-31", end="2025-12-31",
        capital=1_000_000, rebal="A", band=None, comm=0.0, contrib=0, cfreq="M", tax=0.0,
        log_y=False, same=True)
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert at.metric, "нет итоговых показателей бэктеста"


def test_backtest_receives_portfolio_prefill():
    at = run("sector-power")
    [b for b in at.button if b.label == "Открыть в бэктесте"][0].click().run()
    state = {k: at.session_state[k] for k in ("bt_prefill", "custom_portfolios")}
    bt = st_testing.AppTest.from_file(str(ROOT / "pages" / "4_Бэктест.py"), default_timeout=180)
    for k, v in state.items():
        bt.session_state[k] = v
    bt.run()
    assert not bt.exception, bt.exception
    ms = bt.multiselect[0]
    assert "Электроэнергетика" in ms.options and "Электроэнергетика" in ms.value


# ====================================================================== после замечаний критика
def all_text(at) -> str:
    return " ".join([*(m.value for m in at.markdown), *(c.value for c in at.caption),
                     *(i.value for i in at.info), *(w.value for w in at.warning)])


def metrics_table(at):
    for d in at.dataframe:
        v = d.value
        if "Показатель" in v.columns:
            return v
    raise AssertionError("нет таблицы коэффициентов")


def test_benchmark_from_core_not_load_series(monkeypatch):
    """Бенчмарк графика — pf.benchmark_values; страница сама ряд бенчмарка не грузит."""
    from core.analytics import portfolios as pf
    import ui.common as uc
    calls, ui_calls = [], []
    real = pf.benchmark_values

    def bv(pid, *a, **k):
        calls.append(pid)
        return real(pid, *a, **k)
    monkeypatch.setattr(pf, "benchmark_values", bv)

    def no_load(*a, **k):
        ui_calls.append(a)
        raise AssertionError("страница грузит ряд сама")
    monkeypatch.setattr(uc, "load_series", no_load)
    at = run("lazy-plus")
    assert not at.exception, at.exception
    assert calls and set(calls) == {"lazy-plus"}
    assert ui_calls == []
    text = all_text(at)
    assert "бенчмарк — **" in text or "бенчмарк —" in text
    assert "есть не за весь период" not in text


def test_benchmark_not_aligned(monkeypatch):
    """Бенчмарк короче портфеля: нет итоговой суммы бенчмарка, есть подпись с периодом сравнения."""
    from core.analytics import portfolios as pf

    def bv(pid, start=None, end=None, source="auto"):
        v = pf.portfolio_values(pid, start=start, end=end, source=source)
        b = v.loc["2016-01-01":"2020-12-31"].copy()
        b = b / b.iloc[0] * 100_000
        b.attrs.update({"key": "MCFTR", "aligned": False, "from": str(b.index[0].date()),
                        "to": str(b.index[-1].date()), "freq": "M", "source": "live", "updated": None})
        return b
    monkeypatch.setattr(pf, "benchmark_values", bv)
    at = run("lazy-plus")
    assert not at.exception, at.exception
    assert not at.error
    head = [m.value for m in at.markdown if "Стоимость 100 000" in m.value]
    assert head and "бенчмарк" not in head[0]
    cap = [c.value for c in at.caption if "есть не за весь период" in c.value]
    assert cap and "31.01.2016" in cap[0] and "31.12.2020" in cap[0]


def test_demo_note_only_for_live(offline):
    at = run()
    assert any("Демо-режим: месячные ряды" in c.value for c in at.caption)
    # есть файл сборщика — источник file, демо-подписи нет
    from core.analytics import portfolios as pf
    body, _ = pf.build_file(fallback=False)
    (offline / "portfolios.json").write_text(
        __import__("json").dumps({"updated": "2026-01-01T00:00:00+00:00", **body}, ensure_ascii=False),
        encoding="utf-8")
    import streamlit as st
    st.cache_data.clear()
    at = run()
    assert not at.exception, at.exception
    assert not any("Демо-режим: месячные ряды" in c.value for c in at.caption)
    no_none_text(rendered(at.dataframe[0]))


def test_ofz_only_no_alpha_beta_rows():
    at = run("ofz-only")
    assert not at.exception, at.exception
    rows = list(metrics_table(at)["Показатель"])
    assert "Коэф. Шарпа" in rows
    for lab in ("Альфа Дженсена (год.)", "Бета", "R² с бенчмарком", "Корреляция с бенчмарком",
                "Захват роста", "Захват падения"):
        assert lab not in rows, lab
    # у портфеля, отличного от бенчмарка, бета есть
    at2 = run("60-40")
    assert "Бета" in list(metrics_table(at2)["Показатель"])


def test_fee_partial_marked_with_star():
    at = run()
    table = rendered(at.dataframe[0])
    fee = dict(zip(table["Портфель"], table["Комиссия фондов"]))
    assert fee["Всепогодный портфель (Далио)"].endswith("*")     # у MEEUTR нет фонда: покрытие 92,5 %
    assert not fee["Портфель 60/40"].endswith("*")
    assert fee["Электроэнергетика"] == "—"
    assert any("Комиссия посчитана не по всему портфелю" in c.value for c in at.caption)


def test_no_sentinel_values_any_column_any_group():
    at = run()
    ms = at.multiselect[0]
    ms.set_value(list(ms.options)).run()
    for grp in ("Все", "Ленивые портфели", "Тематические"):
        at.get("button_group")[0].set_value(grp).run()
        assert not at.exception, at.exception
        no_none_text(rendered(at.dataframe[0]))
