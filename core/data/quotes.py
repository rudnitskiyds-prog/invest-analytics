"""Текущие котировки в рублях за 1 бумагу + история цен для учёта портфеля.

Облигации с валютным номиналом (еврооблигации, замещающие, юаневые ОФЗ) пересчитываются
в рубли по официальному курсу ЦБ (core/data/cbr.py): цена — % от номинала в валюте FACEUNIT,
НКД — в валюте расчётов CURRENCYID. Для рублёвых бумаг курс = 1, сеть не нужна.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from core.data import cbr, iss

log = logging.getLogger(__name__)


def _board_quotes(board: str, market: str, secids: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """Котировки режима в рублях за 1 бумагу (price_rub) и валюта номинала (currency).
    secids — если задан, таблица сужается до этих бумаг до запроса курсов, поэтому ЦБ
    опрашивается только по валютам нужных облигаций (рублёвые — без обращения к ЦБ)."""
    if market == "bonds":
        df = iss.bonds(board)
        if secids is not None:
            df = df[df["SECID"].isin(set(secids))].copy()
        # курс ЦБ на сегодня — по одному запросу на валюту; при ошибке — NaN в price_rub
        rates = cbr.rates_on(set(df["FACE_CURRENCY"]) | set(df["SETTLE_CURRENCY"]))
        face_rate = df["FACE_CURRENCY"].map(rates).astype(float)
        settle_rate = df["SETTLE_CURRENCY"].map(rates).astype(float)
        # грязная цена, руб.: % × номинал / 100 × курс(номинал) + НКД × курс(расчёты)
        df["price_rub"] = (df["PRICE"] * df["FACEVALUE"] / 100 * face_rate
                           + df["ACCRUEDINT"].fillna(0) * settle_rate)
        df["currency"] = df["FACE_CURRENCY"]
        df["LOTSIZE"] = 1
        return df.rename(columns={"SHORTNAME": "name"})
    if market == "selt":
        df = iss.metals()
        df["price_rub"] = df["PRICE"]
        df["currency"] = "RUB"
        return df.rename(columns={"NAME": "name"})
    df = iss.shares(board)
    df["price_rub"] = df["PRICE"]
    df["currency"] = "RUB"
    return df.rename(columns={"SHORTNAME": "name"})


def get_quotes(secids: Iterable[str]) -> pd.DataFrame:
    """index=secid; столбцы: price (руб. за 1 бумагу, для облигаций — с НКД), name, lot,
    board, market, currency (валюта номинала облигации; 'RUB' для прочих бумаг).
    Если курс ЦБ для валютной облигации получить не удалось — price = NaN."""
    secids = sorted({s.upper() for s in secids if s})
    rows = []
    groups: dict[tuple, list] = {}
    for s in secids:
        try:
            info = iss.resolve(s)
        except Exception:  # noqa: BLE001
            continue
        groups.setdefault((info["board"], info["market"]), []).append(s)
    for (board, market), items in groups.items():
        try:
            tbl = _board_quotes(board, market, items).set_index("SECID")
        except Exception:  # noqa: BLE001
            continue
        for s in items:
            if s in tbl.index:
                r = tbl.loc[s]
                rows.append({"secid": s, "price": float(r["price_rub"]), "name": r.get("name", s),
                             "lot": int(r.get("LOTSIZE", 1) or 1), "board": board, "market": market,
                             "currency": r.get("currency", "RUB") if market == "bonds" else "RUB"})
    return pd.DataFrame(rows).set_index("secid") if rows else \
        pd.DataFrame(columns=["price", "name", "lot", "board", "market", "currency"])


def price_history_rub(secids: Iterable[str], start: str, end: Optional[str] = None) -> pd.DataFrame:
    """История цен в рублях за бумагу.

    Облигации: % × номинал / 100 (без НКД); при валютном номинале (faceunit ≠ RUB) ряд
    умножается на дневной официальный курс ЦБ (cbr.currency_rate), выровненный по датам
    итоговой таблицы: берётся последний курс, установленный на дату или ранее (reindex + ffill).
    Курс применяется после ffill цены, поэтому в дни без сделок рублёвая стоимость валютной
    бумаги следует за курсом. Номинал — текущий из описания ISS.

    Если курс валюты получить не удалось (ЦБ недоступен, неизвестная валюта), исключение
    не пробрасывается: столбцы бумаг этой валюты заполняются NaN (как price в get_quotes),
    в лог пишется предупреждение; рублёвые столбцы не затрагиваются.
    """
    def one(s):
        info = iss.resolve(s)
        px = iss.close_series(s, start, end)
        cur = "RUB"
        if info["market"] == "bonds":
            fv = float(info.get("facevalue") or 1000)
            px = px * fv / 100  # в валюте номинала
            cur = cbr.normalize_currency(info.get("faceunit"))
        return s, px, cur
    out, curs = {}, {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for s, px, cur in ex.map(one, list(secids)):
            out[s] = px
            curs[s] = cur
    df = pd.DataFrame(out).sort_index().ffill()
    for cur in {c for c in curs.values() if c != "RUB"}:
        cols = [s for s, c in curs.items() if c == cur and df[s].notna().any()]
        if not cols:
            continue
        first = df[cols].apply(lambda c: c.first_valid_index()).min()
        # начало — 1 января года (first − 14 дн.): одинаковые запросы → кэш переиспользуется
        r_start = f"{(first - pd.Timedelta(days=14)).year}-01-01"
        try:
            rate = cbr.currency_rate(cur, r_start, end)
        except Exception as e:  # noqa: BLE001
            log.warning("Нет курса ЦБ %s (%s): история %s в рублях не рассчитана (NaN)",
                        cur, e, ", ".join(cols))
            df[cols] = np.nan
            continue
        idx = rate.index.union(df.index)
        df[cols] = df[cols].mul(rate.reindex(idx).ffill().reindex(df.index), axis=0)
    return df
