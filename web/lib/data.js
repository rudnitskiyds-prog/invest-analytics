/**
 * Данные для веб-версии: ISS Московской биржи (напрямую из браузера), ряды Банка России
 * из файлов сборщика public/data/*.json, демо-режим на месячных фикстурах tests/fixtures.
 *
 * Порт core/data/universe.py и core/data/iss.py (только чтение рядов цен).
 * Модуль не трогает DOM/window/localStorage. Все сетевые функции принимают fetchImpl
 * (по умолчанию globalThis.fetch), чтобы тесты в Node подставляли фикстуры.
 *
 * @typedef {{dates: string[], values: number[]}} Series   даты "YYYY-MM-DD" по возрастанию
 * @typedef {{dates: string[], cols: Object<string, number[]>}} Frame  общий календарь, пропуск = NaN
 */

export const ISS_BASE = 'https://iss.moex.com/iss';
export const DEFAULT_DATA_BASE = '../public/data/';
export const DEFAULT_FIXTURES_BASE = '../tests/fixtures/';

/** Каталог базовых активов — как core/data/universe.py: CATALOG (индексы-аналоги — НИР, табл. 3). */
export const CATALOG = [
  { key: 'MCFTR', name: 'Индекс МосБиржи полной доходности брутто', assetClass: 'Акции', source: 'iss', etf: 'EQMX', note: 'Бенчмарк НИР' },
  { key: 'IMOEX', name: 'Индекс МосБиржи (ценовой)', assetClass: 'Акции', source: 'iss', etf: '', note: '' },
  { key: 'MEBCTR', name: 'Индекс голубых фишек полной доходности', assetClass: 'Акции', source: 'iss', etf: 'SBBC', note: '' },
  { key: 'IRDIVTR', name: 'Индекс дивидендных акций полной доходности', assetClass: 'Акции', source: 'iss', etf: 'DIVD', note: '' },
  { key: 'MOEXEU', name: 'Индекс электроэнергетики', assetClass: 'Акции', source: 'iss', etf: 'ОПИФ ГПБ-Электроэнергетика', note: '' },
  { key: 'RGBITR', name: 'Индекс гособлигаций полной доходности', assetClass: 'Облигации гос.', source: 'iss', etf: 'SBGB', note: '' },
  { key: 'RUGBITR1Y', name: 'Гособлигации до 1 года (TR)', assetClass: 'Облигации гос.', source: 'iss', etf: 'SUGB', note: '' },
  { key: 'RUGBITR5+', name: 'Гособлигации 5+ лет (TR)', assetClass: 'Облигации гос.', source: 'iss', etf: 'SBLB', note: '' },
  { key: 'RUGBITR10Y', name: 'Гособлигации 10+ лет (TR)', assetClass: 'Облигации гос.', source: 'iss', etf: 'AMGB', note: '' },
  { key: 'RUCBITR', name: 'Корпоративные облигации (TR)', assetClass: 'Облигации корп.', source: 'iss', etf: 'OBLG', note: '' },
  { key: 'RUCBTRNS', name: 'Корпоративные облигации (TR, новая методика)', assetClass: 'Облигации корп.', source: 'iss', etf: 'OBLG', note: '' },
  { key: 'CORP_CHAIN', name: 'Корп. облигации: RUCBITR → RUCBTRNS (склейка)', assetClass: 'Облигации корп.', source: 'chain', etf: 'OBLG', note: 'Как в НИР: пересчёт базы при смене методики' },
  { key: 'GOLD_CBR', name: 'Золото, учётная цена ЦБ РФ', assetClass: 'Золото', source: 'cbr', etf: 'GOLD', note: '' },
  { key: 'GLDRUB_TOM', name: 'Золото, биржевой (руб./г)', assetClass: 'Золото', source: 'iss', etf: 'GOLD', note: '' },
  { key: 'RUONIA', name: 'Индекс денежного рынка (накопленная RUONIA)', assetClass: 'Денежный рынок', source: 'cbr', etf: 'LQDT', note: '' },
];

/** Индексы каталога (ряд берётся из ISS history рынка index). */
export const INDEX_KEYS = new Set(['MCFTR', 'IMOEX', 'MEBCTR', 'IRDIVTR', 'MOEXEU', 'RGBITR', 'RUGBITR1Y',
  'RUGBITR5+', 'RUGBITR10Y', 'RUCBITR', 'RUCBTRNS']);

/** Склейки: ключ -> [старый, новый, дата переключения] (universe.CHAIN_SWITCH). */
export const CHAIN_SWITCH = { CORP_CHAIN: ['RUCBITR', 'RUCBTRNS', '2018-12-29'] };

/** Файлы сборщика для рядов ЦБ. */
export const CBR_FILES = { GOLD_CBR: 'cbr_gold', RUONIA: 'ruonia_index' };

const DEMO_FILES = ['iss_monthly.csv', 'cbr_monthly.csv', 'iss_monthly_extra.csv'];

// ------------------------------------------------------------------ кэш и сеть
// Кэш ответов в памяти на время сессии; отдельный на каждый fetchImpl, чтобы подмена
// fetch в тестах не получала чужие ответы.
const _caches = new WeakMap();

function cacheFor(fetchImpl) {
  let c = _caches.get(fetchImpl);
  if (!c) { c = new Map(); _caches.set(fetchImpl, c); }
  return c;
}

function resolveFetch(fetchImpl) {
  const f = fetchImpl || globalThis.fetch;
  if (typeof f !== 'function') throw new Error('fetch недоступен: передайте fetchImpl');
  return f;
}

async function getCached(url, fetchImpl, kind = 'json', retries = 3) {
  const f = resolveFetch(fetchImpl);
  const cache = cacheFor(f);
  const key = kind + ' ' + url;
  if (cache.has(key)) return cache.get(key);
  const p = (async () => {
    let lastErr;
    for (let attempt = 0; attempt < retries; attempt++) {
      try {
        const resp = await f(url);
        if (resp && resp.ok === false) throw new Error(`HTTP ${resp.status}`);
        return kind === 'json' ? await resp.json() : await resp.text();
      } catch (e) {
        lastErr = e;
        if (attempt < retries - 1) await new Promise((r) => setTimeout(r, 500 * (attempt + 1)));
      }
    }
    throw new Error(`Источник недоступен: ${url} (${lastErr && lastErr.message})`);
  })();
  cache.set(key, p);
  p.catch(() => cache.delete(key));   // ошибки не кэшируем
  return p;
}

/** Очистить кэш ответов (для тестов). */
export function clearCache(fetchImpl) {
  _caches.delete(resolveFetch(fetchImpl));
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function qs(params) {
  return Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
}

function block(data, name) {
  const b = data && data[name];
  if (!b || !b.columns || !b.data) return [];
  return b.data.map((row) => Object.fromEntries(b.columns.map((c, i) => [c, row[i]])));
}

/** Пары [дата, значение] -> Series: сортировка, дубли — последний, только конечные > 0. */
function pairsToSeries(pairs, { positive = true } = {}) {
  const m = new Map();
  for (const [d, v] of pairs) {
    if (d == null || v == null) continue;
    const x = Number(v);
    if (!Number.isFinite(x) || (positive && !(x > 0))) continue;
    m.set(String(d).slice(0, 10), x);
  }
  const dates = [...m.keys()].sort();
  return { dates, values: dates.map((d) => m.get(d)) };
}

// ------------------------------------------------------------------ ISS
/**
 * Значения индекса Мосбиржи из /history (iss.index_history): свечи многих индексов начинаются
 * лишь с 2016 г., история — полная. Пагинация по блоку history.cursor (INDEX, TOTAL, PAGESIZE).
 * @returns {Promise<Series>}
 */
export async function fetchIssIndex(secid, from = '2000-01-01', till = null, { fetchImpl } = {}) {
  const pairs = [];
  let start = 0;
  for (let guard = 0; guard < 10000; guard++) {
    const url = `${ISS_BASE}/history/engines/stock/markets/index/securities/${encodeURIComponent(secid)}.json?` +
      qs({ 'iss.meta': 'off', from: from || '2000-01-01', till: till || todayIso(),
        'history.columns': 'TRADEDATE,CLOSE', start });
    const data = await getCached(url, fetchImpl);
    const rows = block(data, 'history');
    if (!rows.length) break;
    for (const r of rows) pairs.push([r.TRADEDATE, r.CLOSE]);
    const cur = block(data, 'history.cursor')[0];
    if (cur) {
      const idx = Number(cur.INDEX), total = Number(cur.TOTAL), size = Number(cur.PAGESIZE);
      if (idx + size >= total) break;
      start = idx + size;
    } else {
      if (rows.length < 100) break;
      start += rows.length;
    }
  }
  return pairsToSeries(pairs);
}

/** Основной режим торгов бумаги: строка boards с is_primary=1 (иначе первая). */
export async function resolveSecurity(secid, { fetchImpl } = {}) {
  const url = `${ISS_BASE}/securities/${encodeURIComponent(secid)}.json?` +
    qs({ 'iss.meta': 'off', 'iss.only': 'boards' });
  const boards = block(await getCached(url, fetchImpl), 'boards');
  if (!boards.length) throw new Error(`Инструмент ${secid} не найден на ISS`);
  const row = boards.find((b) => Number(b.is_primary) === 1) || boards[0];
  return { secid, engine: row.engine, market: row.market, board: row.boardid };
}

/**
 * Цены закрытия бумаги (акция, пай, металл) — дневные свечи основного режима.
 * Свечи отдаются страницами по 500 строк без cursor. Индексы перенаправляются в fetchIssIndex.
 * Ограничение: в отличие от iss.close_series, исторические режимы (EQBR до 2013 г.) не подклеиваются.
 * @returns {Promise<Series>}
 */
export async function fetchIssSecurity(secid, from = '2000-01-01', till = null, { fetchImpl } = {}) {
  const info = await resolveSecurity(secid, { fetchImpl });
  if (info.market === 'index') return fetchIssIndex(secid, from, till, { fetchImpl });
  const pairs = [];
  let start = 0;
  for (let guard = 0; guard < 10000; guard++) {
    const url = `${ISS_BASE}/engines/${info.engine}/markets/${info.market}/boards/${info.board}` +
      `/securities/${encodeURIComponent(secid)}/candles.json?` +
      qs({ 'iss.meta': 'off', interval: 24, from: from || '2000-01-01', till: till || todayIso(), start });
    const rows = block(await getCached(url, fetchImpl), 'candles');
    if (!rows.length) break;
    for (const r of rows) pairs.push([String(r.begin).slice(0, 10), r.close]);
    if (rows.length < 500) break;
    start += rows.length;
  }
  return pairsToSeries(pairs);
}

// ------------------------------------------------------------------ Банк России
/**
 * Файл сборщика public/data/{id}.json -> {meta, series}.
 * id: cbr_gold | ruonia_index | ruonia_rate | key_rate.
 */
export async function loadCbrFile(id, { base = DEFAULT_DATA_BASE, fetchImpl } = {}) {
  const b = base.endsWith('/') ? base : base + '/';
  const json = await getCached(`${b}${id}.json`, fetchImpl);
  const { data, ...meta } = json;
  return { meta, series: pairsToSeries(data || [], { positive: false }) };
}

// ------------------------------------------------------------------ ряды
/** Ряд без NaN. */
export function dropNaN(s) {
  const dates = [], values = [];
  for (let i = 0; i < s.dates.length; i++) {
    const v = s.values[i];
    if (v !== null && v !== undefined && !Number.isNaN(v)) { dates.push(s.dates[i]); values.push(v); }
  }
  return { dates, values };
}

/** Срез ряда по датам [from, till] включительно (строки ISO сравниваются лексикографически). */
export function sliceSeries(s, from, till) {
  const dates = [], values = [];
  for (let i = 0; i < s.dates.length; i++) {
    const d = s.dates[i];
    if ((from && d < from) || (till && d > till)) continue;
    dates.push(d); values.push(s.values[i]);
  }
  return { dates, values };
}

/**
 * Склейка двух индексов (universe.chain): до даты — старый, с первой даты нового ≥ switchDate —
 * новый, пересчитанный к уровню старого. Повторяет Python буквально: из старого ряда берутся
 * точки строго до последней даты старого ≤ якоря (old.loc[:anchor].iloc[:-1]).
 */
export function chain(oldS, newS, switchDate) {
  const o = dropNaN(oldS), n = dropNaN(newS);
  if (!n.dates.length) return o;
  let a = n.dates.findIndex((d) => d >= switchDate);
  if (a < 0) a = 0;
  const anchor = n.dates[a];
  let k = -1;                                     // последняя дата старого ≤ якоря
  for (let i = 0; i < o.dates.length && o.dates[i] <= anchor; i++) k = i;
  if (k < 0) return { dates: n.dates.slice(a), values: n.values.slice(a) };
  const scale = o.values[k] / n.values[a];
  return {
    dates: o.dates.slice(0, k).concat(n.dates.slice(a)),
    values: o.values.slice(0, k).concat(n.values.slice(a).map((v) => v * scale)),
  };
}

function isSeries(x) {
  return x && Array.isArray(x.values) && Array.isArray(x.dates);
}

/**
 * Выравнивание рядов на объединённый календарь с ffill (universe.load_prices).
 * mode='common' — дополнительно удаляются строки, где хоть один ряд ещё не начался
 * (обрезка по самой поздней дате начала); иначе ('union') пропуски = NaN.
 * @param {Object<string, Series>} seriesByKey
 * @returns {Frame}
 */
export function alignFrame(seriesByKey, { mode = 'common' } = {}) {
  const keys = Object.keys(seriesByKey);
  const all = new Set();
  for (const k of keys) for (const d of seriesByKey[k].dates) all.add(d);
  const dates = [...all].sort();
  const pos = new Map(dates.map((d, i) => [d, i]));
  const cols = {};
  for (const k of keys) {
    const s = seriesByKey[k];
    const col = new Array(dates.length).fill(NaN);
    for (let i = 0; i < s.dates.length; i++) {
      const v = s.values[i];
      if (v !== null && v !== undefined && !Number.isNaN(v)) col[pos.get(s.dates[i])] = v;
    }
    for (let i = 1; i < col.length; i++) if (Number.isNaN(col[i])) col[i] = col[i - 1];
    cols[k] = col;
  }
  const frame = { dates, cols };
  return mode === 'common' ? dropNaNRows(frame, 'any') : frame;
}

/** Удаление строк фрейма: how='any' — есть хоть один NaN, 'all' — все NaN. */
export function dropNaNRows(frame, how = 'any') {
  const keys = Object.keys(frame.cols);
  const keep = [];
  for (let i = 0; i < frame.dates.length; i++) {
    let nan = 0;
    for (const k of keys) if (Number.isNaN(frame.cols[k][i])) nan++;
    const drop = how === 'any' ? nan > 0 : (keys.length > 0 && nan === keys.length);
    if (!drop) keep.push(i);
  }
  const cols = {};
  for (const k of keys) cols[k] = keep.map((i) => frame.cols[k][i]);
  return { dates: keep.map((i) => frame.dates[i]), cols };
}

/** Протяжка пропусков вперёд по каждому столбцу (DataFrame.ffill). */
export function ffillFrame(frame) {
  const cols = {};
  for (const [k, c] of Object.entries(frame.cols)) {
    const x = c.slice();
    for (let i = 1; i < x.length; i++) if (Number.isNaN(x[i])) x[i] = x[i - 1];
    cols[k] = x;
  }
  return { dates: frame.dates.slice(), cols };
}

// ------------------------------------------------------------------ ресемплинг
const pad = (x) => String(x).padStart(2, '0');
const DAY = 86400000;

function lastDayOfMonth(y, m) {           // m — 1..12
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** Метка периода как в pandas resample: W-FRI, ME, QE, YE (календарный конец периода). */
export function periodLabel(date, freq) {
  const y = Number(date.slice(0, 4)), m = Number(date.slice(5, 7));
  if (freq === 'M') return `${y}-${pad(m)}-${pad(lastDayOfMonth(y, m))}`;
  if (freq === 'Q') { const qm = Math.ceil(m / 3) * 3; return `${y}-${pad(qm)}-${pad(lastDayOfMonth(y, qm))}`; }
  if (freq === 'A') return `${y}-12-31`;
  if (freq === 'W') {
    const t = Date.parse(date + 'T00:00:00Z');
    const wd = (new Date(t).getUTCDay() + 6) % 7;             // пн=0 … вс=6
    return new Date(t + ((4 - wd + 7) % 7) * DAY).toISOString().slice(0, 10);
  }
  throw new Error(`Неизвестная частота: ${freq}`);
}

/**
 * Цены на конец периода (metrics.resample_prices): 'D' — без изменений; 'W','M','Q','A' —
 * последнее непустое значение периода по каждому столбцу, метка — календарный конец периода
 * (как pandas .resample('W-FRI'|'ME'|'QE'|'YE').last()), полностью пустые периоды удаляются.
 * @template {Series|Frame} T
 * @param {T} x
 * @returns {T}
 */
export function resample(x, freq) {
  if (freq === 'D') return x;
  if (isSeries(x)) {
    const f = resample({ dates: x.dates, cols: { v: x.values } }, freq);
    return { dates: f.dates, values: f.cols.v };
  }
  const keys = Object.keys(x.cols);
  const labels = [], groups = [];
  for (let i = 0; i < x.dates.length; i++) {
    const l = periodLabel(x.dates[i], freq);
    if (!labels.length || labels[labels.length - 1] !== l) { labels.push(l); groups.push([]); }
    groups[groups.length - 1].push(i);
  }
  const cols = {};
  for (const k of keys) {
    const c = x.cols[k];
    cols[k] = groups.map((g) => {
      for (let j = g.length - 1; j >= 0; j--) if (!Number.isNaN(c[g[j]]) && c[g[j]] != null) return c[g[j]];
      return NaN;
    });
  }
  return dropNaNRows({ dates: labels, cols }, 'all');
}

/** Срез фрейма по датам [from, till] включительно. */
export function sliceFrame(frame, from, till) {
  const idx = [];
  for (let i = 0; i < frame.dates.length; i++) {
    const d = frame.dates[i];
    if ((from && d < from) || (till && d > till)) continue;
    idx.push(i);
  }
  const cols = {};
  for (const [k, c] of Object.entries(frame.cols)) cols[k] = idx.map((i) => c[i]);
  return { dates: idx.map((i) => frame.dates[i]), cols };
}

/** Столбец фрейма как ряд без пропусков. */
export function frameColumn(frame, key) {
  if (!(key in frame.cols)) throw new Error(`${key}: нет в данных`);
  return dropNaN({ dates: frame.dates, values: frame.cols[key] });
}

// ------------------------------------------------------------------ демо
function parseCsv(text) {
  const lines = text.replace(/\r/g, '').split('\n').filter((l) => l.trim() !== '');
  const header = lines[0].split(',');
  const rows = lines.slice(1).map((l) => l.split(','));
  return { header, rows };
}

/** YYYYMM -> последний день месяца YYYY-MM-DD. */
function monthEnd(m) {
  const y = Number(m.slice(0, 4)), mm = Number(m.slice(4, 6));
  return `${y}-${pad(mm)}-${pad(lastDayOfMonth(y, mm))}`;
}

/**
 * Демо-фрейм из месячных фикстур (universe._offline_frame / reproduce_nir.load_fixture):
 * iss_monthly.csv ⋈ cbr_monthly.csv ⋈ iss_monthly_extra.csv по столбцу m (inner join),
 * дата — последний день месяца, плюс CORP_CHAIN = chain(RUCBITR, RUCBTRNS, '2018-12-29').
 * @returns {Promise<Frame>}
 */
export async function loadDemoFrame({ base = DEFAULT_FIXTURES_BASE, fetchImpl } = {}) {
  const b = base.endsWith('/') ? base : base + '/';
  const parts = await Promise.all(DEMO_FILES.map((f) => getCached(b + f, fetchImpl, 'text')));
  let months = null;
  const colsByMonth = {};                          // key -> Map(m -> value)
  for (const text of parts) {
    const { header, rows } = parseCsv(text);
    const mi = header.indexOf('m');
    const ms = rows.map((r) => r[mi].trim());
    months = months === null ? ms : months.filter((m) => ms.includes(m));
    header.forEach((h, j) => {
      if (j === mi) return;
      const mp = new Map();
      rows.forEach((r) => {
        const v = r[j] === undefined ? '' : r[j].trim();
        mp.set(r[mi].trim(), v === '' ? NaN : Number(v));
      });
      colsByMonth[h.trim()] = mp;
    });
  }
  const dates = months.map(monthEnd);
  const cols = {};
  for (const [k, mp] of Object.entries(colsByMonth)) cols[k] = months.map((m) => mp.get(m));
  const [o, n, sw] = CHAIN_SWITCH.CORP_CHAIN;
  if (cols[o] && cols[n]) {
    const ch = chain({ dates, values: cols[o] }, { dates, values: cols[n] }, sw);
    const mp = new Map(ch.dates.map((d, i) => [d, ch.values[i]]));
    cols.CORP_CHAIN = dates.map((d) => (mp.has(d) ? mp.get(d) : NaN));
  }
  return { dates, cols };
}

// ------------------------------------------------------------------ единая точка входа
/**
 * Ряд актива каталога или любого тикера ISS (universe.load_series).
 * GOLD_CBR -> cbr_gold, RUONIA -> ruonia_index, CORP_CHAIN -> chain(RUCBITR, RUCBTRNS, '2018-12-29'),
 * индексы каталога -> fetchIssIndex, прочее -> fetchIssSecurity.
 * demo: true (или {base}) — ряд из демо-фрейма (месячные фикстуры).
 * @returns {Promise<Series>}
 */
export async function loadAsset(key, from = '2008-01-01', till = null,
  { fetchImpl, dataBase = DEFAULT_DATA_BASE, demo = false } = {}) {
  if (demo) {
    const base = typeof demo === 'object' && demo.base ? demo.base : DEFAULT_FIXTURES_BASE;
    const df = await loadDemoFrame({ base, fetchImpl });
    if (!(key in df.cols)) throw new Error(`${key}: нет в демо-данных`);
    return sliceSeries(frameColumn(df, key), from, till);
  }
  if (CBR_FILES[key]) {
    const { series } = await loadCbrFile(CBR_FILES[key], { base: dataBase, fetchImpl });
    return sliceSeries(series, from, till);
  }
  if (CHAIN_SWITCH[key]) {
    const [o, n, sw] = CHAIN_SWITCH[key];
    const [so, sn] = await Promise.all([fetchIssIndex(o, from, till, { fetchImpl }),
      fetchIssIndex(n, from, till, { fetchImpl })]);
    return chain(so, sn, sw);
  }
  if (INDEX_KEYS.has(key)) return fetchIssIndex(key, from, till, { fetchImpl });
  return fetchIssSecurity(key, from, till, { fetchImpl });
}
