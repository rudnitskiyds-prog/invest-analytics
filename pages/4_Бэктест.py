import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.analytics import metrics as m
from core.analytics.backtest import REBAL_LABELS, BacktestConfig, run_backtest
from core.analytics.strategies import STRATEGIES
from ui.common import (annual_returns, asset_label, base_layout, catalog_options, color_map,
                       drawdown_chart, fmt_rub, heatmap_returns, line_chart, load_prices,
                       load_series, page_setup, to_excel)

page_setup("Бэктест портфелей и стратегий", "⏱")
rf, bench, freq = st.session_state["rf"], st.session_state["benchmark"], st.session_state["freq"]
custom = st.session_state.setdefault("custom_portfolios", {})

# ------------------------------------------------------------------ собственные портфели
with st.expander("Собственные портфели (добавить свой состав)", expanded=False):
    name = st.text_input("Название портфеля", "Мой портфель")
    st.caption("Ключи — из каталога (MCFTR, RGBITR, GOLD_CBR, RUONIA, CORP_CHAIN…) или любые тикеры ISS "
               "(SBER, EQMX, GLDRUB_TOM). Доли в %, нормируются к 100.")
    init = pd.DataFrame({"Актив": ["MCFTR", "RGBITR", "GOLD_CBR"], "Доля, %": [50.0, 30.0, 20.0]})
    ed = st.data_editor(init, num_rows="dynamic", key="pf_editor", width="stretch")
    if st.button("Сохранить портфель"):
        ed = ed.dropna()
        w = {str(a).strip().upper(): float(v) / 100
             for a, v in zip(ed["Актив"], ed["Доля, %"]) if v and str(a).strip()}
        custom[name] = w
        st.success(f"Сохранён «{name}»")
    if custom:
        st.write({k: {a: f"{v:.1%}" for a, v in w.items()} for k, w in custom.items()})
        if st.button("Очистить собственные портфели"):
            custom.clear()
            st.rerun()

# ------------------------------------------------------------------ параметры
with st.form("bt"):
    c1, c2 = st.columns([3, 2])
    options = list(STRATEGIES) + list(custom)
    picked = c1.multiselect("Стратегии и портфели", options,
                            [s for s in STRATEGIES if "Баффетт" not in s] + list(custom))
    c2.markdown("<br>", unsafe_allow_html=True)
    same_rebal = c2.toggle("Единые параметры ребалансировки для всех", True)
    c3, c4, c5, c6 = st.columns(4)
    start = c3.date_input("Начало", pd.Timestamp("2011-01-31"))
    end = c4.date_input("Конец", pd.Timestamp("2025-12-31"))
    capital = c5.number_input("Начальный капитал, руб.", 10_000, 1_000_000_000, 1_000_000, 100_000)
    rebal = c6.selectbox("Ребалансировка", list(REBAL_LABELS), 4, format_func=REBAL_LABELS.get)
    c7, c8, c9, c10, c11 = st.columns(5)
    band = c7.number_input("Коридор, п.п. (0 — нет)", 0.0, 50.0, 0.0, 1.0) / 100
    comm = c8.number_input("Комиссия, % от сделки", 0.0, 2.0, 0.0, 0.01) / 100
    contrib = c9.number_input("Пополнение, руб.", 0, 10_000_000, 0, 10_000)
    cfreq = c10.selectbox("Периодичность пополнения", ["M", "Q", "A"],
                          format_func={"M": "Ежемесячно", "Q": "Ежеквартально", "A": "Ежегодно"}.get)
    tax = c11.number_input("НДФЛ при ребалансировке, %", 0.0, 30.0, 0.0, 1.0) / 100
    log_y = st.toggle("Логарифмическая шкала", False)
    run = st.form_submit_button("Запустить бэктест", type="primary")

if run:
    st.session_state["bt_req"] = dict(picked=picked, start=str(start), end=str(end), capital=capital,
                                      rebal=rebal, band=band or None, comm=comm, contrib=contrib,
                                      cfreq=cfreq, tax=tax, log_y=log_y, same=same_rebal)
req = st.session_state.get("bt_req")
if not req or not req["picked"]:
    st.info("Методика по умолчанию — как в НИР: 1 000 000 руб., 2011–2025, ежегодная ребалансировка "
            "в последний торговый день декабря, без комиссий и налогов, бенчмарк MCFTR.")
    st.stop()

ports = {p: (STRATEGIES[p]["weights"] if p in STRATEGIES else custom[p]) for p in req["picked"]
         if p in STRATEGIES or p in custom}
keys = sorted({a for w in ports.values() for a in w} | {bench})
with st.spinner(f"Загружаю {len(keys)} рядов…"):
    prices, info = load_prices(tuple(keys), "2000-01-01", req["end"], align="none")

results, errors = {}, []
for name, w in ports.items():
    cols = list(w)
    miss = [c for c in cols if c not in prices.columns]
    if miss:
        errors.append(f"{name}: нет данных по {', '.join(miss)}")
        continue
    sub = prices[cols].loc[req["start"]:].dropna()
    if sub.empty:
        errors.append(f"{name}: нет общего периода данных")
        continue
    if sub.index[0] > pd.Timestamp(req["start"]) + pd.Timedelta(days=45):
        errors.append(f"{name}: данные доступны только с {sub.index[0].date()} — бэктест начат с этой даты")
    rb = req["rebal"] if req["same"] or name not in STRATEGIES else STRATEGIES[name]["rebalance"]
    cfg = BacktestConfig(weights=w, initial=req["capital"], rebalance=rb, band=req["band"],
                         commission=req["comm"], contribution=req["contrib"], contribution_freq=req["cfreq"],
                         tax_rate=req["tax"], start=req["start"], end=req["end"])
    results[name] = run_backtest(sub, cfg)
for e in errors:
    st.warning(e)
if not results:
    st.stop()

eq = pd.DataFrame({k: r.equity for k, r in results.items()})
b = prices[bench].loc[eq.index[0]:eq.index[-1]].dropna()
eq[bench] = b / b.iloc[0] * req["capital"]
eq = eq.ffill()

cols = st.columns(min(len(results), 6))
for c, (k, r) in zip(cols * 3, results.items()):
    c.metric(k, fmt_rub(r.final_value), f"{m.cagr(r.equity) * 100:.1f}% CAGR".replace(".", ","))

t1, t2, t3, t4, t5 = st.tabs(["Стоимость", "Просадки", "Показатели (табл. 4 НИР)", "По годам", "Структура и сделки"])
with t1:
    st.plotly_chart(line_chart(eq, bench, "Стоимость, руб.", log_y=req["log_y"], height=520), width="stretch")
    if req["contrib"]:
        inv = results[next(iter(results))].invested
        st.caption(f"Внесено всего: {fmt_rub(inv.iloc[-1])}. При пополнениях CAGR по стоимости "
                   f"завышен — ориентируйтесь на XIRR на странице «Мой портфель».")
with t2:
    st.plotly_chart(drawdown_chart(eq, bench, 400), width="stretch")
with t3:
    raw = m.metrics_table({k: eq[k] for k in eq.columns}, eq[bench], rf, freq)
    st.dataframe(m.format_metrics_table(raw), width="stretch", height=900)
    st.caption(f"Rf = {rf:.2%}, доходности — {freq}; бенчмарк {bench}. Оборот и издержки: " +
               "; ".join(f"{k}: оборот {r.turnover:.1f}×, комиссии {fmt_rub(r.costs)}, налог {fmt_rub(r.taxes)}"
                         for k, r in results.items()))
with t4:
    ar = annual_returns(eq)
    st.plotly_chart(heatmap_returns(ar, 60 + 40 * len(eq.columns)), width="stretch")
with t5:
    sel = st.selectbox("Портфель", list(results))
    r = results[sel]
    wdf = r.weights.resample("ME").last()
    colors = color_map(list(wdf.columns))
    fig = go.Figure()
    for c in wdf.columns:
        fig.add_trace(go.Scatter(x=wdf.index, y=wdf[c], stackgroup="one", name=asset_label(c),
                                 line=dict(width=0.5, color=colors[c]),
                                 hovertemplate=f"%{{y:.1%}}<extra>{c}</extra>"))
    st.plotly_chart(base_layout(fig, 360, "Доля", pct_y=True), width="stretch")
    st.dataframe(r.trades, width="stretch", height=300, hide_index=True)

st.download_button("Скачать результаты (Excel)",
                   to_excel({"Стоимость": eq, "Показатели": m.metrics_table({k: eq[k] for k in eq}, eq[bench], rf, freq),
                             "По годам": annual_returns(eq),
                             **{f"Сделки {i+1}": r.trades for i, r in enumerate(results.values())}}),
                   "backtest.xlsx")
