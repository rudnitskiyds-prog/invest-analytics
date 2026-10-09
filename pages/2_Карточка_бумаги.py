"""
Карточка бумаги — те же блоки и в том же порядке, что страница бумаги веб-версии
(web/js/page-symbol.js, docs/STRUCTURE.md, п. 2).

Интерфейс ничего не считает сам: данные и показатели — core.analytics.symbol_page
(load_symbol, report), рейтинг — core.data.public_data.symbol_stats; здесь только выбор,
срезы по датам и вывод. Каждый блок рисуется независимо: ошибка одного не ломает страницу.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import pandas as pd
import streamlit as st

from core.analytics import metrics as m
from ui.common import (MONTHS_RU, asset_label, bar_chart, catalog_options, esc, fmt_big_rub, fmt_date, fmt_metric,
                       fmt_num,
                       fmt_pct, fmt_signed_pct, is_num, is_offline, line_chart, load_series, monthly_heatmap,
                       page_setup, position_scale, show_metrics_table)


def _papers(n) -> str:
    """Склонение «бумага»: 1 бумага, 2 бумаги, 5 бумаг."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "бумага"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "бумаги"
    return "бумаг"


page_setup("Карточка бумаги", "🔎")
ss = st.session_state
rf = ss["rf"]
bench = ss["benchmark"]   # демо-режим — из боковой панели; на живых данных — выбор на странице (ниже)
OFFLINE = is_offline()
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,31}$")

# варианты бенчмарка на странице (по умолчанию — по классу бумаги: info["benchmark"])
BENCH_OPTIONS = ["MCFTR", "IMOEX", "MEBCTR", "RGBITR", "RUCBTRNS", "GOLD_CBR", "RUONIA"]
CHART_PERIODS = {"1M": "1М", "6M": "6М", "YTD": "YTD", "1Y": "1Г", "5Y": "5Л", "10Y": "10Л", "ALL": "Всё"}
RETURN_PERIODS = [("1D", "1 день"), ("1W", "1 неделя"), ("1M", "1 месяц"), ("6M", "6 месяцев"),
                  ("YTD", "С начала года"), ("1Y", "1 год"), ("3Y", "3 года"), ("5Y", "5 лет"),
                  ("10Y", "10 лет"), ("ALL", "Всё время")]
RATING_KEYS = [("sharpe", "Шарп"), ("sortino", "Сортино"), ("omega", "Омега"), ("calmar", "Кальмар"),
               ("martin", "Мартин")]
RISK_LABELS = {"cagr": "CAGR (среднегод.)", "volatility": "Волатильность (год.)", "max_drawdown": "Макс. просадка",
               "sharpe": "Коэф. Шарпа", "sortino": "Коэф. Сортино", "omega": "Коэф. Омега",
               "calmar": "Коэф. Кальмара", "martin": "Коэф. Мартина"}
REL_LABELS = {"alpha": "Альфа Дженсена (год.)", "beta": "Бета", "r_squared": "R²",
              "up_capture": "Захват роста", "down_capture": "Захват падения", "correlation": "Корреляция",
              "tracking_error": "Ошибка слежения", "information_ratio": "Информационный коэф."}
CLASS_LABELS = {"share": "Акция", "fund": "Фонд (пай)", "bond": "Облигация", "index": "Индекс",
                "metal": "Драгоценный металл", "currency": "Валюта"}


class NoData(Exception):
    """«Данных нет» — показывается спокойным текстом, а не как сбой."""


# ------------------------------------------------------------------ утилиты вывода
def g(obj: Any, *keys, default=None):
    """Первое непустое поле из dict / объекта (поддержка snake_case и camelCase)."""
    if obj is None:
        return default
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            return v
    return default


def humanize(e: BaseException) -> str:
    s = str(e) or e.__class__.__name__
    low = s.lower()
    if any(x in low for x in ("connection", "timed out", "timeout", "name resolution", "max retries", "недоступен")):
        return "нет связи с источником (сеть недоступна или источник не отвечает)"
    if "404" in s:
        return "источник не нашёл данные (404)"
    if re.search(r"\b5\d\d\b", s):
        return "источник временно недоступен"
    return s


def price_unit(cls: Optional[str], currency: Optional[str]) -> str:
    if cls == "bond":
        return "% от номинала"
    if cls == "index":
        return "пунктов"
    if not currency or str(currency).upper() in ("RUB", "SUR"):
        return "₽"
    return str(currency)


def fmt_price(v, unit: str) -> str:
    if not is_num(v):
        return "—"
    a = abs(float(v))
    s = fmt_num(v, 2 if a >= 1 or a == 0 else 4 if a >= 0.01 else 6)
    if unit == "₽":
        return f"{s} ₽"
    if unit == "пунктов":
        return s
    if unit.startswith("%"):
        return f"{s} %"
    return f"{s} {unit}"


def years_word(n: int) -> str:
    a, b = abs(n) % 100, abs(n) % 10
    if 10 < a < 20:
        return "лет"
    return "год" if b == 1 else "года" if 2 <= b <= 4 else "лет"


def card(title: str, fn, *args, **kwargs):
    """Блок страницы в рамке; NoData — «Нет данных», прочие ошибки — понятное предупреждение."""
    with st.container(border=True):
        st.markdown(f"#### {title}")
        try:
            fn(*args, **kwargs)
        except NoData as e:
            st.caption(f"Нет данных. {e}".strip())   # тексты NoData экранируются при создании
        except Exception as e:  # noqa: BLE001
            st.warning(f"Блок недоступен: {esc(humanize(e))}")


def color_sign(v) -> str:
    if not is_num(v) or float(v) == 0:
        return ""
    return "color: #1baf7a" if float(v) > 0 else "color: #e34948"


# ------------------------------------------------------------------ загрузка (кэш)
@st.cache_data(ttl=24 * 3600, show_spinner=False, max_entries=32)
def _search(q: str) -> pd.DataFrame:
    from core.data import iss
    return iss.search(q)


@st.cache_data(ttl=3600, show_spinner=False)
def _stats() -> Optional[dict]:
    from core.data import public_data
    return public_data.symbol_stats()


@st.cache_data(ttl=15 * 60, show_spinner=False, max_entries=32)
def _load(secid: str) -> dict:
    from core.analytics import symbol_page
    return symbol_page.load_symbol(secid)


@st.cache_data(ttl=5 * 60, show_spinner=False, max_entries=32)
def _snapshot(secid: str) -> Optional[dict]:
    """Котировки устаревают быстрее истории: отдельный кэш на 5 минут (свечи — из _load)."""
    from core.analytics import symbol_page
    data = _load(secid)
    if not data.get("info"):
        return data.get("snapshot")
    return symbol_page.snapshot(secid, data.get("ohlc"))


@st.cache_data(ttl=15 * 60, show_spinner=False, max_entries=32)
def _prices(key: str, start: Optional[str]) -> pd.Series:
    """Живой ряд цен бенчмарка / бумаги сравнения (без демо-подмены); сбой — исключение."""
    from core.analytics import symbol_page
    return symbol_page.load_benchmark(key, start)


@st.cache_data(ttl=15 * 60, show_spinner=False, max_entries=32)
def _report(secid: str, bench_key: Optional[str], rf: float) -> tuple[dict, Optional[str]]:
    """(отчёт, причина, по которой бенчмарка нет | None). Бенчмарк — только symbol_page.load_benchmark."""
    from core.analytics import symbol_page
    data = _load(secid)
    ohlc = data.get("ohlc")
    if ohlc is None or len(ohlc) < 3:
        raise NoData(f"ISS не вернул истории торгов {secid}.")
    bser, berr = None, None
    if bench_key and bench_key != secid:
        try:
            bser = _prices(bench_key, str(pd.Timestamp(ohlc.index[0]).date()))
        except Exception as e:  # noqa: BLE001
            bser, berr = None, humanize(e)
    rep = symbol_page.report(data, bser, rf)
    berr = berr or (rep.get("errors") or {}).get("benchmark")
    if bench_key and bench_key != secid and rep.get("benchmark") is None and not berr:
        berr = "нет общих дат с бумагой"
    return rep, berr


def _search_box(query: str, container, label: str = "Найдено") -> Optional[str]:
    """Поиск ISS по названию: выбранный тикер или None (сообщение уже выведено)."""
    if OFFLINE:
        if query.isascii():   # латиница со знаками / пробелами — скорее опечатка в тикере
            st.error(f"«{esc(query)}» не похоже на тикер Мосбиржи, а поиск по названию работает только "
                     "с живыми данными ISS. Введите тикер, например SBER или MCFTR.")
        else:
            st.warning("Поиск по названию работает только с живыми данными ISS. Введите тикер латиницей.")
        return None
    try:
        res = _search(query)
    except Exception as e:  # noqa: BLE001
        st.error(f"Поиск ISS недоступен: {esc(humanize(e))}. Введите тикер латиницей.")
        return None
    if res is None or res.empty:
        st.warning(f"По запросу «{esc(query)}» ничего не найдено.")
        return None
    names = res.drop_duplicates("secid").set_index("secid")
    return container.selectbox(
        label, list(names.index),
        format_func=lambda x: f"{x} — {names.loc[x].get('shortname') or names.loc[x].get('name') or ''}")


# ------------------------------------------------------------------ выбор бумаги
qp = (st.query_params.get("s") or "").strip().upper()
if "sym_q" not in ss:   # ключ виджета очищается при уходе со страницы — восстанавливаем
    ss["sym_q"] = qp or ss.get("sym_secid") or ("MCFTR" if OFFLINE else "SBER")
elif qp and qp != ss.get("sym_secid"):   # пришли по ссылке ?s=… с другой страницы
    ss["sym_q"] = qp
if "sym_cmp_in" not in ss:
    ss["sym_cmp_in"] = ss.get("sym_cmp", "")
c1, c2, c3 = st.columns([3, 2, 2])
q = c1.text_input("Тикер или название", key="sym_q",
                  help="Тикер Мосбиржи (SBER, EQMX, SU26238RMFS4, IMOEX) или часть названия.")
query = q.strip()
secid = query.upper()
if not secid:
    st.info("Введите тикер бумаги.")
    st.stop()
# не тикер (кириллица, пробелы, знаки) — поиск по названию
if not query.isascii() or not TICKER_RE.match(secid):
    secid = _search_box(query, c1)
    if not secid:
        st.stop()
ss["sym_secid"] = secid
if st.query_params.get("s") != secid:
    st.query_params["s"] = secid

cmp = c2.text_input("Сравнить с бумагой", key="sym_cmp_in", placeholder="Тикер, например GAZP").strip().upper()
ss["sym_cmp"] = cmp
if cmp and (cmp == secid or not TICKER_RE.match(cmp)):
    if cmp != secid:
        st.warning(f"«{esc(cmp)}» не похоже на тикер — сравнение не показано.")
    cmp = ""


# ------------------------------------------------------------------ рейтинг (нужен и в демо-режиме)
def block_rating(sec: str, rating: Optional[dict] = None, err: Optional[str] = None):
    try:
        stats = _stats()
    except Exception:  # noqa: BLE001 — функции ещё нет или файл не читается
        stats = None
    it = rating or ((stats or {}).get("items") or {}).get(sec)
    if not it:
        if not stats:
            raise NoData(esc(err) if err else "Рейтинг появится после ночного пересчёта (public/data/symbol_stats.json).")
        st.caption(f"{esc(sec)} нет в рейтинге: в него входят акции и фонды основного режима торгов, "
                   "торгуемые сейчас, с историей не менее 36 месяцев.")
        return
    cls = it.get("class")
    n = (((stats or {}).get("classes") or {}).get(cls) or {}).get("n") or it.get("n_class")
    peers = "фондов" if cls == "fund" else "акций"
    basis = "с реинвестированием дивидендов (T-Invest)" if it.get("tr") else "по цене"
    upd = fmt_date((stats or {}).get("updated") or it.get("updated"))
    st.caption(f"Перцентиль 0–100 среди {peers}{f' ({fmt_num(n)} {_papers(n)})' if is_num(n) else ''}, торгуемых сейчас; "
               f"100 — лучший показатель класса. Рейтинг ночной, по месячным доходностям {basis}; "
               "Rf — средняя ключевая ставка ЦБ за окно, поэтому значения могут отличаться от коэффициентов "
               "ниже (они — по дневным данным). "
               f"Источник: {(stats or {}).get('source') or 'ISS MOEX (расчёт ИнвестАналитики)'}"
               f"{f', обновлено {upd}' if upd != '—' else ''}.")
    score = it.get("score")
    a, b = st.columns([1, 4])
    a.metric("Итоговый балл", f"{fmt_num(score)} из 100" if is_num(score) else "—")
    b.progress(min(max(float(score), 0.0), 100.0) / 100 if is_num(score) else 0.0)
    ranks = it.get("rank") or {}
    for key, label in RATING_KEYS:
        r = ranks.get(key)
        st.progress(min(max(float(r), 0.0), 100.0) / 100 if is_num(r) else 0.0,
                    text=f"{label}: перцентиль {fmt_num(r)} · значение по месячным данным {fmt_metric(key, it.get(key))}")
    months = it.get("months")
    span = f"за {fmt_num(months)} мес." if is_num(months) else "за окно до 10 лет"
    st.caption(f"Коэффициенты рейтинга — по месячным доходностям {span}: CAGR {fmt_pct(it.get('cagr'))}, "
               f"волатильность {fmt_pct(it.get('volatility'))}, макс. просадка {fmt_pct(it.get('max_drawdown'))}.")


# ------------------------------------------------------------------ демо-режим
if OFFLINE:
    st.caption(f"Бенчмарк ({bench}) и безрисковая ставка ({fmt_pct(rf, 2)} годовых) — в боковой панели.")
    st.info("**Карточка бумаги работает на живых данных ISS Мосбиржи**: ей нужны дневные свечи, текущие котировки "
            "и справочник бумаги. В демо-режиме есть только месячные ряды индексов и активов каталога — "
            "ниже показаны они и рейтинг из ночного пересчёта (если бумага в нём есть).")
    st.subheader(esc(secid))
    try:
        px = load_series(secid, "2000-01-01")
    except Exception:  # noqa: BLE001
        px = None
        st.caption(f"Ряда {esc(secid)} нет в демо-данных. Доступны: {', '.join(catalog_options())}.")
    if px is not None and len(px) > 3:
        def _demo():
            cols = {secid: px}
            if bench != secid:
                try:
                    cols[bench] = load_series(bench, str(px.index[0].date()))
                except Exception:  # noqa: BLE001
                    st.caption(f"Бенчмарка {bench} нет в демо-данных.")
            df = pd.DataFrame(cols).sort_index().ffill().dropna()
            st.plotly_chart(line_chart(df / df.iloc[0] * 100, bench if bench in df else None,
                                       "Стоимость, база = 100", hover_fmt=".1f"), width="stretch")
            st.caption(f"Рост 100 ₽ вложений, {fmt_date(df.index[0])} — {fmt_date(df.index[-1])}; месячные данные.")
            show_metrics_table({c: df[c] for c in df.columns}, df[bench] if bench in df else None, rf, "M")
        card("График и коэффициенты (демо, месячные данные)", _demo)
    card("Рейтинг риск/доходность", block_rating, secid)
    st.stop()


# ------------------------------------------------------------------ живые данные
with st.spinner(f"Загружаю {secid}…"):
    try:
        data = _load(secid)
        load_err = None
    except Exception as e:  # noqa: BLE001
        data, load_err = {}, humanize(e)
errors: dict = data.get("errors") or {}
info: dict = data.get("info") or {}
cls = info.get("cls")
unit = price_unit(cls, info.get("currency"))

# --- шапка
# латиницей введено название, а не тикер (ISS бумагу не нашёл) — предлагаем поиск
if not info and data.get("ohlc") is None and not load_err and query.isascii():
    def _pick():
        ss["sym_q"] = ss["sym_suggest"]
    try:
        _sr = _search(query)
    except Exception:  # noqa: BLE001
        _sr = None
    if _sr is not None and not _sr.empty and secid not in set(_sr["secid"]):
        _names = _sr.drop_duplicates("secid").set_index("secid")
        st.info(f"Тикер {esc(secid)} не найден на ISS — возможно, вы искали одну из этих бумаг.")
        st.selectbox("Найдено по названию", list(_names.index), index=None, key="sym_suggest", on_change=_pick,
                     placeholder="Выберите бумагу",
                     format_func=lambda x: f"{x} — {_names.loc[x].get('shortname') or _names.loc[x].get('name') or ''}")

# бенчмарк: по умолчанию — по классу бумаги, пока пользователь не выбрал его на странице явно
default_bench = info.get("benchmark") or ss["benchmark"]
bench = ss.get("sym_bench_user") or default_bench
bench_opts = BENCH_OPTIONS if bench in BENCH_OPTIONS else [bench] + BENCH_OPTIONS
ss["sym_bench_sel"] = bench


def _bench_chosen():
    ss["sym_bench_user"] = ss["sym_bench_sel"]


c3.selectbox("Бенчмарк", bench_opts, key="sym_bench_sel", on_change=_bench_chosen, format_func=asset_label,
             help=f"По умолчанию — по классу бумаги ({default_bench}).")
st.caption(f"Бенчмарк — {'выбран на странице' if ss.get('sym_bench_user') else 'по классу бумаги'}; "
           f"безрисковая ставка ({fmt_pct(rf, 2)} годовых) — в боковой панели. "
           "Коэффициенты на этой странице считаются по дневным доходностям.")

title = info.get("name") or info.get("short_name") or secid
st.subheader(esc(f"{title} · {secid}" if title != secid else secid))
if load_err:
    st.error(f"Не удалось загрузить данные {esc(secid)}: {esc(load_err)}. Попробуйте позже или проверьте тикер.")
elif info:
    cur = info.get("currency")
    facts = [
        ("Тип", info.get("type_label") or CLASS_LABELS.get(cls)),
        ("ISIN", info.get("isin")),
        ("Управляющая компания" if cls == "fund" else "Эмитент", info.get("issuer")),
        ("Начало торгов", fmt_date(info.get("first_trade_date")) if info.get("first_trade_date") else None),
        ("Уровень листинга", fmt_num(info.get("list_level")) if is_num(info.get("list_level")) else None),
        ("Валюта", "рубль" if str(cur or "").upper() in ("RUB", "SUR") else cur),
        ("Режим торгов", info.get("board")),
    ]
    st.markdown(" · ".join(f"{k}: **{esc(v)}**" for k, v in facts if v not in (None, "", "—")))
else:
    st.warning(f"Справка по {esc(secid)} недоступна: "
               f"{esc(humanize(Exception(errors.get('info') or 'ISS не вернул описания бумаги')))}. "
               "Остальные блоки строятся по истории торгов.")

rep, berr, rep_err = {}, None, None
if not load_err:
    with st.spinner("Считаю показатели…"):
        try:
            rep, berr = _report(secid, bench, rf)
        except Exception as e:  # noqa: BLE001
            rep_err = str(e) if isinstance(e, NoData) else humanize(e)
if rep_err:
    st.warning(f"Показатели недоступны: {esc(rep_err)}")
rep_errors: dict = rep.get("errors") or {}
# бенчмарк, выровненный по датам бумаги (report); устаревший / месячный / не загрузившийся — None
bser = rep.get("benchmark")
has_bench = bser is not None and bench != secid
# коэффициенты бумаги и бенчмарка — за общий период (metrics_common); без бенчмарка — по всей истории бумаги
mcommon = rep.get("metrics_common") if has_bench else None
if mcommon:
    msec, mbench = g(mcommon, "security", "sec"), g(mcommon, "benchmark", "bench")
else:
    msec, mbench = g(rep.get("metrics") or {}, "security", "sec"), None


def need_rep(key: Optional[str] = None):
    """Нет отчёта или блока key в нём — NoData с текстом ошибки из core."""
    if not rep:
        raise NoData(esc(rep_err or load_err or ""))
    if key and rep.get(key) is None:
        raise NoData(esc(rep_errors.get(key) or ""))


def divs_note() -> str:
    if rep.get("total_return"):
        return "С учётом дивидендов (реинвестирование в дату отсечки)."
    return "Доходность — по цене, без дивидендов." if cls == "share" else ""


def bench_unavailable() -> str:
    """«Бенчмарк X недоступен: причина» без точки в конце (причина из core может быть готовой фразой)."""
    why = str(berr or rep_errors.get("benchmark") or "нет данных").strip().rstrip(".")
    if why.lower().startswith("бенчмарк"):
        return esc(why)
    return f"Бенчмарк {bench} недоступен: {esc(why)}"


def bench_warn():
    if bench != secid and not has_bench:
        why = bench_unavailable()
        st.warning(why + ("." if "сравнение не показано" in why else " — бумага показана без него."))


def period_note() -> str:
    """Период коэффициентов: общий для бумаги и бенчмарка (common_from — common_to) или вся история бумаги."""
    if mcommon:
        return (f"общий период бумаги и бенчмарка {fmt_date(rep.get('common_from') or g(msec, 'start'))} — "
                f"{fmt_date(rep.get('common_to') or g(msec, 'end'))}")
    return f"период {fmt_date(g(msec, 'start'))} — {fmt_date(g(msec, 'end'))}"


# --- ключевые цифры
def block_key():
    try:
        s = _snapshot(secid) or data.get("snapshot")
    except Exception:  # noqa: BLE001 — свежие котировки не загрузились: те, что пришли со свечами
        s = data.get("snapshot")
    if not s:
        raise NoData(esc(errors.get("snapshot") or "ISS не вернул текущих котировок."))
    if s.get("date"):
        st.caption(f"Данные ISS Мосбиржи на {fmt_date(s.get('date'))} (с задержкой до 15 минут).")
    cols = st.columns(5 if is_num(s.get("market_cap")) else 4)
    prev = s.get("prev_close")
    cols[0].metric("Цена", fmt_price(s.get("last"), unit),
                   help=f"Закрытие накануне: {fmt_price(prev, unit)}" if is_num(prev) else None)
    ch = s.get("change")
    cols[1].metric("Изменение за день", fmt_signed_pct(s.get("change_pct"), 2),
                   delta=(("+" if float(ch) > 0 else "") + fmt_price(ch, unit)) if is_num(ch) and float(ch) != 0 else None)
    lo, hi, last = s.get("low52"), s.get("high52"), s.get("last")
    cols[2].metric("Диапазон 52 недель", f"{fmt_price(lo, unit)} — {fmt_price(hi, unit)}",
                   help="Минимум и максимум внутридневных цен (свечи ISS) за 52 недели")
    if is_num(lo) and is_num(hi) and is_num(last) and float(hi) > float(lo):
        cols[2].progress(min(max((float(last) - float(lo)) / (float(hi) - float(lo)), 0.0), 1.0),
                         text="текущая цена в диапазоне")
    cols[3].metric("Оборот за день", fmt_big_rub(s.get("value_rub")))
    if is_num(s.get("market_cap")):
        cols[4].metric("Капитализация", fmt_big_rub(s.get("market_cap")))


# --- график
def block_chart():
    need_rep("series")
    a, b, c = st.columns([3, 2, 1])
    per = a.segmented_control("Период", list(CHART_PERIODS), format_func=CHART_PERIODS.get, default="1Y",
                              key="sym_per") or "1Y"
    mode = b.segmented_control("Что показывать", ["growth", "price"], default="growth", key="sym_mode",
                               format_func={"growth": "Рост 100 ₽", "price": "Цена"}.get) or "growth"
    log_y = c.toggle("Лог. шкала", False, key="sym_log")
    if mode == "price":
        close = rep.get("close")
        if close is None:
            ohlc = data.get("ohlc")
            close = ohlc["close"].dropna() if ohlc is not None else pd.Series(dtype=float)
        if close.empty:
            raise NoData("Нет цен закрытия.")
        close = close.loc[m.period_start(close.index, per) or close.index[0]:]
        st.caption(f"Цена закрытия, {fmt_date(close.index[0])} — {fmt_date(close.index[-1])}.")
        st.plotly_chart(line_chart(close.to_frame(secid), y_title=f"Цена, {unit}", log_y=log_y, hover_fmt=",.2f"),
                        width="stretch")
        return
    cols = {secid: rep["series"]}
    if cmp:
        try:
            cols[cmp] = _prices(cmp, str(rep["series"].index[0].date()))
        except Exception as e:  # noqa: BLE001
            st.warning(f"{esc(cmp)}: {esc(humanize(e))}")
    if has_bench:
        cols[bench] = bser
    else:
        bench_warn()
    df = pd.DataFrame(cols).sort_index().ffill().dropna()
    if len(df) < 2:
        raise NoData("Нет общего периода данных.")
    df = df.loc[m.period_start(df.index, per) or df.index[0]:]
    norm = df / df.iloc[0] * 100
    notes = [f"Рост 100 ₽ вложений, {fmt_date(norm.index[0])} — {fmt_date(norm.index[-1])}."]
    if rep.get("total_return"):
        notes.append(f"{esc(secid)} — с учётом дивидендов.")
    if cmp in cols:
        notes.append(f"{esc(cmp)} — по цене закрытия.")
    if has_bench:
        notes.append(f"Бенчмарк — {bench} (серый пунктир).")
    st.caption(" ".join(notes))
    st.plotly_chart(line_chart(norm, bench if has_bench else None, "Стоимость, база = 100", log_y=log_y,
                               hover_fmt=".1f"), width="stretch")


# --- доходность по периодам
def block_periods():
    need_rep("periods")
    pr = rep.get("periods") or {}
    mine, bm = g(pr, "sec", "security") or {}, g(pr, "bench", "benchmark") if has_bench else None
    bench_warn()
    st.caption(f"{divs_note()} Для периодов больше года — также CAGR (среднегодовая доходность).".strip())

    def cell(x):
        if not x or not is_num(x.get("ret")):
            return "—"
        out = fmt_signed_pct(x["ret"], 1)
        return out + (f"  ·  CAGR {fmt_pct(x['cagr'], 1)}" if is_num(x.get("cagr")) else "")

    rows, raw = [], []
    for k, label in RETURN_PERIODS:
        row, rr = {"Период": label, secid: cell(mine.get(k))}, {"Период": None, secid: g(mine.get(k), "ret")}
        if bm is not None:
            row[bench] = cell(bm.get(k))
            rr[bench] = g(bm.get(k), "ret")
        rows.append(row)
        raw.append(rr)
    df, rdf = pd.DataFrame(rows), pd.DataFrame(raw)
    sty = df.style.apply(lambda col: [color_sign(v) for v in rdf[col.name]] if col.name != "Период"
                         else [""] * len(col), axis=0)
    st.dataframe(sty, hide_index=True, width="stretch")


# --- моментум
def block_momentum():
    need_rep("momentum")
    mo = rep.get("momentum") or {}
    score = g(mo, "score")
    if not is_num(score):
        raise NoData("Для расчёта нужна история не меньше года.")
    st.caption("По цене закрытия. Балл 0–100 — среднее пяти признаков тренда (формула — в методике).")
    a, b = st.columns([1, 4])
    a.metric("Балл моментума", f"{fmt_num(score)} из 100")
    b.progress(min(max(float(score), 0.0), 100.0) / 100)
    sma50, sma200 = g(mo, "sma50"), g(mo, "sma200")

    def yes(v, y, n):
        return "—" if v is None else (f":green[{y}]" if v else f":red[{n}]")

    a50, a200 = g(mo, "aboveSma50", "above_sma50"), g(mo, "aboveSma200", "above_sma200")
    cross = (sma50 > sma200) if is_num(sma50) and is_num(sma200) else None
    st.markdown("\n".join([
        f"- Цена против SMA 50: {yes(a50, 'выше', 'ниже')} (SMA 50 = {fmt_price(sma50, unit)})",
        f"- Цена против SMA 200: {yes(a200, 'выше', 'ниже')} (SMA 200 = {fmt_price(sma200, unit)})",
        f"- SMA 50 против SMA 200: {yes(cross, 'выше (восходящий тренд)', 'ниже (нисходящий тренд)')}",
        f"- До максимума цены закрытия за 52 недели: **{fmt_pct(g(mo, 'distHigh52', 'dist_high52'))}** "
        f"(максимум {fmt_price(g(mo, 'high52'), unit)})",
        f"- Моментум 6 мес. (без последнего месяца): **{fmt_signed_pct(g(mo, 'mom6'))}**",
        f"- Моментум 12 мес. (без последнего месяца): **{fmt_signed_pct(g(mo, 'mom12'))}**",
    ]))


# --- помесячная доходность
def block_monthly():
    need_rep("monthly")
    gr = rep.get("monthly") or {}
    years = gr.get("years") or []
    if not years:
        raise NoData("Недостаточно истории.")
    st.caption(f"{divs_note()} Строки — годы (свежие сверху), столбцы — месяцы; справа — итог года, "
               "внизу — медиана по месяцу.".strip())
    st.plotly_chart(monthly_heatmap(years, gr.get("cells") or [], g(gr, "year_total", "yearTotal") or [],
                                    g(gr, "median_by_month", "medianByMonth") or []), width="stretch")


# --- относительно бенчмарка
def block_relative():
    if bench == secid:
        raise NoData("Бумага совпадает с бенчмарком — выберите другой бенчмарк.")
    if not has_bench:
        raise NoData(bench_unavailable() + ".")
    if not mcommon:
        raise NoData(esc(rep_errors.get("metrics_common") or "нет общего периода бумаги и бенчмарка") + ".")
    st.caption(f"{divs_note()} Дневные доходности, {period_note()}.".strip())
    keys = list(REL_LABELS)
    for i in range(0, len(keys), 4):
        for col, k in zip(st.columns(4), keys[i:i + 4]):
            col.metric(REL_LABELS[k], fmt_metric(k, g(msec, k)))


# --- риск-доходность
def block_riskret():
    need_rep("metrics")
    bench_warn()
    if msec is None:
        raise NoData(esc(rep_errors.get("metrics") or ""))
    st.caption(f"Rf = {fmt_pct(rf, 2)}, дневные доходности, {period_note()}. {divs_note()}".strip())
    df = pd.DataFrame({"Показатель": list(RISK_LABELS.values()),
                       secid: [fmt_metric(k, g(msec, k)) for k in RISK_LABELS]})
    if has_bench and mbench is not None:
        df[f"{bench} (бенчмарк)"] = [fmt_metric(k, g(mbench, k)) for k in RISK_LABELS]
    st.dataframe(df, hide_index=True, width="stretch")


# --- хвостовые риски
def block_tail():
    need_rep("metrics")
    if msec is None:
        raise NoData(esc(rep_errors.get("metrics") or ""))
    st.caption("Исторический метод по дневным доходностям: потеря за день, которую превышают 5 % худших дней "
               f"(VaR), и средняя потеря в этих днях (CVaR); {period_note()}.")
    pairs = [(secid, msec)] + ([(f"{bench} (бенчмарк)", mbench)] if has_bench and mbench is not None else [])
    cols = st.columns(len(pairs) * 2)
    for i, (n, r) in enumerate(pairs):
        cols[2 * i].metric(f"VaR 95 % · {n}", fmt_metric("var_95", g(r, "var_95")))
        cols[2 * i + 1].metric(f"CVaR 95 % · {n}", fmt_metric("cvar_95", g(r, "cvar_95")))


# --- комиссия фонда
def block_fund():
    fd = data.get("fund") or {}
    fi, mk = fd.get("info"), fd.get("market") or {}
    src = ("Источник: [RusETFs (rusetfs.com)](https://rusetfs.com/) — комиссии, УК и СЧА фондов; "
           "данные обновляются раз в сутки.")
    if not fi:
        st.caption(f"{esc(secid)} нет в базе фондов RusETFs. " + esc(errors.get("fund") or ""))
        st.caption(src)
        return

    def pct(v):
        return f"{fmt_num(v, 2)} %" if is_num(v) else "—"

    fee = fi.get("commission_pct")
    items = [("Комиссия фонда (TER), год.", pct(fee), f"класс: {fi['asset_class']}" if fi.get("asset_class") else None),
             ("Медиана класса", pct(g(mk, "median", "classMedian", "class_median")),
              f"по {fmt_num(mk['n'])} торгуемым фондам того же класса" if is_num(mk.get("n")) and mk.get("n")
              else "нет данных рынка")]
    mm = g(mk, "marketMedian", "market_median")
    if is_num(mm):
        items.append(("Медиана всех фондов", pct(mm), None))
    items.append(("Управляющая компания", fi.get("issuer") or "—", None))
    if is_num(fi.get("aum_rub")):
        items.append(("СЧА", fmt_big_rub(fi.get("aum_rub")), None))
    for col, (k, v, h) in zip(st.columns(len(items)), items):
        col.metric(k, v, help=h)
    lo, hi = mk.get("min"), mk.get("max")
    if is_num(fee) and is_num(lo) and is_num(hi) and float(hi) > float(lo):
        st.plotly_chart(position_scale(float(fee), float(lo), float(hi), secid, "Комиссия, % годовых",
                                       q25=mk.get("p25"), median=g(mk, "median"), q75=mk.get("p75"), fmt=pct),
                        width="stretch")
        st.caption(f"Фонды того же класса: от {pct(lo)} до {pct(hi)}; серая полоса — межквартильный диапазон, "
                   "черта — медиана, точка — этот фонд.")
    st.caption(src)


# --- дивиденды / купоны
def _by_year(x) -> list[tuple]:
    if x is None:
        return []
    if isinstance(x, pd.Series):
        return [(int(k), v) for k, v in x.items()]
    if isinstance(x, dict):
        return [(int(k), v) for k, v in x.items()]
    return [(int(g(r, "year")), g(r, "value")) for r in x]


def block_divs():
    divs = [d for d in (data.get("dividends") or []) if not g(d, "cancelled", default=False)]
    st.caption("Источник: T-Invest API (используется с разрешения Т-Банка). Доходность TTM — выплаты "
               "за последние 12 месяцев к текущей цене.")
    if not divs:
        raise NoData(esc(errors.get("dividends") or f"Выплат {secid} в базе нет."))
    ds = rep.get("dividend_stats") or {}
    if not ds:
        st.caption("Статистика выплат недоступна: " + esc(rep_errors.get("dividend_stats") or rep_err or "нет истории цен") + ".")
    streak, ppy = g(ds, "growth_streak", "growthStreak"), g(ds, "payouts_per_year", "payoutsPerYear")
    ttm_v = g(ds, "ttm_value", "ttmValue")
    c = st.columns(4)
    c[0].metric("Выплаты за 12 мес.", f"{fmt_num(ttm_v, 2)} ₽" if is_num(ttm_v) else "—")
    c[1].metric("Доходность TTM", fmt_pct(g(ds, "ttm_yield", "ttmYield")))
    c[2].metric("Рост выплат подряд", f"{fmt_num(streak)} {years_word(int(streak))}" if is_num(streak) else "—")
    c[3].metric("Выплат в год", fmt_num(ppy, 0 if is_num(ppy) and float(ppy) % 1 == 0 else 1))
    by = [(y, v) for y, v in _by_year(g(ds, "by_year", "byYear")) if y]
    if by:
        st.plotly_chart(bar_chart([y for y, _ in by], [v if is_num(v) else None for _, v in by], secid,
                                  "Выплаты, ₽ на акцию", "Год"), width="stretch")
    bm = list(g(ds, "by_month", "byMonth") or [])
    if len(bm) == 12:
        st.markdown("**Число выплат по месяцам отсечки (за всю историю)**")
        st.dataframe(pd.DataFrame([[fmt_num(v) if is_num(v) and v else "—" for v in bm]], columns=MONTHS_RU),
                     hide_index=True, width="stretch")
    up = g(ds, "upcoming") or []
    if len(up):
        st.info("Объявлено: " + "; ".join(
            f"{fmt_num(g(d, 'value'), 2)} ₽, отсечка {fmt_date(g(d, 'recordDate', 'record_date', 'exDate', 'ex_date'))}"
            for d in up) + ".")
    st.markdown("**Последние выплаты**")
    recent = sorted(divs, key=lambda d: str(g(d, "recordDate", "record_date", default="")), reverse=True)[:10]

    def amount(d):
        cur = str(g(d, "currency", default="RUB")).upper()
        return f"{fmt_num(g(d, 'value'), 2)} {'₽' if cur in ('RUB', 'SUR') else cur}"

    st.dataframe(pd.DataFrame([{
        "Дата отсечки": fmt_date(g(d, "recordDate", "record_date")),
        "Экс-дивидендная дата": fmt_date(g(d, "exDate", "ex_date")),
        "На акцию": amount(d), "Доходность": fmt_pct(g(d, "yield")),
    } for d in recent]), hide_index=True, width="stretch")


def _col(df: pd.DataFrame, *names):
    return next((n for n in names if n in df.columns), None)


def block_coupons():
    cp = data.get("coupons")
    st.caption("Источник: ISS Мосбиржи (bondization). Будущие купоны показаны бледными столбиками.")
    if cp is None or len(cp) == 0:
        raise NoData(esc(errors.get("coupons") or "ISS не вернул графика купонов."))
    dcol = _col(cp, "date", "coupondate")
    vcol = _col(cp, "value", "value_rub")
    if dcol is None or vcol is None:
        raise NoData("Неизвестный формат графика купонов.")
    rcol = _col(cp, "rate")
    pcol = _col(cp, "valueprc")
    df = cp.copy()
    df[dcol] = pd.to_datetime(df[dcol])
    df = df.sort_values(dcol)
    today = pd.Timestamp.today().normalize()
    vals = [float(v) if is_num(v) else None for v in df[vcol]]
    st.plotly_chart(bar_chart([fmt_date(d) for d in df[dcol]], vals, secid, "Купон, ₽", "Дата выплаты",
                              muted=list(df[dcol] >= today)), width="stretch")
    fut = df[df[dcol] >= today].head(8)
    st.markdown("**Ближайшие купоны**")
    if fut.empty:
        st.caption("Выплат впереди нет.")
        return

    def rate(r):
        if rcol and is_num(r[rcol]):
            return fmt_pct(r[rcol], 2)
        if pcol and is_num(r[pcol]):
            return f"{fmt_num(r[pcol], 2)}%"
        return "—"

    st.dataframe(pd.DataFrame([{"Дата": fmt_date(r[dcol]), "Купон, ₽": fmt_num(r[vcol], 2), "Ставка": rate(r)}
                               for _, r in fut.iterrows()]), hide_index=True, width="stretch")


# --- просадки
def block_drawdowns():
    need_rep("drawdowns")
    dd = rep.get("drawdowns") or {}
    ser = dd.get("series")
    cur = dd.get("current") or {}
    span = ""
    if isinstance(ser, pd.Series) and len(ser):
        span = f"{fmt_date(ser.index[0])} — {fmt_date(ser.index[-1])}. "
    st.caption(f"Снижение от предыдущего максимума, {span}{divs_note()}".strip())
    days = g(cur, "days")
    c = st.columns(4)
    c[0].metric("Максимальная", fmt_pct(g(dd, "max") if is_num(g(dd, "max")) else g(msec, "max_drawdown")))
    c[1].metric("Текущая", fmt_pct(g(cur, "depth")),
                help=(f"{fmt_num(days)} дн. от пика {fmt_date(g(cur, 'peak'))}" if is_num(days) and days
                      else "на максимуме"))
    c[2].metric("Средняя", fmt_pct(dd.get("avg")))
    c[3].metric("Индекс язвы", fmt_pct(dd.get("ulcer"), 2))
    if isinstance(ser, pd.Series) and len(ser):
        cols = {secid: ser}
        bdd = dd.get("benchmark_series")
        if has_bench and isinstance(bdd, pd.Series) and len(bdd):
            cols[bench] = bdd
        df = pd.DataFrame(cols).sort_index().ffill()
        st.plotly_chart(line_chart(df, bench if has_bench else None, "Просадка", pct_y=True, height=300),
                        width="stretch")
    top = dd.get("top") or []
    st.markdown("**Пять крупнейших просадок**")
    if not top:
        st.caption("Просадок нет.")
        return
    st.dataframe(pd.DataFrame([{
        "Глубина": fmt_pct(g(d, "depth")), "Пик": fmt_date(g(d, "peak")), "Дно": fmt_date(g(d, "trough")),
        "Восстановление": fmt_date(g(d, "recovery")) if g(d, "recovery") else "не восстановлена",
        "Дней до дна": fmt_num(g(d, "daysToTrough", "days_to_trough")),
        "Дней до восстановления": fmt_num(g(d, "daysToRecover", "days_to_recover")),
    } for d in top]), hide_index=True, width="stretch")


# --- волатильность
def block_vol():
    need_rep("volatility")
    v = rep.get("volatility") or {}
    roll = v.get("rolling")
    span = (f" ({fmt_date(v.get('from'))} — {fmt_date(v.get('to'))})" if v.get("from") and v.get("to") else "")
    st.caption("Скользящая волатильность — стандартное отклонение дневных доходностей за 21 торговый день "
               "в годовом выражении. Оценки по OHLC (цены открытия, максимума, минимума, закрытия) — "
               f"за последний год{span}.")
    if isinstance(roll, pd.Series) and roll.dropna().size:
        roll = roll.dropna()
        cols = {secid: roll}
        rb = v.get("rolling_benchmark")
        if has_bench and isinstance(rb, pd.Series) and rb.dropna().size:
            cols[bench] = rb.dropna()
        st.plotly_chart(line_chart(pd.DataFrame(cols).sort_index(), bench if bench in cols else None,
                                   "Волатильность, год.", pct_y=True, height=300), width="stretch")
    rows = [("Close-to-close", g(v, "close_to_close", "close"))] if g(v, "close_to_close", "close") is not None else []
    rows += [("Паркинсон", v.get("parkinson")), ("Гарман–Класс", v.get("garman_klass")),
             ("Роджерс–Сатчелл", v.get("rogers_satchell")), ("Янг–Чжан", v.get("yang_zhang"))]
    if not any(is_num(x) for _, x in rows) and not isinstance(roll, pd.Series):
        raise NoData("Нет свечей OHLC.")
    st.markdown("**Годовая волатильность за 1 год**")
    st.dataframe(pd.DataFrame([{"Оценка": n, "Волатильность, год.": fmt_pct(x)} for n, x in rows]),
                 hide_index=True, width="stretch")
    if cls == "index":
        st.caption("Для индексов OHLC берутся из истории ISS; в днях без внутридневных данных оценки могут быть занижены.")


# --- облигация
def block_bond():
    bd = (data.get("snapshot") or {}).get("bond") or {}
    st.caption("Доходность к погашению (или к оферте, если она назначена), дюрация и НКД — ISS Мосбиржи. "
               "Цена облигации — в % от номинала.")
    dur, aci, cv = g(bd, "duration"), g(bd, "accrued_int", "accruedInt"), g(bd, "coupon_value", "couponValue")
    cp, per, fv = info.get("coupon_percent"), info.get("coupon_period"), info.get("face_value")
    facts = [
        ("Доходность", fmt_pct(g(bd, "yield"), 2)),
        ("Дюрация", f"{fmt_num(dur, 2)} г." if is_num(dur) else "—"),
        ("НКД", f"{fmt_num(aci, 2)} ₽" if is_num(aci) else "—"),
        ("Купон", f"{fmt_num(cv, 2)} ₽" if is_num(cv) else "—"),
        ("Ставка купона", f"{fmt_num(cp, 2)} % год." if is_num(cp) else "—"),
        ("Купонный период", f"{fmt_num(per)} дн." if is_num(per) else "—"),
        ("Ближайший купон", fmt_date(g(bd, "next_coupon", "nextCoupon"))),
        ("Номинал", f"{fmt_num(fv, 2)} ₽" if is_num(fv) else "—"),
        ("Погашение", fmt_date(info.get("mat_date"))),
        ("Оферта", fmt_date(info.get("offer_date")) if info.get("offer_date") else "нет"),
    ]
    for i in range(0, len(facts), 5):
        for col, (k, val) in zip(st.columns(5), facts[i:i + 5]):
            col.metric(k, val)


# ------------------------------------------------------------------ блоки по порядку
# без истории торгов расчётные блоки не показываем (одно сообщение выше вместо десятка «нет данных»)
calc = bool(rep) and rep.get("series") is not None
if data.get("snapshot") or not load_err:
    card("Ключевые цифры", block_key)
if calc:
    card("График", block_chart)
    card("Доходность по периодам", block_periods)
    card("Моментум", block_momentum)
    card("Помесячная доходность", block_monthly)
card("Рейтинг риск/доходность", block_rating, secid, data.get("rating"), errors.get("rating"))
if calc:
    card(f"Относительно бенчмарка {bench}", block_relative)
    card("Риск-доходность", block_riskret)
    card("Хвостовые риски: VaR и CVaR 95 %", block_tail)
if cls == "fund":
    card("Комиссия фонда против рынка", block_fund)
if cls == "share" and (calc or data.get("dividends")):
    card("Дивиденды", block_divs)
elif cls == "bond":
    card("Купоны", block_coupons)
if calc:
    card("Просадки", block_drawdowns)
    card("Волатильность", block_vol)
if cls == "bond":
    card("Облигация", block_bond)

# --- призыв (без истории торгов бэктест по бумаге не построить)
if not calc:
    st.stop()
with st.container(border=True):
    a, b = st.columns([3, 1])
    a.markdown(f"#### Собрать портфель с {esc(secid)}")
    a.caption("Бумага попадёт в собственные портфели бэктеста с долей 100 % — добавьте другие активы "
              "и сравните с классическими стратегиями.")
    if b.button("Собрать портфель с этой бумагой", type="primary", width="stretch"):
        name = f"Портфель с {secid}"
        ss.setdefault("custom_portfolios", {})[name] = {secid: 1.0}
        ss["bt_prefill"] = {"name": name, "weights": {secid: 1.0}}
        ss["bt_prefill_msg"] = (f"{esc(secid)} добавлена в собственные портфели («{name}») с долей 100 %. "
                                "Добавьте другие активы, задайте доли, сохраните портфель и запустите бэктест.")
        try:
            st.switch_page("pages/4_Бэктест.py")
        except Exception:  # noqa: BLE001 — одиночный запуск файла без навигации
            st.success(ss["bt_prefill_msg"] + " Откройте страницу «Бэктест».")
