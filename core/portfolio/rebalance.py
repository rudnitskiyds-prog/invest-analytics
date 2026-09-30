"""
Календарь ребалансировок и расчёт заявок.

* schedule()        — ближайшие плановые даты (последний рабочий день периода);
* drift_report()    — отклонение фактических долей от целевых, сигнал по коридору;
* rebalance_orders() — заявки купить/продать с округлением до лотов;
* to_ics()          — экспорт календаря в .ics (Google / Apple / Outlook).
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Optional

import numpy as np
import pandas as pd

RULE_MONTHS = {"M": 1, "Q": 3, "H": 6, "A": 12}


def _last_bday(year: int, month: int) -> pd.Timestamp:
    return (pd.Timestamp(year=year, month=month, day=1) + pd.offsets.BMonthEnd(0))


def schedule(rule: str, start: Optional[str] = None, n: int = 8) -> list[pd.Timestamp]:
    """Следующие n дат ребалансировки: последний рабочий день месяца/квартала/полугодия/года."""
    if rule not in RULE_MONTHS:
        return []
    step = RULE_MONTHS[rule]
    today = pd.Timestamp(start or dt.date.today()).normalize()
    out = []
    y, m = today.year, today.month
    # первый месяц, кратный шагу (M: любой, Q: 3,6,9,12, H: 6,12, A: 12)
    while len(out) < n:
        if m % step == 0:
            d = _last_bday(y, m)
            if d >= today:
                out.append(d)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def drift_report(current: pd.Series, target: pd.Series, band: float = 0.05) -> pd.DataFrame:
    """current/target — доли по тикерам (сумма 1). Абсолютное и относительное отклонение."""
    keys = current.index.union(target.index)
    cur = current.reindex(keys).fillna(0)
    tgt = target.reindex(keys).fillna(0)
    df = pd.DataFrame({"Факт": cur, "Цель": tgt})
    df["Отклонение, п.п."] = (cur - tgt) * 100
    df["Отн. отклонение"] = np.where(tgt > 0, (cur - tgt) / tgt, np.nan)
    df["Вне коридора"] = (cur - tgt).abs() > band
    return df


def rebalance_orders(values: pd.Series, target: pd.Series, prices: pd.Series,
                     lots: Optional[pd.Series] = None, cash: float = 0.0,
                     commission: float = 0.0) -> pd.DataFrame:
    """
    values — текущая стоимость позиций (руб.), prices — цена 1 бумаги, lots — размер лота.
    Свободные деньги (cash) распределяются вместе с перебалансировкой.
    Сначала продажи, затем покупки; округление до целых лотов вниз.
    """
    keys = values.index.union(target.index)
    v = values.reindex(keys).fillna(0)
    t = target.reindex(keys).fillna(0)
    p = prices.reindex(keys)
    lot = (lots.reindex(keys) if lots is not None else pd.Series(1, index=keys)).fillna(1)
    total = v.sum() + cash
    delta = t * total - v
    lot_value = p * lot
    lots_n = np.trunc(delta / lot_value).fillna(0)
    qty = lots_n * lot
    amount = qty * p
    df = pd.DataFrame({
        "Действие": np.where(qty > 0, "Купить", np.where(qty < 0, "Продать", "—")),
        "Лотов": lots_n.abs().astype(int), "Бумаг": qty.abs(), "Цена": p,
        "Сумма": amount.abs(), "Текущая стоимость": v, "Целевая стоимость": t * total,
        "Доля после": (v + amount) / total if total else np.nan,
    })
    df["Комиссия"] = df["Сумма"] * commission
    return df.sort_values("Действие", key=lambda s: s.map({"Продать": 0, "Купить": 1, "—": 2}))


def to_ics(dates: list[pd.Timestamp], portfolio: str, description: str = "") -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//invest-platform//RU", "CALSCALE:GREGORIAN"]
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for d in dates:
        day = pd.Timestamp(d).strftime("%Y%m%d")
        nxt = (pd.Timestamp(d) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@invest-platform", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day}", f"DTEND;VALUE=DATE:{nxt}",
                  f"SUMMARY:Ребалансировка — {portfolio}",
                  f"DESCRIPTION:{description}".replace("\n", "\\n"),
                  "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY",
                  f"DESCRIPTION:Завтра ребалансировка {portfolio}", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
