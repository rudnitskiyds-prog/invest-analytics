import pandas as pd
import streamlit as st

from core.analytics import metrics as m
from core.data import iss, universe
from ui.common import (drawdown_chart, fmt_pct, line_chart, load_series, page_setup,
                       show_metrics_table, annual_returns, heatmap_returns)

page_setup("Карточка бумаги", "🔎")
rf, bench, freq = st.session_state["rf"], st.session_state["benchmark"], st.session_state["freq"]

c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
q = c1.text_input("Тикер или название (SBER, OFZ 26238, EQMX, GLDRUB_TOM, RGBITR…)", "SBER")
secid = q.strip().upper()
if q and not q.isascii():
    res = iss.search(q)
    if res.empty:
        st.warning("Ничего не найдено")
        st.stop()
    secid = c1.selectbox("Найдено", res["secid"], format_func=lambda s: f"{s} — "
                         f"{res.set_index('secid').loc[s, 'shortname']}")
years = c2.selectbox("Период, лет", [1, 3, 5, 10, 15], 2)
tr = c3.toggle("С дивидендами (TR)", value=False, help="Реинвестирование дивидендов по данным ISS")
log_y = c4.toggle("Лог. шкала", value=False)

try:
    spec = universe.CATALOG.get(secid)
    info = ({"name": spec.name, "market": "special", "board": spec.source, "isin": None, "type": spec.asset_class}
            if spec and spec.source != "iss" else iss.resolve(secid))
except Exception as e:  # noqa: BLE001
    st.error(f"{secid}: {e}")
    st.stop()

st.subheader(f"{info.get('name') or secid}  ·  {secid}")
st.caption(f"ISIN {info.get('isin') or '—'} · рынок {info['market']} · режим {info['board']} · {info.get('type') or ''}")

start = (pd.Timestamp.today() - pd.DateOffset(years=years)).date().isoformat()
with st.spinner("Загружаю историю…"):
    try:
        px = load_series(secid, start, total_return=tr)
        bpx = load_series(bench, start)
    except KeyError as e:  # в демо-режиме есть не все бумаги
        st.warning(f"Нет данных по этой бумаге: {e.args[0]}")
        st.stop()
if px.empty:
    st.warning("Нет истории котировок")
    st.stop()

rep = m.compute_all(px, bpx, rf, freq)
k = st.columns(6)
k[0].metric("Последняя цена", f"{px.iloc[-1]:,.2f}".replace(",", " "))
k[1].metric("Доходность за период", fmt_pct(rep.total_return))
k[2].metric("CAGR", fmt_pct(rep.cagr))
k[3].metric("Волатильность", fmt_pct(rep.volatility))
k[4].metric("Шарп", f"{rep.sharpe:.2f}")
k[5].metric("Бета к " + bench, f"{rep.beta:.2f}" if pd.notna(rep.beta) else "—")

df = pd.DataFrame({secid: px, bench: bpx}).ffill().dropna()
norm = df / df.iloc[0] * 100
t1, t2, t3, t4, t5 = st.tabs(["Цена и бенчмарк", "Просадки", "Скользящие метрики", "Доходность по годам",
                              "Выплаты"])
with t1:
    st.plotly_chart(line_chart(norm, bench, "База = 100", log_y=log_y, hover_fmt=".1f"), width="stretch")
with t2:
    st.plotly_chart(drawdown_chart(df, bench), width="stretch")
    info_dd = m.max_drawdown_info(px)
    st.caption(f"Макс. просадка {fmt_pct(info_dd['max_drawdown'])}: пик {info_dd['peak'].date()}, "
               f"дно {info_dd['trough'].date()}, "
               + (f"восстановление {info_dd['recovery'].date()} ({info_dd['days_to_recover']} дн.)"
                  if info_dd['recovery'] is not None else "не восстановилась"))
with t3:
    win = st.slider("Окно, мес.", 6, 36, 12)
    roll = pd.DataFrame({
        "Шарп": m.rolling_metric(px, win, "sharpe", rf, freq="M"),
        "Волатильность": m.rolling_metric(px, win, "volatility", freq="M"),
        "Бета": m.rolling_metric(px, win, "beta", benchmark=bpx, freq="M"),
    }).dropna(how="all")
    for col in roll:
        st.plotly_chart(line_chart(roll[[col]], y_title=col, height=240,
                                   pct_y=col == "Волатильность", hover_fmt=".2f"), width="stretch")
with t4:
    st.plotly_chart(heatmap_returns(annual_returns(df), 200), width="stretch")
with t5:
    if info["market"] == "special":
        st.info("Для этого ряда выплат нет")
    elif info["market"] == "bonds":
        cp = iss.coupons(secid)
        st.dataframe(cp, width="stretch", hide_index=True)
    else:
        dv = iss.dividends(secid)
        if dv.empty:
            st.info("Нет данных о дивидендах")
        else:
            st.dataframe(dv, width="stretch", hide_index=True)

st.subheader("Полный набор коэффициентов")
show_metrics_table({secid: px, bench: bpx}, bpx, rf, freq)
