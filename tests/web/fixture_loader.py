"""Загрузка месячной эталонной фикстуры (tests/fixtures: индексы ISS MOEX и ряды Банка России на конец месяца)
в один DataFrame — вход генератора эталонов tests/web/make_reference.py (и loadDemoFrame в web/lib/data.js).

Раньше жила в scripts/reproduce_nir.py (удалён вместе со сверкой с табл. 4 НИР)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.data.universe import chain

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture() -> pd.DataFrame:
    a = pd.read_csv(FIX / "iss_monthly.csv", dtype={"m": str})
    b = pd.read_csv(FIX / "cbr_monthly.csv", dtype={"m": str})
    c = pd.read_csv(FIX / "iss_monthly_extra.csv", dtype={"m": str})
    df = a.merge(b, on="m").merge(c, on="m")
    df.index = pd.to_datetime(df.pop("m"), format="%Y%m") + pd.offsets.MonthEnd(0)
    df["CORP_CHAIN"] = chain(df["RUCBITR"], df["RUCBTRNS"], "2018-12-29")
    return df
