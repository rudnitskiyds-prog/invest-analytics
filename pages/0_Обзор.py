"""
ИнвестАналитика — информационно-аналитическая платформа оценки эффективности инвестиций.
Запуск:  streamlit run app.py
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.data import iss
from ui.common import base_layout, fmt_pct, line_chart, load_series, page_setup, PALETTE

page_setup("ИнвестАналитика — обзор рынка", "📊")

st.caption("Моделирование портфелей, граница Марковица, бэктесты, учёт портфеля и "
           "витрина данных по бумагам Московской биржи.")

KEY_INDICES = {"IMOEX": "Индекс МосБиржи", "MCFTR": "МосБиржа полной доходности",
               "RGBITR": "Гособлигации TR", "RUCBTRNS": "Корп. облигации TR",
               "RTSI": "Индекс РТС"}

try:
    idx = iss.indices().set_index("SECID")
    cols = st.columns(len(KEY_INDICES))
    for c, (k, name) in zip(cols, KEY_INDICES.items()):
        if k in idx.index:
            r = idx.loc[k]
            ch = r.get("LASTCHANGEPRC")
            c.metric(name, f"{r['CURRENTVALUE']:,.2f}".replace(",", " "),
                     f"{ch:+.2f}%" if pd.notna(ch) else None)
except Exception as e:  # noqa: BLE001
    st.error(f"Не удалось получить котировки ISS: {e}")

left, right = st.columns([3, 2])
with left:
    st.subheader("Динамика за 5 лет (база = 100)")
    start = (pd.Timestamp.today() - pd.DateOffset(years=5)).date().isoformat()
    series = {}
    for k in ["MCFTR", "RGBITR", "RUCBTRNS", "GOLD_CBR", "RUONIA"]:
        try:
            series[k] = load_series(k, start)
        except Exception:  # noqa: BLE001
            pass
    if series:
        df = pd.DataFrame(series).ffill().dropna()
        st.plotly_chart(line_chart(df / df.iloc[0] * 100, benchmark="MCFTR", y_title="База = 100",
                                   hover_fmt=".1f"), width="stretch")
with right:
    st.subheader("Кривая бескупонной доходности (КБД)")
    try:
        cur = iss.zcyc()
        yr = iss.zcyc((pd.Timestamp.today() - pd.DateOffset(years=1)).date().isoformat())
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=cur["period"], y=cur["value"], name="Сегодня", mode="lines+markers",
                                 line=dict(width=2, color=PALETTE[0]), marker=dict(size=7)))
        if not yr.empty:
            fig.add_trace(go.Scatter(x=yr["period"], y=yr["value"], name="Год назад", mode="lines",
                                     line=dict(width=2, color=PALETTE[1], dash="dot")))
        base_layout(fig, 380, "% годовых")
        fig.update_xaxes(title="Срок, лет")
        st.plotly_chart(fig, width="stretch")
        st.caption(f"КБД 15 лет сегодня: {fmt_pct(iss.kbd_rate(None, 15), 2)}")
    except Exception as e:  # noqa: BLE001
        st.info(f"КБД недоступна: {e}")

st.divider()
st.markdown("""
**Разделы платформы** (меню слева):
1. **Витрина** — акции, фонды (паи), облигации, металлы: котировки, доходности, волатильность, Шарп, бета, мультипликаторы.
2. **Карточка бумаги** — график, просадки, скользящие метрики, дивиденды/купоны, полный набор коэффициентов.
3. **Граница Марковица** — облако портфелей, эффективная граница, мин. дисперсия, касательный портфель.
4. **Бэктест** — классические пассивные стратегии и собственные портфели, ребалансировка, комиссии, пополнения, налог.
5. **Мой портфель** — учёт сделок, оценка позиций, доходность TWR/XIRR, сравнение с бенчмарком и стратегиями.
6. **Ребалансировки** — календарь, отклонение от целевых долей, заявки с округлением до лотов, экспорт в календарь.
7. **Методика** — формулы коэффициентов.
""")
