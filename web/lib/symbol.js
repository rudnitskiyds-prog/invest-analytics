/**
 * Данные страницы бумаги `#/symbol/{TICKER}` (контракт — web/CONTRACT.md, «Этап 1»).
 *
 * Источники: ISS Мосбиржи (из браузера, CORS разрешён) — справочник, режимы торгов, текущие котировки,
 * дневные свечи / history индексов, купоны (bondization); файлы сборщика public/data/ —
 * дивиденды (T-Invest API), справочник фондов (RusETFs), ночные рейтинги (symbol_stats.json).
 *
 * Модуль не трогает DOM. Сетевые функции принимают fetchImpl (тесты в Node), кэш ответов — общий
 * с data.js. Код терпим к отсутствию полей ISS: нет поля — null, а не исключение.
 * Доли — в долях (0.123 = 12,3 %), кроме полей с суффиксом Percent/_pct (как в источнике, в %).
 */
import { ISS_BASE, DEFAULT_DATA_BASE, getCached, qs, block, todayIso, fetchBoards, fetchHistoryPages,
  mapLimit, ISS_PARALLEL } from './data.js';
import { percentile, adjustSplits, splitFactors } from './metrics.js';

export { adjustSplits, splitFactors };

export const SYMBOL_CLASSES = { share: 'Акция', fund: 'Фонд', bond: 'Облигация', index: 'Индекс', metal: 'Металл', currency: 'Валюта' };

/** Бенчмарк по умолчанию для класса бумаги (ключи каталога data.js). */
export const DEFAULT_BENCHMARKS = { share: 'MCFTR', fund: 'MCFTR', bond_ofz: 'RGBITR', bond: 'RUCBTRNS', index: 'IMOEX', metal: 'GOLD_CBR' };

const METAL_IDS = new Set(['GLDRUB_TOM', 'SLVRUB_TOM', 'PLDRUB_TOM', 'PLTRUB_TOM']);
const RUB = new Set(['RUB', 'SUR', 'RUR']);

// ------------------------------------------------------------------ помощники
const num = (v) => {
  if (v === null || v === undefined || v === '') return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
};
const pos = (v) => { const x = num(v); return x !== null && x > 0 ? x : null; };
const firstNum = (...vs) => { for (const v of vs) { const x = num(v); if (x !== null) return x; } return null; };
const firstPos = (...vs) => { for (const v of vs) { const x = pos(v); if (x !== null) return x; } return null; };
const isoDate = (v) => {
  if (!v) return null;
  const s = String(v).slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(s) && !s.startsWith('0000') ? s : null;
};
const currency = (v) => {
  const c = v ? String(v).toUpperCase() : 'RUB';
  return RUB.has(c) ? 'RUB' : c;
};
const baseDir = (base) => { const b = base || DEFAULT_DATA_BASE; return b.endsWith('/') ? b : b + '/'; };
const addDays = (date, k) => new Date(Date.parse(date + 'T00:00:00Z') + k * 86400000).toISOString().slice(0, 10);

/** Следующий рабочий день (пн–пт) после даты; праздники биржи не учитываются. */
function nextWeekday(date) {
  let d = addDays(date, 1);
  for (;;) {
    const wd = new Date(Date.parse(d + 'T00:00:00Z')).getUTCDay();
    if (wd !== 0 && wd !== 6) return d;
    d = addDays(d, 1);
  }
}

/**
 * Класс бумаги по полям ISS TYPE/GROUP (description, /securities.json) и рынку режима торгов:
 * share — акции и расписки (common_share, preferred_share, depositary_receipt; TQBR SECTYPE 1/2/D),
 * fund — паи ПИФ/БПИФ (*_ppif, etf; SECTYPE J/9/A/B), bond, index, metal, currency; иначе null.
 */
export function classify({ type, group, secid, market, sectype } = {}) {
  const t = String(type || '').toLowerCase(), g = String(group || '').toLowerCase();
  const st = sectype == null ? '' : String(sectype).toUpperCase();
  if (METAL_IDS.has(String(secid || '').toUpperCase()) || t.includes('metal') || g.includes('metal')) return 'metal';
  if (t.includes('index') || g.includes('index') || market === 'index') return 'index';
  if (t.includes('bond') || g.includes('bond') || market === 'bonds') return 'bond';
  if (t.includes('ppif') || t.includes('etf') || g.includes('ppif') || g.includes('etf') || ['J', '9', 'A', 'B'].includes(st)) return 'fund';
  if (t.includes('share') || t.includes('depositary') || g.includes('share') || g === 'stock_dr' || ['1', '2', 'D'].includes(st)) return 'share';
  if (g.startsWith('currency') || market === 'selt') return 'currency';
  return null;
}

function defaultBenchmark(cls, { secid, type } = {}) {
  if (cls === 'bond') {
    const ofz = String(type || '').toLowerCase().includes('ofz') || /^SU\d/.test(String(secid || ''));
    return ofz ? DEFAULT_BENCHMARKS.bond_ofz : DEFAULT_BENCHMARKS.bond;
  }
  if (cls === 'index' && secid === 'IMOEX') return 'MCFTR';   // не сравнивать индекс сам с собой
  return DEFAULT_BENCHMARKS[cls] || null;
}

// ------------------------------------------------------------------ поиск и справочник
/**
 * Поиск по тикеру, названию, ISIN: ISS /securities.json?q=. Торгуемые бумаги — первыми (порядок ISS внутри групп).
 * @returns {Promise<{secid, name, shortName, isin, cls, primaryBoard, isTraded}[]>}
 */
export async function searchSecurities(q, { fetchImpl, limit = 20 } = {}) {
  const query = String(q || '').trim();
  if (!query) return [];
  const url = `${ISS_BASE}/securities.json?` + qs({ 'iss.meta': 'off', q: query, limit: Math.min(Math.max(1, limit), 100),
    'securities.columns': 'secid,shortname,name,isin,type,group,primary_boardid,is_traded' });
  const rows = block(await getCached(url, fetchImpl), 'securities');
  const out = rows.filter((r) => r.secid).map((r) => ({
    secid: r.secid,
    name: r.name || r.shortname || r.secid,
    shortName: r.shortname || r.secid,
    isin: r.isin || null,
    cls: classify({ type: r.type, group: r.group, secid: r.secid }),
    primaryBoard: r.primary_boardid || null,
    isTraded: Number(r.is_traded) === 1,
  }));
  return out.sort((a, b) => Number(b.isTraded) - Number(a.isTraded)).slice(0, limit);
}

/** Эмитент из /securities.json?q= (поле emitent_title точного совпадения secid); ошибка — null. */
async function issuerOf(secid, fetchImpl) {
  try {
    const url = `${ISS_BASE}/securities.json?` + qs({ 'iss.meta': 'off', q: secid, limit: 100,
      'securities.columns': 'secid,emitent_title' });
    const row = block(await getCached(url, fetchImpl), 'securities').find((r) => r.secid === secid);
    return (row && row.emitent_title) || null;
  } catch {
    return null;
  }
}

/**
 * Шапка страницы: ISS /securities/{id}.json (блоки description — name/title/value — и boards).
 * couponPercent — % годовых (как в ISS), couponPeriod — дней, faceValue — в валюте номинала.
 * firstTradeDate — самая ранняя history_from среди режимов того же рынка, что основной.
 */
export async function fetchSecurityInfo(secid, { fetchImpl } = {}) {
  const id = String(secid || '').trim().toUpperCase();
  const url = `${ISS_BASE}/securities/${encodeURIComponent(id)}.json?` + qs({ 'iss.meta': 'off', 'iss.only': 'description,boards' });
  const data = await getCached(url, fetchImpl);
  const boards = block(data, 'boards');
  if (!boards.length) throw new Error(`Инструмент ${id} не найден на ISS`);
  const d = {};
  for (const r of block(data, 'description')) if (r.name) d[String(r.name).toUpperCase()] = r.value;
  const prim = boards.find((b) => Number(b.is_primary) === 1) || boards[0];
  const cls = classify({ type: d.TYPE, group: d.GROUP, secid: id, market: prim.market });
  let firstTrade = null;
  for (const b of boards) {
    if (b.engine !== prim.engine || b.market !== prim.market) continue;
    const h = isoDate(b.history_from);
    if (h && (!firstTrade || h < firstTrade)) firstTrade = h;
  }
  const freq = pos(d.COUPONFREQUENCY);
  const issuer = await issuerOf(id, fetchImpl);
  return {
    secid: id,
    name: d.NAME || d.SHORTNAME || id,
    shortName: d.SHORTNAME || id,
    isin: d.ISIN || null,
    cls,
    typeLabel: d.TYPENAME || (cls ? SYMBOL_CLASSES[cls] : null) || 'Инструмент',
    issuer,
    listLevel: num(d.LISTLEVEL),
    currency: currency(d.FACEUNIT || d.CURRENCYID),
    issueDate: isoDate(d.ISSUEDATE),
    firstTradeDate: firstTrade,
    faceValue: pos(d.FACEVALUE),
    matDate: isoDate(d.MATDATE),
    couponPercent: num(d.COUPONPERCENT),
    couponPeriod: pos(d.COUPONPERIOD) ?? (freq ? Math.round(364 / freq) : null),
    offerDate: isoDate(d.OFFERDATE) || isoDate(d.PUTOPTIONDATE) || isoDate(d.BUYBACKDATE) || isoDate(d.CALLOPTIONDATE),
    board: prim.boardid,
    engine: prim.engine,
    market: prim.market,
    benchmark: defaultBenchmark(cls, { secid: id, type: d.TYPE }),
  };
}

// ------------------------------------------------------------------ котировки
/** Прежние основные режимы (до перехода на Т+ в 2013–2014 гг.) и TQTF (фонды до июня 2026 г.) — как iss.py. */
const LEGACY_MAIN_BOARDS = new Set(['EQBR', 'EQNE', 'EQBS', 'EQNL', 'EQLV', 'TQBS', 'TQNE', 'TQNL', 'TQLV',
  'EQTF', 'EQOB', 'EQOS', 'EQDB', 'EQNO', 'EQQI', 'EQIR', 'TQTF']);
const CANDLE_CHUNK_DAYS = 700;   // ≈ 480 торговых дней — обычно одна страница свечей (500 строк)

/**
 * Сплиты и консолидации бумаги: ISS /statistics/engines/stock/splits.json (общий список: блок splits —
 * tradedate (первый день торгов в новых акциях), secid, before, after; ~57 строк без cursor — пагинация по
 * splits.cursor, если появится, иначе по start), при его недоступности — /statistics/engines/stock/splits/{secid}.json.
 * Сплит 1:100 — before=1, after=100. Утилита для рядов из /history (не скорректированы на сплиты); свечи ISS
 * уже скорректированы биржей — к ним не применять.
 * @returns {Promise<{splits: {date, before, after}[], ok: boolean}>}  ok = false — оба источника недоступны
 * Результат (в том числе неудача) запоминается на сессию для каждого fetchImpl.
 */
const _splitsMemo = new WeakMap();   // fetch -> Map(secid -> Promise) — и неудача запоминается на сессию

export async function fetchSplits(secid, { fetchImpl } = {}) {
  const id = String(secid || '').trim().toUpperCase();
  const f = fetchImpl || globalThis.fetch;
  let memo = _splitsMemo.get(f);
  if (!memo) { memo = new Map(); _splitsMemo.set(f, memo); }
  if (!memo.has(id)) memo.set(id, loadSplits(id, fetchImpl));
  return memo.get(id);
}

async function loadSplits(id, fetchImpl) {
  const norm = (rows) => rows.filter((r) => String(r.secid || '').toUpperCase() === id && isoDate(r.tradedate))
    .map((r) => ({ date: isoDate(r.tradedate), before: num(r.before), after: num(r.after) }))
    .filter((r) => r.before > 0 && r.after > 0)
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  try {
    const all = [];
    let start = 0;
    for (let guard = 0; guard < 100; guard++) {
      const data = await getCached(`${ISS_BASE}/statistics/engines/stock/splits.json?` + qs({ 'iss.meta': 'off', start }), fetchImpl, 'json', 1);
      if (!data || !data.splits) throw new Error('нет блока splits');
      const page = block(data, 'splits');
      if (!page.length) break;
      all.push(...page);
      const cur = block(data, 'splits.cursor')[0];
      if (cur) {
        const idx = Number(cur.INDEX), size = Number(cur.PAGESIZE), total = Number(cur.TOTAL);
        if (!(size > 0) || !(idx + size < total)) break;
        start = idx + size;
      } else {
        if (page.length < 100) break;
        const before = all.length - page.length;
        const key = (r) => `${r.secid}|${r.tradedate}|${r.before}|${r.after}`;
        const seen = new Set(all.slice(0, before).map(key));
        if (page.every((r) => seen.has(key(r)))) break;      // сервер игнорирует start
        start += page.length;
      }
    }
    return { splits: norm(all), ok: true };
  } catch { /* общий список недоступен — по бумаге */ }
  try {
    const data = await getCached(`${ISS_BASE}/statistics/engines/stock/splits/${encodeURIComponent(id)}.json?` +
      qs({ 'iss.meta': 'off' }), fetchImpl, 'json', 1);
    if (!data || !data.splits) throw new Error('нет блока splits');
    return { splits: norm(block(data, 'splits')), ok: true };
  } catch {
    return { splits: [], ok: false };
  }
}

/** Свечи interval=24 по пути path на [f, t]: диапазон делится на куски по CANDLE_CHUNK_DAYS, куски — параллельно,
 * внутри куска — страницы по 500 строк. */
async function fetchCandleRows(path, f, t, fetchImpl) {
  const chunks = [];
  for (let a = f; a <= t; a = addDays(a, CANDLE_CHUNK_DAYS)) {
    const b = addDays(a, CANDLE_CHUNK_DAYS - 1);
    chunks.push([a, b < t ? b : t]);
  }
  const parts = await mapLimit(chunks, ISS_PARALLEL, async ([a, b]) => {
    const out = [];
    let start = 0;
    for (let guard = 0; guard < 10000; guard++) {
      const page = block(await getCached(`${ISS_BASE}${path}?` + qs({ 'iss.meta': 'off', interval: 24, from: a, till: b, start }), fetchImpl), 'candles');
      if (!page.length) break;
      out.push(...page);
      if (page.length < 500) break;
      start += page.length;
    }
    return out;
  });
  return parts.flat();
}

/** Начало дневных свечей индекса (candleborders, interval 24) или null, если узнать не удалось. */
async function indexCandleBegin(id, fetchImpl) {
  try {
    const data = await getCached(`${ISS_BASE}/engines/stock/markets/index/securities/${encodeURIComponent(id)}/candleborders.json?` +
      qs({ 'iss.meta': 'off' }), fetchImpl, 'json', 1);
    const row = block(data, 'borders').find((r) => Number(r.interval) === 24);
    return row ? isoDate(row.begin) : null;
  } catch {
    return null;
  }
}

/**
 * Дневные OHLC основного режима с корректировкой на сплиты.
 * Акции, фонды, облигации, металлы: свечи interval=24; прежние режимы того же рынка (EQBR до 2013 г., TQTF
 * у фондов до июня 2026 г. — LEGACY_MAIN_BOARDS или та же board_group_id) подклеиваются до начала основного,
 * как iss.close_series; при совпадении даты берётся основной режим. Диапазон грузится параллельными кусками.
 * Индексы: свечи рынка index (500 строк) с начала дневных свечей (candleborders), history (параллельные
 * страницы после первой) — только для периода до них; candleborders недоступен — только history. volume — NaN.
 * Сплиты: свечи ISS (interval 24 и 31; TQBR, EQBR, TQTF) уже скорректированы биржей задним числом (проверено
 * 08.10.2026: GMKN 25.03.2024 close 151.38 при сплите 1:100 от 08.04.2024), поэтому по умолчанию сплиты не
 * запрашиваются и не применяются, splitAdjusted: true. adjust: true — дополнительно применить fetchSplits +
 * adjustSplits; для свечей это ДВОЙНАЯ корректировка — опция только для отладки/рядов из /history.
 * Пропуски цен — NaN; строки без close > 0 отбрасываются.
 * @returns {Promise<{dates, open, high, low, close, volume, splitAdjusted: boolean}>}
 */
export async function fetchOHLC(secid, from = '2000-01-01', till = null, { fetchImpl, adjust = false } = {}) {
  const id = String(secid || '').trim().toUpperCase();
  const boards = await fetchBoards(id, { fetchImpl });
  if (!boards.length) throw new Error(`Инструмент ${id} не найден на ISS`);
  const prim = boards.find((b) => Number(b.is_primary) === 1) || boards[0];
  const rows = new Map();
  const put = (date, o, h, l, c, v) => {
    const cl = pos(c);
    if (!date || cl === null) return;
    rows.set(String(date).slice(0, 10), [pos(o) ?? NaN, pos(h) ?? NaN, pos(l) ?? NaN, cl, num(v) ?? NaN]);
  };
  const f = from || '2000-01-01', t = till || todayIso();
  // свечи ISS скорректированы биржей; индексы сплитов не имеют
  const splitAdjusted = true;
  if (prim.market === 'index') {
    const begin = await indexCandleBegin(id, fetchImpl);
    const histTill = begin === null ? t : (addDays(begin, -1) < t ? addDays(begin, -1) : t);
    const tasks = [];
    if (f <= histTill) {
      tasks.push(fetchHistoryPages((start) => `${ISS_BASE}/history/engines/stock/markets/index/securities/${encodeURIComponent(id)}.json?` +
        qs({ 'iss.meta': 'off', from: f, till: histTill, 'history.columns': 'TRADEDATE,OPEN,HIGH,LOW,CLOSE', start }), fetchImpl)
        .then((hs) => hs.map((r) => [r.TRADEDATE, r.OPEN, r.HIGH, r.LOW, r.CLOSE])));
    }
    if (begin !== null && (begin > f ? begin : f) <= t) {
      tasks.push(fetchCandleRows(`/engines/stock/markets/index/securities/${encodeURIComponent(id)}/candles.json`,
        begin > f ? begin : f, t, fetchImpl).then((cs) => cs.map((r) => [r.begin, r.open, r.high, r.low, r.close])));
    }
    for (const part of await Promise.all(tasks)) for (const [d, o, h, l, c] of part) put(d, o, h, l, c, null);
  } else {
    const primFrom = isoDate(prim.history_from);
    const segs = [];
    for (const b of boards) {
      if (b === prim || b.engine !== prim.engine || b.market !== prim.market) continue;
      const hf = isoDate(b.history_from), ht = isoDate(b.history_till);
      if (!hf || !primFrom || hf >= primFrom) continue;
      if (!LEGACY_MAIN_BOARDS.has(b.boardid) && !(b.board_group_id != null && b.board_group_id === prim.board_group_id)) continue;
      const a = hf > f ? hf : f;
      const z = [t, ht || t, primFrom].sort()[0];
      if (a <= z) segs.push({ board: b.boardid, from: a, till: z, hf });
    }
    segs.sort((x, y) => (x.hf < y.hf ? -1 : 1));
    segs.push({ board: prim.boardid, from: primFrom && primFrom > f ? primFrom : f, till: t });
    const parts = await Promise.all(segs.map((sg) => (sg.from <= sg.till
      ? fetchCandleRows(`/engines/${prim.engine}/markets/${prim.market}/boards/${sg.board}/securities/${encodeURIComponent(id)}/candles.json`,
        sg.from, sg.till, fetchImpl)
      : [])));
    for (const part of parts) for (const r of part) put(r.begin, r.open, r.high, r.low, r.close, r.volume);
  }
  const dates = [...rows.keys()].sort();
  const col = (j) => dates.map((d) => rows.get(d)[j]);
  let out = { dates, open: col(0), high: col(1), low: col(2), close: col(3), volume: col(4) };
  if (adjust && prim.market !== 'index' && dates.length) {
    const sp = await fetchSplits(id, { fetchImpl });
    if (sp.splits.length) out = adjustSplits(out, sp.splits);
  }
  return { ...out, splitAdjusted };
}

/**
 * Ключевые цифры: ISS marketdata + securities (+ marketdata_yields для облигаций) основного режима
 * и дневные свечи за 52 недели (high52/low52 — по high/low, при их отсутствии — по close). Свечи можно передать
 * готовыми: fetchSnapshot(secid, {ohlc}) — результат fetchOHLC (тогда отдельного запроса свечей нет).
 * changePct — доля; для облигаций цены — % от номинала, bond.yield — доля, bond.duration — лет.
 */
export async function fetchSnapshot(secid, { fetchImpl, ohlc: given = null } = {}) {
  const id = String(secid || '').trim().toUpperCase();
  const bl = await fetchBoards(id, { fetchImpl });
  if (!bl.length) throw new Error(`Инструмент ${id} не найден на ISS`);
  const p0 = bl.find((b) => Number(b.is_primary) === 1) || bl[0];
  const sec = { engine: p0.engine, market: p0.market, board: p0.boardid };
  const url = `${ISS_BASE}/engines/${sec.engine}/markets/${sec.market}/boards/${sec.board}/securities/${encodeURIComponent(id)}.json?` +
    qs({ 'iss.meta': 'off', 'iss.only': 'securities,marketdata,marketdata_yields' });
  const data = await getCached(url, fetchImpl);
  const s = block(data, 'securities')[0] || {};
  const md = block(data, 'marketdata')[0] || {};
  const my = block(data, 'marketdata_yields')[0] || {};
  // свечи: переданные страницей (fetchOHLC уже загружен для графика) или за 380 дней
  let ohlc = { dates: [], open: [], high: [], low: [], close: [] };
  if (given && Array.isArray(given.dates)) {
    ohlc = given;
  } else {
    try {
      ohlc = await fetchOHLC(id, addDays(todayIso(), -380), null, { fetchImpl });
    } catch { /* без свечей — только marketdata */ }
  }
  const nC = ohlc.dates.length;
  const lastCandle = nC ? ohlc.close[nC - 1] : null;
  const date = isoDate(md.TRADEDATE) || isoDate(md.SYSTIME) || (nC ? ohlc.dates[nC - 1] : null) || isoDate(s.PREVDATE);
  const last = firstPos(md.LAST, md.LCURRENTPRICE, md.MARKETPRICE, md.CURRENTVALUE, lastCandle, s.PREVPRICE);
  let candlePrev = null;
  if (nC) candlePrev = ohlc.dates[nC - 1] === date ? (nC > 1 ? ohlc.close[nC - 2] : null) : ohlc.close[nC - 1];
  const idxPrev = num(md.CURRENTVALUE) !== null && num(md.LASTCHANGE) !== null ? md.CURRENTVALUE - md.LASTCHANGE : null;
  const prevClose = firstPos(s.PREVPRICE, idxPrev, candlePrev);
  const change = last !== null && prevClose !== null ? last - prevClose : null;
  let high52 = null, low52 = null;
  if (nC) {
    const from = addDays(ohlc.dates[nC - 1], -365);
    for (let i = 0; i < nC; i++) {
      if (ohlc.dates[i] < from) continue;
      const h = Number.isFinite(ohlc.high[i]) ? ohlc.high[i] : ohlc.close[i];
      const l = Number.isFinite(ohlc.low[i]) ? ohlc.low[i] : ohlc.close[i];
      if (high52 === null || h > high52) high52 = h;
      if (low52 === null || l < low52) low52 = l;
    }
  }
  const cls = classify({ secid: id, market: sec.market, sectype: s.SECTYPE });
  const issueSize = pos(s.ISSUESIZE);
  let marketCap = firstPos(md.ISSUECAPITALIZATION, sec.market === 'index' ? md.CAPITALIZATION : null);
  if (marketCap === null && cls === 'share' && issueSize !== null && last !== null) marketCap = last * issueSize;
  let bond = null;
  if (sec.market === 'bonds') {
    const y = firstNum(md.YIELD, my.EFFECTIVEYIELD);
    const dur = firstPos(md.DURATION, my.DURATION);
    bond = {
      yield: y !== null ? y / 100 : null,
      duration: dur !== null ? dur / 365.25 : null,
      accruedInt: num(s.ACCRUEDINT),
      couponValue: num(s.COUPONVALUE),
      nextCoupon: isoDate(s.NEXTCOUPON),
    };
  }
  return {
    date,
    last,
    prevClose,
    change,
    changePct: change !== null && prevClose ? change / prevClose : null,
    high52,
    low52,
    valueRub: firstNum(md.VALTODAY_RUR, md.VALTODAY),
    marketCap,
    bond,
  };
}

// ------------------------------------------------------------------ дивиденды и купоны
/**
 * Дивиденды из public/data/dividends.json (T-Invest API). exDate — следующий рабочий день после last_buy_date
 * (нет last_buy_date — дата отсечки = record_date); yield — доля (yield_value T-Invest — в %).
 * Нет файла или бумаги — []. Отменённые остаются с cancelled = true (исключает dividendStats).
 */
export async function loadDividends(secid, { base, fetchImpl } = {}) {
  let json;
  try {
    json = await getCached(`${baseDir(base)}dividends.json`, fetchImpl, 'json', 2);
  } catch {
    return [];
  }
  const data = json && typeof json.data === 'object' && json.data ? json.data : {};
  const rows = data[String(secid || '').toUpperCase()];
  if (!Array.isArray(rows)) return [];
  const out = [];
  for (const r of rows) {
    if (!r || typeof r !== 'object') continue;
    const recordDate = isoDate(r.record_date);
    const value = num(r.value);
    if (!recordDate || value === null) continue;
    const lbd = isoDate(r.last_buy_date);
    const y = num(r.yield_value);
    out.push({
      recordDate,
      exDate: lbd ? nextWeekday(lbd) : recordDate,
      value,
      currency: currency(r.currency),
      yield: y !== null ? y / 100 : null,
      cancelled: Boolean(r.cancelled),
      paymentDate: isoDate(r.payment_date),
      declaredDate: isoDate(r.declared_date),
    });
  }
  return out.sort((a, b) => (a.recordDate < b.recordDate ? -1 : a.recordDate > b.recordDate ? 1 : 0));
}

/**
 * Статистика дивидендов (без отменённых и не в рублях) на последнюю дату ряда цен close (нет цен — сегодня):
 * ttmValue — сумма с exDate за 365 дн.; ttmYield = ttmValue / последняя цена (доля; нет цены — null);
 * byYear — суммы по годам exDate; byMonth — число выплат по месяцам exDate (янв … дек);
 * growthStreak — число подряд идущих лет, считая назад от последнего полного года, где сумма больше суммы
 *   предыдущего года; год первой выплаты ростом не считается (год без выплат внутри истории — сумма 0);
 * payoutsPerYear — среднее число выплат за последние 3 полных года (не раньше первой выплаты);
 * upcoming — объявленные выплаты с exDate позже даты расчёта.
 */
export function dividendStats(divs, close) {
  const hasPx = close && Array.isArray(close.dates) && close.dates.length;
  const asOf = hasPx ? close.dates[close.dates.length - 1] : todayIso();
  let lastPx = null;
  if (hasPx) for (let i = close.values.length - 1; i >= 0; i--) { const v = pos(close.values[i]); if (v !== null) { lastPx = v; break; } }
  const valid = (divs || []).filter((d) => d && !d.cancelled && d.exDate && num(d.value) !== null && RUB.has(currency(d.currency)));
  const past = valid.filter((d) => d.exDate <= asOf);
  const upcoming = valid.filter((d) => d.exDate > asOf);
  const ttmFrom = addDays(asOf, -365);
  const ttmValue = past.filter((d) => d.exDate > ttmFrom).reduce((a, d) => a + Number(d.value), 0);
  const totals = new Map(), counts = new Map();
  const byMonth = new Array(12).fill(0);
  for (const d of past) {
    const y = Number(d.exDate.slice(0, 4));
    totals.set(y, (totals.get(y) || 0) + Number(d.value));
    counts.set(y, (counts.get(y) || 0) + 1);
    byMonth[Number(d.exDate.slice(5, 7)) - 1] += 1;
  }
  const years = [...totals.keys()].sort((a, b) => a - b);
  const lastFull = Number(asOf.slice(0, 4)) - 1;
  // год первой выплаты ростом не считается: сравниваются только годы после первого года с выплатами
  let growthStreak = 0;
  if (years.length) {
    for (let y = lastFull; y > years[0] && (totals.get(y) || 0) > (totals.get(y - 1) || 0); y--) growthStreak++;
  }
  let payoutsPerYear = null;
  if (years.length) {
    const span = [lastFull - 2, lastFull - 1, lastFull].filter((y) => y >= years[0]);
    if (span.length) payoutsPerYear = span.reduce((a, y) => a + (counts.get(y) || 0), 0) / span.length;
  }
  return {
    ttmValue,
    ttmYield: lastPx ? ttmValue / lastPx : null,
    byYear: years.map((year) => ({ year, value: totals.get(year) })),
    byMonth,
    growthStreak,
    payoutsPerYear,
    upcoming,
  };
}

/**
 * Купоны, амортизации и оферты облигации: ISS /statistics/engines/stock/markets/bonds/bondization/{id}.json
 * (страницы по 100 строк; блоки coupons, amortizations, offers и их *.cursor).
 * coupons.value — руб. на бумагу (value_rub, иначе value), rate — доля годовых (valueprc / 100), null — не определён.
 */
export async function loadCoupons(secid, { fetchImpl } = {}) {
  const id = String(secid || '').trim().toUpperCase();
  const names = ['coupons', 'amortizations', 'offers'];
  const acc = { coupons: [], amortizations: [], offers: [] };
  const limit = 100;
  let start = 0;
  for (let guard = 0; guard < 100; guard++) {
    const url = `${ISS_BASE}/statistics/engines/stock/markets/bonds/bondization/${encodeURIComponent(id)}.json?` +
      qs({ 'iss.meta': 'off', limit, start });
    const data = await getCached(url, fetchImpl);
    let more = false;
    for (const n of names) {
      const page = block(data, n);
      acc[n].push(...page);
      const cur = block(data, `${n}.cursor`)[0];
      if (cur) {
        if (Number(cur.INDEX) + Number(cur.PAGESIZE) < Number(cur.TOTAL)) more = true;
      } else if (page.length >= limit) {
        more = true;
      }
    }
    if (!more) break;
    start += limit;
  }
  const byDate = (a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0);
  const coupons = acc.coupons.map((r) => {
    const rate = num(r.valueprc);
    return { date: isoDate(r.coupondate), value: firstNum(r.value_rub, r.value), rate: rate !== null ? rate / 100 : null };
  }).filter((r) => r.date).sort(byDate);
  const amortizations = acc.amortizations.map((r) => ({ date: isoDate(r.amortdate), value: firstNum(r.value_rub, r.value) }))
    .filter((r) => r.date).sort(byDate);
  const offers = acc.offers.map((r) => ({ date: isoDate(r.offerdate), type: r.offertype || null }))
    .filter((r) => r.date).sort(byDate);
  return { coupons, amortizations, offers };
}

// ------------------------------------------------------------------ фонды и рейтинги
/**
 * Фонд в справочнике RusETFs (public/data/rusetfs_funds.json) и комиссия против рынка.
 * market — по торгуемым фондам (trade_status «Торгуется» или не указан) с комиссией > 0 того же класса активов
 * (нет записи фонда или класса — по всем торгуемым): min, p25, median, p75, max (линейная интерполяция, как numpy),
 * n, classMedian (= median), marketMedian — медиана по всем торгуемым фондам. Комиссии — в % годовых, как commission_pct.
 */
export async function loadFundInfo(secid, { base, fetchImpl } = {}) {
  const empty = { min: null, p25: null, median: null, p75: null, max: null, n: 0, classMedian: null, marketMedian: null };
  let json;
  try {
    json = await getCached(`${baseDir(base)}rusetfs_funds.json`, fetchImpl, 'json', 2);
  } catch {
    return { info: null, market: empty };
  }
  const rows = Array.isArray(json && json.data) ? json.data.filter((r) => r && typeof r === 'object' && r.ticker) : [];
  const id = String(secid || '').toUpperCase();
  const info = rows.find((r) => String(r.ticker).toUpperCase() === id) || null;
  const traded = rows.filter((r) => (r.trade_status == null || r.trade_status === 'Торгуется') && (num(r.commission_pct) || 0) > 0);
  const same = info && info.asset_class ? traded.filter((r) => r.asset_class === info.asset_class) : traded;
  const fees = same.map((r) => Number(r.commission_pct));
  const all = traded.map((r) => Number(r.commission_pct));
  if (!fees.length) return { info, market: { ...empty, marketMedian: all.length ? percentile(all, 50) : null } };
  const med = percentile(fees, 50);
  return {
    info,
    market: {
      min: Math.min(...fees), p25: percentile(fees, 25), median: med, p75: percentile(fees, 75), max: Math.max(...fees),
      n: fees.length, classMedian: med, marketMedian: all.length ? percentile(all, 50) : null,
    },
  };
}

/** Ночные рейтинги public/data/symbol_stats.json ({updated, source, rf, window, classes, items}); нет файла — null. */
export async function loadSymbolStats({ base, fetchImpl } = {}) {
  try {
    const json = await getCached(`${baseDir(base)}symbol_stats.json`, fetchImpl, 'json', 1);
    if (!json || typeof json !== 'object' || !json.items || typeof json.items !== 'object') return null;
    return json;
  } catch {
    return null;
  }
}
