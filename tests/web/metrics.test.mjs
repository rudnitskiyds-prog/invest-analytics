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
    test(`ключ ${key} против MCFTR, постоянная rf (эталон), M`, () => {
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
  test('rf — ряд ставок (на начало периода, ffill; до первой даты — bfill)', () => {
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

  test('rf рядом ставок: ставка на НАЧАЛО периода (ffill), до первой даты — bfill', () => {
    const rf = { dates: ['2020-03-31', '2020-06-30'], values: [0.12, 0] };
    // начала периодов: 2019-11-02 (bfill), 2020-01-31 (bfill), 2020-04-30 (12 %), 2020-07-31 (0 %)
    const got = M.perPeriodRate(rf, 12, ['2020-01-31', '2020-04-30', '2020-07-31', '2020-10-31']);
    const m = 1.12 ** (1 / 12) - 1;
    closeArr(got, [m, m, m, 0], EPS, 'per-period');
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

// ------------------------------------------------------------------ безрисковая ставка рядом: начало периода
describe('periodStarts / perPeriodRate: ставка на начало периода доходности', () => {
  const pp = (x, n) => (1 + x) ** (1 / n) - 1;
  const RATE = { dates: ['2020-01-01', '2020-02-15'], values: [0.10, 0.20] };

  test('periodStarts: месячные даты — шаг назад для первой, далее предыдущая дата', () => {
    assert.deepEqual(M.periodStarts(['2020-01-31', '2020-02-29', '2020-03-31']), ['2020-01-02', '2020-01-31', '2020-02-29']);
  });
  test('periodStarts: дневные даты, одна дата, пусто', () => {
    assert.deepEqual(M.periodStarts(['2020-02-13', '2020-02-14', '2020-02-17']), ['2020-02-12', '2020-02-13', '2020-02-14']);
    assert.deepEqual(M.periodStarts(['2020-05-05']), ['2020-05-05']);
    assert.deepEqual(M.periodStarts([]), []);
  });
  test('perPeriodRate: месячные — на 29.02 уже 20 %, но период начался при 10 %', () => {
    const got = M.perPeriodRate(RATE, 12, ['2020-01-31', '2020-02-29', '2020-03-31']);
    closeArr(got, [pp(0.10, 12), pp(0.10, 12), pp(0.20, 12)], 1e-15, 'monthly');
  });
  test('perPeriodRate: дневные — публикация в день начала периода действует', () => {
    const rate = { dates: ['2020-01-01', '2020-02-14'], values: [0.10, 0.20] };
    const got = M.perPeriodRate(rate, 252, ['2020-02-13', '2020-02-14', '2020-02-17', '2020-02-18']);
    closeArr(got, [pp(0.10, 252), pp(0.10, 252), pp(0.20, 252), pp(0.20, 252)], 1e-15, 'daily');
  });
  test('perPeriodRate: число — как раньше', () => {
    close(M.perPeriodRate(0.12, 12), 1.12 ** (1 / 12) - 1, 1e-15, 'scalar');
  });
  test('Шарп с рядом ставок = ручной расчёт (как tests/test_rf_series_metrics.py)', () => {
    const r = { dates: ['2020-01-31', '2020-02-29', '2020-03-31', '2020-04-30'], values: [0.02, -0.01, 0.03, 0.015] };
    const rfp = [pp(0.10, 12), pp(0.10, 12), pp(0.20, 12), pp(0.20, 12)];
    const ex = r.values.map((x, i) => x - rfp[i]);
    const mean = ex.reduce((s, v) => s + v, 0) / 4;
    const sd = Math.sqrt(ex.reduce((s, v) => s + (v - mean) ** 2, 0) / 3);
    close(M.sharpe(r, RATE, 12), mean / sd * Math.sqrt(12), 1e-12, 'Sharpe');
  });
  test('постоянный ряд = число', () => {
    const r = { dates: ['2020-01-31', '2020-02-29', '2020-03-31', '2020-04-30'], values: [0.02, -0.01, 0.03, 0.015] };
    close(M.sharpe(r, { dates: ['2010-01-01'], values: [0.15] }, 12), M.sharpe(r, 0.15, 12), 1e-12, 'Sharpe const');
  });
});

// ------------------------------------------------------------------ ratesAtStarts / annualRf: окно ряда ставок не влияет
describe('ratesAtStarts / annualRf (как tests/test_rf_series_metrics.py)', () => {
  test('ставки на начала периодов и их среднее — вручную', () => {
    const rate = { dates: ['2021-12-15', '2022-02-10', '2022-03-15'], values: [0.08, 0.20, 0.17] };
    const dates = ['2022-01-31', '2022-02-28', '2022-03-31'];
    // начала: 2021-12-31 (8 %), 2022-01-31 (8 %), 2022-02-28 (20 %)
    closeArr(M.ratesAtStarts(rate, dates), [0.08, 0.08, 0.20], 1e-15, 'ratesAtStarts');
    close(M.annualRf(rate, dates), 0.12, 1e-15, 'annualRf с датами');
    close(M.annualRf(rate), 0.15, 1e-15, 'annualRf без дат — среднее значений');
    close(M.annualRf(0.07, dates), 0.07, 1e-15, 'число');
  });

  // ряд ставок 2014–2026 и его срез с 2021-12-01 (первой точкой — действующая ставка), бумага и бенчмарк с 2022
  const DAY = 86400000;
  const iso = (t) => new Date(t).toISOString().slice(0, 10);
  const longRate = (() => {
    const dates = [], values = [];
    for (let t = Date.parse('2014-01-10T00:00:00Z'), i = 0; t <= Date.parse('2026-06-10T00:00:00Z'); t += 61 * DAY, i++) {
      dates.push(iso(t)); values.push(0.06 + 0.1 * (1 + Math.sin(i / 3)) / 2);
    }
    return { dates, values };
  })();
  const shortRate = (() => {
    const start = '2021-12-01';
    let v = longRate.values[0];
    const dates = [start], values = [];
    longRate.dates.forEach((d, i) => { if (d <= start) v = longRate.values[i]; });
    values.push(v);
    longRate.dates.forEach((d, i) => { if (d > start) { dates.push(d); values.push(longRate.values[i]); } });
    return { dates, values };
  })();
  const ends = [];
  for (let y = 2022; y <= 2025; y++) for (let mth = 1; mth <= 12; mth++) ends.push(iso(Date.UTC(y, mth, 0)));
  const cum = (f) => { let p = 100; return ends.map((_, k) => (p *= 1 + f(k))); };
  const asset = { dates: ends, values: cum((k) => 0.01 + 0.04 * Math.sin(k * 1.3)) };
  const bench = { dates: ends, values: cum((k) => 0.008 + 0.03 * Math.cos(k * 0.7)) };

  for (const method of ['arith', 'cagr']) {
    test(`computeAll (${method}): длинный и короткий ряд ставок дают одинаковые коэффициенты`, () => {
      assert.ok(Math.abs(M.annualRf(longRate) - M.annualRf(shortRate)) > 1e-4, 'средние всего ряда различны');
      const a = M.computeAll(asset, bench, longRate, 'M', { returnMethod: method });
      const b = M.computeAll(asset, bench, shortRate, 'M', { returnMethod: method });
      for (const f of ['sharpe', 'sortino', 'treynor', 'alpha', 'm2', 'martin', 'omega']) {
        assert.ok(Number.isFinite(a[f]), f);
        close(a[f], b[f], 1e-12 * Math.max(1, Math.abs(b[f])), f);
      }
    });
  }

  test('M², Мартин, CAGR-Шарп/Трейнор берут среднее ставок на начала периодов', () => {
    const r = M.toReturns(asset);
    const rfa = M.annualRf(longRate, r.dates);
    const rep = M.computeAll(asset, bench, longRate, 'M');
    close(rep.martin, M.martin(asset, rfa), 1e-12, 'martin');
    const cg = M.computeAll(asset, bench, longRate, 'M', { returnMethod: 'cagr' });
    close(cg.sharpe, (cg.cagr - rfa) / cg.volatility, 1e-12, 'cagr sharpe');
    close(cg.treynor, (cg.cagr - rfa) / cg.beta, 1e-12, 'cagr treynor');
  });
});
