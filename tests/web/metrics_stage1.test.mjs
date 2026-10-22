// Новые метрики этапа 1 (web/CONTRACT.md): паритет с Python (tests/fixtures/web_reference.json, блок stage1)
// до 1e-9 и ручные примеры. Эталон: python -m tests.web.make_reference.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as M from '../../web/lib/metrics.js';
import { reference, close, seriesFromRef } from './_helpers.mjs';

const TOL = 1e-9;
const EPS = 1e-12;

/** Глубокое сравнение результата JS с эталоном Python: числа — до TOL, null = NaN/null, ключи и порядок. */
function same(actual, expected, path) {
  if (expected === null || expected === undefined) {
    assert.ok(actual === null || actual === undefined || (typeof actual === 'number' && Number.isNaN(actual)),
      `${path}: ожидалось null/NaN, получено ${JSON.stringify(actual)}`);
    return;
  }
  if (typeof expected === 'number') { close(actual, expected, TOL, path); return; }
  if (typeof expected === 'string' || typeof expected === 'boolean') {
    assert.equal(actual, expected, path);
    return;
  }
  if (Array.isArray(expected)) {
    assert.ok(Array.isArray(actual), `${path}: ожидался массив`);
    assert.equal(actual.length, expected.length, `${path}: длина`);
    expected.forEach((v, i) => same(actual[i], v, `${path}[${i}]`));
    return;
  }
  assert.ok(actual && typeof actual === 'object', `${path}: ожидался объект, получено ${actual}`);
  assert.deepEqual(Object.keys(actual), Object.keys(expected), `${path}: ключи и порядок`);
  for (const k of Object.keys(expected)) same(actual[k], expected[k], `${path}.${k}`);
}

/** OHLC из эталона: null -> NaN (как в браузере после fetchOHLC). */
const ohlcNaN = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) =>
  [k, k === 'dates' ? v.slice() : v.map((x) => (x === null ? NaN : x))]));

const ST = () => reference().stage1;

// ------------------------------------------------------------------ паритет
describe('этап 1: паритет с Python до 1e-9', () => {
  test('эталон содержит блок stage1', () => assert.ok(ST(), 'перегенерируйте: python -m tests.web.make_reference'));

  for (const name of ['full', 'last_year', 'rows3', 'rows2', 'rows1']) {
    test(`OHLC-оценки (${name}): Паркинсон, Гарман–Класс, Роджерс–Сатчелл, Янг–Чжан; n = 252 и 12`, () => {
      const c = ST().ohlc[name];
      const fns = { parkinson: 'parkinson', garman_klass: 'garmanKlass', rogers_satchell: 'rogersSatchell', yang_zhang: 'yangZhang' };
      for (const [py, js] of Object.entries(fns)) {
        for (const n of [252, 12]) {
          same(M[js](c.input, n), c[`${py}_${n}`], `${name}.${js}(n=${n}) [null в данных]`);
          same(M[js](ohlcNaN(c.input), n), c[`${py}_${n}`], `${name}.${js}(n=${n}) [NaN в данных]`);
        }
      }
    });
  }

  for (const key of ['daily', 'mcftr_m', 'short', 'tiny', 'gap', 'flat', 'rising']) {
    test(`ряд ${key}: ulcer, martin, rolling, periodReturns, momentum, monthlyGrid, просадки`, () => {
      const c = ST().series[key];
      const s = seriesFromRef(c.input);
      same(M.ulcerIndex(s), c.ulcer, `${key}.ulcerIndex`);
      same(M.martin(s), c.martin_0, `${key}.martin(rf=0)`);
      same(M.martin(s, 0.05), c.martin_5, `${key}.martin(rf=5%)`);
      same(M.rollingVolatility(s), c.rolling_21_252, `${key}.rollingVolatility()`);
      same(M.rollingVolatility(s, 5, 12), c.rolling_5_12, `${key}.rollingVolatility(5,12)`);
      same(M.periodReturns(s), c.period, `${key}.periodReturns`);
      same(M.momentum(s), c.momentum, `${key}.momentum`);
      same(M.monthlyGrid(s), c.grid, `${key}.monthlyGrid`);
      same(M.topDrawdowns(s), c.top5, `${key}.topDrawdowns(5)`);
      same(M.topDrawdowns(s, 2), c.top2, `${key}.topDrawdowns(2)`);
      same(M.currentDrawdown(s), c.current, `${key}.currentDrawdown`);
      same(M.avgDrawdown(s), c.avg, `${key}.avgDrawdown`);
    });
  }

  test('periodReturns(s, asOf): високосный день, конец месяца, выходной, до начала и после конца ряда', () => {
    const s = seriesFromRef(ST().series.daily.input);
    for (const [d, exp] of Object.entries(ST().as_of)) same(M.periodReturns(s, d), exp, `periodReturns(asOf=${d})`);
  });

  test('totalReturnSeries: выходной exDate, две выплаты в день, на первую дату / вне ряда, ex_date, пустые поля', () => {
    const st = ST();
    same(M.totalReturnSeries(seriesFromRef(st.series.daily.input), st.divs), st.tr, 'totalReturnSeries');
  });

  test('captureRatios и rSquared: inner join, без периодов падения рынка', () => {
    const b = ST().bench;
    const r = seriesFromRef(b.r), rm = seriesFromRef(b.rm), rg = seriesFromRef(b.rg);
    same(M.captureRatios(r, rm), b.cap_div, 'cap IRDIVTR/MCFTR');
    same(M.rSquared(r, rm), b.r2_div, 'R² IRDIVTR/MCFTR');
    same(M.captureRatios(rg, rm), b.cap_gold, 'cap GOLD/MCFTR (разные календари)');
    same(M.rSquared(rg, rm), b.r2_gold, 'R² GOLD/MCFTR');
    const c = M.captureRatios(r, seriesFromRef(b.rm_up));
    same(c, b.cap_up_only, 'cap без rm < 0');
    assert.ok(Number.isNaN(c.down), 'down — NaN, если нет периодов падения рынка');
  });

  test('computeAll отдаёт новые ключи в порядке контракта', () => {
    const keys = Object.keys(reference().metrics.MCFTR);
    const at = (k) => keys.indexOf(k);
    assert.equal(at('martin'), at('days_to_recover') + 1);
    assert.equal(at('ulcer_index'), at('martin') + 1);
    assert.equal(at('beta'), at('ulcer_index') + 1);
    assert.equal(keys.slice(-4).join(), 'correlation,r_squared,up_capture,down_capture');
    const nb = reference().metrics.__MCFTR_nobench;
    for (const k of ['r_squared', 'up_capture', 'down_capture']) assert.equal(nb[k], null, `${k} без бенчмарка — NaN`);
    assert.ok(nb.martin !== null && nb.ulcer_index !== null, 'Мартин и UI считаются и без бенчмарка');
  });
});

// ------------------------------------------------------------------ ручные примеры
const ME = ['2020-01-31', '2020-02-29', '2020-03-31', '2020-04-30', '2020-05-31', '2020-06-30'];
const ser = (values, dates = ME) => ({ dates: dates.slice(0, values.length), values });

describe('этап 1: ручные примеры', () => {
  test('ulcerIndex: 100, 120, 90, 108, 120 → dd = 0, 0, −0.25, −0.1, 0', () => {
    const s = ser([100, 120, 90, 108, 120]);
    close(M.ulcerIndex(s), Math.sqrt((0.0625 + 0.01) / 5), EPS, 'UI');
    close(M.avgDrawdown(s), -0.25, EPS, 'один эпизод');
    const top = M.topDrawdowns(s);
    assert.equal(top.length, 1);
    assert.deepEqual(top[0], { depth: top[0].depth, peak: '2020-02-29', trough: '2020-03-31', recovery: '2020-05-31',
      daysToTrough: 31, daysToRecover: 92 });
    close(top[0].depth, -0.25, EPS, 'depth');
  });

  test('ulcerIndex монотонно растущего ряда = 0, Мартин — NaN; пустой ряд — NaN', () => {
    assert.equal(M.ulcerIndex(ser([1, 2, 3])), 0);
    assert.ok(Number.isNaN(M.martin(ser([1, 2, 3]))));
    assert.ok(Number.isNaN(M.ulcerIndex({ dates: [], values: [] })));
    assert.ok(Number.isNaN(M.avgDrawdown(ser([1, 2, 3]))));
    assert.deepEqual(M.topDrawdowns(ser([1, 2, 3])), []);
  });

  test('невосстановленная просадка: recovery = null, текущая просадка от последнего пика', () => {
    const s = ser([100, 120, 90, 100, 120, 110]);  // пик 120 повторён 31.05 — последняя дата максимума
    const top = M.topDrawdowns(s);
    assert.equal(top.length, 2);
    close(top[0].depth, -0.25, EPS, 'первый');
    assert.equal(top[1].recovery, null);
    assert.equal(top[1].daysToRecover, null);
    assert.equal(top[1].peak, '2020-05-31');
    const cur = M.currentDrawdown(s);
    close(cur.depth, 110 / 120 - 1, EPS, 'depth');
    assert.equal(cur.peak, '2020-05-31');
    assert.equal(cur.days, 30);
    close(M.avgDrawdown(s), (-0.25 + (110 / 120 - 1)) / 2, EPS, 'avg');
  });

  test('topDrawdowns: сортировка по глубине, при равенстве — более ранний; k обрезает', () => {
    const s = ser([100, 90, 100, 90, 100, 80]);
    const top = M.topDrawdowns(s);
    assert.deepEqual(top.map((d) => d.peak), ['2020-05-31', '2020-01-31', '2020-03-31']);
    assert.equal(M.topDrawdowns(s, 1).length, 1);
  });

  test('rSquared и captureRatios вручную', () => {
    const r = ser([0.02, -0.01, 0.03, -0.02]), rm = ser([0.01, -0.02, 0.02, -0.01]);
    const c = M.captureRatios(r, rm);
    close(c.up, 0.05 / 0.03, EPS, 'up');
    close(c.down, -0.03 / -0.03, EPS, 'down');
    const k = M.correlation(r, rm);
    close(M.rSquared(r, rm), k * k, EPS, 'R²');
    close(M.rSquared(r, ser([0.02, -0.01, 0.03, -0.02])), 1, EPS, 'R² с собой = 1');
  });

  test('rollingVolatility: только полные окна, окно 2 вручную', () => {
    const s = ser([100, 110, 99, 108.9]);          // r = 0.1, −0.1, 0.1
    const rv = M.rollingVolatility(s, 2, 12);
    assert.deepEqual(rv.dates, ['2020-03-31', '2020-04-30']);
    const sd = Math.sqrt(0.02);                    // std(0.1, −0.1) с ddof = 1
    close(rv.values[0], sd * Math.sqrt(12), EPS, 'σ1');
    close(rv.values[1], sd * Math.sqrt(12), EPS, 'σ2');
    assert.deepEqual(M.rollingVolatility(s, 21, 252), { dates: [], values: [] });
  });

  test('Паркинсон и Гарман–Класс на одной строке вручную; Янг–Чжан при N < 2 — NaN', () => {
    const o = { dates: ['2024-01-02'], open: [100], high: [110], low: [95], close: [105] };
    const hl = Math.log(110 / 95), co = Math.log(105 / 100);
    close(M.parkinson(o, 1), Math.sqrt(hl * hl / (4 * Math.log(2))), EPS, 'Parkinson');
    close(M.garmanKlass(o, 1), Math.sqrt(0.5 * hl * hl - (2 * Math.log(2) - 1) * co * co), EPS, 'GK');
    const rs = Math.log(110 / 105) * Math.log(110 / 100) + Math.log(95 / 105) * Math.log(95 / 100);
    close(M.rogersSatchell(o, 1), Math.sqrt(rs), EPS, 'RS');
    assert.ok(Number.isNaN(M.yangZhang(o)), 'YZ: 1 строка');
    const two = { dates: ['a', 'b'], open: [100, 101], high: [102, 103], low: [99, 100], close: [101, 102] };
    assert.ok(Number.isNaN(M.yangZhang(two)), 'YZ: 2 строки (N = 1)');
    assert.ok(Number.isNaN(M.parkinson({ dates: [], open: [], high: [], low: [], close: [] })), 'пусто');
  });

  test('Янг–Чжан на трёх строках вручную (N = 2, k = 0.34 / (1.34 + 3))', () => {
    const o = { dates: ['a', 'b', 'c'], open: [100, 102, 101], high: [103, 104, 105], low: [99, 100, 98], close: [101, 103, 104] };
    const on = [Math.log(102 / 101), Math.log(101 / 103)];
    const oc = [Math.log(103 / 102), Math.log(104 / 101)];
    const v2 = (a) => { const m = (a[0] + a[1]) / 2; return (a[0] - m) ** 2 + (a[1] - m) ** 2; };   // ddof = 1, n = 2
    const rs = (h, l, op, c) => Math.log(h / c) * Math.log(h / op) + Math.log(l / c) * Math.log(l / op);
    const mrs = (rs(104, 100, 102, 103) + rs(105, 98, 101, 104)) / 2;
    const k = 0.34 / (1.34 + 3);
    close(M.yangZhang(o, 1), Math.sqrt(v2(on) + k * v2(oc) + (1 - k) * mrs), EPS, 'YZ');
  });

  test('periodReturns: конец месяца (31.03 − 1 мес. = 29.02), YTD от 31.12, нет истории — null', () => {
    const s = { dates: ['2023-12-29', '2024-02-28', '2024-02-29', '2024-03-29', '2024-03-31'], values: [90, 95, 100, 105, 110] };
    const p = M.periodReturns(s);
    close(p['1M'].ret, 0.1, EPS, '1M от 29.02');
    assert.equal(p['1M'].from, '2024-02-29');
    assert.equal(p['1M'].cagr, null);
    close(p.YTD.ret, 110 / 90 - 1, EPS, 'YTD');
    assert.equal(p.YTD.from, '2023-12-29');
    assert.equal(p['1D'].from, '2024-03-29', '1D: последняя точка ≤ 30.03');
    assert.equal(p['1Y'], null);
    assert.equal(p['10Y'], null);
    assert.equal(p.ALL.from, '2023-12-29');
    assert.equal(p.ALL.cagr, null, 'ALL короче года — без CAGR');
  });

  test('periodReturns: CAGR для 3Y и ALL > 1 года', () => {
    const s = { dates: ['2020-01-31', '2023-01-31'], values: [100, 133.1] };
    const p = M.periodReturns(s);
    const days = 1096;
    close(p['3Y'].cagr, 1.331 ** (365.25 / days) - 1, EPS, '3Y cagr');
    close(p.ALL.cagr, p['3Y'].cagr, EPS, 'ALL cagr');
    // 1Y: последняя точка ≤ 31.01.2022 — 31.01.2020 (контракт: «последняя дата ≤ asOf − период»); CAGR — только > 1 года
    assert.equal(p['1Y'].from, '2020-01-31');
    assert.equal(p['1Y'].cagr, null);
  });

  test('momentum: < 50 точек — без SMA, score по доступным подбаллам; пустой ряд — все null', () => {
    const e = M.momentum({ dates: [], values: [] });
    assert.ok(Object.values(e).every((v) => v === null));
    const s = ser([100, 110, 99, 108.9]);
    const m = M.momentum(s);
    assert.equal(m.sma50, null);
    assert.equal(m.aboveSma200, null);
    close(m.high52, 110, EPS, 'high52');
    close(m.distHigh52, 108.9 / 110 - 1, EPS, 'dist');
    assert.equal(m.mom6, null, 'нет точки за 6 мес. до конца');
    assert.equal(m.score, null, 'нет ни одного подбалла');
  });

  test('monthlyGrid: месячный ряд — первый месяц только база; годовой итог и медиана', () => {
    const s = { dates: ['2020-11-30', '2020-12-31', '2021-01-31', '2021-12-31'], values: [100, 110, 99, 99] };
    const g = M.monthlyGrid(s);
    assert.deepEqual(g.years, [2020, 2021]);
    assert.equal(g.cells[0][10], null, 'ноябрь 2020 — база');
    close(g.cells[0][11], 0.1, EPS, 'дек 2020');
    close(g.cells[1][0], -0.1, EPS, 'янв 2021');
    close(g.cells[1][11], 0, EPS, 'дек 2021');
    close(g.yearTotal[0], 0.1, EPS, '2020');
    close(g.yearTotal[1], -0.1, EPS, '2021');
    close(g.medianByMonth[11], 0.05, EPS, 'медиана дек');
    assert.equal(g.medianByMonth[5], null);
    const d = M.monthlyGrid({ dates: ['2021-01-05', '2021-01-29'], values: [100, 105] });
    close(d.cells[0][0], 0.05, EPS, 'дневной ряд: база — первая точка месяца');
    assert.deepEqual(M.monthlyGrid(ser([100])).years, []);
  });

  test('totalReturnSeries вручную: дивиденд 10 на цене 100 → +10 %', () => {
    const s = { dates: ['2024-01-01', '2024-01-02', '2024-01-03'], values: [110, 100, 100] };
    const tr = M.totalReturnSeries(s, [{ exDate: '2024-01-02', value: 10 }]);
    assert.deepEqual(tr.dates, s.dates);
    close(tr.values[0], 110, EPS, 'TR0');
    close(tr.values[1], 110, EPS, 'TR1 = 110·(100+10)/110');
    close(tr.values[2], 110, EPS, 'TR2');
    const none = M.totalReturnSeries(s, []);
    assert.deepEqual(none.values, s.values);
    assert.deepEqual(M.totalReturnSeries({ dates: [], values: [] }, null), { dates: [], values: [] });
  });
});

// ------------------------------------------------------------------ periodStart (второй круг)
describe('periodStart: паритет с Python period_start и ручные случаи', () => {
  test('140 случаев эталона: дневной ряд и концы месяцев, asOf на 29.02 / 31.03 / 31.08 / до начала ряда', () => {
    const ps = ST().period_start;
    assert.ok(ps, 'перегенерируйте эталон: python -m tests.web.make_reference');
    const sets = { daily: seriesFromRef(ST().series.daily.input).dates, eom: ps.dates.eom };
    for (const c of ps.cases) {
      assert.equal(M.periodStart(sets[c.dates], c.period, c.as_of), c.result,
        `periodStart(${c.dates}, ${c.period}, asOf=${c.as_of})`);
    }
  });
  test('конец месяца: 31.03 − 1 мес. = 29.02; 31.08 − 6 мес. = 29.02; 29.02 − 1 год = 28.02', () => {
    const d = ['2023-02-27', '2023-02-28', '2023-03-01', '2024-02-28', '2024-02-29', '2024-03-01', '2024-03-02', '2024-03-31', '2024-08-31'];
    assert.equal(M.periodStart(d, '1M', '2024-03-31'), '2024-02-29');
    assert.equal(M.periodStart(d, '6M', '2024-08-31'), '2024-02-29');
    assert.equal(M.periodStart(d, '1Y', '2024-02-29'), '2023-02-28');
    assert.equal(M.periodStart(d, 'YTD', '2024-03-31'), '2023-03-01');
    assert.equal(M.periodStart(d, 'ALL'), '2023-02-27');
    assert.equal(M.periodStart(d, '10Y'), null);
    assert.equal(M.periodStart([], '1M'), null);
    assert.throws(() => M.periodStart(d, '2M'));
  });
  test('periodReturns использует ту же границу', () => {
    const s = seriesFromRef(ST().series.daily.input);
    const pr = M.periodReturns(s, '2024-03-31');
    for (const k of M.PERIOD_RETURN_KEYS) {
      assert.equal(pr[k] ? pr[k].from : null, M.periodStart(s.dates, k, '2024-03-31'), k);
    }
  });
});
