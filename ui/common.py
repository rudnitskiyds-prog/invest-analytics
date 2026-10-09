"""Общие элементы интерфейса: кэш загрузок, палитра, графики, форматирование."""
from __future__ import annotations

import io
import os
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.analytics import metrics as m
from core.config import DEFAULT_BENCHMARK, DEFAULT_FREQ, DEFAULT_RF
from core.data import iss, universe

# Категориальная палитра (проверена на различимость при дальтонизме, порядок фиксирован)
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BENCH_COLOR = "#52514e"      # бенчмарк — нейтральный серый
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIVERGING = [[0, "#e34948"], [0.5, "#f0efec"], [1, "#2a78d6"]]

FREQ_LABELS = {"D": "Дневные", "W": "Недельные", "M": "Месячные"}


def color_map(names: list[str], benchmark: Optional[str] = None) -> dict[str, str]:
    """Цвет закрепляется за сущностью, а не за позицией в списке."""
    key = "_color_registry"
    reg = st.session_state.setdefault(key, {})
    out = {}
    for n in names:
        if benchmark and n == benchmark:
            out[n] = BENCH_COLOR
            continue
        if n not in reg:
            reg[n] = PALETTE[len(reg) % len(PALETTE)]
        out[n] = reg[n]
    return out


def page_setup(title: str, icon: str = "📈"):
    st.set_page_config(page_title=f"{title} · ИнвестАналитика", page_icon=icon, layout="wide")
    st.markdown("""<style>
      .block-container {padding-top: 1.6rem; max-width: 1400px}
      div[data-testid="stMetricValue"] {font-size: 1.5rem}
    </style>""", unsafe_allow_html=True)
    sidebar_settings()
    st.title(title)


def sidebar_settings():
    ss = st.session_state
    ss.setdefault("rf", DEFAULT_RF)
    ss.setdefault("benchmark", DEFAULT_BENCHMARK)
    ss.setdefault("freq", DEFAULT_FREQ)
    with st.sidebar:
        st.markdown("### Параметры расчёта")
        ss["rf"] = st.number_input("Безрисковая ставка, % годовых", 0.0, 40.0, float(ss["rf"] * 100), 0.01,
                                   help="По умолчанию — 7,86 %: 15-летняя ставка КБД Мосбиржи на 11.01.2011") / 100
        c1, c2 = st.columns(2)
        if c1.button("КБД 15 лет", help="Текущая ставка КБД Мосбиржи на 15 лет"):
            try:
                ss["rf"] = iss.kbd_rate(None, 15)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.warning(f"КБД недоступна: {e}")
        if c2.button("Сброс", help="Вернуть 7,86 % — ставку КБД на 15 лет на 11.01.2011"):
            ss["rf"] = DEFAULT_RF
            st.rerun()
        bench_opts = ["MCFTR", "IMOEX", "MEBCTR", "RGBITR", "RUONIA", "GOLD_CBR"]
        ss["benchmark"] = st.selectbox("Бенчмарк", bench_opts, bench_opts.index(ss["benchmark"])
                                       if ss["benchmark"] in bench_opts else 0)
        ss["freq"] = st.selectbox("Частота доходностей для метрик", list(FREQ_LABELS),
                                  list(FREQ_LABELS).index(ss["freq"]), format_func=FREQ_LABELS.get)
        st.caption("Данные: ISS Московской биржи (задержка 15 мин), Банк России.")
        if os.environ.get("IP_OFFLINE") == "1":
            st.info("Демо-режим: месячные ряды 2010–2025 без подключения к бирже.")
        elif universe.FALLBACK_USED:
            st.warning("Источник данных не ответил — для " + ", ".join(sorted(universe.FALLBACK_USED)) +
                       " использованы демо-ряды (месячные, по 2025 г.).")


# ------------------------------------------------------------------ cached loaders
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_prices(keys: tuple[str, ...], start: str, end: Optional[str] = None,
                align: str = "common") -> tuple[pd.DataFrame, dict]:
    return universe.load_prices(list(keys), start, end, align=align)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_series(key: str, start: str, end: Optional[str] = None, total_return: bool = False) -> pd.Series:
    return universe.load_series(key, start, end, total_return)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def catalog_options() -> list[str]:
    return list(universe.CATALOG)


def asset_label(k: str) -> str:
    spec = universe.CATALOG.get(k)
    return f"{k} — {spec.name}" if spec else k


# ------------------------------------------------------------------ formatting
def fmt_pct(x, digits=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    out = f"{x * 100:.{digits}f}"
    if float(out) == 0:
        out = out.lstrip("-")
    return f"{out}%".replace(".", ",")


def fmt_rub(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:,.0f} ₽".replace(",", " ")


def is_num(x) -> bool:
    """Конечное число (не None, не NaN, не bool)."""
    if x is None or isinstance(x, bool):
        return False
    try:
        return bool(np.isfinite(float(x)))
    except (TypeError, ValueError):
        return False


def fmt_num(x, digits: int = 0) -> str:
    """Число с пробелом между разрядами и запятой в дробной части; нет числа — «—»."""
    if not is_num(x):
        return "—"
    s = f"{float(x):,.{digits}f}"
    if float(s.replace(",", "")) == 0:
        s = s.lstrip("-")
    return s.replace(",", " ").replace(".", ",")


def fmt_signed_pct(x, digits: int = 1) -> str:
    """Доля со знаком: +1,2% / -0,5%; округлённый ноль — без знака."""
    if not is_num(x):
        return "—"
    s = fmt_pct(float(x), digits)
    return "+" + s if float(x) > 0 and s != fmt_pct(0.0, digits) else s


def fmt_big_rub(x) -> str:
    """Крупные суммы компактно: «6,50 трлн ₽», «12,3 млрд ₽»."""
    if not is_num(x):
        return "—"
    v = float(x)
    a = abs(v)
    for k, unit in ((1e12, "трлн"), (1e9, "млрд"), (1e6, "млн")):
        if a >= k:
            return f"{fmt_num(v / k, 2 if a / k < 10 else 1)} {unit} ₽"
    return fmt_rub(v)


def fmt_date(x) -> str:
    """Дата в формате ДД.ММ.ГГГГ; нет даты — «—»."""
    if x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NaT or x == "":
        return "—"
    try:
        return pd.Timestamp(str(x)[:10] if isinstance(x, str) else x).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(x)


def fmt_metric(key: str, x) -> str:
    """Значение коэффициента: доли из metrics.PERCENT_FIELDS — в процентах, прочие — 2 знака."""
    if not is_num(x):
        return "—"
    if key in m.PERCENT_FIELDS:
        return fmt_pct(float(x), 1)
    if key == "days_to_recover":
        return fmt_num(x)
    return fmt_num(x, 2)


_MD_SPECIAL = set("\\`*_[](){}#<>$~|!")


def esc(text) -> str:
    """Экранирование пользовательского ввода и внешних строк для st.markdown/caption/error:
    ссылки, разметка, HTML и формулы выводятся как обычный текст."""
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in str(text))


def is_offline() -> bool:
    return os.environ.get("IP_OFFLINE") == "1"


SYMBOL_PAGE = "pages/2_Карточка_бумаги.py"


def symbol_link(secid: str, label: Optional[str] = None, container=None):
    """Ссылка на карточку бумаги (?s=SECID). Вне многостраничного приложения — обычный текст."""
    target = container or st
    try:
        target.page_link(SYMBOL_PAGE, label=label or secid, query_params={"s": secid})
    except Exception:  # noqa: BLE001 — страница не зарегистрирована (одиночный запуск файла)
        target.markdown(f"**{label or secid}**")


def to_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31])
    return buf.getvalue()


# ------------------------------------------------------------------ charts
def base_layout(fig: go.Figure, height: int = 420, y_title: str = "", pct_y: bool = False,
                log_y: bool = False) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=30, b=10),
        hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        yaxis_title=y_title, font=dict(size=13),
    )
    fig.update_xaxes(showgrid=False, showline=True, linewidth=1, linecolor="rgba(128,128,128,.4)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,.15)", zeroline=False,
                     tickformat=".0%" if pct_y else None, type="log" if log_y else "linear")
    return fig


def line_chart(df: pd.DataFrame, benchmark: Optional[str] = None, y_title: str = "",
               pct_y: bool = False, log_y: bool = False, height: int = 420,
               hover_fmt: str = ",.0f") -> go.Figure:
    colors = color_map(list(df.columns), benchmark)
    fig = go.Figure()
    for c in df.columns:
        is_b = benchmark is not None and c == benchmark
        fig.add_trace(go.Scatter(
            x=df.index, y=df[c], name=c, mode="lines",
            line=dict(width=2, color=colors[c], dash="dot" if is_b else "solid"),
            hovertemplate=f"%{{y:{'.1%' if pct_y else hover_fmt}}}<extra>{c}</extra>"))
    return base_layout(fig, height, y_title, pct_y, log_y)


def drawdown_chart(prices: pd.DataFrame, benchmark: Optional[str] = None, height: int = 280) -> go.Figure:
    dd = prices.apply(m.drawdown_series)
    colors = color_map(list(dd.columns), benchmark)
    fig = go.Figure()
    for c in dd.columns:
        fig.add_trace(go.Scatter(x=dd.index, y=dd[c], name=c, mode="lines",
                                 line=dict(width=1.6, color=colors[c],
                                           dash="dot" if c == benchmark else "solid"),
                                 hovertemplate=f"%{{y:.1%}}<extra>{c}</extra>"))
    return base_layout(fig, height, "Просадка", pct_y=True)


def annual_returns(prices: pd.DataFrame) -> pd.DataFrame:
    y = prices.resample("YE").last()
    first = prices.iloc[[0]]
    y = pd.concat([first, y])
    r = y.pct_change().dropna(how="all")
    r.index = r.index.year
    return r[~r.index.duplicated(keep="last")]


def heatmap_returns(r: pd.DataFrame, height: int = 360) -> go.Figure:
    z = r.T.values
    lim = np.nanmax(np.abs(z)) if np.isfinite(z).any() else 1
    fig = go.Figure(go.Heatmap(
        z=z, x=[str(i) for i in r.index], y=list(r.columns), colorscale=DIVERGING, zmid=0,
        zmin=-lim, zmax=lim, xgap=2, ygap=2,
        text=[[fmt_pct(v, 0) for v in row] for row in z], texttemplate="%{text}",
        hovertemplate="%{y}, %{x}: %{text}<extra></extra>", showscale=False))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10))
    fig.update_xaxes(type="category")
    return fig


MONTHS_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


def monthly_heatmap(years: list, cells: list, year_total: list, median_by_month: list) -> go.Figure:
    """Сетка «год × месяц» (свежие годы сверху, внизу — медиана) и отдельная колонка итога года
    со своей цветовой шкалой. Значения — доли; пустые ячейки — «—»."""
    from plotly.subplots import make_subplots

    def num(v):
        return float(v) if is_num(v) else np.nan

    rows = [[num(v) for v in row] for row in cells]
    med = [num(v) for v in (median_by_month or [None] * 12)]
    z = [med] + rows                          # снизу вверх: медиана, затем годы по возрастанию
    ylab = ["Медиана"] + [str(y) for y in years]
    zt = [[np.nan]] + [[num(v)] for v in (year_total or [None] * len(years))]
    arr = np.array(z, dtype=float)
    lim = float(np.nanmax(np.abs(arr))) if np.isfinite(arr).any() else 0.1
    tarr = np.array(zt, dtype=float)
    tlim = float(np.nanmax(np.abs(tarr))) if np.isfinite(tarr).any() else 0.1
    fig = make_subplots(rows=1, cols=2, column_widths=[0.88, 0.12], shared_yaxes=True,
                        horizontal_spacing=0.01)
    fig.add_trace(go.Heatmap(
        z=z, x=MONTHS_RU, y=ylab, colorscale=DIVERGING, zmid=0, zmin=-lim, zmax=lim, xgap=2, ygap=2,
        text=[[fmt_pct(v, 1) for v in row] for row in z], texttemplate="%{text}",
        hovertemplate="%{y}, %{x}: %{text}<extra></extra>", showscale=False), 1, 1)
    fig.add_trace(go.Heatmap(
        z=zt, x=["Год"], y=ylab, colorscale=DIVERGING, zmid=0, zmin=-tlim, zmax=tlim, xgap=2, ygap=2,
        text=[["" if not is_num(r[0]) else fmt_pct(r[0], 1)] for r in zt], texttemplate="<b>%{text}</b>",
        hovertemplate="%{y}, итог года: %{text}<extra></extra>", showscale=False), 1, 2)
    fig.update_layout(height=60 + 26 * len(ylab), margin=dict(l=10, r=10, t=10, b=10), font=dict(size=12))
    fig.update_xaxes(side="top", type="category")
    fig.update_yaxes(type="category")
    return fig


def bar_chart(labels: list, values: list, name: str, y_title: str, x_title: str = "",
              muted: Optional[list[bool]] = None, hover_fmt: str = ",.2f", height: int = 300) -> go.Figure:
    """Столбики одной сущности (цвет закреплён за name); muted — бледные столбики (напр. будущие купоны)."""
    color = color_map([name])[name]
    op = [0.35 if (muted and muted[i]) else 1.0 for i in range(len(values))]
    fig = go.Figure(go.Bar(x=[str(x) for x in labels], y=values, name=name, marker=dict(color=color, opacity=op),
                           hovertemplate=f"%{{x}}: %{{y:{hover_fmt}}}<extra>{name}</extra>"))
    base_layout(fig, height, y_title)
    fig.update_layout(hovermode="closest", showlegend=False)
    fig.update_xaxes(title=x_title, type="category")
    return fig


def position_scale(value: float, lo: float, hi: float, name: str, x_title: str,
                   q25: Optional[float] = None, median: Optional[float] = None,
                   q75: Optional[float] = None, fmt=None, height: int = 130) -> go.Figure:
    """Горизонтальная шкала «где бумага среди рынка»: диапазон min–max, межквартильный диапазон,
    медиана и точка бумаги. Только отображение готовых чисел."""
    fmt = fmt or (lambda v: fmt_num(v, 2))
    color = color_map([name])[name]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[lo, hi], y=[0, 0], mode="lines", line=dict(color="rgba(128,128,128,.35)", width=6),
                             hoverinfo="skip", showlegend=False))
    if is_num(q25) and is_num(q75):
        fig.add_trace(go.Scatter(x=[q25, q75], y=[0, 0], mode="lines", line=dict(color=BENCH_COLOR, width=12),
                                 name="Межквартильный диапазон", hovertemplate=f"{fmt(q25)} — {fmt(q75)}<extra></extra>"))
    if is_num(median):
        fig.add_trace(go.Scatter(x=[median], y=[0], mode="markers", name="Медиана",
                                 marker=dict(symbol="line-ns", size=26, line=dict(width=3, color="#000")),
                                 hovertemplate=f"Медиана {fmt(median)}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[value], y=[0], mode="markers", name=name,
                             marker=dict(size=16, color=color, line=dict(width=2, color="#fff")),
                             hovertemplate=f"{name}: {fmt(value)}<extra></extra>"))
    base_layout(fig, height)
    fig.update_layout(hovermode="closest", legend=dict(orientation="h", y=1.15, x=0))
    fig.update_yaxes(visible=False, range=[-1, 1])
    fig.update_xaxes(title=x_title)
    return fig


def show_metrics_table(series: dict[str, pd.Series], benchmark_series: Optional[pd.Series],
                       rf: float, freq: str, key: str = "mt") -> pd.DataFrame:
    raw = m.metrics_table(series, benchmark_series, rf, freq)
    st.dataframe(m.format_metrics_table(raw), width="stretch", height=min(38 * (len(raw) + 1), 900))
    return raw
