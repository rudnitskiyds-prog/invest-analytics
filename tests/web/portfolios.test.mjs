// Раздел «Портфели и стратегии»: web/lib/portfolios.js — загрузка и выборка из portfolios.json (без сети).
// Фикстура: tests/fixtures/portfolios_small.json (4 портфеля, 2 группы с портфелями + пустая группа).
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import * as P from '../../web/lib/portfolios.js';
import { DEFAULT_DATA_BASE, clearCache } from '../../web/lib/data.js';
import { FIXTURES, routeFetch } from './_helpers.mjs';

const RAW = fs.readFileSync(path.join(FIXTURES, 'portfolios_small.json'), 'utf8');
/** Свежая копия фикстуры (тесты могут её менять). */
const fixture = () => JSON.parse(RAW);
const ids = (rows) => rows.map((r) => r.id);

/** fetchImpl, отдающий фикстуру на любой URL, оканчивающийся на portfolios.json; новый на каждый тест (свой кэш). */
function fixtureServer(body = RAW) {
  return routeFetch((url) => (url.endsWith('/portfolios.json') ? body : undefined));
}

describe('portfolioList: сортировка и фильтр', () => {
  test('все портфели: rank по убыванию, равные — по имени, без rank — в конце', () => {
    const rows = P.portfolioList(fixture());
    // zeta 80; alpha «Альфа» 50 и beta «Бета» 50 — по имени; gamma rank null — последний
    assert.deepEqual(ids(rows), ['zeta', 'alpha', 'beta', 'gamma']);
    assert.deepEqual(rows[0], { id: 'zeta', name: 'Яблоко', group: 'Ленивые портфели', rank: 80, cagr_10y: 0.12 });
  });

  test('rank NaN / строка / отсутствует считаются «без rank» и сортируются по имени (ru)', () => {
    const d = fixture();
    d.items.nan = { row: { name: 'Аист', group: 'Тематические', rank: NaN } };
    d.items.str = { row: { name: 'Ёж', group: 'Тематические', rank: '99' } };
    d.items.none = { row: { name: 'Дуб', group: 'Тематические' } };
    d.items.inf = { row: { name: 'Бесконечность', group: 'Тематические', rank: Infinity } };
    const rows = P.portfolioList(d);
    assert.deepEqual(ids(rows).slice(0, 3), ['zeta', 'alpha', 'beta']);
    // без rank: Аист, Бесконечность, Вега, Дуб, Ёж (Ё после Д в localeCompare 'ru')
    assert.deepEqual(rows.slice(3).map((r) => r.name), ['Аист', 'Бесконечность', 'Вега', 'Дуб', 'Ёж']);
  });

  test('равный rank: порядок не зависит от порядка ключей в items', () => {
    const d = fixture();
    const reversed = { ...d, items: Object.fromEntries(Object.entries(d.items).reverse()) };
    assert.deepEqual(ids(P.portfolioList(reversed)), ids(P.portfolioList(d)));
  });

  test('фильтр по группе', () => {
    const d = fixture();
    assert.deepEqual(ids(P.portfolioList(d, 'Ленивые портфели')), ['zeta', 'alpha']);
    assert.deepEqual(ids(P.portfolioList(d, 'Тематические')), ['beta', 'gamma']);
    assert.deepEqual(ids(P.portfolioList(d, null)), ['zeta', 'alpha', 'beta', 'gamma']);
    assert.deepEqual(ids(P.portfolioList(d, undefined)), ['zeta', 'alpha', 'beta', 'gamma']);
  });

  test('несуществующая и пустая группа -> []', () => {
    const d = fixture();
    assert.deepEqual(P.portfolioList(d, 'Нет такой'), []);
    assert.deepEqual(P.portfolioList(d, 'Пустая группа'), []);
  });

  test('пустые/некорректные данные -> []', () => {
    assert.deepEqual(P.portfolioList(null), []);
    assert.deepEqual(P.portfolioList({}), []);
    assert.deepEqual(P.portfolioList({ items: {} }), []);
  });

  test('item без row: id есть, остальные поля отсутствуют', () => {
    const rows = P.portfolioList({ items: { x: {} } });
    assert.deepEqual(rows, [{ id: 'x' }]);
  });
});

describe('portfolioById', () => {
  test('портфель с рядами', () => {
    const d = fixture();
    const p = P.portfolioById(d, 'alpha');
    assert.equal(p.id, 'alpha');
    assert.deepEqual(p.row, d.items.alpha.row);
    assert.deepEqual(p.report, { name: 'Альфа' });
    assert.deepEqual(p.series, [['2026-01-01', 100000], ['2026-02-01', 101000]]);
    assert.deepEqual(p.benchmark, [['2026-01-01', 100000], ['2026-02-01', 99000]]);
  });

  test('без series / series_benchmark -> пустые массивы; report null сохраняется', () => {
    const d = fixture();
    const g = P.portfolioById(d, 'gamma');
    assert.deepEqual(g.series, []);
    assert.deepEqual(g.benchmark, []);
    assert.equal(g.report, null);
    const noSeries = fixture();
    delete noSeries.series;
    delete noSeries.series_benchmark;
    const a = P.portfolioById(noSeries, 'alpha');
    assert.deepEqual(a.series, []);
    assert.deepEqual(a.benchmark, []);
  });

  test('report отсутствует -> null; row отсутствует -> {}', () => {
    const p = P.portfolioById({ items: { x: { report: undefined } } }, 'x');
    assert.deepEqual(p, { id: 'x', row: {}, report: null, series: [], benchmark: [] });
  });

  test('несуществующий id -> null', () => {
    const d = fixture();
    assert.equal(P.portfolioById(d, 'nope'), null);
    assert.equal(P.portfolioById(d, ''), null);
    assert.equal(P.portfolioById(d, undefined), null);
    assert.equal(P.portfolioById(null, 'alpha'), null);
  });

  test('id из Object.prototype не считается портфелем', () => {
    const d = fixture();
    assert.equal(P.portfolioById(d, 'toString'), null);
    assert.equal(P.portfolioById(d, '__proto__'), null);
    assert.equal(P.portfolioById(d, 'constructor'), null);
  });
});

describe('groupCounts', () => {
  test('все группы из data.groups, пустая -> 0', () => {
    assert.deepEqual(P.groupCounts(fixture()), {
      all: 4, 'Ленивые портфели': 2, 'Тематические': 2, 'Пустая группа': 0,
    });
  });

  test('группа портфеля, которой нет в data.groups, тоже считается', () => {
    const d = fixture();
    d.items.x = { row: { name: 'Икс', group: 'Новая' } };
    d.items.y = { row: { name: 'Игрек' } };   // без группы — только в all
    assert.deepEqual(P.groupCounts(d), {
      all: 6, 'Ленивые портфели': 2, 'Тематические': 2, 'Пустая группа': 0, 'Новая': 1,
    });
  });

  test('нет groups / нет items', () => {
    assert.deepEqual(P.groupCounts({ items: {} }), { all: 0 });
    assert.deepEqual(P.groupCounts({ groups: ['A'] }), { all: 0, A: 0 });
    assert.deepEqual(P.groupCounts(null), { all: 0 });
  });
});

describe('riskReturnPoints', () => {
  test('окно по умолчанию — 10Y', () => {
    const pts = P.riskReturnPoints(fixture());
    assert.equal(pts.length, 1);
    assert.deepEqual(pts[0], { id: 'alpha', name: 'Альфа', group: 'Ленивые портфели', ret: 0.1, volatility: 0.12,
      from: '2016-11-18', to: '2026-11-18' });
  });

  test('пустое окно, отсутствующее окно, нет risk_return -> []', () => {
    const d = fixture();
    assert.deepEqual(P.riskReturnPoints(d, '1Y'), []);
    assert.deepEqual(P.riskReturnPoints(d, '5Y'), []);
    assert.deepEqual(P.riskReturnPoints({ items: {} }, '10Y'), []);
    assert.deepEqual(P.riskReturnPoints(null), []);
    assert.deepEqual(P.riskReturnPoints({ risk_return: { '10Y': {} } }), [], 'не массив -> []');
  });
});

describe('isStale', () => {
  test('stale — объект {slug: {stale_date, reason}}', () => {
    const d = fixture();
    assert.equal(P.isStale(d, 'alpha'), true);
    assert.equal(P.isStale(d, 'beta'), false);
    assert.equal(P.isStale(d, 'gamma'), false);
  });

  test('items[id].stale === true', () => {
    const d = fixture();
    assert.equal(P.isStale(d, 'zeta'), true);
    d.stale = {};
    assert.equal(P.isStale(d, 'zeta'), true, 'флаг в item работает без записи в data.stale');
    assert.equal(P.isStale(d, 'alpha'), false);
  });

  test('stale — массив slug-ов (старый формат)', () => {
    const d = fixture();
    d.stale = ['beta'];
    assert.equal(P.isStale(d, 'beta'), true);
    assert.equal(P.isStale(d, 'alpha'), false);
    assert.equal(P.isStale(d, 'zeta'), true, 'items[zeta].stale по-прежнему учитывается');
  });

  test('нет stale / null / несуществующий id / имя из прототипа', () => {
    const d = fixture();
    delete d.stale;
    assert.equal(P.isStale(d, 'alpha'), false);
    d.stale = null;
    assert.equal(P.isStale(d, 'alpha'), false);
    assert.equal(P.isStale(fixture(), 'nope'), false);
    assert.equal(P.isStale(fixture(), 'toString'), false);
    assert.equal(P.isStale(null, 'alpha'), false);
  });
});

describe('loadPortfolios', () => {
  test('загрузка по base со слэшем; файл возвращается как есть', async () => {
    const f = fixtureServer();
    const d = await P.loadPortfolios({ base: '/data/', fetchImpl: f });
    assert.deepEqual(d, fixture());
    assert.deepEqual(f.calls, ['/data/portfolios.json']);
  });

  test('base без слэша дополняется', async () => {
    const f = fixtureServer();
    await P.loadPortfolios({ base: 'https://example.invalid/public/data', fetchImpl: f });
    assert.deepEqual(f.calls, ['https://example.invalid/public/data/portfolios.json']);
  });

  test('base по умолчанию — DEFAULT_DATA_BASE', async () => {
    const f = fixtureServer();
    await P.loadPortfolios({ fetchImpl: f });
    assert.deepEqual(f.calls, [`${DEFAULT_DATA_BASE}portfolios.json`]);
  });

  test('повторный вызов берёт ответ из кэша; clearCache сбрасывает', async () => {
    const f = fixtureServer();
    await P.loadPortfolios({ base: '/d/', fetchImpl: f });
    await P.loadPortfolios({ base: '/d/', fetchImpl: f });
    assert.equal(f.calls.length, 1);
    clearCache(f);
    await P.loadPortfolios({ base: '/d/', fetchImpl: f });
    assert.equal(f.calls.length, 2);
  });

  test('404 -> Error с русским сообщением, 2 попытки', async () => {
    const f = routeFetch(() => undefined);
    await assert.rejects(P.loadPortfolios({ base: '/d/', fetchImpl: f }),
      (e) => e instanceof Error && /^Не удалось загрузить данные портфелей \(portfolios\.json\): /.test(e.message)
        && /HTTP 404/.test(e.message));
    assert.equal(f.calls.length, 2);
  });

  test('исключение fetch -> Error; ошибка не кэшируется', async () => {
    let fail = true;
    const f = routeFetch(() => (fail ? new Error('сеть недоступна') : RAW));
    await assert.rejects(P.loadPortfolios({ base: '/d/', fetchImpl: f }),
      /Не удалось загрузить данные портфелей \(portfolios\.json\):.*сеть недоступна/);
    assert.equal(f.calls.length, 2);
    fail = false;
    const d = await P.loadPortfolios({ base: '/d/', fetchImpl: f });
    assert.equal(Object.keys(d.items).length, 4);
  });

  test('первая попытка падает, вторая успешна -> данные', async () => {
    let n = 0;
    const f = routeFetch(() => (n++ === 0 ? new Error('обрыв') : RAW));
    const d = await P.loadPortfolios({ base: '/d/', fetchImpl: f });
    assert.equal(Object.keys(d.items).length, 4);
    assert.equal(f.calls.length, 2);
  });

  for (const [label, body] of [
    ['JSON без items', { groups: [], series: {} }],
    ['items = null', { items: null }],
    ['items — строка', { items: 'x' }],
    ['JSON — null', null],
    ['JSON — число', 42],
  ]) {
    test(`${label} -> Error «повреждён или имеет неожиданный формат»`, async () => {
      const f = fixtureServer(JSON.stringify(body));
      await assert.rejects(P.loadPortfolios({ base: '/d/', fetchImpl: f }),
        /Файл данных портфелей \(portfolios\.json\) повреждён или имеет неожиданный формат/);
    });
  }

  test('битый JSON -> Error загрузки', async () => {
    const f = fixtureServer('{"items": ');
    await assert.rejects(P.loadPortfolios({ base: '/d/', fetchImpl: f }), /Не удалось загрузить данные портфелей/);
  });

  test('пустой items {} — допустим (список пуст, ошибки нет)', async () => {
    const f = fixtureServer(JSON.stringify({ items: {} }));
    const d = await P.loadPortfolios({ base: '/d/', fetchImpl: f });
    assert.deepEqual(P.portfolioList(d), []);
    assert.deepEqual(P.groupCounts(d), { all: 0 });
  });
});
