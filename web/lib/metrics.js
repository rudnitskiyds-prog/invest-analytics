/**
 * Коэффициенты эффективности — порт core/analytics/metrics.py с теми же конвенциями.
 *
 * Набор показателей — НИР «Сравнительный анализ пассивных стратегий управления инвестиционным
 * портфелем на российском финансовом рынке» (Рудницкий Д.С., 2026), п. 1.3, табл. 2 и табл. 4.
 * Формулы меняются только вместе с core/analytics/metrics.py и страницей «Методика» (инвариант 3).
 *
 * Соглашения (как в Python):
 *  * ряды — {dates, values}; функции доходностей принимают Series доходностей (или number[] —
 *    тогда считается, что ряды уже выровнены);
 *  * rf — годовая ставка в долях или Series годовых ставок; за период: (1 + rf)^(1/n) − 1;
 *  * стандартные отклонения — выборочные (ddof = 1);
 *  * пары рядов выравниваются по общим датам (inner join, как pd.concat(join='inner'));
 *  * асимметрия/эксцесс — как pandas .skew()/.kurt(); перцентили — линейные, как numpy.
 */
import { resample } from './data.js';

export const PERIODS = { D: 252, W: 52, M: 12, Q: 4, A: 1 };

// ------------------------------------------------------------------ helpers
const isSeries = (x) => x && !Array.isArray(x) && Array.isArray(x.values);
const vals = (x) => (Array.isArray(x) ? x : x.values);
const isNum = (v) => v !== null && v !== undefined && !Number.isNaN(v);

function clean(x) {
  if (Array.isArray(x)) return x.filter(isNum);
  const dates = [], values = [];
  x.values.forEach((v, i) => { if (isNum(v)) { dates.push(x.dates[i]); values.push(v); } });
  return { dates, values };
}

function sum(a) { let s = 0; for (const v of a) s += v; return s; }
function mean(a) { return a.length ? sum(a) / a.length : NaN; }

/** Выборочное стандартное отклонение (ddof = 1), как pandas .std(). */
function std(a, ddof = 1) {
  const n = a.length;
  if (n - ddof <= 0) return NaN;
  const m = mean(a);
  let s = 0;
  for (const v of a) s += (v - m) * (v - m);
  return Math.sqrt(s / (n - ddof));
}

const days = (a, b) => Math.round((Date.parse(b + 'T00:00:00Z') - Date.parse(a + 'T00:00:00Z')) / 86400000);

/** Число периодов в году по медианному шагу дат (infer_periods_per_year). */
export function inferPeriodsPerYear(dates) {
  if (!dates || dates.length < 3) return 252;
  const steps = [];
  for (let i = 1; i < dates.length; i++) steps.push(days(dates[i - 1], dates[i]));
  steps.sort((a, b) => a - b);
  const h = steps.length >> 1;
  const step = steps.length % 2 ? steps[h] : (steps[h - 1] + steps[h]) / 2;
  if (step <= 4) return 252;
  if (step <= 10) return 52;
  if (step <= 40) return 12;
  if (step <= 120) return 4;
  return 1;
}

const nOf = (r, n) => n || inferPeriodsPerYear(isSeries(r) ? r.dates : null);

/**
 * Годовая ставка -> ставка за период (per_period_rate): (1 + rf)^(1/n) − 1.
 * Для ряда ставок — значение на дату (ffill, до первой даты — bfill), длина = dates.length.
 * @returns {number | number[]}
 */
export function perPeriodRate(rf, n, dates = null) {
  if (isSeries(rf)) {
    if (!dates) throw new Error('Для ряда ставок нужны даты доходностей');
    const s = clean(rf);
    const out = [];
    let j = -1;
    for (const d of dates) {
      while (j + 1 < s.dates.length && s.dates[j + 1] <= d) j++;
      const v = j >= 0 ? s.values[j] : s.values[0];
      out.push((1 + v) ** (1 / n) - 1);
    }
    return out;
  }
  return (1 + Number(rf)) ** (1 / n) - 1;
}

/** Средняя годовая ставка (annual_rf). */
export function annualRf(rf) {
  return isSeries(rf) ? mean(clean(rf).values) : Number(rf);
}

/** Избыточные доходности r − rf за период. */
function excess(r, rf, n) {
  const v = vals(r);
  if (isSeries(rf) && !isSeries(r)) throw new Error('Ряд ставок требует доходностей с датами');
  const p = perPeriodRate(rf, n, isSeries(r) ? r.dates : null);
  return Array.isArray(p) ? v.map((x, i) => x - p[i]) : v.map((x) => x - p);
}

/** Inner join двух рядов по датам (без NaN). Для массивов — попарно по позиции. */
function joinInner(a, b) {
  if (!isSeries(a) || !isSeries(b)) {
    const x = vals(a), y = vals(b), xs = [], ys = [];
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      if (isNum(x[i]) && isNum(y[i])) { xs.push(x[i]); ys.push(y[i]); }
    }
    return { dates: null, x: xs, y: ys };
  }
  const mb = new Map();
  b.dates.forEach((d, i) => { if (isNum(b.values[i])) mb.set(d, b.values[i]); });
  const dates = [], x = [], y = [];
  a.dates.forEach((d, i) => {
    if (isNum(a.values[i]) && mb.has(d)) { dates.push(d); x.push(a.values[i]); y.push(mb.get(d)); }
  });
  return { dates, x, y };
}

function cov2(x, y) {
  const mx = mean(x), my = mean(y);
  let sxy = 0, syy = 0, sxx = 0;
  for (let i = 0; i < x.length; i++) {
    sxy += (x[i] - mx) * (y[i] - my);
    syy += (y[i] - my) * (y[i] - my);
    sxx += (x[i] - mx) * (x[i] - mx);
  }
  const d = x.length - 1;
  return { xy: sxy / d, yy: syy / d, xx: sxx / d };
}

/** Перцентиль с линейной интерполяцией (numpy.percentile, method='linear'). q — в процентах. */
export function percentile(a, q) {
  const x = a.filter(isNum).sort((u, v) => u - v);
  const n = x.length;
  if (!n) return NaN;
  const qq = q / 100;
  const vi = n * qq + (1 - qq) - 1;                // numpy _compute_virtual_index, alpha=beta=1
  let lo = Math.floor(vi);
  const t = vi - lo;
  if (lo < 0) return x[0];
  if (lo >= n - 1) return x[n - 1];
  const a0 = x[lo], b0 = x[lo + 1], diff = b0 - a0;
  return t >= 0.5 ? b0 - diff * (1 - t) : a0 + diff * t;   // numpy _lerp
}

// ------------------------------------------------------------------ basic
/** Простые доходности из цен (to_returns): pct_change без пропусков. Массив -> массив, Series -> Series. */
export function toReturns(prices) {
  if (Array.isArray(prices)) {
    const p = prices.filter(isNum), out = [];
    for (let i = 1; i < p.length; i++) out.push(p[i] / p[i - 1] - 1);
    return out;
  }
  const p = clean(prices), dates = [], values = [];
  for (let i = 1; i < p.values.length; i++) { dates.push(p.dates[i]); values.push(p.values[i] / p.values[i - 1] - 1); }
  return { dates, values };
}

/** Накопленная доходность: P_T / P_0 − 1. */
export function totalReturn(s) {
  const p = vals(clean(s));
  return p[p.length - 1] / p[0] - 1;
}

/** CAGR = (P_T / P_0)^(1/лет) − 1, годы = календарные дни / 365,25 (НИР, табл. 2). */
export function cagr(s) {
  const p = clean(s);
  const years = days(p.dates[0], p.dates[p.dates.length - 1]) / 365.25;
  if (years <= 0) return NaN;
  return (p.values[p.values.length - 1] / p.values[0]) ** (1 / years) - 1;
}

/** Волатильность σ·√n (ddof = 1). */
export function volatility(r, n) {
  return std(vals(clean(r))) * Math.sqrt(nOf(r, n));
}

/** Ряд просадок P / max(P) − 1. */
export function drawdownSeries(s) {
  const p = clean(s);
  let peak = -Infinity;
  return { dates: p.dates, values: p.values.map((v) => { peak = Math.max(peak, v); return v / peak - 1; }) };
}

/** Максимальная просадка (≤ 0). */
export function maxDrawdown(s) {
  return Math.min(...drawdownSeries(s).values);
}

/** Глубина, пик, дно, восстановление, дней до восстановления (max_drawdown_info). */
export function maxDrawdownInfo(s) {
  const p = clean(s);
  const dd = drawdownSeries(p).values;
  let ti = 0;
  for (let i = 1; i < dd.length; i++) if (dd[i] < dd[ti]) ti = i;      // idxmin — первое вхождение
  let pi = 0;
  for (let i = 1; i <= ti; i++) if (p.values[i] > p.values[pi]) pi = i; // idxmax — первое вхождение
  let ri = -1;
  for (let i = ti; i < p.values.length; i++) if (p.values[i] >= p.values[pi]) { ri = i; break; }
  return {
    max_drawdown: dd[ti],
    peak: p.dates[pi],
    trough: p.dates[ti],
    recovery: ri >= 0 ? p.dates[ri] : null,
    days_to_recover: ri >= 0 ? days(p.dates[pi], p.dates[ri]) : null,
  };
}

/** Кальмар K = CAGR / |MaxDD| (НИР, табл. 2). */
export function calmar(s) {
  const mdd = maxDrawdown(s);
  return mdd < 0 ? cagr(s) / Math.abs(mdd) : NaN;
}

// ------------------------------------------------------------------ risk-adjusted
/** Шарп S = (Rp − Rf) / σp, годовой: mean(r − rf) / σ(r − rf) · √n (НИР, табл. 2). */
export function sharpe(r, rf = 0, n) {
  r = clean(r); n = nOf(r, n);
  const ex = excess(r, rf, n);
  const sd = std(ex);
  return sd > 0 ? mean(ex) / sd * Math.sqrt(n) : NaN;
}

/** σд — среднеквадратичное отрицательных (ниже MAR) доходностей, годовое. */
export function downsideDeviation(r, mar = 0, n) {
  r = clean(r); n = nOf(r, n);
  const ex = excess(r, mar, n);
  return Math.sqrt(mean(ex.map((x) => Math.min(x, 0) ** 2))) * Math.sqrt(n);
}

/** Сортино So = (Rp − Rf) / σд (НИР, табл. 2; конвенция — как в metrics.py). */
export function sortino(r, rf = 0, n) {
  r = clean(r); n = nOf(r, n);
  const ex = excess(r, rf, n);
  const dd = downsideDeviation(r, rf, n);
  return dd > 0 ? mean(ex) * n / dd : NaN;
}

/** β = Cov(Rp, Rm) / Var(Rm) по общим датам; меньше 3 точек — NaN. */
export function beta(r, rm) {
  const j = joinInner(r, rm);
  if (j.x.length < 3) return NaN;
  const c = cov2(j.x, j.y);
  return c.yy > 0 ? c.xy / c.yy : NaN;
}

/** Корреляция Пирсона по общим датам. */
export function correlation(r, rm) {
  const j = joinInner(r, rm);
  if (j.x.length <= 2) return NaN;
  const c = cov2(j.x, j.y);
  return c.xy / Math.sqrt(c.xx * c.yy);
}

/** Альфа Дженсена α = Rp − [Rf + β(Rm − Rf)], годовая (НИР, табл. 2). */
export function jensenAlpha(r, rm, rf = 0, n) {
  n = nOf(r, n);
  const j = joinInner(r, rm);
  const p = perPeriodRate(rf, n, j.dates);
  const at = (i) => (Array.isArray(p) ? p[i] : p);
  const b = beta(j.dates ? { dates: j.dates, values: j.x } : j.x, j.dates ? { dates: j.dates, values: j.y } : j.y);
  const a = mean(j.x.map((x, i) => x - at(i))) - b * mean(j.y.map((y, i) => y - at(i)));
  return a * n;
}

/** Трейнор T = (Rp − Rf)·n / β (НИР, табл. 2; конвенция — как в metrics.py). */
export function treynor(r, rm, rf = 0, n) {
  r = clean(r); n = nOf(r, n);
  const b = beta(r, rm);
  const ex = mean(excess(r, rf, n)) * n;
  return b && !Number.isNaN(b) && b !== 0 ? ex / b : NaN;
}

/** Ошибка слежения TE = σ(Rp − Rm)·√n. */
export function trackingError(r, rm, n) {
  n = nOf(r, n);
  const j = joinInner(r, rm);
  return std(j.x.map((x, i) => x - j.y[i])) * Math.sqrt(n);
}

/** Информационный коэффициент IR = mean(Rp − Rm) / σ(Rp − Rm) · √n. */
export function informationRatio(r, rm, n) {
  n = nOf(r, n);
  const j = joinInner(r, rm);
  const a = j.x.map((x, i) => x - j.y[i]);
  const sd = std(a);
  return sd > 0 ? mean(a) / sd * Math.sqrt(n) : NaN;
}

/** M² Модильяни = Sp · σм + Rf. */
export function modiglianiM2(r, rm, rf = 0, n) {
  n = nOf(r, n);
  return sharpe(r, rf, n) * volatility(clean(rm), n) + annualRf(rf);
}

/** Омега Ω = Σ max(r − L, 0) / Σ max(L − r, 0) (НИР, табл. 2). */
export function omega(r, threshold = 0, n) {
  r = clean(r); n = nOf(r, n);
  const ex = excess(r, threshold, n);
  let g = 0, l = 0;
  for (const x of ex) { g += Math.max(x, 0); l += Math.max(-x, 0); }
  return l > 0 ? g / l : NaN;
}

/** Швагер Sw = ΣProfit / Σ|Loss| по доходностям периодов (НИР, табл. 2). */
export function schwager(r) {
  let g = 0, l = 0;
  for (const x of vals(clean(r))) { if (x > 0) g += x; else if (x < 0) l += -x; }
  return l > 0 ? g / l : NaN;
}

/** Исторический VaR: −перцентиль (1 − level) доходностей за период. */
export function varHistoric(r, level = 0.95) {
  return -percentile(vals(clean(r)), (1 - level) * 100);
}

/** Исторический CVaR: −среднее доходностей не выше VaR-порога. */
export function cvarHistoric(r, level = 0.95) {
  const x = vals(clean(r));
  const cut = percentile(x, (1 - level) * 100);
  return -mean(x.filter((v) => v <= cut));
}

function zeroOutFpErr(v, tol) { return Math.abs(v) < tol ? 0 : v; }

/** Асимметрия, как pandas Series.skew() (скорректированная Фишера–Пирсона). */
export function skew(a) {
  const x = vals(clean(a)), n = x.length;
  if (n < 3) return NaN;
  const m = sum(x) / n;
  let m2 = 0, m3 = 0, maxAbs = 0;
  for (const v of x) { const d = v - m; m2 += d * d; m3 += d * d * d; maxAbs = Math.max(maxAbs, Math.abs(v)); }
  const eps = 2.220446049250313e-16;
  m2 = zeroOutFpErr(m2, ((eps * maxAbs) ** 2) * n);
  m3 = zeroOutFpErr(m3, ((eps * maxAbs) ** 3) * n);
  if (m2 === 0) return 0;
  return (n * (n - 1) ** 0.5 / (n - 2)) * (m3 / m2 ** 1.5);
}

/** Эксцесс (избыточный), как pandas Series.kurt(). */
export function kurtosis(a) {
  const x = vals(clean(a)), n = x.length;
  if (n < 4) return NaN;
  const m = sum(x) / n;
  let m2 = 0, m4 = 0, maxAbs = 0;
  for (const v of x) { const d2 = (v - m) ** 2; m2 += d2; m4 += d2 * d2; maxAbs = Math.max(maxAbs, Math.abs(v)); }
  const eps = 2.220446049250313e-16;
  m2 = zeroOutFpErr(m2, ((eps * maxAbs) ** 2) * n);
  m4 = zeroOutFpErr(m4, ((eps * maxAbs) ** 4) * n);
  const adj = 3 * (n - 1) ** 2 / ((n - 2) * (n - 3));
  const num = n * (n + 1) * (n - 1) * m4;
  const den = (n - 2) * (n - 3) * m2 ** 2;
  if (den === 0) return 0;
  return num / den - adj;
}

// ------------------------------------------------------------------ summary
/** Подписи строк; порядок ключей = порядок строк таблицы (как LABELS_RU в Python). */
export const LABELS_RU = {
  total_return: 'Накопленная доходность',
  cagr: 'CAGR (среднегод.)',
  volatility: 'Волатильность (год.)',
  max_drawdown: 'Макс. просадка',
  sharpe: 'Коэф. Шарпа',
  sortino: 'Коэф. Сортино',
  calmar: 'Коэф. Кальмара',
  omega: 'Коэф. Омега',
  schwager: 'Коэф. Швагера',
  beta: 'Бета',
  alpha: 'Альфа Дженсена (год.)',
  treynor: 'Коэф. Трейнора',
  m2: 'M² Модильяни',
  tracking_error: 'Ошибка слежения',
  information_ratio: 'Информационный коэф.',
  correlation: 'Корреляция с бенчмарком',
  var_95: 'VaR 95% (за период)',
  cvar_95: 'CVaR 95% (за период)',
  skew: 'Асимметрия',
  kurtosis: 'Эксцесс',
  positive_share: 'Доля положит. периодов',
  best_period: 'Лучший период',
  worst_period: 'Худший период',
  days_to_recover: 'Дней до восстановления',
};

export const PERCENT_FIELDS = new Set(['total_return', 'cagr', 'volatility', 'max_drawdown', 'alpha',
  'treynor', 'm2', 'tracking_error', 'var_95', 'cvar_95', 'positive_share', 'best_period', 'worst_period']);

/**
 * Полный набор показателей по ряду стоимости (compute_all): сначала ресемплинг по freq
 * (цены на конец периода), затем доходности. Бенчмарк учитывается, если в нём > 3 точек.
 * Ключи и порядок — как dataclass MetricsReport; без бенчмарка его поля — NaN;
 * days_to_recover = null, если просадка не восстановлена.
 * @param {{dates:string[], values:number[]}} series
 * @param {{dates:string[], values:number[]}|null} benchmark
 * @param {number|{dates:string[], values:number[]}} rf
 * @param {'D'|'W'|'M'|'Q'|'A'} freq
 * @param {{returnMethod?: 'arith'|'cagr'}} [opts]
 */
export function computeAll(series, benchmark = null, rf = 0, freq = 'M', { returnMethod = 'arith' } = {}) {
  const p = resample(clean(series), freq);
  const n = PERIODS[freq];
  const r = toReturns(p);
  const rv = r.values;
  const rep = {
    start: p.dates[0],
    end: p.dates[p.dates.length - 1],
    periods_per_year: n,
    total_return: totalReturn(p),
    cagr: cagr(p),
    volatility: volatility(r, n),
    max_drawdown: maxDrawdown(p),
    sharpe: sharpe(r, rf, n),
    sortino: sortino(r, rf, n),
    calmar: calmar(p),
    omega: omega(r, rf, n),
    schwager: schwager(r),
    var_95: varHistoric(r),
    cvar_95: cvarHistoric(r),
    skew: skew(r),
    kurtosis: kurtosis(r),
    positive_share: rv.length ? rv.filter((x) => x > 0).length / rv.length : NaN,
    best_period: rv.length ? Math.max(...rv) : NaN,
    worst_period: rv.length ? Math.min(...rv) : NaN,
    days_to_recover: maxDrawdownInfo(p).days_to_recover,
    beta: NaN,
    alpha: NaN,
    treynor: NaN,
    m2: NaN,
    tracking_error: NaN,
    information_ratio: NaN,
    correlation: NaN,
  };
  if (returnMethod === 'cagr') {
    rep.sharpe = rep.volatility ? (rep.cagr - annualRf(rf)) / rep.volatility : NaN;
  }
  if (benchmark && clean(benchmark).values.length > 3) {
    const rb = toReturns(resample(clean(benchmark), freq));
    const j = joinInner(r, rb);
    const rr = { dates: j.dates, values: j.x }, rbb = { dates: j.dates, values: j.y };
    rep.beta = beta(rr, rbb);
    rep.alpha = jensenAlpha(rr, rbb, rf, n);
    rep.treynor = treynor(rr, rbb, rf, n);
    if (returnMethod === 'cagr' && rep.beta) rep.treynor = (rep.cagr - annualRf(rf)) / rep.beta;
    rep.m2 = rep.sharpe * volatility(rbb, n) + annualRf(rf);
    rep.tracking_error = trackingError(rr, rbb, n);
    rep.information_ratio = informationRatio(rr, rbb, n);
    rep.correlation = correlation(rr, rbb);
  }
  return rep;
}

/**
 * Доходность по календарным годам (ui/common.annual_returns в Python):
 * pd.concat([первая строка, resample('YE').last()]).pct_change().dropna(how='all').
 * База первого (неполного) года — первая точка ряда, далее последнее значение года к последнему
 * значению предыдущего. Пропуски (NaN) распространяются, как pct_change без заполнения.
 * @param {{dates:string[], values:number[]} | {dates:string[], cols:Object<string, number[]>}} x
 * @returns {{years:number[], values:number[]} | {years:number[], cols:Object<string, number[]>}}
 */
export function annualReturns(x) {
  const asSeries = isSeries(x);
  const cols = asSeries ? { v: x.values } : x.cols;
  const keys = Object.keys(cols);
  if (!x.dates.length) return asSeries ? { years: [], values: [] } : { years: [], cols: Object.fromEntries(keys.map((k) => [k, []])) };
  const norm = (v) => (isNum(v) ? v : NaN);
  const y0 = Number(x.dates[0].slice(0, 4)), y1 = Number(x.dates[x.dates.length - 1].slice(0, 4));
  // строки: первая точка (год y0), затем последнее непустое значение каждого года y0…y1 (пустой год — NaN)
  const rowYears = [y0];
  const rows = Object.fromEntries(keys.map((k) => [k, [norm(cols[k][0])]]));
  for (let y = y0; y <= y1; y++) {
    rowYears.push(y);
    for (const k of keys) {
      let last = NaN;
      for (let i = 0; i < x.dates.length; i++) {
        if (Number(x.dates[i].slice(0, 4)) === y && isNum(cols[k][i])) last = cols[k][i];
      }
      rows[k].push(last);
    }
  }
  // pct_change без заполнения, удаление строк, где все NaN, дубли лет — последнее
  const byYear = new Map();
  for (let i = 1; i < rowYears.length; i++) {
    const r = Object.fromEntries(keys.map((k) => [k, rows[k][i] / rows[k][i - 1] - 1]));
    if (keys.every((k) => Number.isNaN(r[k]))) continue;
    byYear.delete(rowYears[i]);
    byYear.set(rowYears[i], r);
  }
  const years = [...byYear.keys()];
  if (asSeries) return { years, values: years.map((y) => byYear.get(y).v) };
  return { years, cols: Object.fromEntries(keys.map((k) => [k, years.map((y) => byYear.get(y)[k])])) };
}

/**
 * Приведение к базе: значение / первое конечное значение × base (s / s.iloc[0] × 100 в интерфейсе).
 * Длина и даты сохраняются; NaN до первого конечного значения остаются NaN.
 * Принимает Series или Frame (тогда — по каждому столбцу отдельно).
 */
export function rebase(series, base = 100) {
  const one = (v) => {
    const i0 = v.findIndex(isNum);
    if (i0 < 0) return v.map(() => NaN);
    const b0 = v[i0];
    return v.map((x) => (isNum(x) ? x / b0 * base : NaN));
  };
  if (isSeries(series)) return { dates: series.dates.slice(), values: one(series.values) };
  return { dates: series.dates.slice(), cols: Object.fromEntries(Object.entries(series.cols).map(([k, v]) => [k, one(v)])) };
}

/**
 * Сводная таблица как табл. 4 НИР (metrics_table): {rows: ключи по порядку LABELS_RU,
 * cols: имена рядов, data: {имя: MetricsReport}}.
 */
export function metricsTable(seriesByName, benchmark = null, rf = 0, freq = 'M', opts = {}) {
  const data = {};
  for (const [name, s] of Object.entries(seriesByName)) data[name] = computeAll(s, benchmark, rf, freq, opts);
  return { rows: Object.keys(LABELS_RU), cols: Object.keys(seriesByName), data };
}

/** Человекочитаемое значение показателя: проценты с 1 знаком, коэффициенты — 3 знака, «—» для NaN. */
export function formatMetric(key, v) {
  if (v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))) return '—';
  if (PERCENT_FIELDS.has(key)) {
    return (v * 100).toFixed(1).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + '%';
  }
  if (key === 'days_to_recover') return String(Math.trunc(v));
  return Number(v).toFixed(3).replace('.', ',');
}
