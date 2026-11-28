// Хранилище «Моего портфеля» (web/js/portfolio-store.js): localStorage подменён, в т.ч. недоступный и бросающий.
import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { loadEngine, UserError } from '../../web/js/engine.js';

await loadEngine();   // store использует engine.ledger (parseCsv, validateTx)

let n = 0;
const KEY = 'finserv.portfolio.v1';

function fakeStorage(init = {}, { failGet = false, failSet = false } = {}) {
  const data = { ...init };
  return {
    data, sets: 0,
    getItem(k) { if (failGet) throw new Error('SecurityError'); return k in data ? data[k] : null; },
    setItem(k, v) { if (failSet) throw new Error('QuotaExceededError'); this.sets++; data[k] = String(v); },
    removeItem(k) { delete data[k]; },
  };
}
/** Свежий экземпляр модуля (у него своё состояние) с заданным localStorage. */
async function fresh(storage) {
  if (storage === undefined) delete globalThis.localStorage; else globalThis.localStorage = storage;
  return (await import(`../../web/js/portfolio-store.js?t=${++n}`));
}
afterEach(() => { delete globalThis.localStorage; });

const rejects = (fn, re) => assert.throws(fn, (e) => { assert.ok(e instanceof UserError, `ожидалась UserError, получено ${e}`); assert.match(e.message, re); return true; });
const buy = { date: '2024-01-10', type: 'BUY', secid: 'sber', qty: 10, price: 250 };

describe('старт и localStorage', () => {
  test('пустое хранилище: нет портфелей, persistent, ничего не записано до первого действия', async () => {
    const ls = fakeStorage();
    const { store } = await fresh(ls);
    assert.deepEqual(store.portfolios(), []); assert.equal(store.isEmpty(), true); assert.equal(store.persistent(), true);
    assert.equal(ls.sets, 0); assert.deepEqual(ls.data, {});
  });
  test('запись под ключом finserv.portfolio.v1 и чтение новым экземпляром', async () => {
    const ls = fakeStorage();
    const a = (await fresh(ls)).store;
    const id = a.createPortfolio('Основной');
    a.addTx(id, buy);
    assert.ok(KEY in ls.data);
    const b = (await fresh(ls)).store;
    assert.equal(b.portfolios()[0].name, 'Основной'); assert.equal(b.transactions(id)[0].secid, 'SBER');
  });
  test('localStorage недоступен (нет объекта): работает в памяти, persistent() = false', async () => {
    const { store } = await fresh(undefined);
    assert.equal(store.persistent(), false);
    const id = store.createPortfolio('P'); store.addTx(id, buy);
    assert.equal(store.transactions(id).length, 1);
  });
  test('getItem бросает: чистое состояние, persistent() = false, работа в памяти', async () => {
    const { store } = await fresh(fakeStorage({}, { failGet: true }));
    assert.equal(store.persistent(), false); assert.deepEqual(store.portfolios(), []);
    store.createPortfolio('P'); assert.equal(store.portfolios().length, 1);
  });
  test('setItem бросает (квота/приватный режим): операция проходит, persistent() = false, подписчики уведомлены', async () => {
    const ls = fakeStorage({}, { failSet: true });
    const { store } = await fresh(ls);
    let calls = 0; store.subscribe(() => calls++);
    store.createPortfolio('P');
    assert.equal(store.persistent(), false); assert.equal(store.portfolios().length, 1); assert.equal(calls, 1);
  });
  test('повреждённый JSON в хранилище: чистый старт, без исключений', async () => {
    const { store } = await fresh(fakeStorage({ [KEY]: '{не json' }));
    assert.deepEqual(store.portfolios(), []); assert.equal(store.persistent(), true);
  });
  test('в хранилище неверная версия/структура: чистый старт', async () => {
    for (const v of [{ version: 2, portfolios: [], transactions: [] }, [], { version: 1 }]) {
      const { store } = await fresh(fakeStorage({ [KEY]: JSON.stringify(v) }));
      assert.deepEqual(store.portfolios(), []);
    }
  });
});

describe('портфели', () => {
  test('создание: умолчания, обрезка пробелов, возрастающие id', async () => {
    const { store } = await fresh(fakeStorage());
    const a = store.createPortfolio('  Основной  '), b = store.createPortfolio('Второй', { benchmark: 'IMOEX' });
    assert.ok(b > a);
    const p = store.portfolio(a);
    assert.equal(p.name, 'Основной'); assert.equal(p.benchmark, 'MCFTR'); assert.deepEqual(p.target, {});
    assert.equal(p.rebalance, 'A'); assert.equal(p.band, 0.05); assert.equal(p.last_rebalance, null); assert.match(p.created, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(store.portfolio(b).benchmark, 'IMOEX'); assert.equal(store.portfolio(999), null);
  });
  test('название уникально без учёта регистра; пустое — ошибка', async () => {
    const { store } = await fresh(fakeStorage());
    store.createPortfolio('Мой Портфель');
    rejects(() => store.createPortfolio('мой портфель'), /уже есть/);
    rejects(() => store.createPortfolio('  МОЙ ПОРТФЕЛЬ '), /уже есть/);
    rejects(() => store.createPortfolio('   '), /Введите название/);
    rejects(() => store.createPortfolio(undefined), /Введите название/);
    assert.equal(store.portfolios().length, 1);
  });
  test('updatePortfolio: правило, цели; переименование — тот же регистр своего имени можно, чужое нельзя', async () => {
    const { store } = await fresh(fakeStorage());
    const a = store.createPortfolio('A'), b = store.createPortfolio('B');
    store.updatePortfolio(a, { rebalance: 'Q', band: 0.1, target: { SBER: 0.6 }, last_rebalance: '2024-05-01' });
    assert.deepEqual([store.portfolio(a).rebalance, store.portfolio(a).band, store.portfolio(a).target, store.portfolio(a).last_rebalance], ['Q', 0.1, { SBER: 0.6 }, '2024-05-01']);
    store.updatePortfolio(a, { name: 'A' }); store.updatePortfolio(a, { name: 'a2' });
    rejects(() => store.updatePortfolio(a, { name: 'b' }), /уже есть/);
    rejects(() => store.updatePortfolio(404, { band: 1 }), /не найден/);
    assert.equal(store.portfolio(b).name, 'B');
  });
  test('возвращаются копии: правка снаружи не меняет хранилище', async () => {
    const { store } = await fresh(fakeStorage());
    const a = store.createPortfolio('A'); store.portfolios()[0].name = 'hack'; store.portfolio(a).target.X = 1;
    assert.equal(store.portfolio(a).name, 'A'); assert.deepEqual(store.portfolio(a).target, {});
  });
  test('удаление портфеля удаляет его операции, чужие остаются', async () => {
    const { store } = await fresh(fakeStorage());
    const a = store.createPortfolio('A'), b = store.createPortfolio('B');
    store.addTx(a, buy); store.addTx(b, buy);
    store.deletePortfolio(a);
    assert.deepEqual(store.transactions(a), []); assert.equal(store.transactions(b).length, 1); assert.equal(store.portfolios().length, 1);
  });
});

describe('операции', () => {
  test('addTx: нормализация (тикер в верхнем регистре, сумма = qty × price), id, сортировка по дате и id', async () => {
    const { store } = await fresh(fakeStorage());
    const p = store.createPortfolio('P');
    const i2 = store.addTx(p, { ...buy, date: '2024-01-12' }), i1 = store.addTx(p, { ...buy, date: '2024-01-10', qty: 2, price: 5.5 });
    const i3 = store.addTx(p, { date: '2024-01-12', type: 'deposit', amount: 1000 });
    const t = store.transactions(p);
    assert.deepEqual(t.map((x) => x.id), [i1, i2, i3]);
    assert.equal(t[0].amount, 11); assert.equal(t[0].secid, 'SBER'); assert.equal(t[2].type, 'DEPOSIT'); assert.equal(t[2].secid, null);
    assert.ok(i2 < i1 && i1 < i3, 'id растут в порядке добавления');
  });
  test('валидация: тип, дата, тикер, количество, нечисла — UserError, ничего не добавляется', async () => {
    const { store } = await fresh(fakeStorage());
    const p = store.createPortfolio('P');
    rejects(() => store.addTx(p, { ...buy, type: 'GIFT' }), /неизвестный тип/);
    rejects(() => store.addTx(p, { ...buy, date: '' }), /не указана дата/);
    rejects(() => store.addTx(p, { ...buy, date: '2024-02-30' }), /некорректная дата/);
    rejects(() => store.addTx(p, { ...buy, secid: '' }), /нужен тикер/);
    rejects(() => store.addTx(p, { ...buy, qty: 0 }), /количество больше нуля/);
    rejects(() => store.addTx(p, { ...buy, qty: -1 }), /отрицательным|больше нуля/);
    rejects(() => store.addTx(p, { date: '2024-01-01', type: 'DEPOSIT' }), /не указана сумма/);
    rejects(() => store.addTx(999, buy), /Портфель не найден/);
    assert.deepEqual(store.transactions(p), []);
  });
  test('deleteTx; несуществующий id — без ошибки', async () => {
    const { store } = await fresh(fakeStorage());
    const p = store.createPortfolio('P'); const id = store.addTx(p, buy);
    store.deleteTx(12345); assert.equal(store.transactions(p).length, 1);
    store.deleteTx(id); assert.equal(store.transactions(p).length, 0);
  });
});

describe('импорт CSV', () => {
  const CSV = 'date,type,secid,qty,price,amount,fee,note\n2024-01-02,DEPOSIT,,,,100000,,\n2024-01-03,BUY,SBER,10,250,,1,первая\n';
  test('добавляет все строки, возвращает их число', async () => {
    const { store } = await fresh(fakeStorage());
    const p = store.createPortfolio('P');
    assert.equal(store.importCsv(p, CSV), 2);
    const t = store.transactions(p); assert.equal(t.length, 2); assert.equal(t[1].amount, 2500); assert.equal(t[1].note, 'первая');
  });
  test('ошибка в любой строке — не добавляется ничего (и подписчики не вызываются)', async () => {
    const ls = fakeStorage();
    const { store } = await fresh(ls);
    const p = store.createPortfolio('P'); store.addTx(p, buy);
    let calls = 0, sets = ls.sets; store.subscribe(() => calls++);
    for (const [bad, re] of [['2024-01-05,XX,A,1,1,,,', /Строка 4: неизвестный тип/], ['2024-01-05,BUY,,1,1,,,', /Строка 4: для операции BUY нужен тикер/],
      ['2024-01-05,BUY,A,abc,1,,,', /Строка 4: нечисловое значение/], ['bad,BUY,A,1,1,,,', /Строка 4: некорректная дата/]]) {
      assert.throws(() => store.importCsv(p, CSV + bad + '\n'), re);   // ошибку разбора бросает lib/ledger.js (Error с текстом по-русски)
    }
    assert.throws(() => store.importCsv(p, ''), /Файл пуст/);
    assert.equal(store.transactions(p).length, 1); assert.equal(calls, 0); assert.equal(ls.sets, sets);
  });
  test('в неизвестный портфель нельзя', async () => {
    const { store } = await fresh(fakeStorage());
    rejects(() => store.importCsv(5, CSV), /Портфель не найден/);
  });
});

describe('JSON: нормализация, режимы, подписки', () => {
  const doc = (over = {}) => ({ version: 1, nextId: 10, portfolios: [{ id: 1, name: 'Импорт', created: '2024-01-01', benchmark: 'MCFTR', target: { sber: 0.5, gazp: -1, x: 'q' }, rebalance: 'Q', band: 0.1, last_rebalance: null }],
    transactions: [{ id: 2, portfolio_id: 1, date: '2024-01-03', type: 'BUY', secid: 'sber', qty: 1, price: 100, amount: 100, fee: 0, note: '' }], ...over });
  test('normalize: структура, верхний регистр тикеров, отбрасывание плохих целей, nextId > max id', async () => {
    const { normalize } = await fresh(fakeStorage());
    const s = normalize(doc({ nextId: 1 }));
    assert.deepEqual(s.portfolios[0].target, { SBER: 0.5 }); assert.equal(s.transactions[0].secid, 'SBER');
    assert.equal(s.nextId, 3);
  });
  test('normalize: понятные ошибки', async () => {
    const { normalize } = await fresh(fakeStorage());
    const bad = (raw, re) => rejects(() => normalize(raw), re);
    bad(null, /ожидался объект/); bad([], /ожидался объект/); bad('x', /ожидался объект/);
    bad({ version: 2, portfolios: [], transactions: [] }, /версия/); bad({ portfolios: [], transactions: [] }, /не указана/);
    bad({ version: 1, portfolios: {} , transactions: [] }, /нет списков/);
    bad(doc({ portfolios: [{ id: 'a', name: 'x' }] }), /Портфель №1: неверный/);
    bad(doc({ portfolios: [{ id: 1, name: 'x' }, { id: 1, name: 'y' }], transactions: [] }), /повторяющийся id/);
    bad(doc({ portfolios: [{ id: 1, name: '  ' }], transactions: [] }), /не задано название/);
    bad(doc({ portfolios: [{ id: 1, name: 'Мой' }, { id: 2, name: 'мой' }], transactions: [] }), /встречается дважды/);
    bad(doc({ transactions: [{ id: 2, portfolio_id: 7, date: '2024-01-03', type: 'BUY' }] }), /портфеля с id 7 нет/);
    bad(doc({ transactions: [{ id: 2, portfolio_id: 1, date: '03.01.2024', type: 'BUY' }] }), /нужен формат ГГГГ-ММ-ДД/);
    bad(doc({ transactions: [{ id: 2, portfolio_id: 1, date: '2024-01-03', type: 'GIFT' }] }), /неизвестный тип «GIFT»/);
    const t = { id: 2, portfolio_id: 1, date: '2024-01-03', type: 'FEE' };
    bad(doc({ transactions: [t, t] }), /Операция №2: неверный или повторяющийся id/);
  });
  test('importJson: не JSON — понятная ошибка, состояние не меняется', async () => {
    const { store } = await fresh(fakeStorage());
    store.createPortfolio('Старый');
    rejects(() => store.importJson('это не json'), /не JSON/);
    rejects(() => store.importJson(JSON.stringify({ version: 9 })), /версия/);
    assert.deepEqual(store.portfolios().map((p) => p.name), ['Старый']);
  });
  test('replace: всё заменяется, возвращается число операций', async () => {
    const { store } = await fresh(fakeStorage());
    store.createPortfolio('Старый');
    assert.equal(store.importJson(JSON.stringify(doc()), 'replace'), 1);
    assert.deepEqual(store.portfolios().map((p) => p.name), ['Импорт']);
    assert.equal(store.transactions(1)[0].id, 2);
    assert.ok(store.createPortfolio('Новый') >= 10, 'nextId не вернулся назад');
  });
  test('merge: одноимённый портфель (без учёта регистра) получает операции, новый — новый id; id не сталкиваются', async () => {
    const { store } = await fresh(fakeStorage());
    const mine = store.createPortfolio('ИМПОРТ'); store.addTx(mine, buy);
    const other = store.createPortfolio('Другой'); store.addTx(other, buy);
    const incoming = doc({ portfolios: [doc().portfolios[0], { id: 5, name: 'Новый из файла', target: {} }],
      transactions: [...doc().transactions, { id: 6, portfolio_id: 5, date: '2024-02-01', type: 'DEPOSIT', amount: 5 }] });
    assert.equal(store.importJson(JSON.stringify(incoming), 'merge'), 2);
    const ps = store.portfolios(); assert.equal(ps.length, 3);
    assert.equal(store.transactions(mine).length, 2, 'операция влилась в существующий портфель');
    const created = ps.find((p) => p.name === 'Новый из файла');
    assert.equal(store.transactions(created.id).length, 1);
    const all = ps.flatMap((p) => store.transactions(p.id)).map((t) => t.id);
    assert.equal(new Set(all).size, all.length, 'id операций уникальны');
    assert.equal(store.transactions(other).length, 1);
  });
  test('exportJson → importJson: круг', async () => {
    const a = (await fresh(fakeStorage())).store;
    const p = a.createPortfolio('P'); a.addTx(p, buy); a.updatePortfolio(p, { target: { SBER: 1 } });
    const text = a.exportJson();
    assert.equal(JSON.parse(text).version, 1);
    const b = (await fresh(fakeStorage())).store;
    b.importJson(text);
    assert.deepEqual(b.portfolios(), a.portfolios()); assert.deepEqual(b.transactions(p), a.transactions(p));
  });
  test('subscribe: уведомляет на каждое изменение, отписка работает, при ошибке — нет', async () => {
    const { store } = await fresh(fakeStorage());
    let n1 = 0; const off = store.subscribe(() => n1++);
    const p = store.createPortfolio('P'); store.addTx(p, buy); store.updatePortfolio(p, { band: 0.2 });
    const t = store.transactions(p)[0].id; store.deleteTx(t); store.deletePortfolio(p);
    assert.equal(n1, 5);
    try { store.createPortfolio(''); } catch { /* ожидаемо */ }
    assert.equal(n1, 5);
    off(); store.createPortfolio('Q'); assert.equal(n1, 5);
  });
});
