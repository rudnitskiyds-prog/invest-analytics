"""
Витрина данных по эмитентам и бумагам.

Для каждой акции / пая: котировка, изменение за день, 1М / 3М / YTD / 1Г,
волатильность, Шарп, Сортино, бета к бенчмарку, макс. просадка за 1 год,
капитализация, дивидендная доходность (12 мес.) и мультипликаторы.

Мультипликаторы (P/E, P/B, P/S, EV/EBITDA, ROE, ND/EBITDA) ISS не публикует —
считаются по файлу data/fundamentals.csv (финансовые показатели эмитента:
чистая прибыль, выручка, EBITDA, капитал, чистый долг — млрд руб., МСФО LTM).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.analytics import metrics as m
from core.config import FUNDAMENTALS_CSV
from core.data import iss

FUND_COLUMNS = ["secid", "period", "net_income", "revenue", "ebitda", "equity", "net_debt",
                "shares_out", "source"]


def load_fundamentals() -> pd.DataFrame:
    if not FUNDAMENTALS_CSV.exists():
        return pd.DataFrame(columns=FUND_COLUMNS)
    df = pd.read_csv(FUNDAMENTALS_CSV)
    return df.sort_values("period").groupby("secid").tail(1).set_index("secid")


def multiples(cap_bln: pd.Series, fund: pd.DataFrame) -> pd.DataFrame:
    f = fund.reindex(cap_bln.index)
    ev = cap_bln + f["net_debt"]
    out = pd.DataFrame(index=cap_bln.index)
    out["P/E"] = cap_bln / f["net_income"].where(f["net_income"] > 0)
    out["P/B"] = cap_bln / f["equity"].where(f["equity"] > 0)
    out["P/S"] = cap_bln / f["revenue"].where(f["revenue"] > 0)
    out["EV/EBITDA"] = ev / f["ebitda"].where(f["ebitda"] > 0)
    out["ND/EBITDA"] = f["net_debt"] / f["ebitda"].where(f["ebitda"] > 0)
    out["ROE"] = f["net_income"] / f["equity"].where(f["equity"] > 0)
    return out


def _stats_one(secid: str, bench_r: pd.Series, rf: float, start: str) -> dict:
    try:
        px = iss.close_series(secid, start)
    except Exception:  # noqa: BLE001
        return {"SECID": secid}
    if len(px) < 30:
        return {"SECID": secid}
    last = px.iloc[-1]
    t = px.index[-1]

    def ret(days=None, ytd=False):
        ref_date = pd.Timestamp(year=t.year, month=1, day=1) if ytd else t - pd.Timedelta(days=days)
        base = px.loc[:ref_date]
        return float(last / base.iloc[-1] - 1) if len(base) else np.nan

    y1 = px.loc[t - pd.Timedelta(days=365):]
    r = m.to_returns(y1)
    rb = bench_r.reindex(r.index).dropna()
    return {
        "SECID": secid,
        "1М": ret(30), "3М": ret(91), "YTD": ret(ytd=True), "1Г": ret(365),
        "Волатильность": m.volatility(r, 252),
        "Шарп": m.sharpe(r, rf, 252), "Сортино": m.sortino(r, rf, 252),
        "Бета": m.beta(r.loc[rb.index], rb) if len(rb) > 30 else np.nan,
        "Макс. просадка 1Г": m.max_drawdown(y1),
    }


def dividend_yield_12m(secid: str, price: float) -> float:
    try:
        dv = iss.dividends(secid)
    except Exception:  # noqa: BLE001
        return np.nan
    if dv.empty or not price:
        return np.nan
    recent = dv[dv["registryclosedate"] >= pd.Timestamp.today() - pd.Timedelta(days=365)]
    return float(recent["value"].sum() / price) if len(recent) else 0.0


def shares_showcase(benchmark: str = "IMOEX", rf: float = 0.16, top: int = 80,
                    board: str = "TQBR", with_dividends: bool = True,
                    progress: Optional[Callable[[float], None]] = None) -> pd.DataFrame:
    base = iss.stocks(board)
    base = base[base["PRICE"].notna()].sort_values("VALTODAY_RUR", ascending=False).head(top)
    start = (pd.Timestamp.today() - pd.Timedelta(days=400)).date().isoformat()
    bench_r = m.to_returns(iss.close_series(benchmark, start))
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_stats_one, s, bench_r, rf, start) for s in base["SECID"]]
        for i, f in enumerate(futs):
            rows.append(f.result())
            if progress:
                progress((i + 1) / len(futs))
    st = pd.DataFrame(rows)
    df = base[["SECID", "SHORTNAME", "PRICE", "LASTTOPREVPRICE", "VALTODAY_RUR", "CAP", "LISTLEVEL"]] \
        .merge(st, on="SECID", how="left")
    df["Кап., млрд"] = df["CAP"] / 1e9
    if with_dividends:
        with ThreadPoolExecutor(max_workers=8) as ex:
            dy = list(ex.map(dividend_yield_12m, df["SECID"], df["PRICE"]))
        if pd.Series(dy, dtype=float).notna().any():
            df["Див. доходность"] = dy
    fund = load_fundamentals()
    if not fund.empty:
        mult = multiples(df.set_index("SECID")["Кап., млрд"], fund)
        df = df.merge(mult, left_on="SECID", right_index=True, how="left")
    return df.rename(columns={"SHORTNAME": "Название", "PRICE": "Цена",
                              "LASTTOPREVPRICE": "Изм. день, %", "VALTODAY_RUR": "Оборот, руб.",
                              "LISTLEVEL": "Листинг"}).drop(columns=["CAP"])


def etf_showcase(benchmark: str = "IMOEX", rf: float = 0.16) -> pd.DataFrame:
    base = iss.etfs()
    base = base[base["PRICE"].notna()]
    start = (pd.Timestamp.today() - pd.Timedelta(days=400)).date().isoformat()
    bench_r = m.to_returns(iss.close_series(benchmark, start))
    with ThreadPoolExecutor(max_workers=8) as ex:
        st = pd.DataFrame(list(ex.map(lambda s: _stats_one(s, bench_r, rf, start), base["SECID"])))
    df = base[["SECID", "SHORTNAME", "FUNDTYPE", "PRICE", "LASTTOPREVPRICE", "VALTODAY_RUR"]] \
        .merge(st, on="SECID", how="left")
    return df.rename(columns={"SHORTNAME": "Название", "FUNDTYPE": "Тип", "PRICE": "Цена",
                              "LASTTOPREVPRICE": "Изм. день, %", "VALTODAY_RUR": "Оборот, руб."})


def bonds_showcase(board: str = "TQOB") -> pd.DataFrame:
    df = iss.bonds(board)
    ytm_col = "EFFECTIVEYIELD" if "EFFECTIVEYIELD" in df else "YIELD"
    out = df[["SECID", "SHORTNAME", "PRICE", "COUPONPERCENT", "NEXTCOUPON", "MATDATE",
              "YEARS_TO_MAT", "DURATION_Y", "ACCRUEDINT", "VALTODAY_RUR", "LISTLEVEL"]].copy()
    out["Доходность, %"] = pd.to_numeric(df[ytm_col], errors="coerce").fillna(pd.to_numeric(df["YIELD"], errors="coerce"))
    # модифицированная дюрация: D / (1 + y)
    out["Мод. дюрация"] = out["DURATION_Y"] / (1 + out["Доходность, %"] / 100)
    return out.rename(columns={"SHORTNAME": "Название", "PRICE": "Цена, %", "COUPONPERCENT": "Купон, %",
                               "NEXTCOUPON": "След. купон", "MATDATE": "Погашение",
                               "YEARS_TO_MAT": "Лет до погаш.", "DURATION_Y": "Дюрация, лет",
                               "ACCRUEDINT": "НКД", "VALTODAY_RUR": "Оборот, руб.", "LISTLEVEL": "Листинг"})
