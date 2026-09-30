"""
Граница эффективности Марковица.

* облако случайных портфелей (распределение Дирихле по весам);
* эффективная граница — минимизация дисперсии при заданной доходности (SLSQP);
* портфель минимальной дисперсии, касательный портфель (макс. Шарп), CML;
* ограничения: только длинные позиции, мин./макс. вес каждого актива.

Ожидаемые доходности — арифметическое среднее × n (или CAGR актива),
ковариации — выборочные, годовые. Опционально — сжатие ковариации
Ледуа–Вольфа (устойчивее на коротких рядах).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .metrics import PERIODS, resample_prices


@dataclass
class FrontierInputs:
    mu: pd.Series          # годовые ожидаемые доходности
    cov: pd.DataFrame      # годовая ковариационная матрица
    rf: float = 0.0

    @property
    def assets(self) -> list[str]:
        return list(self.mu.index)


@dataclass
class FrontierResult:
    inputs: FrontierInputs
    frontier: pd.DataFrame                  # ret, vol, sharpe, w_<asset>…
    random: pd.DataFrame                    # облако случайных портфелей
    min_var: pd.Series                      # веса
    max_sharpe: pd.Series                   # веса
    assets_points: pd.DataFrame = field(default_factory=pd.DataFrame)  # отдельные активы

    def point(self, w: pd.Series) -> dict:
        return portfolio_point(w.values, self.inputs)


def ledoit_wolf(returns: pd.DataFrame) -> pd.DataFrame:
    """Сжатие ковариации к диагональной цели (упрощённый Ledoit–Wolf, 2004)."""
    x = returns.values - returns.values.mean(axis=0)
    t, n = x.shape
    s = x.T @ x / t
    mu = np.trace(s) / n
    target = mu * np.eye(n)
    d2 = np.linalg.norm(s - target, "fro") ** 2
    b2 = sum(np.linalg.norm(np.outer(r, r) - s, "fro") ** 2 for r in x) / t ** 2
    shrink = min(b2, d2) / d2 if d2 > 0 else 0
    sigma = shrink * target + (1 - shrink) * s
    return pd.DataFrame(sigma * t / (t - 1), index=returns.columns, columns=returns.columns)


def estimate_inputs(prices: pd.DataFrame, freq: str = "M", rf: float = 0.0,
                    mu_method: str = "arith", shrink: bool = False) -> FrontierInputs:
    p = resample_prices(prices.dropna(how="all").ffill(), freq).dropna()
    r = p.pct_change().dropna()
    n = PERIODS[freq]
    if mu_method == "cagr":
        years = (p.index[-1] - p.index[0]).days / 365.25
        mu = (p.iloc[-1] / p.iloc[0]) ** (1 / years) - 1
    else:
        mu = r.mean() * n
    cov = (ledoit_wolf(r) if shrink else r.cov()) * n
    return FrontierInputs(mu=mu, cov=cov, rf=rf)


def portfolio_point(w: np.ndarray, inp: FrontierInputs) -> dict:
    ret = float(w @ inp.mu.values)
    vol = float(np.sqrt(w @ inp.cov.values @ w))
    return {"ret": ret, "vol": vol, "sharpe": (ret - inp.rf) / vol if vol > 0 else np.nan}


def _bounds(n: int, w_min: float, w_max: float):
    return [(w_min, w_max)] * n


def min_variance(inp: FrontierInputs, w_min=0.0, w_max=1.0, target: Optional[float] = None):
    n = len(inp.mu)
    cov = inp.cov.values
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    if target is not None:
        cons.append({"type": "eq", "fun": lambda w: w @ inp.mu.values - target})
    res = minimize(lambda w: w @ cov @ w, np.full(n, 1 / n), method="SLSQP",
                   bounds=_bounds(n, w_min, w_max), constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-12})
    return res.x if res.success else None


def max_sharpe(inp: FrontierInputs, w_min=0.0, w_max=1.0):
    n = len(inp.mu)
    mu, cov = inp.mu.values, inp.cov.values

    def neg_sharpe(w):
        v = np.sqrt(w @ cov @ w)
        return -(w @ mu - inp.rf) / v if v > 0 else 0

    best = None
    for x0 in [np.full(n, 1 / n)] + [np.eye(n)[i] * 0.5 + 0.5 / n for i in range(min(n, 5))]:
        x0 = x0 / x0.sum()
        res = minimize(neg_sharpe, x0, method="SLSQP", bounds=_bounds(n, w_min, w_max),
                       constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
                       options={"maxiter": 500})
        if res.success and (best is None or res.fun < best.fun):
            best = res
    return best.x if best is not None else None


def random_portfolios(inp: FrontierInputs, n_samples: int = 5000, seed: int = 42,
                      w_min=0.0, w_max=1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(inp.mu)
    w = rng.dirichlet(np.ones(n) * 0.7, n_samples)
    if w_min > 0 or w_max < 1:
        ok = (w >= w_min - 1e-9).all(axis=1) & (w <= w_max + 1e-9).all(axis=1)
        w = w[ok]
    ret = w @ inp.mu.values
    vol = np.sqrt(np.einsum("ij,jk,ik->i", w, inp.cov.values, w))
    df = pd.DataFrame({"ret": ret, "vol": vol, "sharpe": (ret - inp.rf) / vol})
    for i, a in enumerate(inp.assets):
        df[f"w_{a}"] = w[:, i]
    return df


def efficient_frontier(inp: FrontierInputs, n_points: int = 40, w_min=0.0, w_max=1.0,
                       n_random: int = 5000) -> FrontierResult:
    w_mv = min_variance(inp, w_min, w_max)
    w_ms = max_sharpe(inp, w_min, w_max)
    r_lo = float(w_mv @ inp.mu.values) if w_mv is not None else inp.mu.min()
    # максимально достижимая доходность при ограничениях — жадно
    order = inp.mu.sort_values(ascending=False).index
    w_hi, left = pd.Series(w_min, index=inp.mu.index, dtype=float), 1 - w_min * len(inp.mu)
    for a in order:
        add = min(w_max - w_min, left)
        w_hi[a] += add
        left -= add
    r_hi = float(w_hi @ inp.mu)
    rows = []
    for t in np.linspace(r_lo, r_hi, n_points):
        w = min_variance(inp, w_min, w_max, target=t)
        if w is None:
            continue
        pt = portfolio_point(w, inp)
        rows.append({**pt, **{f"w_{a}": w[i] for i, a in enumerate(inp.assets)}})
    frontier = pd.DataFrame(rows)
    assets_points = pd.DataFrame({
        "ret": inp.mu, "vol": np.sqrt(np.diag(inp.cov.values)),
    })
    assets_points["sharpe"] = (assets_points["ret"] - inp.rf) / assets_points["vol"]
    return FrontierResult(
        inputs=inp,
        frontier=frontier,
        random=random_portfolios(inp, n_random, w_min=w_min, w_max=w_max),
        min_var=pd.Series(w_mv, index=inp.assets) if w_mv is not None else pd.Series(dtype=float),
        max_sharpe=pd.Series(w_ms, index=inp.assets) if w_ms is not None else pd.Series(dtype=float),
        assets_points=assets_points,
    )
