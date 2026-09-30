"""Общие элементы интерфейса: кэш загрузок, палитра, графики, форматирование."""
from __future__ import annotations

import io
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

FREQ_LABELS = {"D": "Дневные", "W": "Недельные", "M": "Месячные (как в НИР)"}


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
                                   help="В НИР: 7,86 % — 15-летняя ставка КБД Мосбиржи на 11.01.2011") / 100
        c1, c2 = st.columns(2)
        if c1.button("КБД 15 лет", help="Текущая ставка КБД Мосбиржи на 15 лет"):
            try:
                ss["rf"] = iss.kbd_rate(None, 15)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.warning(f"КБД недоступна: {e}")
        if c2.button("Как в НИР"):
            ss["rf"] = DEFAULT_RF
            st.rerun()
        bench_opts = ["MCFTR", "IMOEX", "MEBCTR", "RGBITR", "RUONIA", "GOLD_CBR"]
        ss["benchmark"] = st.selectbox("Бенчмарк", bench_opts, bench_opts.index(ss["benchmark"])
                                       if ss["benchmark"] in bench_opts else 0)
        ss["freq"] = st.selectbox("Частота доходностей для метрик", list(FREQ_LABELS),
                                  list(FREQ_LABELS).index(ss["freq"]), format_func=FREQ_LABELS.get)
        st.caption("Данные: ISS Московской биржи (задержка 15 мин), Банк России.")


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
    return f"{x * 100:.{digits}f}%".replace(".", ",")


def fmt_rub(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:,.0f} ₽".replace(",", " ")


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
    return fig


def show_metrics_table(series: dict[str, pd.Series], benchmark_series: Optional[pd.Series],
                       rf: float, freq: str, key: str = "mt") -> pd.DataFrame:
    raw = m.metrics_table(series, benchmark_series, rf, freq)
    st.dataframe(m.format_metrics_table(raw), width="stretch", height=min(38 * (len(raw) + 1), 900))
    return raw
