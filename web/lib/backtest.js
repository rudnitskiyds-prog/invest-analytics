/**
 * Движок бэктеста портфеля с ребалансировкой — порт core/analytics/backtest.py.
 *
 * Методика бэктеста платформы по умолчанию: начальный капитал 1 000 000 руб., без довнесений;
 * ребалансировка раз в год в последний торговый день декабря; без комиссий и налогов.
 * Дополнительно: частота ребалансировки (нет / месяц / квартал / полгода / год), коридор по
 * отклонению долей, комиссия, регулярные пополнения (распределяются к недостающим до цели долям),
 * налог на положительный финрезультат при продаже (по средней стоимости покупки, без ЛДВ и ИИС).
 */
import { sliceFrame } from './data.js';

export const REBAL_LABELS = {
  none: 'Без ребалансировки',
  M: 'Ежемесячно',
  Q: 'Ежеквартально',
  H: 'Раз в полгода',
  A: 'Раз в год (конец декабря)',
};

function periodKey(date, rule) {
  const y = Number(date.slice(0, 4)), m = Number(date.slice(5, 7));
  if (rule === 'M') return y * 100 + m;
  if (rule === 'Q') return y * 10 + Math.ceil(m / 3);
  if (rule === 'H') return y * 10 + (m > 6 ? 1 : 0);
  if (rule === 'A') return y;
  throw new Error(`Неизвестная частота: ${rule}`);
}

/**
 * Последний торговый день каждого периода в пределах дат ряда (period_end_dates);
 * последний день ряда не ребалансируется.
 * @param {string[]} dates — по возрастанию
 * @returns {Set<string>}
 */
export function periodEndDates(dates, rule) {
  const ends = new Map();
  for (const d of dates) {
    const k = periodKey(d, rule);
    const cur = ends.get(k);
    if (cur === undefined || d > cur) ends.set(k, d);
  }
  const out = new Set(ends.values());
  out.delete(dates[dates.length - 1]);
  return out;
}

const sumArr = (a) => a.reduce((s, v) => s + v, 0);

/**
 * Бэктест (run_backtest).
 * @param {{dates:string[], cols:Object<string, number[]>}} frame — цены активов
 * @param {{weights:Object<string,number>, initial?:number, rebalance?:'none'|'M'|'Q'|'H'|'A',
 *          band?:number|null, commission?:number, contribution?:number,
 *          contributionFreq?:'M'|'Q'|'H'|'A', taxRate?:number, start?:string, end?:string}} cfg
 * @returns {{equity, invested, weights, trades, rebalanceDates, costs, taxes, turnover}}
 */
export function runBacktest(frame, {
  weights, initial = 1e6, rebalance = 'A', band = null, commission = 0,
  contribution = 0, contributionFreq = 'M', taxRate = 0, start = null, end = null,
} = {}) {
  let assets = Object.keys(weights).filter((k) => Number(weights[k]) > 0);
  if (!assets.length) throw new Error('Не заданы веса активов');
  let target = assets.map((k) => Number(weights[k]));
  const tw = sumArr(target);
  if (Math.abs(tw - 1) > 1e-6) target = target.map((x) => x / tw);
  for (const a of assets) if (!(a in frame.cols)) throw new Error(`${a}: нет в данных`);

  // px = prices[w.index].loc[start:end].ffill().dropna()
  const sub = sliceFrame({ dates: frame.dates, cols: Object.fromEntries(assets.map((a) => [a, frame.cols[a]])) },
    start, end);
  const cols = assets.map((a) => sub.cols[a].map((v) => (v === null || v === undefined ? NaN : v)));
  for (const c of cols) for (let i = 1; i < c.length; i++) if (Number.isNaN(c[i])) c[i] = c[i - 1];
  const idx = [], P = [];
  for (let i = 0; i < sub.dates.length; i++) {
    const row = cols.map((c) => c[i]);
    if (row.every((v) => !Number.isNaN(v))) { idx.push(sub.dates[i]); P.push(row); }
  }
  if (idx.length < 2) throw new Error('Недостаточно данных для бэктеста: проверьте период и активы');

  const rebal = rebalance !== 'none' ? periodEndDates(idx, rebalance) : new Set();
  const contribDates = contribution > 0 ? periodEndDates(idx, contributionFreq) : new Set();
  const useBand = band !== null && band !== undefined && band > 0;
  const m = assets.length;

  let units = target.map((t, j) => initial * t / P[0][j]);
  const costBasis = target.map((t) => initial * t);
  let invested = initial;
  let costs = 0, taxes = 0, traded = 0;

  const eq = new Array(idx.length), inv = new Array(idx.length);
  const wts = assets.map(() => new Array(idx.length));
  const trades = assets.map((a, j) => ({ date: idx[0], asset: a, amount: initial * target[j], reason: 'Покупка' }));
  const rdates = [];

  for (let i = 0; i < idx.length; i++) {
    const d = idx[i];
    let values = units.map((u, j) => u * P[i][j]);
    let V = sumArr(values);
    const doContrib = contribDates.has(d);
    const curW = V > 0 ? values.map((v) => v / V) : target.slice();
    let doRebal;
    if (useBand) {
      // коридор: при календарной схеме проверяем только в даты ребалансировки, без календаря — каждый день
      const candidate = rebalance !== 'none' ? rebal.has(d) : i > 0;
      doRebal = candidate && Math.max(...curW.map((w, j) => Math.abs(w - target[j]))) > band;
    } else {
      doRebal = rebal.has(d);
    }
    if (doContrib) {
      V += contribution;
      invested += contribution;
    }
    if (doRebal || doContrib) {
      let tgtValues;
      if (doRebal) {
        tgtValues = target.map((t) => V * t);
      } else {
        // пополнение распределяется так, чтобы приблизиться к целевым долям
        const need = target.map((t, j) => Math.max(V * t - values[j], 0));
        const ns = sumArr(need);
        tgtValues = values.map((v, j) => v + contribution * (ns > 0 ? need[j] / ns : target[j]));
      }
      const delta = tgtValues.map((t, j) => t - values[j]);
      // налог на реализованную прибыль по продажам (средняя стоимость покупки)
      let tax = 0;
      if (taxRate > 0) {
        for (let j = 0; j < m; j++) {
          if (!(delta[j] < 0)) continue;
          const soldShare = values[j] > 0 ? -delta[j] / values[j] : 0;
          const gain = -delta[j] - costBasis[j] * soldShare;
          if (gain > 0) tax += gain * taxRate;
          costBasis[j] *= (1 - soldShare);
        }
      }
      const gross = sumArr(delta.map(Math.abs));
      const fee = gross * commission;
      costs += fee;
      taxes += tax;
      traded += gross;
      const scale = (V - fee - tax) / V;
      const newValues = values.map((v, j) => (v + delta[j]) * scale);
      for (let j = 0; j < m; j++) costBasis[j] += Math.max(delta[j], 0);
      units = newValues.map((v, j) => v / P[i][j]);
      for (let j = 0; j < m; j++) {
        if (Math.abs(delta[j]) > 1e-6) {
          trades.push({ date: d, asset: assets[j], amount: delta[j], reason: doRebal ? 'Ребалансировка' : 'Пополнение' });
        }
      }
      if (doRebal) rdates.push(d);
      values = units.map((u, j) => u * P[i][j]);
      V = sumArr(values);
    }
    eq[i] = V;
    inv[i] = invested;
    for (let j = 0; j < m; j++) wts[j][i] = values[j] / V;
  }

  const meanEq = sumArr(eq) / eq.length;
  return {
    equity: { dates: idx, values: eq },
    invested: { dates: idx.slice(), values: inv },
    weights: { dates: idx.slice(), cols: Object.fromEntries(assets.map((a, j) => [a, wts[j]])) },
    trades,
    rebalanceDates: rdates,
    costs,
    taxes,
    turnover: meanEq > 0 ? traded / meanEq : 0,
  };
}

/** Внутренняя норма доходности (xirr) для потоков [{date, amount}] (инвестиции < 0), бисекция. */
export function xirr(cashflows) {
  if (!cashflows || !cashflows.length) return NaN;
  const t0 = Date.parse(cashflows[0].date + 'T00:00:00Z');
  const ts = cashflows.map((c) => (Date.parse(c.date + 'T00:00:00Z') - t0) / 86400000 / 365.25);
  const npv = (r) => cashflows.reduce((s, c, i) => s + c.amount / (1 + r) ** ts[i], 0);
  let lo = -0.99, hi = 10;
  if (npv(lo) * npv(hi) > 0) return NaN;
  for (let k = 0; k < 200; k++) {
    const mid = (lo + hi) / 2;
    if (npv(lo) * npv(mid) <= 0) hi = mid; else lo = mid;
  }
  return (lo + hi) / 2;
}
