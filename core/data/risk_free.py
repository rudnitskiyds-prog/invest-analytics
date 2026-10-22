"""
Безрисковая ставка платформы — ряд по датам, а не константа.

Методика (решение пользователя, 10.2026):
  * с первой даты индекса RUGBITR1Y (ISS: engine stock, market index; первая доходность — 11.01.2011) —
    доходность к погашению индекса коротких ОФЗ (до 1 года), поле YIELD истории ISS, % годовых;
  * до этой даты (splice_date) — RGBEY, эффективная доходность всего рынка ОФЗ ММВБ
    (ISS: engine state, market index, режим GNDX, поле CLOSE, % годовых; 30.12.2002 – 02.03.2012),
    склейка без поправки на спред между рядами;
  * до 30.12.2002 данных нет: ставка = первое известное значение RGBEY (bfill), describe() ставит признак
    before_data.
Нулевые и отрицательные значения (в RGBEY — сбойные нули в январе 2009 г. и 02.03.2012) отбрасываются:
на эти даты действует предыдущая публикация.

Источник — файл сборщика public/data/risk_free.json (scripts/collect_data.py, источник "risk_free";
значения — доля годовых). Нет файла — ряд собирается с ISS (core/data/iss.py, кэш и повторы);
в офлайн-режиме (IP_OFFLINE=1) без файла — исключение RiskFreeUnavailable (сеть не трогаем).

Ставки в функциях модуля — годовые, в ДОЛЯХ (0.1405 = 14,05 %). Ряд ступенчатый: значение действует
с даты публикации до следующей публикации.
"""
from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache
from typing import Optional

import pandas as pd

from core.data import public_data

FILE = "risk_free"
SHORT_ID = "RUGBITR1Y"          # индекс коротких ОФЗ (до 1 года), поле YIELD
LONG_ID = "RGBEY"               # эффективная доходность рынка ОФЗ ММВБ (архив, engine state), поле CLOSE
RGBEY_FIRST = "2002-12-30"      # первая дата RGBEY на ISS
LABEL = "доходность коротких ОФЗ (RUGBITR1Y)"
LABEL_RGBEY = "эффективная доходность рынка ОФЗ (RGBEY)"
TITLE = "Безрисковая ставка: доходность к погашению коротких ОФЗ (RUGBITR1Y), до 11.01.2011 — RGBEY"
UNIT = "доля годовых"
SOURCE = ("ISS MOEX: /history/engines/stock/markets/index/securities/RUGBITR1Y (YIELD, % годовых) + "
          "/history/engines/state/markets/index/securities/RGBEY (CLOSE, % годовых), значения / 100")


class RiskFreeUnavailable(RuntimeError):
    """Ряд безрисковой ставки недоступен: нет public/data/risk_free.json, а ISS недоступен или офлайн-режим."""


# ------------------------------------------------------------------ сборка ряда (ISS)
def build(end: Optional[str] = None) -> pd.Series:
    """Склейка RGBEY (до первой даты RUGBITR1Y) + RUGBITR1Y (YIELD) с ISS -> доля годовых, name="RF".

    attrs: splice_date (первая дата RUGBITR1Y с доходностью; None, если RGBEY пуст — склейки нет),
    rgbey_first, rgbey_till
    (последняя использованная дата RGBEY). Сеть — только через core/data/iss.py."""
    from core.data import iss

    short = iss.index_history(SHORT_ID, "2000-01-01", end, engine="stock", field="YIELD")
    short = short[short > 0]
    if short.empty:
        raise RiskFreeUnavailable("ISS не вернул доходность RUGBITR1Y")
    splice = short.index.min()
    long = iss.index_history(LONG_ID, "2000-01-01", splice.date().isoformat(), engine="state", field="CLOSE")
    if long.empty:                       # RGBEY не вернулся — ряд только из RUGBITR1Y
        long = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    else:
        long.index = pd.DatetimeIndex(long.index)
        long = long[(long > 0) & (long.index < splice)]
    s = pd.concat([long, short]).sort_index() if len(long) else short.sort_index()
    s = s[~s.index.duplicated(keep="last")] / 100.0
    s.name = "RF"
    s.attrs.update({
        # склейки нет, если RGBEY не вернулся: тогда splice_date = None
        "splice_date": splice.date().isoformat() if len(long) else None,
        "rgbey_first": long.index.min().date().isoformat() if len(long) else None,
        "rgbey_till": long.index.max().date().isoformat() if len(long) else None,
    })
    return s


def file_meta(s: pd.Series) -> dict:
    """Дополнительные поля risk_free.json по ряду build(): splice_date и sources."""
    a = s.attrs
    short_from = a.get("splice_date") or (s.index.min().date().isoformat() if len(s) else None)
    sources = [
            {"id": LONG_ID, "title": "Эффективная доходность гос. облигаций ММВБ (рынок ОФЗ)",
             "iss": "engine=state, market=index, board=GNDX, поле CLOSE (% годовых)",
             "from": a.get("rgbey_first"), "till": a.get("rgbey_till")},
            {"id": SHORT_ID, "title": "Индекс гособлигаций до 1 года, доходность к погашению",
             "iss": "engine=stock, market=index, поле YIELD (% годовых)",
             "from": short_from},
    ]
    if not a.get("rgbey_first"):
        sources = sources[1:]
    return {"splice_date": a.get("splice_date"), "sources": sources}


# ------------------------------------------------------------------ чтение
def _offline() -> bool:
    return os.environ.get("IP_OFFLINE") == "1"


@lru_cache(maxsize=4)
def _live(day: str) -> pd.Series:
    """Ряд с ISS, кэш на календарный день (ответы ISS дополнительно кэшируются в SQLite)."""
    return build()


def _load() -> tuple[pd.Series, dict]:
    """(полный ряд, метаданные {splice_date, first, source}). Файл сборщика -> ISS -> исключение."""
    js = public_data.load_file(FILE)
    rows = js.get("data") if isinstance(js, dict) else None
    if isinstance(rows, list) and rows:
        s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows if d and v is not None}, dtype=float)
        s = s[s > 0].sort_index()
        if not s.empty:
            s.name = "RF"
            return s, {"splice_date": js.get("splice_date"), "source": js.get("source", SOURCE),
                       "updated": js.get("updated")}
    if _offline():
        raise RiskFreeUnavailable("нет public/data/risk_free.json, а в офлайн-режиме (IP_OFFLINE=1) "
                                  "ряд с ISS не загружается: запустите python -m scripts.collect_data --only risk_free")
    try:
        s = _live(dt.date.today().isoformat())
    except RiskFreeUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise RiskFreeUnavailable(f"нет public/data/risk_free.json и ISS недоступен: {e}") from e
    return s, {"splice_date": s.attrs.get("splice_date"), "source": SOURCE, "updated": None}


def _full() -> pd.Series:
    return _load()[0]


def _splice_date() -> Optional[str]:
    """Дата склейки RGBEY -> RUGBITR1Y или None, если ряд без RGBEY."""
    return _load()[1].get("splice_date")


def rate_series(start=None, end=None) -> pd.Series:
    """Годовая безрисковая ставка в ДОЛЯХ по датам публикации на [start, end], name="RF".

    Если start не совпадает с датой публикации, первой точкой ряда ставится start со ставкой,
    действовавшей на эту дату (последняя публикация ≤ start); если start раньше первой даты ряда
    (30.12.2002) — значением первой даты (bfill). Ряд пригоден как rf для core/analytics/metrics.py
    (per_period_rate берёт ставку на начало каждого периода доходности).
    Нет файла и ISS недоступен / офлайн — RiskFreeUnavailable."""
    s = _full()
    a = pd.Timestamp(start) if start is not None else None
    z = pd.Timestamp(end) if end is not None else None
    out = s.loc[a:z] if (a is not None or z is not None) else s.copy()
    if a is not None and (z is None or a <= z) and a not in out.index:
        prev = s.loc[:a]
        v = float(prev.iloc[-1]) if len(prev) else float(s.iloc[0])
        out = pd.concat([pd.Series([v], index=pd.DatetimeIndex([a])), out])
    out = out.astype(float)
    out.index = pd.DatetimeIndex(out.index)
    out.name = "RF"
    return out


def time_mean(rate: pd.Series, start, end) -> float:
    """Средняя по времени (по календарным дням) годовая ставка ступенчатого ряда на [start, end]:
    значение действует до следующей даты (ffill); до первой даты ряда — первое значение (bfill).
    Пустой ряд или пустой интервал — NaN. (Стандартное определение средней по времени.)"""
    s = rate.dropna().astype(float).sort_index()
    if s.empty:
        return float("nan")
    days = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    if not len(days):
        return float("nan")
    daily = s.reindex(s.index.union(days)).ffill().bfill().reindex(days)
    return float(daily.mean())


def mean_rate(start, end) -> float:
    """Средняя по календарным дням безрисковая ставка (доля годовых) за [start, end] — time_mean
    по полному ряду. Её передают скаляром как rf в core/analytics/frontier.estimate_inputs и как
    справочную ставку окна."""
    return time_mean(_full(), start, end)


def describe(start=None, end=None) -> dict:
    """Сводка для подписей интерфейса за [start, end] (None — весь ряд):
      mean        — средняя ставка по календарным дням (доля);
      last        — ставка, действующая на end (последняя публикация ≤ end; доля), last_date — её дата;
      first_date  — первая дата ряда (30.12.2002);
      splice_date — первая дата RUGBITR1Y; раньше неё — RGBEY (None — склейки нет, ряд только RUGBITR1Y);
      uses_rgbey  — период захватывает даты до splice_date (без склейки — False);
      before_data — start раньше первой даты ряда (ставка достроена первым значением);
      label       — текст источника для подписи; source — источник данных."""
    s, meta = _load()
    first = s.index.min()
    a = pd.Timestamp(start) if start is not None else first
    z = pd.Timestamp(end) if end is not None else s.index.max()
    splice = meta.get("splice_date")
    upto = s.loc[:z]
    last_v, last_d = (float(upto.iloc[-1]), upto.index[-1]) if len(upto) else (float(s.iloc[0]), first)
    uses_rgbey = splice is not None and a < pd.Timestamp(splice)
    before = a < first
    label = LABEL
    if uses_rgbey:
        label += f"; до {pd.Timestamp(splice).strftime('%d.%m.%Y')} — {LABEL_RGBEY}"
    if before:
        label += f"; до {first.strftime('%d.%m.%Y')} — первое известное значение"
    return {
        "mean": mean_rate(a, z),
        "last": last_v,
        "last_date": last_d.date().isoformat(),
        "first_date": first.date().isoformat(),
        "splice_date": splice,
        "uses_rgbey": bool(uses_rgbey),
        "before_data": bool(before),
        "label": label,
        "source": meta.get("source", SOURCE),
    }


def current() -> tuple[float, str]:
    """(последняя ставка — доля годовых, её дата YYYY-MM-DD)."""
    s = _full()
    return float(s.iloc[-1]), s.index[-1].date().isoformat()
