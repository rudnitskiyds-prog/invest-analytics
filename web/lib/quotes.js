// Котировки в рублях за 1 бумагу и история цен для учёта портфеля — порт core/data/quotes.py (+ курсы ЦБ из cbr.py).
// Облигации: грязная цена = цена% × номинал / 100 × курс(валюта номинала) + НКД × курс(валюта расчётов).
// Сеть — только через fetchImpl (по умолчанию глобальный fetch); ISS — ISS_BASE, ЦБ — CBR_BASE (прокси).
import * as D from './data.js';

const { ISS_BASE, getCached, qs, block, mapLimit, ISS_PARALLEL, fetchIssSecurity, alignFrame, todayIso } = D;
const CBR_BASE = D.CBR_BASE ?? 'https://www.cbr.ru';

// внутренние коды валют ЦБ для XML_dynamic.asp (core/data/currency.py)
export const CURRENCY_CODES = { USD: 'R01235', EUR: 'R01239', CNY: 'R01375', CHF: 'R01775', GBP: 'R01035' };
const METALS = { GLDRUB_TOM: 'Золото (руб./г)', SLVRUB_TOM: 'Серебро (руб./г)',
  PLDRUB_TOM: 'Палладий (руб./г)', PLTRUB_TOM: 'Платина (руб./г)' };

/** Код валюты ISS -> ISO: 'SUR' / 'RUR' / пусто -> 'RUB', иначе верхний регистр. */
export function normalizeCurrency(code) {
  const c = String(code ?? '').trim().toUpperCase();
  return !c || c === 'SUR' || c === 'RUR' || c === 'NAN' ? 'RUB' : c;
}

const numOr = (x) => (x === null || x === undefined || x === '' ? NaN : Number(x));
const dmy = (iso) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}`;
const addDays = (iso, n) => new Date(Date.parse(iso + 'T00:00:00Z') + n * 86400000).toISOString().slice(0, 10);

/** Курсы ЦБ (Value / Nominal) за период по одной валюте -> [{date, rate}] (по возрастанию даты); 5-летние куски, как в Python. */
async function currencyRate(code, startIso, endIso, fetchImpl) {
  const cur = normalizeCurrency(code);
  if (cur === 'RUB') return [];
  if (!(cur in CURRENCY_CODES)) throw new Error(`Неизвестная валюта для курса ЦБ: ${code}`);
  const out = new Map();
  let from = startIso;
  while (from <= endIso) {
    const y = +from.slice(0, 4) + 5, m = +from.slice(5, 7), d = +from.slice(8, 10);
    let nxt = new Date(Date.UTC(y, m - 1, d));
    nxt = nxt.getUTCMonth() !== m - 1 ? `${y}-${String(m).padStart(2, '0')}-28` : addDays(nxt.toISOString().slice(0, 10), -1);
    if (nxt > endIso) nxt = endIso;
    const url = `${CBR_BASE}/scripts/XML_dynamic.asp?` + qs({ date_req1: dmy(from), date_req2: dmy(nxt), VAL_NM_RQ: CURRENCY_CODES[cur] });
    const xml = await getCached(url, fetchImpl, 'text', 2);
    for (const rec of String(xml).matchAll(/<Record\b([^>]*)>([\s\S]*?)<\/Record>/g)) {
      const date = /Date="(\d{2})\.(\d{2})\.(\d{4})"/.exec(rec[1]);
      const val = /<Value>([^<]*)<\/Value>/.exec(rec[2]);
      if (!date || !val || !val[1].trim()) continue;
      const nom = /<Nominal>([^<]*)<\/Nominal>/.exec(rec[2]);
      const n = nom && nom[1].trim() ? Number(nom[1].replace(',', '.').replace(/\s/g, '')) : 1;
      out.set(`${date[3]}-${date[2]}-${date[1]}`, Number(val[1].replace(',', '.').replace(/\s/g, '')) / (n || 1));
    }
    from = addDays(nxt, 1);
  }
  return [...out.keys()].sort().map((date) => ({ date, rate: out.get(date) }));
}

/** Курсы ЦБ на дату для набора валют: {ISO: руб. за 1 единицу}; рубль — 1, при любой ошибке/нет курса за 14 дней — NaN. */
async function ratesOn(codes, dateIso, fetchImpl) {
  const out = {};
  await Promise.all([...new Set([...codes].map(normalizeCurrency))].map(async (c) => {
    if (c === 'RUB') { out[c] = 1; return; }
    try {
      const rows = (await currencyRate(c, addDays(dateIso, -14), dateIso, fetchImpl)).filter((r) => r.date <= dateIso);
      out[c] = rows.length ? rows[rows.length - 1].rate : NaN;
    } catch { out[c] = NaN; }
  }));
  return out;
}

/** description + boards бумаги (тот же URL и кэш, что у symbol.fetchSecurityInfo) -> {board, market, facevalue, faceunit}. */
async function resolveInfo(secid, fetchImpl) {
  const url = `${ISS_BASE}/securities/${encodeURIComponent(secid)}.json?` + qs({ 'iss.meta': 'off', 'iss.only': 'description,boards' });
  const data = await getCached(url, fetchImpl);
  const boards = block(data, 'boards');
  if (!boards.length) throw new Error(`Инструмент ${secid} не найден на ISS`);
  const row = boards.find((b) => Number(b.is_primary) === 1) || boards[0];
  const d = {};
  for (const r of block(data, 'description')) if (r.name) d[r.name] = r.value;
  return { board: row.boardid, market: row.market, facevalue: d.FACEVALUE, faceunit: normalizeCurrency(d.FACEUNIT) };
}

const CHUNK = 25;   // тикеров в одном запросе (длина query у прокси ограничена)

async function boardTable(path, secCols, mdCols, secids, fetchImpl) {
  const parts = [];
  for (let i = 0; i < secids.length; i += CHUNK) parts.push(secids.slice(i, i + CHUNK));
  // ошибка одной пачки не теряет остальные; если упали все — пробрасываем ошибку
  const res = await Promise.allSettled(parts.map((p) => getCached(`${ISS_BASE}${path}.json?` + qs({ 'iss.meta': 'off', 'iss.only': 'securities,marketdata',
    securities: p.join(','), 'securities.columns': secCols, 'marketdata.columns': mdCols }), fetchImpl)));
  const ok = res.filter((r) => r.status === 'fulfilled');
  if (parts.length && !ok.length) throw res[0].reason;
  return ok.flatMap(({ value: data }) => {
    const md = new Map(block(data, 'marketdata').map((r) => [r.SECID, r]));
    return block(data, 'securities').map((s) => ({ ...s, LAST: (md.get(s.SECID) || {}).LAST }));
  });
}

// Котировки режима: {SECID: {price, name, lot, currency}}; цена = LAST, иначе PREVPRICE.
async function boardQuotes(board, market, secids, date, fetchImpl) {
  const want = new Set(secids), res = {};
  if (market === 'bonds') {
    const rows = (await boardTable(`/engines/stock/markets/bonds/boards/${board}/securities`,
      'SECID,SHORTNAME,FACEVALUE,ACCRUEDINT,PREVPRICE,FACEUNIT,CURRENCYID', 'SECID,LAST', secids, fetchImpl)).filter((r) => want.has(r.SECID));
    const rates = await ratesOn(rows.flatMap((r) => [r.FACEUNIT, r.CURRENCYID]), date, fetchImpl);
    for (const r of rows) {
      const price = numOr(r.LAST ?? r.PREVPRICE);
      const face = normalizeCurrency(r.FACEUNIT), settle = normalizeCurrency(r.CURRENCYID);
      const acc = r.ACCRUEDINT === null || r.ACCRUEDINT === undefined ? 0 : Number(r.ACCRUEDINT);
      // порядок операций как в Python: ((price × номинал) / 100) × курс + НКД × курс
      res[r.SECID] = { price: price * numOr(r.FACEVALUE) / 100 * rates[face] + acc * rates[settle],
        name: r.SHORTNAME, lot: 1, currency: face };
    }
  } else {
    const rows = market === 'selt'
      ? (await boardTable('/engines/currency/markets/selt/boards/CETS/securities', 'SECID,SHORTNAME,PREVPRICE,LOTSIZE', 'SECID,LAST', secids, fetchImpl))
        .filter((r) => r.SECID in METALS).map((r) => ({ ...r, SHORTNAME: METALS[r.SECID] }))
      : await boardTable(`/engines/stock/markets/shares/boards/${board}/securities`, 'SECID,SHORTNAME,LOTSIZE,PREVPRICE', 'SECID,LAST', secids, fetchImpl);
    for (const r of rows) {
      if (want.has(r.SECID)) res[r.SECID] = { price: numOr(r.LAST ?? r.PREVPRICE), name: r.SHORTNAME, lot: Number(r.LOTSIZE) || 1, currency: 'RUB' };
    }
  }
  return res;
}

/**
 * Текущие котировки -> {SECID: {price, name, lot, board, market, currency}}.
 * price — рубли за 1 бумагу (облигации — с НКД); валютные облигации — по курсу ЦБ на сегодня (opts.date — ISO-дата),
 * нет курса — price = null. currency — валюта номинала облигации ('RUB' для прочих). Бумаги, которые не нашлись
 * на ISS или чей режим не отвечает, в результат не попадают (как в Python).
 */
export async function getQuotes(secids, { fetchImpl, date } = {}) {
  const ids = [...new Set([...(secids || [])].map((s) => String(s || '').toUpperCase()).filter(Boolean))].sort();
  const today = date || todayIso();
  const info = await mapLimit(ids, ISS_PARALLEL, (s) => resolveInfo(s, fetchImpl).catch(() => null));
  const groups = new Map();
  ids.forEach((s, i) => {
    if (!info[i]) return;
    const k = `${info[i].board}|${info[i].market}`;
    if (!groups.has(k)) groups.set(k, { board: info[i].board, market: info[i].market, items: [] });
    groups.get(k).items.push(s);
  });
  const out = {};
  await Promise.all([...groups.values()].map(async ({ board, market, items }) => {
    let tbl;
    try { tbl = await boardQuotes(board, market, items, today, fetchImpl); } catch { return; }
    for (const s of items) {
      const r = tbl[s];
      if (r) out[s] = { price: Number.isFinite(r.price) ? r.price : null, name: r.name || s, lot: r.lot, board, market,
        currency: market === 'bonds' ? r.currency : 'RUB' };
    }
  }));
  return out;
}

/**
 * История цен в рублях за бумагу -> Frame {dates, cols} (объединённый календарь, ffill; до первой цены — NaN).
 * Облигации: % × номинал / 100 (без НКД; номинал — текущий из описания ISS); при валютном номинале ряд умножается
 * на курс ЦБ на дату (последний установленный на дату или ранее, ffill). Нет курса валюты — столбцы этой валюты NaN.
 * Ошибка загрузки цен бумаги пробрасывается. Отличие от Python: цены — fetchIssSecurity (без подклейки старых режимов).
 */
export async function priceHistoryRub(secids, startIso, endIso = null, { fetchImpl } = {}) {
  const ids = [...new Set((secids || []).map((s) => String(s).toUpperCase()))];
  const end = endIso || todayIso();
  const curs = {};
  const series = {};
  await mapLimit(ids, ISS_PARALLEL, async (s) => {
    const info = await resolveInfo(s, fetchImpl);
    let px = await fetchIssSecurity(s, startIso, end, { fetchImpl });
    curs[s] = 'RUB';
    if (info.market === 'bonds') {
      const fv = Number(info.facevalue) || 1000;
      px = { dates: px.dates, values: px.values.map((v) => v * fv / 100) };
      curs[s] = info.faceunit;
    }
    series[s] = px;
  });
  const frame = alignFrame(Object.fromEntries(ids.map((s) => [s, series[s]])), { mode: 'union' });
  for (const cur of new Set(Object.values(curs).filter((c) => c !== 'RUB'))) {
    const cols = ids.filter((s) => curs[s] === cur && frame.cols[s].some((v) => !Number.isNaN(v)));
    if (!cols.length) continue;
    const first = frame.dates[Math.min(...cols.map((s) => frame.cols[s].findIndex((v) => !Number.isNaN(v))))];
    let rate;
    try { rate = await currencyRate(cur, `${addDays(first, -14).slice(0, 4)}-01-01`, end, fetchImpl); }
    catch { for (const s of cols) frame.cols[s] = frame.cols[s].map(() => NaN); continue; }
    // курс на каждую дату фрейма: последний установленный на дату или ранее
    let k = -1, last = NaN;
    const onDate = frame.dates.map((d) => { while (k + 1 < rate.length && rate[k + 1].date <= d) last = rate[++k].rate; return last; });
    for (const s of cols) frame.cols[s] = frame.cols[s].map((v, i) => v * onDate[i]);
  }
  return frame;
}
