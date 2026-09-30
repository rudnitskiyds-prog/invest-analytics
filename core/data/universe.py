"""
Каталог «базовых активов» для моделирования и бэктеста.

Любой ключ — это либо тикер ISS (акция, пай, облигация, металл, индекс),
либо специальный ряд (золото ЦБ, индекс RUONIA, склейка индексов).
Индексы-аналоги ПИФов — из табл. 3 НИР.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from functools import lru_cache
from typing import Callable, Optional

import pandas as pd

from core.config import ROOT
from core.data import cbr, iss


@dataclass
class AssetSpec:
    key: str
    name: str
    asset_class: str
    source: str               # 'iss' | 'cbr' | 'chain' | 'iss_tr'
    etf_analog: str = ""      # биржевой фонд, следующий за индексом
    note: str = ""


# Индексы-аналоги ПИФов (НИР, табл. 3) и прочие ключевые ряды
CATALOG: dict[str, AssetSpec] = {s.key: s for s in [
    AssetSpec("MCFTR", "Индекс МосБиржи полной доходности брутто", "Акции", "iss", "EQMX", "Бенчмарк НИР"),
    AssetSpec("IMOEX", "Индекс МосБиржи (ценовой)", "Акции", "iss", "", ""),
    AssetSpec("MEBCTR", "Индекс голубых фишек полной доходности", "Акции", "iss", "SBBC"),
    AssetSpec("IRDIVTR", "Индекс дивидендных акций полной доходности", "Акции", "iss", "DIVD"),
    AssetSpec("MOEXEU", "Индекс электроэнергетики", "Акции", "iss", "ОПИФ ГПБ-Электроэнергетика"),
    AssetSpec("RGBITR", "Индекс гособлигаций полной доходности", "Облигации гос.", "iss", "SBGB"),
    AssetSpec("RUGBITR1Y", "Гособлигации до 1 года (TR)", "Облигации гос.", "iss", "SUGB"),
    AssetSpec("RUGBITR5+", "Гособлигации 5+ лет (TR)", "Облигации гос.", "iss", "SBLB"),
    AssetSpec("RUGBITR10Y", "Гособлигации 10+ лет (TR)", "Облигации гос.", "iss", "AMGB"),
    AssetSpec("RUCBITR", "Корпоративные облигации (TR)", "Облигации корп.", "iss", "OBLG"),
    AssetSpec("RUCBTRNS", "Корпоративные облигации (TR, новая методика)", "Облигации корп.", "iss", "OBLG"),
    AssetSpec("CORP_CHAIN", "Корп. облигации: RUCBITR → RUCBTRNS (склейка)", "Облигации корп.", "chain", "OBLG",
              "Как в НИР: пересчёт базы при смене методики"),
    AssetSpec("GOLD_CBR", "Золото, учётная цена ЦБ РФ", "Золото", "cbr", "GOLD"),
    AssetSpec("GLDRUB_TOM", "Золото, биржевой (руб./г)", "Золото", "iss", "GOLD"),
    AssetSpec("RUONIA", "Индекс денежного рынка (накопленная RUONIA)", "Денежный рынок", "cbr", "LQDT"),
]}

CHAIN_SWITCH = {"CORP_CHAIN": ("RUCBITR", "RUCBTRNS", "2018-12-29")}


def chain(old: pd.Series, new: pd.Series, switch: str) -> pd.Series:
    """Склейка двух индексов: до даты — старый, после — новый, база пересчитана."""
    sw = pd.Timestamp(switch)
    new = new.dropna()
    old = old.dropna()
    anchor = new.index[new.index >= sw][0] if (new.index >= sw).any() else new.index[0]
    old_at = old.loc[:anchor].iloc[-1]
    scaled_new = new.loc[anchor:] * (old_at / new.loc[anchor])
    return pd.concat([old.loc[:anchor].iloc[:-1], scaled_new])




@lru_cache(maxsize=1)
def _offline_frame() -> pd.DataFrame:
    """Демо-режим без сети: месячные ряды 2010–2025 из tests/fixtures (ISS MOEX, ЦБ РФ)."""
    fx = ROOT / "tests" / "fixtures"
    df = None
    for f in ["iss_monthly.csv", "cbr_monthly.csv", "iss_monthly_extra.csv"]:
        part = pd.read_csv(fx / f, dtype={"m": str})
        df = part if df is None else df.merge(part, on="m")
    df.index = pd.to_datetime(df.pop("m"), format="%Y%m") + pd.offsets.MonthEnd(0)
    df["CORP_CHAIN"] = chain(df["RUCBITR"], df["RUCBTRNS"], "2018-12-29")
    return df


def load_series(key: str, start: str = "2008-01-01", end: Optional[str] = None,
                total_return: bool = False) -> pd.Series:
    if os.environ.get("IP_OFFLINE") == "1":
        df = _offline_frame()
        if key not in df:
            raise KeyError(f"{key}: нет в демо-данных (режим IP_OFFLINE=1)")
        s = df[key].loc[start:end].dropna()
        s.name = key
        return s
    spec = CATALOG.get(key)
    if spec is not None and spec.source == "cbr":
        s = cbr.gold_price(start, end) if key == "GOLD_CBR" else cbr.ruonia_index(start, end)
    elif spec is not None and spec.source == "chain":
        a, b, sw = CHAIN_SWITCH[key]
        s = chain(iss.close_series(a, start, end), iss.close_series(b, start, end), sw)
    elif total_return:
        s = iss.total_return_series(key, start, end)
    else:
        s = iss.close_series(key, start, end)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.name = key
    return s


def load_prices(keys: list[str], start: str = "2008-01-01", end: Optional[str] = None,
                total_return: bool = False, align: str = "common",
                progress: Optional[Callable[[str], None]] = None) -> tuple[pd.DataFrame, dict]:
    """
    Загружает ряды и выравнивает их на общий календарь торговых дней.
    align='common' — обрезка по самой поздней дате начала (все активы есть с первого дня).
    Возвращает (DataFrame, info{key: {'first': date, 'last': date, 'n': …}}).
    """
    series, info = {}, {}
    for k in keys:
        if progress:
            progress(k)
        s = load_series(k, start, end, total_return=total_return)
        if s.empty:
            info[k] = {"first": None, "last": None, "n": 0, "error": "нет данных"}
            continue
        series[k] = s
        info[k] = {"first": s.index[0], "last": s.index[-1], "n": len(s)}
    if not series:
        return pd.DataFrame(), info
    df = pd.DataFrame(series).sort_index().ffill()
    if align == "common":
        df = df.dropna()
    return df, info


def describe_catalog() -> pd.DataFrame:
    return pd.DataFrame([{
        "Ключ": s.key, "Название": s.name, "Класс": s.asset_class,
        "Фонд-аналог": s.etf_analog, "Источник": {"iss": "ISS MOEX", "cbr": "Банк России",
                                                 "chain": "ISS MOEX (склейка)", "iss_tr": "ISS MOEX"}[s.source],
        "Примечание": s.note} for s in CATALOG.values()])
