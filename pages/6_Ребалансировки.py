import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.analytics.backtest import REBAL_LABELS
from core.data.quotes import get_quotes
from core.portfolio.ledger import Ledger, valuation
from core.portfolio.rebalance import drift_report, rebalance_orders, schedule, to_ics
from ui.common import PALETTE, base_layout, fmt_rub, page_setup

page_setup("Календарь ребалансировок", "🗓")
L = Ledger()
pfs = L.portfolios()
if pfs.empty:
    st.info("Сначала создайте портфель на странице «Мой портфель».")
    st.stop()

cur = st.selectbox("Портфель", list(pfs["name"]))
pf = pfs[pfs["name"] == cur].iloc[0]
pid = int(pf["id"])
tx = L.transactions(pid)
held = sorted(tx["secid"].dropna().unique()) if not tx.empty else []

st.subheader("Целевая структура и правило")
c1, c2, c3 = st.columns([3, 1, 1])
tgt = pf["target"] or {s: round(1 / len(held), 4) for s in held}
ed = c1.data_editor(pd.DataFrame({"Тикер": list(tgt), "Цель, %": [v * 100 for v in tgt.values()]}),
                    num_rows="dynamic", width="stretch", key="tgt")
rule = c2.selectbox("Периодичность", list(REBAL_LABELS), list(REBAL_LABELS).index(pf["rebalance"])
                    if pf["rebalance"] in REBAL_LABELS else 4, format_func=REBAL_LABELS.get)
band = c3.number_input("Коридор, п.п.", 0.0, 50.0, float(pf["band"] or 0) * 100, 0.5) / 100
if st.button("Сохранить правило", type="primary"):
    ed = ed.dropna()
    t = {str(a).upper(): float(v) / 100 for a, v in zip(ed["Тикер"], ed["Цель, %"]) if str(a).strip()}
    s = sum(t.values())
    L.update_portfolio(pid, target={k: v / s for k, v in t.items()} if s else {}, rebalance=rule, band=band)
    st.rerun()

target = pd.Series(pf["target"], dtype=float)
if target.empty:
    st.info("Задайте целевые доли и сохраните правило.")
    st.stop()

# ------------------------------------------------------------------ календарь
dates = schedule(pf["rebalance"], n=8)
st.subheader("Ближайшие даты")
c1, c2 = st.columns([2, 1])
c1.dataframe(pd.DataFrame({"Дата": [d.date() for d in dates],
                           "Через, дней": [(d - pd.Timestamp.today().normalize()).days for d in dates]}),
             hide_index=True, width="stretch")
c2.download_button("Добавить в календарь (.ics)",
                   to_ics(dates, cur, "Проверить отклонение долей и выполнить заявки из платформы"),
                   f"rebalance_{cur}.ics", "text/calendar")
c2.caption("Файл открывается в Apple Календаре, Google Calendar, Outlook; напоминание за день.")

# ------------------------------------------------------------------ отклонение и заявки
if not held:
    st.stop()
quotes = get_quotes(set(held) | set(target.index))
val, cash = valuation(tx, quotes)
values = val.set_index("Тикер")["Стоимость"].fillna(0) if not val.empty else pd.Series(dtype=float)
total = values.sum() + cash
current = values / total if total else values

st.subheader("Отклонение от целевых долей")
dr = drift_report(current, target, pf["band"] or 0.05)
fig = go.Figure()
fig.add_trace(go.Bar(x=dr.index, y=dr["Цель"], name="Цель", marker=dict(color=PALETTE[0], cornerradius=4)))
fig.add_trace(go.Bar(x=dr.index, y=dr["Факт"], name="Факт", marker=dict(color=PALETTE[1], cornerradius=4)))
fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
st.plotly_chart(base_layout(fig, 320, "Доля", pct_y=True), width="stretch")
out = dr["Вне коридора"].any()
(st.warning if out else st.success)(
    "Есть активы вне коридора — рекомендуется ребалансировка" if out else "Все доли в пределах коридора")
st.dataframe(dr, width="stretch", column_config={
    "Факт": st.column_config.NumberColumn(format="percent"),
    "Цель": st.column_config.NumberColumn(format="percent"),
    "Отн. отклонение": st.column_config.NumberColumn(format="percent"),
    "Отклонение, п.п.": st.column_config.NumberColumn(format="%.2f")})

st.subheader("Заявки для ребалансировки")
c1, c2 = st.columns(2)
use_cash = c1.toggle("Распределить свободные деньги", True)
comm = c2.number_input("Комиссия брокера, %", 0.0, 1.0, 0.05, 0.01) / 100
orders = rebalance_orders(values, target, quotes["price"], quotes["lot"], cash if use_cash else 0.0, comm)
st.dataframe(orders, width="stretch", column_config={
    "Доля после": st.column_config.NumberColumn(format="percent"),
    **{c: st.column_config.NumberColumn(format="localized") for c in
       ["Сумма", "Текущая стоимость", "Целевая стоимость", "Комиссия"]}})
buy = orders.loc[orders["Действие"] == "Купить", "Сумма"].sum()
sell = orders.loc[orders["Действие"] == "Продать", "Сумма"].sum()
st.caption(f"Покупки {fmt_rub(buy)}, продажи {fmt_rub(sell)}, комиссии {fmt_rub(orders['Комиссия'].sum())}. "
           f"Количество округлено вниз до целых лотов.")
if st.button("Отметить ребалансировку выполненной"):
    L.update_portfolio(pid, last_rebalance=str(pd.Timestamp.today().date()))
    st.success("Дата сохранена")
