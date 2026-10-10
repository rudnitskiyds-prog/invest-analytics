// Сверка JS-движка с табл. 4 НИР (как tests/test_nir_reproduction.py и scripts/reproduce_nir.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadDemoFrame, sliceFrame, clearCache } from '../../web/lib/data.js';
import { runBacktest } from '../../web/lib/backtest.js';
import { computeAll } from '../../web/lib/metrics.js';
import { STRATEGIES } from '../../web/lib/strategies.js';
import { fixtureFetch, reference, close } from './_helpers.mjs';

const RF = 0.0786;

// НИР, табл. 4 (scripts/reproduce_nir.NIR_TABLE4)
const ROWS = ['total_return', 'cagr', 'volatility', 'max_drawdown', 'sharpe', 'sortino', 'calmar',
  'beta', 'alpha', 'treynor', 'tracking_error'];
const NIR = {
  'Портфель 60/40': [2.711, 0.092, 0.140, -0.327, 0.154, 0.133, 0.282, 0.669, -0.001, 0.044, 0.075],
  'Постоянный портфель (Браун)': [3.908, 0.113, 0.080, -0.196, 0.422, 0.417, 0.578, 0.287, 0.024, 0.145, 0.155],
  'Всепогодный портфель (Далио)': [2.154, 0.081, 0.115, -0.281, 0.068, 0.059, 0.287, 0.490, -0.009, 0.032, 0.118],
  'Портфель 40/40/20': [4.086, 0.116, 0.095, -0.254, 0.391, 0.373, 0.457, 0.408, 0.023, 0.111, 0.129],
  'Лежебока плюс (Спирин)': [4.618, 0.123, 0.091, -0.217, 0.478, 0.500, 0.568, 0.297, 0.033, 0.173, 0.158],
  MCFTR: [2.842, 0.095, 0.203, -0.513, 0.169, 0.149, 0.185, 1.0, 0.0, 0.042, 0.0],
};
// допуски — как TOL в tests/test_nir_reproduction.py
const TOL = { total_return: 0.03, cagr: 0.002, volatility: 0.003, max_drawdown: 0.004, beta: 0.005,
  tracking_error: 0.002, sharpe: 0.02, calmar: 0.015, alpha: 0.003 };

// итоги до рубля (Python core, scripts/reproduce_nir)
const FINAL = {
  'Лежебока плюс (Спирин)': 5618574.82,
  'Портфель 60/40': 3711511.88,
  'Постоянный портфель (Браун)': 4908614.20,
  'Всепогодный портфель (Далио)': 3153998.13,
  'Портфель 40/40/20': 5104982.96,
  MCFTR: 3842074.60,
};

async function run() {
  const fetchImpl = fixtureFetch();
  clearCache(fetchImpl);
  const frame = await loadDemoFrame({ base: '../tests/fixtures/', fetchImpl });
  assert.ok(fetchImpl.calls.every((u) => !/^https?:/.test(u)), 'демо-фрейм не должен ходить в сеть');
  const px = sliceFrame(frame, '2011-01-31', '2025-12-31');
  const eq = {};
  const bt = {};
  for (const [name, s] of Object.entries(STRATEGIES)) {
    if (name.includes('Баффетт')) continue;
    bt[name] = runBacktest(px, { weights: s.weights, initial: 1e6, rebalance: 'A' });
    eq[name] = bt[name].equity;
  }
  const m = px.cols.MCFTR;
  eq.MCFTR = { dates: px.dates.slice(), values: m.map((v) => v / m[0] * 1e6) };
  const tbl = Object.fromEntries(Object.entries(eq).map(([k, s]) => [k, computeAll(s, eq.MCFTR, RF, 'M')]));
  return { px, eq, bt, tbl };
}

const R = await run();

test('период сверки: 180 месяцев 2011-01-31 … 2025-12-31', () => {
  assert.equal(R.px.dates[0], '2011-01-31');
  assert.equal(R.px.dates.at(-1), '2025-12-31');
  assert.equal(R.px.dates.length, 180);
});

for (const [name, v] of Object.entries(FINAL)) {
  test(`итоговая стоимость до рубля: ${name}`, () => {
    const got = R.eq[name].values.at(-1);
    assert.ok(Math.abs(got - v) < 0.01, `${name}: ${got.toFixed(2)} vs ${v.toFixed(2)}`);
  });
}

for (const col of Object.keys(NIR)) {
  test(`табл. 4 НИР в пределах допусков: ${col}`, () => {
    for (const [k, tol] of Object.entries(TOL)) {
      const exp = NIR[col][ROWS.indexOf(k)];
      const got = R.tbl[col][k];
      assert.ok(Math.abs(got - exp) <= tol + 1e-12, `${col}.${k}: ${got} vs НИР ${exp} (допуск ${tol})`);
    }
  });
}

test('табл. 4: все показатели совпадают с Python до 1e-9', () => {
  const ref = reference().backtest.metrics;
  for (const col of Object.keys(NIR)) {
    for (const [k, v] of Object.entries(ref[col])) {
      if (typeof v === 'string') assert.equal(R.tbl[col][k], v, `${col}.${k}`);
      else close(R.tbl[col][k], v, 1e-9, `${col}.${k}`);
    }
  }
});

test('ежегодная ребалансировка — 31 декабря 2011…2024 (14 дат), 2025-12-31 последняя и не ребалансируется', () => {
  const exp = reference().backtest.strategies['Портфель 60/40'].rebalance_dates;
  for (const [name, r] of Object.entries(R.bt)) {
    assert.deepEqual(r.rebalanceDates, exp, name);
  }
  assert.equal(exp.length, 14);
  assert.ok(exp.every((d) => d.endsWith('-12-31')));
});
