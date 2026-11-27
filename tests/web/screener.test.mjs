// Скринеры (web/lib/screener.js): чистые filterRows / sortRows и склейка файлов loadScreener на фейковом fetch.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { filterRows, sortRows, loadScreener, COLUMNS } from '../../web/lib/screener.js';
import { response } from './_helpers.mjs';

const ROWS = [
  { secid: 'SBER', name: 'Сбербанк', sector: 'Финансы', cagr: 0.2, pe: 4, d: '2030-01-01' },
  { secid: 'GAZP', name: 'Газпром', sector: 'Энергетика', cagr: -0.05, pe: null, d: '2028-06-30' },
  { secid: 'LKOH', name: 'Лукойл', sector: 'энергетика', cagr: 0.15, pe: 6, d: null },
  { secid: 'YDEX', name: 'Яндекс', sector: 'ИТ', cagr: null, pe: 30, d: '2035-12-31' },
];
const ids = (rows) => rows.map((r) => r.secid);

describe('filterRows', () => {
  test('q — подстрока тикера или названия без регистра', () => {
    assert.deepEqual(ids(filterRows(ROWS, { q: 'сбер' })), ['SBER']);
    assert.deepEqual(ids(filterRows(ROWS, { q: 'kO' })), ['LKOH']);
    assert.deepEqual(ids(filterRows(ROWS, { q: '  ' })), ids(ROWS));
  });
  test('min/max включительно, null не проходит', () => {
    assert.deepEqual(ids(filterRows(ROWS, { pe: { min: 4, max: 6 } })), ['SBER', 'LKOH']);
    assert.deepEqual(ids(filterRows(ROWS, { cagr: { min: 0.15 } })), ['SBER', 'LKOH']);
    assert.deepEqual(ids(filterRows(ROWS, { cagr: { max: 0 } })), ['GAZP']);
    assert.deepEqual(ids(filterRows(ROWS, { pe: { min: null, max: null } })), ids(ROWS));
  });
  test('диапазон ISO-дат', () => {
    assert.deepEqual(ids(filterRows(ROWS, { d: { max: '2030-01-01' } })), ['SBER', 'GAZP']);
  });
  test('список и пустой список', () => {
    assert.deepEqual(ids(filterRows(ROWS, { sector: ['ИТ', 'Финансы'] })), ['SBER', 'YDEX']);
    assert.deepEqual(ids(filterRows(ROWS, { sector: [] })), ids(ROWS));
  });
  test('точное совпадение без регистра; пустые условия игнорируются', () => {
    assert.deepEqual(ids(filterRows(ROWS, { sector: 'ЭНЕРГЕТИКА' })), ['GAZP', 'LKOH']);
    assert.deepEqual(ids(filterRows(ROWS, { sector: '', pe: null, x: undefined })), ids(ROWS));
  });
  test('несколько условий — И; исходный массив не меняется', () => {
    const copy = structuredClone(ROWS);
    assert.deepEqual(ids(filterRows(ROWS, { q: 'а', cagr: { min: 0 } })), ['SBER']);
    assert.deepEqual(ROWS, copy);
  });
});

describe('sortRows', () => {
  test('desc по умолчанию, null в конце при обоих направлениях', () => {
    assert.deepEqual(ids(sortRows(ROWS, 'cagr')), ['SBER', 'LKOH', 'GAZP', 'YDEX']);
    assert.deepEqual(ids(sortRows(ROWS, 'cagr', 'asc')), ['GAZP', 'LKOH', 'SBER', 'YDEX']);
    assert.deepEqual(ids(sortRows(ROWS, 'pe', 'asc')).at(-1), 'GAZP');
  });
  test('русский алфавит', () => {
    assert.deepEqual(sortRows(ROWS, 'name', 'asc').map((r) => r.name), ['Газпром', 'Лукойл', 'Сбербанк', 'Яндекс']);
    const r = sortRows([{ n: 'Ёлка' }, { n: 'Ель' }, { n: 'Жук' }, { n: 'Ангара' }], 'n', 'asc').map((x) => x.n);
    assert.equal(r[0], 'Ангара');
    assert.equal(r.at(-1), 'Жук');
  });
  test('устойчивость и новый массив', () => {
    const rows = [{ k: 1, i: 0 }, { k: 2, i: 1 }, { k: 1, i: 2 }, { k: null, i: 3 }, { k: 2, i: 4 }, { k: null, i: 5 }];
    assert.deepEqual(sortRows(rows, 'k', 'desc').map((r) => r.i), [1, 4, 0, 2, 3, 5]);
    assert.deepEqual(sortRows(rows, 'k', 'asc').map((r) => r.i), [0, 2, 1, 4, 3, 5]);
    const out = sortRows(rows, 'k');
    assert.notEqual(out, rows);
    assert.deepEqual(rows.map((r) => r.i), [0, 1, 2, 3, 4, 5]);
  });
});

/** fetch по имени файла: body | undefined (404). */
function fakeFetch(files) {
  const calls = [];
  const f = async (url) => {
    calls.push(url);
    const body = files[url.split('/').pop()];
    return body === undefined ? response('not found', { status: 404 }) : response(body);
  };
  f.calls = calls;
  return f;
}

const STATS = {
  updated: '2026-11-26T03:00:00Z',
  items: {
    SBER: { class: 'share', cagr: 0.21, volatility: 0.3, max_drawdown: -0.5, sharpe: 0.8, sortino: 1.1, score: 77 },
    LQDT: { class: 'fund', cagr: 0.12, volatility: 0.01, max_drawdown: -0.001, sharpe: 2, sortino: 3, score: 90 },
    IMOEX: { class: 'index', cagr: 0.1 },
  },
};
const SHARES = { data: [
  { ticker: 'SBER', name: 'Сбербанк', sector: 'financial', logo: 'sber.png', logo_color: '#309C0B' },
  { ticker: 'NEWC', name: 'Новая', sector: 'unknown_sector' },
] };
const FUND = { data: { SBER: { pe: '4.5', pb: 1, ev_ebitda: null, roe: 22.5, dividend_yield_ttm: 11 } } };

describe('loadScreener', () => {
  test('stocks: склейка stats + T-Invest + фундаментал, проценты — доли', async () => {
    const f = fakeFetch({ 'symbol_stats.json': STATS, 'tinvest_shares.json': SHARES, 'fundamentals.json': FUND });
    const d = await loadScreener('stocks', { base: '/data', fetchImpl: f });
    assert.equal(d.updated, STATS.updated);
    assert.match(d.source, /FinServ/);
    assert.deepEqual(d.columns, COLUMNS.stocks);
    assert.deepEqual(ids(d.rows), ['NEWC', 'SBER']);
    const s = d.rows.find((r) => r.secid === 'SBER');
    assert.equal(s.name, 'Сбербанк');
    assert.equal(s.sector, 'Финансы');
    assert.equal(s.logo, 'sber.png');
    assert.equal(s.logo_color, '#309C0B');
    assert.equal(s.cagr, 0.21);
    assert.equal(s.score, 77);
    assert.equal(s.pe, 4.5);
    assert.equal(s.ev_ebitda, null);
    assert.ok(Math.abs(s.roe - 0.225) < 1e-12);
    assert.ok(Math.abs(s.dividend_yield - 0.11) < 1e-12);
    const n = d.rows.find((r) => r.secid === 'NEWC');
    assert.equal(n.sector, 'unknown_sector', 'неизвестный сектор — как есть');
    assert.equal(n.cagr, null);
    assert.equal(n.logo, null);
    assert.ok(f.calls.every((u) => u.startsWith('/data/')), f.calls);
  });

  test('stocks: нет fundamentals и tinvest — колонки null, строки из stats', async () => {
    const d = await loadScreener('stocks', { base: '/data/', fetchImpl: fakeFetch({ 'symbol_stats.json': STATS }) });
    assert.deepEqual(ids(d.rows), ['SBER']);
    const s = d.rows[0];
    assert.equal(s.name, 'SBER');
    for (const k of ['sector', 'pe', 'pb', 'roe', 'dividend_yield', 'logo']) assert.equal(s[k], null, k);
  });

  test('funds: RusETFs обогащает, commission — доля; иначе fixed_commission T-Invest', async () => {
    const f = fakeFetch({
      'symbol_stats.json': STATS,
      'tinvest_etfs.json': { data: [{ ticker: 'LQDT', name: 'Ликвидность', fixed_commission: 0.5 }, { ticker: 'TMOS', name: 'Мосбиржа', fixed_commission: 0.79 }] },
      'rusetfs_funds.json': { data: [{ ticker: 'LQDT', issuer: 'ВИМ', asset_class: 'Денежный рынок', commission_pct: 0.29, aum_rub: 5e11 }, { ticker: 'NOTRADED', name: 'X' }] },
    });
    const d = await loadScreener('funds', { base: '/d', fetchImpl: f });
    assert.deepEqual(ids(d.rows), ['LQDT', 'TMOS']);
    const [l, t] = d.rows;
    assert.ok(Math.abs(l.commission - 0.0029) < 1e-12);
    assert.equal(l.issuer, 'ВИМ');
    assert.equal(l.aum_rub, 5e11);
    assert.equal(l.score, 90);
    assert.ok(Math.abs(t.commission - 0.0079) < 1e-12);
    assert.equal(t.issuer, null);
    assert.equal(t.cagr, null);
  });

  test('bonds: поля, тип по режиму, null для пустых', async () => {
    const f = fakeFetch({ 'screener_bonds.json': { updated: '2026-11-27', source: 'ISS MOEX', data: [
      { secid: 'SU26238RMFS4', name: 'ОФЗ 26238', board: 'TQOB', yield: 0.142, coupon_pct: 0.071, coupon_freq: 2,
        duration_days: 3000, maturity_date: '2041-05-15', offer_date: null, price_pct: 60.1, nkd: 10.5,
        volume_rub: 1e9, listing_level: 1, face_currency: 'RUB' },
      { secid: 'RU000X', board: 'TQCB', yield: '', face_currency: 'USD' },
      { name: 'без secid' },
    ] } });
    const d = await loadScreener('bonds', { fetchImpl: f, base: '/d' });
    assert.equal(d.updated, '2026-11-27');
    assert.deepEqual(ids(d.rows), ['SU26238RMFS4', 'RU000X']);
    assert.equal(d.rows[0].type, 'ОФЗ');
    assert.equal(d.rows[0].yield, 0.142);
    assert.equal(d.rows[0].face_currency, 'RUB');
    assert.equal(d.rows[1].type, 'Корпоративная');
    assert.equal(d.rows[1].name, 'RU000X');
    assert.equal(d.rows[1].yield, null);
    assert.equal(d.rows[1].face_currency, 'USD');
  });

  test('bonds: файла нет — пустые строки, без исключения', async () => {
    const d = await loadScreener('bonds', { fetchImpl: fakeFetch({}), base: '/d' });
    assert.deepEqual(d.rows, []);
    assert.equal(d.updated, null);
  });

  test('неизвестный вид — ошибка', async () => {
    await assert.rejects(loadScreener('crypto', { fetchImpl: fakeFetch({}) }), /неизвестный скринер/);
  });
});
