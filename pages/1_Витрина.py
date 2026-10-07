import pandas as pd
import streamlit as st

from core.analytics import screener
from core.config import FUNDAMENTALS_CSV
from core.data import iss, public_data
from ui.common import page_setup, to_excel

page_setup("Витрина данных по бумагам", "🗂")
rf = st.session_state["rf"]

PCT = ["1М", "3М", "YTD", "1Г", "Волатильность", "Макс. просадка 1Г", "Див. доходность", "ROE"]


@st.cache_data(ttl=3600, show_spinner="Загружаю котировки и считаю метрики…")
def _shares(top, rf, bench):
    return screener.shares_showcase(bench, rf, top)


@st.cache_data(ttl=3600, show_spinner="Загружаю фонды…")
def _etfs(rf, bench):
    return screener.etf_showcase(bench, rf)


@st.cache_data(ttl=900, show_spinner="Загружаю облигации…")
def _bonds(board):
    return screener.bonds_showcase(board)


def pct_config(df):
    cfg = {}
    for c in df.columns:
        if c in PCT:
            cfg[c] = st.column_config.NumberColumn(c, format="percent")
    for c in ["Цена", "Цена, %", "НКД"]:
        if c in df.columns:
            cfg[c] = st.column_config.NumberColumn(c, format="%.2f")
    for c in ["Шарп", "Сортино", "Бета", "P/E", "P/B", "P/S", "EV/EBITDA", "ND/EBITDA", "Мод. дюрация",
              "Дюрация, лет", "Лет до погаш."]:
        if c in df.columns:
            cfg[c] = st.column_config.NumberColumn(c, format="%.2f")
    for c in ["Оборот, руб.", "Кап., млрд"]:
        if c in df.columns:
            cfg[c] = st.column_config.NumberColumn(c, format="localized")
    if "Комиссия, %" in df.columns:
        # значение уже в процентах годовых (0.95 = 0,95 %), без пересчёта
        cfg["Комиссия, %"] = st.column_config.NumberColumn(
            "Комиссия, %", format="%.2f %%",
            help="Комиссия фонда (TER), % годовых. Источник: RusETFs, при отсутствии — T-Invest API")
    if "СЧА, млрд руб." in df.columns:
        cfg["СЧА, млрд руб."] = st.column_config.NumberColumn(
            "СЧА, млрд руб.", format="%.2f", help="Стоимость чистых активов фонда, RusETFs")
    return cfg


SOURCES = ("Источники: ISS Московской биржи; дивиденды, мультипликаторы и сектора — T-Invest API; "
           "комиссии, УК и СЧА фондов — RusETFs (rusetfs.com).")


tab_sh, tab_etf, tab_bd, tab_mt, tab_ix = st.tabs(["Акции", "Фонды (паи)", "Облигации", "Металлы", "Индексы"])

with tab_sh:
    c1, c2, c3 = st.columns([1, 1, 2])
    top = c1.slider("Самые ликвидные акции, шт.", 20, 250, 80, 10)
    bench = c2.selectbox("Бета к", ["IMOEX", "MCFTR"], key="sh_b")
    c3.caption("Доходности и риск — за последние 12 мес. по дневным данным; Шарп/Сортино годовые "
               "при текущей безрисковой ставке из боковой панели. Дивидендная доходность — "
               "выплаты с датой реестра за 12 мес. / цена.")
    try:
        df = _shares(top, rf, bench)
        q = st.text_input("Поиск по тикеру/названию", key="sh_q")
        if q:
            df = df[df["SECID"].str.contains(q.upper(), regex=False)
                    | df["Название"].str.contains(q, case=False, regex=False)]
        if "Сектор" in df.columns:
            sectors = sorted(df["Сектор"].dropna().unique())
            picked = st.multiselect("Сектор", sectors, placeholder="Все секторы", key="sh_sector")
            if picked:
                df = df[df["Сектор"].isin(picked)]
        st.dataframe(df, width="stretch", height=620, hide_index=True, column_config=pct_config(df))
        st.download_button("Скачать Excel", to_excel({"Акции": df}), "shares.xlsx")
        if public_data.fundamentals().empty and not FUNDAMENTALS_CSV.exists():
            st.info("Мультипликаторы (P/E, P/B, EV/EBITDA, ROE) пока недоступны. Основной источник — "
                    "ежедневный сбор из T-Invest API в `public/data/fundamentals.json` "
                    "(`python -m scripts.collect_data`); запасной — ручной `data/fundamentals.csv`, "
                    "шаблон в `data/fundamentals_template.csv`. "
                    "ISS Мосбиржи финансовую отчётность не публикует.")
    except Exception as e:  # noqa: BLE001
        st.error(f"Ошибка загрузки: {e}")

with tab_etf:
    try:
        df = _etfs(rf, "IMOEX")
        has_fee = "Комиссия, %" in df.columns
        # колонки RusETFs появляются, только если данные есть
        cat_filters = [(c, k) for c, k in [("УК", "etf_uk"), ("Класс активов", "etf_class")] if c in df.columns]
        if has_fee or cat_filters:
            widths = ([1, 1] if has_fee else []) + [1.5] * len(cat_filters)
            cols = st.columns(widths + [max(0.5, 5 - sum(widths))])
            picks = {}
            for (c, k), col in zip(cat_filters, cols[2 if has_fee else 0:]):
                opts = sorted(df[c].dropna().astype(str).unique())
                picks[c] = col.multiselect(c, opts, placeholder="Все", key=k)
            for c, chosen in picks.items():
                if chosen:
                    df = df[df[c].astype(str).isin(chosen)]
            if has_fee:
                fee_max = cols[0].number_input("Комиссия до, %", min_value=0.0, max_value=10.0, value=None,
                                               step=0.05, format="%.2f", placeholder="без ограничения",
                                               key="etf_fee_max")
                if cols[1].toggle("Сначала дешёвые", key="etf_fee_sort"):
                    df = df.sort_values("Комиссия, %", na_position="last")
                if fee_max is not None:
                    df = df[df["Комиссия, %"] <= fee_max]
                    st.caption("При фильтре по комиссии фонды без данных о комиссии скрыты.")
        st.dataframe(df, width="stretch", height=620, hide_index=True, column_config=pct_config(df))
        if {"УК", "СЧА, млрд руб."} & set(df.columns):
            st.caption("Данные о комиссиях, УК и СЧА — [RusETFs](https://rusetfs.com)")
        st.download_button("Скачать Excel", to_excel({"Фонды": df}), "etf.xlsx")
    except Exception as e:  # noqa: BLE001
        st.error(f"Ошибка загрузки: {e}")

with tab_bd:
    board = st.radio("Режим", ["TQOB", "TQCB"], horizontal=True,
                     format_func={"TQOB": "ОФЗ", "TQCB": "Корпоративные"}.get)
    try:
        df = _bonds(board)
        c1, c2, c3 = st.columns(3)
        ymin, ymax = c1.slider("Лет до погашения", 0.0, 30.0, (0.0, 30.0), 0.5)
        min_turn = c2.number_input("Мин. оборот за день, млн руб.", 0.0, 10000.0, 0.0, 1.0)
        lvl = c3.multiselect("Уровень листинга", [1, 2, 3], [1, 2, 3])
        f = df[(df["Лет до погаш."].between(ymin, ymax)) &
               (df["Оборот, руб."].fillna(0) >= min_turn * 1e6) & (df["Листинг"].isin(lvl))]
        st.dataframe(f, width="stretch", height=560, hide_index=True, column_config=pct_config(f))
        st.download_button("Скачать Excel", to_excel({"Облигации": f}), f"bonds_{board}.xlsx")
    except Exception as e:  # noqa: BLE001
        st.error(f"Ошибка загрузки: {e}")

with tab_mt:
    try:
        st.dataframe(iss.metals()[["SECID", "NAME", "PRICE", "LASTTOPREVPRICE", "VALTODAY_RUR", "UPDATETIME"]],
                     width="stretch", hide_index=True)
        st.caption("Биржевые металлы валютного рынка Мосбиржи (режим CETS), руб. за грамм.")
    except Exception as e:  # noqa: BLE001
        st.error(f"Ошибка загрузки: {e}")

with tab_ix:
    try:
        st.dataframe(iss.indices(), width="stretch", height=600, hide_index=True)
    except Exception as e:  # noqa: BLE001
        st.error(f"Ошибка загрузки: {e}")

st.caption(SOURCES)
