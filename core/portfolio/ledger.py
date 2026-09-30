"""
Учёт текущего портфеля (SQLite).

Сущности:
  portfolios   — портфель: название, бенчмарк, целевые доли, правило ребалансировки;
  transactions — операции: BUY, SELL, DIVIDEND, COUPON, DEPOSIT, WITHDRAW, FEE, TAX.

Цена в операциях — в рублях за 1 бумагу (для облигаций — с учётом НКД,
как в брокерском отчёте). Позиции считаются по средней цене приобретения.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from core.config import PORTFOLIO_DB

TX_TYPES = {
    "BUY": "Покупка", "SELL": "Продажа", "DIVIDEND": "Дивиденд", "COUPON": "Купон",
    "DEPOSIT": "Пополнение", "WITHDRAW": "Вывод", "FEE": "Комиссия", "TAX": "Налог",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created TEXT DEFAULT (date('now')),
    benchmark TEXT DEFAULT 'MCFTR',
    target TEXT DEFAULT '{}',
    rebalance TEXT DEFAULT 'A',
    band REAL DEFAULT 0.05,
    last_rebalance TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    type TEXT NOT NULL,
    secid TEXT,
    qty REAL DEFAULT 0,
    price REAL DEFAULT 0,
    amount REAL DEFAULT 0,
    fee REAL DEFAULT 0,
    note TEXT
);
CREATE INDEX IF NOT EXISTS ix_tx_pf ON transactions(portfolio_id, date);
"""


class Ledger:
    def __init__(self, path=PORTFOLIO_DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._c() as c:
            c.executescript(SCHEMA)

    def _c(self):
        c = sqlite3.connect(self.path)
        c.execute("PRAGMA foreign_keys = ON")
        return c

    # ---------------------------------------------------------------- portfolios
    def create_portfolio(self, name: str, benchmark: str = "MCFTR", target: Optional[dict] = None,
                         rebalance: str = "A", band: float = 0.05) -> int:
        with self._c() as c:
            cur = c.execute("INSERT INTO portfolios(name, benchmark, target, rebalance, band) VALUES (?,?,?,?,?)",
                            (name, benchmark, json.dumps(target or {}), rebalance, band))
            return cur.lastrowid

    def update_portfolio(self, pid: int, **fields) -> None:
        if "target" in fields:
            fields["target"] = json.dumps(fields["target"])
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._c() as c:
            c.execute(f"UPDATE portfolios SET {sets} WHERE id=?", (*fields.values(), pid))

    def delete_portfolio(self, pid: int) -> None:
        with self._c() as c:
            c.execute("DELETE FROM portfolios WHERE id=?", (pid,))

    def portfolios(self) -> pd.DataFrame:
        with self._c() as c:
            df = pd.read_sql("SELECT * FROM portfolios ORDER BY id", c)
        df["target"] = df["target"].map(lambda s: json.loads(s or "{}"))
        return df

    def portfolio(self, pid: int) -> dict:
        df = self.portfolios()
        row = df[df["id"] == pid]
        if row.empty:
            raise KeyError(pid)
        return row.iloc[0].to_dict()

    # ---------------------------------------------------------------- transactions
    def add(self, pid: int, date: str, type_: str, secid: Optional[str] = None, qty: float = 0,
            price: float = 0, amount: Optional[float] = None, fee: float = 0, note: str = "") -> int:
        type_ = type_.upper()
        if type_ not in TX_TYPES:
            raise ValueError(f"Неизвестный тип операции {type_}")
        if amount is None:
            amount = qty * price
        with self._c() as c:
            cur = c.execute(
                "INSERT INTO transactions(portfolio_id,date,type,secid,qty,price,amount,fee,note) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, str(pd.Timestamp(date).date()), type_, (secid or "").upper() or None,
                 float(qty), float(price), float(amount), float(fee), note))
            return cur.lastrowid

    def delete_tx(self, tx_id: int) -> None:
        with self._c() as c:
            c.execute("DELETE FROM transactions WHERE id=?", (tx_id,))

    def transactions(self, pid: int) -> pd.DataFrame:
        with self._c() as c:
            df = pd.read_sql("SELECT * FROM transactions WHERE portfolio_id=? ORDER BY date, id", c,
                             params=(pid,))
        df["date"] = pd.to_datetime(df["date"])
        return df

    def import_csv(self, pid: int, df: pd.DataFrame) -> int:
        """Столбцы: date, type, secid, qty, price, amount(опц.), fee(опц.), note(опц.)."""
        n = 0
        for _, r in df.iterrows():
            self.add(pid, r["date"], r["type"], r.get("secid"), r.get("qty", 0) or 0,
                     r.get("price", 0) or 0,
                     None if pd.isna(r.get("amount", np.nan)) else r.get("amount"),
                     r.get("fee", 0) or 0, str(r.get("note", "") or ""))
            n += 1
        return n


# -------------------------------------------------------------------- calculations
def cash_flow(tx: pd.DataFrame) -> pd.Series:
    """Изменение денежного остатка по каждой операции."""
    sign = {"BUY": -1, "SELL": 1, "DIVIDEND": 1, "COUPON": 1, "DEPOSIT": 1,
            "WITHDRAW": -1, "FEE": -1, "TAX": -1}
    return tx.apply(lambda r: sign[r["type"]] * abs(r["amount"]) - (r["fee"] or 0), axis=1)


def external_flows(tx: pd.DataFrame) -> pd.Series:
    """Внешние потоки инвестора (+ пополнение, − вывод) по датам."""
    f = tx[tx["type"].isin(["DEPOSIT", "WITHDRAW"])]
    s = f.apply(lambda r: abs(r["amount"]) * (1 if r["type"] == "DEPOSIT" else -1), axis=1)
    return s.groupby(f["date"]).sum() if len(f) else pd.Series(dtype=float)


@dataclass
class Position:
    secid: str
    qty: float
    avg_cost: float
    invested: float
    realized: float
    income: float


def positions(tx: pd.DataFrame) -> tuple[list[Position], float]:
    """Позиции по средней цене + денежный остаток."""
    book: dict[str, Position] = {}
    for _, r in tx.iterrows():
        s = r["secid"]
        if r["type"] in ("BUY", "SELL", "DIVIDEND", "COUPON") and s:
            p = book.setdefault(s, Position(s, 0.0, 0.0, 0.0, 0.0, 0.0))
            if r["type"] == "BUY":
                cost = abs(r["amount"]) + (r["fee"] or 0)
                p.invested += cost
                p.qty += r["qty"]
                p.avg_cost = p.invested / p.qty if p.qty else 0
            elif r["type"] == "SELL":
                q = min(r["qty"], p.qty)
                proceeds = abs(r["amount"]) - (r["fee"] or 0)
                p.realized += proceeds - p.avg_cost * q
                p.invested -= p.avg_cost * q
                p.qty -= q
            else:
                p.income += abs(r["amount"])
    cash = float(cash_flow(tx).sum()) if len(tx) else 0.0
    return list(book.values()), cash


def valuation(tx: pd.DataFrame, quotes: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """
    Текущая оценка. quotes: index=secid, столбцы price (руб. за бумагу), name, lot.
    Возвращает таблицу позиций и денежный остаток.
    """
    pos, cash = positions(tx)
    rows = []
    for p in pos:
        if p.qty <= 1e-9 and p.realized == 0 and p.income == 0:
            continue
        price = float(quotes.loc[p.secid, "price"]) if p.secid in quotes.index else np.nan
        mv = p.qty * price
        rows.append({
            "Тикер": p.secid,
            "Название": quotes.loc[p.secid, "name"] if p.secid in quotes.index else "",
            "Кол-во": p.qty, "Ср. цена": p.avg_cost, "Цена": price,
            "Стоимость": mv, "Вложено": p.invested,
            "Нереал. P&L": mv - p.invested if p.qty > 0 else 0.0,
            "Реал. P&L": p.realized, "Доход (див/куп)": p.income,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        total = df["Стоимость"].fillna(0).sum() + cash
        df["Доля"] = df["Стоимость"] / total if total else np.nan
        df["Доходность"] = (df["Нереал. P&L"] + df["Реал. P&L"] + df["Доход (див/куп)"]) / \
            df["Вложено"].where(df["Вложено"] > 0)
    return df, cash


def value_history(tx: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """
    Ежедневная стоимость портфеля: Σ qty_t × P_t + cash_t.
    prices — DataFrame цен (руб./бумага) по тикерам на торговые дни.
    Возвращает value, flows (внешние потоки) и индекс доходности TWR.
    """
    if tx.empty:
        return pd.DataFrame()
    start = tx["date"].min()
    idx = prices.loc[start:].index.union(pd.DatetimeIndex(tx["date"].unique())).sort_values()
    px = prices.reindex(idx).ffill()
    tx = tx.copy()
    tx["cash"] = cash_flow(tx)
    tx["dq"] = np.where(tx["type"] == "BUY", tx["qty"], np.where(tx["type"] == "SELL", -tx["qty"], 0.0))
    qty = tx[tx["dq"] != 0].pivot_table(index="date", columns="secid", values="dq", aggfunc="sum")
    qty = qty.reindex(idx).fillna(0).cumsum()
    cash = tx.groupby("date")["cash"].sum().reindex(idx).fillna(0).cumsum()
    cols = [c for c in qty.columns if c in px.columns]
    value = (qty[cols] * px[cols]).sum(axis=1) + cash
    flows = external_flows(tx).reindex(idx).fillna(0)
    # TWR: r_t = (V_t − F_t) / V_{t−1} − 1 (поток в конце дня t)
    prev = value.shift(1)
    r = ((value - flows) / prev - 1).where(prev > 0).fillna(0)
    twr = (1 + r).cumprod()
    return pd.DataFrame({"value": value, "flows": flows, "twr": twr, "cash": cash})
