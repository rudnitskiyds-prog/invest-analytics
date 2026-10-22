/**
 * Коэффициенты эффективности — порт core/analytics/metrics.py с теми же конвенциями.
 *
 * Набор показателей — стандартные коэффициенты эффективности портфеля (классические формулы).
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

/** CAGR = (P_T / P_0)^(1/лет) − 1, годы = календарные дни / 365,25 (стандартное определение). */
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

/** Кальмар K = CAGR / |MaxDD| (стандартное определение). */
export function calmar(s) {
  const mdd = maxDrawdown(s);
  return mdd < 0 ? cagr(s) / Math.abs(mdd) : NaN;
}

// ------------------------------------------------------------------ risk-adjusted
/** Шарп S = (Rp − Rf) / σp, годовой: mean(r − rf) / σ(r − rf) · √n (стандартное определение). */
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

/** Сортино So = (Rp − Rf) / σд (конвенция — как в metrics.py). */
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

/** Альфа Дженсена α = Rp − [Rf + β(Rm − Rf)], годовая (стандартное определение). */
export function jensenAlpha(r, rm, rf = 0, n) {
  n = nOf(r, n);
  const j = joinInner(r, rm);
  const p = perPeriodRate(rf, n, j.dates);
  const at = (i) => (Array.isArray(p) ? p[i] : p);
  const b = beta(j.dates ? { dates: j.dates, values: j.x } : j.x, j.dates ? { dates: j.dates, values: j.y } : j.y);
  const a = mean(j.x.map((x, i) => x - at(i))) - b * mean(j.y.map((y, i) => y - at(i)));
  return a * n;
}

/** Трейнор T = (Rp − Rf)·n / β (конвенция — как в metrics.py). */
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

/** Омега Ω = Σ max(r − L, 0) / Σ max(L − r, 0) (стандартное определение). */
export function omega(r, threshold = 0, n) {
  r = clean(r); n = nOf(r, n);
  const ex = excess(r, threshold, n);
  let g = 0, l = 0;
  for (const x of ex) { g += Math.max(x, 0); l += Math.max(-x, 0); }
  return l > 0 ? g / l : NaN;
}

/** Швагер Sw = ΣProfit / Σ|Loss| по доходностям периодов (стандартное определение). */
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

// ------------------------------------------------------------------ этап 1: просадки, бенчмарк, OHLC
// Тождественно core/analytics/metrics.py (паритет до 1e-9). Даты — строки 'YYYY-MM-DD'.

/** Индекс язвы UI = sqrt(mean(dd_t²)), dd_t = P_t / max_{s≤t} P_s − 1 (доли; Martin & McCann, 1989). */
export function ulcerIndex(s) {
  const dd = drawdownSeries(s).values;
  return dd.length ? Math.sqrt(mean(dd.map((x) => x * x))) : NaN;
}

/** Коэффициент Мартина = (CAGR − Rf) / UI; rf — годовая в долях; UI = 0 -> NaN. */
export function martin(s, rf = 0) {
  const ui = ulcerIndex(s);
  return ui > 0 ? (cagr(s) - Number(rf)) / ui : NaN;
}

/** R² = corr(Rp, Rm)². */
export function rSquared(r, rm) {
  const c = correlation(r, rm);
  return Number.isNaN(c) ? NaN : c * c;
}

/** Upside/downside capture (арифметический): up = Σ r_t / Σ rm_t по rm_t > 0, down — по rm_t < 0. */
export function captureRatios(r, rm) {
  const j = joinInner(r, rm);
  const ratio = (pred) => {
    let a = 0, b = 0, any = false;
    for (let i = 0; i < j.x.length; i++) if (pred(j.y[i])) { a += j.x[i]; b += j.y[i]; any = true; }
    return any && b !== 0 ? a / b : NaN;
  };
  return { up: ratio((y) => y > 0), down: ratio((y) => y < 0) };
}

/** Скользящая волатильность: ст. откл. (ddof = 1) доходностей окна × √n; точки без полного окна не выводятся. */
export function rollingVolatility(s, window = 21, n = 252) {
  const r = toReturns(clean(s));
  const dates = [], values = [];
  for (let i = window - 1; i < r.values.length; i++) {
    dates.push(r.dates[i]);
    values.push(std(r.values.slice(i - window + 1, i + 1)) * Math.sqrt(n));
  }
  return { dates, values };
}

/** OHLC {dates, open, high, low, close}: все строки с флагом ok (все четыре цены конечны и > 0). */
function ohlcAll(ohlc) {
  const out = [];
  const len = ohlc && ohlc.close ? ohlc.close.length : 0;
  const val = (a, i) => (a[i] === null || a[i] === undefined ? NaN : Number(a[i]));
  for (let i = 0; i < len; i++) {
    const o = val(ohlc.open, i), h = val(ohlc.high, i), l = val(ohlc.low, i), c = val(ohlc.close, i);
    out.push({ o, h, l, c, ok: [o, h, l, c].every((v) => Number.isFinite(v) && v > 0) });
  }
  return out;
}

/** Только корректные строки OHLC. */
function ohlcRows(ohlc) {
  return ohlcAll(ohlc).filter((r) => r.ok);
}

const LN2 = Math.log(2);
const rsTerm = (x) => Math.log(x.h / x.c) * Math.log(x.h / x.o) + Math.log(x.l / x.c) * Math.log(x.l / x.o);

/** Паркинсон (1980): σ² = mean(ln(H/L)²) / (4 ln 2); годовая √(n·σ²). */
export function parkinson(ohlc, n = 252) {
  const x = ohlcRows(ohlc);
  if (!x.length) return NaN;
  return Math.sqrt(n * mean(x.map((v) => Math.log(v.h / v.l) ** 2)) / (4 * LN2));
}

/** Гарман–Класс (1980): σ² = mean(½ ln(H/L)² − (2 ln 2 − 1) ln(C/O)²); годовая √(n·σ²). */
export function garmanKlass(ohlc, n = 252) {
  const x = ohlcRows(ohlc);
  if (!x.length) return NaN;
  const v = mean(x.map((r) => 0.5 * Math.log(r.h / r.l) ** 2 - (2 * LN2 - 1) * Math.log(r.c / r.o) ** 2));
  return v >= 0 ? Math.sqrt(n * v) : NaN;
}

/** Роджерс–Сатчелл (1991): σ² = mean(ln(H/C)ln(H/O) + ln(L/C)ln(L/O)); годовая √(n·σ²). */
export function rogersSatchell(ohlc, n = 252) {
  const x = ohlcRows(ohlc);
  if (!x.length) return NaN;
  const v = mean(x.map(rsTerm));
  return v >= 0 ? Math.sqrt(n * v) : NaN;
}

/**
 * Янг–Чжан (2000): σ² = σo² + k·σc² + (1 − k)·σrs², k = 0.34 / (1.34 + (N+1)/(N−1)); по парам соседних строк (t−1, t),
 * где обе корректны (некорректная строка исключает пары с ней): σo² — дисп. ln(O_t/C_{t−1}), σc² — ln(C_t/O_t)
 * (ddof = 1), σrs² — среднее Роджерса–Сатчелла строк t; N — число пар (≥ 2); годовая √(n·σ²).
 */
export function yangZhang(ohlc, n = 252) {
  const x = ohlcAll(ohlc);
  const on = [], oc = [], rs = [];
  for (let i = 1; i < x.length; i++) {
    if (!x[i].ok || !x[i - 1].ok) continue;
    on.push(Math.log(x[i].o / x[i - 1].c));
    oc.push(Math.log(x[i].c / x[i].o));
    rs.push(rsTerm(x[i]));
  }
  const N = on.length;
  if (N < 2) return NaN;
  const k = 0.34 / (1.34 + (N + 1) / (N - 1));
  const v = std(on) ** 2 + k * std(oc) ** 2 + (1 - k) * mean(rs);
  return v >= 0 ? Math.sqrt(n * v) : NaN;
}

/**
 * Сплиты -> [{date, k = before/after}] в исходном порядке; splits: [{date|tradedate, before, after}].
 * Записи без даты или с before/after ≤ 0 пропускаются (как _split_list в Python).
 */
function splitList(splits) {
  const out = [];
  for (const r of splits || []) {
    if (!r) continue;
    const d = r.date || r.tradedate;
    const b = Number(r.before), a = Number(r.after);
    if (!d || r.before == null || r.after == null || !(b > 0 && a > 0) || !Number.isFinite(b) || !Number.isFinite(a)) continue;
    out.push({ date: String(d).slice(0, 10), k: b / a });
  }
  return out;
}

/** Множитель цены на даты: произведение before/after сплитов с датой строго позже (split_factors в Python). */
export function splitFactors(dates, splits) {
  const f = new Array(dates.length).fill(1);
  for (const { date, k } of splitList(splits)) {
    for (let i = 0; i < dates.length; i++) if (dates[i] < date) f[i] *= k;
  }
  return f;
}

/**
 * Корректировка на сплиты/консолидации (adjust_splits в Python): цены (values / open, high, low, close) × множитель,
 * volume ÷ множитель; ряд приводится к текущему количеству акций. Принимает Series или OHLC, возвращает копию
 * того же вида (прочие поля сохраняются).
 */
export function adjustSplits(x, splits) {
  const f = splitFactors(x.dates || [], splits);
  const out = { ...x };
  for (const key of ['values', 'open', 'high', 'low', 'close']) {
    if (Array.isArray(x[key])) out[key] = x[key].map((v, i) => (v === null || v === undefined ? v : Number(v) * f[i]));
  }
  if (Array.isArray(x.volume)) out.volume = x.volume.map((v, i) => (v === null || v === undefined ? v : Number(v) / f[i]));
  return out;
}

const pad2 = (x) => String(x).padStart(2, '0');
const daysInMonth = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();   // m — 1..12

/** Дата на k календарных месяцев раньше; день ограничен концом месяца (как pd.DateOffset). */
function minusMonths(date, k) {
  const y0 = Number(date.slice(0, 4)), m0 = Number(date.slice(5, 7)), d0 = Number(date.slice(8, 10));
  let m = m0 - k;
  const y = y0 + Math.floor((m - 1) / 12);
  m = ((((m - 1) % 12) + 12) % 12) + 1;
  return `${y}-${pad2(m)}-${pad2(Math.min(d0, daysInMonth(y, m)))}`;
}

function minusDays(date, k) {
  return new Date(Date.parse(date + 'T00:00:00Z') - k * 86400000).toISOString().slice(0, 10);
}

/** Индекс последней точки с датой ≤ target (−1 — нет). */
function idxAt(dates, target) {
  let lo = 0, hi = dates.length;            // первая дата > target
  while (lo < hi) { const mid = (lo + hi) >> 1; if (dates[mid] <= target) lo = mid + 1; else hi = mid; }
  return lo - 1;
}

export const PERIOD_RETURN_KEYS = ['1D', '1W', '1M', '6M', 'YTD', '1Y', '3Y', '5Y', '10Y', 'ALL'];

/**
 * Доходность по периодам {'1D',…,'ALL': {ret, cagr, from} | null} (period_returns в Python).
 * Начало — последняя точка ≤ asOf − период (YTD — ≤ 31.12 прошлого года, ALL — первая точка);
 * cagr = (P1/P0)^(365.25/дней) − 1 для 3Y/5Y/10Y и для ALL длиннее года, иначе null.
 */
const PERIOD_MONTHS = { '1M': 1, '6M': 6, '1Y': 12, '3Y': 36, '5Y': 60, '10Y': 120 };

function periodTarget(endD, period) {
  if (period === '1D') return minusDays(endD, 1);
  if (period === '1W') return minusDays(endD, 7);
  if (period === 'YTD') return `${Number(endD.slice(0, 4)) - 1}-12-31`;
  if (!(period in PERIOD_MONTHS)) throw new Error(`Неизвестный период: ${period}`);
  return minusMonths(endD, PERIOD_MONTHS[period]);
}

/**
 * Дата начальной точки периода (period_start в Python; та же логика, что в periodReturns):
 * последняя дата ≤ asOf − период (asOf по умолчанию — последняя дата; месяцы — календарные, день ограничен
 * концом месяца; YTD — ≤ 31.12 прошлого года); 'ALL' — первая дата. Нет такой точки — null.
 * @param {string[]} dates  даты 'YYYY-MM-DD' по возрастанию
 */
export function periodStart(dates, period, asOf = null) {
  if (!dates || !dates.length) return null;
  if (period === 'ALL') return dates[0];
  const i = idxAt(dates, periodTarget(asOf || dates[dates.length - 1], period));
  return i >= 0 ? dates[i] : null;
}

export function periodReturns(s, asOf = null) {
  const p = clean(s);
  const out = Object.fromEntries(PERIOD_RETURN_KEYS.map((k) => [k, null]));
  if (!p.dates.length) return out;
  const endD = asOf || p.dates[p.dates.length - 1];
  const ie = idxAt(p.dates, endD);
  if (ie < 0) return out;
  const d1 = p.dates[ie], v1 = p.values[ie];
  for (const k of PERIOD_RETURN_KEYS) {
    const ds = periodStart(p.dates, k, endD);
    if (ds === null) continue;
    const i0 = idxAt(p.dates, ds);
    const d0 = p.dates[i0], v0 = p.values[i0];
    const dd = days(d0, d1);
    const long = k === '3Y' || k === '5Y' || k === '10Y' || (k === 'ALL' && dd / 365.25 > 1);
    out[k] = { ret: v1 / v0 - 1, cagr: long && dd > 0 ? (v1 / v0) ** (365.25 / dd) - 1 : null, from: d0 };
  }
  return out;
}

const clamp = (x, lo = 0, hi = 100) => Math.min(Math.max(x, lo), hi);

/**
 * Моментум по дневным ценам (momentum в Python): SMA50/SMA200 — среднее последних 50/200 точек;
 * high52 — максимум за 365 дней до последней даты; mom6/mom12 — доходность от точки ≤ T − 6/12 мес.
 * до точки ≤ T − 1 мес.; score — среднее доступных подбаллов (0/100 и clamp(50 + 250·mom, 0, 100)).
 */
export function momentum(s) {
  const p = clean(s);
  const out = { sma50: null, sma200: null, aboveSma50: null, aboveSma200: null, high52: null,
    distHigh52: null, mom6: null, mom12: null, score: null };
  const len = p.values.length;
  if (!len) return out;
  const last = p.values[len - 1], T = p.dates[len - 1];
  if (len >= 50) { out.sma50 = mean(p.values.slice(-50)); out.aboveSma50 = last > out.sma50; }
  if (len >= 200) { out.sma200 = mean(p.values.slice(-200)); out.aboveSma200 = last > out.sma200; }
  const from = minusDays(T, 365);
  let hi = -Infinity;
  p.dates.forEach((d, i) => { if (d >= from) hi = Math.max(hi, p.values[i]); });
  out.high52 = hi;
  out.distHigh52 = last / hi - 1;
  const ia = idxAt(p.dates, minusMonths(T, 1));
  for (const [key, k] of [['mom6', 6], ['mom12', 12]]) {
    const ib = idxAt(p.dates, minusMonths(T, k));
    if (ia >= 0 && ib >= 0) out[key] = p.values[ia] / p.values[ib] - 1;
  }
  const sub = [];
  if (out.aboveSma50 !== null) sub.push(out.aboveSma50 ? 100 : 0);
  if (out.aboveSma200 !== null) sub.push(out.aboveSma200 ? 100 : 0);
  if (out.sma50 !== null && out.sma200 !== null) sub.push(out.sma50 > out.sma200 ? 100 : 0);
  for (const key of ['mom6', 'mom12']) if (out[key] !== null) sub.push(clamp(50 + 250 * out[key]));
  out.score = sub.length ? sum(sub) / sub.length : null;
  return out;
}

function median(a) {
  const x = a.slice().sort((u, v) => u - v);
  const n = x.length;
  if (!n) return null;
  return n % 2 ? x[n >> 1] : (x[n / 2 - 1] + x[n / 2]) / 2;
}

/**
 * Помесячная доходность (monthly_grid в Python): месячные цены — последнее значение месяца; база первого
 * месяца — первая точка, если в первом месяце больше одной точки (иначе первый месяц — только база).
 * @returns {{years:number[], cells:(number|null)[][], yearTotal:(number|null)[], medianByMonth:(number|null)[]}}
 */
export function monthlyGrid(s) {
  const p = clean(s);
  if (p.values.length < 2) return { years: [], cells: [], yearTotal: [], medianByMonth: new Array(12).fill(null) };
  const me = resample(p, 'M');
  const ym = (d) => d.slice(0, 7);
  const firstCount = p.dates.filter((d) => ym(d) === ym(p.dates[0])).length;
  const bd = firstCount > 1 ? [p.dates[0], ...me.dates] : me.dates;
  const bv = firstCount > 1 ? [p.values[0], ...me.values] : me.values;
  const y0 = Number(p.dates[0].slice(0, 4)), y1 = Number(p.dates[p.dates.length - 1].slice(0, 4));
  const years = [];
  for (let y = y0; y <= y1; y++) years.push(y);
  const cells = years.map(() => new Array(12).fill(null));
  for (let i = 1; i < bd.length; i++) {
    cells[Number(bd[i].slice(0, 4)) - y0][Number(bd[i].slice(5, 7)) - 1] = bv[i] / bv[i - 1] - 1;
  }
  const yearTotal = cells.map((row) => {
    const xs = row.filter((v) => v !== null);
    if (!xs.length) return null;
    let pr = 1;
    for (const v of xs) pr *= 1 + v;
    return pr - 1;
  });
  const medianByMonth = [];
  for (let m = 0; m < 12; m++) medianByMonth.push(median(cells.map((r) => r[m]).filter((v) => v !== null)));
  return { years, cells, yearTotal, medianByMonth };
}

/** Эпизоды просадки: пик (последняя точка с dd = 0) -> дно (первый минимум) -> восстановление | конец ряда. */
function drawdownEpisodes(s) {
  const p = clean(s);
  const v = p.values, dates = p.dates, n = v.length;
  const out = [];
  if (!n) return out;
  let runMax = v[0], peak = 0, i = 1;
  while (i < n) {
    if (v[i] >= runMax) { runMax = v[i]; peak = i; i++; continue; }
    let t = i, j = i;
    while (j < n && v[j] < runMax) { if (v[j] < v[t]) t = j; j++; }
    const rec = j < n ? j : null;
    out.push({
      depth: v[t] / runMax - 1, peak: dates[peak], trough: dates[t],
      recovery: rec !== null ? dates[rec] : null,
      daysToTrough: days(dates[peak], dates[t]),
      daysToRecover: rec !== null ? days(dates[peak], dates[rec]) : null,
    });
    i = j;
  }
  return out;
}

/** k крупнейших непересекающихся просадок по глубине (при равенстве — более ранняя). */
export function topDrawdowns(s, k = 5) {
  return drawdownEpisodes(s).slice().sort((a, b) => a.depth - b.depth).slice(0, k);
}

/** Текущая просадка {depth, peak, days}: peak — последняя дата максимума. */
export function currentDrawdown(s) {
  const p = clean(s);
  if (!p.values.length) return { depth: NaN, peak: null, days: null };
  let m = -Infinity, pi = 0;
  p.values.forEach((v, i) => { if (v >= m) { m = v; pi = i; } });
  const last = p.values.length - 1;
  return { depth: p.values[last] / m - 1, peak: p.dates[pi], days: days(p.dates[pi], p.dates[last]) };
}

/** Средняя глубина эпизодов просадки (вкл. текущий), доли ≤ 0; нет просадок — NaN. */
export function avgDrawdown(s) {
  const e = drawdownEpisodes(s);
  return e.length ? mean(e.map((x) => x.depth)) : NaN;
}

/**
 * Ряд полной доходности: TR_0 = P_0, TR_t = TR_{t−1}·(P_t + D_t)/P_{t−1}; дивиденд — на первую торговую дату ≥ exDate
 * (позже последней или не позже первой даты — не учитывается). dividends: [{exDate, value}].
 */
export function totalReturnSeries(close, dividends) {
  const p = clean(close);
  const n = p.values.length;
  const d = new Array(n).fill(0);
  for (const r of dividends || []) {
    const ex = r && (r.exDate || r.ex_date);
    if (!ex || r.value === null || r.value === undefined) continue;
    const pos = idxAt(p.dates, minusDays(String(ex).slice(0, 10), 1)) + 1;   // первая дата ≥ exDate
    if (pos > 0 && pos < n) d[pos] += Number(r.value);
  }
  const values = new Array(n);
  if (n) values[0] = p.values[0];
  for (let i = 1; i < n; i++) values[i] = values[i - 1] * (p.values[i] + d[i]) / p.values[i - 1];
  return { dates: p.dates.slice(), values };
}

// ------------------------------------------------------------------ summary
/** Подписи строк; порядок ключей = порядок строк таблицы (как LABELS_RU в Python). */
export const LABELS_RU = {
  total_return: 'Накопленная доходность',
  cagr: 'CAGR (среднегод.)',
  volatility: 'Волатильность (год.)',
  max_drawdown: 'Макс. просадка',
  ulcer_index: 'Индекс язвы',
  sharpe: 'Коэф. Шарпа',
  sortino: 'Коэф. Сортино',
  calmar: 'Коэф. Кальмара',
  martin: 'Коэф. Мартина',
  omega: 'Коэф. Омега',
  schwager: 'Коэф. Швагера',
  beta: 'Бета',
  alpha: 'Альфа Дженсена (год.)',
  treynor: 'Коэф. Трейнора',
  m2: 'M² Модильяни',
  tracking_error: 'Ошибка слежения',
  information_ratio: 'Информационный коэф.',
  correlation: 'Корреляция с бенчмарком',
  r_squared: 'R² с бенчмарком',
  up_capture: 'Захват роста (upside capture)',
  down_capture: 'Захват падения (downside capture)',
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
  'treynor', 'm2', 'tracking_error', 'var_95', 'cvar_95', 'positive_share', 'best_period', 'worst_period',
  'ulcer_index', 'up_capture', 'down_capture']);

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
    martin: martin(p, annualRf(rf)),
    ulcer_index: ulcerIndex(p),
    beta: NaN,
    alpha: NaN,
    treynor: NaN,
    m2: NaN,
    tracking_error: NaN,
    information_ratio: NaN,
    correlation: NaN,
    r_squared: NaN,
    up_capture: NaN,
    down_capture: NaN,
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
    rep.r_squared = rSquared(rr, rbb);
    const cap = captureRatios(rr, rbb);
    rep.up_capture = cap.up;
    rep.down_capture = cap.down;
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
 * Сводная таблица показателей (metrics_table): {rows: ключи по порядку LABELS_RU,
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
