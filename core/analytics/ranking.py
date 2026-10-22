"""
Рейтинги риск/доходность по классам бумаг (ночной пересчёт `public/data/symbol_stats.json`,
scripts/collect_data.py, источник symbol_stats; контракт — web/CONTRACT.md, «Этап 1»).

Для акций цены — ряд полной доходности (metrics.total_return_series: дивиденды T-Invest из
public/data/dividends.json, рублёвые и не отменённые, относятся на первую месячную дату ≥ exDate);
фонды — по ценам (дивидендов фондов в файле нет).
По каждой бумаге на месячных ценах (окно до 10 лет, n = 12) считаются коэффициенты из
core/analytics/metrics.py с теми же формулами, что на странице бумаги: Шарп, Сортино, Омега
(порог — безрисковая ставка), Кальмар, Мартин, CAGR, волатильность, макс. просадка.
Ранг — перцентиль 0–100 внутри класса (акции / фонды): (средний ранг по возрастанию − 1) / (N − 1) × 100,
чем выше коэффициент, тем выше ранг; бумаги без значения в ранжировании не участвуют. Score — среднее
доступных рангов пяти коэффициентов.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from core.analytics import metrics as m

RANK_METRICS = ("sharpe", "sortino", "omega", "calmar", "martin")
STAT_FIELDS = ("sharpe", "sortino", "omega", "calmar", "martin", "cagr", "volatility", "max_drawdown")
DIGITS = 6


def _num(x) -> Optional[float]:
    """float с округлением до DIGITS знаков; NaN / inf / None -> None (JSON без NaN)."""
    if x is None:
        return None
    x = float(x)
    return round(x, DIGITS) if math.isfinite(x) else None


def symbol_metrics(prices: pd.Series, rf: float) -> dict:
    """Коэффициенты по месячным ценам (n = 12); rf — годовая ставка в долях.
    Формулы — core/analytics/metrics.py (Шарп, Сортино, Омега с порогом rf, Кальмар, Мартин — стандартные
    определения). months — число месячных доходностей."""
    p = prices.dropna().astype(float).sort_index()
    r = m.to_returns(p)
    n = 12
    out = {
        "sharpe": m.sharpe(r, rf, n),
        "sortino": m.sortino(r, rf, n),
        "omega": m.omega(r, rf, n),
        "calmar": m.calmar(p),
        "martin": m.martin(p, rf),
        "cagr": m.cagr(p),
        "volatility": m.volatility(r, n),
        "max_drawdown": m.max_drawdown(p),
    }
    return {**{k: _num(v) for k, v in out.items()}, "months": int(len(r))}


def percentile_ranks(values: pd.Series) -> pd.Series:
    """Перцентиль 0–100: (средний ранг по возрастанию − 1) / (N − 1) × 100; одна бумага — 50;
    NaN / None / ±inf — NaN (в N не входят)."""
    v = pd.to_numeric(values, errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)
    ok = v.dropna()
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if len(ok) == 1:
        out[ok.index] = 50.0
    elif len(ok) > 1:
        out[ok.index] = (ok.rank(method="average") - 1) / (len(ok) - 1) * 100
    return out


def rank_items(items: dict[str, dict]) -> dict[str, dict]:
    """Добавляет каждой записи {"class", коэффициенты…} поля rank {sharpe, sortino, omega, calmar, martin}
    (перцентили внутри класса, 1 знак) и score — среднее доступных рангов (None, если рангов нет)."""
    by_class: dict[str, list[str]] = {}
    for sec, it in items.items():
        by_class.setdefault(it.get("class"), []).append(sec)
    for secs in by_class.values():
        ranks = {k: percentile_ranks(pd.Series({s: items[s].get(k) for s in secs}, dtype=object))
                 for k in RANK_METRICS}
        for s in secs:
            rk = {k: (round(float(ranks[k][s]), 1) if not np.isnan(ranks[k][s]) else None) for k in RANK_METRICS}
            vals = [v for v in rk.values() if v is not None]
            items[s]["rank"] = rk
            items[s]["score"] = round(float(np.mean(vals)), 1) if vals else None
    return items


RUB_CODES = {"RUB", "SUR", "RUR"}


def next_weekday(d: pd.Timestamp) -> pd.Timestamp:
    """Следующий рабочий день (пн–пт) после даты; праздники биржи не учитываются (как web/lib/symbol.js)."""
    d = d + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


_next_weekday = next_weekday          # прежнее имя (совместимость)


def ex_dividends(rows) -> list[dict]:
    """Выплаты из public/data/dividends.json (T-Invest) -> [{exDate, value}] для total_return_series:
    только рублёвые и не отменённые, с датой реестра и суммой. exDate — следующий рабочий день после
    last_buy_date, без неё — record_date (как loadDividends в web/lib/symbol.js)."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("cancelled") or r.get("value") is None or not r.get("record_date"):
            continue
        if str(r.get("currency") or "RUB").upper() not in RUB_CODES:
            continue
        lbd = r.get("last_buy_date")
        ex = next_weekday(pd.Timestamp(lbd[:10])) if lbd else pd.Timestamp(r["record_date"][:10])
        out.append({"exDate": str(ex.date()), "value": float(r["value"])})
    return out


def mean_rate(rate: pd.Series, start, end) -> float:
    """Средняя по времени (по календарным дням) годовая ставка ступенчатого ряда на [start, end]:
    значение действует до следующей даты (ffill); до первой даты ряда — первое значение."""
    s = rate.dropna().astype(float).sort_index()
    if s.empty:
        return float("nan")
    days = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    if not len(days):
        return float("nan")
    daily = s.reindex(s.index.union(days)).ffill().bfill().reindex(days)
    return float(daily.mean())
