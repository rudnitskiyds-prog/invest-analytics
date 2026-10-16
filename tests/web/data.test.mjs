// Данные: моки ISS / файлов ЦБ (без сети), склейка, выравнивание, ресемплинг против pandas.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as D from '../../web/lib/data.js';
import { reference, close, closeArr, fixtureFetch, routeFetch, frameFromRef, seriesFromRef, response } from './_helpers.mjs';

const qp = (url, k) => new URL(url).searchParams.get(k);

/** Ряд дат YYYY-MM-DD подряд по дням с 2015-01-01. */
function days(n, start = '2015-01-01') {
  const t0 = Date.parse(start + 'T00:00:00Z');
  return Array.from({ length: n }, (_, i) => new Date(t0 + i * 86400000).toISOString().slice(0, 10));
}

describe('ISS: индексы (history)', () => {
  test('пагинация по history.cursor: 5 строк страницами по 2', async () => {
    const rows = [['2020-01-03', 10], ['2020-01-06', 11], ['2020-01-08', 12], ['2020-01-09', 13], ['2020-01-10', 14]];
    const f = routeFetch((url) => {
      assert.match(url, /^https:\/\/iss\.moex\.com\/iss\/history\/engines\/stock\/markets\/index\/securities\/MCFTR\.json\?/);
      assert.equal(qp(url, 'iss.meta'), 'off');
      assert.equal(qp(url, 'history.columns'), 'TRADEDATE,CLOSE');
      assert.equal(qp(url, 'from'), '2020-01-01');
      assert.equal(qp(url, 'till'), '2020-12-31');
      const start = Number(qp(url, 'start'));
      return {
        history: { columns: ['TRADEDATE', 'CLOSE'], data: rows.slice(start, start + 2) },
        'history.cursor': { columns: ['INDEX', 'TOTAL', 'PAGESIZE'], data: [[start, 5, 2]] },
      };
    });
    const s = await D.fetchIssIndex('MCFTR', '2020-01-01', '2020-12-31', { fetchImpl: f });
    assert.deepEqual(f.calls.map((u) => qp(u, 'start')), ['0', '2', '4']);
    assert.deepEqual(s.dates, rows.map((r) => r[0]));
    assert.deepEqual(s.values, rows.map((r) => r[1]));
  });

  test("'+' в тикере кодируется: RUGBITR5+ → RUGBITR5%2B", async () => {
    const f = routeFetch(() => ({ history: { columns: ['TRADEDATE', 'CLOSE'], data: [['2020-01-03', 1]] } }));
    await D.fetchIssIndex('RUGBITR5+', '2020-01-01', '2020-02-01', { fetchImpl: f });
    assert.match(f.calls[0], /\/securities\/RUGBITR5%2B\.json\?/);
    assert.ok(!f.calls[0].includes('RUGBITR5+'), f.calls[0]);
  });

  test('дубли дат — последнее значение, нули/пустые CLOSE отбрасываются, сортировка', async () => {
    const f = routeFetch(() => ({ history: { columns: ['TRADEDATE', 'CLOSE'],
      data: [['2020-01-06', 2], ['2020-01-03', 1], ['2020-01-06', 3], ['2020-01-07', null], ['2020-01-08', 0]] } }));
    const s = await D.fetchIssIndex('IMOEX', '2020-01-01', '2020-02-01', { fetchImpl: f });
    assert.deepEqual(s, { dates: ['2020-01-03', '2020-01-06'], values: [1, 3] });
  });

  test('пустой ответ → пустой ряд', async () => {
    const f = routeFetch(() => ({ history: { columns: ['TRADEDATE', 'CLOSE'], data: [] } }));
    assert.deepEqual(await D.fetchIssIndex('MCFTR', '2020-01-01', '2020-02-01', { fetchImpl: f }), { dates: [], values: [] });
  });
});

describe('ISS: бумаги (candles)', () => {
  const BOARDS = {
    boards: {
      columns: ['secid', 'boardid', 'market', 'engine', 'is_primary'],
      data: [['SBER', 'SMAL', 'shares', 'stock', 0], ['SBER', 'TQBR', 'shares', 'stock', 1], ['SBER', 'SPEQ', 'shares', 'stock', 0]],
    },
  };

  test('выбор режима is_primary = 1 и свечи страницами по 500 (1003 строки → 3 запроса)', async () => {
    const all = days(1003).map((d, i) => [`${d} 00:00:00`, 100 + i]);
    const f = routeFetch((url) => {
      if (url.includes('/iss/securities/SBER.json')) {
        assert.equal(qp(url, 'iss.only'), 'boards');
        return BOARDS;
      }
      assert.match(url, /\/iss\/engines\/stock\/markets\/shares\/boards\/TQBR\/securities\/SBER\/candles\.json\?/);
      assert.equal(qp(url, 'interval'), '24');
      const start = Number(qp(url, 'start'));
      return { candles: { columns: ['open', 'close', 'begin'], data: all.slice(start, start + 500).map(([b, c]) => [0, c, b]) } };
    });
    const s = await D.fetchIssSecurity('SBER', '2015-01-01', '2018-01-01', { fetchImpl: f });
    const starts = f.calls.filter((u) => u.includes('candles')).map((u) => qp(u, 'start'));
    assert.deepEqual(starts, ['0', '500', '1000']);
    assert.equal(s.dates.length, 1003);
    assert.equal(s.dates[0], '2015-01-01');
    assert.equal(s.values.at(-1), 1102);
  });

  test('ровно 500 строк: следующий запрос возвращает пусто и цикл останавливается', async () => {
    const all = days(500).map((d, i) => [`${d} 00:00:00`, 1 + i]);
    const f = routeFetch((url) => {
      if (url.includes('iss.only=boards')) return BOARDS;
      const start = Number(qp(url, 'start'));
      return { candles: { columns: ['close', 'begin'], data: all.slice(start, start + 500).map(([b, c]) => [c, b]) } };
    });
    const s = await D.fetchIssSecurity('SBER', '2015-01-01', '2018-01-01', { fetchImpl: f });
    assert.equal(s.dates.length, 500);
    assert.equal(f.calls.filter((u) => u.includes('candles')).length, 2);
  });

  test('без is_primary — берётся первая строка boards', async () => {
    const f = routeFetch((url) => (url.includes('iss.only=boards')
      ? { boards: { columns: ['boardid', 'market', 'engine', 'is_primary'], data: [['CETS', 'selt', 'currency', 0]] } }
      : { candles: { columns: ['close', 'begin'], data: [[5000, '2024-01-03 00:00:00']] } }));
    const info = await D.resolveSecurity('GLDRUB_TOM', { fetchImpl: f });
    assert.deepEqual(info, { secid: 'GLDRUB_TOM', engine: 'currency', market: 'selt', board: 'CETS' });
  });

  test('инструмент не найден → понятная ошибка', async () => {
    const f = routeFetch(() => ({ boards: { columns: ['boardid', 'market', 'engine', 'is_primary'], data: [] } }));
    await assert.rejects(D.fetchIssSecurity('NOSUCH', '2020-01-01', '2020-02-01', { fetchImpl: f }), /NOSUCH не найден/);
  });

  test('индекс через fetchIssSecurity перенаправляется в history', async () => {
    const f = routeFetch((url) => (url.includes('iss.only=boards')
      ? { boards: { columns: ['boardid', 'market', 'engine', 'is_primary'], data: [['SNDX', 'index', 'stock', 1]] } }
      : { history: { columns: ['TRADEDATE', 'CLOSE'], data: [['2020-01-03', 7]] } }));
    const s = await D.fetchIssSecurity('MOEXOG', '2020-01-01', '2020-02-01', { fetchImpl: f });
    assert.deepEqual(s.values, [7]);
    assert.ok(f.calls.some((u) => u.includes('/history/engines/stock/markets/index/securities/MOEXOG.json')));
  });
});

describe('недоступный источник', () => {
  test('сеть падает → 3 попытки и ошибка «Источник недоступен», ошибка не кэшируется', async () => {
    let n = 0, fail = true;
    const f = async () => {
      n++;
      if (fail) throw new TypeError('Failed to fetch');
      return response({ history: { columns: ['TRADEDATE', 'CLOSE'], data: [['2020-01-03', 1]] } });
    };
    await assert.rejects(D.fetchIssIndex('MCFTR', '2020-01-01', '2020-02-01', { fetchImpl: f }), /Источник недоступен.*Failed to fetch/);
    assert.equal(n, 3);
    fail = false;
    const s = await D.fetchIssIndex('MCFTR', '2020-01-01', '2020-02-01', { fetchImpl: f });
    assert.deepEqual(s.values, [1]);
  });

  test('HTTP 500 → ошибка с кодом', async () => {
    const f = routeFetch(() => ({ __status: 500 }));
    await assert.rejects(D.loadCbrFile('cbr_gold', { base: 'x/', fetchImpl: f }), /HTTP 500/);
  });

  test('кэш: повторный запрос того же URL не идёт в fetch', async () => {
    const f = fixtureFetch();
    await D.loadDemoFrame({ base: '../tests/fixtures/', fetchImpl: f });
    await D.loadDemoFrame({ base: '../tests/fixtures/', fetchImpl: f });
    assert.equal(f.calls.length, 3);
  });
});

describe('Банк России: файлы сборщика', () => {
  const FILE = { id: 'cbr_gold', title: 'Золото', unit: 'руб./г', source: 'ЦБ', updated: '2026-10-06',
    first: '2020-01-09', last: '2020-01-10', data: [['2020-01-10', 3100.5], ['2020-01-09', 3050]] };

  test('loadCbrFile: meta без data, ряд отсортирован, базовый путь дополняется «/»', async () => {
    const f = routeFetch((url) => (url === '../public/data/cbr_gold.json' ? FILE : undefined));
    const { meta, series } = await D.loadCbrFile('cbr_gold', { base: '../public/data', fetchImpl: f });
    assert.equal(meta.unit, 'руб./г');
    assert.ok(!('data' in meta));
    assert.deepEqual(series, { dates: ['2020-01-09', '2020-01-10'], values: [3050, 3100.5] });
  });

  test('loadAsset: GOLD_CBR → cbr_gold, RUONIA → ruonia_index, срез по датам', async () => {
    const f = routeFetch((url) => {
      if (url === 'DB/cbr_gold.json') return FILE;
      if (url === 'DB/ruonia_index.json') return { ...FILE, data: [['2019-12-31', 1], ['2020-01-09', 1.001]] };
      return undefined;
    });
    const g = await D.loadAsset('GOLD_CBR', '2020-01-10', null, { fetchImpl: f, dataBase: 'DB/' });
    assert.deepEqual(g, { dates: ['2020-01-10'], values: [3100.5] });
    const r = await D.loadAsset('RUONIA', '2019-01-01', '2020-12-31', { fetchImpl: f, dataBase: 'DB/' });
    assert.deepEqual(r.values, [1, 1.001]);
  });

  test('loadAsset: CORP_CHAIN = chain(RUCBITR, RUCBTRNS, 2018-12-29) из ISS history', async () => {
    const f = routeFetch((url) => {
      const data = url.includes('/RUCBITR.json')
        ? [['2018-12-27', 400], ['2018-12-28', 410], ['2019-01-03', 411]]
        : [['2018-12-28', 100], ['2019-01-03', 101], ['2019-01-04', 102]];
      return { history: { columns: ['TRADEDATE', 'CLOSE'], data } };
    });
    const s = await D.loadAsset('CORP_CHAIN', '2018-01-01', '2019-02-01', { fetchImpl: f });
    // якорь — 2019-01-03; масштаб 411/101; старый ряд берётся до последней даты ≤ якоря, без неё
    assert.deepEqual(s.dates, ['2018-12-27', '2018-12-28', '2019-01-03', '2019-01-04']);
    closeArr(s.values, [400, 410, 411, 102 * 411 / 101], 1e-12, 'chain');
  });

  test('loadAsset: прочий тикер → fetchIssSecurity, индекс каталога → history', async () => {
    const f = routeFetch((url) => {
      if (url.includes('iss.only=boards')) return { boards: { columns: ['boardid', 'market', 'engine', 'is_primary'], data: [['TQTF', 'shares', 'stock', 1]] } };
      if (url.includes('candles')) return { candles: { columns: ['close', 'begin'], data: [[1.5, '2024-01-03 00:00:00']] } };
      return { history: { columns: ['TRADEDATE', 'CLOSE'], data: [['2024-01-03', 9]] } };
    });
    assert.deepEqual((await D.loadAsset('EQMX', '2024-01-01', '2024-02-01', { fetchImpl: f })).values, [1.5]);
    assert.deepEqual((await D.loadAsset('RUGBITR10Y', '2024-01-01', '2024-02-01', { fetchImpl: f })).values, [9]);
  });
});

describe('демо-фрейм', () => {
  test('loadDemoFrame совпадает с reproduce_nir.load_fixture (все столбцы, NaN, CORP_CHAIN)', async () => {
    const ref = frameFromRef(reference().demo_frame);
    const f = fixtureFetch();
    const df = await D.loadDemoFrame({ base: '../tests/fixtures', fetchImpl: f });
    assert.deepEqual(df.dates, ref.dates);
    assert.deepEqual(Object.keys(df.cols).sort(), Object.keys(ref.cols).sort());
    for (const k of Object.keys(ref.cols)) closeArr(df.cols[k], ref.cols[k].map((v) => (Number.isNaN(v) ? null : v)), 1e-12, k);
  });

  test('loadAsset(demo): ряд без NaN, срез по датам', async () => {
    const f = fixtureFetch();
    const s = await D.loadAsset('IRDIVTR', '2022-01-01', '2022-12-31', { fetchImpl: f, demo: true });
    assert.equal(s.dates.length, 12);
    assert.ok(s.values.every(Number.isFinite));
    await assert.rejects(D.loadAsset('SBER', null, null, { fetchImpl: f, demo: true }), /нет в демо/);
  });

  test('нет фикстуры (404) → ошибка, а не пустой фрейм', async () => {
    const f = routeFetch(() => undefined);
    await assert.rejects(D.loadDemoFrame({ base: 'nowhere/', fetchImpl: f }), /HTTP 404/);
  });
});

describe('chain', () => {
  for (const c of reference().chain) {
    test(`паритет с universe.chain: ${c.name}`, () => {
      const got = D.chain(seriesFromRef(c.old), seriesFromRef(c.new), c.switch);
      assert.deepEqual(got.dates, c.result.dates);
      closeArr(got.values, c.result.values, 1e-12, c.name);
    });
  }
  test('пустой новый ряд → старый', () => {
    const o = { dates: ['2020-01-01'], values: [1] };
    assert.deepEqual(D.chain(o, { dates: [], values: [] }, '2020-01-01'), o);
  });
});

describe('alignFrame / sliceFrame', () => {
  const A = { dates: ['2020-01-01', '2020-01-03', '2020-01-05'], values: [1, 3, 5] };
  const B = { dates: ['2020-01-02', '2020-01-03', '2020-01-04'], values: [20, 30, 40] };

  test("common: объединённый календарь, ffill, обрезка до даты, когда есть все ряды", () => {
    const f = D.alignFrame({ A, B });
    assert.deepEqual(f.dates, ['2020-01-02', '2020-01-03', '2020-01-04', '2020-01-05']);
    assert.deepEqual(f.cols.A, [1, 3, 3, 5]);
    assert.deepEqual(f.cols.B, [20, 30, 40, 40]);
  });
  test("union: ведущие пропуски — NaN", () => {
    const f = D.alignFrame({ A, B }, { mode: 'union' });
    assert.equal(f.dates.length, 5);
    assert.ok(Number.isNaN(f.cols.B[0]));
    assert.deepEqual(f.cols.A, [1, 1, 3, 3, 5]);
  });
  test('один актив и пустой ряд', () => {
    assert.deepEqual(D.alignFrame({ A }), { dates: A.dates, cols: { A: A.values } });
    const f = D.alignFrame({ A, E: { dates: [], values: [] } });
    assert.deepEqual(f.dates, [], 'common с пустым рядом — пустой фрейм');
  });
  test('sliceFrame включительно с обеих сторон', () => {
    const f = D.sliceFrame(D.alignFrame({ A, B }), '2020-01-03', '2020-01-04');
    assert.deepEqual(f.dates, ['2020-01-03', '2020-01-04']);
  });
});

describe('resample против pandas (W-FRI, ME, QE, YE)', () => {
  const R = reference().resample;
  const input = frameFromRef(R.input);
  for (const freq of ['W', 'M', 'Q', 'A']) {
    test(`Frame ${freq}`, () => {
      const got = D.resample(input, freq);
      assert.deepEqual(got.dates, R[freq].dates);
      for (const k of Object.keys(R[freq].cols)) closeArr(got.cols[k], R[freq].cols[k], 0, `${freq}.${k}`);
    });
  }
  test('Series M', () => {
    const s = { dates: input.dates, values: input.cols.A };
    const got = D.resample(s, 'M');
    assert.deepEqual(got.dates, R.series_A_M.dates);
    closeArr(got.values, R.series_A_M.values, 0, 'A.M');
  });
  test("'D' — без изменений; неизвестная частота — ошибка", () => {
    assert.equal(D.resample(input, 'D'), input);
    assert.throws(() => D.resample(input, 'X'), /Неизвестная частота/);
  });
  test('метки недели: суббота/воскресенье → следующая пятница', () => {
    assert.equal(D.periodLabel('2021-05-07', 'W'), '2021-05-07');
    assert.equal(D.periodLabel('2021-05-08', 'W'), '2021-05-14');
    assert.equal(D.periodLabel('2021-05-09', 'W'), '2021-05-14');
    assert.equal(D.periodLabel('2024-02-10', 'M'), '2024-02-29');
    assert.equal(D.periodLabel('2024-11-01', 'Q'), '2024-12-31');
  });
});

describe('каталог', () => {
  test('ключи как в core/data/universe.py', () => {
    assert.deepEqual(D.CATALOG.map((a) => a.key), ['MCFTR', 'IMOEX', 'MEBCTR', 'IRDIVTR', 'MOEXEU', 'RGBITR',
      'RUGBITR1Y', 'RUGBITR5+', 'RUGBITR10Y', 'RUCBITR', 'RUCBTRNS', 'CORP_CHAIN', 'GOLD_CBR', 'GLDRUB_TOM', 'RUONIA']);
  });
});
