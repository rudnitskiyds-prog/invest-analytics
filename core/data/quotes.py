"""Текущие котировки в рублях за 1 бумагу + история цен для учёта портфеля."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from core.data import iss


def _board_quotes(board: str, market: str) -> pd.DataFrame:
    if market == "bonds":
        df = iss.bonds(board)
        df["price_rub"] = df["PRICE"] * df["FACEVALUE"] / 100 + df["ACCRUEDINT"].fillna(0)
        df["LOTSIZE"] = 1
        return df.rename(columns={"SHORTNAME": "name"})
    if market == "selt":
        df = iss.metals()
        df["price_rub"] = df["PRICE"]
        return df.rename(columns={"NAME": "name"})
    df = iss.shares(board)
    df["price_rub"] = df["PRICE"]
    return df.rename(columns={"SHORTNAME": "name"})


def get_quotes(secids: Iterable[str]) -> pd.DataFrame:
    """index=secid; столбцы: price, name, lot, board, market."""
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
            tbl = _board_quotes(board, market).set_index("SECID")
        except Exception:  # noqa: BLE001
            continue
        for s in items:
            if s in tbl.index:
                r = tbl.loc[s]
                rows.append({"secid": s, "price": float(r["price_rub"]), "name": r.get("name", s),
                             "lot": int(r.get("LOTSIZE", 1) or 1), "board": board, "market": market})
    return pd.DataFrame(rows).set_index("secid") if rows else \
        pd.DataFrame(columns=["price", "name", "lot", "board", "market"])


def price_history_rub(secids: Iterable[str], start: str, end: Optional[str] = None) -> pd.DataFrame:
    """История цен в рублях за бумагу (облигации: % × номинал / 100, без НКД)."""
    def one(s):
        info = iss.resolve(s)
        px = iss.close_series(s, start, end)
        if info["market"] == "bonds":
            fv = float(info.get("facevalue") or 1000)
            px = px * fv / 100
        return s, px
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for s, px in ex.map(one, list(secids)):
            out[s] = px
    return pd.DataFrame(out).sort_index().ffill()
