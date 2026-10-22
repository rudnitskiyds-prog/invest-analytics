// Бэктест: ручные примеры и паритет с core/analytics/backtest.py.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { runBacktest, periodEndDates, xirr, REBAL_LABELS } from '../../web/lib/backtest.js';
import { sliceFrame } from '../../web/lib/data.js';
import { reference, close, closeArr, frameFromRef } from './_helpers.mjs';

const EPS = 1e-9;
// A удваивается к 30.12.2020 и возвращается к 100; B постоянен
const F = {
  dates: ['2020-11-30', '2020-12-30', '2021-01-04', '2021-06-30', '2021-12-29', '2022-01-10'],
  cols: { A: [100, 200, 100, 100, 100, 100], B: [100, 100, 100, 100, 100, 100] },
};
const W = { A: 0.5, B: 0.5 };

describe('ребалансировка', () => {
  test("rebalance 'none' = buy & hold: V_t = Σ w·K·P_t/P_0", () => {
    const r = runBacktest(F, { weights: W, initial: 1000, rebalance: 'none' });
    closeArr(r.equity.values, [1000, 1500, 1000, 1000, 1000, 1000], EPS, 'equity');
    assert.deepEqual(r.rebalanceDates, []);
    assert.equal(r.trades.length, 2, 'только начальные покупки');
    closeArr(r.weights.cols.A, [0.5, 2 / 3, 0.5, 0.5, 0.5, 0.5], EPS, 'доля A');
  });

  test("rebalance 'A': в последний торговый день декабря, последний день ряда — нет", () => {
    const r = runBacktest(F, { weights: W, initial: 1000, rebalance: 'A' });
    assert.deepEqual(r.rebalanceDates, ['2020-12-30', '2021-12-29']);
    // 30.12: V = 1500 → 750/750; 04.01: A падает вдвое → 375 + 750
    closeArr(r.equity.values, [1000, 1500, 1125, 1125, 1125, 1125], EPS, 'equity');
    closeArr(r.weights.cols.A, [0.5, 0.5, 1 / 3, 1 / 3, 0.5, 0.5], EPS, 'доля A после ребалансировки');
    const rt = r.trades.filter((t) => t.reason === 'Ребалансировка');
    assert.deepEqual(rt.map((t) => [t.date, t.asset]), [['2020-12-30', 'A'], ['2020-12-30', 'B'],
      ['2021-12-29', 'A'], ['2021-12-29', 'B']]);
    close(rt[0].amount, -250, EPS, 'продажа A');
    close(rt[1].amount, 250, EPS, 'покупка B');
  });

  test('periodEndDates: M/Q/H/A по последней доступной дате периода', () => {
    const d = ['2021-01-29', '2021-02-26', '2021-03-30', '2021-06-29', '2021-07-01', '2021-12-30', '2022-01-14'];
    assert.deepEqual([...periodEndDates(d, 'A')].sort(), ['2021-12-30']);
    assert.deepEqual([...periodEndDates(d, 'H')].sort(), ['2021-06-29', '2021-12-30']);
    assert.deepEqual([...periodEndDates(d, 'Q')].sort(), ['2021-03-30', '2021-06-29', '2021-07-01', '2021-12-30']);
    assert.deepEqual([...periodEndDates(d, 'M')].sort(), d.slice(0, -1).sort());
    assert.throws(() => periodEndDates(d, 'X'));
  });

  test('коридор: ребалансировка, только если отклонение доли > band', () => {
    // в 30.12 доля A = 2/3, отклонение 1/6 ≈ 0.167
    const wide = runBacktest(F, { weights: W, initial: 1000, rebalance: 'A', band: 0.2 });
    assert.deepEqual(wide.rebalanceDates, []);
    closeArr(wide.equity.values, [1000, 1500, 1000, 1000, 1000, 1000], EPS, 'как buy&hold');
    const narrow = runBacktest(F, { weights: W, initial: 1000, rebalance: 'A', band: 0.1 });
    // 29.12.2021: после падения A доля A = 1/3 — снова вне коридора
    assert.deepEqual(narrow.rebalanceDates, ['2020-12-30', '2021-12-29']);
  });

  test("коридор без календаря ('none'): проверка каждый день, кроме первого", () => {
    const r = runBacktest(F, { weights: W, initial: 1000, rebalance: 'none', band: 0.1 });
    // 30.12 — 2/3 → ребаланс; 04.01 — A/B = 375/750 → доля 1/3 → ребаланс снова
    assert.deepEqual(r.rebalanceDates, ['2020-12-30', '2021-01-04']);
    closeArr(r.equity.values, [1000, 1500, 1125, 1125, 1125, 1125], EPS, 'equity');
  });
});

describe('пополнения, комиссия, налог, веса', () => {
  test('пополнения: invested нарастает, взнос уходит в недовешенный актив', () => {
    const r = runBacktest(F, { weights: W, initial: 1000, rebalance: 'none', contribution: 100, contributionFreq: 'M' });
    // даты взносов: концы месяцев, кроме последнего дня ряда: 30.11, 30.12, 04.01, 30.06, 29.12
    closeArr(r.invested.values, [1100, 1200, 1300, 1400, 1500, 1500], EPS, 'invested');
    // 30.11: V = 1100, нужда 50/50 → по 50; 30.12: A=1100, B=550, V=1750, нужда B = 325 → взнос в B
    const c = r.trades.filter((t) => t.reason === 'Пополнение');
    assert.deepEqual(c.filter((t) => t.date === '2020-12-30').map((t) => t.asset), ['B']);
    close(c.find((t) => t.date === '2020-12-30').amount, 100, EPS, 'взнос в B');
    close(r.equity.values[1], 1750, EPS, 'V 30.12');
    // 04.01: A=550, B=650 → взнос в A; 30.06 и 29.12 — по 50/50; итог 750 + 750
    close(r.equity.values.at(-1), 1500, EPS, 'итог');
    assert.ok(Math.abs(r.weights.cols.A.at(-1) - 0.5) < EPS);
  });

  test('комиссия уменьшает итог: 1 % от оборота (500 + 373,75)', () => {
    const base = runBacktest(F, { weights: W, initial: 1000, rebalance: 'A' });
    const fee = runBacktest(F, { weights: W, initial: 1000, rebalance: 'A', commission: 0.01 });
    // 30.12: оборот 500 → 5; после — по 747,5; 04.01: A = 373,75; 29.12: оборот 2·186,875 → 3,7375
    close(fee.costs, 8.7375, EPS, 'издержки');
    close(fee.equity.values.at(-1), 1121.25 - 3.7375, EPS, 'итог');
    assert.ok(fee.equity.values.at(-1) < base.equity.values.at(-1));
    close(fee.equity.values[1], 1495, EPS, 'V после комиссии 30.12');
  });

  test('НДФЛ 13 % с реализованной прибыли: продажа 250 из A (база 125) → 16.25', () => {
    const r = runBacktest(sliceFrame(F, null, '2021-01-04'),
      { weights: W, initial: 1000, rebalance: 'A', taxRate: 0.13 });
    close(r.taxes, 16.25, EPS, 'налог');
    close(r.equity.values[1], 1500 - 16.25, EPS, 'V после налога');
  });

  test('веса с суммой ≠ 1 нормируются; нулевые веса отбрасываются', () => {
    const a = runBacktest(F, { weights: { A: 0.5, B: 0.5 }, initial: 1000 });
    const b = runBacktest(F, { weights: { A: 2, B: 2, C: 0 }, initial: 1000 });
    closeArr(b.equity.values, a.equity.values, EPS, 'equity');
    assert.deepEqual(Object.keys(b.weights.cols), ['A', 'B']);
    const c = runBacktest(F, { weights: { A: 30, B: 10 }, initial: 1000, rebalance: 'none' });
    close(c.equity.values[1], 750 * 2 + 250, EPS, '75/25');
  });

  test('пропуски: ffill внутри ряда, старт — с первой даты, когда есть все активы', () => {
    const f = { dates: ['d1', 'd2', 'd3', 'd4'].map((_, i) => `2021-0${i + 1}-15`),
      cols: { A: [NaN, 100, NaN, 110], B: [50, 50, 55, NaN] } };
    const r = runBacktest(f, { weights: W, initial: 1000, rebalance: 'none' });
    assert.deepEqual(r.equity.dates, ['2021-02-15', '2021-03-15', '2021-04-15']);
    closeArr(r.equity.values, [1000, 1050, 1100], EPS, 'equity');
  });

  test('ошибки: нет весов, нет актива, меньше 2 строк', () => {
    assert.throws(() => runBacktest(F, { weights: {} }), /веса/);
    assert.throws(() => runBacktest(F, { weights: { Z: 1 } }), /Z/);
    assert.throws(() => runBacktest(F, { weights: W, start: '2022-01-10' }), /Недостаточно данных/);
  });

  test('REBAL_LABELS как в Python', () => {
    assert.deepEqual(Object.keys(REBAL_LABELS), ['none', 'M', 'Q', 'H', 'A']);
  });

  test('xirr: 1000 → 1100 через 365,25 дня = 10 %', () => {
    // 2020-01-01 + 366 дней (високосный) ≈ 1.002 года
    const r = xirr([{ date: '2020-01-01', amount: -1000 }, { date: '2021-01-01', amount: 1100 }]);
    close(r, 1.1 ** (365.25 / 366) - 1, 1e-9, 'xirr');
    assert.ok(Number.isNaN(xirr([])));
  });
});

describe('паритет с Python', () => {
  test('квартальная ребалансировка + коридор + комиссия + пополнения + НДФЛ (окно НИР)', () => {
    const ref = reference();
    const c = ref.backtest.complex;
    const px = sliceFrame(frameFromRef(ref.demo_frame), ref.params.start, ref.params.end);
    const r = runBacktest(px, c.cfg);
    close(r.equity.values.at(-1), c.final, 1e-9, 'итог');
    close(r.invested.values.at(-1), c.invested, 1e-9, 'invested');
    close(r.costs, c.costs, 1e-9, 'costs');
    close(r.taxes, c.taxes, 1e-9, 'taxes');
    close(r.turnover, c.turnover, 1e-9, 'turnover');
    assert.deepEqual(r.rebalanceDates, c.rebalance_dates);
    assert.equal(r.trades.length, c.n_trades);
  });

  test('итоги всех 6 стратегий (включая Баффетта) до 1e-9', () => {
    const ref = reference();
    const px = sliceFrame(frameFromRef(ref.demo_frame), ref.params.start, ref.params.end);
    return import('../../web/lib/strategies.js').then(({ STRATEGIES }) => {
      for (const [name, s] of Object.entries(STRATEGIES)) {
        const r = runBacktest(px, { weights: s.weights, initial: 1e6, rebalance: s.rebalance });
        close(r.equity.values.at(-1), ref.backtest.strategies[name].final, 1e-9, name);
      }
    });
  });
});
