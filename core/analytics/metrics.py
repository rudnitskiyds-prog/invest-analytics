"""
Коэффициенты эффективности инвестиционных портфелей и отдельных бумаг.

Набор показателей — стандартные коэффициенты эффективности (классические формулы):

    CAGR, накопленная доходность, волатильность, максимальная просадка,
    Шарп, Сортино, Трейнор, Кальмар, M² (Модильяни), бета, альфа Дженсена,
    ошибка слежения, Омега, коэффициент Швагера.

Дополнительно: информационный коэффициент, VaR / CVaR, асимметрия, эксцесс,
доля положительных периодов, срок восстановления после просадки.
Этап 1: индекс язвы и коэффициент Мартина, R², upside/downside capture, скользящая
волатильность, OHLC-оценки волатильности (Паркинсон, Гарман–Класс, Роджерс–Сатчелл, Янг–Чжан),
доходность по периодам, моментум, помесячная сетка, эпизоды просадок, ряд полной доходности.

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

import math
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


# ---------------------------------------------------------------- этап 1: просадки, бенчмарк, OHLC
# Все функции ниже повторены в web/lib/metrics.js с тождественными формулами (паритет до 1e-9).
def ulcer_index(prices: pd.Series) -> float:
    """Индекс язвы UI = sqrt(mean(dd_t²)), dd_t = P_t / max_{s≤t} P_s − 1 (в долях, dd_t ≤ 0).
    Стандартное определение (P. Martin, B. McCann, 1989), просадки в долях, а не в процентах."""
    dd = drawdown_series(prices).to_numpy(dtype=float)
    return float(np.sqrt(np.mean(dd ** 2))) if len(dd) else np.nan


def martin(prices: pd.Series, rf: float = 0.0) -> float:
    """Коэффициент Мартина (Ulcer Performance Index) = (CAGR − Rf) / UI; rf — годовая ставка в долях.
    Стандартное определение (P. Martin, 1989); NaN, если просадок не было (UI = 0)."""
    ui = ulcer_index(prices)
    return float((cagr(prices) - float(rf)) / ui) if ui > 0 else np.nan


def r_squared(returns: pd.Series, market: pd.Series) -> float:
    """R² = corr(Rp, Rm)² по общим датам (стандартное определение для однофакторной модели)."""
    c = correlation(returns, market)
    return float(c * c) if not np.isnan(c) else np.nan


def capture_ratios(returns: pd.Series, market: pd.Series) -> dict:
    """Upside/downside capture (арифметический вариант): up = Σ r_t / Σ rm_t по периодам rm_t > 0,
    down — по периодам rm_t < 0. Ряды выравниваются по общим датам. NaN — нет таких периодов."""
    df = pd.concat([returns, market], axis=1, join="inner").dropna()
    r, rm = df.iloc[:, 0].to_numpy(float), df.iloc[:, 1].to_numpy(float)

    def ratio(mask):
        den = rm[mask].sum()
        return float(r[mask].sum() / den) if mask.any() and den != 0 else np.nan
    return {"up": ratio(rm > 0), "down": ratio(rm < 0)}


def rolling_volatility(prices: pd.Series, window: int = 21, n: int = 252) -> pd.Series:
    """Скользящая волатильность: ст. откл. (ddof = 1) простых доходностей окна × sqrt(n).
    Точки без полного окна не возвращаются."""
    r = to_returns(prices.dropna())
    return (r.rolling(window).std(ddof=1) * np.sqrt(n)).dropna()


def _ohlc_raw(ohlc) -> tuple[pd.DataFrame, np.ndarray]:
    """OHLC: DataFrame с колонками open/high/low/close или dict {dates, open, high, low, close}
    -> (все строки как float, маска корректных: все четыре цены конечны и > 0)."""
    if isinstance(ohlc, dict):
        idx = pd.to_datetime(ohlc.get("dates")) if ohlc.get("dates") is not None else None
        df = pd.DataFrame({k: ohlc[k] for k in ("open", "high", "low", "close")}, index=idx)
    else:
        df = ohlc.rename(columns=str.lower)[["open", "high", "low", "close"]]
    df = df.apply(pd.to_numeric, errors="coerce").astype(float)
    ok = (np.isfinite(df).all(axis=1) & (df > 0).all(axis=1)).to_numpy()
    return df, ok


def _ohlc_frame(ohlc) -> pd.DataFrame:
    """Только корректные строки OHLC (все четыре цены конечны и > 0)."""
    df, ok = _ohlc_raw(ohlc)
    return df[ok]


def parkinson(ohlc, n: int = 252) -> float:
    """Волатильность Паркинсона (1980): σ² = mean(ln(H/L)²) / (4·ln 2); годовая sqrt(n·σ²)."""
    df = _ohlc_frame(ohlc)
    if df.empty:
        return np.nan
    hl = np.log(df["high"] / df["low"]).to_numpy()
    return float(np.sqrt(n * np.mean(hl ** 2) / (4 * np.log(2))))


def garman_klass(ohlc, n: int = 252) -> float:
    """Волатильность Гармана–Класса (1980): σ² = mean(½·ln(H/L)² − (2·ln 2 − 1)·ln(C/O)²); годовая sqrt(n·σ²)."""
    df = _ohlc_frame(ohlc)
    if df.empty:
        return np.nan
    hl = np.log(df["high"] / df["low"]).to_numpy()
    co = np.log(df["close"] / df["open"]).to_numpy()
    v = np.mean(0.5 * hl ** 2 - (2 * np.log(2) - 1) * co ** 2)
    return float(np.sqrt(n * v)) if v >= 0 else np.nan


def _rs_terms(df: pd.DataFrame) -> np.ndarray:
    h, l_, o, c = (df[k].to_numpy() for k in ("high", "low", "open", "close"))
    return np.log(h / c) * np.log(h / o) + np.log(l_ / c) * np.log(l_ / o)


def rogers_satchell(ohlc, n: int = 252) -> float:
    """Волатильность Роджерса–Сатчелла (1991): σ² = mean(ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O)); годовая sqrt(n·σ²)."""
    df = _ohlc_frame(ohlc)
    if df.empty:
        return np.nan
    v = np.mean(_rs_terms(df))
    return float(np.sqrt(n * v)) if v >= 0 else np.nan


def yang_zhang(ohlc, n: int = 252) -> float:
    """Волатильность Янга–Чжана (2000): σ² = σo² + k·σc² + (1 − k)·σrs², k = 0.34 / (1.34 + (N+1)/(N−1)).
    Считается по парам соседних строк (t−1, t), где обе строки корректны (некорректная строка исключает
    пары с ней, а не склеивает соседей): σo² — выборочная дисперсия ln(O_t / C_{t−1}), σc² — ln(C_t / O_t),
    σrs² — среднее слагаемых Роджерса–Сатчелла строк t; N — число таких пар (нужно ≥ 2). Годовая sqrt(n·σ²)."""
    df, ok = _ohlc_raw(ohlc)
    t = np.flatnonzero(ok[1:] & ok[:-1]) + 1
    N = len(t)
    if N < 2:
        return np.nan
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    on = np.log(o[t] / c[t - 1])
    oc = np.log(c[t] / o[t])
    rs = _rs_terms(df.iloc[t])
    k = 0.34 / (1.34 + (N + 1) / (N - 1))
    v = np.var(on, ddof=1) + k * np.var(oc, ddof=1) + (1 - k) * np.mean(rs)
    return float(np.sqrt(n * v)) if v >= 0 else np.nan


def _split_list(splits) -> list[tuple[pd.Timestamp, float]]:
    """Сплиты -> [(дата, before/after)] в исходном порядке. splits — DataFrame с колонками date|tradedate,
    before, after или список dict с теми же ключами; записи без даты или с before/after ≤ 0 пропускаются."""
    if splits is None:
        return []
    rows = splits.to_dict("records") if isinstance(splits, pd.DataFrame) else list(splits)
    out = []
    for r in rows:
        d = r.get("date") or r.get("tradedate")
        try:
            b, a = float(r.get("before")), float(r.get("after"))
        except (TypeError, ValueError):
            continue
        if d is None or not (b > 0 and a > 0) or not (math.isfinite(b) and math.isfinite(a)):
            continue
        out.append((pd.Timestamp(str(d)[:10]), b / a))
    return out


def split_factors(dates, splits) -> np.ndarray:
    """Множитель цены на каждую дату: произведение before/after всех сплитов с датой строго позже этой даты
    (цены до сплита приводятся к текущему количеству акций; сплит 1:100 -> before=1, after=100 -> ×0,01)."""
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    f = np.ones(len(idx))
    for d, k in _split_list(splits):
        f[idx < d] *= k
    return f


def adjust_splits(x, splits):
    """Корректировка на сплиты/консолидации (стандартная обратная корректировка ряда к текущим акциям):
    цены (Series; колонки/ключи open, high, low, close) × split_factors, объём (volume) ÷ split_factors.
    x — pd.Series / pd.DataFrame (индекс — даты) или dict {dates, open, high, low, close, volume} (как в JS).
    Нет сплитов — копия без изменений."""
    if isinstance(x, dict):
        f = split_factors(x.get("dates") or [], splits)
        out = dict(x)
        for k in ("open", "high", "low", "close", "values"):
            if x.get(k) is not None:
                out[k] = [None if v is None else float(v) * f[i] for i, v in enumerate(x[k])]
        if x.get("volume") is not None:
            out["volume"] = [None if v is None else float(v) / f[i] for i, v in enumerate(x["volume"])]
        return out
    f = split_factors(x.index, splits)
    if isinstance(x, pd.Series):
        return x.astype(float) * f
    out = x.copy()
    for c in out.columns:
        lc = str(c).lower()
        if lc in ("open", "high", "low", "close"):
            out[c] = out[c].astype(float) * f
        elif lc == "volume":
            out[c] = out[c].astype(float) / f
    return out


def _minus_months(d: pd.Timestamp, k: int) -> pd.Timestamp:
    """Дата на k календарных месяцев раньше; день — не больше последнего дня месяца (как pd.DateOffset)."""
    y, m = d.year, d.month - k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    last = pd.Timestamp(year=y, month=m, day=1).days_in_month
    return pd.Timestamp(year=y, month=m, day=min(d.day, last))


def _value_at(p: pd.Series, target: pd.Timestamp):
    """Последняя точка ряда с датой ≤ target: (дата, значение) или None."""
    pos = p.index.searchsorted(target, side="right") - 1
    return (p.index[pos], float(p.iloc[pos])) if pos >= 0 else None


PERIOD_RETURN_KEYS = ["1D", "1W", "1M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y", "ALL"]


def _period_target(end_d: pd.Timestamp, period: str) -> pd.Timestamp:
    """Граница начала периода: 1D/1W — календарные дни назад, месяцы/годы — календарные с ограничением
    дня концом месяца, YTD — 31.12 прошлого года."""
    if period == "1D":
        return end_d - pd.Timedelta(days=1)
    if period == "1W":
        return end_d - pd.Timedelta(days=7)
    if period == "YTD":
        return pd.Timestamp(year=end_d.year - 1, month=12, day=31)
    months = {"1M": 1, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60, "10Y": 120}
    if period not in months:
        raise ValueError(f"неизвестный период: {period}")
    return _minus_months(end_d, months[period])


def period_start(dates, period: str, as_of=None) -> Optional[str]:
    """Дата начальной точки периода (та же логика, что в period_returns): последняя дата ≤ as_of − период
    (as_of по умолчанию — последняя дата); 'ALL' — первая дата. Нет такой точки — None.
    dates — DatetimeIndex / список дат по возрастанию; результат — 'YYYY-MM-DD'."""
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates))).sort_values()
    if not len(idx):
        return None
    if period == "ALL":
        return str(idx[0].date())
    end_d = pd.Timestamp(as_of) if as_of is not None else idx[-1]
    pos = idx.searchsorted(_period_target(end_d, period), side="right") - 1
    return str(idx[pos].date()) if pos >= 0 else None


def period_returns(prices: pd.Series, as_of=None) -> dict:
    """Доходность по периодам {'1D',…,'ALL': {ret, cagr, from} | None}.
    Конец — последняя точка ≤ as_of (по умолчанию последняя дата ряда); начало — последняя точка
    ≤ as_of − период (1D/1W — календарные дни, месяцы и годы — календарные, как pd.DateOffset;
    YTD — ≤ 31.12 прошлого года; ALL — первая точка). Нет такой точки — None.
    cagr — (P1/P0)^(365.25/дней) − 1 (как cagr()) для 3Y/5Y/10Y и для ALL длиннее года, иначе None."""
    p = prices.dropna().astype(float).sort_index()
    out = {k: None for k in PERIOD_RETURN_KEYS}
    if p.empty:
        return out
    end_d = pd.Timestamp(as_of) if as_of is not None else p.index[-1]
    end = _value_at(p, end_d)
    if end is None:
        return out
    d1, v1 = end
    for k in PERIOD_RETURN_KEYS:
        ds = period_start(p.index, k, end_d)
        if ds is None:
            continue
        i0 = p.index.searchsorted(pd.Timestamp(ds), side="right") - 1   # последняя точка этой даты
        d0, v0 = p.index[i0], float(p.iloc[i0])
        days = (d1 - d0).days
        long = k in ("3Y", "5Y", "10Y") or (k == "ALL" and days / 365.25 > 1)
        c = float((v1 / v0) ** (365.25 / days) - 1) if long and days > 0 else None
        out[k] = {"ret": float(v1 / v0 - 1), "cagr": c, "from": str(d0.date())}
    return out


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return min(max(x, lo), hi)


def momentum(prices: pd.Series) -> dict:
    """Моментум по дневным ценам. sma50/sma200 — среднее 50/200 последних точек (None, если точек меньше);
    high52 — максимум за 365 дней до последней даты включительно, distHigh52 = P / high52 − 1;
    mom6 / mom12 — доходность от точки ≤ T − 6 (12) мес. до точки ≤ T − 1 мес. (без последнего месяца).
    score 0–100 — среднее доступных подбаллов: P > SMA50, P > SMA200, SMA50 > SMA200 (0/100),
    clamp(50 + 250·mom6), clamp(50 + 250·mom12); нет ни одного — None."""
    p = prices.dropna().astype(float).sort_index()
    out = {"sma50": None, "sma200": None, "aboveSma50": None, "aboveSma200": None, "high52": None,
           "distHigh52": None, "mom6": None, "mom12": None, "score": None}
    if p.empty:
        return out
    last, T = float(p.iloc[-1]), p.index[-1]
    if len(p) >= 50:
        out["sma50"] = float(p.iloc[-50:].mean())
        out["aboveSma50"] = bool(last > out["sma50"])
    if len(p) >= 200:
        out["sma200"] = float(p.iloc[-200:].mean())
        out["aboveSma200"] = bool(last > out["sma200"])
    win = p[p.index >= T - pd.Timedelta(days=365)]
    out["high52"] = float(win.max())
    out["distHigh52"] = float(last / out["high52"] - 1)
    a = _value_at(p, _minus_months(T, 1))
    for key, k in (("mom6", 6), ("mom12", 12)):
        b = _value_at(p, _minus_months(T, k))
        if a is not None and b is not None:
            out[key] = float(a[1] / b[1] - 1)
    sub = []
    if out["aboveSma50"] is not None:
        sub.append(100.0 if out["aboveSma50"] else 0.0)
    if out["aboveSma200"] is not None:
        sub.append(100.0 if out["aboveSma200"] else 0.0)
    if out["sma50"] is not None and out["sma200"] is not None:
        sub.append(100.0 if out["sma50"] > out["sma200"] else 0.0)
    for key in ("mom6", "mom12"):
        if out[key] is not None:
            sub.append(_clamp(50 + 250 * out[key]))
    out["score"] = float(sum(sub) / len(sub)) if sub else None
    return out


def _median(xs: list) -> Optional[float]:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return float(xs[n // 2]) if n % 2 else float((xs[n // 2 - 1] + xs[n // 2]) / 2)


def monthly_grid(prices: pd.Series) -> dict:
    """Сетка помесячной доходности: месячные цены — последнее значение месяца (resample('ME').last()),
    база первого месяца — первая точка ряда (как annual_returns), если в первом месяце больше одной точки,
    иначе первый месяц — только база (ячейка None). cells[год][месяц−1] — доля или None;
    yearTotal — Π(1 + r) − 1 по месяцам года (None, если месяцев нет); medianByMonth — медиана по годам."""
    p = prices.dropna().astype(float).sort_index()
    if len(p) < 2:
        return {"years": [], "cells": [], "yearTotal": [], "medianByMonth": [None] * 12}
    me = p.resample("ME").last().dropna()
    first_month = (p.index.year == p.index[0].year) & (p.index.month == p.index[0].month)
    # база первого месяца — первая точка, если в месяце есть и более поздние точки; иначе
    # (месячный ряд) первый месяц — только база, без ячейки
    base = pd.concat([p.iloc[[0]], me]) if first_month.sum() > 1 else me
    r = (base / base.shift(1) - 1).iloc[1:]
    years = list(range(p.index[0].year, p.index[-1].year + 1))
    cells = [[None] * 12 for _ in years]
    for d, v in r.items():
        cells[d.year - years[0]][d.month - 1] = float(v)
    total = []
    for row in cells:
        xs = [v for v in row if v is not None]
        total.append(float(np.prod([1 + v for v in xs]) - 1) if xs else None)
    med = [_median([row[m] for row in cells if row[m] is not None]) for m in range(12)]
    return {"years": years, "cells": cells, "yearTotal": total, "medianByMonth": med}


def _drawdown_episodes(prices: pd.Series) -> list[dict]:
    """Эпизоды просадки: от пика (последняя точка с dd = 0) через дно (первый минимум) до восстановления
    (первая точка с dd = 0 после дна) или конца ряда. Даты — 'YYYY-MM-DD'."""
    p = prices.dropna().astype(float).sort_index()
    v = p.to_numpy()
    dates = p.index
    out, peak_i, i, n = [], 0, 1, len(v)
    run_max = v[0] if n else np.nan
    while i < n:
        if v[i] >= run_max:
            run_max, peak_i = v[i], i
            i += 1
            continue
        t_i, j = i, i
        while j < n and v[j] < run_max:
            if v[j] < v[t_i]:
                t_i = j
            j += 1
        rec = j if j < n else None
        out.append({
            "depth": float(v[t_i] / run_max - 1),
            "peak": str(dates[peak_i].date()), "trough": str(dates[t_i].date()),
            "recovery": str(dates[rec].date()) if rec is not None else None,
            "daysToTrough": int((dates[t_i] - dates[peak_i]).days),
            "daysToRecover": int((dates[rec] - dates[peak_i]).days) if rec is not None else None,
        })
        i = j
    return out


def top_drawdowns(prices: pd.Series, k: int = 5) -> list[dict]:
    """k крупнейших непересекающихся эпизодов просадки по глубине (при равенстве — более ранний):
    [{depth, peak, trough, recovery|None, daysToTrough, daysToRecover|None}], depth ≤ 0 в долях."""
    eps = _drawdown_episodes(prices)
    return sorted(eps, key=lambda e: e["depth"])[:k]


def current_drawdown(prices: pd.Series) -> dict:
    """Текущая просадка: depth = P_T / max P − 1, peak — последняя дата максимума, days — дней от пика."""
    p = prices.dropna().astype(float).sort_index()
    if p.empty:
        return {"depth": np.nan, "peak": None, "days": None}
    m = p.max()
    peak = p.index[np.flatnonzero(p.to_numpy() == m)[-1]]
    return {"depth": float(p.iloc[-1] / m - 1), "peak": str(peak.date()),
            "days": int((p.index[-1] - peak).days)}


def avg_drawdown(prices: pd.Series) -> float:
    """Средняя глубина эпизодов просадки (вкл. текущий невосстановленный), доли ≤ 0; NaN — просадок нет."""
    eps = _drawdown_episodes(prices)
    return float(np.mean([e["depth"] for e in eps])) if eps else np.nan


def total_return_series(close: pd.Series, dividends) -> pd.Series:
    """Ряд полной доходности с реинвестированием дивидендов в дату отсечки:
    TR_0 = P_0, TR_t = TR_{t−1} · (P_t + D_t) / P_{t−1}, D_t — сумма дивидендов с exDate, отнесённых на первую
    торговую дату ≥ exDate (позже последней даты или не позже первой — не учитываются).
    dividends — [{exDate|ex_date, value}] или Series (индекс — exDate). Стандартное определение индекса полной доходности."""
    p = close.dropna().astype(float).sort_index()
    if isinstance(dividends, pd.Series):
        items = [(pd.Timestamp(d), float(v)) for d, v in dividends.items()]
    else:
        items = [(pd.Timestamp(r.get("exDate") or r.get("ex_date")), float(r["value"]))
                 for r in (dividends or []) if (r.get("exDate") or r.get("ex_date")) and r.get("value") is not None]
    d = np.zeros(len(p))
    for day, v in items:
        pos = p.index.searchsorted(day)
        if 0 < pos < len(p):
            d[pos] += v
    v = p.to_numpy()
    tr = np.empty(len(v))
    if len(v):
        tr[0] = v[0]
        for i in range(1, len(v)):
            tr[i] = tr[i - 1] * (v[i] + d[i]) / v[i - 1]
    return pd.Series(tr, index=p.index, name=close.name)


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
    martin: float = np.nan
    ulcer_index: float = np.nan
    beta: float = np.nan
    alpha: float = np.nan
    treynor: float = np.nan
    m2: float = np.nan
    tracking_error: float = np.nan
    information_ratio: float = np.nan
    correlation: float = np.nan
    r_squared: float = np.nan
    up_capture: float = np.nan
    down_capture: float = np.nan

    def to_dict(self) -> dict:
        return asdict(self)


LABELS_RU = {
    "total_return": "Накопленная доходность",
    "cagr": "CAGR (среднегод.)",
    "volatility": "Волатильность (год.)",
    "max_drawdown": "Макс. просадка",
    "ulcer_index": "Индекс язвы",
    "sharpe": "Коэф. Шарпа",
    "sortino": "Коэф. Сортино",
    "calmar": "Коэф. Кальмара",
    "martin": "Коэф. Мартина",
    "omega": "Коэф. Омега",
    "schwager": "Коэф. Швагера",
    "beta": "Бета",
    "alpha": "Альфа Дженсена (год.)",
    "treynor": "Коэф. Трейнора",
    "m2": "M² Модильяни",
    "tracking_error": "Ошибка слежения",
    "information_ratio": "Информационный коэф.",
    "correlation": "Корреляция с бенчмарком",
    "r_squared": "R² с бенчмарком",
    "up_capture": "Захват роста (upside capture)",
    "down_capture": "Захват падения (downside capture)",
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
                  "best_period", "worst_period", "ulcer_index", "up_capture", "down_capture"}


def compute_all(prices: pd.Series, benchmark: Optional[pd.Series] = None,
                rf: RateLike = 0.0, freq: str = "M",
                return_method: str = "arith") -> MetricsReport:
    """
    Полный набор показателей по ряду стоимости.

    prices     — ряд цен/стоимости портфеля (любая исходная частота);
    benchmark  — ряд цен бенчмарка (например, MCFTR);
    rf         — безрисковая ставка (годовая) или ряд ставок;
    freq       — частота расчёта доходностей: 'D','W','M','Q' (по умолчанию — 'M');
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
        martin=martin(p, annual_rf(rf)), ulcer_index=ulcer_index(p),
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
        rep.r_squared = r_squared(rr, rbb)
        cap = capture_ratios(rr, rbb)
        rep.up_capture, rep.down_capture = cap["up"], cap["down"]
    return rep


def metrics_table(series: dict[str, pd.Series], benchmark: Optional[pd.Series] = None,
                  rf: RateLike = 0.0, freq: str = "M", return_method: str = "arith") -> pd.DataFrame:
    """Сводная таблица показателей: строки — показатели, столбцы — портфели."""
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
