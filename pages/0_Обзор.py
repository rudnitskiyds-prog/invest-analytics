"""
ИнвестАналитика — информационно-аналитическая платформа оценки эффективности инвестиций.
Запуск:  streamlit run app.py
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.data import iss, public_data
from ui.common import (base_layout, fmt_date, fmt_num, fmt_pct, is_num, is_offline, line_chart, load_series, page_setup,
                       symbol_link, PALETTE)

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
    if is_offline():
        st.info("Текущие значения индексов в демо-режиме не показываются — нужны живые данные ISS.")
    else:
        st.error(f"Не удалось получить котировки ISS: {str(e)[:200]}")

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
        st.info("КБД недоступна в демо-режиме — нужны живые данные ISS." if is_offline()
                else f"КБД недоступна: {str(e)[:200]}")

st.subheader("Лидеры рейтинга риск/доходность")


def _leaders(items: dict, cls: str, k: int = 5) -> list[tuple[str, dict]]:
    rows = [(s, it) for s, it in items.items() if isinstance(it, dict) and it.get("class") == cls
            and is_num(it.get("score"))]
    return sorted(rows, key=lambda x: -float(x[1]["score"]))[:k]


try:
    stats = public_data.symbol_stats()
    items = (stats or {}).get("items") or {}
    if not items:
        st.caption("Рейтинг появится после ночного пересчёта (public/data/symbol_stats.json).")
    else:
        cols = st.columns(2)
        for col, (cls, title) in zip(cols, [("share", "Акции"), ("fund", "Фонды")]):
            with col:
                st.markdown(f"**{title}**")
                top = _leaders(items, cls)
                if not top:
                    st.caption("Нет данных.")
                for i, (secid, it) in enumerate(top, 1):
                    a, b = st.columns([2, 3])
                    with a:
                        symbol_link(secid, f"{i}. {secid} — {fmt_num(it['score'])} из 100")
                    b.caption(f"CAGR {fmt_pct(it.get('cagr'))} · волатильность {fmt_pct(it.get('volatility'))} · "
                              f"Шарп {fmt_num(it.get('sharpe'), 2)}")
        upd = fmt_date(stats.get("updated"))
        st.caption("Балл 0–100 — среднее перцентилей Шарпа, Сортино, Омеги, Кальмара и Мартина среди торгуемых "
                   "сейчас бумаг того же класса; месячные данные за окно до 10 лет, акции — с дивидендами. "
                   "Пересчёт — ночью" + (f", обновлено {upd}." if upd != "—" else ".") +
                   " Нажмите на тикер, чтобы открыть карточку бумаги.")
except Exception as e:  # noqa: BLE001
    st.info(f"Рейтинг недоступен: {e}")

st.divider()
st.markdown("""
**Разделы платформы** (меню слева):
1. **Витрина** — акции, фонды (паи), облигации, металлы: котировки, доходности, волатильность, Шарп, бета, мультипликаторы.
2. **Карточка бумаги** — ключевые цифры, график против бенчмарка, доходность по периодам, моментум, помесячная доходность, рейтинг, риск и просадки, дивиденды/купоны, комиссия фонда.
3. **Граница Марковица** — облако портфелей, эффективная граница, мин. дисперсия, касательный портфель.
4. **Бэктест** — классические пассивные стратегии и собственные портфели, ребалансировка, комиссии, пополнения, налог.
5. **Мой портфель** — учёт сделок, оценка позиций, доходность TWR/XIRR, сравнение с бенчмарком и стратегиями.
6. **Ребалансировки** — календарь, отклонение от целевых долей, заявки с округлением до лотов, экспорт в календарь.
7. **Методика** — формулы коэффициентов.
""")
