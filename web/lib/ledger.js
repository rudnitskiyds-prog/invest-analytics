// Учёт портфеля — точный порт core/portfolio/ledger.py (формулы и порядок операций те же).
// Операция (tx): {id?, portfolio_id?, date: 'YYYY-MM-DD', type, secid, qty, price, amount, fee, note}.
// Цена в операциях — в рублях за 1 бумагу (для облигаций с НКД). Чистые функции, без DOM и сети.

export const TX_TYPES = {
  BUY: 'Покупка', SELL: 'Продажа', DIVIDEND: 'Дивиденд', COUPON: 'Купон',
  DEPOSIT: 'Пополнение', WITHDRAW: 'Вывод', FEE: 'Комиссия', TAX: 'Налог',
};
const SIGN = { BUY: -1, SELL: 1, DIVIDEND: 1, COUPON: 1, DEPOSIT: 1, WITHDRAW: -1, FEE: -1, TAX: -1 };
const CSV_COLUMNS = ['date', 'type', 'secid', 'qty', 'price', 'amount', 'fee', 'note'];

const isBlank = (x) => x === null || x === undefined || x === '';
const num = (x) => (isBlank(x) ? NaN : Number(x));
const day = (d) => (d instanceof Date ? d.toISOString() : String(d)).slice(0, 10);
const fee = (r) => Number(r.fee) || 0;
// как Ledger.add: пустая сумма = qty × price
const amountOf = (r) => (isBlank(r.amount) ? (Number(r.qty) || 0) * (Number(r.price) || 0) : Number(r.amount));
const typeOf = (r) => {
  const t = String(r.type);
  if (!(t in SIGN)) throw new Error(`Неизвестный тип операции ${t}`);
  return t;
};

/** Дата 'YYYY-MM-DD' | 'YYYY/MM/DD' | 'DD.MM.YYYY' | 'DD/MM/YYYY' (+ время после даты) -> ISO или null. */
export function parseDate(s) {
  const t = String(s ?? '').trim();
  let y, m, d, k;
  if ((k = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)/.exec(t))) [y, m, d] = [k[1], k[2], k[3]];
  else if ((k = /^(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)/.exec(t))) [d, m, y] = [k[1], k[2], k[3]];
  else return null;
  const dt = new Date(Date.UTC(+y, +m - 1, +d));
  if (dt.getUTCFullYear() !== +y || dt.getUTCMonth() !== +m - 1 || dt.getUTCDate() !== +d) return null;
  return dt.toISOString().slice(0, 10);
}

// порядок как в БД: ORDER BY date, id (стабильно)
function ordered(tx) {
  return tx.map((r, i) => [r, i]).sort((a, b) => {
    const da = day(a[0].date), db = day(b[0].date);
    if (da !== db) return da < db ? -1 : 1;
    if (!isBlank(a[0].id) && !isBlank(b[0].id) && a[0].id !== b[0].id) return a[0].id - b[0].id;
    return a[1] - b[1];
  }).map((x) => x[0]);
}

/** Изменение денежного остатка по каждой операции (в порядке tx). */
export function cashFlow(tx) {
  return tx.map((r) => SIGN[typeOf(r)] * Math.abs(amountOf(r)) - fee(r));
}

/** Внешние потоки инвестора (+ пополнение, − вывод) по датам: [{date, amount}], по возрастанию даты. */
export function externalFlows(tx) {
  const m = new Map();
  for (const r of tx) {
    if (r.type !== 'DEPOSIT' && r.type !== 'WITHDRAW') continue;
    const d = day(r.date);
    m.set(d, (m.get(d) || 0) + Math.abs(amountOf(r)) * (r.type === 'DEPOSIT' ? 1 : -1));
  }
  return [...m.keys()].sort().map((date) => ({ date, amount: m.get(date) }));
}

/** Позиции по средней цене + денежный остаток -> {positions: [{secid, qty, avgCost, invested, realized, income}], cash}. */
export function positions(tx) {
  const book = new Map();
  for (const r of ordered(tx)) {
    const s = r.secid;
    if (!(r.type === 'BUY' || r.type === 'SELL' || r.type === 'DIVIDEND' || r.type === 'COUPON') || !s) continue;
    let p = book.get(s);
    if (!p) { p = { secid: s, qty: 0, avgCost: 0, invested: 0, realized: 0, income: 0 }; book.set(s, p); }
    if (r.type === 'BUY') {
      p.invested += Math.abs(amountOf(r)) + fee(r);
      p.qty += Number(r.qty);
      p.avgCost = p.qty ? p.invested / p.qty : 0;
    } else if (r.type === 'SELL') {
      const q = Math.min(Number(r.qty), p.qty);
      const proceeds = Math.abs(amountOf(r)) - fee(r);
      p.realized += proceeds - p.avgCost * q;
      p.invested -= p.avgCost * q;
      p.qty -= q;
    } else {
      p.income += Math.abs(amountOf(r));
    }
  }
  const cash = tx.length ? cashFlow(tx).reduce((a, b) => a + b, 0) : 0;
  return { positions: [...book.values()], cash };
}

const nz = (x) => (Number.isFinite(x) ? x : null);

/**
 * Текущая оценка. quotes: {SECID: {price, name, lot}} (price = null/NaN — цены нет).
 * rows: {secid, name, qty, avgCost, price, value, invested, unrealized, realized, income, share, ret};
 * price/value/unrealized/share/ret = null, если не определены (NaN в Python).
 */
export function valuation(tx, quotes = {}) {
  const { positions: pos, cash } = positions(tx);
  const rows = [];
  for (const p of pos) {
    if (p.qty <= 1e-9 && p.realized === 0 && p.income === 0) continue;
    const q = quotes[p.secid];
    const price = q && !isBlank(q.price) ? Number(q.price) : NaN;
    const mv = p.qty * price;
    rows.push({
      secid: p.secid, name: (q && q.name) || '', qty: p.qty, avgCost: p.avgCost, price, value: mv,
      invested: p.invested, unrealized: p.qty > 0 ? mv - p.invested : 0, realized: p.realized, income: p.income,
    });
  }
  const total = rows.reduce((a, r) => a + (Number.isFinite(r.value) ? r.value : 0), 0) + cash;
  for (const r of rows) {
    r.share = total ? r.value / total : NaN;
    r.ret = r.invested > 0 ? (r.unrealized + r.realized + r.income) / r.invested : NaN;
    for (const k of ['price', 'value', 'unrealized', 'share', 'ret']) r[k] = nz(r[k]);
  }
  return { rows, cash };
}

/**
 * Ежедневная стоимость портфеля Σ qty_t × P_t + cash_t и TWR (порт value_history).
 * prices — Frame {dates, cols} (руб./бумага). Результат: {dates, value, twr, flows, cash, missingPrices}
 * (value/twr — NaN в днях без определённой стоимости; flows/cash — как в Python).
 * Правила пропусков цен: ffill, затем bfill только ведущих пропусков; удерживаемая бумага без единой цены
 * -> value = NaN, twr = NaN; бумаги, которых нет в prices, в стоимость не входят.
 */
export function valueHistory(tx, prices) {
  const out = (o = {}) => ({ dates: [], value: [], twr: [], flows: [], cash: [], missingPrices: [], ...o });
  if (!tx.length) return out();
  const rows = tx.map((r) => ({ ...r, date: day(r.date) }));
  const start = rows.reduce((a, r) => (r.date < a ? r.date : a), rows[0].date);
  const set = new Set(prices.dates.filter((d) => d >= start));
  for (const r of rows) set.add(r.date);
  const dates = [...set].sort();
  const n = dates.length, at = new Map(dates.map((d, i) => [d, i]));
  const cf = cashFlow(rows);
  const cashD = new Array(n).fill(0);
  rows.forEach((r, i) => { cashD[at.get(r.date)] += cf[i]; });
  const cash = []; let c = 0;
  for (let i = 0; i < n; i++) { c += cashD[i]; cash.push(c); }
  // количество по бумагам: накопленная сумма dq
  const dqBy = new Map();
  for (const r of rows) {
    const dq = r.type === 'BUY' ? Number(r.qty) : r.type === 'SELL' ? -Number(r.qty) : 0;
    if (dq === 0 || !r.secid || !(r.secid in prices.cols)) continue;
    if (!dqBy.has(r.secid)) dqBy.set(r.secid, new Array(n).fill(0));
    dqBy.get(r.secid)[at.get(r.date)] += dq;
  }
  const pAt = new Map(prices.dates.map((d, i) => [d, i]));
  const cols = [...dqBy.keys()].sort();
  const value = cash.slice(), missing = [];
  const ok = new Array(n).fill(true);
  for (const s of cols) {
    const src = prices.cols[s];
    const px = dates.map((d) => { const k = pAt.get(d); const v = k === undefined ? NaN : src[k]; return isBlank(v) ? NaN : v; });
    for (let i = 1; i < n; i++) if (Number.isNaN(px[i])) px[i] = px[i - 1];
    let first = px.findIndex((v) => !Number.isNaN(v));
    for (let i = 0; i < first; i++) px[i] = px[first];
    const dq = dqBy.get(s);
    let q = 0, gap = false;
    // value = сумма по бумагам (в алфавитном порядке тикеров) + cash — как pos.sum(axis=1) + cash
    for (let i = 0; i < n; i++) {
      q += dq[i];
      const held = Math.abs(q) > 1e-9;
      if (held && Number.isNaN(px[i])) { gap = true; ok[i] = false; }
      dq[i] = held ? q * px[i] : 0;
    }
    if (gap) missing.push(s);
  }
  const sumPos = new Array(n).fill(0);
  for (const s of cols) { const a = dqBy.get(s); for (let i = 0; i < n; i++) sumPos[i] += a[i]; }
  for (let i = 0; i < n; i++) value[i] = ok[i] ? sumPos[i] + cash[i] : NaN;
  const flowsD = new Array(n).fill(0);
  for (const f of externalFlows(rows)) flowsD[at.get(f.date)] += f.amount;
  const cum = []; let fc = 0;
  for (let i = 0; i < n; i++) { fc += flowsD[i]; cum.push(fc); }
  // TWR: r_t = (V_t − F_t) / V_{t−1} − 1 по дням с известной стоимостью, F — потоки с прошлого такого дня
  const twr = new Array(n).fill(NaN);
  let prevV = NaN, prevCum = NaN, acc = 1;
  for (let i = 0; i < n; i++) {
    if (!ok[i]) continue;
    let r = 0;
    if (prevV > 0) r = (value[i] - (cum[i] - prevCum)) / prevV - 1;
    acc *= 1 + (Number.isNaN(r) ? 0 : r);
    twr[i] = acc;
    prevV = value[i]; prevCum = cum[i];
  }
  return out({ dates, value, twr, flows: flowsD, cash, missingPrices: missing.sort() });
}

// ------------------------------------------------------------------ CSV
function parseNumber(s) {
  let t = String(s).replace(/[\s ]/g, '');
  if (t.includes(',') && t.includes('.')) t = t.lastIndexOf(',') > t.lastIndexOf('.') ? t.replace(/\./g, '').replace(',', '.') : t.replace(/,/g, '');
  else t = t.replace(',', '.');
  return /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(t) ? Number(t) : NaN;
}

function splitCsv(text, delim) {
  const rows = [];
  let row = [], f = '', q = false, line = 1, startLine = 1;
  const push = () => { row.push(f); f = ''; };
  const end = () => { push(); if (row.some((x) => x.trim() !== '')) rows.push({ cells: row, line: startLine }); row = []; startLine = line; };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; }
      else { if (ch === '\n') line++; f += ch; }
    } else if (ch === '"' && f === '') q = true;
    else if (ch === delim) push();
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; line++; end(); startLine = line; }
    else f += ch;
  }
  if (f !== '' || row.length) end();
  return rows;
}

/**
 * Проверка операции (после нормализации) -> список проблем по-русски; пусто — всё в порядке.
 * Строже Python: покупка/продажа/дивиденд/купон без тикера и операции с нулевой суммой считаются ошибкой.
 */
export function validateTx(tx) {
  const errs = [];
  if (isBlank(tx.date)) errs.push('не указана дата');
  else if (!parseDate(tx.date)) errs.push(`некорректная дата «${tx.date}» (нужен формат ГГГГ-ММ-ДД)`);
  const type = String(tx.type ?? '').toUpperCase();
  if (!(type in TX_TYPES)) errs.push(`неизвестный тип операции «${tx.type ?? ''}» (допустимо: ${Object.keys(TX_TYPES).join(', ')})`);
  for (const k of ['qty', 'price', 'amount', 'fee']) {
    if (!isBlank(tx[k]) && !Number.isFinite(Number(tx[k]))) errs.push(`нечисловое значение «${tx[k]}» в поле ${k}`);
  }
  if (errs.length) return errs;
  if (['BUY', 'SELL', 'DIVIDEND', 'COUPON'].includes(type) && !tx.secid) errs.push(`для операции ${type} нужен тикер`);
  if (Number(tx.qty) < 0) errs.push('количество не может быть отрицательным');
  if (Number(tx.price) < 0) errs.push('цена не может быть отрицательной');
  if ((type === 'BUY' || type === 'SELL') && !(Number(tx.qty) > 0)) errs.push('для покупки/продажи нужно количество больше нуля');
  if (!(amountOf({ ...tx, type }) > 0 || amountOf({ ...tx, type }) < 0)) errs.push('не указана сумма (или количество и цена)');
  return errs;
}

/** Строка CSV-объекта {date,type,secid,qty,price,amount,fee,note} -> нормализованная операция (как Ledger.add). */
export function normalizeTx(raw) {
  const qty = isBlank(raw.qty) ? 0 : Number(raw.qty);
  const price = isBlank(raw.price) ? 0 : Number(raw.price);
  return {
    date: parseDate(raw.date) || raw.date, type: String(raw.type ?? '').toUpperCase(),
    secid: String(raw.secid ?? '').trim().toUpperCase() || null, qty, price,
    amount: isBlank(raw.amount) ? qty * price : Number(raw.amount),
    fee: isBlank(raw.fee) ? 0 : Number(raw.fee), note: String(raw.note ?? ''),
  };
}

/**
 * Импорт CSV: столбцы date,type,secid,qty,price,amount,fee,note (date и type обязательны, остальные — по желанию,
 * порядок любой, лишние игнорируются). Разделитель , ; или табуляция, BOM, десятичная запятая, кавычки.
 * Пустая сумма = qty × price. Ошибки — одним Error со списком «Строка N: …».
 */
export function parseCsv(text) {
  const src = String(text ?? '').replace(/^﻿/, '');
  const head = src.split(/\r?\n/, 1)[0];
  const delim = head.includes(';') ? ';' : head.includes('\t') && !head.includes(',') ? '\t' : ',';
  const rows = splitCsv(src, delim);
  if (!rows.length) throw new Error('Файл пуст: нет строки заголовка');
  const cols = rows[0].cells.map((h) => h.trim().toLowerCase());
  for (const need of ['date', 'type']) {
    if (!cols.includes(need)) throw new Error(`В заголовке CSV нет столбца «${need}». Ожидается: ${CSV_COLUMNS.join(',')}`);
  }
  const errors = [], out = [];
  for (const { cells, line } of rows.slice(1)) {
    const raw = {}, bad = [];
    cols.forEach((c, i) => { if (CSV_COLUMNS.includes(c)) raw[c] = (cells[i] ?? '').trim(); });
    for (const k of ['qty', 'price', 'amount', 'fee']) {
      if (isBlank(raw[k])) { raw[k] = null; continue; }
      const v = parseNumber(raw[k]);
      if (Number.isNaN(v)) bad.push(`нечисловое значение «${raw[k]}» в столбце ${k}`);
      raw[k] = v;
    }
    const tx = normalizeTx(raw);
    const problems = bad.length ? bad : validateTx(tx);
    if (problems.length) errors.push(`Строка ${line}: ${problems.join('; ')}`);
    else out.push(tx);
  }
  if (errors.length) throw new Error(`Не удалось прочитать CSV.\n${errors.slice(0, 20).join('\n')}${errors.length > 20 ? `\n… и ещё ${errors.length - 20}` : ''}`);
  return out;
}

/** Операции -> CSV (запятая, точка как десятичный разделитель, заголовок date,type,secid,qty,price,amount,fee,note). */
export function toCsv(tx) {
  // защита от CSV-инъекции: строки (не числа), начинающиеся с = + - @ \t \r, получают префикс '
  const q = (v) => { let s = isBlank(v) ? '' : String(v); if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = `'${s}`; return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [CSV_COLUMNS.join(','), ...tx.map((r) => CSV_COLUMNS.map((c) => q(c === 'date' ? day(r.date) : r[c])).join(','))].join('\n') + '\n';
}

/**
 * Сводка портфеля для страницы «Мой портфель».
 * valuationRows — rows из valuation(); cash — денежный остаток; flows — externalFlows(tx); today — ISO-дата для XIRR (необязательно).
 * -> {total, netIn, gain, gainPct, xirrFlows, alloc}: total = Σ конечных value + cash; netIn = Σ flows.amount; gain = total − netIn;
 * gainPct = gain / netIn (null при netIn = 0); xirrFlows = [{date, amount: −flow}…, {date: today, amount: total}] (без последнего, если today не задан);
 * alloc = [{label, value, share}] по возрастанию value: бумаги с value > 0 и «Деньги» при cash > 0; share = value / Σ alloc.
 */
export function portfolioSummary(valuationRows, cash = 0, flows = [], today = null) {
  const rows = valuationRows || [];
  const total = rows.reduce((a, r) => a + (Number.isFinite(r.value) ? r.value : 0), 0) + (Number(cash) || 0);
  const netIn = flows.reduce((a, f) => a + f.amount, 0);
  const gain = total - netIn;
  const xirrFlows = flows.map((f) => ({ date: f.date, amount: -f.amount }));
  if (today) xirrFlows.push({ date: today, amount: total });
  const alloc = rows.filter((r) => r.value > 0).map((r) => ({ label: r.secid, value: r.value }));
  if (cash > 0) alloc.push({ label: 'Деньги', value: cash });
  alloc.sort((a, b) => a.value - b.value);
  const sum = alloc.reduce((a, x) => a + x.value, 0);
  for (const a of alloc) a.share = a.value / sum;
  return { total, netIn, gain, gainPct: netIn ? gain / netIn : null, xirrFlows, alloc };
}

/** TWR-ряд в процентах (база 100 берёт график): [x × 100]. NaN остаётся NaN. */
export const twrPercent = (twr) => (twr || []).map((x) => x * 100);
