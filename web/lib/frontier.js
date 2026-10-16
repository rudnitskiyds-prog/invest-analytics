/**
 * Граница эффективности Марковица без scipy — аналог core/analytics/frontier.py.
 *
 * Модель (Markowitz, 1952; стандартное определение): ожидаемые доходности — арифметическое
 * среднее × n (или CAGR актива), ковариации — выборочные (ddof = 1), годовые.
 * Ограничения: Σw = 1, wMin ≤ w_i ≤ wMax (только длинные позиции при wMin ≥ 0).
 *
 * Метод. Для каждого λ ≥ 0 решается выпуклая квадратичная задача
 *     min w'Σw − λ·μ'w   при  Σw = 1,  wMin ≤ w ≤ wMax
 * прямым методом активных множеств (точное решение за конечное число шагов, n ≤ ~20).
 *  * мин. дисперсия — λ = 0;
 *  * точка границы с целевой доходностью t — бисекция по λ (доходность решения монотонна по λ,
 *    путь w(λ) кусочно-линеен) + линейная интерполяция внутри найденного отрезка;
 *  * макс. Шарп — поиск по λ: сетка, затем золотое сечение (при положительной избыточной
 *    доходности касательный портфель лежит на эффективной границе и решает задачу с
 *    λ = 2σ²/(μ'w − rf)).
 * Облако случайных портфелей — Дирихле(α = 0,7) с детерминированным ГПСЧ (seed);
 * последовательность чисел отличается от numpy, но распределение то же.
 */
import { resample, dropNaNRows, ffillFrame } from './data.js';
import { PERIODS } from './metrics.js';

// ------------------------------------------------------------------ входы
/**
 * Годовые μ и Σ по ценам (estimate_inputs).
 * @param {{dates:string[], cols:Object<string, number[]>}} frame
 * @param {{freq?:string, rf?:number, muMethod?:'arith'|'cagr', shrink?:boolean}} [opts]
 * @returns {{assets:string[], mu:number[], cov:number[][], rf:number}}
 */
export function estimateInputs(frame, { freq = 'M', rf = 0, muMethod = 'arith', shrink = false } = {}) {
  const assets = Object.keys(frame.cols);
  const p = dropNaNRows(resample(ffillFrame(dropNaNRows(frame, 'all')), freq), 'any');
  const T = p.dates.length;
  if (T < 3) throw new Error('Недостаточно данных для оценки доходностей и ковариаций');
  const n = PERIODS[freq];
  const R = assets.map((a) => {
    const c = p.cols[a], out = [];
    for (let i = 1; i < T; i++) out.push(c[i] / c[i - 1] - 1);
    return out;
  });
  const t = T - 1;
  const means = R.map((r) => r.reduce((s, v) => s + v, 0) / t);
  let mu;
  if (muMethod === 'cagr') {
    const years = (Date.parse(p.dates[T - 1]) - Date.parse(p.dates[0])) / 86400000 / 365.25;
    mu = assets.map((a) => (p.cols[a][T - 1] / p.cols[a][0]) ** (1 / years) - 1);
  } else {
    mu = means.map((m) => m * n);
  }
  const k = assets.length;
  let S = Array.from({ length: k }, () => new Array(k).fill(0));
  for (let i = 0; i < k; i++) {
    for (let j = i; j < k; j++) {
      let s = 0;
      for (let q = 0; q < t; q++) s += (R[i][q] - means[i]) * (R[j][q] - means[j]);
      S[i][j] = S[j][i] = s / (t - 1);
    }
  }
  if (shrink) S = ledoitWolf(R, means);
  return { assets, mu, cov: S.map((row) => row.map((v) => v * n)), rf };
}

/** Сжатие ковариации к диагональной цели (упрощённый Ledoit–Wolf, 2004), как frontier.ledoit_wolf. */
function ledoitWolf(R, means) {
  const k = R.length, t = R[0].length;
  const X = Array.from({ length: t }, (_, q) => R.map((r, i) => r[q] - means[i]));
  const s = Array.from({ length: k }, (_, i) => Array.from({ length: k }, (_, j) => {
    let v = 0; for (let q = 0; q < t; q++) v += X[q][i] * X[q][j]; return v / t;
  }));
  let tr = 0; for (let i = 0; i < k; i++) tr += s[i][i];
  const m = tr / k;
  let d2 = 0;
  for (let i = 0; i < k; i++) for (let j = 0; j < k; j++) d2 += (s[i][j] - (i === j ? m : 0)) ** 2;
  let b2 = 0;
  for (let q = 0; q < t; q++) {
    for (let i = 0; i < k; i++) for (let j = 0; j < k; j++) b2 += (X[q][i] * X[q][j] - s[i][j]) ** 2;
  }
  b2 /= t * t;
  const sh = d2 > 0 ? Math.min(b2, d2) / d2 : 0;
  return s.map((row, i) => row.map((v, j) => (sh * (i === j ? m : 0) + (1 - sh) * v) * t / (t - 1)));
}

// ------------------------------------------------------------------ линейная алгебра
/** Решение A x = b методом Гаусса с выбором главного элемента; null при вырожденности. */
function solveLinear(A, b) {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
    if (Math.abs(M[piv][c]) < 1e-300) return null;
    [M[c], M[piv]] = [M[piv], M[c]];
    for (let r = c + 1; r < n; r++) {
      const f = M[r][c] / M[c][c];
      if (f === 0) continue;
      for (let q = c; q <= n; q++) M[r][q] -= f * M[c][q];
    }
  }
  const x = new Array(n);
  for (let r = n - 1; r >= 0; r--) {
    let s = M[r][n];
    for (let q = r + 1; q < n; q++) s -= M[r][q] * x[q];
    x[r] = s / M[r][r];
  }
  return x;
}

const dot = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; };
const matVec = (M, v) => M.map((row) => dot(row, v));

// ------------------------------------------------------------------ QP
/**
 * min w'Σw − λ·μ'w  при Σw = 1, lo ≤ w ≤ hi — прямой метод активных множеств
 * (Nocedal & Wright, Numerical Optimization, гл. 16.5). Возвращает веса.
 */
export function solveQP(cov, mu, lam, lo, hi) {
  const n = mu.length;
  if (n * lo > 1 + 1e-12 || n * hi < 1 - 1e-12) {
    throw new Error('Ограничения на веса несовместимы: нужно wMin·n ≤ 1 ≤ wMax·n');
  }
  const x = new Array(n).fill(1 / n);               // допустимая начальная точка
  if (hi - lo < 1e-15) return x;
  const state = new Array(n).fill(0);                // 0 — свободна, −1 — на нижней, +1 — на верхней
  const scale = 1 + Math.max(...cov.map((r, i) => Math.abs(r[i]))) + Math.abs(lam) * Math.max(...mu.map(Math.abs));
  for (let iter = 0; iter < 50 * (n + 5); iter++) {
    const G = matVec(cov, x).map((v, i) => 2 * v - lam * mu[i]);
    const F = [];
    for (let i = 0; i < n; i++) if (state[i] === 0) F.push(i);
    const k = F.length;
    // KKT: 2Σ_FF p + ν·1 = −G_F,  1'p = 0
    const A = Array.from({ length: k + 1 }, () => new Array(k + 1).fill(0));
    const b = new Array(k + 1).fill(0);
    for (let a = 0; a < k; a++) {
      for (let c = 0; c < k; c++) A[a][c] = 2 * cov[F[a]][F[c]];
      A[a][k] = 1; A[k][a] = 1;
      b[a] = -G[F[a]];
    }
    let sol = solveLinear(A, b);
    if (!sol) {                                      // вырожденная Σ на свободных — регуляризация
      for (let a = 0; a < k; a++) A[a][a] += 1e-12 * scale;
      sol = solveLinear(A, b);
      if (!sol) return x;
    }
    const p = new Array(n).fill(0);
    for (let a = 0; a < k; a++) p[F[a]] = sol[a];
    const nu = sol[k];
    const pmax = Math.max(0, ...p.map(Math.abs));
    if (pmax < 1e-13) {
      // проверка множителей зафиксированных границ: z = G_i + ν (нижняя ≥ 0), −(G_i + ν) (верхняя ≥ 0)
      let worst = -1, wv = -1e-12 * scale;
      for (let i = 0; i < n; i++) {
        if (state[i] === 0) continue;
        const z = state[i] < 0 ? G[i] + nu : -(G[i] + nu);
        if (z < wv) { wv = z; worst = i; }
      }
      if (worst < 0) return x;                       // KKT выполнены
      state[worst] = 0;
      continue;
    }
    // шаг с проверкой блокирующих границ
    let alpha = 1, block = -1, blockSide = 0;
    for (const i of F) {
      if (p[i] < 0) {
        const a = (lo - x[i]) / p[i];
        if (a < alpha) { alpha = a; block = i; blockSide = -1; }
      } else if (p[i] > 0) {
        const a = (hi - x[i]) / p[i];
        if (a < alpha) { alpha = a; block = i; blockSide = 1; }
      }
    }
    alpha = Math.max(alpha, 0);
    for (const i of F) x[i] += alpha * p[i];
    if (block >= 0) {
      state[block] = blockSide;
      x[block] = blockSide < 0 ? lo : hi;
    }
  }
  return x;
}

function point(w, inp) {
  const ret = dot(w, inp.mu);
  const vol = Math.sqrt(Math.max(dot(w, matVec(inp.cov, w)), 0));
  return { ret, vol, sharpe: vol > 0 ? (ret - inp.rf) / vol : NaN };
}

const wObj = (w, assets) => Object.fromEntries(assets.map((a, i) => [a, w[i]]));

/** Портфель с целевой доходностью t на эффективной границе (min_variance с target). */
function frontierAt(inp, t, lo, hi, wMv, rMv, rHi) {
  if (t <= rMv) return wMv.slice();
  const sol = (lam) => solveQP(inp.cov, inp.mu, lam, lo, hi);
  let L0 = 0, w0 = wMv, r0 = rMv;
  let L1 = 1, w1 = sol(L1), r1 = dot(w1, inp.mu);
  const tt = Math.min(t, rHi);
  while (r1 < tt - 1e-14 && L1 < 1e15) {
    L0 = L1; w0 = w1; r0 = r1;
    L1 *= 2; w1 = sol(L1); r1 = dot(w1, inp.mu);
  }
  if (r1 < tt - 1e-14) return w1;
  for (let it = 0; it < 200 && L1 - L0 > 1e-15 * L1; it++) {
    const Lm = (L0 + L1) / 2, wm = sol(Lm), rm = dot(wm, inp.mu);
    if (rm < tt) { L0 = Lm; w0 = wm; r0 = rm; } else { L1 = Lm; w1 = wm; r1 = rm; }
  }
  if (r1 - r0 <= 1e-16) return w1;
  const a = (tt - r0) / (r1 - r0);
  return w0.map((v, i) => v + a * (w1[i] - v));
}

/** Касательный портфель: максимум Шарпа вдоль пути w(λ). */
function maxSharpeWeights(inp, lo, hi) {
  const sol = (lam) => solveQP(inp.cov, inp.mu, lam, lo, hi);
  const S = (lam) => point(sol(lam), inp).sharpe;
  // сетка по λ: 0 и геометрическая прогрессия до выхода на максимальную доходность
  const grid = [0];
  let L = 1e-6;
  const wTop = sol(1e12), rTop = dot(wTop, inp.mu);
  while (L < 1e12) {
    grid.push(L);
    if (dot(sol(L), inp.mu) >= rTop - 1e-13) break;
    L *= 1.5;
  }
  const vals = grid.map(S);
  let k = 0;
  for (let i = 1; i < vals.length; i++) if (vals[i] > vals[k]) k = i;
  let a = grid[Math.max(k - 1, 0)], b = grid[Math.min(k + 1, grid.length - 1)];
  // золотое сечение на [a, b]
  const g = (Math.sqrt(5) - 1) / 2;
  let c = b - g * (b - a), d = a + g * (b - a), fc = S(c), fd = S(d);
  for (let it = 0; it < 200 && b - a > 1e-14 * Math.max(1, b); it++) {
    if (fc >= fd) { b = d; d = c; fd = fc; c = b - g * (b - a); fc = S(c); }
    else { a = c; c = d; fc = fd; d = a + g * (b - a); fd = S(d); }
  }
  let best = grid[k], bestS = vals[k];
  for (const [lam, s] of [[c, fc], [d, fd], [(a + b) / 2, S((a + b) / 2)]]) if (s > bestS) { best = lam; bestS = s; }
  return sol(best);
}

// ------------------------------------------------------------------ ГПСЧ и Дирихле
function mulberry32(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6D2B79F5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function makeGamma(rand) {
  let spare = null;
  const normal = () => {
    if (spare !== null) { const s = spare; spare = null; return s; }
    const u1 = 1 - rand(), u2 = rand();
    const r = Math.sqrt(-2 * Math.log(u1));
    spare = r * Math.sin(2 * Math.PI * u2);
    return r * Math.cos(2 * Math.PI * u2);
  };
  const gamma = (alpha) => {                      // Marsaglia & Tsang (2000)
    if (alpha < 1) return gamma(alpha + 1) * (1 - rand()) ** (1 / alpha);
    const d = alpha - 1 / 3, c = 1 / Math.sqrt(9 * d);
    for (;;) {
      const x = normal(), v0 = 1 + c * x;
      if (v0 <= 0) continue;
      const v = v0 * v0 * v0, u = 1 - rand();
      if (u < 1 - 0.0331 * x ** 4) return d * v;
      if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
    }
  };
  return gamma;
}

// ------------------------------------------------------------------ граница
/**
 * Эффективная граница (efficient_frontier).
 * @param {{assets:string[], mu:number[], cov:number[][], rf:number}} inputs
 * @returns {{frontier:{ret,vol,sharpe,w}[], random:{ret,vol,sharpe}[],
 *            minVar:{ret,vol,sharpe,w}, maxSharpe:{ret,vol,sharpe,w},
 *            assets:{key,ret,vol,sharpe}[]}}  w — объект {актив: доля}
 */
export function efficientFrontier(inputs, { nPoints = 40, wMin = 0, wMax = 1, nRandom = 4000, seed = 42 } = {}) {
  const { assets, mu, cov } = inputs;
  const inp = { ...inputs, rf: inputs.rf || 0 };
  const n = mu.length;
  const wMv = solveQP(cov, mu, 0, wMin, wMax);
  const rLo = dot(wMv, mu);
  // максимально достижимая доходность при ограничениях — жадно, как в Python
  const order = mu.map((m, i) => i).sort((a, b) => mu[b] - mu[a]);
  const wHi = new Array(n).fill(wMin);
  let left = 1 - wMin * n;
  for (const i of order) { const add = Math.min(wMax - wMin, left); wHi[i] += add; left -= add; }
  const rHi = dot(wHi, mu);

  const frontier = [];
  for (let k = 0; k < nPoints; k++) {
    const t = nPoints === 1 ? rLo : rLo + (rHi - rLo) * k / (nPoints - 1);
    const w = frontierAt(inp, t, wMin, wMax, wMv, rLo, rHi);
    frontier.push({ ...point(w, inp), w: wObj(w, assets) });
  }

  const wMs = maxSharpeWeights(inp, wMin, wMax);

  const rand = mulberry32(seed);
  const gamma = makeGamma(rand);
  const random = [];
  for (let s = 0; s < nRandom; s++) {
    const g = new Array(n);
    let tot = 0;
    for (let i = 0; i < n; i++) { g[i] = gamma(0.7); tot += g[i]; }
    const w = g.map((v) => v / tot);
    if ((wMin > 0 || wMax < 1) && w.some((v) => v < wMin - 1e-9 || v > wMax + 1e-9)) continue;
    random.push(point(w, inp));
  }

  return {
    frontier,
    random,
    minVar: { ...point(wMv, inp), w: wObj(wMv, assets) },
    maxSharpe: { ...point(wMs, inp), w: wObj(wMs, assets) },
    assets: assets.map((key, i) => {
      const vol = Math.sqrt(cov[i][i]);
      return { key, ret: mu[i], vol, sharpe: (mu[i] - inp.rf) / vol };
    }),
  };
}
