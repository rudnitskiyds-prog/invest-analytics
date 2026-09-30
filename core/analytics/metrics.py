"""
Коэффициенты эффективности инвестиционных портфелей и отдельных бумаг.

Набор показателей взят из НИР «Сравнительный анализ пассивных стратегий
управления инвестиционным портфелем на российском финансовом рынке»
(Рудницкий Д.С., 2026), п. 1.3, табл. 2 и табл. 4:

    CAGR, накопленная доходность, волатильность, максимальная просадка,
    Шарп, Сортино, Трейнор, Кальмар, M² (Модильяни), бета, альфа Дженсена,
    ошибка слежения, Омега, коэффициент Швагера.

Дополнительно: информационный коэффициент, VaR / CVaR, асимметрия, эксцесс,
доля положительных периодов, срок восстановления после просадки.

Соглашения
----------
* Все функции принимают ряд ЦЕН (стоимости портфеля) или ДОХОДНОСТЕЙ
  pandas.Series с DatetimeIndex. Периодичность определяется автоматически
  (день / неделя / месяц / квартал / год) либо задаётся явно `periods_per_year`.
* Безрисковая ставка `rf` — годовая в долях (0.0786 = 7,86 %) или ряд
  годовых ставок по датам (например, RUONIA / КБД), приводится к ставке
  за период: (1 + rf) ** (1 / n) − 1.
* Годовая «средняя» доходность для Шарпа / Трейнора / Дженсена / M²
  по умолчанию арифметическая: mean(r) × n (классическая формула CAPM).
  Параметр `return_method="cagr"` включает геометрический вариант.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Union

import numpy as np
import pandas as pd

RateLike = Union[float, pd.Series]

PERIODS = {"D": 252, "W": 52, "M": 12, "Q": 4, "A": 1}


# ---------------------------------------------------------------- helpers
def infer_periods_per_year(index: pd.Index) -> int:
    """Определяет число периодов в году по медианному шагу дат."""
    if len(index) < 3:
        return 252
    step = pd.Series(index).diff().dt.days.median()
    if step <= 4:
        return 252
    if step <= 10:
        return 52
    if step <= 40:
        return 12
    if step <= 120:
        return 4
    return 1


def to_returns(prices: pd.Series) -> pd.Series:
    """Простые доходности из ряда цен."""
    return prices.astype(float).pct_change().dropna()


def resample_prices(prices: pd.Series | pd.DataFrame, freq: str) -> pd.Series | pd.DataFrame:
    """Цены на конец периода: 'D' — без изменений, 'W', 'M', 'Q', 'A'."""
    if freq == "D":
        return prices
    rule = {"W": "W-FRI", "M": "ME", "Q": "QE", "A": "YE"}[freq]
    return prices.resample(rule).last().dropna(how="all")


def per_period_rate(rf: RateLike, n: int, index: Optional[pd.Index] = None) -> Union[float, pd.Series]:
    """Годовая ставка -> ставка за период. Для ряда — выравнивание по index."""
    if isinstance(rf, pd.Series):
        s = rf.sort_index()
        if index is not None:
            s = s.reindex(s.index.union(index)).ffill().bfill().reindex(index)
        return (1 + s) ** (1.0 / n) - 1
    return (1 + float(rf)) ** (1.0 / n) - 1


def annual_rf(rf: RateLike) -> float:
    return float(rf.mean()) if isinstance(rf, pd.Series) else float(rf)


def _as_returns(x: pd.Series, is_prices: bool) -> pd.Series:
    return to_returns(x) if is_prices else x.dropna().astype(float)


# ---------------------------------------------------------------- basic
def total_return(prices: pd.Series) -> float:
    p = prices.dropna()
    return float(p.iloc[-1] / p.iloc[0] - 1)


def cagr(prices: pd.Series) -> float:
    """Среднегодовой темп роста по календарным дням."""
    p = prices.dropna()
    years = (p.index[-1] - p.index[0]).days / 365.25
    if years <= 0:
        return np.nan
    return float((p.iloc[-1] / p.iloc[0]) ** (1 / years) - 1)


def volatility(returns: pd.Series, n: int) -> float:
    return float(returns.std(ddof=1) * np.sqrt(n))


def drawdown_series(prices: pd.Series) -> pd.Series:
    p = prices.dropna()
    return p / p.cummax() - 1


def max_drawdown(prices: pd.Series) -> float:
    return float(drawdown_series(prices).min())


def max_drawdown_info(prices: pd.Series) -> dict:
    """Глубина, дата пика, дата дна, дата восстановления, дней до восстановления."""
    dd = drawdown_series(prices)
    trough = dd.idxmin()
    peak = prices.loc[:trough].idxmax()
    after = prices.loc[trough:]
    rec = after[after >= prices.loc[peak]]
    recovery = rec.index[0] if len(rec) else None
    return {
        "max_drawdown": float(dd.min()),
        "peak": peak,
        "trough": trough,
        "recovery": recovery,
        "days_to_recover": (recovery - peak).days if recovery is not None else None,
    }


def annualized_mean(returns: pd.Series, n: int) -> float:
    return float(returns.mean() * n)


# ---------------------------------------------------------------- risk-adjusted
def sharpe(returns: pd.Series, rf: RateLike = 0.0, n: Optional[int] = None) -> float:
    """S = (Rp − Rf) / σp, годовое."""
    n = n or infer_periods_per_year(returns.index)
    ex = returns - per_period_rate(rf, n, returns.index)
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * np.sqrt(n)) if sd > 0 else np.nan


def downside_deviation(returns: pd.Series, mar: RateLike = 0.0, n: Optional[int] = None) -> float:
    """σд — стандартное отклонение отрицательных (ниже MAR) доходностей, годовое."""
    n = n or infer_periods_per_year(returns.index)
    ex = returns - per_period_rate(mar, n, returns.index)
    neg = np.minimum(ex, 0.0)
    return float(np.sqrt((neg ** 2).mean()) * np.sqrt(n))


def sortino(returns: pd.Series, rf: RateLike = 0.0, n: Optional[int] = None) -> float:
    """So = (Rp − Rf) / σд."""
    n = n or infer_periods_per_year(returns.index)
    ex = returns - per_period_rate(rf, n, returns.index)
    dd = downside_deviation(returns, rf, n)
    return float(ex.mean() * n / dd) if dd > 0 else np.nan


def beta(returns: pd.Series, market: pd.Series) -> float:
    """β = Cov(Rp, Rm) / Var(Rm)."""
    df = pd.concat([returns, market], axis=1, join="inner").dropna()
    if len(df) < 3:
        return np.nan
    cov = np.cov(df.iloc[:, 0], df.iloc[:, 1], ddof=1)
    return float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else np.nan


def correlation(returns: pd.Series, market: pd.Series) -> float:
    df = pd.concat([returns, market], axis=1, join="inner").dropna()
    return float(df.iloc[:, 0].corr(df.iloc[:, 1])) if len(df) > 2 else np.nan


def jensen_alpha(returns: pd.Series, market: pd.Series, rf: RateLike = 0.0,
                 n: Optional[int] = None) -> float:
    """α = Rp − [Rf + β(Rm − Rf)], годовая."""
    n = n or infer_periods_per_year(returns.index)
    df = pd.concat([returns, market], axis=1, join="inner").dropna()
    rp, rm = df.iloc[:, 0], df.iloc[:, 1]
    rfp = per_period_rate(rf, n, df.index)
    b = beta(rp, rm)
    a = (rp - rfp).mean() - b * (rm - rfp).mean()
    return float(a * n)


def treynor(returns: pd.Series, market: pd.Series, rf: RateLike = 0.0,
            n: Optional[int] = None) -> float:
    """T = (Rp − Rf) / βp."""
    n = n or infer_periods_per_year(returns.index)
    b = beta(returns, market)
    ex = (returns - per_period_rate(rf, n, returns.index)).mean() * n
    return float(ex / b) if b and not np.isnan(b) and b != 0 else np.nan


def tracking_error(returns: pd.Series, market: pd.Series, n: Optional[int] = None) -> float:
    """TE = σ(Rp − Rm), годовая."""
    n = n or infer_periods_per_year(returns.index)
    df = pd.concat([returns, market], axis=1, join="inner").dropna()
    return float((df.iloc[:, 0] - df.iloc[:, 1]).std(ddof=1) * np.sqrt(n))


def information_ratio(returns: pd.Series, market: pd.Series, n: Optional[int] = None) -> float:
    n = n or infer_periods_per_year(returns.index)
    df = pd.concat([returns, market], axis=1, join="inner").dropna()
    active = df.iloc[:, 0] - df.iloc[:, 1]
    sd = active.std(ddof=1)
    return float(active.mean() / sd * np.sqrt(n)) if sd > 0 else np.nan


def modigliani_m2(returns: pd.Series, market: pd.Series, rf: RateLike = 0.0,
                  n: Optional[int] = None) -> float:
    """M² = Sp × σм + Rf."""
    n = n or infer_periods_per_year(returns.index)
    return float(sharpe(returns, rf, n) * volatility(market.dropna(), n) + annual_rf(rf))


def calmar(prices: pd.Series) -> float:
    """K = CAGR / |MaxDD|."""
    mdd = max_drawdown(prices)
    return float(cagr(prices) / abs(mdd)) if mdd < 0 else np.nan


def omega(returns: pd.Series, threshold: RateLike = 0.0, n: Optional[int] = None) -> float:
    """Ω = Σ max(r − L, 0) / Σ max(L − r, 0) — дискретный аналог ∫[1−F]dr / ∫F dr."""
    n = n or infer_periods_per_year(returns.index)
    ex = returns - per_period_rate(threshold, n, returns.index)
    gains, losses = ex.clip(lower=0).sum(), (-ex).clip(lower=0).sum()
    return float(gains / losses) if losses > 0 else np.nan


def schwager(returns: pd.Series) -> float:
    """Sw = ΣProfit / Σ|Loss| (profit factor) по доходностям периодов."""
    gains, losses = returns[returns > 0].sum(), returns[returns < 0].abs().sum()
    return float(gains / losses) if losses > 0 else np.nan


def var_historic(returns: pd.Series, level: float = 0.95) -> float:
    return float(-np.percentile(returns.dropna(), (1 - level) * 100))


def cvar_historic(returns: pd.Series, level: float = 0.95) -> float:
    r = returns.dropna()
    cut = np.percentile(r, (1 - level) * 100)
    return float(-r[r <= cut].mean())


# ---------------------------------------------------------------- summary
@dataclass
class MetricsReport:
    start: str
    end: str
    periods_per_year: int
    total_return: float
    cagr: float
    volatility: float
    max_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    omega: float
    schwager: float
    var_95: float
    cvar_95: float
    skew: float
    kurtosis: float
    positive_share: float
    best_period: float
    worst_period: float
    days_to_recover: Optional[int]
    beta: float = np.nan
    alpha: float = np.nan
    treynor: float = np.nan
    m2: float = np.nan
    tracking_error: float = np.nan
    information_ratio: float = np.nan
    correlation: float = np.nan

    def to_dict(self) -> dict:
        return asdict(self)


LABELS_RU = {
    "total_return": "Накопленная доходность",
    "cagr": "CAGR (среднегод.)",
    "volatility": "Волатильность (год.)",
    "max_drawdown": "Макс. просадка",
    "sharpe": "Коэф. Шарпа",
    "sortino": "Коэф. Сортино",
    "calmar": "Коэф. Кальмара",
    "omega": "Коэф. Омега",
    "schwager": "Коэф. Швагера",
    "beta": "Бета",
    "alpha": "Альфа Дженсена (год.)",
    "treynor": "Коэф. Трейнора",
    "m2": "M² Модильяни",
    "tracking_error": "Ошибка слежения",
    "information_ratio": "Информационный коэф.",
    "correlation": "Корреляция с бенчмарком",
    "var_95": "VaR 95% (за период)",
    "cvar_95": "CVaR 95% (за период)",
    "skew": "Асимметрия",
    "kurtosis": "Эксцесс",
    "positive_share": "Доля положит. периодов",
    "best_period": "Лучший период",
    "worst_period": "Худший период",
    "days_to_recover": "Дней до восстановления",
}

PERCENT_FIELDS = {"total_return", "cagr", "volatility", "max_drawdown", "alpha", "treynor",
                  "m2", "tracking_error", "var_95", "cvar_95", "positive_share",
                  "best_period", "worst_period"}


def compute_all(prices: pd.Series, benchmark: Optional[pd.Series] = None,
                rf: RateLike = 0.0, freq: str = "M",
                return_method: str = "arith") -> MetricsReport:
    """
    Полный набор показателей по ряду стоимости.

    prices     — ряд цен/стоимости портфеля (любая исходная частота);
    benchmark  — ряд цен бенчмарка (например, MCFTR);
    rf         — безрисковая ставка (годовая) или ряд ставок;
    freq       — частота расчёта доходностей: 'D','W','M','Q' (в НИР — 'M');
    return_method — 'arith' (среднее × n) или 'cagr' для числителя Шарпа/Трейнора.
    """
    p = resample_prices(prices.dropna(), freq)
    n = PERIODS[freq]
    r = to_returns(p)
    rep = MetricsReport(
        start=str(p.index[0].date()), end=str(p.index[-1].date()), periods_per_year=n,
        total_return=total_return(p), cagr=cagr(p), volatility=volatility(r, n),
        max_drawdown=max_drawdown(p), sharpe=sharpe(r, rf, n), sortino=sortino(r, rf, n),
        calmar=calmar(p), omega=omega(r, rf, n), schwager=schwager(r),
        var_95=var_historic(r), cvar_95=cvar_historic(r),
        skew=float(r.skew()), kurtosis=float(r.kurt()),
        positive_share=float((r > 0).mean()), best_period=float(r.max()),
        worst_period=float(r.min()),
        days_to_recover=max_drawdown_info(p)["days_to_recover"],
    )
    if return_method == "cagr":
        rep.sharpe = (rep.cagr - annual_rf(rf)) / rep.volatility if rep.volatility else np.nan
    if benchmark is not None and len(benchmark.dropna()) > 3:
        b = resample_prices(benchmark.dropna(), freq)
        rb = to_returns(b)
        common = r.index.intersection(rb.index)
        rr, rbb = r.loc[common], rb.loc[common]
        rep.beta = beta(rr, rbb)
        rep.alpha = jensen_alpha(rr, rbb, rf, n)
        rep.treynor = treynor(rr, rbb, rf, n)
        if return_method == "cagr" and rep.beta:
            rep.treynor = (rep.cagr - annual_rf(rf)) / rep.beta
        rep.m2 = rep.sharpe * volatility(rbb, n) + annual_rf(rf)
        rep.tracking_error = tracking_error(rr, rbb, n)
        rep.information_ratio = information_ratio(rr, rbb, n)
        rep.correlation = correlation(rr, rbb)
    return rep


def metrics_table(series: dict[str, pd.Series], benchmark: Optional[pd.Series] = None,
                  rf: RateLike = 0.0, freq: str = "M", return_method: str = "arith") -> pd.DataFrame:
    """Сводная таблица (как табл. 4 НИР): строки — показатели, столбцы — портфели."""
    cols = {name: compute_all(s, benchmark, rf, freq, return_method).to_dict()
            for name, s in series.items()}
    df = pd.DataFrame(cols)
    order = [k for k in LABELS_RU if k in df.index]
    return df.loc[order]


def format_metrics_table(df: pd.DataFrame) -> pd.DataFrame:
    """Человекочитаемое представление: проценты, 3 знака для коэффициентов."""
    out = pd.DataFrame(index=[LABELS_RU.get(i, i) for i in df.index], columns=df.columns, dtype=object)
    for key, label in zip(df.index, out.index):
        for c in df.columns:
            v = df.loc[key, c]
            if v is None or (isinstance(v, float) and np.isnan(v)):
                out.loc[label, c] = "—"
            elif key in PERCENT_FIELDS:
                out.loc[label, c] = f"{float(v) * 100:,.1f}%".replace(",", " ").replace(".", ",")
            elif key == "days_to_recover":
                out.loc[label, c] = f"{int(v)}"
            else:
                out.loc[label, c] = f"{float(v):.3f}".replace(".", ",")
    return out


def rolling_metric(prices: pd.Series, window: int, kind: str = "sharpe",
                   rf: RateLike = 0.0, benchmark: Optional[pd.Series] = None,
                   freq: str = "M") -> pd.Series:
    """Скользящие Шарп / волатильность / бета (окно в периодах freq)."""
    p = resample_prices(prices.dropna(), freq)
    n = PERIODS[freq]
    r = to_returns(p)
    if kind == "volatility":
        return r.rolling(window).std() * np.sqrt(n)
    if kind == "sharpe":
        ex = r - per_period_rate(rf, n, r.index)
        return ex.rolling(window).mean() / ex.rolling(window).std() * np.sqrt(n)
    if kind == "beta" and benchmark is not None:
        rb = to_returns(resample_prices(benchmark.dropna(), freq)).reindex(r.index)
        return r.rolling(window).cov(rb) / rb.rolling(window).var()
    raise ValueError(kind)
