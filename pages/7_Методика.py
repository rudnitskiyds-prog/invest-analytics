import streamlit as st

from core.data.universe import describe_catalog
from core.analytics.strategies import STRATEGIES
from ui.common import page_setup

page_setup("Методика расчёта", "📐")

st.markdown("""
Показатели и допущения — по НИР «Сравнительный анализ пассивных стратегий управления
инвестиционным портфелем на российском финансовом рынке» (п. 1.3, табл. 2; п. 2.1).

**Обозначения:** $R_p$ — доходность портфеля, $R_f$ — безрисковая ставка, $R_m$ — доходность рынка,
$\\sigma_p$ — стандартное отклонение портфеля, $\\sigma_d$ — стандартное отклонение отрицательных
доходностей, $\\sigma_m$ — стандартное отклонение рынка, $n$ — число периодов в году
(252 / 52 / 12).
""")

F = [
    ("Накопленная доходность", r"TR = \frac{V_T}{V_0} - 1", ""),
    ("CAGR", r"CAGR = \left(\frac{V_T}{V_0}\right)^{365.25/\text{дней}} - 1", ""),
    ("Волатильность", r"\sigma = s(r_t)\cdot\sqrt{n}", ""),
    ("Максимальная просадка", r"MaxDD = \min_t\left(\frac{V_t}{\max_{\tau\le t}V_\tau} - 1\right)", ""),
    ("Бета", r"\beta = \frac{Cov(R_p, R_m)}{Var(R_m)}", "Мера систематического риска"),
    ("Коэффициент Шарпа", r"S = \frac{R_p - R_f}{\sigma_p}",
     "Числитель — среднее превышение над Rf × n (или CAGR − Rf в «геометрическом» режиме)"),
    ("Коэффициент Трейнора", r"T = \frac{R_p - R_f}{\beta_p}", ""),
    ("Коэффициент Сортино", r"So = \frac{R_p - R_f}{\sigma_d},\quad \sigma_d=\sqrt{\tfrac{1}{N}\sum\min(r_t-r_f,0)^2}\cdot\sqrt{n}", ""),
    ("M² Модильяни", r"M^2 = S_p\cdot\sigma_m + R_f", ""),
    ("Альфа Дженсена", r"\alpha = R_p - \left[R_f + \beta(R_m - R_f)\right]", ""),
    ("Коэффициент Кальмара", r"K = \frac{CAGR}{|MaxDD|}", ""),
    ("Коэффициент Швагера", r"Sw = \frac{\sum Profit}{\sum|Loss|}", "По доходностям периодов"),
    ("Коэффициент Омега", r"\Omega = \frac{\int_L^\infty[1-F(r)]dr}{\int_{-\infty}^L F(r)dr}"
                          r"\approx\frac{\sum\max(r_t-L,0)}{\sum\max(L-r_t,0)}", "Порог L = Rf"),
    ("Ошибка слежения", r"TE = s(R_p - R_m)\cdot\sqrt{n}", ""),
    ("Информационный коэффициент", r"IR = \frac{\overline{R_p - R_m}\cdot n}{TE}", ""),
    ("VaR / CVaR 95%", r"VaR = -q_{5\%}(r_t),\quad CVaR = -E[r_t \mid r_t\le q_{5\%}]", "Исторический метод"),
    ("TWR портфеля", r"1+TWR = \prod_t \frac{V_t - F_t}{V_{t-1}}", "F — внешние потоки (пополнения/выводы)"),
    ("XIRR", r"\sum_i \frac{CF_i}{(1+r)^{(t_i-t_0)/365.25}} = 0", "Денежно-взвешенная доходность инвестора"),
]
for name, tex, note in F:
    c1, c2 = st.columns([1, 2])
    c1.markdown(f"**{name}**" + (f"  \n<span style='color:gray;font-size:.85em'>{note}</span>" if note else ""),
                unsafe_allow_html=True)
    c2.latex(tex)

st.subheader("Граница Марковица")
st.markdown(r"""
$\min_w\; w^\top\Sigma w$ при $w^\top\mu = r^*$, $\sum w_i = 1$, $w_{min}\le w_i\le w_{max}$ (SLSQP).
Касательный портфель — $\max_w (w^\top\mu - R_f)/\sqrt{w^\top\Sigma w}$. $\mu$ и $\Sigma$ — годовые
оценки по историческим доходностям выбранной частоты; опционально — сжатие ковариации Ледуа–Вольфа.
""")

st.subheader("Допущения бэктеста (НИР, п. 2.1)")
st.markdown("""
* Начальный капитал 1 000 000 руб., без довнесений (опционально — пополнения).
* Ребалансировка раз в год в последний торговый день декабря (опционально — месяц / квартал / полгода, коридор).
* Индексы-аналоги ПИФов вместо котировок фондов: комиссии УК, спред слежения и налоги не учитываются
  (опционально — комиссия сделки и НДФЛ на реализованную прибыль).
* Безрисковая ставка 7,86 % — ставка КБД Мосбиржи на 15 лет на 11.01.2011; бенчмарк — MCFTR.
""")

st.subheader("Каталог базовых активов")
st.dataframe(describe_catalog(), hide_index=True, width="stretch")

st.subheader("Преднастроенные стратегии")
for k, v in STRATEGIES.items():
    st.markdown(f"**{k}** — {v['note']}  \n" + ", ".join(f"{a}: {w:.1%}" for a, w in v["weights"].items()))
