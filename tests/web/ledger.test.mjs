// Учёт портфеля: паритет web/lib/ledger.js с core/portfolio/ledger.py (эталон ledger_reference.json) и CSV.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as L from '../../web/lib/ledger.js';
import { deepClose, ledgerReference } from './_helpers.mjs';

const ref = ledgerReference();
const TOL = 1e-9;
const tx = (date, type, secid = null, qty = 0, price = 0, amount = null, fee = 0) =>
  ({ date, type, secid, qty, price, amount: amount ?? qty * price, fee, note: '' });

describe('паритет с Python: cashFlow / externalFlows / positions', () => {
  for (const c of ref.cashFlow) test(`cashFlow: ${c.name}`, () => deepClose(L.cashFlow(c.tx), c.expected, TOL, c.name));
  for (const c of ref.externalFlows) {
    test(`externalFlows: ${c.name}`, () => {
      const got = L.externalFlows(c.tx);
      deepClose(got.map((x) => [x.date, x.amount]), c.expected, TOL, c.name);
      assert.deepEqual(got.map((x) => x.date), [...got.map((x) => x.date)].sort(), 'по возрастанию даты');
    });
  }
  for (const c of ref.positions) {
    test(`positions: ${c.name}`, () => {
      const got = L.positions(c.tx);
      deepClose(got.positions, c.positions, TOL, c.name);
      assert.equal(got.positions.length, c.positions.length);
      deepClose(got.cash, c.cash, TOL, 'cash');
    });
  }
});

describe('ручные значения', () => {
  test('средняя цена и реализованный результат: две покупки, потом продажа', () => {
    const { positions, cash } = L.positions([tx('2024-01-10', 'BUY', 'SBER', 10, 100), tx('2024-01-11', 'BUY', 'SBER', 10, 120),
      tx('2024-01-12', 'SELL', 'SBER', 5, 150, null, 1)]);
    const p = positions[0];
    assert.equal(p.qty, 15); assert.equal(p.avgCost, 110); assert.equal(p.invested, 1650);
    assert.equal(p.realized, 5 * 150 - 1 - 5 * 110);            // 199
    assert.equal(cash, -1000 - 1200 + 750 - 1);
  });
  test('продажа больше остатка списывает только остаток', () => {
    const p = L.positions([tx('2024-01-10', 'BUY', 'SBER', 10, 100), tx('2024-01-11', 'SELL', 'SBER', 15, 110)]).positions[0];
    assert.equal(p.qty, 0); assert.equal(p.invested, 0);
  });
  test('покупка с комиссией: средняя включает комиссию', () => {
    const p = L.positions([tx('2024-01-10', 'BUY', 'GAZP', 10, 100, null, 5)]).positions[0];
    assert.equal(p.avgCost, 100.5); assert.equal(p.invested, 1005);
  });
  test('знак cashFlow по типам', () => {
    const types = { BUY: -1, SELL: 1, DIVIDEND: 1, COUPON: 1, DEPOSIT: 1, WITHDRAW: -1, FEE: -1, TAX: -1 };
    for (const [t, s] of Object.entries(types)) {
      assert.equal(L.cashFlow([tx('2024-01-01', t, 'A', 1, 100, 100)])[0], s * 100, t);
      assert.equal(L.cashFlow([tx('2024-01-01', t, 'A', 1, 100, -100)])[0], s * 100, `${t}: отрицательная сумма берётся по модулю`);
      assert.equal(L.cashFlow([tx('2024-01-01', t, 'A', 1, 100, 100, 3)])[0], s * 100 - 3, `${t}: комиссия всегда уменьшает остаток`);
    }
  });
  test('пустая сумма = количество × цена; неизвестный тип — ошибка', () => {
    assert.equal(L.cashFlow([{ date: '2024-01-01', type: 'BUY', secid: 'A', qty: 3, price: 10, amount: null, fee: 0 }])[0], -30);
    assert.throws(() => L.cashFlow([tx('2024-01-01', 'GIFT', 'A', 1, 1)]), /Неизвестный тип операции GIFT/);
  });
  test('порядок операций — по дате и id, а не по порядку в массиве', () => {
    const rows = [{ ...tx('2024-01-12', 'SELL', 'A', 5, 150), id: 3 }, { ...tx('2024-01-10', 'BUY', 'A', 10, 100), id: 1 },
      { ...tx('2024-01-11', 'BUY', 'A', 10, 120), id: 2 }];
    assert.deepEqual(L.positions(rows), L.positions([...rows].sort((a, b) => a.id - b.id)));
    assert.equal(L.positions(rows).positions[0].realized, 5 * 150 - 5 * 110);
  });
  test('TX_TYPES: 8 типов с русскими подписями', () => {
    assert.deepEqual(Object.keys(L.TX_TYPES), ['BUY', 'SELL', 'DIVIDEND', 'COUPON', 'DEPOSIT', 'WITHDRAW', 'FEE', 'TAX']);
    assert.ok(Object.values(L.TX_TYPES).every((x) => /^[А-Яа-я]+$/.test(x)));
  });
});

describe('паритет с Python: valuation', () => {
  for (const c of ref.valuation) {
    test(`valuation: ${c.name}`, () => {
      const got = L.valuation(c.tx, c.quotes);
      assert.equal(got.rows.length, c.rows.length, `число строк: ${got.rows.map((r) => r.secid)}`);
      deepClose(got.rows, c.rows, TOL, c.name);
      deepClose(got.cash, c.cash, TOL, 'cash');
    });
  }
  test('нет цены -> price/value/share = null, а не NaN', () => {
    const c = ref.valuation.find((x) => x.name.endsWith('/no_price'));
    const r = L.valuation(c.tx, c.quotes).rows[0];
    assert.equal(r.price, null); assert.equal(r.value, null); assert.equal(r.share, null);
    assert.ok(Number.isFinite(r.avgCost) && Number.isFinite(r.invested));
  });
  test('полностью проданная позиция с realized ≠ 0 остаётся; qty = 0 без дохода пропускается', () => {
    const full = ref.valuation.find((x) => x.name.startsWith('full_sell_and_income'));
    const rows = L.valuation(full.tx, full.quotes).rows;
    assert.deepEqual(rows.map((r) => r.secid), ['SBER', 'SU26238RMFS4']);
    assert.equal(rows[0].qty, 0); assert.notEqual(rows[0].realized, 0); assert.equal(rows[0].unrealized, 0);
    const zero = ref.valuation.find((x) => x.name === 'zero_qty_without_income_skipped');
    assert.deepEqual(L.valuation(zero.tx, zero.quotes).rows, []);
  });
  test('доля при нулевом итоге — null', () => {
    const c = ref.valuation.find((x) => x.name === 'share_with_zero_total');
    const got = L.valuation(c.tx, c.quotes);
    assert.equal(got.cash, -1000); assert.equal(got.rows[0].value, 1000); assert.equal(got.rows[0].share, null);
  });
});

describe('паритет с Python: valueHistory', () => {
  for (const c of ref.valueHistory) {
    test(`valueHistory: ${c.name}`, () => {
      const got = L.valueHistory(c.tx, c.prices);
      assert.deepEqual(got.dates, c.dates);
      for (const k of ['value', 'twr', 'flows', 'cash']) deepClose(got[k], c[k], TOL, `${c.name}.${k}`);
      assert.deepEqual(got.missingPrices, c.missingPrices);
    });
  }
  test('бумага без цены: value и twr — NaN (не заниженная сумма), в missingPrices', () => {
    const c = ref.valueHistory.find((x) => x.name === 'no_price_for_held_security');
    const got = L.valueHistory(c.tx, c.prices);
    assert.deepEqual(got.missingPrices, ['NOPR']);
    assert.equal(got.value[0], 100000);
    assert.ok(got.value.slice(1).every(Number.isNaN) && got.twr.slice(1).every(Number.isNaN));
  });
  test('покупка в выходной берёт ближайшую известную цену, дата выходного входит в ряд', () => {
    const c = ref.valueHistory.find((x) => x.name === 'weekend_buy');
    assert.ok(L.valueHistory(c.tx, c.prices).dates.includes('2024-01-06'));
  });
  test('пустой список операций -> пустой результат', () => {
    const got = L.valueHistory([], { dates: ['2024-01-02'], cols: { A: [1] } });
    assert.deepEqual(got, { dates: [], value: [], twr: [], flows: [], cash: [], missingPrices: [] });
  });
});

describe('CSV', () => {
  const sample = [
    { date: '2024-01-03', type: 'BUY', secid: 'SBER', qty: 10, price: 250.5, amount: 2505, fee: 1.5, note: 'a,b' },
    { date: '2024-01-04', type: 'DEPOSIT', secid: null, qty: 0, price: 0, amount: 10000.5, fee: 0, note: 'с "кавычкой"\nи переносом' },
  ];
  test('toCsv → parseCsv: туда и обратно', () => {
    const csv = L.toCsv(sample);
    assert.ok(csv.startsWith('date,type,secid,qty,price,amount,fee,note\n') && csv.endsWith('\n'));
    assert.deepEqual(L.parseCsv(csv), sample);
  });
  test('toCsv: формулы в текстовых полях получают префикс \', числа не трогаются', () => {
    const rows = [{ date: '2024-01-03', type: 'DEPOSIT', secid: null, qty: 0, price: 0, amount: -5, fee: 0, note: '=1+1' },
      { date: '2024-01-04', type: 'DEPOSIT', secid: '@x', qty: 0, price: 0, amount: 5, fee: 0, note: '-cmd' }, { date: '2024-01-05', type: 'DEPOSIT', secid: null, qty: 0, price: 0, amount: 1, fee: 0, note: '\tx' }];
    const lines = L.toCsv(rows).split('\n');
    assert.equal(lines[1], "2024-01-03,DEPOSIT,,0,0,-5,0,'=1+1");
    assert.equal(lines[2], "2024-01-04,DEPOSIT,'@x,0,0,5,0,'-cmd");
    assert.ok(lines[3].endsWith("'\tx"));
  });
  test(';, BOM и десятичная запятая; регистр типа и тикера; даты ДД.ММ.ГГГГ', () => {
    const csv = '﻿date;type;secid;qty;price;amount;fee;note\n03.01.2024;buy;sber;10;250,5;;1,5;"a;b"\n04/01/2024;DEPOSIT;;;;10 000,50;;\n';
    const got = L.parseCsv(csv);
    assert.deepEqual(got[0], { date: '2024-01-03', type: 'BUY', secid: 'SBER', qty: 10, price: 250.5, amount: 2505, fee: 1.5, note: 'a;b' });
    assert.equal(got[1].date, '2024-01-04'); assert.equal(got[1].amount, 10000.5); assert.equal(got[1].secid, null);
  });
  test('порядок столбцов любой, лишние игнорируются, CRLF', () => {
    const got = L.parseCsv('type,date,extra,amount\r\nDEPOSIT,2024-02-01,zzz,500\r\n');
    assert.deepEqual(got, [{ date: '2024-02-01', type: 'DEPOSIT', secid: null, qty: 0, price: 0, amount: 500, fee: 0, note: '' }]);
  });
  const fails = (csv, re) => assert.throws(() => L.parseCsv(csv), (e) => { assert.match(e.message, re); return true; });
  test('ошибки с номером строки', () => {
    fails('date,type,secid,qty,price\n2024-01-01,BUY,A,1,1\n2024-01-02,XX,A,1,1', /Строка 3: неизвестный тип операции «XX»/);
    fails('date,type,amount\n2024-13-01,DEPOSIT,5', /Строка 2: некорректная дата «2024-13-01»/);
    fails('date,type,amount\n2024-02-30,DEPOSIT,5', /Строка 2: некорректная дата/);
    fails('date,type,secid,qty,price\n2024-01-01,BUY,SBER,abc,10', /Строка 2: нечисловое значение «abc» в столбце qty/);
    fails('date,type,secid,qty,price\n2024-01-01,BUY,,10,10', /Строка 2: для операции BUY нужен тикер/);
    fails('date,type,amount\n,DEPOSIT,5', /Строка 2: не указана дата/);
    fails('date,type,amount\n2024-01-01,DEPOSIT,', /Строка 2: не указана сумма/);
  });
  test('строка с переносом внутри кавычек не сбивает нумерацию', () => {
    fails('date,type,note,amount\n2024-01-01,DEPOSIT,"a\nb",5\n2024-01-02,ZZ,,1', /Строка 4: неизвестный тип/);
  });
  test('несколько ошибок собираются в одно сообщение', () => {
    fails('date,type,amount\n2024-01-01,ZZ,1\nbad,DEPOSIT,1', /Строка 2:[\s\S]*Строка 3:/);
  });
  test('нет обязательных столбцов и пустой файл', () => {
    fails('x,y\n1,2', /нет столбца «date»/);
    fails('date,amount\n2024-01-01,1', /нет столбца «type»/);
    fails('', /Файл пуст/);
  });
  test('пустые строки пропускаются', () => assert.equal(L.parseCsv('date,type,amount\n\n2024-01-01,DEPOSIT,5\n\n').length, 1));
  test('validateTx', () => {
    assert.deepEqual(L.validateTx(tx('2024-01-01', 'BUY', 'A', 1, 10)), []);
    assert.match(L.validateTx(tx('01-01-2024x', 'BUY', 'A', 1, 10)).join(), /дата/);
    assert.match(L.validateTx(tx('2024-01-01', 'BUY', null, 1, 10)).join(), /нужен тикер/);
    assert.match(L.validateTx(tx('2024-01-01', 'SELL', 'A', 0, 10, 5)).join(), /количество больше нуля/);
    assert.match(L.validateTx({ ...tx('2024-01-01', 'BUY', 'A', 1, 10), fee: 'x' }).join(), /нечисловое значение «x» в поле fee/);
    assert.match(L.validateTx(tx('2024-01-01', 'BUY', 'A', -1, 10)).join(), /отрицательным/);
  });
  test('parseDate', () => {
    for (const [s, w] of [['2024-01-05', '2024-01-05'], ['2024/1/5', '2024-01-05'], ['05.01.2024', '2024-01-05'], ['5/1/2024', '2024-01-05'],
      ['2024-01-05 12:30:00', '2024-01-05'], ['2024-02-30', null], ['x', null], ['', null], ['2024-1-5-7', '2024-01-05']]) assert.equal(L.parseDate(s), w, s);
  });
});

describe('portfolioSummary', () => {
  const rows = [{ secid: 'A', value: 300 }, { secid: 'B', value: null }, { secid: 'C', value: 100 }];
  test('итоги, XIRR-поток и доли диаграммы', () => {
    const s = L.portfolioSummary(rows, 100, [{ date: '2024-01-01', amount: 400 }, { date: '2024-02-01', amount: -50 }], '2024-06-01');
    assert.equal(s.total, 500); assert.equal(s.netIn, 350); assert.equal(s.gain, 150); assert.ok(Math.abs(s.gainPct - 150 / 350) < 1e-12);
    assert.deepEqual(s.xirrFlows, [{ date: '2024-01-01', amount: -400 }, { date: '2024-02-01', amount: 50 }, { date: '2024-06-01', amount: 500 }]);
    assert.deepEqual(s.alloc.map((a) => a.label), ['C', 'Деньги', 'A']);
    assert.deepEqual(s.alloc.map((a) => a.share), [0.2, 0.2, 0.6]);
  });
  test('без внесений gainPct = null; без today нет конечного потока', () => {
    const s = L.portfolioSummary([], 0, []);
    assert.equal(s.total, 0); assert.equal(s.gainPct, null); assert.deepEqual(s.xirrFlows, []); assert.deepEqual(s.alloc, []);
  });
  test('twrPercent', () => assert.deepEqual(L.twrPercent([0, 0.5, NaN]).slice(0, 2), [0, 50]));
});
