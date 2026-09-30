import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.analytics import metrics as m
from core.analytics.backtest import BacktestConfig, run_backtest, xirr
from core.analytics.strategies import STRATEGIES
from core.data.quotes import get_quotes, price_history_rub
from core.portfolio.ledger import TX_TYPES, Ledger, external_flows, value_history, valuation
from ui.common import (PALETTE, color_map, fmt_pct, fmt_rub, line_chart, load_prices, load_series,
                       page_setup, show_metrics_table, to_excel)

page_setup("Мой портфель — учёт и эффективность", "💼")
rf, bench, freq = st.session_state["rf"], st.session_state["benchmark"], st.session_state["freq"]
L = Ledger()

pfs = L.portfolios()
with st.sidebar:
    st.markdown("### Портфель")
    names = list(pfs["name"])
    cur = st.selectbox("Выбрать", names) if names else None
    with st.expander("Создать новый"):
        nm = st.text_input("Название", "Основной")
        if st.button("Создать") and nm:
            L.create_portfolio(nm, benchmark=bench)
            st.rerun()
if cur is None:
    st.info("Создайте портфель в боковой панели.")
    st.stop()
pf = pfs[pfs["name"] == cur].iloc[0]
pid = int(pf["id"])
tx = L.transactions(pid)

# ------------------------------------------------------------------ операции
with st.expander("Добавить операцию", expanded=tx.empty):
    with st.form("tx", clear_on_submit=True):
        c = st.columns([1.2, 1.2, 1, 1, 1, 1, 1.5])
        d = c[0].date_input("Дата", pd.Timestamp.today())
        t = c[1].selectbox("Тип", list(TX_TYPES), format_func=TX_TYPES.get)
        s = c[2].text_input("Тикер", "")
        q = c[3].number_input("Кол-во, шт.", 0.0, 1e9, 0.0, 1.0)
        p = c[4].number_input("Цена, руб.", 0.0, 1e9, 0.0, 0.01)
        a = c[5].number_input("Сумма, руб.", 0.0, 1e12, 0.0, 100.0, help="Для покупок/продаж можно оставить 0 — "
                                                                           "будет кол-во × цена")
        fee = c[6].number_input("Комиссия, руб.", 0.0, 1e9, 0.0, 1.0)
        if st.form_submit_button("Добавить"):
            L.add(pid, str(d), t, s or None, q, p, a if a else None, fee)
            st.rerun()
    up = st.file_uploader("Импорт CSV: date,type,secid,qty,price,amount,fee,note", type="csv")
    if up is not None and st.button("Импортировать"):
        n = L.import_csv(pid, pd.read_csv(up))
        st.success(f"Импортировано {n} операций")
        st.rerun()

if tx.empty:
    st.info("Добавьте операции: сначала «Пополнение», затем «Покупки».")
    st.stop()

held = sorted(tx["secid"].dropna().unique())
with st.spinner("Получаю котировки…"):
    quotes = get_quotes(held)
val, cash = valuation(tx, quotes)
total = (val["Стоимость"].fillna(0).sum() if not val.empty else 0) + cash
flows = external_flows(tx)
net_in = flows.sum() if len(flows) else 0.0
cf = [(d, -v) for d, v in flows.items()] + [(pd.Timestamp.today().normalize(), total)]
irr = xirr(cf) if len(cf) > 1 else float("nan")

k = st.columns(5)
k[0].metric("Стоимость портфеля", fmt_rub(total))
k[1].metric("Внесено (нетто)", fmt_rub(net_in))
k[2].metric("Результат", fmt_rub(total - net_in), fmt_pct((total - net_in) / net_in) if net_in else None)
k[3].metric("XIRR (годовая)", fmt_pct(irr))
k[4].metric("Свободные деньги", fmt_rub(cash))

t1, t2, t3, t4 = st.tabs(["Позиции", "Динамика и метрики", "Сравнение со стратегиями", "Операции"])
with t1:
    if not val.empty:
        c1, c2 = st.columns([3, 2])
        c1.dataframe(val, width="stretch", hide_index=True, column_config={
            "Доля": st.column_config.NumberColumn(format="percent"),
            "Доходность": st.column_config.NumberColumn(format="percent"),
            **{x: st.column_config.NumberColumn(format="localized") for x in
               ["Стоимость", "Вложено", "Нереал. P&L", "Реал. P&L", "Доход (див/куп)"]}})
        alloc = val[val["Стоимость"] > 0].set_index("Тикер")["Стоимость"]
        if cash > 0:
            alloc["Деньги"] = cash
        alloc = alloc.sort_values()
        colors = color_map(list(alloc.index))
        fig = go.Figure(go.Bar(x=alloc.values / alloc.sum(), y=alloc.index, orientation="h",
                               marker=dict(color=[colors[i] for i in alloc.index], cornerradius=4),
                               text=[fmt_pct(v / alloc.sum()) for v in alloc.values], textposition="outside",
                               hovertemplate="%{y}: %{x:.1%}<extra></extra>"))
        fig.update_layout(height=40 * len(alloc) + 80, margin=dict(l=10, r=40, t=10, b=10),
                          xaxis=dict(tickformat=".0%", showgrid=False))
        c2.plotly_chart(fig, width="stretch")

hist = None
with t2:
    start = str(tx["date"].min().date())
    with st.spinner("Строю историю стоимости…"):
        try:
            prices = price_history_rub(held, start)
            hist = value_history(tx, prices)
        except Exception as e:  # noqa: BLE001
            st.error(f"Не удалось построить историю: {e}")
    if hist is not None and not hist.empty:
        st.plotly_chart(line_chart(hist[["value"]].rename(columns={"value": cur}), y_title="Стоимость, руб."),
                        width="stretch")
        twr = hist["twr"] * 100
        b = load_series(bench, start)
        comp = pd.DataFrame({cur: twr, bench: b / b.iloc[0] * 100}).ffill().dropna()
        st.markdown("**Доходность без учёта пополнений (TWR) против бенчмарка**")
        st.plotly_chart(line_chart(comp, bench, "База = 100", hover_fmt=".1f"), width="stretch")
        if len(comp) > 40:
            show_metrics_table({cur: comp[cur], bench: comp[bench]}, comp[bench], rf,
                               "D" if len(comp) < 400 else freq)
        else:
            st.caption("Метрики появятся, когда история портфеля будет длиннее ~2 месяцев.")

with t3:
    st.caption("Сравнение TWR портфеля со стратегиями НИР на том же отрезке (без пополнений).")
    if hist is not None and not hist.empty and len(hist) > 20:
        s0, s1 = hist.index[0], hist.index[-1]
        picks = st.multiselect("Стратегии", list(STRATEGIES), list(STRATEGIES)[:3])
        keys = sorted({a for p in picks for a in STRATEGIES[p]["weights"]})
        if picks:
            px, _ = load_prices(tuple(keys), "2000-01-01", None, align="none")
            comp = {cur: hist["twr"] * 100}
            for p in picks:
                sub = px[list(STRATEGIES[p]["weights"])].loc[s0:s1].dropna()
                if len(sub) > 2:
                    r = run_backtest(sub, BacktestConfig(STRATEGIES[p]["weights"], 100, "A"))
                    comp[p] = r.equity
            b = load_series(bench, str(s0.date()))
            comp[bench] = b / b.iloc[0] * 100
            cdf = pd.DataFrame(comp).ffill().dropna()
            cdf = cdf / cdf.iloc[0] * 100
            st.plotly_chart(line_chart(cdf, bench, "База = 100", hover_fmt=".1f"), width="stretch")
            if len(cdf) > 40:
                show_metrics_table({c: cdf[c] for c in cdf}, cdf[bench], rf, "D" if len(cdf) < 400 else freq)

with t4:
    show = tx.copy()
    show["type"] = show["type"].map(TX_TYPES)
    st.dataframe(show.drop(columns=["portfolio_id"]), width="stretch", hide_index=True)
    c1, c2 = st.columns([1, 3])
    del_id = c1.number_input("ID операции для удаления", 0, 10**9, 0)
    if c1.button("Удалить операцию") and del_id:
        L.delete_tx(int(del_id))
        st.rerun()
    c2.download_button("Экспорт (Excel)", to_excel({"Операции": tx, "Позиции": val}), f"{cur}.xlsx")
