// web/lib/symbol.js — данные страницы бумаги (web/CONTRACT.md, «Этап 1») на поддельном fetch, без сети.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as Y from '../../web/lib/symbol.js';
import { routeFetch, close } from './_helpers.mjs';

const EPS = 1e-12;
const b = (columns, data) => ({ columns, data });
const qp = (url, k) => new URL(url).searchParams.get(k);
const BOARD_COLS = ['secid', 'boardid', 'market', 'engine', 'is_primary', 'history_from'];
const boards = (...rows) => ({ boards: b(BOARD_COLS, rows) });
const desc = (pairs) => ({ description: b(['name', 'title', 'value'], pairs.map(([k, v]) => [k, k, v])) });

/** Таблица маршрутов: [regex, ответ | (url) => ответ]; первый совпавший. Нет совпадения — 404. */
function iss(routes) {
  return routeFetch((url) => {
    for (const [re, v] of routes) if (re.test(url)) return typeof v === 'function' ? v(url) : v;
    return undefined;
  });
}

// ------------------------------------------------------------------ классификация
describe('classify', () => {
  const cases = [
    [{ type: 'common_share', group: 'stock_shares' }, 'share'],
    [{ type: 'preferred_share' }, 'share'],
    [{ type: 'depositary_receipt', group: 'stock_dr' }, 'share'],
    [{ sectype: '1' }, 'share'], [{ sectype: '2' }, 'share'], [{ sectype: 'D' }, 'share'],
    [{ type: 'etf_ppif', group: 'stock_ppif' }, 'fund'], [{ type: 'public_ppif' }, 'fund'],
    [{ sectype: 'J' }, 'fund'], [{ sectype: '9' }, 'fund'], [{ sectype: 'A' }, 'fund'], [{ sectype: 'B' }, 'fund'],
    [{ type: 'ofz_bond', group: 'stock_bonds' }, 'bond'], [{ type: 'corporate_bond' }, 'bond'], [{ market: 'bonds' }, 'bond'],
    [{ type: 'stock_index', group: 'stock_index' }, 'index'], [{ market: 'index' }, 'index'],
    [{ secid: 'GLDRUB_TOM', group: 'currency_metal' }, 'metal'], [{ secid: 'gldrub_tom' }, 'metal'],
    [{ group: 'currency_selt' }, 'currency'], [{ market: 'selt' }, 'currency'],
    [{ type: 'futures' }, null], [{}, null], [undefined, null],
  ];
  for (const [arg, exp] of cases) {
    test(`${JSON.stringify(arg)} → ${exp}`, () => assert.equal(Y.classify(arg), exp));
  }
  test('SYMBOL_CLASSES: подписи по-русски для всех классов', () => {
    assert.deepEqual(Y.SYMBOL_CLASSES, { share: 'Акция', fund: 'Фонд', bond: 'Облигация', index: 'Индекс', metal: 'Металл', currency: 'Валюта' });
  });
});

// ------------------------------------------------------------------ поиск
describe('searchSecurities', () => {
  const SEARCH_COLS = ['secid', 'shortname', 'name', 'isin', 'type', 'group', 'primary_boardid', 'is_traded'];
  test('запрос ISS /securities.json?q=, торгуемые первыми, классы, поля', async () => {
    const f = iss([[/\/securities\.json\?/, (u) => {
      assert.equal(qp(u, 'q'), 'сбер');
      assert.equal(qp(u, 'iss.meta'), 'off');
      return { securities: b(SEARCH_COLS, [
        ['SBERX', null, 'Старый фонд', null, 'etf_ppif', 'stock_ppif', 'TQTF', 0],
        ['SBER', 'Сбербанк', 'Сбербанк ао', 'RU0009029540', 'common_share', 'stock_shares', 'TQBR', 1],
        [null, 'мусор', 'без secid', null, null, null, null, 1],
        ['SBERP', 'Сбербанк-п', 'Сбербанк ап', 'RU0009029557', 'preferred_share', 'stock_shares', 'TQBR', 1],
      ]) };
    }]]);
    const r = await Y.searchSecurities('  сбер ', { fetchImpl: f });
    assert.deepEqual(r.map((x) => x.secid), ['SBER', 'SBERP', 'SBERX']);
    assert.deepEqual(r[0], { secid: 'SBER', name: 'Сбербанк ао', shortName: 'Сбербанк', isin: 'RU0009029540', cls: 'share', primaryBoard: 'TQBR', isTraded: true });
    assert.deepEqual(r[2], { secid: 'SBERX', name: 'Старый фонд', shortName: 'SBERX', isin: null, cls: 'fund', primaryBoard: 'TQTF', isTraded: false });
  });
  test('limit: в запросе 1…100, ответ обрезается', async () => {
    const rows = Array.from({ length: 5 }, (_, i) => [`T${i}`, `T${i}`, `T${i}`, null, 'common_share', 'stock_shares', 'TQBR', 1]);
    const f = iss([[/securities\.json/, { securities: b(SEARCH_COLS, rows) }]]);
    const r = await Y.searchSecurities('t', { fetchImpl: f, limit: 2 });
    assert.equal(r.length, 2);
    assert.equal(qp(f.calls[0], 'limit'), '2');
    await Y.searchSecurities('tt', { fetchImpl: f, limit: 1000 });
    assert.equal(qp(f.calls[1], 'limit'), '100');
  });
  test('пустой запрос — [] без обращения к сети; нет блока securities — []', async () => {
    const f = iss([[/securities\.json/, {}]]);
    assert.deepEqual(await Y.searchSecurities('   ', { fetchImpl: f }), []);
    assert.equal(f.calls.length, 0);
    assert.deepEqual(await Y.searchSecurities('x', { fetchImpl: f }), []);
  });
});

// ------------------------------------------------------------------ справка
describe('fetchSecurityInfo', () => {
  const SBER = [
    [/\/securities\/SBER\.json\?/, {
      ...desc([['SECID', 'SBER'], ['NAME', 'Сбербанк ао'], ['SHORTNAME', 'Сбербанк'], ['ISIN', 'RU0009029540'],
        ['TYPE', 'common_share'], ['GROUP', 'stock_shares'], ['TYPENAME', 'Акция обыкновенная'], ['LISTLEVEL', '1'],
        ['FACEUNIT', 'SUR'], ['ISSUEDATE', '2007-07-11'], ['FACEVALUE', '3']]),
      ...boards(['SBER', 'EQBR', 'shares', 'stock', 0, '1997-03-24'], ['SBER', 'TQBR', 'shares', 'stock', 1, '2013-03-25'],
        ['SBER', 'SPEQ', 'shares', 'stock', 0, null], ['SBER', 'RPMA', 'repo', 'stock', 0, '1990-01-01']),
    }],
    [/\/securities\.json\?.*emitent_title/, { securities: b(['secid', 'emitent_title'], [['SBERP', 'не тот'], ['SBER', 'ПАО Сбербанк']]) }],
  ];
  test('акция: поля, эмитент, начало торгов — самая ранняя history_from того же рынка', async () => {
    const info = await Y.fetchSecurityInfo('sber', { fetchImpl: iss(SBER) });
    assert.equal(info.secid, 'SBER');
    assert.equal(info.cls, 'share');
    assert.equal(info.typeLabel, 'Акция обыкновенная');
    assert.equal(info.issuer, 'ПАО Сбербанк');
    assert.equal(info.listLevel, 1);
    assert.equal(info.currency, 'RUB');
    assert.equal(info.firstTradeDate, '1997-03-24', 'EQBR, а не repo-режим 1990');
    assert.equal(info.board, 'TQBR');
    assert.equal(info.engine, 'stock');
    assert.equal(info.market, 'shares');
    assert.equal(info.benchmark, 'MCFTR');
    assert.equal(info.matDate, null);
    assert.equal(info.couponPercent, null);
  });
  test('ключи результата — как в контракте', async () => {
    const info = await Y.fetchSecurityInfo('SBER', { fetchImpl: iss(SBER) });
    for (const k of ['secid', 'name', 'shortName', 'isin', 'cls', 'typeLabel', 'issuer', 'listLevel', 'currency', 'issueDate',
      'firstTradeDate', 'faceValue', 'matDate', 'couponPercent', 'couponPeriod', 'offerDate', 'board', 'engine', 'market', 'benchmark']) {
      assert.ok(k in info, k);
    }
  });
  test('облигация ОФЗ: купонный период из частоты, нулевая дата оферты, бенчмарк RGBITR', async () => {
    const f = iss([
      [/\/securities\/SU26238RMFS4\.json/, {
        ...desc([['NAME', 'ОФЗ-ПД 26238'], ['TYPE', 'ofz_bond'], ['GROUP', 'stock_bonds'], ['COUPONFREQUENCY', '2'],
          ['COUPONPERCENT', '7.1'], ['MATDATE', '2041-05-15'], ['OFFERDATE', '0000-00-00'], ['FACEVALUE', '1000'], ['FACEUNIT', 'SUR']]),
        ...boards(['SU26238RMFS4', 'TQOB', 'bonds', 'stock', 1, '2021-06-16']),
      }],
      [/emitent_title/, new Error('сбой поиска')],
    ]);
    const info = await Y.fetchSecurityInfo('SU26238RMFS4', { fetchImpl: f });
    assert.equal(info.cls, 'bond');
    assert.equal(info.couponPeriod, 182);
    assert.equal(info.couponPercent, 7.1);
    assert.equal(info.faceValue, 1000);
    assert.equal(info.matDate, '2041-05-15');
    assert.equal(info.offerDate, null);
    assert.equal(info.issuer, null, 'ошибка поиска эмитента — null, не исключение');
    assert.equal(info.benchmark, 'RGBITR');
    assert.equal(info.shortName, 'SU26238RMFS4', 'нет SHORTNAME — тикер');
  });
  test('корпоративная облигация — RUCBTRNS; COUPONPERIOD важнее частоты; оферта PUTOPTIONDATE', async () => {
    const f = iss([[/\/securities\/RU000A0JX0J2\.json/, {
      ...desc([['TYPE', 'corporate_bond'], ['COUPONPERIOD', '91'], ['COUPONFREQUENCY', '2'], ['PUTOPTIONDATE', '2027-03-01']]),
      ...boards(['RU000A0JX0J2', 'TQCB', 'bonds', 'stock', 1, '2017-01-01']),
    }], [/emitent_title/, { securities: b(['secid', 'emitent_title'], []) }]]);
    const info = await Y.fetchSecurityInfo('RU000A0JX0J2', { fetchImpl: f });
    assert.equal(info.benchmark, 'RUCBTRNS');
    assert.equal(info.couponPeriod, 91);
    assert.equal(info.offerDate, '2027-03-01');
    assert.equal(info.issuer, null);
  });
  test('индекс IMOEX — бенчмарк MCFTR (не сам с собой); металл — GOLD_CBR; валюта — null', async () => {
    const one = (id, d, board) => iss([[new RegExp(`/securities/${id}\\.json`), { ...desc(d), ...boards(board) }],
      [/emitent_title/, { securities: b(['secid', 'emitent_title'], []) }]]);
    const imoex = await Y.fetchSecurityInfo('IMOEX', { fetchImpl: one('IMOEX', [['TYPE', 'stock_index']], ['IMOEX', 'SNDX', 'index', 'stock', 1, '1997-09-22']) });
    assert.equal(imoex.cls, 'index');
    assert.equal(imoex.benchmark, 'MCFTR');
    const mc = await Y.fetchSecurityInfo('RTSI', { fetchImpl: one('RTSI', [['TYPE', 'stock_index']], ['RTSI', 'SNDX', 'index', 'stock', 1, null]) });
    assert.equal(mc.benchmark, 'IMOEX');
    const gold = await Y.fetchSecurityInfo('GLDRUB_TOM', { fetchImpl: one('GLDRUB_TOM', [['GROUP', 'currency_metal']], ['GLDRUB_TOM', 'CETS', 'selt', 'currency', 1, '2016-01-01']) });
    assert.equal(gold.cls, 'metal');
    assert.equal(gold.benchmark, 'GOLD_CBR');
    const usd = await Y.fetchSecurityInfo('USD000UTSTOM', { fetchImpl: one('USD000UTSTOM', [['GROUP', 'currency_selt']], ['USD000UTSTOM', 'CETS', 'selt', 'currency', 1, null]) });
    assert.equal(usd.cls, 'currency');
    assert.equal(usd.benchmark, null);
  });
  test('нет description и полей — null вместо исключения; нет режимов — ошибка «не найден»', async () => {
    const f = iss([[/\/securities\/XXXX\.json/, boards(['XXXX', 'TQBR', 'shares', 'stock', 0, null])],
      [/emitent_title/, {}], [/\/securities\/NONE\.json/, { boards: b(BOARD_COLS, []) }]]);
    const info = await Y.fetchSecurityInfo('XXXX', { fetchImpl: f });
    assert.equal(info.name, 'XXXX');
    assert.equal(info.isin, null);
    assert.equal(info.cls, null);
    assert.equal(info.typeLabel, 'Инструмент');
    assert.equal(info.firstTradeDate, null);
    assert.equal(info.listLevel, null);
    assert.equal(info.board, 'TQBR', 'нет is_primary — первый режим');
    await assert.rejects(Y.fetchSecurityInfo('NONE', { fetchImpl: f }), /не найден/);
  });
});

// ------------------------------------------------------------------ свечи
const CANDLE_COLS = ['open', 'close', 'high', 'low', 'value', 'volume', 'begin', 'end'];
function dayList(n, start = '2024-01-01') {
  const t0 = Date.parse(start + 'T00:00:00Z');
  return Array.from({ length: n }, (_, i) => new Date(t0 + i * 86400000).toISOString().slice(0, 10));
}

describe('fetchOHLC', () => {
  test('свечи основного режима: куски ≤ 700 дней, пагинация по 500, пропуски O/H/L — NaN, строки без close отброшены', async () => {
    const ds = dayList(620);
    const rows = ds.map((d, i) => [100 + i, 101 + i, 102 + i, 99 + i, 1, 10 * i, `${d} 00:00:00`, `${d} 23:59:59`]);
    rows[3][0] = null;                 // нет open
    rows[4][1] = 0;                    // close = 0 — строка отбрасывается
    rows[5][1] = null;                 // нет close
    const f = iss([
      [/\/securities\/SBER\.json\?.*iss\.only=boards/, boards(['SBER', 'TQBR', 'shares', 'stock', 1, null])],
      [/\/engines\/stock\/markets\/shares\/boards\/TQBR\/securities\/SBER\/candles\.json/, (u) => {
        assert.equal(qp(u, 'interval'), '24');
        const s = Number(qp(u, 'start'));
        const from = qp(u, 'from'), till = qp(u, 'till');
        const inRange = rows.filter((r) => r[6].slice(0, 10) >= from && r[6].slice(0, 10) <= till);
        return { candles: b(CANDLE_COLS, inRange.slice(s, s + 500)) };
      }],
    ]);
    const o = await Y.fetchOHLC('sber', ds[0], '2025-12-31', { fetchImpl: f });
    const calls = f.calls.filter((u) => u.includes('candles'));
    for (const u of calls) {
      const days = (Date.parse(qp(u, 'till')) - Date.parse(qp(u, 'from'))) / 864e5;
      assert.ok(days <= 700, `кусок ${qp(u, 'from')}..${qp(u, 'till')} длиннее 700 дней`);
    }
    assert.ok(calls.some((u) => qp(u, 'start') === '500') || calls.length > 1);   // больше одной страницы/куска
    assert.ok(!calls.some((u) => /splits/.test(u)));                                 // сплиты не запрашиваются
    assert.equal(o.dates.length, 618);
    assert.equal(new Set(o.dates).size, o.dates.length);                             // куски не дублируют даты
    assert.equal(o.splitAdjusted, true);
    assert.ok(!o.dates.includes(ds[4]) && !o.dates.includes(ds[5]));
    assert.ok(Number.isNaN(o.open[3]));
    assert.equal(o.close[0], 101);
    assert.equal(o.volume[1], 10);
    assert.deepEqual(Object.keys(o), ['dates', 'open', 'high', 'low', 'close', 'volume', 'splitAdjusted']);
  });
  test('индекс: history рынка index с cursor, volume — NaN', async () => {
    const rows = [['2024-01-03', 3000, 3050, 2990, 3010], ['2024-01-04', 3010, null, null, 3020], ['2024-01-05', null, null, null, 3030]];
    const f = iss([
      [/\/iss\/securities\/IMOEX\.json/, boards(['IMOEX', 'SNDX', 'index', 'stock', 1, null])],
      [/\/history\/engines\/stock\/markets\/index\/securities\/IMOEX\.json/, (u) => {
        const s = Number(qp(u, 'start'));
        return { history: b(['TRADEDATE', 'OPEN', 'HIGH', 'LOW', 'CLOSE'], rows.slice(s, s + 2)),
          'history.cursor': b(['INDEX', 'TOTAL', 'PAGESIZE'], [[s, 3, 2]]) };
      }],
    ]);
    const o = await Y.fetchOHLC('IMOEX', '2024-01-01', '2024-01-31', { fetchImpl: f });
    assert.deepEqual(o.dates, ['2024-01-03', '2024-01-04', '2024-01-05']);
    assert.deepEqual(o.close, [3010, 3020, 3030]);
    assert.ok(o.volume.every(Number.isNaN));
    assert.ok(Number.isNaN(o.high[1]) && Number.isNaN(o.open[2]));
  });
});

// ------------------------------------------------------------------ ключевые цифры
describe('fetchSnapshot', () => {
  const candles = (rows) => ({ candles: b(CANDLE_COLS, rows.map(([d, o, c, h, l]) => [o, c, h, l, 1, 1, `${d} 00:00:00`, ''])) });
  const CANDLES = candles([['2025-01-10', 380, 390, 400, 370], ['2026-01-05', 260, 300, 320, 250], ['2026-10-07', 295, 300, 310, 290]]);

  test('акция: изменение к PREVPRICE, капитализация = цена × выпуск, 52 недели по свечам за 365 дней', async () => {
    const f = iss([
      [/\/securities\/SBER\.json\?.*iss\.only=boards/, boards(['SBER', 'TQBR', 'shares', 'stock', 1, null])],
      [/boards\/TQBR\/securities\/SBER\.json\?/, {
        securities: b(['SECID', 'PREVPRICE', 'ISSUESIZE', 'SECTYPE'], [['SBER', 300, 1000, '1']]),
        marketdata: b(['SECID', 'LAST', 'VALTODAY_RUR', 'ISSUECAPITALIZATION', 'TRADEDATE'], [['SBER', 306, 5e9, null, '2026-10-08']]),
      }],
      [/candles\.json/, (u) => (Number(qp(u, 'start')) ? candles([]) : CANDLES)],
    ]);
    const s = await Y.fetchSnapshot('SBER', { fetchImpl: f });
    assert.equal(s.date, '2026-10-08');
    assert.equal(s.last, 306);
    assert.equal(s.prevClose, 300);
    assert.equal(s.change, 6);
    close(s.changePct, 0.02, EPS, 'changePct — доля');
    assert.equal(s.high52, 320, '2025-01-10 старше 365 дней от последней свечи');
    assert.equal(s.low52, 250);
    assert.equal(s.valueRub, 5e9);
    assert.equal(s.marketCap, 306000);
    assert.equal(s.bond, null);
  });
  test('нет полей marketdata и securities: цена и изменение — по свечам, без исключений', async () => {
    const f = iss([
      [/iss\.only=boards/, boards(['GAZP', 'TQBR', 'shares', 'stock', 1, null])],
      [/boards\/TQBR\/securities\/GAZP\.json\?/, { marketdata: b(['SECID'], [['GAZP']]) }],
      [/candles\.json/, (u) => (Number(qp(u, 'start')) ? candles([]) : CANDLES)],
    ]);
    const s = await Y.fetchSnapshot('GAZP', { fetchImpl: f });
    assert.equal(s.date, '2026-10-07');
    assert.equal(s.last, 300);
    assert.equal(s.prevClose, 300, 'закрытие предыдущей свечи');
    assert.equal(s.change, 0);
    assert.equal(s.valueRub, null);
    assert.equal(s.marketCap, null, 'класс неизвестен (нет SECTYPE) — капитализацию не выдумываем');
  });
  test('пустые ответы: всё null, без исключений', async () => {
    const f = iss([[/iss\.only=boards/, boards(['ZZZ', 'TQBR', 'shares', 'stock', 1, null])],
      [/boards\/TQBR\/securities\/ZZZ\.json\?/, {}], [/candles\.json/, candles([])]]);
    const s = await Y.fetchSnapshot('ZZZ', { fetchImpl: f });
    assert.deepEqual(s, { date: null, last: null, prevClose: null, change: null, changePct: null, high52: null, low52: null,
      valueRub: null, marketCap: null, bond: null });
  });
  test('облигация: доходность — доля, дюрация — годы, НКД и купон; EFFECTIVEYIELD как запасной', async () => {
    const f = iss([
      [/iss\.only=boards/, boards(['SU26238RMFS4', 'TQOB', 'bonds', 'stock', 1, null])],
      [/boards\/TQOB\/securities\/SU26238RMFS4\.json\?/, {
        securities: b(['SECID', 'PREVPRICE', 'ACCRUEDINT', 'COUPONVALUE', 'NEXTCOUPON'], [['SU26238RMFS4', 95.5, 10.5, 35.4, '2026-11-01']]),
        marketdata: b(['SECID', 'LAST', 'YIELD', 'DURATION'], [['SU26238RMFS4', 96, null, 730.5]]),
        marketdata_yields: b(['SECID', 'EFFECTIVEYIELD'], [['SU26238RMFS4', 12.5]]),
      }],
      [/candles\.json/, candles([])],
    ]);
    const s = await Y.fetchSnapshot('SU26238RMFS4', { fetchImpl: f });
    close(s.change, 0.5, EPS, 'change');
    close(s.bond.yield, 0.125, EPS, 'yield');
    close(s.bond.duration, 2, EPS, 'duration');
    assert.deepEqual({ ...s.bond, yield: 0, duration: 0 }, { yield: 0, duration: 0, accruedInt: 10.5, couponValue: 35.4, nextCoupon: '2026-11-01' });
  });
  test('индекс: prevClose = CURRENTVALUE − LASTCHANGE', async () => {
    const f = iss([
      [/\/securities\/IMOEX\.json\?.*iss\.only=boards/, boards(['IMOEX', 'SNDX', 'index', 'stock', 1, null])],
      [/boards\/SNDX\/securities\/IMOEX\.json\?/, { marketdata: b(['SECID', 'CURRENTVALUE', 'LASTCHANGE', 'CAPITALIZATION'], [['IMOEX', 3000, 30, 5e13]]) }],
      [/history/, { history: b(['TRADEDATE', 'OPEN', 'HIGH', 'LOW', 'CLOSE'], []) }],
    ]);
    const s = await Y.fetchSnapshot('IMOEX', { fetchImpl: f });
    assert.equal(s.last, 3000);
    assert.equal(s.prevClose, 2970);
    close(s.changePct, 30 / 2970, EPS, 'changePct');
    assert.equal(s.marketCap, 5e13);
  });
});

// ------------------------------------------------------------------ купоны
describe('loadCoupons (bondization)', () => {
  test('пагинация по *.cursor: 150 купонов страницами по 100; value_rub/value, rate — доля', async () => {
    const coupons = Array.from({ length: 150 }, (_, i) => {
      const d = new Date(Date.UTC(2020, 0, 1) + i * 182 * 86400000).toISOString().slice(0, 10);
      return [d, 35, 35, 7.1];
    }).reverse();                                                     // в обратном порядке — функция сортирует
    coupons[0] = [coupons[0][0], 40, null, null];                    // нет value_rub и ставки
    coupons[1] = [coupons[1][0], null, null, null];                  // неизвестный купон
    const amort = [['2041-05-15', 1000, 1000]];
    const offers = [['2030-01-01', 'Put']];
    const f = iss([[/bondization\/SU26238RMFS4\.json/, (u) => {
      const s = Number(qp(u, 'start'));
      assert.equal(qp(u, 'limit'), '100');
      return {
        coupons: b(['coupondate', 'value', 'value_rub', 'valueprc'], coupons.slice(s, s + 100)),
        'coupons.cursor': b(['INDEX', 'TOTAL', 'PAGESIZE'], [[s, 150, 100]]),
        amortizations: b(['amortdate', 'value', 'value_rub'], amort.slice(s, s + 100)),
        'amortizations.cursor': b(['INDEX', 'TOTAL', 'PAGESIZE'], [[s, 1, 100]]),
        offers: b(['offerdate', 'offertype'], offers.slice(s, s + 100)),
      };
    }]]);
    const c = await Y.loadCoupons('su26238rmfs4', { fetchImpl: f });
    assert.deepEqual(f.calls.map((u) => qp(u, 'start')), ['0', '100']);
    assert.equal(c.coupons.length, 150);
    assert.deepEqual(c.coupons.map((x) => x.date), [...c.coupons.map((x) => x.date)].sort());
    const last = c.coupons.at(-1), prev = c.coupons.at(-2);
    assert.deepEqual(last, { date: last.date, value: 40, rate: null });
    assert.deepEqual(prev, { date: prev.date, value: null, rate: null });
    assert.equal(c.coupons[0].value, 35);
    close(c.coupons[0].rate, 0.071, EPS, 'rate');
    assert.deepEqual(c.amortizations, [{ date: '2041-05-15', value: 1000 }]);
    assert.deepEqual(c.offers, [{ date: '2030-01-01', type: 'Put' }]);
  });
  test('пустой ответ — пустые списки', async () => {
    const c = await Y.loadCoupons('X', { fetchImpl: iss([[/bondization/, {}]]) });
    assert.deepEqual(c, { coupons: [], amortizations: [], offers: [] });
  });
});

// ------------------------------------------------------------------ дивиденды
describe('loadDividends', () => {
  const FILE = { updated: '2026-10-08', source: 'T-Invest API', data: {
    SBER: [
      { record_date: '2025-07-21', last_buy_date: '2025-07-18', value: 34.84, currency: 'rub', yield_value: 11.2, cancelled: false,
        payment_date: '2025-07-30', declared_date: '2025-04-17' },            // пятница -> понедельник
      { record_date: '2024-07-11', last_buy_date: '2024-07-10', value: 33.3, currency: 'RUB', yield_value: 10.2 },   // ср -> чт
      { record_date: '2023-05-11', value: 25, yield_value: null },             // нет last_buy_date -> exDate = record_date
      { record_date: '2022-05-12', last_buy_date: '2022-05-10', value: 18.7, cancelled: true },
      { record_date: '2021-05-12', value: null },                              // без значения — пропуск
      { record_date: 'нет даты', value: 1 },                                   // плохая дата — пропуск
      null,
    ],
  } };
  test('exDate — следующий рабочий день после last_buy_date; отменённые остаются с флагом; yield — доля', async () => {
    const f = iss([[/\/x\/dividends\.json$/, FILE]]);
    const d = await Y.loadDividends('sber', { base: '/x', fetchImpl: f });
    assert.deepEqual(d.map((x) => x.recordDate), ['2022-05-12', '2023-05-11', '2024-07-11', '2025-07-21']);
    assert.deepEqual(d.map((x) => x.exDate), ['2022-05-11', '2023-05-11', '2024-07-11', '2025-07-21']);
    assert.deepEqual(d.map((x) => x.cancelled), [true, false, false, false]);
    const last = d.at(-1);
    close(last.yield, 0.112, EPS, 'yield');
    assert.equal(last.currency, 'RUB');
    assert.equal(last.paymentDate, '2025-07-30');
    assert.equal(last.declaredDate, '2025-04-17');
    assert.equal(d[1].yield, null);
    assert.equal(d[1].currency, 'RUB', 'валюта не указана — рубль');
  });
  test('нет бумаги в файле или нет файла — []', async () => {
    assert.deepEqual(await Y.loadDividends('GAZP', { base: '/x/', fetchImpl: iss([[/dividends\.json/, FILE]]) }), []);
    assert.deepEqual(await Y.loadDividends('SBER', { base: '/x/', fetchImpl: iss([[/dividends\.json/, { data: null }]]) }), []);
    assert.deepEqual(await Y.loadDividends('SBER', { base: '/x/', fetchImpl: iss([]) }), []);
  });
});

describe('dividendStats', () => {
  const D = (exDate, value, extra = {}) => ({ recordDate: exDate, exDate, value, currency: 'RUB', cancelled: false, ...extra });
  const divs = [
    D('2021-07-10', 10), D('2022-07-11', 10), D('2023-05-10', 6), D('2023-10-10', 6), D('2024-07-10', 15),
    D('2024-12-10', 1, { cancelled: true }), D('2024-10-10', 5, { currency: 'USD' }),
    D('2025-01-15', 3), D('2025-07-15', 20),
  ];
  const px = { dates: ['2025-06-27', '2025-06-30'], values: [95, 100] };

  test('вручную: TTM, годы, месяцы, серия роста, выплат в год, объявленные', () => {
    const st = Y.dividendStats(divs, px);
    close(st.ttmValue, 18, EPS, 'TTM = 15 + 3 (отменённая и долларовая исключены)');
    close(st.ttmYield, 0.18, EPS, 'TTM / 100');
    assert.deepEqual(st.byYear, [{ year: 2021, value: 10 }, { year: 2022, value: 10 }, { year: 2023, value: 12 },
      { year: 2024, value: 15 }, { year: 2025, value: 3 }]);
    assert.deepEqual(st.byMonth, [1, 0, 0, 0, 1, 0, 3, 0, 0, 1, 0, 0]);
    assert.equal(st.growthStreak, 2, '2024 > 2023 > 2022 = 2021');
    close(st.payoutsPerYear, 4 / 3, EPS, '2022–2024: 1, 2, 1');
    assert.deepEqual(st.upcoming.map((d) => d.exDate), ['2025-07-15']);
  });
  test('нет цены — ttmYield null; нет выплат — нули и null', () => {
    const st = Y.dividendStats(divs, { dates: ['2025-06-30'], values: [NaN] });
    assert.equal(st.ttmYield, null);
    const e = Y.dividendStats([], px);
    assert.deepEqual({ ...e, upcoming: e.upcoming.length }, { ttmValue: 0, ttmYield: 0, byYear: [], byMonth: new Array(12).fill(0),
      growthStreak: 0, payoutsPerYear: null, upcoming: 0 });
    const n = Y.dividendStats(null, null);
    assert.equal(n.ttmValue, 0);
  });
});

// ------------------------------------------------------------------ фонды и рейтинги
describe('loadFundInfo', () => {
  const F = (ticker, asset_class, commission_pct, trade_status = 'Торгуется', extra = {}) =>
    ({ ticker, asset_class, commission_pct, trade_status, issuer: `УК ${ticker}`, aum_rub: 1e9, ...extra });
  const FILE = { updated: '2026-10-08', source: 'RusETFs', data: [
    F('AAA', 'Облигации', 0.2), F('BBB', 'Облигации', 0.4), F('CCC', 'Облигации', 0.6, null),
    F('DDD', 'Облигации', 1.0, 'Не торгуется'), F('EEE', 'Облигации', 0),
    F('FFF', 'Акции', 1.2), F('GGG', 'Акции', 0.8), { ticker: null }, 'мусор',
  ] };
  const f = () => iss([[/\/d\/rusetfs_funds\.json$/, FILE]]);

  test('рынок — торгуемые фонды того же класса с комиссией > 0; перцентили линейные', async () => {
    const r = await Y.loadFundInfo('aaa', { base: '/d/', fetchImpl: f() });
    assert.equal(r.info.ticker, 'AAA');
    assert.equal(r.info.issuer, 'УК AAA');
    const m = r.market;
    close(m.min, 0.2, EPS, 'min');
    close(m.p25, 0.3, EPS, 'p25');
    close(m.median, 0.4, EPS, 'median');
    close(m.p75, 0.5, EPS, 'p75');
    close(m.max, 0.6, EPS, 'max');
    assert.equal(m.n, 3);
    close(m.classMedian, 0.4, EPS, 'classMedian');
    close(m.marketMedian, 0.6, EPS, 'медиана всех торгуемых: .2 .4 .6 .8 1.2');
  });
  test('неторгуемый фонд есть в справочнике, рынок — по его классу', async () => {
    const r = await Y.loadFundInfo('DDD', { base: '/d/', fetchImpl: f() });
    assert.equal(r.info.ticker, 'DDD');
    assert.equal(r.market.n, 3);
  });
  test('фонда нет — info null, рынок по всем торгуемым', async () => {
    const r = await Y.loadFundInfo('ZZZ', { base: '/d/', fetchImpl: f() });
    assert.equal(r.info, null);
    assert.equal(r.market.n, 5);
    close(r.market.min, 0.2, EPS, 'min');
    close(r.market.median, 0.6, EPS, 'median');
    close(r.market.max, 1.2, EPS, 'max');
  });
  test('нет файла — {info: null, market: {… null, n: 0}}', async () => {
    const r = await Y.loadFundInfo('AAA', { base: '/d/', fetchImpl: iss([]) });
    assert.deepEqual(r, { info: null, market: { min: null, p25: null, median: null, p75: null, max: null, n: 0, classMedian: null, marketMedian: null } });
  });
});

describe('loadSymbolStats', () => {
  test('файл есть — объект как в файле; нет файла / нет items — null', async () => {
    const body = { updated: '2026-10-08T03:00:00+00:00', source: 'ISS MOEX (расчёт ИнвестАналитики)', rf: 0.17,
      window: {}, classes: { share: { n: 1 } }, items: { SBER: { class: 'share', score: 50 } } };
    assert.deepEqual(await Y.loadSymbolStats({ base: '/d', fetchImpl: iss([[/\/d\/symbol_stats\.json$/, body]]) }), body);
    assert.equal(await Y.loadSymbolStats({ base: '/d', fetchImpl: iss([]) }), null);
    assert.equal(await Y.loadSymbolStats({ base: '/d', fetchImpl: iss([[/symbol_stats/, { updated: 'x' }]]) }), null);
    assert.equal(await Y.loadSymbolStats({ base: '/d', fetchImpl: iss([[/symbol_stats/, 'не json']]) }), null);
  });
});

describe('dividendStats.growthStreak (второй круг): год первой выплаты ростом не считается', () => {
  const D = (exDate, value) => ({ recordDate: exDate, exDate, value, currency: 'RUB', cancelled: false });
  const px = { dates: ['2026-06-30'], values: [100] };            // последний полный год — 2025
  test('2023: 1, 2024: 2, 2025: 3 → 2', () => {
    assert.equal(Y.dividendStats([D('2023-07-10', 1), D('2024-07-10', 2), D('2025-07-10', 3)], px).growthStreak, 2);
  });
  test('только 2025 → 0', () => {
    assert.equal(Y.dividendStats([D('2025-07-10', 3)], px).growthStreak, 0);
  });
  test('2022: 5, 2023 без выплат, 2024: 3, 2025: 4 → 2 (рост после пропущенного года засчитывается)', () => {
    assert.equal(Y.dividendStats([D('2022-07-10', 5), D('2024-07-10', 3), D('2025-07-10', 4)], px).growthStreak, 2);
  });
  test('выплаты только в текущем неполном году → 0', () => {
    assert.equal(Y.dividendStats([D('2026-05-10', 3)], px).growthStreak, 0);
  });
});
