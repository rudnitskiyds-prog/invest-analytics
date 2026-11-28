/**
 * «Пенсионная индустрия»: загрузка базы НПФ (public/data/npf/) и ВСЕ расчёты раздела (web/CONTRACT.md,
 * раздел «Пенсионная индустрия»). Чистые функции без DOM; методика 1:1 с присланным приложением
 * «Монитор портфелей НПФ»: АУМ — активы до вычета обязательств, доли — % от АУМ, срез месяца — последний
 * внутри календарного месяца, пропуски не заполняются. Деньги — в рублях, доли — в процентах (0–100).
 *
 * `kind` везде: 0 — пенсионные резервы, 1 — пенсионные накопления, 'all' — вместе (допускаются '0'/'1').
 * `src`: 'comp' — расчёт по составу портфеля, 'struct' — отчёт фонда о структуре. `grp`: 'class' | 'sector'.
 * Состояние страницы (месяц, фонд, вкладки) хранит интерфейс; при смене kind/src он передаёт month = null,
 * и функция выбирает месяц по умолчанию сама.
 */
import { DEFAULT_DATA_BASE } from './data.js';

export const KN = ['Резервы', 'Накопления'];
export const KNL = ['Пенсионные резервы', 'Пенсионные накопления'];
export const RES = 16;   // класс «разница с итогом / нераскрытый остаток»
export const OFZ = 0;    // класс «ОФЗ и госбумаги»
const MON = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
export const dmy = (s) => s.split('-').reverse().join('.');
export const monthLabel = (m) => MON[+m.slice(5, 7) - 1] + ' ' + m.slice(0, 4);
export const shortName = (name) => name.replace(/^НПФ\s*/, '').replace(/[«»]/g, '');

// ---------------------------------------------------------------- загрузка
const CONCURRENCY = 4;
const stale = (name) => new Error(`НПФ: файл ${name} устарел или повреждён, обновите страницу`);
/** Число записей куска так, как его считает импорт (scripts/import_npf.py): meta — фонды, остальное — длина массива. */
const recordsOf = (part, body) => (part === 'meta' ? (Array.isArray(body?.funds) ? body.funds.length : -1) : Array.isArray(body) ? body.length : -1);
/**
 * Склеивает куски по manifest.json в исходный объект базы (ключи как в исходном приложении). Куски грузятся ≤ 4
 * параллельно с `?v=<sha256[:8]>` (кэш не подмешивает старые куски); `records` из manifest сверяется с фактической
 * длиной; битый JSON и HTTP-ошибки — понятные сообщения; при первой ошибке остальные запросы отменяются.
 * sha256 из manifest на клиенте не проверяется (дорого) — он для CI/целостности репозитория.
 */
export async function loadNpf({ fetchImpl, base = DEFAULT_DATA_BASE, onProgress } = {}) {
  const f = fetchImpl || ((...a) => fetch(...a));
  const dir = base.replace(/\/?$/, '/') + 'npf/';
  const ctl = new AbortController();
  const get = async (name, ver) => {
    const r = await f(dir + name + (ver ? '?v=' + ver : ''), { signal: ctl.signal, cache: ver ? 'default' : 'no-cache' });
    if (!r.ok) throw new Error(`НПФ: ${name} — HTTP ${r.status}`);
    try { return await r.json(); } catch (e) { if (ctl.signal.aborted) throw e; throw new Error(`НПФ: ${name} — некорректный JSON`); }
  };
  try {
    const manifest = await get('manifest.json');
    if (!manifest || !Array.isArray(manifest.files) || !manifest.files.length) throw stale('manifest.json');
    const total = manifest.files.reduce((a, x) => a + x.bytes, 0);
    let done = 0, next = 0;
    const parts = new Array(manifest.files.length);
    const worker = async () => {
      while (next < manifest.files.length && !ctl.signal.aborted) {
        const i = next++, x = manifest.files[i];
        const body = await get(x.name, String(x.sha256 || manifest.built || '').slice(0, 8));
        if (recordsOf(x.part, body) !== x.records) throw stale(x.name);
        done += x.bytes;
        if (onProgress) onProgress({ file: x.name, loaded: done, total });
        parts[i] = [x, body];
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, manifest.files.length) }, worker));
    const db = { comp: [] };
    for (const [x, body] of parts) {      // порядок кусков — как в manifest
      if (x.part === 'comp') db.comp.push(...body);
      else if (x.part === 'meta') Object.assign(db, body);
      else db[x.part] = body;
    }
    return db;
  } catch (e) { ctl.abort(); throw e; }
}
let cache = null;
/** loadNpf с кэшем в памяти (один раз за сессию; параллельные вызовы делят один набор запросов) → индекс. */
export function getNpf(opts = {}) {
  if (cache) return cache;
  const p = cache = loadNpf(opts).then(buildIndex);
  p.catch(() => { if (cache === p) cache = null; });
  return p;
}
export function clearNpfCache() { cache = null; }

// ---------------------------------------------------------------- арифметика над деньгами и долями (для UI)
const fin = (x) => typeof x === 'number' && Number.isFinite(x);
/** Доля значения от АУМ, %: value / aum × 100; null при aum ≤ 0/NaN/null или нечисловом value. */
export const rowShare = (value, aum) => (fin(value) && fin(aum) && aum > 0 ? value / aum * 100 : null);
/** Доля суммы набора значений (числа или объекты с полем value) от АУМ, %. */
export const groupShare = (items, aum) => rowShare((items || []).reduce((a, x) => a + (typeof x === 'number' ? x : x?.value || 0), 0), aum);
/** Доля топ-N элементов списка (отсортирован по убыванию value) от АУМ, %. */
export const topShare = (list, n, aum) => groupShare((list || []).slice(0, n), aum);
/** Изменение между двумя значениями (АУМ на двух датах): { abs: b − a, pct: (b/a − 1)·100 }; null, если a или b не число; pct = null при a = 0. */
export function compareDelta(a, b) {
  if (!fin(a) || !fin(b)) return null;
  return { abs: b - a, pct: a ? (b / a - 1) * 100 : null };
}
/** Разность b − a (долей в п. п. или стоимостей в ₽); null, если любой аргумент не число. */
export const diffOf = (a, b) => (fin(a) && fin(b) ? b - a : null);
/** Длина столбика: |value| / max, 0…1; null при max ≤ 0/NaN. */
export const barFrac = (value, max) => (fin(value) && fin(max) && max > 0 ? Math.min(1, Math.abs(value) / max) : null);
export const RECON_THRESHOLD = 2;
/** Порог расхождения сверки: { diff, abs, flag } (flag — |diff| ≥ threshold п. п., по умолчанию 2); null, если diff не число. */
export const reconFlag = (diff, threshold = RECON_THRESHOLD) => (fin(diff) ? { diff, abs: Math.abs(diff), flag: Math.abs(diff) >= threshold } : null);

// ---------------------------------------------------------------- индекс
const push = (m, k, v) => { let a = m.get(k); if (!a) m.set(k, a = []); a.push(v); };
/** Индексы по (фонд, вид, месяц/дата). Дальше все функции принимают ix первым аргументом. */
export function buildIndex(db) {
  const compBy = new Map(), structBy = new Map(), compM = new Map(), structM = new Map();
  db.comp.forEach((c) => { push(compBy, c.f + '|' + c.k, c); compM.set(c.f + '|' + c.k + '|' + c.d.slice(0, 7), c); });
  const struct = db.struct.map((s) => {
    const sh = {};
    for (let i = 0; i < s.r.length; i += 2) sh[s.r[i]] = s.r[i + 1];
    return { ...s, sh };
  });
  struct.forEach((s) => { push(structBy, s.f + '|' + s.k, s); structM.set(s.f + '|' + s.k + '|' + s.d.slice(0, 7), s); });
  const months = [...new Set([...db.comp.map((c) => c.d.slice(0, 7)), ...struct.map((s) => s.d.slice(0, 7))])].sort();
  return { db, F: db.funds, CL: db.classes, SEC: db.sectors, INST: db.inst, struct, months, compBy, structBy, compM, structM };
}

export const fundKinds = (ix, f) => [0, 1].filter((k) => ix.compBy.has(f + '|' + k));
export const fundSKinds = (ix, f) => [0, 1].filter((k) => ix.structBy.has(f + '|' + k));
export function kindsOf(ix, f, kind, src) {
  const av = src === 'struct' ? fundSKinds(ix, f) : fundKinds(ix, f);
  return kind === 'all' ? av : av.includes(+kind) ? [+kind] : [];
}

// ---------------------------------------------------------------- состав портфеля
/** Складывает срезы резервов и накоплений (или один срез) в один объект: c/s — суммы по классам/секторам. */
export function combine(ix, parts) {
  if (!parts.length || parts.some((p) => !p)) return null;
  const notes = ix.db.notes;
  const o = { parts, date: parts[0].d, c: {}, s: {}, aum: 0, est: false, nav: 0, navL: '', li: null, agg: parts.some((p) => p.agg) };
  for (const p of parts) {
    for (const k in p.c) o.c[k] = (o.c[k] || 0) + p.c[k];
    for (const k in p.s) o.s[k] = (o.s[k] || 0) + p.s[k];
    if (p.nf || o.aum === null) o.aum = null; else o.aum += p.t;
    if (p.e) o.est = true;
    if (p.nav == null || o.nav === null) {
      o.nav = null;
      o.navL = parts.length > 1 ? 'СЧА не подтверждена для одного или обоих портфелей' : notes[p.nl];
    } else {
      o.nav += p.nav;
      o.navL = parts.length > 1 ? 'Резервы + накопления · по итогам отчётов' : notes[p.nl];
    }
    if (p.li != null) o.li = (o.li || 0) + p.li;
  }
  o.sum = parts.reduce((a, p) => a + (p.t || 0), 0);
  return o;
}
/** Строки активов объединённого среза. */
export function rowsOf(ix, o) {
  const { db, INST } = ix, out = [];
  for (const p of o.parts) {
    const r = p.r;
    for (let i = 0; i < r.length; i += 4) {
      const it = INST[r[i]];
      out.push({ inst: r[i], name: db.names[it[0]], isin: db.isins[it[1]], issuer: db.issuers[it[2]], cls: it[3], sec: it[4], g: it[5],
        value: r[i + 1], qty: r[i + 2], page: db.pages[r[i + 3]], k: p.k, src: db.srcs[p.src] });
    }
  }
  return out;
}
export const isIsin = (s) => /^[A-Z]{2}[A-Z0-9]{10}$/.test(s);
export const posKey = (r) => r.cls + '|' + (isIsin(r.isin) ? r.isin : r.g + '|' + r.name);
/** Группировка строк с суммированием стоимости, по убыванию. */
export function groupSum(rows, key) {
  const m = new Map();
  for (const r of rows) { const k = key(r); let o = m.get(k); if (!o) m.set(k, o = { ...r, value: 0, n: 0 }); o.value += r.value; o.n++; }
  return [...m.values()].sort((a, b) => b.value - a.value);
}
export function monthComp(ix, f, kind, m) {
  const ks = kindsOf(ix, f, kind, 'comp');
  return ks.length ? combine(ix, ks.map((k) => ix.compM.get(f + '|' + k + '|' + m))) : null;
}
/** Последний состав не позже месяца m, но не глубже 12 месяцев назад. */
export function lastComp(ix, f, kind, m) {
  const ks = kindsOf(ix, f, kind, 'comp');
  if (!ks.length) return null;
  const mi = ix.months.indexOf(m);
  for (let i = mi; i >= 0 && i > mi - 13; i--) {
    const c = combine(ix, ks.map((k) => ix.compM.get(f + '|' + k + '|' + ix.months[i])));
    if (c) return c;
  }
  return null;
}
/** Доли (% от АУМ) по классам или секторам: {индекс: %}. Пусто, если АУМ неизвестен. */
export function sharesComp(o, grp) {
  const src = grp === 'sector' ? o.s : o.c, out = {};
  if (o.aum) for (const k in src) out[k] = src[k] / o.aum * 100;
  return out;
}

// ---------------------------------------------------------------- рынок
function marketRows(ix, m, src, kind) {
  const out = [];
  ix.F.forEach((f, i) => {
    let sh = null, rv = false, d = null;
    if (src === 'struct') {
      if (kind === 'all') return;
      const s = ix.structM.get(i + '|' + kind + '|' + m);
      if (s) { sh = s.sh; rv = !!s.rv; d = s.d; }
    } else {
      const c = monthComp(ix, i, kind, m);
      if (c && c.aum) { sh = sharesComp(c, 'class'); d = c.date; }
    }
    const lc = lastComp(ix, i, kind, m);
    if (sh || lc) out.push({ i, f, sh, rv, d, aum: lc?.aum ?? null, aumD: lc?.date, est: lc?.est });
  });
  return out;
}
const countMonth = (ix, src, kind, m) => ix.F.reduce((a, f, i) => a + ((src === 'struct' ? ix.structM.has(i + '|' + kind + '|' + m) : !!monthComp(ix, i, kind, m)) ? 1 : 0), 0);
/** Месяцы, где есть хотя бы один фонд: [{month, count}] по возрастанию. */
export function marketMonths(ix, kind, src) {
  if (kind === 'all' && src === 'struct') src = 'comp';
  return ix.months.map((month) => ({ month, count: countMonth(ix, src, kind, month) })).filter((x) => x.count > 0);
}
/**
 * Страница «Рынок»: строки по фондам (sh — доли по 17 классам, aum — АУМ последнего состава не позже месяца),
 * KPI (активы под наблюдением, взвешенные по АУМ и простые средние доли по классам), топ-12 фондов.
 * kind 'all' + src 'struct' → src станет 'comp' (отчёты о структуре без сумм не складываются).
 */
export function marketOverview(ix, { kind = 0, src = 'struct', month = null } = {}) {
  if (kind === 'all' && src === 'struct') src = 'comp';
  const avail = marketMonths(ix, kind, src);
  if (!avail.length) return { kind, src, month: null, avail, rows: [], withSh: 0, withAum: 0, totAum: 0, wn: 0, wa: 0, weighted: [], simple: [], usedCols: [], maxSh: 1, top: [] };
  if (!month || !avail.some((x) => x.month === month)) {
    const best = Math.max(...avail.map((x) => x.count));
    month = [...avail].reverse().find((x) => x.count >= best * .7)?.month || avail.at(-1).month;
  }
  const rows = marketRows(ix, month, src, kind), withSh = rows.filter((r) => r.sh), withAum = rows.filter((r) => r.aum);
  const totAum = withAum.reduce((a, r) => a + r.aum, 0);
  const wc = {}; let wa = 0, wn = 0;
  ix.F.forEach((f, i) => {
    const c = monthComp(ix, i, kind, month) || (src === 'struct' ? lastComp(ix, i, kind, month) : null);
    if (c && c.aum) { wa += c.aum; wn++; for (const k in c.c) wc[k] = (wc[k] || 0) + c.c[k]; }
  });
  const n = ix.CL.length;
  const weighted = ix.CL.map((c, k) => (wa ? (wc[k] || 0) / wa * 100 : null));
  const simple = ix.CL.map((c, k) => (withSh.length ? withSh.reduce((a, r) => a + (r.sh[k] || 0), 0) / withSh.length : null));
  const usedCols = ix.CL.map((c, k) => withSh.some((r) => Math.abs(r.sh[k] || 0) >= .005));
  const maxSh = Math.max(1, ...withSh.flatMap((r) => ix.CL.map((c, k) => (k === RES ? 0 : Math.abs(r.sh[k] || 0)))));
  const top = [...withAum].sort((a, b) => b.aum - a.aum).slice(0, 12);
  return { kind, src, month, avail, rows: sortMarketRows(ix, rows, { sort: 'aum', dir: -1 }), withSh: withSh.length, withAum: withAum.length, totAum, wn, wa, weighted, simple, usedCols, maxSh, top, nClasses: n };
}
/** Сортировка строк таблицы рынка: sort — 'name' | 'aum' | индекс класса; dir 1 | -1. Пустые — в конец. */
export function sortMarketRows(ix, rows, { sort = 'aum', dir = -1 } = {}) {
  return [...rows].sort((a, b) => {
    if (sort === 'name') return a.f.name.localeCompare(b.f.name, 'ru') * dir;
    const g = (r) => (sort === 'aum' ? r.aum : r.sh?.[sort]);
    const x = g(a), y = g(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (x - y) * dir;
  });
}
/** Структура рынка во времени (по расчёту из составов): столбцы по месяцам от `from`. mode 'share' (%) | 'value' (трлн ₽). */
export function marketSeries(ix, { kind = 0, mode = 'share', from = '2020-12' } = {}) {
  const used = new Set();
  const cols = ix.months.filter((m) => m >= from).map((m) => {
    const v = {}; let aum = 0, n = 0;
    ix.F.forEach((f, fi) => {
      const c = monthComp(ix, fi, kind, m);
      if (c && c.aum) { aum += c.aum; n++; for (const k in c.c) v[k] = (v[k] || 0) + c.c[k]; }
    });
    if (n < 3) return { month: m, n, aum, v: null };
    for (const k in v) { v[k] = mode === 'share' ? v[k] / aum * 100 : v[k] / 1e12; if (Math.abs(v[k]) > 1e-9) used.add(+k); }
    return { month: m, n, aum, v };
  });
  return { cols, used: [...used].sort((a, b) => a - b) };
}

// ---------------------------------------------------------------- фонд
export function fundDates(ix, fi, kind, src) {
  const ks = kindsOf(ix, fi, kind, src);
  if (!ks.length) return [];
  if (src === 'struct') return ks.length === 1 ? ix.structBy.get(fi + '|' + ks[0]).map((s) => s.d) : [];
  const sets = ks.map((k) => new Set(ix.compBy.get(fi + '|' + k).map((c) => c.d)));
  return [...sets[0]].filter((d) => sets.every((s) => s.has(d))).sort();
}
export const compAt = (ix, fi, kind, d) => combine(ix, kindsOf(ix, fi, kind, 'comp').map((k) => ix.compBy.get(fi + '|' + k).find((c) => c.d === d)));
export function structAt(ix, fi, kind, d) {
  const ks = kindsOf(ix, fi, kind, 'struct');
  return ks.length === 1 ? ix.structBy.get(fi + '|' + ks[0]).find((s) => s.d === d) : null;
}
/** Доли (%) фонда на дату по текущему источнику; null, если нет. */
export function sharesAt(ix, fi, kind, src, grp, d) {
  if (src === 'comp') { const o = compAt(ix, fi, kind, d); return o && o.aum ? sharesComp(o, grp) : null; }
  const x = structAt(ix, fi, kind, d);
  return x ? x.sh : null;
}
const coverage = (ix, fi, src, k) => {
  const a = (src === 'comp' ? ix.compBy : ix.structBy).get(fi + '|' + k);
  return a ? { kind: k, from: a[0].d, to: a.at(-1).d, count: a.length } : null;
};
/**
 * Страница «Фонд», шапка и KPI. Нормализует вход как исходное приложение: у фонда без состава src → 'struct';
 * kind 'all' не бывает у отчётов о структуре; если на (kind, src) дат нет — подставляется доступный вид средств.
 * Возвращает { fi, fund, kind, src, grp, isComp, dates, idx, date, cur, sr, sh, prevSh, prev, rows, pos, kpi, notes,
 * coverage:{comp,struct}, tabs:{holdings,issuers,recon: bool}, kinds:{comp,struct} } (rows/pos — только для isComp).
 * idx — позиция в dates; вне диапазона → последняя.
 */
export function fundView(ix, { fi, kind = 0, src = 'comp', grp = 'class', idx = -1 } = {}) {
  const f = ix.F[fi], { CL } = ix;
  if (!f.comp) src = 'struct';
  if (kind === 'all' && src === 'struct' && f.comp) src = 'comp';
  let dates = fundDates(ix, fi, kind, src);
  if (!dates.length) {
    const alt = [0, 1].find((k) => (src === 'struct' ? fundSKinds(ix, fi) : fundKinds(ix, fi)).includes(k));
    if (alt != null && kind !== alt) { kind = alt; dates = fundDates(ix, fi, kind, src); }
  }
  if (src === 'struct') grp = 'class';
  if (idx < 0 || idx >= dates.length) idx = dates.length - 1;
  const date = dates[idx] ?? null, isComp = src === 'comp';
  const cur = date && isComp ? compAt(ix, fi, kind, date) : null;
  const sr = date && !isComp ? structAt(ix, fi, kind, date) : null;
  const sh = date ? sharesAt(ix, fi, kind, src, grp, date) : null;
  const prevSh = idx > 0 ? sharesAt(ix, fi, kind, src, grp, dates[idx - 1]) : null;
  const prev = isComp && idx > 0 ? compAt(ix, fi, kind, dates[idx - 1]) : null;
  const { db } = ix;
  const notes = cur ? [...new Set(cur.parts.flatMap((p) => p.n.map((n) => (cur.parts.length > 1 ? KN[p.k] + ': ' : '') + db.notes[n])))]
    : sr ? [...(sr.is || []).map((i) => db.issues[i]), sr.nt != null ? db.notes[sr.nt] : ''].filter(Boolean) : [];
  let kpi = null, rows = null, pos = null;
  if (isComp && cur) {
    rows = rowsOf(ix, cur); pos = groupSum(rows, posKey);
    const top5 = pos.slice(0, 5).reduce((a, r) => a + r.value, 0), ofz = cur.c[OFZ] || 0;
    kpi = {
      aum: cur.aum, est: cur.est, nav: cur.nav, navLabel: cur.navL, agg: cur.agg,
      deltaPct: prev?.aum && cur.aum ? (cur.aum / prev.aum - 1) * 100 : null,
      deltaAbs: prev?.aum && cur.aum ? cur.aum - prev.aum : null, hasPrev: !!prev,
      ofzValue: ofz, ofzShare: cur.aum && !cur.agg ? ofz / cur.aum * 100 : null,
      top5Share: cur.aum ? top5 / cur.aum * 100 : null, nPositions: pos.length, nRows: rows.length,
    };
  } else if (sr) {
    const big = Object.entries(sr.sh).sort((a, b) => b[1] - a[1]), lc = lastComp(ix, fi, kind, date.slice(0, 7));
    kpi = {
      biggestClass: big[0] ? +big[0][0] : null, biggestShare: big[0]?.[1] ?? null, ofz: sr.sh[0] || 0, corp: sr.sh[2] || 0, stocks: sr.sh[6] || 0,
      sum: sr.sum, rv: !!sr.rv, lastAum: lc?.aum ?? null, lastAumDate: lc?.date ?? null,
    };
  }
  return {
    fi, fund: f, kind, src, grp, isComp, dates, idx, date, cur, sr, sh, prevSh, prev, rows, pos, kpi, notes,
    coverage: { comp: fundKinds(ix, fi).map((k) => coverage(ix, fi, 'comp', k)), struct: fundSKinds(ix, fi).map((k) => coverage(ix, fi, 'struct', k)) },
    tabs: { holdings: !!f.comp, issuers: !!f.comp, recon: !!(f.comp && f.mid) },
    kinds: { comp: fundKinds(ix, fi), struct: fundSKinds(ix, fi) },
  };
}
/** Классы/сектора со значением ≥ 0,005 п. п., по убыванию: [{i, cat, share}] (кольцевая диаграмма «Структура на дату»). */
export function mixList(ix, view) {
  const CA = view.grp === 'sector' ? ix.SEC : ix.CL;
  return view.sh ? CA.map((c, i) => ({ i, cat: c, share: view.sh[i] || 0 })).filter((x) => Math.abs(x.share) >= .005).sort((a, b) => b.share - a.share) : [];
}
/** Изменение долей к предыдущему срезу (п. п.), топ-6 по модулю. */
export function fundChanges(ix, view) {
  const CA = view.grp === 'sector' ? ix.SEC : ix.CL;
  return view.sh && view.prevSh
    ? CA.map((c, i) => ({ i, cat: c, delta: (view.sh[i] || 0) - (view.prevSh[i] || 0) })).filter((x) => Math.abs(x.delta) >= .005).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 6)
    : [];
}
/** «Как менялась структура»: столбец на каждую дату; stack 'share' (%) | 'value' (млрд ₽, только по составу). */
export function fundStructureSeries(ix, view, { stack = 'share' } = {}) {
  const used = new Set();
  const cols = view.dates.map((date) => {
    let v = null;
    if (view.isComp) {
      const o = compAt(ix, view.fi, view.kind, date);
      if (o && o.aum) { v = {}; const src = view.grp === 'sector' ? o.s : o.c; for (const k in src) v[k] = stack === 'share' ? src[k] / o.aum * 100 : src[k] / 1e9; }
    } else { const x = sharesAt(ix, view.fi, view.kind, 'struct', 'class', date); if (x) v = { ...x }; }
    if (v) for (const k in v) if (Math.abs(v[k]) > 1e-9) used.add(+k);
    return { date, v };
  });
  return { cols, used: [...used].sort((a, b) => a - b) };
}
/** Стоимость портфеля во времени (метрика 'aum' | 'nav'), по виду средств: { xs, series:[{k,name,v}], scale, unit }. */
export function fundVolumeSeries(ix, view, { metric = 'aum' } = {}) {
  const fi = view.fi, ks = fundKinds(ix, fi);
  const all = [...new Set(ks.flatMap((k) => ix.compBy.get(fi + '|' + k).map((c) => c.d)))].sort();
  const val = (c) => (!c ? null : metric === 'nav' ? (c.nav ?? null) : (c.nf ? null : c.t));
  const series = ks.map((k) => { const m = new Map(ix.compBy.get(fi + '|' + k).map((c) => [c.d, c])); return { k, name: KN[k], v: all.map((x) => val(m.get(x))) }; });
  const mx = Math.max(0, ...series.flatMap((x) => x.v.filter((v) => v != null))), scale = mx < 2e9 ? 1e6 : 1e9;
  series.forEach((x) => { x.v = x.v.map((v) => (v == null ? null : v / scale)); });
  return { xs: all, series, scale, unit: scale === 1e6 ? 'млн ₽' : 'млрд ₽', max: mx / scale };
}
/** Состав: фильтр по названию/ISIN/эмитенту (q), группе (cat — индекс или ''), сортировка 'desc'|'asc'|'name'. */
export function fundHoldings(ix, view, { q = '', cat = '', sort = 'desc' } = {}) {
  if (!view.rows) return { list: [], total: 0, sum: 0 };
  const gk = (r) => (view.grp === 'sector' ? r.sec : r.cls), ql = q.toLowerCase();
  const list = view.rows.filter((r) => (cat === '' || gk(r) === +cat) && (!ql || (r.name + ' ' + r.isin + ' ' + r.issuer).toLowerCase().includes(ql)));
  list.sort((a, b) => (sort === 'name' ? a.name.localeCompare(b.name, 'ru') : sort === 'asc' ? a.value - b.value : b.value - a.value));
  return { list, total: view.rows.length, sum: list.reduce((a, r) => a + r.value, 0) };
}
/** Концентрация по эмитентам/контрагентам (группировка по ОГРН/ISIN): { list:[{g,name,value,n,cls}], top10 }. */
export function fundIssuers(ix, view) {
  if (!view.rows) return { list: [], top10: 0 };
  const g = new Map();
  for (const r of view.rows) {
    let o = g.get(r.g);
    if (!o) g.set(r.g, o = { g: r.g, name: ix.db.groups[r.g] || r.issuer || r.name, value: 0, n: 0, cls: {} });
    o.value += r.value; o.n++; o.cls[r.cls] = (o.cls[r.cls] || 0) + r.value;
  }
  const list = [...g.values()].sort((a, b) => b.value - a.value);
  return { list, top10: list.slice(0, 10).reduce((a, x) => a + x.value, 0) };
}
/** «Сравнение дат» одного фонда. from/to по умолчанию: 12 дат назад → последняя. */
export function fundCompareDates(ix, view, { from = null, to = null } = {}) {
  const { dates, isComp, fi, kind, grp } = view;
  if (!from || !dates.includes(from)) from = dates[Math.max(0, dates.length - 13)];
  if (!to || !dates.includes(to)) to = dates.at(-1);
  const a = sharesAt(ix, fi, kind, view.src, grp, from), b = sharesAt(ix, fi, kind, view.src, grp, to);
  const ca = isComp ? compAt(ix, fi, kind, from) : null, cb = isComp ? compAt(ix, fi, kind, to) : null;
  const CA = grp === 'sector' ? ix.SEC : ix.CL;
  const val = (o, i) => (o ? ((grp === 'sector' ? o.s : o.c)[i] || 0) : null);
  const rows = a && b ? CA.map((c, i) => ({ i, cat: c, a: a[i] || 0, b: b[i] || 0, valA: isComp ? val(ca, i) : null, valB: isComp ? val(cb, i) : null }))
    .filter((r) => Math.abs(r.a) >= .005 || Math.abs(r.b) >= .005).sort((x, y) => Math.abs(y.b - y.a) - Math.abs(x.b - x.a)) : [];
  return { from, to, ca, cb, rows, maxDelta: Math.max(.01, ...rows.map((r) => Math.abs(r.b - r.a))) };
}
/** Сверка «состав против отчёта о структуре» за месяц; kind 'all' не поддерживается (supported:false). */
export function fundRecon(ix, { fi, kind, month }) {
  if (kind === 'all') return { supported: false };
  const k = +kind, c = ix.compM.get(fi + '|' + k + '|' + month), x = ix.structM.get(fi + '|' + k + '|' + month);
  const hist = ix.months.map((mm) => {
    const cc = ix.compM.get(fi + '|' + k + '|' + mm), xx = ix.structM.get(fi + '|' + k + '|' + mm);
    if (!cc || cc.nf || !xx) return null;
    let mx = 0;
    ix.CL.forEach((cl, i) => { if (i === 15 || i === RES) return; mx = Math.max(mx, Math.abs((cc.c[i] || 0) / cc.t * 100 - (xx.sh[i] || 0))); });
    return mx;
  });
  const i0 = hist.findIndex((v) => v != null), i1 = hist.length - 1 - [...hist].reverse().findIndex((v) => v != null);
  const out = { supported: true, comp: c || null, struct: x || null, rows: [], srRows: [], history: i0 >= 0 ? { xs: ix.months.slice(i0, i1 + 1), v: hist.slice(i0, i1 + 1) } : null };
  if (c && x) {
    out.rows = ix.CL.map((cl, i) => ({ i, cat: cl, a: c.nf ? null : (c.c[i] || 0) / c.t * 100, b: x.sh[i] || 0 }))
      .filter((r) => Math.abs(r.a || 0) >= .005 || Math.abs(r.b) >= .005).map((r) => ({ ...r, diff: r.a == null ? null : r.a - r.b }));
    out.srRows = structSourceRows(ix, x);
  }
  return out;
}
/** Исходные строки отчёта о структуре: [{label, share, cls (−1 — не учитывается)}]. */
export function structSourceRows(ix, x) {
  const out = [];
  for (let i = 0; i < x.sr.length; i += 3) out.push({ label: ix.db.labels[x.sr[i]], share: x.sr[i + 1], cls: x.sr[i + 2] });
  return out;
}
/** Вкладка «Источники»: срезы состава и отчёты о структуре фонда (новые первыми) и статистика сверки итогов. */
export function fundSources(ix, fi) {
  const byDate = (a, b) => b.d.localeCompare(a.d) || a.k - b.k;
  const comps = fundKinds(ix, fi).flatMap((k) => ix.compBy.get(fi + '|' + k)).sort(byDate);
  const structs = fundSKinds(ix, fi).flatMap((k) => ix.structBy.get(fi + '|' + k)).sort(byDate);
  const cnt = (s) => comps.filter((c) => c.st === s).length;
  return {
    comps: comps.map((c) => ({ ...c, rowsN: c.r.length / 4, srcName: ix.db.srcs[c.src] })),
    structs: structs.map((x) => ({ ...x, srcName: ix.db.srcs[x.src], issueTexts: (x.is || []).map((i) => ix.db.issues[i]) })),
    stats: { matched: cnt('matched'), rounded: cnt('rounded'), recomputed: cnt('recomputed'), noTotal: cnt('no_total'), rows: comps.reduce((a, c) => a + c.r.length / 4, 0), structAuto: structs.filter((x) => !x.rv).length, structReview: structs.filter((x) => x.rv).length },
  };
}

// ---------------------------------------------------------------- сравнение фондов
/** Фонды по умолчанию: 5 крупнейших по АУМ на asOf (в исходном приложении — 2026-03). */
export function defaultCompareFunds(ix, kind = 0, asOf = '2026-03') {
  const m = ix.months.filter((x) => x <= asOf).at(-1);
  return ix.F.map((f, i) => [i, lastComp(ix, i, kind === 'all' ? 0 : kind, m)?.aum || 0]).sort((a, b) => b[1] - a[1]).slice(0, 5).map((x) => x[0]);
}
/**
 * Страница «Сравнение фондов». metric: 'aum' (млрд ₽, только по составу) или индекс класса ('0'…); src 'comp'|'struct'.
 * funds — индексы фондов (≤ 8; пусто → defaultCompareFunds). Возвращает { funds, src, metric, xs, series:[{fi,name,v}],
 * month, bars:[{fi,sh,dt}], used:[индексы классов] }.
 */
export function compareFunds(ix, { funds = [], kind = 0, metric = 'aum', src = 'struct', month = null } = {}) {
  if (kind === 'all' && src === 'struct') src = 'comp';
  if (!funds.length) funds = defaultCompareFunds(ix, kind);
  const isAum = metric === 'aum', ci = +metric;
  if (isAum) src = 'comp';
  const val = (fi, m) => {
    if (isAum || src === 'comp') { const c = monthComp(ix, fi, kind, m); if (!c || !c.aum) return null; return isAum ? c.aum / 1e9 : (c.c[ci] || 0) / c.aum * 100; }
    const x = ix.structM.get(fi + '|' + kind + '|' + m);
    return x ? (x.sh[ci] || 0) : null;
  };
  const series = funds.map((fi) => ({ fi, name: shortName(ix.F[fi].name), v: ix.months.map((m) => val(fi, m)) }));
  const has = ix.months.map((m, i) => series.some((x) => x.v[i] != null)), i0 = has.indexOf(true), i1 = has.lastIndexOf(true);
  const xs = i0 < 0 ? [] : ix.months.slice(i0, i1 + 1);
  series.forEach((x) => { x.v = i0 < 0 ? [] : x.v.slice(i0, i1 + 1); });
  if (!month || !xs.includes(month)) month = [...xs].reverse().find((m) => series.filter((x) => x.v[xs.indexOf(m)] != null).length >= Math.min(series.length, 2)) || xs.at(-1) || null;
  const bars = funds.map((fi) => {
    let sh = null, dt = null;
    if (src === 'comp') { const c = monthComp(ix, fi, kind, month); if (c && c.aum) { sh = sharesComp(c, 'class'); dt = c.date; } }
    else { const x = ix.structM.get(fi + '|' + kind + '|' + month); if (x) { sh = x.sh; dt = x.d; } }
    return { fi, sh, dt };
  });
  const used = new Set();
  bars.forEach((b) => b.sh && Object.keys(b.sh).forEach((k) => Math.abs(b.sh[k]) >= .05 && used.add(+k)));
  return { funds, src, metric, xs, series, month, bars, used: [...used].sort((a, b) => a - b) };
}

// ---------------------------------------------------------------- бумаги и эмитенты
/** Ключ бумаги: по ISIN, иначе по эмитенту и названию. */
export const instKey = (ix, it) => (isIsin(ix.db.isins[it[1]]) ? 'i:' + ix.db.isins[it[1]] : 'n:' + it[5] + '|' + it[0]);
const kindList = (kind) => (kind === 'all' ? [0, 1] : [+kind]);
/** Все строки активов всех фондов за месяц и суммарный АУМ: { rows:[{fi,k,c,inst,value,qty}], total }. */
export function monthHoldings(ix, kind, m) {
  const out = []; let total = 0;
  ix.F.forEach((f, fi) => kindList(kind).forEach((k) => {
    const c = ix.compM.get(fi + '|' + k + '|' + m);
    if (!c) return;
    if (!c.nf) total += c.t;
    const r = c.r;
    for (let i = 0; i < r.length; i += 4) out.push({ fi, k, c, inst: r[i], value: r[i + 1], qty: r[i + 2] });
  }));
  return { rows: out, total };
}
export function securitiesMonths(ix, kind) {
  const ks = kindList(kind);
  return ix.months.map((month) => ({ month, count: ix.F.reduce((a, f, i) => a + (ks.some((k) => ix.compM.has(i + '|' + k + '|' + month)) ? 1 : 0), 0) })).filter((x) => x.count > 0);
}
function pickSecMonth(avail, month) {
  const best = Math.max(...avail.map((x) => x.count));
  if (!month || !avail.some((x) => x.month === month)) return [...avail].reverse().find((x) => x.count >= best * .8)?.month ?? null;
  return month;
}
/**
 * Список эмитентов и бумаг за месяц (без выбранного объекта). q — поиск по названию/ISIN/эмитенту (без q из бумаг
 * убраны деньги, депозиты, РЕПО, дебиторка). Возвращает { avail, month, nFunds, total, groups:[{id,name,value,nFunds,cls}],
 * instruments:[{key,it,name,isin,issuer,cls,sec,g,value,nFunds}], historical:[{key,it}] }.
 */
export function securitiesOverview(ix, { kind = 0, month = null, q = '' } = {}) {
  const avail = securitiesMonths(ix, kind);
  if (!avail.length) return { avail, month: null, nFunds: 0, total: 0, groups: [], instruments: [], historical: [] };
  month = pickSecMonth(avail, month);
  const H = monthHoldings(ix, kind, month), { db, INST } = ix;
  const gi = new Map(), ii = new Map();
  for (const r of H.rows) {
    const it = INST[r.inst]; let g = gi.get(it[5]);
    if (!g) gi.set(it[5], g = { id: it[5], value: 0, f: new Set(), cls: {} });
    g.value += r.value; g.f.add(r.fi); g.cls[it[3]] = (g.cls[it[3]] || 0) + r.value;
    const k = instKey(ix, it); let x = ii.get(k);
    if (!x) ii.set(k, x = { key: k, it, value: 0, f: new Set() });
    x.value += r.value; x.f.add(r.fi);
  }
  const ql = q.trim().toLowerCase();
  let gl = [...gi.values()], il = [...ii.values()];
  if (ql) {
    gl = gl.filter((g) => (db.groups[g.id] || '').toLowerCase().includes(ql));
    il = il.filter((x) => (db.names[x.it[0]] + ' ' + db.isins[x.it[1]] + ' ' + db.issuers[x.it[2]]).toLowerCase().includes(ql));
  } else il = il.filter((x) => ![9, 13, 10, 8].includes(x.it[3]));
  gl.sort((a, b) => b.value - a.value); il.sort((a, b) => b.value - a.value);
  let historical = [];
  if (ql && !gl.length && !il.length) {
    const hs = new Map();
    INST.forEach((it) => { if ((db.names[it[0]] + ' ' + db.isins[it[1]] + ' ' + db.issuers[it[2]]).toLowerCase().includes(ql)) hs.set(instKey(ix, it), it); });
    historical = [...hs.entries()].slice(0, 40).map(([key, it]) => ({ key, it }));
  }
  const inst = (x) => ({ key: x.key, it: x.it, name: db.names[x.it[0]], isin: db.isins[x.it[1]], issuer: db.issuers[x.it[2]], cls: x.it[3], sec: x.it[4], g: x.it[5], value: x.value, nFunds: x.f.size });
  return {
    avail, month, nFunds: avail.find((x) => x.month === month).count, total: H.total,
    groups: gl.map((g) => ({ id: g.id, name: db.groups[g.id] || 'Без эмитента', value: g.value, nFunds: g.f.size, cls: g.cls })),
    instruments: il.map(inst), historical,
  };
}
/**
 * Карточка эмитента (sel.t 'g', id — индекс группы) или бумаги (sel.t 'i', id — instKey): история вложений по
 * месяцам, держатели на месяц, инструменты эмитента. null, если объект не найден.
 * Возвращает { title, rep, kindLabel, month, total, totalShare, holders:[{fi,k,c,value,qty,hasQty,n,share}], nHolders,
 * instruments, xs, series:[{name,fi,v}] (первая — «Все фонды», затем 7 крупнейших держателей; млрд ₽),
 * maxTotal, maxMonth, first, last }.
 */
export function securityDetail(ix, { kind = 0, month, sel }) {
  const { db, INST, F, months } = ix, ks = kindList(kind);
  const match = sel.t === 'g' ? (it) => it[5] === sel.id : (it) => instKey(ix, it) === sel.id;
  const flags = new Uint8Array(INST.length); let rep = null;
  INST.forEach((it, i) => { if (match(it)) { flags[i] = 1; rep = rep || it; } });
  if (!rep) return null;
  const title = sel.t === 'g' ? (db.groups[sel.id] || 'Без эмитента') : db.names[rep[0]];
  const totals = months.map(() => 0), nf = months.map(() => 0), byFund = new Map();
  months.forEach((m, mi) => F.forEach((f, fi) => {
    let v = 0, hit = false;
    ks.forEach((k) => {
      const c = ix.compM.get(fi + '|' + k + '|' + m);
      if (!c) return;
      const r = c.r;
      for (let i = 0; i < r.length; i += 4) if (flags[r[i]]) { v += r[i + 1]; hit = true; }
    });
    if (hit) { totals[mi] += v; nf[mi]++; let a = byFund.get(fi); if (!a) byFund.set(fi, a = months.map(() => null)); a[mi] = v; }
  }));
  let i0 = nf.findIndex((n) => n > 0), i1 = nf.length - 1 - [...nf].reverse().findIndex((n) => n > 0);
  const has = i0 >= 0;   // инструмент есть в INST, но ни в одном составе → пустой результат
  if (!has) { i0 = 0; i1 = -1; }
  const xs = months.slice(i0, i1 + 1);
  const H = monthHoldings(ix, kind, month), hold = new Map(), instAgg = new Map();
  for (const r of H.rows) {
    if (!flags[r.inst]) continue;
    const k = r.fi + '|' + r.k; let o = hold.get(k);
    if (!o) hold.set(k, o = { fi: r.fi, k: r.k, c: r.c, value: 0, qty: 0, hasQty: false, n: 0 });
    o.value += r.value; o.n++;
    if (typeof r.qty === 'number') { o.qty += r.qty; o.hasQty = true; }
    const it = INST[r.inst], ik = instKey(ix, it); let x = instAgg.get(ik);
    if (!x) instAgg.set(ik, x = { key: ik, it, value: 0, f: new Set() });
    x.value += r.value; x.f.add(r.fi);
  }
  const hl = [...hold.values()].sort((a, b) => b.value - a.value), tot = hl.reduce((a, x) => a + x.value, 0);
  const topF = [...byFund.entries()].map(([fi, a]) => [fi, Math.max(...a.map((v) => v || 0))]).sort((a, b) => b[1] - a[1]).slice(0, 7);
  const series = [{ name: 'Все фонды', fi: null, v: totals.slice(i0, i1 + 1).map((v, i) => (nf[i0 + i] ? v / 1e9 : null)) },
    ...topF.map(([fi]) => ({ name: shortName(F[fi].name), fi, v: byFund.get(fi).slice(i0, i1 + 1).map((v) => (v == null ? null : v / 1e9)) }))];
  const maxTotal = Math.max(...totals);
  return {
    title, rep, kindLabel: sel.t === 'g' ? 'Эмитент / контрагент' : 'Бумага', month, total: tot, totalShare: rowShare(tot, H.total) ?? 0,
    holders: hl.map((x) => ({ ...x, share: x.c.nf ? null : x.value / x.c.t * 100 })), nHolders: new Set(hl.map((x) => x.fi)).size,
    instruments: [...instAgg.values()].sort((a, b) => b.value - a.value).map((x) => ({ key: x.key, it: x.it, name: db.names[x.it[0]], isin: db.isins[x.it[1]], cls: x.it[3], value: x.value, nFunds: x.f.size })),
    xs, series, maxTotal, maxMonth: has ? months[totals.indexOf(maxTotal)] : null, first: has ? months[i0] : null, last: has ? months[i1] : null,
  };
}
/** Глобальный поиск (≥ 2 символа): фонды (≤6), эмитенты (≤7), бумаги (≤8). */
export function searchGlobal(ix, q) {
  q = (q || '').trim().toLowerCase();
  const out = { funds: [], groups: [], insts: [] };
  if (q.length < 2) return out;
  const { db } = ix;
  out.funds = ix.F.map((f, i) => ({ i, fund: f })).filter((x) => x.fund.name.toLowerCase().includes(q)).slice(0, 6);
  db.groups.forEach((g, i) => { if (out.groups.length < 7 && g && g.toLowerCase().includes(q)) out.groups.push({ i, name: g }); });
  const seen = new Set();
  for (const it of ix.INST) {
    if (out.insts.length >= 8) break;
    const isin = db.isins[it[1]], nm = db.names[it[0]];
    if ((isin && isin.toLowerCase().includes(q)) || nm.toLowerCase().includes(q)) {
      const key = instKey(ix, it);
      if (!seen.has(key)) { seen.add(key); out.insts.push({ it, key, name: nm, isin, cls: it[3] }); }
    }
  }
  return out;
}

// ---------------------------------------------------------------- данные и методика
/**
 * «Данные и методика»: счётчики базы и сетка покрытия по месяцам с 2018-01.
 * coverage: [{fi, cells:[{month, v}]}], v: 'both' | 'comp' | 'struct' | null.
 */
export function dataOverview(ix, kind = 0) {
  const { db, F } = ix, ms = ix.months.filter((m) => m >= '2018-01');
  const has = (map, fi, m) => (kind === 'all' ? (map.has(fi + '|0|' + m) || map.has(fi + '|1|' + m)) : map.has(fi + '|' + kind + '|' + m));
  return {
    built: db.built, monitorUpdated: db.monitorUpdated, nFunds: F.length, nWithComp: F.filter((f) => f.comp).length, nWithStruct: F.filter((f) => f.mid).length,
    nComp: db.comp.length, nCompRows: db.comp.reduce((a, c) => a + c.r.length / 4, 0), nStruct: db.struct.length, nStructReview: db.struct.filter((s) => s.rv).length,
    nInstruments: ix.INST.length, nGroups: db.groups.length, from: ix.months[0], to: ix.months.at(-1), sectorNote: db.sectorNote, aumNote: db.aumNote,
    months: ms,
    coverage: F.map((f, fi) => ({ fi, cells: ms.map((month) => { const c = has(ix.compM, fi, month), s = has(ix.structM, fi, month); return { month, v: c && s ? 'both' : c ? 'comp' : s ? 'struct' : null }; }) })),
  };
}
/** Строки для CSV «Структура всех фондов»: [{fund, kind, date, source, status, shares:[%×17]}]. */
export function structureTable(ix) {
  const { db, F, CL } = ix, rows = [];
  ix.struct.forEach((s) => rows.push({ fund: F[s.f].name, kind: KNL[s.k], date: s.d, source: 'Отчёт о структуре', status: s.rv ? 'требует сверки' : 'авто', shares: CL.map((c, i) => s.sh[i] || 0) }));
  db.comp.forEach((c) => { if (!c.nf) rows.push({ fund: F[c.f].name, kind: KNL[c.k], date: c.d, source: 'Расчёт по составу', status: c.st, shares: CL.map((x, i) => (c.c[i] || 0) / c.t * 100) }); });
  return rows;
}
/** Строки для CSV «АУМ и СЧА»: по каждому срезу состава. */
export function aumTable(ix) {
  const { db, F } = ix;
  return db.comp.map((c) => ({ fund: F[c.f].name, kind: KNL[c.k], date: c.d, aum: c.nf ? null : c.t, est: !!c.e, nav: c.nav ?? null, liabilities: c.li ?? null, reported: c.rep ?? null, rows: c.r.length / 4, status: c.st, file: db.srcs[c.src] }));
}
