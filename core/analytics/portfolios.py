"""
Портфели и стратегии (этап 1): каталог портфелей с постоянными долями и их расчёт.

История — «гибрид»: доходность портфеля считается по индексам полной доходности (длинная история),
а для каждого портфеля есть блок «Как повторить» — какими БПИФ его собрать и их комиссии
(RusETFs, public/data/rusetfs_funds.json; резервно — T-Invest, core.data.public_data.etf_commissions).

Расчёт:
  * стоимость портфеля из 100 000 ₽ — core.analytics.backtest.run_backtest с долями и частотой
    ребалансировки каталога, без комиссий и налогов, по ценам core.data.universe.load_prices
    (как страница бэктеста); начало истории — самая поздняя первая дата среди активов портфеля;
  * коэффициенты — core.analytics.metrics.compute_all (формулы — там), безрисковая ставка —
    ряд core.data.risk_free.rate_series за окно (ставка на начало каждого периода доходности);
    если ряд ставки недоступен — ставка 0 и предупреждение в warnings (показатели завышены);
  * частота доходностей для коэффициентов — дневная для дневных рядов (как страница бумаги),
    месячная для месячных демо-данных (IP_OFFLINE=1);
  * ранг риск/доходность 0–100 — core.analytics.ranking.rank_items (перцентили Шарпа, Сортино, Омеги,
    Кальмара и Мартина за 5 лет среди портфелей с историей ≥ 5 лет, score — среднее рангов; короче — None).

Источник данных функций (параметр source):
  "auto" — public/data/portfolios.json, если он есть (собирает scripts/collect_data.py, источник
           portfolios), иначе живой расчёт; "file" — только файл; "live" — только живой расчёт.
Ряды стоимости в файле — месячные (последний торговый день месяца + первая и последняя даты).

Модуль не импортирует Streamlit.
"""
from __future__ import annotations

import math
import os
import copy
import datetime as dt
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.analytics import metrics as m
from core.analytics import ranking
from core.analytics.backtest import BacktestConfig, BacktestResult, run_backtest
from core.data import public_data, universe

INITIAL = 100_000.0
START = "2000-01-01"            # как страница бэктеста: загрузка с 2000 г., начало — по самому позднему активу
FILE = "portfolios"
DEFAULT_BENCHMARK = "MCFTR"
GROUPS = ("Ленивые портфели", "Тематические")
WINDOWS = ("1Y", "5Y", "10Y", "MAX")
RISK_RETURN_PERIODS = ("1Y", "5Y", "10Y")
RANK_WINDOW = "5Y"              # окно коэффициентов для ранга риск/доходность (колонки таблицы — за 1 год)
REBALANCE_LABELS = {"none": "без ребалансировки", "M": "ежемесячно", "Q": "ежеквартально",
                    "H": "раз в полгода", "A": "раз в год (последний торговый день декабря)"}
METRIC_FIELDS = ("total_return", "cagr", "volatility", "sharpe", "sortino", "omega", "calmar", "martin",
                 "ulcer_index", "max_drawdown", "alpha", "beta", "r_squared", "correlation",
                 "up_capture", "down_capture")
TABLE_COLUMNS = ["name", "group", "since", "last_date", "ret_1d", "ytd", "cagr_10y", "cagr_all",
                 "max_drawdown", "current_drawdown", "fee", "fee_coverage", "sharpe", "sortino", "omega",
                 "martin", "calmar", "ulcer_index", "rank", "rank_window", "volatility_1y", "benchmark"]

# Названия активов вне core.data.universe.CATALOG (тикеры индексов ISS MOEX)
EXTRA_ASSETS: dict[str, str] = {
    "MEOGTR": "Индекс МосБиржи нефти и газа полной доходности",
    "MEFNTR": "Индекс МосБиржи финансов полной доходности",
    "MEMMTR": "Индекс МосБиржи металлов и добычи полной доходности",
    "MECNTR": "Индекс МосБиржи потребительского сектора полной доходности",
    "MECHTR": "Индекс МосБиржи химии и нефтехимии полной доходности",
    "METNTR": "Индекс МосБиржи транспорта полной доходности",
    "MEITTR": "Индекс МосБиржи информационных технологий полной доходности",
}

# «Как повторить»: биржевые фонды по активам. Первый фонд — основной (по нему считается комиссия портфеля).
REPLICATE: dict[str, dict] = {
    "MCFTR": {"funds": ["EQMX", "SBMX", "AMIX", "TMOS"],
              "note": "Фонды на индекс МосБиржи; дивиденды реинвестируются внутри фонда."},
    "CORP_CHAIN": {"funds": ["OBLG", "SBRB"],
                   "note": "Фонды корпоративных облигаций; SBRB — облигации с дюрацией 1–3 года."},
    "RGBITR": {"funds": ["SBGB", "WOFZ", "TOFZ"], "note": "Фонды на индекс гособлигаций (ОФЗ)."},
    "RUGBITR1Y": {"funds": ["SUGB"],
                  "note": "Фонда на ОФЗ до 1 года нет: SUGB — ОФЗ с дюрацией 1–3 года; "
                          "по риску близок и фонд денежного рынка (LQDT)."},
    "RUGBITR5+": {"funds": ["SBLB"],
                  "note": "Фонда на индекс RUGBITR5+ нет; SBLB — близкий фонд долгосрочных ОФЗ, не на этот индекс."},
    "RUGBITR10Y": {"funds": ["AMGB"],
                   "note": "Фонда на индекс RUGBITR10Y нет; AMGB — близкий фонд длинных ОФЗ с активным управлением, "
                           "не на этот индекс."},
    "GOLD_CBR": {"funds": ["GOLD", "AKGD", "SBGD"],
                 "note": "Фонды на биржевое золото; в расчёте — учётная цена золота Банка России."},
    "RUONIA": {"funds": ["LQDT", "SBMM", "AKMM", "TMON"],
               "note": "Фонды денежного рынка (РЕПО с центральным контрагентом), доходность близка к RUONIA."},
    "MOEXEU": {"funds": [], "note": "Прямого БПИФ нет."},
    "IRDIVTR": {"funds": ["DIVD", "MKBD", "TDIV"],
                "note": "DIVD следует за индексом IRDIVTR; MKBD и TDIV — за другими индексами дивидендных акций."},
    "MEFNTR": {"funds": ["AKFN"], "note": "AKFN — индексный фонд на индекс МосБиржи финансов "
                                          "(«Альфа-Капитал Индекс МосБиржи финансов»)."},
    "MECNTR": {"funds": ["AKCN"], "note": "AKCN — индексный фонд на индекс МосБиржи потребительского сектора "
                                          "(«Альфа-Капитал Индекс МосБиржи потреб. сектора»)."},
    "MEITTR": {"funds": ["TITR"],
               "note": "Фонда на индекс MEITTR нет; TITR — близкий по составу фонд российских технологических акций."},
    "MEOGTR": {"funds": [], "note": "Прямого БПИФ нет."},
    "MEMMTR": {"funds": [], "note": "Прямого БПИФ нет."},
    "MEEUTR": {"funds": [], "note": "Прямого БПИФ нет."},
    "MECHTR": {"funds": [], "note": "Прямого БПИФ нет."},
    "METNTR": {"funds": [], "note": "Прямого БПИФ нет."},
}

# Короткие названия активов для интерфейса: «класс (тикер индекса)»
SHORT_NAMES: dict[str, str] = {
    "MCFTR": "Акции (MCFTR)",
    "IMOEX": "Акции, ценовой индекс (IMOEX)",
    "MEBCTR": "Голубые фишки (MEBCTR)",
    "IRDIVTR": "Дивидендные акции (IRDIVTR)",
    "MOEXEU": "Электроэнергетика, ценовой (MOEXEU)",
    "MEEUTR": "Электроэнергетика (MEEUTR)",
    "RGBITR": "ОФЗ (RGBITR)",
    "RUGBITR1Y": "Короткие ОФЗ (RUGBITR1Y)",
    "RUGBITR5+": "ОФЗ 5+ лет (RUGBITR5+)",
    "RUGBITR10Y": "Длинные ОФЗ (RUGBITR10Y)",
    "RUCBITR": "Корп. облигации (RUCBITR)",
    "RUCBTRNS": "Корп. облигации (RUCBTRNS)",
    "CORP_CHAIN": "Корп. облигации (RUCBITR→RUCBTRNS)",
    "GOLD_CBR": "Золото (ЦБ)",
    "GLDRUB_TOM": "Золото, биржевое (GLDRUB_TOM)",
    "RUONIA": "Денежный рынок (RUONIA)",
    "MEOGTR": "Нефть и газ (MEOGTR)",
    "MEFNTR": "Финансы (MEFNTR)",
    "MEMMTR": "Металлы и добыча (MEMMTR)",
    "MECNTR": "Потребительский сектор (MECNTR)",
    "MECHTR": "Химия и нефтехимия (MECHTR)",
    "METNTR": "Транспорт (METNTR)",
    "MEITTR": "IT (MEITTR)",
}

_LAZY, _THEME = GROUPS


def _p(name: str, group: str, description: str, rules: str, weights: dict[str, float], rebalance: str = "A",
       benchmark: str = DEFAULT_BENCHMARK, strategy: Optional[str] = None, note: str = "",
       one_fund: Optional[dict] = None) -> dict:
    """Запись каталога. strategy — имя в core.analytics.strategies.STRATEGIES (страница бэктеста)."""
    return {"name": name, "group": group, "description": description, "rules": rules, "weights": weights,
            "rebalance": rebalance, "benchmark": benchmark, "strategy": strategy, "note": note,
            "replicate": [{"asset": a, **REPLICATE.get(a, {"funds": [], "note": "Прямого БПИФ нет."})}
                          for a in weights],
            "one_fund": one_fund}


def _sector(name: str, key: str, about: str) -> dict:
    return _p(name, _THEME,
              f"Акции российских компаний сектора «{about}» — весь сектор одним индексом МосБиржи "
              f"полной доходности (дивиденды реинвестируются). Подходит как тематическая добавка к основному "
              f"портфелю, а не как единственное вложение: риск одного сектора выше, чем у рынка в целом.",
              f"100 % в индексе {key} ({universe.CATALOG[key].name if key in universe.CATALOG else EXTRA_ASSETS[key]}); ребалансировка не нужна.",
              {key: 1.0}, rebalance="none")


PORTFOLIOS: dict[str, dict] = {
    # ------------------------------------------------------------------ ленивые портфели
    "lazy-plus": _p(
        "Лежебока плюс (Спирин)", _LAZY,
        "Равные доли акций, облигаций и золота плюс немного «кэша». Портфель почти не требует внимания: "
        "раз в год доли возвращаются к исходным. Золото защищает от девальвации рубля, облигации и денежный "
        "рынок сглаживают падения акций.",
        "30 % акции (MCFTR), 30 % корпоративные облигации (RUCBITR → RUCBTRNS), 30 % золото (учётная цена ЦБ), "
        "10 % денежный рынок (индекс RUONIA); ребалансировка раз в год в конце декабря.",
        {"MCFTR": 0.30, "CORP_CHAIN": 0.30, "GOLD_CBR": 0.30, "RUONIA": 0.10},
        strategy="Лежебока плюс (Спирин)", note="30% EQMX / 30% OBLG / 30% GOLD / 10% LQDT",
        one_fund={"funds": ["SPRN"], "note": "Готовый фонд «Модельный портфель Лежебоки» (Первая) — "
                                              "та же идея одним фондом, состав и доли фонда могут отличаться."}),
    "60-40": _p(
        "Портфель 60/40", _LAZY,
        "Классика: 60 % в акциях ради роста и 40 % в государственных облигациях ради устойчивости. "
        "Падения меньше, чем у чистых акций, а доходность обычно выше, чем у облигаций.",
        "60 % акции (MCFTR), 40 % ОФЗ (RGBITR); ребалансировка раз в год в конце декабря.",
        {"MCFTR": 0.60, "RGBITR": 0.40},
        strategy="Портфель 60/40", note="60% акции / 40% облигации"),
    "permanent-browne": _p(
        "Постоянный портфель (Браун)", _LAZY,
        "«Вечный» портфель Гарри Брауна: четыре актива поровну, каждый хорош в своей экономической ситуации — "
        "акции при росте, длинные облигации при снижении ставок, золото при инфляции и девальвации, "
        "деньги в кризис. Цель — пережить любой сценарий без больших просадок.",
        "По 25 % в акциях (MCFTR), длинных ОФЗ 10+ лет (RUGBITR10Y), золоте (учётная цена ЦБ) и денежном "
        "рынке (индекс RUONIA); ребалансировка раз в год в конце декабря.",
        {"MCFTR": 0.25, "RUGBITR10Y": 0.25, "GOLD_CBR": 0.25, "RUONIA": 0.25},
        strategy="Постоянный портфель (Браун)",
        note="25% акции / 25% длинные облигации / 25% золото / 25% денежный рынок",
        one_fund={"funds": ["TRUR"], "note": "Готовый фонд «Стратегия вечного портфеля в рублях» (Т-Капитал) — "
                                              "похожая идея одним фондом, доли фонда отличаются от 25/25/25/25."}),
    "all-weather-dalio": _p(
        "Всепогодный портфель (Далио)", _LAZY,
        "Российская адаптация портфеля Рэя Далио: много облигаций разной длины, треть в акциях, немного золота "
        "и сырьевого сектора. Задумка — сбалансировать риски так, чтобы портфель вёл себя ровно "
        "при любой «погоде» в экономике.",
        "30 % акции (MCFTR), 40 % длинные ОФЗ 10+ лет (RUGBITR10Y), 15 % ОФЗ 5+ лет (RUGBITR5+), 7,5 % золото, "
        "7,5 % индекс электроэнергетики полной доходности MEEUTR (замена сырьевой доли); ребалансировка раз в год. "
        "История — с 30.12.2013 (начало MEEUTR).",
        {"MCFTR": 0.30, "RUGBITR10Y": 0.40, "RUGBITR5+": 0.15, "GOLD_CBR": 0.075, "MEEUTR": 0.075},
        strategy="Всепогодный портфель (Далио)",
        note="30% акции / 40% длинные / 15% среднесрочные облигации / 7,5% золото / "
             "7,5% сырьё -> индекс электроэнергетики полной доходности MEEUTR (замена по умолчанию, "
             "состав можно изменить)"),
    "40-40-20": _p(
        "Портфель 40/40/20", _LAZY,
        "Умеренный портфель: поровну акций и корпоративных облигаций и пятая часть в золоте. "
        "Корпоративные облигации платят больше государственных, золото страхует от девальвации рубля.",
        "40 % акции (MCFTR), 40 % корпоративные облигации (RUCBITR → RUCBTRNS), 20 % золото (учётная цена ЦБ); "
        "ребалансировка раз в год в конце декабря.",
        {"MCFTR": 0.40, "CORP_CHAIN": 0.40, "GOLD_CBR": 0.20},
        strategy="Портфель 40/40/20",
        note="40% акции (рост + дивиденды -> MCFTR) / 40% облигации -> корп. облигации / 20% золото"),
    "buffett-90-10": _p(
        "Завещание Баффетта 90/10", _LAZY,
        "Совет Уоррена Баффетта для наследников: 90 % в дешёвом индексном фонде акций и 10 % в коротких "
        "гособлигациях. Максимум роста на длинном горизонте ценой глубоких просадок — подходит тем, "
        "кто готов терпеть падения рынка.",
        "90 % акции (MCFTR), 10 % ОФЗ до 1 года (RUGBITR1Y); ребалансировка раз в год в конце декабря.",
        {"MCFTR": 0.90, "RUGBITR1Y": 0.10},
        strategy="Завещание Баффетта 90/10", note="90% акции / 10% краткосрочные гособлигации"),
    "three-funds": _p(
        "Три фонда", _LAZY,
        "Простейший полноценный портфель из трёх фондов: акции России, ОФЗ и золото. В классическом варианте "
        "третий фонд — иностранные акции; на российском рынке его роль (защита от ослабления рубля) "
        "выполняет золото. Половина в акциях даёт рост, ОФЗ и золото смягчают просадки.",
        "50 % акции (MCFTR), 30 % ОФЗ (RGBITR), 20 % золото (учётная цена ЦБ); ребалансировка раз в год "
        "в конце декабря.",
        {"MCFTR": 0.50, "RGBITR": 0.30, "GOLD_CBR": 0.20}),
    "ofz-only": _p(
        "Только ОФЗ", _LAZY,
        "Весь капитал в государственных облигациях — консервативный вариант для тех, кто не готов к колебаниям "
        "акций. Доходность складывается из купонов и изменения цен облигаций; при росте ставок цены ОФЗ "
        "временно снижаются.",
        "100 % в индексе гособлигаций полной доходности RGBITR; ребалансировка не нужна.",
        {"RGBITR": 1.0}, rebalance="none", benchmark="RGBITR"),
    "index-money-market": _p(
        "Индекс + денежный рынок", _LAZY,
        "Большая часть в акциях через индекс МосБиржи, остальное — «подушка» в фонде денежного рынка. "
        "Подушка почти не колеблется, приносит доход на уровне ставок денежного рынка и при ежегодной "
        "ребалансировке позволяет докупать акции после падений.",
        "70 % акции (MCFTR), 30 % денежный рынок (индекс RUONIA — накопленная ставка RUONIA, аналог фондов "
        "LQDT, SBMM, AKMM); ребалансировка раз в год в конце декабря.",
        {"MCFTR": 0.70, "RUONIA": 0.30}),
    "dividend": _p(
        "Дивидендный", _LAZY,
        "Акции российских компаний с высокими и стабильными дивидендами — индекс дивидендных акций "
        "полной доходности (дивиденды реинвестируются). Подходит тем, кто ценит денежный поток "
        "от бизнеса; по риску близок к рынку акций в целом.",
        "100 % в индексе дивидендных акций РФ полной доходности IRDIVTR (рассчитывает МосБиржа по методике "
        "УК «Доходъ»); ребалансировка не нужна. История индекса на ISS — с 18.06.2021.",
        {"IRDIVTR": 1.0}, rebalance="none"),
    # ------------------------------------------------------------------ тематические (секторные индексы)
    "sector-oil-gas": _sector("Нефть и газ", "MEOGTR", "нефть и газ"),
    "sector-finance": _sector("Финансы", "MEFNTR", "финансы"),
    "sector-metals-mining": _sector("Металлы и добыча", "MEMMTR", "металлы и добыча"),
    "sector-consumer": _sector("Потребительский сектор", "MECNTR", "потребительский сектор"),
    "sector-power": _sector("Электроэнергетика", "MEEUTR", "электроэнергетика"),
    "sector-chemicals": _sector("Химия и нефтехимия", "MECHTR", "химия и нефтехимия"),
    "sector-transport": _sector("Транспорт", "METNTR", "транспорт"),
    "sector-it": _sector("Информационные технологии", "MEITTR", "информационные технологии"),
}


class PortfolioUnavailable(Exception):
    """Нет данных по активам портфеля (например, в демо-режиме нет ряда индекса)."""


# ---------------------------------------------------------------------------- данные
def asset_name(key: str) -> str:
    """Полное название актива (CATALOG или EXTRA_ASSETS); неизвестный ключ — как есть."""
    spec = universe.CATALOG.get(key)
    return spec.name if spec is not None else EXTRA_ASSETS.get(key, key)


def asset_short_name(key: str) -> str:
    """Короткое название актива для интерфейса: «Акции (MCFTR)», «Золото (ЦБ)» и т. п. (SHORT_NAMES);
    неизвестный ключ — сам ключ."""
    return SHORT_NAMES.get(key, key)


def all_asset_keys() -> list[str]:
    keys = {DEFAULT_BENCHMARK}
    for p in PORTFOLIOS.values():
        keys |= set(p["weights"]) | {p["benchmark"]}
    return sorted(keys)


def _offline() -> bool:
    return os.environ.get("IP_OFFLINE") == "1"


_CACHE: dict[tuple, pd.Series] = {}
_DEMO_KEYS: set[str] = set()      # ключи, для которых при последней загрузке подставлен демо-ряд (fallback)


def clear_cache() -> None:
    """Сбросить кэш рядов (в процессе; ответы ISS/ЦБ дополнительно кэшируются в SQLite)."""
    _CACHE.clear()
    _DEMO_KEYS.clear()


def _series(key: str, fallback: bool = True) -> pd.Series:
    """Ряд актива (universe.load_series с 2000 г.). Кэш — на календарный день (ключ с датой, как
    risk_free._live), отдельно для офлайн-режима; ряд, подменённый демо-данными из-за сбоя источника
    (universe.FALLBACK_USED), не кэшируется и отмечается в _DEMO_KEYS."""
    ck = (key, fallback, _offline(), dt.date.today().isoformat())
    if ck in _CACHE:
        return _CACHE[ck]
    universe.FALLBACK_USED.discard(key)
    s = universe.load_series(key, START, None, fallback=fallback)
    s = s.dropna().astype(float)
    s = s[s > 0]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if key in universe.FALLBACK_USED:
        _DEMO_KEYS.add(key)
    else:
        _DEMO_KEYS.discard(key)
        _CACHE[ck] = s
    return s


_series.cache_clear = clear_cache          # прежний интерфейс (functools.lru_cache), используется в тестах


def _calendar(series: dict[str, pd.Series]) -> pd.DatetimeIndex:
    """Календарь портфеля: торговые дни биржи (даты рядов ISS), если в портфеле есть биржевой актив,
    иначе объединение дат; конец — самая ранняя последняя дата активов (у ЦБ учётная цена золота
    датируется следующим днём и выходит и по субботам — без обрезки последний день портфеля
    состоял бы из протянутых значений индексов)."""
    exch = [s.index for k, s in series.items()
            if not (universe.CATALOG.get(k) and universe.CATALOG[k].source == "cbr")]
    idx = exch[0] if exch else next(iter(series.values())).index
    for i in (exch[1:] if exch else [s.index for s in list(series.values())[1:]]):
        idx = idx.union(i)
    last = min(s.index[-1] for s in series.values())
    return pd.DatetimeIndex(idx[idx <= last])


def _prices(keys: list[str], fallback: bool = True) -> pd.DataFrame:
    """Цены активов на общем календаре (_calendar): значение на дату — последнее известное (ffill),
    начало — самая поздняя первая дата активов (как universe.load_prices, align='common').
    Нет ряда — PortfolioUnavailable."""
    series = {}
    for k in keys:
        try:
            s = _series(k, fallback)
        except Exception as e:  # noqa: BLE001
            raise PortfolioUnavailable(f"{k}: нет данных ({type(e).__name__}: {str(e)[:150]})") from e
        if s.empty:
            raise PortfolioUnavailable(f"{k}: пустой ряд")
        series[k] = s
    cal = _calendar(series)
    df = pd.DataFrame({k: s.reindex(s.index.union(cal)).ffill().reindex(cal) for k, s in series.items()})
    df = df.dropna()
    if len(df) < 2:
        raise PortfolioUnavailable("у активов портфеля нет общей истории")
    return df


def _get(pid: str) -> dict:
    if pid not in PORTFOLIOS:
        raise KeyError(f"неизвестный портфель: {pid}")
    return PORTFOLIOS[pid]


def _backtest(pid: str, start=None, end=None, fallback: bool = True) -> tuple[BacktestResult, pd.DataFrame]:
    p = _get(pid)
    px = _prices(list(p["weights"]), fallback)
    cfg = BacktestConfig(weights=dict(p["weights"]), initial=INITIAL, rebalance=p["rebalance"],
                         start=str(pd.Timestamp(start).date()) if start is not None else None,
                         end=str(pd.Timestamp(end).date()) if end is not None else None)
    return run_backtest(px, cfg), px


def _load_file() -> Optional[dict]:
    js = public_data.load_file(FILE)
    return js if isinstance(js, dict) and isinstance(js.get("items"), dict) else None


def _use_file(source: str) -> Optional[dict]:
    if source not in ("auto", "file", "live"):
        raise ValueError(f"source: auto | file | live, получено {source!r}")
    if source == "live":
        return None
    js = _load_file()
    if js is None and source == "file":
        raise FileNotFoundError("нет public/data/portfolios.json: python -m scripts.collect_data --only portfolios")
    return js


# ---------------------------------------------------------------------------- стоимость
def portfolio_values(pid: str, start=None, end=None, source: str = "auto") -> pd.Series:
    """Стоимость 100 000 ₽, вложенных в портфель pid, по датам (name = pid).

    Живой расчёт — по дням: run_backtest с долями и ребалансировкой каталога, без комиссий и налогов,
    цены — core.data.universe (как страница бэктеста), начало — самая поздняя первая дата активов
    (или start). Из файла (source="auto" при наличии public/data/portfolios.json или "file") — месячные
    точки файла (последний торговый день месяца, первая и последняя даты), отрезанные по [start, end]
    и пересчитанные к 100 000 ₽ на первую точку; attrs["freq"] = "M" (живой ряд — "D" или "M" для демо).
    Нет данных по активам — PortfolioUnavailable."""
    _get(pid)
    js = _use_file(source)
    if js is not None and isinstance((js.get("series") or {}).get(pid), list):
        rows = js["series"][pid]
        s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows}, dtype=float).sort_index()
        if start is not None:
            s = s.loc[pd.Timestamp(start):]
        if end is not None:
            s = s.loc[:pd.Timestamp(end)]
        if s.empty:
            raise PortfolioUnavailable(f"{pid}: нет точек в выбранном периоде")
        s = s / s.iloc[0] * INITIAL
        s.name = pid
        s.attrs.update({"freq": "M", "source": "file", "updated": js.get("updated"),
                        "stale": bool((js["items"].get(pid) or {}).get("stale"))})
        return s
    if js is not None and source == "file":
        raise PortfolioUnavailable(f"{pid}: портфеля нет в public/data/portfolios.json")
    res, _ = _backtest(pid, start, end)
    s = res.equity.rename(pid)
    s.attrs.update({"freq": _freq(s), "source": "live", "updated": None, "stale": False})
    return s


def benchmark_values(pid: str, start=None, end=None, source: str = "auto") -> Optional[pd.Series]:
    """Стоимость 100 000 ₽ в бенчмарке портфеля на тех же датах, что portfolio_values(pid, start, end, source),
    только на общем отрезке (бенчмарк начался позже / закончился раньше — ряд короче), 100 000 ₽ на первую
    общую дату. attrs: key, aligned (True — бенчмарк есть на всех датах портфеля), from / to (общий отрезок),
    portfolio_from / portfolio_to, freq, source ('file' | 'live'), updated. Нет ряда или < 3 точек — None.
    Из файла — месячный ряд series_benchmark (те же даты, что series)."""
    p = _get(pid)
    pv = portfolio_values(pid, start, end, source)
    if pv.attrs.get("source") == "file":
        js = _use_file(source)
        rows = (js.get("series_benchmark") or {}).get(pid)
        if not isinstance(rows, list) or not rows:
            return None
        b = pd.Series({pd.Timestamp(d): float(v) for d, v in rows}, dtype=float).sort_index()
        b = b.reindex(pv.index).dropna()
        if len(b) < 3:
            return None
        b = _align_attrs((b / b.iloc[0] * INITIAL).rename(p["benchmark"]), pv.index, p["benchmark"])
    else:
        b = _bench_values(pid, pv.index)
        if b is None:
            return None
    b.attrs.update({"freq": pv.attrs.get("freq"), "source": pv.attrs.get("source"),
                    "updated": pv.attrs.get("updated")})
    return b


# ---------------------------------------------------------------------------- вспомогательные
def _clean(x: Any) -> Any:
    """JSON-совместимый вид: NaN/inf -> None, numpy -> python, Timestamp -> 'YYYY-MM-DD'."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(x).date())
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return round(x, 8) if math.isfinite(x) else None
    return x


def _freq(s: pd.Series) -> str:
    return "D" if m.infer_periods_per_year(s.index) == 252 else "M"


def _rate(start, end) -> tuple[Any, Optional[str]]:
    """Ряд безрисковой ставки за окно или (0.0, предупреждение), если ряд недоступен."""
    from core.data import risk_free
    try:
        rf = risk_free.rate_series(start, end)
        if rf.empty:
            raise ValueError("пустой ряд")
        return rf, None
    except Exception as e:  # noqa: BLE001
        return 0.0, (f"Безрисковая ставка недоступна ({type(e).__name__}): коэффициенты посчитаны при ставке 0 "
                     f"и завышены.")


def window_start(dates, window: str) -> Optional[str]:
    """Начало окна '1Y' / '5Y' / '10Y' (metrics.period_start: последняя дата ≤ конец − окно) или первая
    дата для 'MAX'; истории меньше окна — None."""
    return m.period_start(dates, "ALL" if window == "MAX" else window)


def _window_metrics(values: pd.Series, bench: Optional[pd.Series], window: str) -> Optional[dict]:
    """Коэффициенты compute_all за окно (None — истории меньше окна): поля METRIC_FIELDS, current_drawdown,
    from, to, rf (средняя годовая ставка на начала периодов), freq."""
    frm = window_start(values.index, window)
    if frm is None:
        return None
    v = values.loc[pd.Timestamp(frm):]
    f = _freq(values)
    if len(m.resample_prices(v, f)) < 3:
        return None
    b = bench.loc[pd.Timestamp(frm):] if bench is not None else None
    rf, warn = _rate(v.index[0], v.index[-1])
    rep = m.compute_all(v, b, rf, freq=f).to_dict()
    r = m.to_returns(m.resample_prices(v, f))
    out = {k: rep.get(k) for k in METRIC_FIELDS}
    out.update({"current_drawdown": m.current_drawdown(v)["depth"], "from": str(v.index[0].date()),
                "to": str(v.index[-1].date()), "rf": m.annual_rf(rf, r.index), "freq": f,
                "rf_warning": warn})
    return out


def _align_attrs(b: pd.Series, dates: pd.DatetimeIndex, key: str) -> pd.Series:
    """attrs бенчмарка: key, aligned (покрывает все даты портфеля), from / to (общий отрезок),
    portfolio_from / portfolio_to."""
    b.attrs.update({"key": key, "aligned": bool(b.index[0] == dates[0] and b.index[-1] == dates[-1]),
                    "from": str(b.index[0].date()), "to": str(b.index[-1].date()),
                    "portfolio_from": str(dates[0].date()), "portfolio_to": str(dates[-1].date())})
    return b


def _bench_values(pid: str, dates: pd.DatetimeIndex, fallback: bool = True) -> Optional[pd.Series]:
    """Бенчмарк на датах портфеля, 100 000 ₽ на первую общую дату. Значение на дату — последнее известное
    (ffill), но только на общем отрезке [max(начала), min(концов)]: за последней датой бенчмарка значения
    не протягиваются, до первой — не достраиваются. attrs — _align_attrs. Меньше 3 точек — None."""
    key = _get(pid)["benchmark"]
    try:
        b = _series(key, fallback)
    except Exception:  # noqa: BLE001
        return None
    if b.empty:
        return None
    d = dates[(dates >= b.index[0]) & (dates <= b.index[-1])]
    b = b.reindex(b.index.union(d)).ffill().reindex(d).dropna()
    if len(b) < 3:
        return None
    return _align_attrs((b / b.iloc[0] * INITIAL).rename(key), pd.DatetimeIndex(dates), key)


def _rebalance_log(res: BacktestResult, px: pd.DataFrame, limit: int = 50) -> list[dict]:
    """Ребалансировки: дата и доли до неё (дрейф с прошлой ребалансировки) -> целевые доли; последние limit."""
    out = []
    w = res.weights
    p = px.loc[w.index, w.columns]
    target = pd.Series(res.config.weights, dtype=float)
    target = target / target.sum()
    for d in res.rebalance_dates[-limit:]:
        i = w.index.get_loc(d)
        if i == 0:
            continue
        prev = w.iloc[i - 1] * (p.iloc[i] / p.iloc[i - 1])
        before = prev / prev.sum()
        out.append({"date": str(pd.Timestamp(d).date()),
                    "before": {k: float(v) for k, v in before.items()},
                    "after": {k: float(target.get(k, 0.0)) for k in w.columns}})
    return out


def _fund_rows(funds: list[str]) -> list[dict]:
    """Фонды с комиссией (доля годовых; RusETFs, резервно T-Invest), УК, СЧА (руб.), статусом торгов."""
    fees = public_data.etf_commissions()
    info = public_data.fund_info()
    out = []
    for t in funds:
        fee = fees.get(t) if len(fees) else None
        row = info.loc[t] if t in info.index else None
        out.append({
            "secid": t,
            "name": (row["name"] if row is not None and isinstance(row["name"], str) else None),
            "issuer": (row["issuer"] if row is not None and isinstance(row["issuer"], str) else None),
            "fee": float(fee) / 100 if fee is not None and pd.notna(fee) else None,
            "aum_rub": (float(row["aum_rub"]) if row is not None and pd.notna(row["aum_rub"]) else None),
            "trade_status": (row["trade_status"] if row is not None and isinstance(row["trade_status"], str)
                             else None),
        })
    return out


def replicate(pid: str) -> list[dict]:
    """«Как повторить»: [{asset, asset_name, asset_short, weight, funds: [{secid, name, issuer, fee (доля годовых) | None,
    aum_rub, trade_status}], note}] по активам портфеля; funds [] — прямого БПИФ нет."""
    p = _get(pid)
    return [{"asset": r["asset"], "asset_name": asset_name(r["asset"]),
             "asset_short": asset_short_name(r["asset"]), "weight": float(p["weights"][r["asset"]]),
             "funds": _fund_rows(r["funds"]), "note": r["note"]} for r in p["replicate"]]


def portfolio_fee(pid: str, rows: Optional[list[dict]] = None) -> tuple[Optional[float], float]:
    """(комиссия, покрытие): средневзвешенная по долям активов комиссия первого фонда каждого актива
    (доля годовых), нормированная на долю портфеля, для которой комиссия известна; покрытие — эта доля
    (0…1). Нет ни одной комиссии — (None, 0)."""
    rows = rows if rows is not None else replicate(pid)
    num = cov = 0.0
    for r in rows:
        fee = r["funds"][0]["fee"] if r["funds"] else None
        if fee is not None:
            num += r["weight"] * fee
            cov += r["weight"]
    return (num / cov if cov > 0 else None), cov


# ---------------------------------------------------------------------------- отчёт
def _compute_report(pid: str, fallback: bool = True) -> tuple[dict, pd.Series]:
    """Отчёт портфеля по живым данным и дневной ряд стоимости."""
    p = _get(pid)
    res, px = _backtest(pid, fallback=fallback)
    values = res.equity.rename(pid)
    bench = _bench_values(pid, values.index, fallback)
    rows = replicate(pid)
    fee, cov = portfolio_fee(pid, rows)
    metrics = {w: _window_metrics(values, bench, w) for w in WINDOWS}
    bmetrics = {w: (_window_metrics(bench, bench, w) if bench is not None else None) for w in WINDOWS}
    warnings = sorted({x["rf_warning"] for x in metrics.values() if x and x.get("rf_warning")})
    if any(k in _DEMO_KEYS for k in [*p["weights"], p["benchmark"]]):
        warnings.append("Часть источников недоступна: использованы демо-данные (месячные ряды).")
    one = p.get("one_fund")
    report = {
        "id": pid, "name": p["name"], "group": p["group"], "description": p["description"], "rules": p["rules"],
        "weights": dict(p["weights"]), "rebalance": p["rebalance"],
        "rebalance_label": REBALANCE_LABELS.get(p["rebalance"], p["rebalance"]),
        "benchmark": p["benchmark"], "benchmark_name": asset_name(p["benchmark"]),
        "assets": {k: asset_name(k) for k in p["weights"]},
        "assets_short": {k: asset_short_name(k) for k in p["weights"]},
        "benchmark_short": asset_short_name(p["benchmark"]),
        "current_weights": {k: float(v) for k, v in res.weights.iloc[-1].items()},
        "rebalance_log": _rebalance_log(res, px),
        "since": str(values.index[0].date()), "last_date": str(values.index[-1].date()),
        "initial": INITIAL, "final_value": float(values.iloc[-1]),
        "freq": _freq(values),
        "returns": {"portfolio": m.period_returns(values),
                    "benchmark": m.period_returns(bench) if bench is not None else None},
        "monthly": m.monthly_grid(values),
        "metrics": metrics, "benchmark_metrics": bmetrics,
        "drawdowns": {"top": m.top_drawdowns(values, 5), "current": m.current_drawdown(values),
                      "max": m.max_drawdown(values), "ulcer_index": m.ulcer_index(values)},
        "fee": fee, "fee_coverage": cov,
        "replicate": rows,
        "one_fund": ({"funds": _fund_rows(one["funds"]), "note": one["note"]} if one else None),
        "warnings": warnings,
    }
    return _clean(report), values


def portfolio_report(pid: str, source: str = "auto") -> dict:
    """Отчёт для страницы портфеля (все доли — в долях, не в процентах):
      id, name, group, description, rules, weights {актив: доля}, rebalance ('A'|'Q'|'M'|'none'),
      rebalance_label, benchmark, benchmark_name, benchmark_short, assets {актив: название},
      assets_short {актив: короткое название — asset_short_name};
      current_weights — фактические доли на последнюю дату (дрейф с последней ребалансировки);
      rebalance_log — [{date, before {актив: доля}, after {актив: доля}}] (последние 50);
      since / last_date — первая и последняя даты ряда ('YYYY-MM-DD'), initial (100 000), final_value;
      freq — 'D' | 'M' (частота ряда и доходностей коэффициентов);
      returns {portfolio, benchmark} — metrics.period_returns: {'1D','1W','1M','6M','YTD','1Y','3Y','5Y','10Y',
        'ALL': {ret, cagr, from} | None} (cagr — для 3Y/5Y/10Y и ALL длиннее года);
      monthly — metrics.monthly_grid (years, cells, yearTotal, medianByMonth);
      metrics / benchmark_metrics {'1Y','5Y','10Y','MAX': {total_return, cagr, volatility, sharpe, sortino,
        omega, calmar, martin, ulcer_index, max_drawdown, alpha, beta, r_squared, correlation, up_capture,
        down_capture, current_drawdown, from, to, rf, freq, rf_warning} | None (истории меньше окна)};
      drawdowns {top (5 эпизодов: depth, peak, trough, recovery, daysToTrough, daysToRecover), current,
        max, ulcer_index};
      fee — средневзвешенная комиссия первых фондов replicate (доля годовых) | None, fee_coverage — доля
        портфеля, для которой комиссия известна;
      replicate — см. replicate(); one_fund — готовый фонд с той же идеей | None; warnings — список строк;
      source — 'file' | 'live', updated — время сборки файла | None; stale / stale_date — отчёт перенесён
      из прежнего файла (последний сбор этого портфеля не удался), stale_date — его последняя дата.
    Из файла возвращается копия (copy.deepcopy). Значения NaN — None. Нет данных — PortfolioUnavailable."""
    _get(pid)
    js = _use_file(source)
    if js is not None:
        item = js["items"].get(pid)
        if item is not None and isinstance(item.get("report"), dict):
            rep = copy.deepcopy(item["report"])
            rep.update({"source": "file", "updated": js.get("updated"), "stale": bool(item.get("stale")),
                        "stale_date": item.get("stale_date")})
            return rep
        if source == "file":
            raise PortfolioUnavailable(f"{pid}: портфеля нет в public/data/portfolios.json "
                                       f"({(js.get('skipped') or {}).get(pid, 'не собран')})")
    rep = _compute_report(pid)[0]
    rep.update({"source": "live", "updated": None, "stale": False, "stale_date": None})
    return rep


def _table_row(rep: dict) -> dict:
    """Строка portfolios_table из отчёта."""
    y1 = (rep.get("metrics") or {}).get("1Y") or {}
    mx = (rep.get("metrics") or {}).get("MAX") or {}
    ret = (rep.get("returns") or {}).get("portfolio") or {}

    def g(k, f="ret"):
        return (ret.get(k) or {}).get(f)
    return {
        "name": rep["name"], "group": rep["group"], "since": rep["since"], "last_date": rep["last_date"],
        "ret_1d": g("1D"), "ytd": g("YTD"), "cagr_10y": g("10Y", "cagr"),
        "cagr_all": g("ALL", "cagr"),
        "max_drawdown": mx.get("max_drawdown"), "current_drawdown": mx.get("current_drawdown"),
        "fee": rep.get("fee"), "fee_coverage": rep.get("fee_coverage"),
        "sharpe": y1.get("sharpe"), "sortino": y1.get("sortino"), "omega": y1.get("omega"),
        "martin": y1.get("martin"), "calmar": y1.get("calmar"), "ulcer_index": y1.get("ulcer_index"),
        "volatility_1y": y1.get("volatility"), "benchmark": rep.get("benchmark"),
    }


def _rank_rows(rows: dict[str, dict], reports: dict[str, dict]) -> dict[str, dict]:
    """Ранг риск/доходность 0–100 (ranking.rank_items, логика рейтинга бумаг): перцентили Шарпа, Сортино,
    Омеги, Кальмара и Мартина за окно RANK_WINDOW (5 лет) среди портфелей с такой историей, rank — среднее
    доступных перцентилей. История короче окна — rank None (портфель в ранжировании не участвует: смешивать
    окна разной длины нечестно — короткая история целиком приходится на один режим ставок).
    rank_window — окно ранга, rank_detail — перцентили по коэффициентам."""
    items = {}
    for pid in rows:
        w = ((reports.get(pid) or {}).get("metrics") or {}).get(RANK_WINDOW)
        if w:
            items[pid] = {"class": "portfolio", **{k: w.get(k) for k in ranking.RANK_METRICS}}
    ranking.rank_items(items)
    for pid, r in rows.items():
        r["rank"] = items[pid]["score"] if pid in items else None
        r["rank_detail"] = items[pid]["rank"] if pid in items else None
        r["rank_window"] = RANK_WINDOW
    return rows


def _frame(rows: dict[str, dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_dict(rows, orient="index")
    df = df.reindex(columns=TABLE_COLUMNS)
    df.index.name = "id"
    for c in TABLE_COLUMNS:
        if c not in ("name", "group", "since", "last_date", "benchmark", "rank_window"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def portfolios_table(source: str = "auto") -> pd.DataFrame:
    """Листинг: строка на портфель (индекс — slug), колонки TABLE_COLUMNS:
      name, group, since, last_date; ret_1d (1 день), ytd, cagr_10y (None — истории < 10 лет), cagr_all;
      max_drawdown, current_drawdown (за всю историю, доли ≤ 0); fee (доля годовых), fee_coverage;
      sharpe, sortino, omega, martin, calmar, ulcer_index, volatility_1y — за 1 год;
      rank — ранг риск/доходность 0–100 (среднее перцентилей Шарпа, Сортино, Омеги, Кальмара, Мартина
      за 5 лет среди портфелей с историей ≥ 5 лет, иначе None — см. _rank_rows); rank_window — окно ранга
      ("5Y"); benchmark.
    stale — строка перенесена из прежнего файла (сбор этого портфеля не удался).
    attrs: skipped {slug: причина} (портфеля нет совсем), stale {slug: {stale_date, reason}} (строка перенесена
    из прежнего файла), source ('file' | 'live'), updated."""
    js = _use_file(source)
    if js is not None:
        rows = {pid: dict(it["row"]) for pid, it in js["items"].items()
                if pid in PORTFOLIOS and isinstance(it, dict) and isinstance(it.get("row"), dict)}
        df = _frame(rows)
        df["stale"] = [bool((js["items"].get(pid) or {}).get("stale")) for pid in df.index]
        df.attrs.update({"skipped": dict(js.get("skipped") or {}), "stale": dict(js.get("stale") or {}),
                         "updated": js.get("updated"), "source": "file"})
        return df
    rows, reps, skipped = {}, {}, {}
    for pid in PORTFOLIOS:
        try:
            reps[pid] = _compute_report(pid)[0]
            rows[pid] = _table_row(reps[pid])
        except PortfolioUnavailable as e:
            skipped[pid] = str(e)
    df = _frame(_rank_rows(rows, reps))
    df["stale"] = False
    df.attrs.update({"skipped": skipped, "stale": {}, "updated": None, "source": "live"})
    return df


def _risk_return(values: pd.Series, period: str) -> Optional[dict]:
    """Годовая доходность (CAGR за окно; 1Y — доходность за год) и волатильность (год.) за окно."""
    frm = window_start(values.index, period)
    if frm is None:
        return None
    v = values.loc[pd.Timestamp(frm):]
    f = _freq(values)
    r = m.to_returns(m.resample_prices(v, f))
    if len(r) < 2:
        return None
    return {"ret": m.cagr(v), "volatility": m.volatility(r, m.PERIODS[f]),
            "from": str(v.index[0].date()), "to": str(v.index[-1].date())}


def risk_return_points(period: str = "1Y", source: str = "auto") -> pd.DataFrame:
    """Точки «риск — доходность» за период '1Y' | '5Y' | '10Y': индекс — slug, колонки name, group,
    ret (годовая доходность: (P1/P0)^(365,25/дней) − 1, для 1Y ≈ доходность за год), volatility
    (годовая: ст. откл. доходностей × √n, n = 252 для дневных рядов), from, to.
    Портфели с историей короче периода не попадают."""
    if period not in RISK_RETURN_PERIODS:
        raise ValueError(f"period: {', '.join(RISK_RETURN_PERIODS)}")
    cols = ["name", "group", "ret", "volatility", "from", "to"]
    js = _use_file(source)
    if js is not None:
        pts = (js.get("risk_return") or {}).get(period) or []
        df = pd.DataFrame(pts, columns=["id", *cols]).set_index("id")
    else:
        recs = []
        for pid, p in PORTFOLIOS.items():
            try:
                pt = _risk_return(portfolio_values(pid, source="live"), period)
            except PortfolioUnavailable:
                continue
            if pt:
                recs.append({"id": pid, "name": p["name"], "group": p["group"], **pt})
        df = pd.DataFrame(recs, columns=["id", *cols]).set_index("id")
    for c in ("ret", "volatility"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df.attrs.update({"source": "file" if js is not None else "live",
                     "updated": js.get("updated") if js is not None else None})
    return df


def portfolio_since(pid: str, source: str = "auto") -> Optional[str]:
    """Начало истории портфеля (самая поздняя первая дата его активов), 'YYYY-MM-DD' | None (нет данных)."""
    p = _get(pid)
    js = _use_file(source)
    if js is not None:
        it = js["items"].get(pid) or {}
        return (it.get("row") or {}).get("since")
    try:
        return str(_prices(list(p["weights"])).index[0].date())
    except PortfolioUnavailable:
        return None


# ---------------------------------------------------------------------------- файл сборщика
def _monthly_points(values: pd.Series) -> list[list]:
    """Месячные точки: последний торговый день каждого месяца + первая дата ряда; 2 знака."""
    v = values.dropna()
    last = v.groupby([v.index.year, v.index.month]).tail(1)
    pts = pd.concat([v.iloc[:1], last])
    pts = pts[~pts.index.duplicated(keep="first")].sort_index()
    return [[str(d.date()), round(float(x), 2)] for d, x in pts.items()]


def build_file(fallback: bool = False, previous: Optional[dict] = None) -> tuple[dict, dict]:
    """Содержимое public/data/portfolios.json (без updated/source) и сводка {n, stale, skipped}:
      items {slug: {row (строка portfolios_table, с rank, rank_detail, rank_window), report (portfolio_report),
        stale?, stale_date?}}, series {slug: [[дата, стоимость], ...] месячные}, series_benchmark {slug: ...
        бенчмарк на тех же датах (общий отрезок), 100 000 ₽ на первую общую дату}, risk_return {'1Y'|'5Y'|'10Y':
        [{id, name, group, ret, volatility, from, to}]}, skipped {slug: причина} (портфеля в файле нет),
      stale {slug: {stale_date, reason}} (перенесён из прежнего файла), last (последняя дата рядов).
    fallback=False — без подмены демо-рядами при сбое источника (портфель пропускается).
    previous — прежний файл: пропущенные сейчас портфели, которые в нём есть, переносятся оттуда
    (item, series, series_benchmark, точки risk_return) с stale: true и stale_date — последней датой прежнего
    расчёта; такие портфели в skipped не попадают, а ведутся в stale {slug: {stale_date, reason}}.
    skipped — только портфели, которых в файле нет. Ранг — по полному набору (свежие + перенесённые)."""
    clear_cache()
    items, series, bseries, skipped, values = {}, {}, {}, {}, {}
    for pid in PORTFOLIOS:
        try:
            rep, v = _compute_report(pid, fallback=fallback)
        except PortfolioUnavailable as e:
            skipped[pid] = str(e)
            continue
        items[pid] = {"row": _table_row(rep), "report": rep}
        series[pid] = _monthly_points(v)
        b = _bench_values(pid, v.index, fallback)
        if b is not None:
            bseries[pid] = _monthly_points(b)
        values[pid] = v
    fresh = len(items)
    old_items = (previous or {}).get("items") if isinstance(previous, dict) else None
    stale_pts: dict[str, list] = {per: [] for per in RISK_RETURN_PERIODS}
    stale: dict[str, dict] = {}
    for pid in list(skipped):
        old = (old_items or {}).get(pid)
        if not (isinstance(old, dict) and isinstance(old.get("row"), dict) and isinstance(old.get("report"), dict)):
            continue
        sd = old.get("stale_date") or old["report"].get("last_date")
        items[pid] = {"row": dict(old["row"]), "report": old["report"], "stale": True, "stale_date": sd}
        stale[pid] = {"stale_date": sd, "reason": skipped.pop(pid)}
        for name, dst in (("series", series), ("series_benchmark", bseries)):
            rows = (previous.get(name) or {}).get(pid)
            if isinstance(rows, list):
                dst[pid] = rows
        for per in RISK_RETURN_PERIODS:
            stale_pts[per] += [pt for pt in ((previous.get("risk_return") or {}).get(per) or [])
                               if isinstance(pt, dict) and pt.get("id") == pid]
    rows = _rank_rows({pid: it["row"] for pid, it in items.items()},
                      {pid: it["report"] for pid, it in items.items()})
    for pid in items:
        items[pid]["row"] = _clean(rows[pid])
    order = [pid for pid in PORTFOLIOS if pid in items]
    items = {pid: items[pid] for pid in order}
    rr = {}
    for per in RISK_RETURN_PERIODS:
        pts = list(stale_pts[per])
        for pid, v in values.items():
            pt = _risk_return(v, per)
            if pt:
                pts.append(_clean({"id": pid, "name": PORTFOLIOS[pid]["name"], "group": PORTFOLIOS[pid]["group"],
                                   **pt}))
        rr[per] = sorted(pts, key=lambda x: order.index(x["id"]) if x["id"] in order else len(order))
    last = max((it["report"]["last_date"] for pid, it in items.items() if pid in values), default=None)
    body = {"initial": INITIAL, "groups": list(GROUPS), "items": items, "series": series,
            "series_benchmark": bseries, "risk_return": rr, "skipped": skipped, "stale": stale, "last": last}
    return body, {"n": fresh, "stale": len(stale), "skipped": skipped}
