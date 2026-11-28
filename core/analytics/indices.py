"""Раздел «Индексы» сайта (public/data/indices.json): карточка индекса — описание, месячный ряд (база 100),
метрики и БПИФ-аналоги. Состав — core/data/universe.py: CATALOG; метрики — core/analytics/metrics.py.
Используется сборщиком (scripts/collect_data.py, шаг indices) и генератором демо-файла (по рядам tests/fixtures)."""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd

from core.analytics import metrics as M
from core.data.universe import CATALOG

SERIES_YEARS = 10            # окно месячного ряда в файле
MIN_MONTHS_VOL = 12          # волатильность за год — по 12 месячным доходностям

# ключ -> (короткое имя, название, ценовой?, описание простыми словами); порядок — порядок в файле.
# RUCBITR/RUCBTRNS не показываем: CORP_CHAIN — их склейка
INDEX_INFO: dict[str, tuple[str, str, bool, str]] = {
    "MCFTR": ("МосБиржи полной доходности", "Индекс МосБиржи полной доходности", False,
              "Корзина крупнейших российских акций; учитывает не только рост цен, но и выплаченные дивиденды."),
    "IMOEX": ("МосБиржи", "Индекс МосБиржи", True,
              "Главный индекс российского рынка акций: показывает изменение цен крупнейших компаний, без дивидендов."),
    "MEBCTR": ("Голубые фишки", "Индекс голубых фишек полной доходности", False,
               "Корзина самых крупных и ликвидных акций; с учётом дивидендов."),
    "IRDIVTR": ("Дивидендные акции", "Индекс дивидендных акций полной доходности", False,
                "Акции компаний, которые стабильно платят высокие дивиденды; выплаты реинвестируются."),
    "MOEXEU": ("Электроэнергетика", "Индекс электроэнергетики", True,
               "Акции российских энергетических компаний: генерация, сети, сбыт; только изменение цен."),
    "MEEUTR": ("Электроэнергетика, полная доходность", "Индекс электроэнергетики полной доходности", False,
               "Те же энергетические компании, но с учётом выплаченных дивидендов."),
    "RGBITR": ("Гособлигации", "Индекс гособлигаций полной доходности", False,
               "Корзина облигаций федерального займа (ОФЗ) разных сроков; купоны реинвестируются."),
    "RUGBITR1Y": ("Гособлигации до 1 года", "Гособлигации до 1 года", False,
                  "ОФЗ со сроком до погашения менее года: почти не реагируют на изменения ставок, как «депозит»."),
    "RUGBITR5+": ("Гособлигации 5+ лет", "Гособлигации 5+ лет", False,
                  "Длинные ОФЗ: сильнее всего растут при снижении ставок и падают при их росте."),
    "RUGBITR10Y": ("Гособлигации 10+ лет", "Гособлигации 10+ лет", False,
                   "Самые длинные ОФЗ: высокая чувствительность к ключевой ставке."),
    "CORP_CHAIN": ("Корпоративные облигации", "Корпоративные облигации", False,
                   "Корзина облигаций российских компаний; доходность выше ОФЗ, но есть риск эмитента."),
    "GOLD_CBR": ("Золото", "Золото, учётная цена Банка России", True,
                 "Цена грамма золота в рублях по официальному курсу Банка России."),
    "GLDRUB_TOM": ("Золото (биржа)", "Золото, биржевая цена", True,
                   "Биржевая цена грамма золота в рублях; следует за мировой ценой и курсом доллара."),
    "RUONIA": ("Денежный рынок", "Индекс денежного рынка (накопленная RUONIA)", False,
               "Как если бы деньги лежали под ставку межбанковского рынка: низкий риск и небольшая доходность."),
}

# приблизительные соответствия индекс -> БПИФ (по названиям и описаниям фондов в rusetfs_funds.json)
ETF_NOTES = {
    "RUGBITR1Y": "у фонда SUGB дюрация 1–3 года, у индекса — до 1 года",
    "RUGBITR5+": "SBLB — долгосрочные ОФЗ, срок может отличаться от 5+ лет индекса",
    "RUGBITR10Y": "AMGB — длинные ОФЗ, срок может отличаться от 10+ лет индекса",
    "RUONIA": "LQDT вкладывает в краткосрочное РЕПО, близко к ставке RUONIA, но не копирует индекс",
    "MEBCTR": "SBBC — высоколиквидные акции, состав может отличаться от индекса голубых фишек",
    "IRDIVTR": "DIVD — дивидендные акции, состав может отличаться от индекса",
}


def monthly(s: pd.Series) -> pd.Series:
    """Последнее значение каждого месяца (с реальной датой наблюдения)."""
    s = s.dropna().astype(float).sort_index()
    return s.groupby(s.index.to_period("M")).tail(1)


def _etf(analog: str, names: dict[str, str]) -> list[dict]:
    """БПИФ-аналоги из поля каталога: только биржевые тикеры (ОПИФ и пустое поле пропускаем)."""
    return [{"secid": t, "name": names.get(t, t)}
            for t in analog.replace(",", " ").split() if t.isascii() and t.isalnum() and t.isupper()]


def _num(x: Optional[float], digits: int) -> Optional[float]:
    return None if x is None or pd.isna(x) else round(float(x), digits)


def build_item(key: str, raw: pd.Series, names: dict[str, str]) -> dict:
    """Карточка индекса по ряду значений (любой частоты)."""
    m = monthly(raw)
    if len(m) < 2:
        raise ValueError("ряд короче двух точек")
    short, name, price, desc = INDEX_INFO[key]
    spec = CATALOG[key]
    pr = M.period_returns(raw.dropna().astype(float).sort_index())   # по исходному (дневному) ряду
    win = m[m.index >= m.index[-1] - pd.DateOffset(years=SERIES_YEARS)]
    norm = win / win.iloc[0] * 100
    rets = M.to_returns(m).tail(MIN_MONTHS_VOL)
    return {
        "key": key, "name": name, "short": short, "class": spec.asset_class,
        "kind": "price" if price else "total_return", "description": desc,
        "since": m.index[0].strftime("%Y-%m-%d"),
        "series": [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in norm.items()],
        "ret_ytd": _num((pr["YTD"] or {}).get("ret"), 4), "ret_1y": _num((pr["1Y"] or {}).get("ret"), 4),
        "cagr_5y": _num((pr["5Y"] or {}).get("cagr"), 4), "cagr_10y": _num((pr["10Y"] or {}).get("cagr"), 4),
        "max_drawdown": _num(M.max_drawdown(win), 4),
        "volatility_1y": _num(M.volatility(rets, 12), 4) if len(rets) >= MIN_MONTHS_VOL else None,
        "etf": _etf(spec.etf_analog, names),
        **({"etf_note": ETF_NOTES[key]} if key in ETF_NOTES and _etf(spec.etf_analog, names) else {}),
    }


def build(load: Callable[[str], pd.Series], names: dict[str, str]) -> tuple[dict, dict]:
    """({'as_of', 'items'}, {key: причина пропуска}); load(key) -> ряд значений индекса."""
    items, skipped = [], {}
    for key in INDEX_INFO:
        try:
            items.append(build_item(key, load(key), names))
        except Exception as e:  # noqa: BLE001
            skipped[key] = f"{type(e).__name__}: {str(e)[:200]}"
    as_of = max((it["series"][-1][0] for it in items), default=None)
    return {"as_of": as_of, "items": items}, skipped
