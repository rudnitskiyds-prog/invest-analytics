// Котировки «Моего портфеля» (web/lib/quotes.js): мок fetchImpl в формате ISS и XML ЦБ, без сети.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as Q from '../../web/lib/quotes.js';
import { routeFetch, response } from './_helpers.mjs';

const blk = (columns, data) => ({ columns, data });
const cbrXml = (recs) => '<?xml version="1.0" encoding="windows-1251"?><ValCurs ID="X">' +
  recs.map(([d, nom, val]) => `<Record Date="${d}" Id="X"><Nominal>${nom}</Nominal><Value>${val}</Value></Record>`).join('') + '</ValCurs>';

const BOARDS = {   // secid -> [boardid, market, engine, description]
  SBER: ['TQBR', 'shares', 'stock', {}],
  OFZ1: ['TQOB', 'bonds', 'stock', { FACEVALUE: '1000', FACEUNIT: 'SUR' }],
  EU1: ['TQOD', 'bonds', 'stock', { FACEVALUE: '1000', FACEUNIT: 'USD' }],
  CNY1: ['TQCB', 'bonds', 'stock', { FACEVALUE: '1000', FACEUNIT: 'CNY' }],
  GLDRUB_TOM: ['CETS', 'selt', 'currency', {}],
};
const SEC = {
  TQBR: blk(['SECID', 'SHORTNAME', 'LOTSIZE', 'PREVPRICE'], [['SBER', 'Сбербанк', 10, 270]]),
  TQOB: blk(['SECID', 'SHORTNAME', 'FACEVALUE', 'ACCRUEDINT', 'PREVPRICE', 'FACEUNIT', 'CURRENCYID'], [['OFZ1', 'ОФЗ 1', 1000, 12.5, 98, 'SUR', 'SUR']]),
  TQOD: blk(['SECID', 'SHORTNAME', 'FACEVALUE', 'ACCRUEDINT', 'PREVPRICE', 'FACEUNIT', 'CURRENCYID'], [['EU1', 'Евро-1', 1000, 10, 99, 'USD', 'SUR']]),
  TQCB: blk(['SECID', 'SHORTNAME', 'FACEVALUE', 'ACCRUEDINT', 'PREVPRICE', 'FACEUNIT', 'CURRENCYID'], [['CNY1', 'Юань-1', 1000, 5, 100, 'CNY', 'CNY']]),
  CETS: blk(['SECID', 'SHORTNAME', 'PREVPRICE', 'LOTSIZE'], [['GLDRUB_TOM', 'x', 7000, 1], ['USD000UTSTOM', 'доллар', 90, 1000]]),
};
const MD = { TQBR: { SBER: null }, TQOB: { OFZ1: 97.5 }, TQOD: { EU1: null }, TQCB: { CNY1: null }, CETS: { GLDRUB_TOM: 7100 } };

/** ISS + ЦБ. opts.rates: {R01235: [[дата, nominal, value]...]}, opts.cbrDown — ЦБ недоступен. */
function mk({ rates = {}, cbrDown = false, md = MD } = {}) {
  return routeFetch((url) => {
    const u = new URL(url);
    if (u.hostname === 'www.cbr.ru') {
      if (cbrDown) return new Error('down');
      return cbrXml(rates[u.searchParams.get('VAL_NM_RQ')] || []);
    }
    const p = u.pathname;
    let m = /\/securities\/([^/]+)\.json$/.exec(p);
    if (m) {
      const b = BOARDS[decodeURIComponent(m[1])];
      if (!b) return { boards: blk(['boardid', 'engine', 'market', 'is_primary'], []), description: blk(['name', 'title', 'value'], []) };
      return { boards: blk(['boardid', 'engine', 'market', 'is_primary'], [[b[0], b[2], b[1], 1]]),
        description: blk(['name', 'title', 'value'], Object.entries(b[3]).map(([k, v]) => [k, '', v])) };
    }
    m = /\/boards\/([A-Z]+)\/securities\.json$/.exec(p);
    if (m) {
      return { securities: SEC[m[1]], marketdata: blk(['SECID', 'LAST'], Object.entries(md[m[1]] || {})) };
    }
    return undefined;
  });
}
const RATE_USD = { R01235: [['22.11.2024', '1', '98,5000'], ['26.11.2024', '1', '100,0000'], ['28.11.2024', '1', '120,0000']] };
const OPTS = (f, extra = {}) => ({ fetchImpl: f, date: '2024-11-27', ...extra });

describe('getQuotes', () => {
  test('акция: LAST пуст -> PREVPRICE; LAST есть -> LAST; лот и название; цена за 1 бумагу', async () => {
    const q = await Q.getQuotes(['SBER'], OPTS(mk()));
    assert.deepEqual(q.SBER, { price: 270, name: 'Сбербанк', lot: 10, board: 'TQBR', market: 'shares', currency: 'RUB' });
    const q2 = await Q.getQuotes(['SBER'], OPTS(mk({ md: { ...MD, TQBR: { SBER: 275.5 } } })));
    assert.equal(q2.SBER.price, 275.5);
  });
  test('рублёвая облигация: цена% × номинал / 100 + НКД, без обращения к ЦБ', async () => {
    const f = mk();
    const q = await Q.getQuotes(['OFZ1'], OPTS(f));
    assert.equal(q.OFZ1.price, 97.5 * 1000 / 100 + 12.5);        // 987,5
    assert.deepEqual([q.OFZ1.market, q.OFZ1.currency, q.OFZ1.lot], ['bonds', 'RUB', 1]);
    assert.ok(!f.calls.some((u) => u.includes('cbr.ru')), 'рублёвой бумаге курс не нужен');
  });
  test('валютная облигация: цена × номинал × курс ЦБ на дату + НКД; курсы из будущего игнорируются', async () => {
    const f = mk({ rates: RATE_USD });
    const q = await Q.getQuotes(['EU1'], OPTS(f));
    assert.equal(q.EU1.price, 99 * 1000 / 100 * 100 + 10 * 1);   // курс 100 на 26.11, а не 120 на 28.11
    assert.equal(q.EU1.currency, 'USD');
    const cbr = f.calls.filter((u) => u.includes('cbr.ru'));
    assert.equal(cbr.length, 1);
    assert.match(cbr[0], /^https:\/\/www\.cbr\.ru\/scripts\/XML_dynamic\.asp\?/);
    assert.match(cbr[0], /date_req1=13%2F11%2F2024&date_req2=27%2F11%2F2024&VAL_NM_RQ=R01235/);   // окно 14 дней, ДД/ММ/ГГГГ
  });
  test('номинал ЦБ делится: 10 юаней = 130 ₽ -> курс 13', async () => {
    const q = await Q.getQuotes(['CNY1'], OPTS(mk({ rates: { R01375: [['26.11.2024', '10', '130,0000']] } })));
    assert.equal(q.CNY1.price, 100 * 1000 / 100 * 13 + 5 * 13);
  });
  test('валютная облигация без курса ЦБ (ЦБ недоступен / нет записей): price = null, остальные бумаги считаются', async () => {
    const q = await Q.getQuotes(['EU1', 'SBER'], OPTS(mk({ cbrDown: true })));
    assert.equal(q.EU1.price, null); assert.equal(q.SBER.price, 270); assert.equal(q.EU1.name, 'Евро-1');
    const q2 = await Q.getQuotes(['EU1'], OPTS(mk({ rates: { R01235: [] } })));
    assert.equal(q2.EU1.price, null);
  });
  test('металл: рублей за грамм, название металла, лот 1; другие инструменты режима не попадают', async () => {
    const q = await Q.getQuotes(['gldrub_tom'], OPTS(mk()));
    assert.deepEqual(q.GLDRUB_TOM, { price: 7100, name: 'Золото (руб./г)', lot: 1, board: 'CETS', market: 'selt', currency: 'RUB' });
    assert.deepEqual(Object.keys(q), ['GLDRUB_TOM']);
  });
  test('неизвестный тикер и пустой ввод пропускаются; регистр и дубли нормализуются; запрос только по нужным бумагам', async () => {
    const f = mk();
    const q = await Q.getQuotes(['sber', 'SBER', 'NOPE', '', null], OPTS(f));
    assert.deepEqual(Object.keys(q), ['SBER']);
    assert.deepEqual(await Q.getQuotes([], OPTS(mk())), {});
    assert.deepEqual(await Q.getQuotes(undefined, OPTS(mk())), {});
    assert.ok(f.calls.some((u) => u.includes('/boards/TQBR/securities.json') && u.includes('securities=SBER')));
    assert.ok(f.calls.every((u) => u.startsWith('https://iss.moex.com/iss/') || u.startsWith('https://www.cbr.ru/')), 'нет других хостов');
  });
  test('режим не отвечает (500) — бумага пропущена, остальные есть', async () => {
    const inner = mk();
    const g = async (url) => (url.includes('/boards/TQOB/securities.json') ? response('err', { status: 500 }) : inner(url));
    const q = await Q.getQuotes(['SBER', 'OFZ1'], { fetchImpl: g, date: '2024-11-27' });
    assert.deepEqual(Object.keys(q), ['SBER']);
  });
  test('normalizeCurrency', () => {
    for (const [a, b] of [['SUR', 'RUB'], ['RUR', 'RUB'], ['', 'RUB'], [null, 'RUB'], ['usd', 'USD'], [' eur ', 'EUR']]) assert.equal(Q.normalizeCurrency(a), b);
    assert.deepEqual(Q.CURRENCY_CODES, { USD: 'R01235', EUR: 'R01239', CNY: 'R01375', CHF: 'R01775', GBP: 'R01035' });
  });
});

describe('priceHistoryRub', () => {
  const candles = (rows) => blk(['begin', 'close'], rows);
  const hist = ({ cbrDown = false, rates = RATE_USD } = {}) => routeFetch((url) => {
    const u = new URL(url);
    if (u.hostname === 'www.cbr.ru') return cbrDown ? new Error('down') : cbrXml(rates[u.searchParams.get('VAL_NM_RQ')] || []);
    let m = /\/securities\/([^/]+)\/candles\.json$/.exec(u.pathname);
    if (m) {
      const id = decodeURIComponent(m[1]);
      if (id === 'SBER') return { candles: candles([['2024-11-25 00:00:00', 270], ['2024-11-26 00:00:00', 271], ['2024-11-27 00:00:00', 272]]) };
      if (id === 'EU1') return { candles: candles([['2024-11-25 00:00:00', 99], ['2024-11-26 00:00:00', 99.5], ['2024-11-27 00:00:00', 100]]) };
      if (id === 'OFZ1') return { candles: candles([['2024-11-25 00:00:00', 98], ['2024-11-27 00:00:00', 97]]) };
    }
    m = /\/securities\/([^/]+)\.json$/.exec(u.pathname);
    if (m) {
      const b = BOARDS[decodeURIComponent(m[1])];
      if (!b) return undefined;
      return { boards: blk(['boardid', 'engine', 'market', 'is_primary'], [[b[0], b[2], b[1], 1]]),
        description: blk(['name', 'title', 'value'], Object.entries(b[3]).map(([k, v]) => [k, '', v])) };
    }
    return undefined;
  });
  const rates = { R01235: [['25.11.2024', '1', '90,0'], ['27.11.2024', '1', '92,0']] };

  test('акции — как есть; рублёвая облигация — % × номинал / 100; общий календарь с ffill', async () => {
    const fr = await Q.priceHistoryRub(['SBER', 'OFZ1'], '2024-11-25', '2024-11-27', { fetchImpl: hist() });
    assert.deepEqual(fr.dates, ['2024-11-25', '2024-11-26', '2024-11-27']);
    assert.deepEqual(fr.cols.SBER, [270, 271, 272]);
    assert.deepEqual(fr.cols.OFZ1, [980, 980, 970]);          // 26.11 — цена переносится (ffill)
  });
  test('валютный номинал: цена × номинал × курс на дату (курс — последний установленный, ffill)', async () => {
    const f = hist({ rates });
    const fr = await Q.priceHistoryRub(['EU1', 'SBER'], '2024-11-25', '2024-11-27', { fetchImpl: f });
    assert.deepEqual(fr.cols.EU1, [99 * 10 * 90, 99.5 * 10 * 90, 100 * 10 * 92]);   // 26.11 — курс 90 (с 25.11)
    assert.deepEqual(fr.cols.SBER, [270, 271, 272]);
    const cbr = f.calls.filter((u) => u.includes('cbr.ru'));
    assert.equal(cbr.length, 1); assert.match(cbr[0], /date_req1=01%2F01%2F2024/);   // с 1 января года (first − 14 дн.)
  });
  test('нет курса валюты (ЦБ недоступен): столбец валютной бумаги — NaN, рублёвые не затронуты', async () => {
    const fr = await Q.priceHistoryRub(['EU1', 'SBER'], '2024-11-25', '2024-11-27', { fetchImpl: hist({ cbrDown: true }) });
    assert.ok(fr.cols.EU1.every(Number.isNaN)); assert.deepEqual(fr.cols.SBER, [270, 271, 272]);
  });
  test('ошибка цен бумаги пробрасывается (неизвестный тикер)', async () => {
    await assert.rejects(() => Q.priceHistoryRub(['NOPE'], '2024-11-25', '2024-11-27', { fetchImpl: hist() }), /не найден|недоступен/);
  });
});

describe('getQuotes: пачки по 25 тикеров', () => {
  const ids = Array.from({ length: 60 }, (_, i) => `T${String(i).padStart(2, '0')}`);
  const mkMany = (failChunkWith) => {
    const reqs = [];
    const f = routeFetch((url) => {
      const u = new URL(url), p = u.pathname;
      if (/\/securities\/([^/]+)\.json$/.test(p)) {
        return { boards: blk(['boardid', 'engine', 'market', 'is_primary'], [['TQBR', 'stock', 'shares', 1]]), description: blk(['name', 'title', 'value'], []) };
      }
      if (/\/boards\/TQBR\/securities\.json$/.test(p)) {
        const want = u.searchParams.get('securities').split(',');
        reqs.push(want);
        if (failChunkWith && want.includes(failChunkWith)) return new Error('boom');
        return { securities: blk(['SECID', 'SHORTNAME', 'LOTSIZE', 'PREVPRICE'], want.map((s) => [s, s, 1, 10])), marketdata: blk(['SECID', 'LAST'], []) };
      }
      return undefined;
    });
    return { f, reqs };
  };
  test('60 тикеров -> 3 запроса (25+25+10)', async () => {
    const { f, reqs } = mkMany();
    const q = await Q.getQuotes(ids, OPTS(f));
    assert.deepEqual(reqs.map((r) => r.length).sort((a, b) => b - a), [25, 25, 10]);
    assert.equal(Object.keys(q).length, 60);
  });
  test('ошибка одной пачки не теряет остальные', async () => {
    const { f } = mkMany('T30');
    const q = await Q.getQuotes(ids, OPTS(f));
    assert.equal(Object.keys(q).length, 35); assert.ok(q.T00 && q.T59 && !q.T30);
  });
});
