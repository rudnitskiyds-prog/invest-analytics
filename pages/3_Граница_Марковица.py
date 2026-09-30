import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.analytics.frontier import efficient_frontier, estimate_inputs
from ui.common import (PALETTE, SEQ_BLUE, asset_label, base_layout, catalog_options, fmt_pct,
                       load_prices, page_setup)

page_setup("Граница эффективности Марковица", "🎯")
rf = st.session_state["rf"]

with st.form("frontier"):
    c1, c2 = st.columns([3, 2])
    base = c1.multiselect("Базовые активы (индексы-аналоги ПИФов)", catalog_options(),
                          ["MCFTR", "CORP_CHAIN", "RGBITR", "GOLD_CBR"], format_func=asset_label,
                          help="Денежный рынок (RUONIA) почти безрисков — при его включении граница вырождается "
                               "в «всё в RUONIA»; его роль играет линия CML от безрисковой ставки")
    extra = c2.text_input("Дополнительно тикеры ISS через запятую", "",
                          help="Акции, паи, металлы: SBER, LKOH, EQMX, GLDRUB_TOM…")
    c3, c4, c5, c6 = st.columns(4)
    start = c3.date_input("Начало", pd.Timestamp("2011-01-31"))
    end = c4.date_input("Конец", pd.Timestamp("2025-12-31"))
    freq = c5.selectbox("Частота", ["M", "W", "D"], format_func={"M": "Месяц", "W": "Неделя", "D": "День"}.get)
    mu_method = c6.selectbox("Ожидаемая доходность", ["arith", "cagr"],
                             format_func={"arith": "Среднее арифм. × n", "cagr": "CAGR"}.get)
    c7, c8, c9, c10 = st.columns(4)
    w_min = c7.number_input("Мин. доля актива, %", 0.0, 50.0, 0.0, 1.0) / 100
    w_max = c8.number_input("Макс. доля актива, %", 5.0, 100.0, 100.0, 5.0) / 100
    n_rand = c9.number_input("Случайных портфелей", 500, 30000, 6000, 500)
    shrink = c10.toggle("Сжатие ковариации (Ledoit–Wolf)", False)
    go_btn = st.form_submit_button("Построить", type="primary")

keys = base + [t.strip().upper() for t in extra.split(",") if t.strip()]
if go_btn:
    st.session_state["frontier_req"] = (tuple(keys), str(start), str(end), freq, mu_method, w_min, w_max,
                                        int(n_rand), shrink)
req = st.session_state.get("frontier_req")
if not req:
    st.info("Выберите активы и нажмите «Построить».")
    st.stop()
keys, start, end, freq, mu_method, w_min, w_max, n_rand, shrink = req
if len(keys) < 2:
    st.warning("Нужно минимум 2 актива")
    st.stop()
if w_min * len(keys) > 1 or w_max * len(keys) < 1:
    st.error("Ограничения на доли несовместимы с числом активов")
    st.stop()

with st.spinner("Загружаю данные…"):
    prices, info = load_prices(tuple(keys), start, end)
missing = [k for k, v in info.items() if v.get("n", 0) == 0]
if missing:
    st.warning(f"Нет данных: {', '.join(missing)}")
if prices.empty or prices.shape[1] < 2:
    st.stop()
st.caption(f"Общий период данных: {prices.index[0].date()} — {prices.index[-1].date()} "
           f"(ограничен самым «молодым» активом: "
           f"{max(info.items(), key=lambda kv: kv[1]['first'] or pd.Timestamp(0))[0]})")

inp = estimate_inputs(prices, freq, rf, mu_method, shrink)
fr = efficient_frontier(inp, 50, w_min, w_max, n_rand)

fig = go.Figure()
rnd = fr.random
fig.add_trace(go.Scatter(
    x=rnd["vol"], y=rnd["ret"], mode="markers", name="Случайные портфели",
    marker=dict(size=4, color=rnd["sharpe"], colorscale=[[i / 6, c] for i, c in enumerate(SEQ_BLUE)],
                colorbar=dict(title="Шарп", thickness=10), opacity=0.55),
    hovertemplate="σ %{x:.1%} · r %{y:.1%} · Шарп %{marker.color:.2f}<extra></extra>"))
f = fr.frontier.sort_values("vol")
fig.add_trace(go.Scatter(x=f["vol"], y=f["ret"], mode="lines", name="Эффективная граница",
                         line=dict(width=3, color=PALETTE[1]),
                         hovertemplate="σ %{x:.1%} · r %{y:.1%}<extra>Граница</extra>"))
mv, ms = fr.point(fr.min_var), fr.point(fr.max_sharpe)
xmax = max(rnd["vol"].max(), fr.assets_points["vol"].max()) * 1.05
fig.add_trace(go.Scatter(x=[0, xmax], y=[rf, rf + ms["sharpe"] * xmax], mode="lines", name="CML",
                         line=dict(width=1.5, color="#52514e", dash="dash"), hoverinfo="skip"))
fig.add_trace(go.Scatter(x=[mv["vol"]], y=[mv["ret"]], mode="markers+text", name="Мин. дисперсия",
                         text=["Мин. дисперсия"], textposition="middle left",
                         marker=dict(size=14, color=PALETTE[2], symbol="diamond",
                                     line=dict(width=2, color="white"))))
fig.add_trace(go.Scatter(x=[ms["vol"]], y=[ms["ret"]], mode="markers+text", name="Макс. Шарп",
                         text=["Макс. Шарп"], textposition="top center",
                         marker=dict(size=16, color=PALETTE[7], symbol="star", line=dict(width=2, color="white"))))
ap = fr.assets_points
fig.add_trace(go.Scatter(x=ap["vol"], y=ap["ret"], mode="markers+text", name="Активы",
                         text=list(ap.index), textposition="bottom center",
                         textfont=dict(size=12, shadow="0 0 3px white"),
                         marker=dict(size=10, color=PALETTE[6], line=dict(width=2, color="white")),
                         hovertemplate="%{text}: σ %{x:.1%} · r %{y:.1%}<extra></extra>"))
base_layout(fig, 560, "Ожидаемая доходность, год.", pct_y=True)
fig.update_layout(hovermode="closest")
fig.update_xaxes(title="Волатильность (σ), год.", tickformat=".0%", range=[0, xmax])
st.plotly_chart(fig, width="stretch")

c1, c2 = st.columns(2)
for col, title, w, pt in [(c1, "Портфель минимальной дисперсии", fr.min_var, mv),
                          (c2, "Касательный портфель (макс. Шарп)", fr.max_sharpe, ms)]:
    with col:
        st.markdown(f"**{title}** — доходность {fmt_pct(pt['ret'])}, σ {fmt_pct(pt['vol'])}, "
                    f"Шарп {pt['sharpe']:.2f}")
        wt = w[w > 1e-4].sort_values(ascending=False)
        st.dataframe(pd.DataFrame({"Доля": wt}), width="stretch",
                     column_config={"Доля": st.column_config.ProgressColumn("Доля", format="percent",
                                                                           min_value=0, max_value=1)})

st.markdown("**Выбрать портфель на границе по целевому риску**")
target_vol = st.slider("Целевая волатильность, %", float(f["vol"].min() * 100), float(f["vol"].max() * 100),
                       float(ms["vol"] * 100), 0.1) / 100
row = f.iloc[(f["vol"] - target_vol).abs().argmin()]
w_sel = pd.Series({c[2:]: row[c] for c in f.columns if c.startswith("w_")})
st.write(f"Доходность {fmt_pct(row['ret'])} · σ {fmt_pct(row['vol'])} · Шарп {row['sharpe']:.2f}")
st.dataframe(pd.DataFrame({"Доля": w_sel[w_sel > 1e-4]}).T.style.format("{:.1%}"), width="stretch")

cc1, cc2, cc3 = st.columns(3)
for col, label, w in [(cc1, "Мин. дисперсия → в бэктест", fr.min_var),
                      (cc2, "Макс. Шарп → в бэктест", fr.max_sharpe),
                      (cc3, "Выбранный → в бэктест", w_sel)]:
    if col.button(label):
        cp = st.session_state.setdefault("custom_portfolios", {})
        cp[label.split(" →")[0] + " (Марковиц)"] = {k: round(float(v), 4) for k, v in w.items() if v > 1e-4}
        st.success("Портфель добавлен на страницу «Бэктест»")

with st.expander("Корреляционная матрица и входные параметры"):
    corr = inp.cov / np.sqrt(np.outer(np.diag(inp.cov), np.diag(inp.cov)))
    st.dataframe(corr.style.format("{:.2f}").background_gradient(cmap="RdBu", vmin=-1, vmax=1),
                 width="stretch")
    st.dataframe(pd.DataFrame({"Ожид. доходность": inp.mu, "Волатильность": np.sqrt(np.diag(inp.cov))})
                 .style.format("{:.2%}"), width="stretch")
