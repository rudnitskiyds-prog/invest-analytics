"""
Эталоны Python для паритета web/lib/ledger.js и web/lib/rebalance.js (tests/web/ledger.test.mjs, rebalance.test.mjs):
выходы core.portfolio.ledger / core.portfolio.rebalance на фиксированных входах -> tests/fixtures/ledger_reference.json.

    python -m tests.web.make_ledger_reference

Перегенерировать при любой правке core/portfolio/ledger.py или rebalance.py (формулы живут парами с JS).
tests/test_ledger_reference.py следит, чтобы файл совпадал с тем, что считает Python сейчас.
Сеть не нужна. UID и DTSTAMP в .ics маскируются (случайные).
"""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from core.portfolio import ledger as L
from core.portfolio import rebalance as R
from tests.web.make_reference import clean

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "ledger_reference.json"


# ---------------------------------------------------------------- входы
def T(date, type_, secid=None, qty=0, price=0, amount=None, fee=0, id_=None, note=""):
    """Операция как после Ledger.add: пустая сумма = qty × price."""
    return {"id": id_, "date": date, "type": type_, "secid": secid, "qty": qty, "price": price,
            "amount": qty * price if amount is None else amount, "fee": fee, "note": note}


def numbered(rows):
    out = []
    for i, r in enumerate(rows, 1):
        out.append({**r, "id": i})
    return out


def df(rows) -> pd.DataFrame:
    d = pd.DataFrame(rows, columns=["id", "date", "type", "secid", "qty", "price", "amount", "fee", "note"])
    d["secid"] = d["secid"].astype(object).where(d["secid"].notna(), None)
    d["date"] = pd.to_datetime(d["date"])
    return d


def frame(dates, cols) -> pd.DataFrame:
    return pd.DataFrame({k: [np.nan if x is None else x for x in v] for k, v in cols.items()}, index=pd.to_datetime(dates))


TX_SETS = {
    "average_price_two_buys_then_sell": [   # средняя 110; реализованный 5×150 − 1 − 5×110 = 199
        T("2024-01-10", "BUY", "SBER", 10, 100), T("2024-01-11", "BUY", "SBER", 10, 120),
        T("2024-01-12", "SELL", "SBER", 5, 150, fee=1)],
    "sell_more_than_held": [                # продано 15 при остатке 10: списывается 10, выручка — вся сумма
        T("2024-01-10", "BUY", "SBER", 10, 100), T("2024-01-11", "SELL", "SBER", 15, 110)],
    "buy_with_fee": [T("2024-01-10", "BUY", "GAZP", 10, 100, fee=5)],
    "full_sell_and_income": [
        T("2024-01-02", "DEPOSIT", amount=50000),
        T("2024-01-03", "BUY", "SBER", 10, 100, fee=2), T("2024-01-04", "SELL", "SBER", 10, 130, fee=2),
        T("2024-01-05", "DIVIDEND", "SBER", amount=40), T("2024-01-06", "COUPON", "SU26238RMFS4", amount=35.4),
        T("2024-01-07", "TAX", amount=5), T("2024-01-08", "FEE", amount=1.5), T("2024-01-09", "WITHDRAW", amount=1000)],
    "types_all": [
        T("2024-02-01", "DEPOSIT", amount=1000), T("2024-02-02", "WITHDRAW", amount=-300), T("2024-02-03", "BUY", "A", 2, 50, fee=1),
        T("2024-02-04", "SELL", "A", 1, 60, fee=2), T("2024-02-05", "DIVIDEND", "A", amount=7), T("2024-02-06", "COUPON", "A", amount=8),
        T("2024-02-07", "FEE", amount=3), T("2024-02-08", "TAX", amount=4)],
    "empty": [],
}
TX_SETS = {k: numbered(sorted(v, key=lambda r: r["date"])) for k, v in TX_SETS.items()}

FLOWS = {
    "two_ops_one_day": numbered([T("2024-03-01", "DEPOSIT", amount=1000), T("2024-03-01", "WITHDRAW", amount=300),
                                 T("2024-03-01", "DEPOSIT", amount=200), T("2024-03-04", "WITHDRAW", amount=50),
                                 T("2024-03-05", "BUY", "A", 1, 10)]),
    "no_external": numbered([T("2024-03-01", "BUY", "A", 1, 10)]),
}

QUOTES = {
    "all_priced": {"SBER": {"price": 140.0, "name": "Сбербанк", "lot": 10}, "SU26238RMFS4": {"price": 900.0, "name": "ОФЗ", "lot": 1}},
    "no_price": {"SBER": {"price": None, "name": "Сбербанк", "lot": 10}},
    "no_quote": {},
}

VAL_CASES = [
    ("average_price_two_buys_then_sell", "all_priced"),
    ("average_price_two_buys_then_sell", "no_price"),
    ("average_price_two_buys_then_sell", "no_quote"),
    ("full_sell_and_income", "all_priced"),                 # SBER продана целиком, realized ≠ 0 — остаётся; облигация — только купон
    ("buy_with_fee", "all_priced"),
]
ZERO_QTY_NO_INCOME = numbered([T("2024-01-10", "BUY", "SBER", 10, 100), T("2024-01-11", "SELL", "SBER", 10, 100)])
SHARE_ZERO_TOTAL = numbered([T("2024-01-10", "BUY", "SBER", 10, 100)])    # cash = −1000, стоимость 1000 -> итог 0

DATES_VH = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12"]
VH_CASES = {
    "weekend_buy": (numbered([T("2024-01-02", "DEPOSIT", amount=100000), T("2024-01-06", "BUY", "SBER", 100, 250, fee=10),
                              T("2024-01-10", "SELL", "SBER", 40, 270)]),
                    {"dates": DATES_VH, "cols": {"SBER": [250, 251, 252, 253, 255, 256, 260, 262, 261]}}),
    "no_price_for_held_security": (numbered([T("2024-01-02", "DEPOSIT", amount=100000), T("2024-01-03", "BUY", "SBER", 10, 250),
                                             T("2024-01-03", "BUY", "NOPR", 5, 100)]),
                                   {"dates": DATES_VH, "cols": {"SBER": [250, 251, 252, 253, 255, 256, 260, 262, 261],
                                                               "NOPR": [None] * 9}}),
    "gap_in_the_middle_ffill": (numbered([T("2024-01-02", "DEPOSIT", amount=50000), T("2024-01-03", "BUY", "GAZP", 50, 160)]),
                                {"dates": DATES_VH, "cols": {"GAZP": [160, 161, None, None, 162, 163, None, 158, 157]}}),
    "leading_gap_bfill": (numbered([T("2024-01-02", "DEPOSIT", amount=50000), T("2024-01-02", "BUY", "GAZP", 10, 160)]),
                          {"dates": DATES_VH, "cols": {"GAZP": [None, None, 161, 162, 163, 164, 165, 166, 167]}}),
    "deposit_withdraw_inside_window": (numbered([T("2024-01-02", "DEPOSIT", amount=100000), T("2024-01-03", "BUY", "SBER", 100, 250),
                                                 T("2024-01-05", "DEPOSIT", amount=30000), T("2024-01-09", "WITHDRAW", amount=20000),
                                                 T("2024-01-11", "SELL", "SBER", 100, 262)]),
                                       {"dates": DATES_VH, "cols": {"SBER": [250, 251, 252, 253, 255, 256, 260, 262, 261]}}),
    "ticker_outside_prices": (numbered([T("2024-01-02", "DEPOSIT", amount=100000), T("2024-01-03", "BUY", "SBER", 10, 250),
                                        T("2024-01-03", "BUY", "ZZZ", 10, 100)]),
                              {"dates": DATES_VH, "cols": {"SBER": [250, 251, 252, 253, 255, 256, 260, 262, 261]}}),
    "sold_security_with_nan_price_after": (numbered([T("2024-01-02", "DEPOSIT", amount=100000), T("2024-01-03", "BUY", "SBER", 10, 250),
                                                     T("2024-01-04", "SELL", "SBER", 10, 252), T("2024-01-03", "BUY", "GAZP", 10, 160),
                                                     T("2024-01-04", "SELL", "GAZP", 10, 161)]),
                                           {"dates": DATES_VH, "cols": {"SBER": [250, 251, 252, 253, 255, 256, 260, 262, 261],
                                                                       "GAZP": [None, 160, 161, None, None, None, None, None, None]}}),
    "tx_before_first_price_date": (numbered([T("2023-12-20", "DEPOSIT", amount=1000), T("2024-01-03", "BUY", "SBER", 1, 250)]),
                                   {"dates": DATES_VH, "cols": {"SBER": [250, 251, 252, 253, 255, 256, 260, 262, 261]}}),
    "empty_tx": ([], {"dates": DATES_VH, "cols": {"SBER": [250] * 9}}),
}
for _k, (_t, _p) in list(VH_CASES.items()):    # порядок операций внутри дня — по id, как Ledger.transactions
    VH_CASES[_k] = (sorted(_t, key=lambda r: (r["date"], r["id"])), _p)

SCHED = [("M", "2024-11-15", 6), ("Q", "2024-11-15", 6), ("H", "2024-11-15", 6), ("A", "2024-11-15", 6),
         ("M", "2024-03-30", 3),                  # 30.03 (сб): последний рабочий день марта (29-е) уже позади -> апрель
         ("M", "2024-03-29", 3),                  # ровно на последнем рабочем дне — включается
         ("M", "2024-03-31", 3),                  # воскресенье: 29 марта позади
         ("M", "2024-06-01", 2),                  # конец июня — воскресенье, дата 28.06
         ("Q", "2024-06-29", 4), ("H", "2024-12-31", 3), ("A", "2024-12-31", 2), ("A", "2025-01-01", 2),
         ("Q", "2024-01-01", 0), ("none", "2024-01-01", 8), ("X", "2024-01-01", 8),
         ("M", "2024-02-29", 2), ("M", "2023-12-31", 2)]

DRIFT = [
    ({"A": .5, "B": .3, "X": .2}, {"A": .4, "B": .35, "C": .25}, 0.05),    # X без цели, C без факта
    ({"A": .5625, "B": .4375}, {"A": .5, "B": .5}, 0.0625),                 # ровно на границе — внутри (строгое >)
    ({"A": .5626, "B": .4374}, {"A": .5, "B": .5}, 0.0625),                 # чуть дальше — вне коридора
    ({}, {"A": 1.0}, 0.05), ({"A": 1.0}, {}, 0.05), ({}, {}, 0.05),
]

VALUES = {"SBER": 60000, "GAZP": 25000, "LKOH": 0}
TARGET = {"SBER": .4, "GAZP": .3, "LKOH": .2, "YNDX": .1}
PRICES = {"SBER": 274.0, "GAZP": 158.0, "LKOH": 7000.0}
LOTS = {"SBER": 10, "GAZP": 10, "LKOH": 1}
ORDERS = [
    dict(values=VALUES, target=TARGET, prices=PRICES, lots=LOTS, cash=15000, commission=0.003),     # лоты 10 и 1, тикер без цены (YNDX), деньги, комиссия
    dict(values=VALUES, target=TARGET, prices=PRICES, lots=LOTS, cash=0, commission=0),
    dict(values=VALUES, target=TARGET, prices=PRICES, lots=None, cash=0, commission=0.01),         # лот 1
    dict(values={"A": 1000, "B": 9000}, target={"A": .5, "B": .5}, prices={"A": 100.0, "B": 90.0}, lots={"A": 1, "B": 1}, cash=0, commission=0),
    dict(values={"A": 1000, "B": 9000}, target={"A": .5, "B": .5}, prices={"A": 100.0, "B": 90.0}, lots={"A": 7, "B": 3}, cash=500, commission=0.001),
    dict(values={"A": 5000, "B": 5000}, target={"A": .5, "B": .5}, prices={"A": 100.0, "B": 100.0}, lots=None, cash=0, commission=0.001),   # всё «—»
    dict(values={}, target={"A": 1.0}, prices={"A": 10.0}, lots=None, cash=0, commission=0),                                        # нулевой итог
    dict(values={}, target={"A": 1.0}, prices={"A": 10.0}, lots=None, cash=1000, commission=0),
    dict(values={"A": 100}, target={}, prices={"A": 10.0}, lots=None, cash=0, commission=0),                                        # всё продать
    dict(values={"A": 6000, "B": 3000, "C": 1000}, target={"A": .2, "B": .3, "C": .5}, prices={"A": 50.0, "B": 25.0, "C": 5.0}, lots=None, cash=0, commission=0),
]
ICS = [
    (["2024-12-31", "2025-01-31"], "Мой портфель", "Проверить доли\nи купить ОФЗ"),
    (["2024-02-28"], "P", ""),
    ([], "P", "x"),
    (["2024-03-29"], "A,B;C\\D\nE", "Купить: a, b; c\\d\r\nвторая строка"),
]


# ---------------------------------------------------------------- расчёт
VAL_KEYS = {"Тикер": "secid", "Название": "name", "Кол-во": "qty", "Ср. цена": "avgCost", "Цена": "price", "Стоимость": "value",
            "Вложено": "invested", "Нереал. P&L": "unrealized", "Реал. P&L": "realized", "Доход (див/куп)": "income",
            "Доля": "share", "Доходность": "ret"}
DRIFT_KEYS = {"Факт": "current", "Цель": "target", "Отклонение, п.п.": "driftPp", "Отн. отклонение": "relDrift", "Вне коридора": "outOfBand"}
ORD_KEYS = {"Действие": "action", "Лотов": "lots", "Бумаги": "qty", "Бумаг": "qty", "Цена": "price", "Сумма": "amount",
            "Текущая стоимость": "value", "Целевая стоимость": "targetValue", "Доля после": "shareAfter", "Комиссия": "commission"}


def _val(tx, quotes):
    q = pd.DataFrame({"price": [np.nan if v["price"] is None else v["price"] for v in quotes.values()],
                      "name": [v["name"] for v in quotes.values()], "lot": [v["lot"] for v in quotes.values()]}, index=list(quotes))
    table, cash = L.valuation(df(tx), q)
    rows = [] if table.empty else [{VAL_KEYS[k]: v for k, v in r.items()} for r in table.to_dict("records")]
    return {"rows": clean(rows), "cash": clean(cash)}


def _positions(tx):
    pos, cash = L.positions(df(tx))
    return {"positions": clean([{"secid": p.secid, "qty": p.qty, "avgCost": p.avg_cost, "invested": p.invested,
                                 "realized": p.realized, "income": p.income} for p in pos]), "cash": clean(cash)}


def _vh(tx, prices):
    out = L.value_history(df(tx), frame(prices["dates"], prices["cols"]))
    if out.empty:
        return {"dates": [], "value": [], "twr": [], "flows": [], "cash": [], "missingPrices": []}
    return clean({"dates": [str(d.date()) for d in out.index], "value": out["value"].tolist(), "twr": out["twr"].tolist(),
                  "flows": out["flows"].tolist(), "cash": out["cash"].tolist(), "missingPrices": out.attrs["missing_prices"]})


def _drift(cur, tgt, band):
    t = R.drift_report(pd.Series(cur, dtype=float), pd.Series(tgt, dtype=float), band)
    return clean([{"secid": k, **{DRIFT_KEYS[c]: v for c, v in r.items()}} for k, r in t.to_dict("index").items()])


def _orders(c):
    lots = None if c["lots"] is None else pd.Series(c["lots"], dtype=float)
    t = R.rebalance_orders(pd.Series(c["values"], dtype=float), pd.Series(c["target"], dtype=float),
                           pd.Series(c["prices"], dtype=float), lots, c["cash"], c["commission"])
    return clean([{"secid": k, **{ORD_KEYS[col]: v for col, v in r.items()}} for k, r in t.to_dict("index").items()])


def mask_ics(s: str) -> str:
    return re.sub(r"DTSTAMP:\d{8}T\d{6}Z", "DTSTAMP:<stamp>", re.sub(r"UID:[0-9a-f-]+@", "UID:<uid>@", s))


def build() -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")    # datetime.utcnow в to_ics
        cash_flow = [{"name": k, "tx": v, "expected": clean(L.cash_flow(df(v)).tolist()) if v else []} for k, v in TX_SETS.items()]
        ext = [{"name": k, "tx": v, "expected": clean([[str(d.date()), a] for d, a in L.external_flows(df(v)).items()])} for k, v in FLOWS.items()]
        val = [{"name": f"{t}/{q}", "tx": TX_SETS[t], "quotes": QUOTES[q], **_val(TX_SETS[t], QUOTES[q])} for t, q in VAL_CASES]
        val.append({"name": "zero_qty_without_income_skipped", "tx": ZERO_QTY_NO_INCOME, "quotes": QUOTES["all_priced"],
                    **_val(ZERO_QTY_NO_INCOME, QUOTES["all_priced"])})
        zq = {"SBER": {"price": 100.0, "name": "Сбербанк", "lot": 10}}
        val.append({"name": "share_with_zero_total", "tx": SHARE_ZERO_TOTAL, "quotes": zq, **_val(SHARE_ZERO_TOTAL, zq)})
        ics = []
        for dates, pf, desc in ICS:
            ics.append({"dates": dates, "portfolio": pf, "description": desc,
                        "expected": mask_ics(R.to_ics([pd.Timestamp(d) for d in dates], pf, desc))})
        return {
            "_note": "Эталоны Python core/portfolio для tests/web. Генератор: python -m tests.web.make_ledger_reference",
            "pandas": pd.__version__, "numpy": np.__version__,
            "cashFlow": cash_flow, "externalFlows": ext,
            "positions": [{"name": k, "tx": v, **_positions(v)} for k, v in TX_SETS.items()],
            "valuation": val,
            "valueHistory": [{"name": k, "tx": t, "prices": p, **_vh(t, p)} for k, (t, p) in VH_CASES.items()],
            "schedule": [{"rule": r, "start": s, "n": n, "expected": [str(d.date()) for d in R.schedule(r, s, n)]} for r, s, n in SCHED],
            "drift": [{"current": c, "target": t, "band": b, "expected": _drift(c, t, b)} for c, t, b in DRIFT],
            "orders": [{**c, "expected": _orders(c)} for c in ORDERS],
            "ics": ics,
        }


def main():
    ref = build()
    OUT.write_text(json.dumps(ref, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"Записано: {OUT} ({OUT.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
