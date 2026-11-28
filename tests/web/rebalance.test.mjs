// Ребалансировки: паритет web/lib/rebalance.js с core/portfolio/rebalance.py (эталон ledger_reference.json).
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as R from '../../web/lib/rebalance.js';
import { deepClose, ledgerReference } from './_helpers.mjs';

const ref = ledgerReference();
const TOL = 1e-9;

describe('schedule', () => {
  for (const c of ref.schedule) test(`schedule(${c.rule}, ${c.start}, ${c.n})`, () => assert.deepEqual(R.schedule(c.rule, c.start, c.n), c.expected));
  test('вручную: M/Q/H/A от 15.11.2024', () => {
    assert.deepEqual(R.schedule('M', '2024-11-15', 3), ['2024-11-29', '2024-12-31', '2025-01-31']);
    assert.deepEqual(R.schedule('Q', '2024-11-15', 3), ['2024-12-31', '2025-03-31', '2025-06-30']);
    assert.deepEqual(R.schedule('H', '2024-11-15', 3), ['2024-12-31', '2025-06-30', '2025-12-31']);
    assert.deepEqual(R.schedule('A', '2024-11-15', 3), ['2024-12-31', '2025-12-31', '2026-12-31']);
  });
  test('конец месяца на выходной -> предыдущая пятница; start в этот день и позже', () => {
    assert.deepEqual(R.schedule('M', '2024-06-01', 1), ['2024-06-28']);       // 30.06.2024 — воскресенье
    assert.deepEqual(R.schedule('M', '2024-03-29', 1), ['2024-03-29']);       // ровно последний рабочий день: включается
    assert.deepEqual(R.schedule('M', '2024-03-30', 1), ['2024-04-30']);
  });
  test('none и неизвестное правило — пусто; без start берётся сегодня', () => {
    assert.deepEqual(R.schedule('none', '2024-01-01'), []);
    assert.deepEqual(R.schedule('Z', '2024-01-01'), []);
    const today = new Date(); const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    const r = R.schedule('M');
    assert.equal(r.length, 8); assert.ok(r[0] >= iso);
  });
  test('даты возрастают, всегда рабочие дни', () => {
    for (const rule of ['M', 'Q', 'H', 'A']) {
      const d = R.schedule(rule, '2024-01-01', 30);
      assert.deepEqual(d, [...d].sort());
      assert.ok(d.every((x) => { const w = new Date(x + 'T00:00:00Z').getUTCDay(); return w >= 1 && w <= 5; }));
    }
  });
});

describe('driftReport', () => {
  ref.drift.forEach((c, i) => test(`driftReport #${i}`, () => {
    const got = R.driftReport(c.current, c.target, c.band);
    assert.deepEqual(got.map((r) => r.secid), c.expected.map((r) => r.secid));
    deepClose(got, c.expected, TOL);
    got.forEach((r, k) => assert.equal(r.outOfBand, c.expected[k].outOfBand));
  }));
  test('тикер без цели и без факта; ровно на границе — внутри коридора', () => {
    const r = Object.fromEntries(R.driftReport({ A: .5, X: .2 }, { A: .4, C: .25 }, .05).map((x) => [x.secid, x]));
    assert.equal(r.X.target, 0); assert.equal(r.X.relDrift, null); assert.equal(r.X.outOfBand, true);
    assert.equal(r.C.current, 0); assert.equal(r.C.relDrift, -1); assert.equal(r.C.driftPp, -25);
    const edge = R.driftReport({ A: .5625 }, { A: .5 }, .0625)[0];
    assert.equal(edge.outOfBand, false);
    assert.equal(R.driftReport({ A: .5626 }, { A: .5 }, .0625)[0].outOfBand, true);
  });
});

describe('rebalanceOrders', () => {
  ref.orders.forEach((c, i) => test(`rebalanceOrders #${i}`, () => {
    const got = R.rebalanceOrders(c.values, c.target, c.prices, c.lots, c.cash, c.commission);
    assert.deepEqual(got.map((r) => r.secid), c.expected.map((r) => r.secid), 'порядок строк');
    deepClose(got, c.expected, TOL);
    got.forEach((r, k) => { assert.equal(r.action, c.expected[k].action); assert.equal(r.lots, c.expected[k].lots); });
  }));
  test('вручную: лоты 10 и 1, свободные деньги, комиссия, порядок «Продать → Купить → —»', () => {
    const c = ref.orders[0];
    const got = R.rebalanceOrders(c.values, c.target, c.prices, c.lots, c.cash, c.commission);
    // итог 100 000: SBER цель 40 000 → продать 20 000 / 2740 = 7 лотов; GAZP +5000 / 1580 = 3 лота; LKOH +20 000 / 7000 = 2 лота
    const by = Object.fromEntries(got.map((r) => [r.secid, r]));
    assert.deepEqual([by.SBER.action, by.SBER.lots, by.SBER.qty], ['Продать', 7, 70]);
    assert.deepEqual([by.GAZP.action, by.GAZP.lots, by.GAZP.qty], ['Купить', 3, 30]);
    assert.deepEqual([by.LKOH.action, by.LKOH.lots, by.LKOH.qty], ['Купить', 2, 2]);
    assert.equal(by.SBER.commission, 70 * 274 * 0.003);
    const rank = { 'Продать': 0, 'Купить': 1, '—': 2 };
    assert.ok(got.every((r, k) => k === 0 || rank[got[k - 1].action] <= rank[r.action]));
  });
  test('тикер без цены: «—», цена/сумма/комиссия — null, без ошибок', () => {
    const c = ref.orders[0];
    const y = R.rebalanceOrders(c.values, c.target, c.prices, c.lots, c.cash, c.commission).find((r) => r.secid === 'YNDX');
    assert.deepEqual([y.action, y.lots, y.qty, y.price, y.amount, y.commission, y.shareAfter], ['—', 0, 0, null, null, null, null]);
    assert.equal(y.targetValue, 0.1 * 100000);
  });
  test('нулевой итог: доля после — null; пустые входы', () => {
    const r = R.rebalanceOrders({}, { A: 1 }, { A: 10 }, null, 0, 0)[0];
    assert.equal(r.shareAfter, null); assert.equal(r.action, '—');
    assert.deepEqual(R.rebalanceOrders({}, {}, {}), []);
  });
});

describe('toIcs', () => {
  const mask = (s) => s.replace(/UID:[0-9a-f-]+@/g, 'UID:<uid>@').replace(/DTSTAMP:\d{8}T\d{6}Z/g, 'DTSTAMP:<stamp>');
  ref.ics.forEach((c, i) => test(`toIcs #${i}: как Python (UID и DTSTAMP замаскированы)`, () => {
    assert.equal(mask(R.toIcs(c.dates, c.portfolio, c.description)), c.expected);
  }));
  test('CRLF между строками, нет голых LF; перенос в описании — «\\n»; UID уникальны', () => {
    const s = R.toIcs(['2024-12-31', '2025-01-31'], 'Мой', 'a\nb');
    assert.ok(!/(^|[^\r])\n/.test(s), 'голый LF');
    assert.equal(s.split('\r\n')[0], 'BEGIN:VCALENDAR'); assert.equal(s.split('\r\n').at(-1), 'END:VCALENDAR');
    assert.ok(s.includes('DESCRIPTION:a\\nb\r\n'));
    const uids = [...s.matchAll(/UID:(\S+)@invest-platform/g)].map((m) => m[1]);
    assert.equal(uids.length, 2); assert.notEqual(uids[0], uids[1]);
    assert.match(s, /DTSTAMP:\d{8}T\d{6}Z/);
    assert.ok(s.includes('DTSTART;VALUE=DATE:20241231\r\nDTEND;VALUE=DATE:20250101'));
  });
});

describe('toIcs: экранирование RFC 5545', () => {
  test('название и описание с переводом строки, запятой, ; и \\', () => {
    const s = R.toIcs(['2024-12-31'], 'A,B;C\\D\r\nE', 'x, y; z\\\r\nq');
    assert.ok(s.includes('SUMMARY:Ребалансировка — A\\,B\\;C\\\\D E\r\n'));
    assert.ok(s.includes('DESCRIPTION:x\\, y\\; z\\\\\\nq\r\n'));
    assert.ok(s.includes('DESCRIPTION:Завтра ребалансировка A\\,B\\;C\\\\D E\r\n'));
    assert.ok(!/(^|[^\r])\n/.test(s) && !s.includes('\r\r'));
  });
});

describe('нормировка и итоги', () => {
  test('normalizeTarget', () => {
    assert.deepEqual(R.normalizeTarget({ A: 30, B: 10, C: -5, D: 'x' }), { A: 0.75, B: 0.25, C: 0, D: 0 });
    assert.deepEqual(R.normalizeTarget({ A: 0 }), {}); assert.deepEqual(R.normalizeTarget(null), {});
  });
  test('equalTarget', () => {
    assert.deepEqual(R.equalTarget(['a', 'B', 'A', '']), { A: 0.5, B: 0.5 }); assert.deepEqual(R.equalTarget([]), {});
  });
  test('factShares: деньги в знаменателе, NaN = 0, нулевой итог', () => {
    assert.deepEqual(R.factShares([{ secid: 'A', value: 60 }, { secid: 'B', value: NaN }], 40), { A: 0.6, B: 0 });
    assert.deepEqual(R.factShares([{ secid: 'A', value: 0 }]), { A: 0 });
  });
  test('ordersTotals', () => {
    const o = R.rebalanceOrders({ A: 800, B: 200 }, { A: 0.5, B: 0.5 }, { A: 10, B: 10 }, null, 0, 0.01);
    assert.deepEqual(R.ordersTotals(o), { buy: 300, sell: 300, fees: 6 });
    assert.deepEqual(R.ordersTotals([{ action: 'Купить', amount: null, commission: null }]), { buy: 0, sell: 0, fees: 0 });
  });
});
