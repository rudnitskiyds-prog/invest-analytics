// Календарь ребалансировок и заявки — точный порт core/portfolio/rebalance.py.
// Даты — ISO-строки 'YYYY-MM-DD'. Доли/стоимости/цены — объекты {SECID: число}. Чистые функции.

export const RULE_MONTHS = { M: 1, Q: 3, H: 6, A: 12 };   // ключи как REBAL_LABELS в backtest.js ('none' — без дат)

const pad = (x) => String(x).padStart(2, '0');
const iso = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;
const nan = (x) => (x === null || x === undefined || x === '' ? NaN : Number(x));
const fin = (x) => (Number.isFinite(x) ? x : null);

/** Последний рабочий день (пн–пт, без праздников) месяца m (1..12) — как pd.offsets.BMonthEnd. */
function lastBday(y, m) {
  const dt = new Date(Date.UTC(y, m, 0));   // последний день месяца
  const wd = dt.getUTCDay();
  dt.setUTCDate(dt.getUTCDate() - (wd === 0 ? 2 : wd === 6 ? 1 : 0));
  return dt.toISOString().slice(0, 10);
}

/** Следующие n дат (не раньше start, по умолчанию сегодня): последний рабочий день месяца/квартала/полугодия/года. */
export function schedule(rule, startIso, n = 8) {
  if (!(rule in RULE_MONTHS)) return [];
  const step = RULE_MONTHS[rule];
  let today = startIso ? String(startIso).slice(0, 10) : null;
  if (!today) { const t = new Date(); today = iso(t.getFullYear(), t.getMonth() + 1, t.getDate()); }
  let y = +today.slice(0, 4), m = +today.slice(5, 7);
  const out = [];
  while (out.length < n) {
    if (m % step === 0) { const d = lastBday(y, m); if (d >= today) out.push(d); }
    if (++m > 12) { m = 1; y++; }
  }
  return out;
}

const keysOf = (...objs) => [...new Set(objs.flatMap((o) => Object.keys(o || {})))].sort();

/**
 * Отклонение долей от целевых (current/target — {SECID: доля}).
 * Строки (по тикерам, отсортированы): {secid, current, target, driftPp, relDrift, outOfBand};
 * driftPp — (факт − цель) × 100; relDrift = (факт − цель) / цель при цели > 0, иначе null; outOfBand — |факт − цель| > band.
 */
export function driftReport(current, target, band = 0.05) {
  return keysOf(current, target).map((secid) => {
    const cur = nan(current && current[secid]) || 0, tgt = nan(target && target[secid]) || 0;
    return { secid, current: cur, target: tgt, driftPp: (cur - tgt) * 100,
      relDrift: tgt > 0 ? (cur - tgt) / tgt : null, outOfBand: Math.abs(cur - tgt) > band };
  });
}

/**
 * Заявки: values — стоимость позиций (руб.), target — целевые доли, prices — цена 1 бумаги, lots — размер лота
 * (по умолчанию 1), cash — свободные деньги (распределяются вместе), commission — доля от суммы.
 * Целое число лотов — усечением к нулю. Строки: {secid, action ('Купить'|'Продать'|'—'), lots, qty, price, amount,
 * value, targetValue, shareAfter, commission}; сначала продажи, потом покупки, потом «—»;
 * price/amount/shareAfter/commission = null, если цены нет (NaN в Python). Деление на нулевую стоимость лота даёт 0 лотов.
 */
export function rebalanceOrders(values, target, prices, lots = null, cash = 0, commission = 0) {
  const keys = keysOf(values, target);
  const v = keys.map((k) => nan(values && values[k]) || 0);
  const total = v.reduce((a, b) => a + b, 0) + cash;
  const rows = keys.map((secid, i) => {
    const t = nan(target && target[secid]) || 0;
    const p = nan(prices && prices[secid]);
    const lot = (lots && nan(lots[secid])) || 1;
    const delta = t * total - v[i];
    const ln = Math.trunc(delta / (p * lot));
    const lotsN = Number.isFinite(ln) ? ln : 0;
    const qty = lotsN * lot, amount = qty * p;
    const sum = Math.abs(amount);
    return { secid, action: qty > 0 ? 'Купить' : qty < 0 ? 'Продать' : '—', lots: Math.abs(lotsN), qty: Math.abs(qty),
      price: fin(p), amount: fin(sum), value: v[i], targetValue: t * total,
      shareAfter: total ? fin((v[i] + amount) / total) : null, commission: fin(sum * commission) };
  });
  const rank = { 'Продать': 0, 'Купить': 1, '—': 2 };
  return rows.map((r, i) => [r, i]).sort((a, b) => rank[a[0].action] - rank[b[0].action] || a[1] - b[1]).map((x) => x[0]);
}

const uuid = () => (globalThis.crypto && globalThis.crypto.randomUUID ? globalThis.crypto.randomUUID()
  : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3) | 8).toString(16); }));

// RFC 5545 TEXT: \ , ; и переводы строк; CR убираем
const icsText = (x) => String(x ?? '').replace(/\r\n?/g, '\n').replace(/\\/g, '\\\\').replace(/,/g, '\\,').replace(/;/g, '\\;').replace(/\n/g, '\\n');

/** Календарь .ics: событие на весь день на каждую дату + напоминание за сутки (CRLF, как в Python). */
export function toIcs(dates, portfolio, description = '') {
  const pf = icsText(String(portfolio ?? '').split(/\s+/).filter(Boolean).join(' ')), desc = icsText(description);
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//invest-platform//RU', 'CALSCALE:GREGORIAN'];
  const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15) + 'Z';
  for (const d of dates) {
    const s = String(d).slice(0, 10);
    const nxt = new Date(Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10) + 1)).toISOString().slice(0, 10);
    lines.push('BEGIN:VEVENT', `UID:${uuid()}@invest-platform`, `DTSTAMP:${stamp}`,
      `DTSTART;VALUE=DATE:${s.replace(/-/g, '')}`, `DTEND;VALUE=DATE:${nxt.replace(/-/g, '')}`,
      `SUMMARY:Ребалансировка — ${pf}`, `DESCRIPTION:${desc}`,
      'BEGIN:VALARM', 'TRIGGER:-P1D', 'ACTION:DISPLAY', `DESCRIPTION:Завтра ребалансировка ${pf}`, 'END:VALARM', 'END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  return lines.join('\r\n');
}

/** Нормировка целевых долей {SECID: x} к сумме 1; отрицательные и нечисловые -> 0 (ключи сохраняются); сумма ≤ 0 -> {}. */
export function normalizeTarget(map) {
  const e = Object.entries(map || {}).map(([k, x]) => [k, Number.isFinite(Number(x)) && Number(x) > 0 ? Number(x) : 0]);
  const s = e.reduce((a, [, x]) => a + x, 0);
  return s > 0 ? Object.fromEntries(e.map(([k, x]) => [k, x / s])) : {};
}

/** Равные доли по умолчанию: {SECID: 1/n} по уникальным тикерам (верхний регистр); пусто -> {}. */
export function equalTarget(secids) {
  const ids = [...new Set((secids || []).map((s) => String(s || '').trim().toUpperCase()).filter(Boolean))];
  return Object.fromEntries(ids.map((s) => [s, 1 / ids.length]));
}

/** Фактические доли: rows [{secid, value}] (нечисловая стоимость = 0), cash — свободные деньги входят в знаменатель -> {SECID: value / (Σ + cash)}; знаменатель 0 -> доли 0. */
export function factShares(rows, cash = 0) {
  const v = (rows || []).map((r) => [r.secid, Number.isFinite(r.value) ? r.value : 0]);
  const total = v.reduce((a, [, x]) => a + x, 0) + (Number(cash) || 0);
  return Object.fromEntries(v.map(([k, x]) => [k, total ? x / total : 0]));
}

/** Итоги заявок (строки rebalanceOrders): {buy, sell, fees} — Σ amount по 'Купить' / 'Продать' и Σ commission; null считается 0. */
export function ordersTotals(orders) {
  const by = (a) => (orders || []).filter((o) => o.action === a).reduce((s, o) => s + (o.amount || 0), 0);
  return { buy: by('Купить'), sell: by('Продать'), fees: (orders || []).reduce((s, o) => s + (o.commission || 0), 0) };
}
