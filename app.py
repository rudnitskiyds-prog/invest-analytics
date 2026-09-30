"""
ИнвестАналитика — информационно-аналитическая платформа оценки эффективности инвестиций.
Запуск:  streamlit run app.py
"""
import streamlit as st

pages = {
    "Рынок": [
        st.Page("pages/0_Обзор.py", title="Обзор рынка", icon=":material/dashboard:", default=True),
        st.Page("pages/1_Витрина.py", title="Витрина бумаг", icon=":material/table_view:"),
        st.Page("pages/2_Карточка_бумаги.py", title="Карточка бумаги", icon=":material/search:"),
    ],
    "Моделирование": [
        st.Page("pages/3_Граница_Марковица.py", title="Граница Марковица", icon=":material/scatter_plot:"),
        st.Page("pages/4_Бэктест.py", title="Бэктест", icon=":material/history:"),
    ],
    "Мой портфель": [
        st.Page("pages/5_Мой_портфель.py", title="Учёт и эффективность", icon=":material/account_balance_wallet:"),
        st.Page("pages/6_Ребалансировки.py", title="Ребалансировки", icon=":material/event_repeat:"),
    ],
    "Справка": [
        st.Page("pages/7_Методика.py", title="Методика", icon=":material/functions:"),
    ],
}
st.navigation(pages).run()
