// Коэффициенты: паритет с Python (core/analytics/metrics.py) и граничные случаи на ручных примерах.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as M from '../../web/lib/metrics.js';
import { reference, close, closeArr, frameFromRef, seriesFromRef } from './_helpers.mjs';

const EPS = 1e-12;
const ME = ['2020-01-31', '2020-02-29', '2020-03-31', '2020-04-30', '2020-05-31', '2020-06-30'];
const ser = (values, dates = ME) => ({ dates: dates.slice(0, values.length), values });

// ------------------------------------------------------------------ паритет с Python
describe('computeAll: паритет с Python до 1e-9', () => {
  const ref = reference();
  const df = frameFromRef(ref.demo_frame);
  const { rf, start, end } = ref.params;
  const win = (k) => {
    const dates = [], values = [];
    df.dates.forEach((d, i) => {
      const v = df.cols[k][i];
      if (d >= start && d <= end && !Number.isNaN(v)) { dates.push(d); values.push(v); }
    });
    return { dates, values };
  };
  const bench = win('MCFTR');

  const check = (got, exp, label) => {
    assert.deepEqual(Object.keys(got), Object.keys(exp), `${label}: ключи и порядок как в MetricsReport`);
    for (const [k, v] of Object.entries(exp)) {
      if (typeof v === 'string') assert.equal(got[k], v, `${label}.${k}`);
      else close(got[k], v, 1e-9, `${label}.${k}`);
    }
  };

  for (const key of Object.keys(ref.metrics).filter((k) => !k.startsWith('__'))) {
    test(`ключ ${key} против MCFTR, rf=7,86 %, M`, () => {
      check(M.computeAll(win(key), bench, rf, 'M'), ref.metrics[key], key);
    });
  }
  test('без бенчмарка, rf = 0', () => {
    check(M.computeAll(win('MCFTR'), null, 0, 'M'), ref.metrics.__MCFTR_nobench, 'nobench');
  });
  test('квартальная частота', () => {
    check(M.computeAll(win('GOLD_CBR'), bench, rf, 'Q'), ref.metrics.__GOLD_CBR_Q, 'GOLD_Q');
  });
  test("returnMethod = 'cagr'", () => {
    check(M.computeAll(win('RGBITR'), bench, rf, 'M', { returnMethod: 'cagr' }), ref.metrics.__RGBITR_cagr, 'cagr');
  });
  test('rf — ряд ставок (ffill, до первой даты — bfill)', () => {
    const c = ref.rf_series;
    check(M.computeAll(win(c.asset), win(c.bench), seriesFromRef(c.rf), 'M'), c.report, 'rf-series');
  });
});

// ------------------------------------------------------------------ ручной пример
describe('ручной пример: цены 100, 110, 99, 108.9 (r = +10 %, −10 %, +10 %)', () => {
  const p = ser([100, 110, 99, 108.9]);
  const b = ser([100, 105, 99.75, 104.7375]);          // r_m = +5 %, −5 %, +5 %
  const rep = M.computeAll(p, b, 0, 'M');

  test('базовые', () => {
    close(rep.total_return, 0.089, EPS, 'TR');
    close(rep.volatility, 0.4, EPS, 'σ = s·√12 = 0.11547·3.4641');
    close(rep.max_drawdown, -0.1, EPS, 'MaxDD');
    close(rep.positive_share, 2 / 3, EPS, 'доля +');
    close(rep.best_period, 0.1, EPS, 'best');
    close(rep.worst_period, -0.1, EPS, 'worst');
    assert.equal(rep.days_to_recover, null, 'пик 110 (2020-02-29) не восстановлен: 108.9 < 110');
  });
  test('Шарп = 1, Сортино = 2, Омега = 2, Швагер = 2 (rf = 0)', () => {
    close(rep.sharpe, 1, EPS, 'Sharpe');
    close(rep.sortino, 2, EPS, 'Sortino: 0.4 / (√(0.01/3)·√12 = 0.2)');
    close(rep.omega, 2, EPS, 'Omega');
    close(rep.schwager, 2, EPS, 'Schwager');
  });
  test('VaR/CVaR 95 % и асимметрия', () => {
    close(rep.var_95, 0.08, EPS, 'VaR: перцентиль 5 % = −0.1 + 0.1·0.2');
    close(rep.cvar_95, 0.1, EPS, 'CVaR');
    close(rep.skew, -Math.sqrt(3), 1e-12, 'skew');
    assert.ok(Number.isNaN(rep.kurtosis), 'эксцесс при n < 4 — NaN');
  });
  test('β = 2, α = 0, Трейнор = 0.2, M² = 0.2, TE = 0.2, IR = 1, ρ = 1', () => {
    close(rep.beta, 2, EPS, 'beta');
    close(rep.alpha, 0, EPS, 'alpha');
    close(rep.treynor, 0.2, EPS, 'treynor');
    close(rep.m2, 0.2, EPS, 'm2');
    close(rep.tracking_error, 0.2, EPS, 'TE');
    close(rep.information_ratio, 1, EPS, 'IR');
    close(rep.correlation, 1, EPS, 'corr');
  });
});

// ------------------------------------------------------------------ граничные случаи
describe('граничные случаи', () => {
  test('ряд из 2 точек: одна доходность, σ и производные — NaN, без исключений', () => {
    const rep = M.computeAll(ser([100, 110]), null, 0.0786, 'M');
    close(rep.total_return, 0.1, EPS, 'TR');
    close(rep.cagr, 1.1 ** (365.25 / 29) - 1, 1e-12, 'CAGR по 29 дням');
    for (const k of ['volatility', 'sharpe', 'sortino', 'calmar', 'omega', 'schwager', 'skew', 'kurtosis']) {
      assert.ok(Number.isNaN(rep[k]), `${k} должен быть NaN, получено ${rep[k]}`);
    }
    close(rep.var_95, -0.1, EPS, 'VaR');
    assert.equal(rep.max_drawdown, 0);
    assert.equal(rep.days_to_recover, 0);
  });

  test('нулевая волатильность (цены ×2 каждый месяц): σ = 0, Шарп/Сортино — NaN, не ±Infinity', () => {
    const rep = M.computeAll(ser([100, 200, 400, 800]), null, 0, 'M');
    assert.equal(rep.volatility, 0);
    for (const k of ['sharpe', 'sortino', 'calmar', 'omega', 'schwager']) {
      assert.ok(Number.isNaN(rep[k]), `${k}: ${rep[k]}`);
    }
    assert.equal(rep.skew, 0, 'как pandas: постоянный ряд → 0');
    close(rep.total_return, 7, EPS, 'TR');
  });

  test('постоянные цены и бенчмарк с нулевой дисперсией: β, корреляция, IR — NaN', () => {
    const flat = ser([100, 100, 100, 100, 100]);
    const rep = M.computeAll(ser([100, 101, 99, 102, 103]), flat, 0, 'M');
    assert.ok(Number.isNaN(rep.beta), `beta ${rep.beta}`);
    assert.ok(Number.isNaN(rep.treynor), `treynor ${rep.treynor}`);
    assert.ok(Number.isNaN(rep.correlation), `corr ${rep.correlation}`);
  });

  test('нет бенчмарка (null) или ≤ 3 точек: поля относительно рынка — NaN', () => {
    const p = ser([100, 101, 99, 102, 103]);
    const BENCH = ['beta', 'alpha', 'treynor', 'm2', 'tracking_error', 'information_ratio', 'correlation'];
    for (const b of [null, ser([100, 101, 102])]) {
      const rep = M.computeAll(p, b, 0.05, 'M');
      for (const k of BENCH) assert.ok(Number.isNaN(rep[k]), `${k} при бенчмарке ${JSON.stringify(b)}: ${rep[k]}`);
      assert.ok(Number.isFinite(rep.sharpe));
    }
  });

  test('бенчмарк с другим календарём: inner join по датам доходностей', () => {
    const r = { dates: ['2020-02-29', '2020-03-31', '2020-04-30', '2020-05-31'], values: [0.1, -0.2, 0.05, 0.03] };
    const rb = { dates: ['2020-02-29', '2020-04-30', '2020-05-31', '2020-06-30'], values: [0.02, 0.04, -0.01, 0.5] };
    // общие даты: 02-29, 04-30, 05-31
    const x = [0.1, 0.05, 0.03], y = [0.02, 0.04, -0.01];
    close(M.beta(r, rb), M.beta(x, y), 1e-15, 'beta по общим датам');
    const my = (0.02 + 0.04 - 0.01) / 3, mx = 0.06;
    const cxy = x.reduce((s, v, i) => s + (v - mx) * (y[i] - my), 0) / 2;
    const vy = y.reduce((s, v) => s + (v - my) ** 2, 0) / 2;
    close(M.beta(r, rb), cxy / vy, 1e-12, 'beta вручную');
    close(M.trackingError(r, rb, 12), M.trackingError(x, y, 12), 1e-15, 'TE по общим датам');
  });

  test('rf рядом ставок: ставка на дату ffill, до первой даты — bfill', () => {
    const rf = { dates: ['2020-03-31', '2020-06-30'], values: [0.12, 0] };
    const got = M.perPeriodRate(rf, 12, ['2020-01-31', '2020-04-30', '2020-07-31']);
    const m = 1.12 ** (1 / 12) - 1;
    closeArr(got, [m, m, 0], EPS, 'per-period');
    // Шарп с рядом ставок = Шарп с теми же ставками, вычтенными вручную
    const p = ser([100, 103, 101, 106, 104, 108]);
    const r = M.toReturns(p);
    const rfS = { dates: ['2020-01-01', '2020-04-15'], values: [0.06, 0.18] };
    const per = M.perPeriodRate(rfS, 12, r.dates);
    const ex = r.values.map((x, i) => x - per[i]);
    const mean = ex.reduce((s, v) => s + v, 0) / ex.length;
    const sd = Math.sqrt(ex.reduce((s, v) => s + (v - mean) ** 2, 0) / (ex.length - 1));
    close(M.sharpe(r, rfS, 12), mean / sd * Math.sqrt(12), 1e-12, 'Sharpe(rf-ряд)');
    close(M.annualRf(rfS), 0.12, EPS, 'annualRf = среднее ставок');
  });

  test('days_to_recover = null, если просадка не восстановлена', () => {
    const rep = M.computeAll(ser([100, 120, 90, 100]), null, 0, 'M');
    assert.equal(rep.days_to_recover, null);
    close(rep.max_drawdown, -0.25, EPS, 'MaxDD');
    const info = M.maxDrawdownInfo(ser([100, 120, 90, 100]));
    assert.equal(info.peak, '2020-02-29');
    assert.equal(info.trough, '2020-03-31');
    assert.equal(info.recovery, null);
  });

  test('days_to_recover — календарные дни от пика до восстановления', () => {
    const rep = M.computeAll(ser([100, 120, 90, 121]), null, 0, 'M');
    assert.equal(rep.days_to_recover, 61);       // 2020-02-29 → 2020-04-30
  });
});

// ------------------------------------------------------------------ annualReturns / rebase
describe('annualReturns', () => {
  test('граница года — 31 декабря: точка 31.12 относится к уходящему году', () => {
    const s = { dates: ['2020-12-30', '2020-12-31', '2021-01-01'], values: [100, 110, 121] };
    const a = M.annualReturns(s);
    assert.deepEqual(a.years, [2020, 2021]);
    closeArr(a.values, [0.1, 0.1], EPS, 'годовые');
  });

  test('первый неполный год считается от первой точки ряда', () => {
    const s = { dates: ['2020-06-30', '2020-12-31', '2021-06-30', '2021-12-31'], values: [100, 120, 90, 108] };
    const a = M.annualReturns(s);
    assert.deepEqual(a.years, [2020, 2021]);
    closeArr(a.values, [0.2, -0.1], EPS, 'годовые');
  });

  test('пустой год: как ui/common.annual_returns (pandas) — год и следующий за ним пропадают', () => {
    // 2022 без точек: доходность 2022 = NaN, 2023 = 108.9 / NaN = NaN → строки удаляются dropna(how='all')
    const s = { dates: ['2020-06-30', '2020-12-31', '2021-12-31', '2023-03-31'], values: [100, 110, 99, 108.9] };
    const a = M.annualReturns(s);
    assert.deepEqual(a.years, [2020, 2021]);
    closeArr(a.values, [0.1, -0.1], EPS, 'годовые');
  });

  test('пустой ряд → пустой результат', () => {
    assert.deepEqual(M.annualReturns({ dates: [], values: [] }), { years: [], values: [] });
  });

  for (const name of ['fixture', 'daily']) {
    test(`паритет с Python: ${name} (Frame, NaN в столбцах)`, () => {
      const c = reference().annual_returns[name];
      const a = M.annualReturns(frameFromRef(c.input));
      assert.deepEqual(a.years, c.years);
      for (const [k, v] of Object.entries(c.cols)) closeArr(a.cols[k], v, 1e-12, `${name}.${k}`);
    });
  }
});

describe('rebase', () => {
  test('ведущие NaN остаются NaN, база — первое конечное значение, длина сохраняется', () => {
    const s = { dates: ['a', 'b', 'c', 'd', 'e', 'f'], values: [NaN, NaN, 50, 75, NaN, 100] };
    const r = M.rebase(s);
    assert.deepEqual(r.dates, s.dates);
    assert.ok(Number.isNaN(r.values[0]) && Number.isNaN(r.values[1]) && Number.isNaN(r.values[4]));
    closeArr([r.values[2], r.values[3], r.values[5]], [100, 150, 200], EPS, 'rebase');
  });
  test('Frame: каждый столбец к своей базе; base = 1', () => {
    const f = { dates: ['a', 'b'], cols: { x: [2, 4], y: [NaN, 5] } };
    const r = M.rebase(f, 1);
    closeArr(r.cols.x, [1, 2], EPS, 'x');
    assert.ok(Number.isNaN(r.cols.y[0]));
    close(r.cols.y[1], 1, EPS, 'y');
  });
  test('все NaN → все NaN', () => {
    const r = M.rebase({ dates: ['a'], values: [NaN] });
    assert.ok(Number.isNaN(r.values[0]));
  });
});

describe('справочники', () => {
  test('LABELS_RU / PERCENT_FIELDS / PERIODS как в Python', () => {
    assert.deepEqual(M.PERIODS, { D: 252, W: 52, M: 12, Q: 4, A: 1 });
    assert.equal(Object.keys(M.LABELS_RU).length, 29);
    assert.equal(Object.keys(M.LABELS_RU)[0], 'total_return');
    assert.equal(Object.keys(M.LABELS_RU).at(-1), 'days_to_recover');
    assert.equal(M.PERCENT_FIELDS.size, 16);
    // этап 1 (web/CONTRACT.md): места новых строк в таблице
    const keys = Object.keys(M.LABELS_RU);
    const after = (k, prev) => assert.equal(keys[keys.indexOf(k) - 1], prev, `${k} после ${prev}`);
    after('ulcer_index', 'max_drawdown');
    after('martin', 'calmar');
    after('r_squared', 'correlation');
    after('up_capture', 'r_squared');
    after('down_capture', 'up_capture');
    for (const k of ['ulcer_index', 'up_capture', 'down_capture']) assert.ok(M.PERCENT_FIELDS.has(k), k);
    for (const k of ['martin', 'r_squared']) assert.ok(!M.PERCENT_FIELDS.has(k), k);
  });
  test('LABELS_RU / PERCENT_FIELDS совпадают с Python (эталон)', () => {
    const ref = reference().labels;
    assert.ok(ref, 'нет reference().labels — перегенерируйте: python -m tests.web.make_reference');
    assert.deepEqual(Object.entries(M.LABELS_RU), ref.labels_ru);
    assert.deepEqual([...M.PERCENT_FIELDS].sort(), ref.percent_fields);
  });
  test('formatMetric: NaN → «—», проценты с запятой', () => {
    assert.equal(M.formatMetric('sharpe', NaN), '—');
    assert.equal(M.formatMetric('days_to_recover', null), '—');
    assert.equal(M.formatMetric('cagr', 0.1234), '12,3%');
    assert.equal(M.formatMetric('sharpe', 0.4786), '0,479');
  });
});
