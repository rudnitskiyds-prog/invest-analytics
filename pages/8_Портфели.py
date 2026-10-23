"""Портфели и стратегии: листинг готовых портфелей и страница портфеля (?p=slug).

Все расчёты — в core.analytics.portfolios; страница только показывает готовые числа.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import config
from core.analytics import metrics as m
from core.analytics.strategies import STRATEGIES
from ui.common import (asset_label, base_layout, color_map, esc, fmt_big_rub, fmt_date, fmt_num, fmt_pct,
                       fmt_rub, fmt_signed_pct, is_num, is_offline, line_chart, monthly_heatmap,
                       page_setup, rf_caption, to_excel)

try:  # модуль core.analytics.portfolios; без него — понятное сообщение, а не трейсбек
    from core.analytics import portfolios as pf
    PF_ERROR: Optional[str] = None
except Exception as e:  # noqa: BLE001
    pf = None
    PF_ERROR = f"{type(e).__name__}: {e}"

ss = st.session_state
BACKTEST_PAGE = "pages/4_Бэктест.py"
GROUPS = ["Все", "Ленивые портфели", "Тематические"]
GROUP_LABELS = {"Все": "Все", "Ленивые портфели": "Ленивые", "Тематические": "Тематические"}
RR_PERIODS = {"1Y": "1 год", "5Y": "5 лет", "10Y": "10 лет"}
RR_SHORTER = {"1Y": "1 года", "5Y": "5 лет", "10Y": "10 лет"}
CHART_PERIODS = {"YTD": "С начала года", "1Y": "1 год", "5Y": "5 лет", "10Y": "10 лет", "ALL": "Всё время"}
RETURN_PERIODS = [("1D", "1 день"), ("1W", "1 неделя"), ("1M", "1 месяц"), ("6M", "6 месяцев"),
                  ("YTD", "С начала года"), ("1Y", "1 год"), ("3Y", "3 года*"), ("5Y", "5 лет*"),
                  ("10Y", "10 лет*"), ("ALL", "Всё время*")]
METRIC_WINDOWS = [("1Y", "1 год"), ("5Y", "5 лет"), ("10Y", "10 лет"), ("MAX", "Всё время")]
METRIC_ROWS = [("total_return", "Доходность за период"), ("cagr", "Доходность в год"),
               ("volatility", "Волатильность в год"), ("max_drawdown", "Макс. просадка"),
               ("current_drawdown", "Текущая просадка"), ("sharpe", "Коэф. Шарпа"), ("sortino", "Коэф. Сортино"),
               ("omega", "Коэф. Омега"), ("calmar", "Коэф. Кальмара"), ("martin", "Коэф. Мартина"),
               ("ulcer_index", "Индекс язвы"), ("alpha", "Альфа Дженсена (год.)"), ("beta", "Бета"),
               ("r_squared", "R² с бенчмарком"), ("correlation", "Корреляция с бенчмарком"),
               ("up_capture", "Захват роста"), ("down_capture", "Захват падения")]
REL_KEYS = {"alpha", "beta", "r_squared", "correlation", "up_capture", "down_capture"}
PCT_KEYS = set(m.PERCENT_FIELDS) | {"current_drawdown", "alpha", "ulcer_index"}
REBAL_RU = {"A": "раз в год", "Q": "раз в квартал", "M": "раз в месяц", "H": "раз в полгода",
            "none": "без ребалансировки"}

# Колонки листинга: (подпись, имена в core, тип). Тип: pct — доля, pct2 — доля с 2 знаками,
# ratio — коэффициент, rank — ранг 0–100, text, date.
TABLE_SPEC = [
    ("Портфель", ["name"], "text"),
    ("Группа", ["group"], "text"),
    ("1 день", ["ret_1d", "1Д", "1D"], "pct"),
    ("С начала года", ["ytd", "YTD"], "pct"),
    ("В год за 10 лет", ["cagr_10y", "CAGR 10 лет"], "pct"),
    ("В год за всё время", ["cagr_all"], "pct"),
    ("Макс. просадка", ["max_drawdown"], "pct"),
    ("Текущая просадка", ["current_drawdown"], "pct"),
    ("Комиссия фондов", ["fee", "Комиссия"], "pct2"),
    ("Шарп", ["sharpe"], "ratio"),
    ("Сортино", ["sortino"], "ratio"),
    ("Омега", ["omega"], "ratio"),
    ("Кальмар", ["calmar"], "ratio"),
    ("Мартин", ["martin"], "ratio"),
    ("Индекс язвы", ["ulcer_index"], "pct2"),
    ("Волатильность за год", ["volatility_1y"], "pct"),
    ("Рейтинг (5 лет)", ["rank", "Ранг"], "rank"),
    ("История с", ["since"], "date"),
    ("Данные на", ["last_date"], "date"),
    ("Бенчмарк", ["benchmark"], "text"),
    ("Комиссия известна для доли", ["fee_coverage"], "pct"),
]
DEFAULT_HIDDEN = {"Группа", "В год за всё время", "Волатильность за год", "Данные на", "Бенчмарк",
                  "Комиссия известна для доли"}
COL_HELP = {
    "1 день": "Изменение стоимости за последний день данных.",
    "С начала года": "Изменение стоимости с последнего торгового дня прошлого года.",
    "В год за 10 лет": "Среднегодовая доходность (CAGR) за 10 лет; пусто — истории меньше 10 лет.",
    "В год за всё время": "Среднегодовая доходность (CAGR) за всю историю портфеля.",
    "Макс. просадка": "Самое глубокое падение от максимума за всю историю.",
    "Текущая просадка": "Насколько портфель сейчас ниже своего максимума.",
    "Комиссия фондов": "Средневзвешенная годовая комиссия БПИФ, которыми можно повторить портфель.",
    "Шарп": "Доходность сверх безрисковой ставки на единицу риска, за последний год.",
    "Сортино": "Как Шарп, но учитывает только падения, за последний год.",
    "Омега": "Отношение суммы приростов к сумме потерь, за последний год.",
    "Кальмар": "Доходность в год, делённая на максимальную просадку, за последний год.",
    "Мартин": "Доходность сверх ставки, делённая на индекс язвы, за последний год.",
    "Индекс язвы": "Глубина и длительность просадок вместе, за последний год. Меньше — спокойнее.",
    "Рейтинг (5 лет)": "Место среди портфелей по соотношению риска и доходности за 5 лет (Шарп, Сортино, "
                       "Омега, Кальмар, Мартин): 100 — лучший. «—» — история короче 5 лет.",
}


# ------------------------------------------------------------------ вспомогательное (только отображение)
def g(obj: Any, *keys, default=None):
    """Первое найденное непустое значение по любому из ключей словаря."""
    if not isinstance(obj, dict):
        return default
    for k in keys:
        if obj.get(k) is not None:
            return obj[k]
    return default


def find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    low = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        if c.strip().lower() in low:
            return low[c.strip().lower()]
    return None


def with_slug(df: pd.DataFrame) -> pd.DataFrame:
    """Индекс (slug портфеля) — в колонку slug."""
    if "slug" in df.columns:
        return df.copy()
    out = df.reset_index()
    return out.rename(columns={out.columns[0]: "slug"})


def humanize(e: BaseException) -> str:
    text = str(e).strip().strip("'\"") or type(e).__name__
    return text if len(text) < 300 else text[:300] + "…"


def is_val(v) -> bool:
    return is_num(v)


def pct_cell(v, digits=1) -> str:
    return fmt_pct(float(v), digits) if is_val(v) else "—"


def ratio_cell(v) -> str:
    return fmt_num(v, 2) if is_val(v) else "—"


def color_sign(v) -> str:
    if not is_val(v) or float(v) == 0:
        return ""
    return "color: #1a7f37" if float(v) > 0 else "color: #c62828"


def portfolio_url(slug: str) -> str:
    """Полная копируемая ссылка на страницу портфеля (?p=slug)."""
    try:
        base = (st.context.url or "").split("?")[0]
    except Exception:  # noqa: BLE001
        base = ""
    return f"{base}?p={slug}"


def open_portfolio(slug: Optional[str]):
    if slug:
        st.query_params["p"] = slug
    else:
        st.query_params.pop("p", None)
    st.rerun()


def portfolios_dict() -> dict:
    return dict(getattr(pf, "PORTFOLIOS", {}) or {})


def short_name(key: str) -> str:
    """Короткое название актива из core («Акции (MCFTR)»); нет функции — ключ."""
    if key and pf is not None and hasattr(pf, "asset_short_name"):
        try:
            return pf.asset_short_name(key) or key
        except Exception:  # noqa: BLE001
            pass
    return asset_label(key) if key else ""


def asset_title(rep: dict, key: str) -> str:
    """Короткое название актива: из отчёта портфеля (assets_short), иначе asset_short_name."""
    return (g(rep, "assets_short") or {}).get(key) or short_name(key)


def bench_title(rep: dict) -> str:
    """Короткое название бенчмарка: benchmark_short из отчёта, иначе asset_short_name."""
    return g(rep, "benchmark_short") or short_name(g(rep, "benchmark") or "") or "Бенчмарк"


# ------------------------------------------------------------------ вызовы core (с кэшем)
def data_stamp() -> float:
    """Время изменения public/data/portfolios.json — часть ключа кэша: после ночного обновления файла
    кэш пересчитывается сразу, а не через 6 часов. Файла нет — 0 (живой расчёт)."""
    try:
        return os.path.getmtime(config.PUBLIC_DATA_DIR / "portfolios.json")
    except OSError:
        return 0.0


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _table(stamp: float) -> tuple[pd.DataFrame, dict, Optional[str]]:
    df = pf.portfolios_table()
    return df, dict(df.attrs.get("skipped") or {}), df.attrs.get("source")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _points(period: str, stamp: float) -> pd.DataFrame:
    return pf.risk_return_points(period)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _report(pid: str, stamp: float) -> dict:
    return pf.portfolio_report(pid)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _values(pid: str, stamp: float, start: Optional[str] = None, end: Optional[str] = None) -> pd.Series:
    return pf.portfolio_values(pid, start=start, end=end)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _bench_values(pid: str, stamp: float, start: Optional[str] = None, end: Optional[str] = None) -> pd.Series:
    """Бенчмарк из core (100 000 ₽ на тех же датах, что portfolio_values); attrs["aligned"]."""
    return pf.benchmark_values(pid, start=start, end=end)


# ------------------------------------------------------------------ режим страницы
if pf is None:
    page_setup("Портфели и стратегии", "🧺")
    st.info("Раздел готовится: модуль расчёта портфелей ещё не подключён. Загляните позже.")
    st.caption(f"Техническая причина: {esc(PF_ERROR)}")
    st.stop()

slug = (st.query_params.get("p") or "").strip()
if slug and slug not in portfolios_dict():
    st.query_params.pop("p", None)
    ss["pf_bad_slug"] = slug
    slug = ""


# ================================================================== ЛИСТИНГ
def build_table(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Колонки core → русские подписи (только переименование). Возвращает таблицу и типы колонок."""
    df = with_slug(raw)
    out = pd.DataFrame({"slug": df["slug"].astype(str)})
    kinds: dict[str, str] = {}
    used = {"slug"}
    for label, cands, kind in TABLE_SPEC:
        c = find_col(df, cands)
        if c is None:
            continue
        used.add(c)
        out[label] = df[c].values
        kinds[label] = kind
    # Служебные колонки core (stale, rank_window и т. п.) и колонки вне TABLE_SPEC не показываются.
    # Строки, перенесённые из прежнего файла (stale), помечаются датой в имени портфеля.
    out["_stale"] = (df["stale"].fillna(False).astype(bool).values if "stale" in df.columns
                     else np.zeros(len(df), dtype=bool))
    sd = find_col(df, ["stale_date", "last_date"])
    out["_stale_date"] = df[sd].values if sd else None
    pdict = portfolios_dict()
    if "Портфель" not in out.columns:
        out.insert(1, "Портфель", [g(pdict.get(s), "name", default=s) for s in out["slug"]])
    out["Портфель"] = [f"{n} (данные на {fmt_date(d)})" if st_ else n
                       for n, st_, d in zip(out["Портфель"], out["_stale"], out["_stale_date"])]
    if "Группа" not in out.columns:
        out["Группа"] = [g(pdict.get(s), "group", default="") for s in out["slug"]]
    kinds.setdefault("Портфель", "text")
    kinds.setdefault("Группа", "text")
    return out, kinds


# Пустые числа в таблице на экране. Streamlit 1.65 рисует NaN как серое «None» (ни na_rep Styler, ни
# NumberColumn не помогают), а ±inf с форматом «— если не конечно» показывает как «—». Поэтому пустые
# заменяются на +inf в колонках «меньше — лучше» и на −inf в остальных: при любой сортировке они не
# попадают в число лучших. Excel выгружается из исходной таблицы (там пустые ячейки), встроенная выгрузка
# CSV у этой таблицы скрыта (в ней были бы inf).
LOWER_BETTER = {"Комиссия фондов", "Индекс язвы", "Волатильность за год"}


def styled(df: pd.DataFrame, kinds: dict, fee_partial: Optional[list] = None):
    """Отображение листинга. fee_partial — по строкам: комиссия известна не для всей доли портфеля («*»)."""
    num = {"pct": lambda v: fmt_pct(float(v), 1), "pct2": lambda v: fmt_pct(float(v), 2),
           "ratio": lambda v: fmt_num(v, 2), "rank": lambda v: fmt_num(v)}
    df = df.copy()
    fmt = {}
    for c, k in kinds.items():
        if c not in df.columns:
            continue
        if k in num:
            fill = np.inf if c in LOWER_BETTER else -np.inf
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float).fillna(fill)
            fmt[c] = (lambda f: lambda v: f(v) if is_num(v) else "—")(num[k])
        elif k == "date":
            df[c] = df[c].astype(object).where(df[c].notna(), "—")
            fmt[c] = fmt_date
        else:
            df[c] = df[c].astype(object).where(df[c].notna(), "—")
    sty = df.style.format(fmt)
    if fee_partial is not None and "Комиссия фондов" in df.columns:
        rows = [i for i, flag in zip(df.index, fee_partial) if flag]
        if rows:
            sty = sty.format(lambda v: fmt_pct(float(v), 2) + "*" if is_num(v) else "—",
                             subset=pd.IndexSlice[rows, ["Комиссия фондов"]])
    sign_cols = [c for c in ("1 день", "С начала года", "В год за 10 лет", "В год за всё время") if c in df.columns]
    if sign_cols:
        sty = sty.map(color_sign, subset=sign_cols)
    return sty


def risk_return_chart(pts: pd.DataFrame, period_label: str) -> Optional[go.Figure]:
    df = with_slug(pts)
    rc = find_col(df, ["ret", "return", "cagr"])
    vc = find_col(df, ["volatility", "vol"])
    if rc is None or vc is None:
        return None
    pdict = portfolios_dict()
    nc, gc = find_col(df, ["name"]), find_col(df, ["group"])
    df["_name"] = df[nc].astype(str) if nc else [g(pdict.get(s), "name", default=s) for s in df["slug"]]
    df["_group"] = df[gc].astype(str) if gc else [g(pdict.get(s), "group", default="Портфели") for s in df["slug"]]
    df = df.dropna(subset=[rc, vc])
    if df.empty:
        return None
    colors = color_map(sorted(df["_group"].unique()))
    fig = go.Figure()
    for grp, part in df.groupby("_group", sort=True):
        fig.add_trace(go.Scatter(
            x=part[vc], y=part[rc], mode="markers+text", name=grp, text=part["_name"],
            textposition="top center", textfont=dict(size=11),
            customdata=np.stack([[pct_cell(v) for v in part[rc]], [pct_cell(v) for v in part[vc]]], axis=-1),
            marker=dict(size=12, color=colors[grp], line=dict(width=1.5, color="white")),
            hovertemplate="<b>%{text}</b><br>Доходность в год: %{customdata[0]}<br>"
                          "Волатильность в год: %{customdata[1]}<extra></extra>"))
    base_layout(fig, 540, f"Доходность в год, % (за {period_label})", pct_y=True)
    fig.update_layout(hovermode="closest", separators=", ")
    fig.update_xaxes(title=f"Волатильность в год, % (за {period_label})", tickformat=".0%")
    return fig


def render_listing():
    page_setup("Портфели и стратегии", "🧺")
    bad = ss.pop("pf_bad_slug", None)
    if bad:
        st.warning(f"Портфель «{esc(bad)}» не найден — показан список всех портфелей.")
    st.markdown(
        "Готовые портфели с постоянными долями активов: раз в период доли возвращаются к заданным. "
        "История построена по индексам полной доходности (с дивидендами и купонами), а повторить портфель "
        "можно через биржевые фонды (БПИФ). Результаты в прошлом не гарантируют доходность в будущем.")

    try:
        with st.spinner("Считаю показатели портфелей…"):
            raw, skipped, source = _table(data_stamp())
    except Exception as e:  # noqa: BLE001
        st.error(f"Не удалось загрузить таблицу портфелей: {esc(humanize(e))}")
        st.stop()
    if raw is None or len(raw) == 0:
        st.info("Нет данных ни по одному портфелю. Попробуйте позже.")
        st.stop()
    full, kinds = build_table(raw)

    c1, c2 = st.columns([2, 3])
    grp = c1.segmented_control("Группа", GROUPS, default="Все", format_func=GROUP_LABELS.get,
                               key="pf_group") or "Все"
    table = full if grp == "Все" else full[full["Группа"] == grp]
    all_cols = [c for c in full.columns if c not in ("slug", "Портфель") and not str(c).startswith("_")]
    if "pf_cols" not in ss:
        ss["pf_cols"] = [c for c in all_cols if c not in DEFAULT_HIDDEN]
    ss["pf_cols"] = [c for c in ss["pf_cols"] if c in all_cols]
    shown = c2.multiselect("Колонки таблицы", all_cols, key="pf_cols")

    pdict = portfolios_dict()
    in_table = set(full["slug"])   # перенесённые из прежнего файла есть в таблице — в «Нет данных» их не дублируем
    skipped_here = {s: r for s, r in (skipped or {}).items()
                    if s not in in_table and (grp == "Все" or g(pdict.get(s), "group") == grp)}
    if table.empty:
        st.info("В этой группе пока нет портфелей с данными."
                + (" В демо-режиме нет рядов отраслевых индексов." if is_offline() and skipped_here else ""))
    else:
        view = table[["Портфель"] + shown].reset_index(drop=True)
        view.insert(1, "Ссылка", ["?p=" + s for s in table["slug"]])
        cfg = {"Портфель": st.column_config.TextColumn("Портфель", pinned=True, width="medium"),
               "Ссылка": st.column_config.LinkColumn(
                   "Ссылка", display_text="открыть", width="small",
                   help="Ссылка на страницу портфеля — открывается в новой вкладке, её можно скопировать")}
        for c in shown:
            if c in COL_HELP:
                cfg[c] = st.column_config.Column(c, help=COL_HELP[c])
        st.caption(f"Портфелей: {fmt_num(len(view))}. Нажмите на заголовок колонки, чтобы отсортировать; "
                   "отметьте строку слева, чтобы открыть портфель. Коэффициенты — за последний год, "
                   "рейтинг — за 5 лет («—» — история короче 5 лет), просадки — за всю историю.")
        cov_col = "Комиссия известна для доли"
        # частичное покрытие — 0 < доля < 1; без фондов вовсе (0) комиссия и так «—»
        fee_partial = ([is_num(v) and 0 < float(v) < 0.999 for v in table[cov_col]] if cov_col in table.columns
                       else None)
        # встроенная выгрузка CSV скрыта: в ней были бы технические ±inf вместо пустых ячеек
        st.markdown('<style>.st-key-pf_table_box [data-testid="stElementToolbarButton"]'
                    ':has(button[aria-label^="Download as CSV"]){display:none}</style>', unsafe_allow_html=True)
        with st.container(key="pf_table_box"):
            ev = st.dataframe(styled(view, kinds, fee_partial), hide_index=True, width="stretch",
                              column_config=cfg, on_select="rerun", selection_mode="single-row", key="pf_table",
                              height=min(35 * (len(view) + 1) + 3, 760))
        partial = ([f"{n} — {fmt_pct(float(v), 1)}" for n, v in zip(table["Портфель"], table[cov_col])
                    if is_num(v) and 0 < float(v) < 0.999] if cov_col in table.columns else [])
        if partial and "Комиссия фондов" in shown:
            st.caption("\\* Комиссия посчитана не по всему портфелю — для части активов нет подходящего фонда или "
                       "данных о его комиссии; доля портфеля, для которой комиссия известна: " + "; ".join(
                           esc(x) for x in partial) + ".")
        rows = list(getattr(getattr(ev, "selection", None), "rows", []) or [])
        if rows:
            open_portfolio(table["slug"].iloc[rows[0]])

        a, b, c = st.columns([3, 1, 2], vertical_alignment="bottom")
        names = dict(zip(table["slug"], table["Портфель"]))
        pick = a.selectbox("Открыть портфель", list(names), format_func=names.get, index=None,
                           placeholder="Выберите портфель…", key="pf_pick")
        if b.button("Открыть", type="primary", disabled=pick is None, width="stretch"):
            open_portfolio(pick)
        xl = table.drop(columns=["slug", "_stale", "_stale_date"], errors="ignore").set_index("Портфель")
        c.download_button("Скачать таблицу (Excel)", to_excel({"Портфели": xl}), "portfolios.xlsx",
                          width="stretch")
        last = pd.to_datetime(full["Данные на"], errors="coerce").max() if "Данные на" in full else pd.NaT
        rf_note = (rf_caption(last - pd.DateOffset(years=1), last) if pd.notna(last) else rf_caption())
        notes = ["В Excel доли выгружены числами (0,05 = 5 %).", rf_note.replace(" за период", " за последний год")]
        if is_offline() and source == "live":
            notes.append("Демо-режим: месячные ряды до 2025 г., поэтому «1 день» — изменение за последний месяц ряда.")
        st.caption(" ".join(notes))
    if skipped_here:
        with st.expander(f"Нет данных: {fmt_num(len(skipped_here))} портф."):
            for s, reason in skipped_here.items():
                st.markdown(f"- **{esc(g(pdict.get(s), 'name', default=s))}** — {esc(reason)}")

    st.subheader("Риск и доходность")
    per = st.segmented_control("Период", list(RR_PERIODS), default="10Y", format_func=RR_PERIODS.get,
                               key="pf_rr_per") or "10Y"
    try:
        pts = _points(per, data_stamp())
    except Exception as e:  # noqa: BLE001
        st.warning(f"Не удалось построить диаграмму: {esc(humanize(e))}")
        return
    if pts is not None and len(pts) and grp != "Все":
        pts = with_slug(pts)
        pts = pts[pts["slug"].isin(set(table["slug"]))]
    fig = risk_return_chart(pts, RR_PERIODS[per]) if pts is not None and len(pts) else None
    if fig is None:
        st.info("Для этого периода не хватает истории.")
        return
    st.caption("Чем выше точка — тем больше доходность; чем правее — тем сильнее колебалась стоимость. "
               f"Портфели с историей короче {RR_SHORTER[per]} не показаны.")
    st.plotly_chart(fig, width="stretch")


# ================================================================== СТРАНИЦА ПОРТФЕЛЯ
def block_composition(rep: dict):
    st.subheader("Состав")
    tw = g(rep, "weights") or {}
    cw = g(rep, "current_weights") or {}
    assets = list(tw) + [a for a in cw if a not in tw]
    if not assets:
        st.info("Состав портфеля не задан.")
        return
    wf = pd.DataFrame({"Целевая доля": [tw.get(a, np.nan) for a in assets],
                       "Текущая доля": [cw.get(a, np.nan) for a in assets]}, index=assets)
    series = ["Целевая доля", "Текущая доля"] if cw else ["Целевая доля"]
    st.caption(f"Целевые доли задают правила портфеля; текущие — доли на {fmt_date(g(rep, 'last_date'))} "
               "с учётом роста и падения активов после последней ребалансировки.")
    labels = [asset_title(rep, a) for a in assets]
    colors = color_map(series)
    fig = go.Figure()
    for s in series:
        fig.add_trace(go.Bar(y=labels, x=[float(v) for v in wf[s]], name=s, orientation="h", marker=dict(color=colors[s]),
                             text=[pct_cell(v) for v in wf[s]], textposition="outside", cliponaxis=False,
                             hovertemplate=f"%{{y}}: %{{text}}<extra>{s}</extra>"))
    base_layout(fig, 90 + 32 * len(assets) * len(series))
    fig.update_layout(barmode="group", hovermode="closest", separators=", ")
    top = float(np.nanmax(wf[series].to_numpy(dtype=float)))
    fig.update_xaxes(title="Доля в портфеле, %", tickformat=".0%", range=[0, top * 1.2])
    fig.update_yaxes(type="category", autorange="reversed", automargin=True, tickformat=None)
    st.plotly_chart(fig, width="stretch")


def block_growth(rep: dict, pid: str, name: str):
    st.subheader("Рост 100 000 ₽")
    a, b = st.columns([4, 1], vertical_alignment="bottom")
    per = a.segmented_control("Период", list(CHART_PERIODS), default="ALL", format_func=CHART_PERIODS.get,
                              key="pf_chart_per") or "ALL"
    log_y = b.toggle("Лог. шкала", False, key="pf_log",
                     help="На логарифмической шкале одинаковый рост в процентах выглядит одинаково "
                          "в любые годы — удобно для длинной истории.")
    try:
        full = _values(pid, data_stamp())
    except Exception as e:  # noqa: BLE001
        st.warning(f"Не удалось построить график: {esc(humanize(e))}")
        return
    if full is None or len(full) < 2:
        st.info("Недостаточно истории для графика.")
        return
    start = m.period_start(full.index, per) if per != "ALL" else None
    if per != "ALL" and start is None:
        st.info(f"История портфеля короче периода «{CHART_PERIODS[per]}» — показана вся история.")
    try:
        vals = _values(pid, data_stamp(), start=start) if start else full
    except Exception as e:  # noqa: BLE001
        st.warning(f"Не удалось построить график за период: {esc(humanize(e))}")
        return
    if vals is None or len(vals) < 2:
        st.info("Недостаточно истории для выбранного периода.")
        return
    df = pd.DataFrame({name: vals})
    bench = g(rep, "benchmark")
    blabel, bser, aligned = None, None, True
    if bench and hasattr(pf, "benchmark_values"):
        try:
            bser = _bench_values(pid, data_stamp(), start=start)
            aligned = bool(bser.attrs.get("aligned", True)) if bser is not None else True
            bser = bser.dropna() if bser is not None and len(bser.dropna()) >= 2 else None
        except Exception:  # noqa: BLE001
            bser = None
    if bser is not None:
        blabel = f"{bench_title(rep)} — бенчмарк"
        df = df.join(bser.rename(blabel), how="left")
    text = (f"Стоимость 100 000 ₽, вложенных {fmt_date(vals.index[0])}, на {fmt_date(vals.index[-1])}: "
            f"портфель — **{fmt_rub(float(vals.iloc[-1]))}**")
    if blabel and aligned:
        text += f", бенчмарк — **{fmt_rub(float(bser.iloc[-1]))}** (серый пунктир)"
    st.markdown(text + ".")
    if blabel and not aligned:
        st.caption(f"Данные бенчмарка {esc(bench_title(rep))} есть не за весь период: сравнение — с "
                   f"{fmt_date(bser.index[0])} по {fmt_date(bser.index[-1])} (серый пунктир), итоговая сумма "
                   "по бенчмарку не приводится.")
    elif bench and blabel is None:
        st.caption(f"Бенчмарк {esc(bench_title(rep))} сейчас недоступен — показан только портфель.")
    fig = line_chart(df, blabel, "Стоимость, ₽", log_y=log_y, height=440)
    fig.update_layout(separators=", ")   # пробел между разрядами, запятая в дробях
    fig.update_yaxes(tickformat=",.0f", ticksuffix=" ₽")
    st.plotly_chart(fig, width="stretch")
    st.caption("С учётом дивидендов и купонов (индексы полной доходности), без налогов и комиссий фондов.")


def block_returns(rep: dict, name: str):
    st.subheader("Доходность по периодам")
    r = g(rep, "returns") or {}
    mine, bm = g(r, "portfolio") or {}, g(r, "benchmark")
    bench = g(rep, "benchmark") or "Бенчмарк"
    if not mine:
        st.info("Нет данных о доходности.")
        return

    def val(src, k):
        x = (src or {}).get(k)
        if not isinstance(x, dict):
            return None
        if k in ("3Y", "5Y", "10Y", "ALL") and is_num(x.get("cagr")):
            return x["cagr"]
        return x.get("ret")

    bcol = f"{bench_title(rep)} — бенчмарк"
    rows, raw = [], []
    for k, label in RETURN_PERIODS:
        pv, bv = val(mine, k), val(bm, k) if bm else None
        row = {"Период": label, name: fmt_signed_pct(pv) if is_num(pv) else "—"}
        rr = {"Период": None, name: pv}
        if bm:
            row[bcol] = fmt_signed_pct(bv) if is_num(bv) else "—"
            rr[bcol] = bv
        rows.append(row)
        raw.append(rr)
    df, rdf = pd.DataFrame(rows), pd.DataFrame(raw)
    sty = df.style.apply(lambda col: [color_sign(v) for v in rdf[col.name]] if col.name != "Период"
                         else [""] * len(col), axis=0)
    st.dataframe(sty, hide_index=True, width="stretch")
    note = ("* — среднегодовая доходность (CAGR) для периодов от 3 лет и за всё время; для коротких периодов — "
            "изменение стоимости за период.")
    if g(rep, "freq") == "M":
        note += " Ряд месячный, поэтому «1 день» и «1 неделя» — изменение за последний месяц."
    st.caption(note)


def block_monthly(rep: dict):
    st.subheader("Доходность по месяцам")
    gr = g(rep, "monthly") or {}
    years = gr.get("years") or []
    if not years:
        st.info("Недостаточно истории.")
        return
    st.caption("Строки — годы (свежие сверху), столбцы — месяцы; справа — итог года, внизу — медиана по месяцу.")
    st.plotly_chart(monthly_heatmap(years, gr.get("cells") or [], g(gr, "yearTotal", "year_total") or [],
                                    g(gr, "medianByMonth", "median_by_month") or []), width="stretch")


def fmt_metric_any(key: str, v) -> str:
    if not is_num(v):
        return "—"
    if key in PCT_KEYS:
        return fmt_pct(float(v), 2 if key == "ulcer_index" else 1)
    return fmt_num(v, 2)


def block_metrics(rep: dict):
    st.subheader("Коэффициенты эффективности")
    mt = g(rep, "metrics") or {}
    bmt = g(rep, "benchmark_metrics") or {}
    windows = [(k, lab) for k, lab in METRIC_WINDOWS if isinstance(mt.get(k), dict)]
    if not windows:
        st.info("Недостаточно истории для расчёта коэффициентов.")
        return
    bench = bench_title(rep) if g(rep, "benchmark") else "бенчмарк"
    with_b = st.toggle(f"Сравнить с бенчмарком: {bench}", False, key="pf_mt_bench") if bmt else False
    weights = {k: float(v) for k, v in (g(rep, "weights") or {}).items() if is_num(v) and float(v) > 0}
    same = bool(g(rep, "benchmark")) and set(weights) == {g(rep, "benchmark")}
    rows_spec = [(key, lab) for key, lab in METRIC_ROWS if not (same and key in REL_KEYS)]
    data = {"Показатель": [lab for _, lab in rows_spec]}
    for k, lab in windows:
        data[lab] = [fmt_metric_any(key, mt[k].get(key)) for key, _ in rows_spec]
        if with_b:
            bw = bmt.get(k) or {}
            data[f"{lab} · {bench}"] = ["" if key in REL_KEYS else fmt_metric_any(key, bw.get(key))
                                        for key, _ in rows_spec]
    df = pd.DataFrame(data)
    df = df[df.iloc[:, 1:].apply(lambda r: any(v not in ("—", "") for v in r), axis=1)]
    st.dataframe(df, hide_index=True, width="stretch", height=38 * (len(df) + 1))
    spans = "; ".join(f"{lab} — {fmt_date(mt[k].get('from'))} — {fmt_date(mt[k].get('to'))}" for k, lab in windows)
    freq = {"D": "дневным", "M": "месячным"}.get(g(mt[windows[0][0]], "freq"), "")
    for w in sorted({mt[k].get("rf_warning") for k, _ in windows if mt[k].get("rf_warning")}):
        st.warning(w)
    rates = "; ".join(f"{lab} — {fmt_pct(float(mt[k]['rf']), 2)}" for k, lab in windows if is_num(mt[k].get("rf")))
    rf_text = (f"Безрисковая ставка — доходность коротких ОФЗ, в среднем за окно: {rates}." if rates
               else rf_caption(g(rep, "since"), g(rep, "last_date")))
    rel = (f"Портфель целиком состоит из бенчмарка ({esc(bench)}), поэтому показатели относительно бенчмарка "
           "(альфа, бета, R², корреляция, захваты) не показаны." if same
           else f"Альфа, бета, R² и захваты — относительно бенчмарка «{esc(bench)}».")
    st.caption(f"Окна: {spans}. Расчёт по {freq} доходностям. {rf_text} {rel} Колонки окон, для которых "
               "не хватает истории, не показаны. Формулы — в разделе «Методика».")


def block_drawdowns(rep: dict):
    st.subheader("Просадки")
    dd = g(rep, "drawdowns") or {}
    top = dd.get("top") if isinstance(dd, dict) else dd
    cur = dd.get("current") if isinstance(dd, dict) else None
    if isinstance(dd, dict):
        c = st.columns(3)
        c[0].metric("Максимальная", pct_cell(dd.get("max")))
        days = g(cur, "days")
        c[1].metric("Текущая", pct_cell(g(cur, "depth")),
                    help=(f"{fmt_num(days)} дн. от максимума {fmt_date(g(cur, 'peak'))}" if is_num(days) and days
                          else "Портфель на максимуме"))
        c[2].metric("Индекс язвы (вся история)", pct_cell(dd.get("ulcer_index"), 2))
    st.markdown("**Пять крупнейших просадок**")
    if not top:
        st.caption("Просадок нет.")
        return
    st.dataframe(pd.DataFrame([{
        "Глубина": pct_cell(g(d, "depth")),
        "Начало (максимум)": fmt_date(g(d, "peak")),
        "Дно": fmt_date(g(d, "trough")),
        "Восстановление": fmt_date(g(d, "recovery")) if g(d, "recovery") else "не восстановлена",
        "Дней до дна": fmt_num(g(d, "daysToTrough", "days_to_trough")),
        "Дней до восстановления": fmt_num(g(d, "daysToRecover", "days_to_recover")),
    } for d in top]), hide_index=True, width="stretch")
    st.caption("Просадка — падение стоимости от предыдущего максимума до дна. "
               "Восстановление — дата, когда стоимость вернулась к прежнему максимуму.")


def block_rebalance_log(rep: dict):
    log = g(rep, "rebalance_log") or []
    with st.expander(f"Журнал ребалансировок ({fmt_num(len(log))})", expanded=False):
        if not log:
            st.caption("Ребалансировок не было: доли не возвращаются к целевым.")
            return
        assets = list(g(rep, "weights") or {})
        rows = []
        for e in reversed(log):   # свежие сверху
            row = {"Дата": fmt_date(g(e, "date"))}
            before, after = g(e, "before") or {}, g(e, "after") or {}
            for a in assets or list(after):
                row[asset_title(rep, a)] = f"{pct_cell(before.get(a))} → {pct_cell(after.get(a))}"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("В дату ребалансировки доли возвращаются к целевым: продаётся то, что выросло, "
                   "докупается то, что отстало. В ячейке — доля до и после. Показаны последние 50.")


def fund_rows(funds: list) -> list[dict]:
    out = []
    for i, f in enumerate(funds):
        f = {"secid": f} if isinstance(f, str) else (f or {})
        out.append({"Фонд": g(f, "secid", default="—") + (" (основной)" if i == 0 and len(funds) > 1 else ""),
                    "Название": g(f, "name", default=""), "УК": g(f, "issuer", default=""),
                    "Комиссия в год": pct_cell(g(f, "fee"), 2), "СЧА": fmt_big_rub(g(f, "aum_rub")),
                    "Торги": g(f, "trade_status", default="")})
    return out


def block_replicate(rep: dict):
    st.subheader("Как повторить портфель")
    repl = g(rep, "replicate") or []
    if not repl:
        st.info("Подходящие биржевые фонды для этого портфеля пока не подобраны.")
        return
    weights = g(rep, "weights") or {}
    st.caption("Для каждого актива — биржевые фонды (БПИФ), которые следуют за тем же или близким индексом. "
               "Комиссию портфеля считаем по первому (основному) фонду каждого актива.")
    rows = []
    for item in repl:
        asset = g(item, "asset")
        title = g(item, "asset_short") or asset_title(rep, asset)
        share = pct_cell(g(item, "weight", default=weights.get(asset)))
        frs = fund_rows(g(item, "funds") or [])
        if not frs:
            frs = [{"Фонд": "нет фонда", "Название": "", "УК": "", "Комиссия в год": "—", "СЧА": "—", "Торги": ""}]
        for i, fr in enumerate(frs):
            rows.append({"Актив": title if i == 0 else "", "Доля": share if i == 0 else "", **fr,
                         "Пояснение": (g(item, "note") or "") if i == 0 else ""})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Пояснение": st.column_config.TextColumn(width="large"),
                                "Название": st.column_config.TextColumn(width="medium")})
    fee, cov = g(rep, "fee"), g(rep, "fee_coverage")
    a, b = st.columns([1, 3])
    a.metric("Средневзвешенная комиссия", f"{fmt_pct(float(fee), 2)} в год" if is_num(fee) else "—",
             help="Годовые комиссии основных фондов, взвешенные по целевым долям активов.")
    if is_num(cov) and float(cov) < 0.999:
        b.caption(f"Комиссия известна для {fmt_pct(float(cov), 0)} портфеля; для остальной части фонда нет "
                  "или нет данных о комиссии — средняя посчитана по известной части.")
    one = g(rep, "one_fund")
    if isinstance(one, dict) and (one.get("funds") or one.get("note")):
        st.markdown("**Одним фондом**")
        if one.get("note"):
            st.caption(esc(one["note"]))
        if one.get("funds"):
            st.dataframe(pd.DataFrame(fund_rows(one["funds"])), hide_index=True, width="stretch")
    st.warning("Фонды повторяют индексы неточно: комиссия управляющей компании, налог на дивиденды внутри "
               "фонда и ошибка слежения уменьшают результат по сравнению с историей индексов. Это не "
               "инвестиционная рекомендация.")
    st.caption("Комиссии, УК и СЧА фондов: [RusETFs (rusetfs.com)](https://rusetfs.com/), резервно — T-Invest API.")


def block_to_backtest(rep: dict, name: str):
    weights = {str(k): float(v) for k, v in (g(rep, "weights") or {}).items() if is_num(v) and float(v) > 0}
    if not weights:
        return
    with st.container(border=True):
        a, b = st.columns([3, 1], vertical_alignment="center")
        a.markdown("#### Проверить портфель в бэктесте")
        a.caption("Состав попадёт в собственные портфели на странице «Бэктест»: там можно задать свой период, "
                  "сумму, пополнения, комиссии и налоги и сравнить с другими стратегиями.")
        if b.button("Открыть в бэктесте", type="primary", width="stretch", key="pf_to_bt"):
            label = name if name not in STRATEGIES else f"{name} (из раздела «Портфели»)"
            ss.setdefault("custom_portfolios", {})[label] = weights
            ss["bt_prefill"] = {"name": label, "weights": weights}
            ss["bt_prefill_msg"] = (f"Портфель «{esc(label)}» добавлен в собственные портфели. Выберите его "
                                    "в списке «Стратегии и портфели» и нажмите «Запустить бэктест».")
            try:
                st.switch_page(BACKTEST_PAGE)
            except Exception:  # noqa: BLE001 — одиночный запуск файла без навигации
                st.success(ss["bt_prefill_msg"] + " Откройте страницу «Бэктест».")


def render_portfolio(pid: str):
    meta = portfolios_dict().get(pid, {})
    name = g(meta, "name", default=pid)
    page_setup(name, "🧺")
    if st.button("Все портфели", icon=":material/arrow_back:", type="tertiary", key="pf_back"):
        open_portfolio(None)
    try:
        with st.spinner("Считаю показатели портфеля…"):
            rep = _report(pid, data_stamp()) or {}
    except Exception as e:  # noqa: BLE001
        unavailable = type(e).__name__ == "PortfolioUnavailable"
        if unavailable:
            msg = f"Для портфеля «{esc(name)}» сейчас нет данных по одному из индексов."
            if is_offline():
                msg += " В демо-режиме есть не все индексы — откройте раздел с подключением к бирже."
            st.info(msg)
        else:
            st.error(f"Не удалось рассчитать портфель «{esc(name)}». Попробуйте позже.")
        st.caption(f"Подробности: {esc(humanize(e))}")
        st.stop()
    name = g(rep, "name", default=name)

    # хлебные крошки
    group = g(rep, "group") or g(meta, "group") or ""
    st.caption(f"Портфели / {esc(group)} / {esc(name)}" if group else f"Портфели / {esc(name)}")
    if g(rep, "stale"):
        st.warning(f"Данные на {fmt_date(g(rep, 'stale_date') or g(rep, 'last_date'))}: последнее обновление "
                   "не удалось, показаны прежние данные.")
    for w in g(rep, "warnings") or []:
        st.warning(esc(w))

    desc = g(rep, "description") or g(meta, "description")
    if desc:
        st.markdown(esc(desc))
    facts = []
    if g(rep, "since"):
        facts.append(f"история с {fmt_date(g(rep, 'since'))}")
    if g(rep, "last_date"):
        facts.append(f"данные на {fmt_date(g(rep, 'last_date'))}")
    rb = g(rep, "rebalance", default=g(meta, "rebalance"))
    if rb is not None:
        facts.append("без ребалансировки" if rb == "none"
                     else f"ребалансировка {g(rep, 'rebalance_label') or REBAL_RU.get(rb, rb)}")
    bench = g(rep, "benchmark") or g(meta, "benchmark")
    if bench:
        facts.append(f"бенчмарк — {esc(bench_title(rep))}")
    with st.container(border=True):
        rules = g(rep, "rules") or g(meta, "rules")
        if rules:
            st.markdown(f"**Правила.** {esc(rules)}")
        if facts:
            st.caption((" · ".join(facts))[:1].upper() + (" · ".join(facts))[1:] + ".")
        st.caption("Ссылка на эту страницу (можно скопировать):")
        st.code(portfolio_url(pid), language=None)

    left, right = st.columns([2, 3])
    with left:
        block_composition(rep)
    with right:
        block_growth(rep, pid, name)
    block_returns(rep, name)
    block_monthly(rep)
    block_metrics(rep)
    block_drawdowns(rep)
    block_rebalance_log(rep)
    block_replicate(rep)
    block_to_backtest(rep, name)
    st.caption("Результаты в прошлом не гарантируют доходность в будущем.")


if slug:
    render_portfolio(slug)
else:
    render_listing()
