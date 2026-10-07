"""
Движок бэктеста портфеля с ребалансировкой.

Методика бэктеста платформы по умолчанию:
  * начальный капитал 1 000 000 руб., без довнесений;
  * ребалансировка раз в год в последний торговый день декабря;
  * без комиссий и налогов (индексы-аналоги).

Дополнительно: частота ребалансировки (нет / месяц / квартал / полгода / год),
пороговая ребалансировка по отклонению долей (коридор), комиссия,
регулярные пополнения, налог на положительный финрезультат при ребалансировке
(упрощённо, без учёта ЛДВ и ИИС).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

REBAL_RULES = {"none": None, "M": "M", "Q": "Q", "H": "H", "A": "A"}
REBAL_LABELS = {"none": "Без ребалансировки", "M": "Ежемесячно", "Q": "Ежеквартально",
                "H": "Раз в полгода", "A": "Раз в год (конец декабря)"}


@dataclass
class BacktestConfig:
    weights: dict[str, float]
    initial: float = 1_000_000
    rebalance: str = "A"                 # none | M | Q | H | A
    band: Optional[float] = None         # коридор, напр. 0.05 = ±5 п.п. от цели
    commission: float = 0.0              # доля от оборота, напр. 0.0005 = 0,05 %
    contribution: float = 0.0            # сумма пополнения
    contribution_freq: str = "M"         # M | Q | A
    tax_rate: float = 0.0                # НДФЛ на реализованную прибыль при продаже
    start: Optional[str] = None
    end: Optional[str] = None


@dataclass
class BacktestResult:
    equity: pd.Series                    # стоимость портфеля
    invested: pd.Series                  # внесённый капитал нарастающим итогом
    weights: pd.DataFrame                # фактические доли
    trades: pd.DataFrame                 # журнал сделок
    rebalance_dates: list = field(default_factory=list)
    costs: float = 0.0
    taxes: float = 0.0
    turnover: float = 0.0                # суммарный оборот / средняя стоимость
    config: Optional[BacktestConfig] = None

    @property
    def final_value(self) -> float:
        return float(self.equity.iloc[-1])


def _period_key(idx: pd.DatetimeIndex, rule: str) -> pd.Index:
    if rule == "M":
        return idx.year * 100 + idx.month
    if rule == "Q":
        return idx.year * 10 + idx.quarter
    if rule == "H":
        return idx.year * 10 + (idx.month > 6).astype(int)
    if rule == "A":
        return pd.Index(idx.year)
    raise ValueError(rule)


def period_end_dates(idx: pd.DatetimeIndex, rule: str) -> set:
    """Последний торговый день каждого периода (в пределах доступных дат)."""
    s = pd.Series(idx, index=idx)
    ends = s.groupby(_period_key(idx, rule)).max()
    out = set(ends.values)
    out.discard(idx[-1])          # последний день ряда — не ребалансируем
    return {pd.Timestamp(x) for x in out}


def run_backtest(prices: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    w = pd.Series(cfg.weights, dtype=float)
    w = w[w > 0]
    if abs(w.sum() - 1) > 1e-6:
        w = w / w.sum()
    px = prices[w.index].copy()
    if cfg.start:
        px = px.loc[pd.Timestamp(cfg.start):]
    if cfg.end:
        px = px.loc[:pd.Timestamp(cfg.end)]
    px = px.ffill().dropna()
    if len(px) < 2:
        raise ValueError("Недостаточно данных для бэктеста: проверьте период и активы")

    idx = px.index
    rebal = period_end_dates(idx, cfg.rebalance) if cfg.rebalance != "none" else set()
    contrib_dates = period_end_dates(idx, cfg.contribution_freq) if cfg.contribution > 0 else set()

    P = px.values
    target = w.values
    units = cfg.initial * target / P[0]
    cost_basis = cfg.initial * target.copy()          # для налога: стоимость покупки
    invested = cfg.initial
    costs = taxes = traded = 0.0

    eq = np.empty(len(idx))
    inv = np.empty(len(idx))
    wts = np.empty((len(idx), len(target)))
    trades, rdates = [], []
    trades += [{"date": idx[0], "asset": a, "amount": cfg.initial * t, "reason": "Покупка"}
               for a, t in zip(w.index, target)]

    for i, d in enumerate(idx):
        values = units * P[i]
        V = values.sum()
        do_contrib = d in contrib_dates
        cur_w = values / V if V > 0 else target
        if cfg.band is not None and cfg.band > 0:
            # коридор: при календарной схеме проверяем только в даты ребалансировки,
            # без календаря — каждый торговый день
            candidate = (d in rebal) if cfg.rebalance != "none" else i > 0
            do_rebal = candidate and np.abs(cur_w - target).max() > cfg.band
        else:
            do_rebal = d in rebal
        if do_contrib:
            V += cfg.contribution
            invested += cfg.contribution
        if do_rebal or do_contrib:
            if do_rebal:
                tgt_values = V * target
            else:
                # пополнение распределяется так, чтобы приблизиться к целевым долям
                need = np.clip(V * target - values, 0, None)
                add = cfg.contribution * (need / need.sum() if need.sum() > 0 else target)
                tgt_values = values + add
            delta = tgt_values - values
            # налог на реализованную прибыль по продажам
            tax = 0.0
            if cfg.tax_rate > 0:
                for j in np.where(delta < 0)[0]:
                    sold_share = -delta[j] / values[j] if values[j] > 0 else 0
                    gain = -delta[j] - cost_basis[j] * sold_share
                    if gain > 0:
                        tax += gain * cfg.tax_rate
                    cost_basis[j] *= (1 - sold_share)
            fee = np.abs(delta).sum() * cfg.commission
            costs += fee
            taxes += tax
            traded += np.abs(delta).sum()
            scale = (V - fee - tax) / V
            new_values = (values + delta) * scale
            cost_basis += np.clip(delta, 0, None)
            units = new_values / P[i]
            for a, dv in zip(w.index, delta):
                if abs(dv) > 1e-6:
                    trades.append({"date": d, "asset": a, "amount": float(dv),
                                   "reason": "Ребалансировка" if do_rebal else "Пополнение"})
            if do_rebal:
                rdates.append(d)
            values = units * P[i]
            V = values.sum()
        eq[i] = V
        inv[i] = invested
        wts[i] = values / V

    equity = pd.Series(eq, index=idx, name="equity")
    return BacktestResult(
        equity=equity,
        invested=pd.Series(inv, index=idx, name="invested"),
        weights=pd.DataFrame(wts, index=idx, columns=w.index),
        trades=pd.DataFrame(trades),
        rebalance_dates=rdates, costs=costs, taxes=taxes,
        turnover=traded / equity.mean() if equity.mean() > 0 else 0.0,
        config=cfg,
    )


def xirr(cashflows: list[tuple[pd.Timestamp, float]]) -> float:
    """Внутренняя норма доходности для нерегулярных потоков (инвестиции < 0)."""
    if not cashflows:
        return np.nan
    t0 = cashflows[0][0]
    ts = np.array([(d - t0).days / 365.25 for d, _ in cashflows])
    cf = np.array([v for _, v in cashflows])

    def npv(r):
        return (cf / (1 + r) ** ts).sum()

    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return np.nan
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2
