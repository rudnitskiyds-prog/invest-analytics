/**
 * Скринеры акций, фондов и облигаций (web/CONTRACT.md, раздел «Скринеры»). Данные считает сборщик
 * (public/data/*.json), здесь — только склейка файлов, фильтр и сортировка; расчётов нет.
 * Проценты — доли (0.12 = 12 %): поля T-Invest и RusETFs, которые приходят в процентах, делятся на 100.
 */
import { getCached, DEFAULT_DATA_BASE } from './data.js';

// Русские названия секторов T-Invest (как SECTOR_LABELS в core/data/public_data.py); неизвестные — как есть
export const SECTOR_LABELS = {
  financial: 'Финансы', energy: 'Энергетика', materials: 'Сырьё', industrials: 'Промышленность',
  utilities: 'Электроэнергетика', telecom: 'Телеком', it: 'ИТ', consumer: 'Потребительский',
  health_care: 'Здравоохранение', real_estate: 'Недвижимость', other: 'Другое', ecomaterials: 'Экоматериалы',
  green_energy: 'Зелёная энергетика', green_buildings: 'Зелёное строительство', electrocars: 'Электромобили',
};
export const BOND_TYPES = { TQOB: 'ОФЗ', TQCB: 'Корпоративная' };

const RISK = [
  { key: 'cagr', label: 'Доходность, % год. (CAGR)', type: 'pct', group: 'Риск и доходность' },
  { key: 'volatility', label: 'Волатильность', type: 'pct', group: 'Риск и доходность' },
  { key: 'max_drawdown', label: 'Макс. просадка', type: 'pct', group: 'Риск и доходность' },
  { key: 'sharpe', label: 'Шарп', type: 'num', group: 'Риск и доходность' },
  { key: 'sortino', label: 'Сортино', type: 'num', group: 'Риск и доходность' },
  { key: 'score', label: 'Рейтинг (0–100)', type: 'num', group: 'Риск и доходность' },
];
const ID = [
  { key: 'secid', label: 'Тикер', type: 'text', group: 'Бумага' },
  { key: 'name', label: 'Название', type: 'text', group: 'Бумага' },
];

export const COLUMNS = {
  stocks: [
    ...ID,
    { key: 'sector', label: 'Сектор', type: 'text', group: 'Бумага' },
    ...RISK,
    { key: 'pe', label: 'P/E', type: 'num', group: 'Мультипликаторы' },
    { key: 'pb', label: 'P/B', type: 'num', group: 'Мультипликаторы' },
    { key: 'ev_ebitda', label: 'EV/EBITDA', type: 'num', group: 'Мультипликаторы' },
    { key: 'roe', label: 'ROE', type: 'pct', group: 'Мультипликаторы' },
    { key: 'dividend_yield', label: 'Див. доходность (12 мес.)', type: 'pct', group: 'Мультипликаторы' },
  ],
  funds: [
    ...ID,
    { key: 'issuer', label: 'Управляющая компания', type: 'text', group: 'Фонд' },
    { key: 'asset_class', label: 'Класс активов', type: 'text', group: 'Фонд' },
    { key: 'commission', label: 'Комиссия, % год.', type: 'pct', group: 'Фонд' },
    { key: 'aum_rub', label: 'СЧА', type: 'rub', group: 'Фонд' },
    ...RISK,
  ],
  bonds: [
    ...ID,
    { key: 'type', label: 'Тип', type: 'text', group: 'Бумага' },
    { key: 'face_currency', label: 'Валюта', type: 'text', group: 'Бумага' },
    { key: 'yield', label: 'Доходность к погашению/оферте', type: 'pct', group: 'Доходность' },
    { key: 'coupon_pct', label: 'Купон, % год.', type: 'pct', group: 'Доходность' },
    { key: 'coupon_freq', label: 'Купонов в год', type: 'num', group: 'Доходность' },
    { key: 'duration_days', label: 'Дюрация, дн.', type: 'num', group: 'Срок' },
    { key: 'maturity_date', label: 'Погашение', type: 'date', group: 'Срок' },
    { key: 'offer_date', label: 'Оферта', type: 'date', group: 'Срок' },
    { key: 'price_pct', label: 'Цена, % номинала', type: 'num', group: 'Торги' },
    { key: 'nkd', label: 'НКД', type: 'rub', group: 'Торги' },
    { key: 'volume_rub', label: 'Оборот за день', type: 'rub', group: 'Торги' },
    { key: 'listing_level', label: 'Уровень листинга', type: 'num', group: 'Торги' },
  ],
};

const num = (v) => {
  if (v === null || v === undefined || v === '') return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
};
const pct = (v) => { const x = num(v); return x === null ? null : x / 100; };
const baseDir = (base) => { const b = base || DEFAULT_DATA_BASE; return b.endsWith('/') ? b : b + '/'; };
const up = (s) => String(s || '').toUpperCase();

/** Файл сборщика или null (нет файла — колонки из него будут null, скринер работает). */
async function load(base, name, fetchImpl) {
  try {
    const j = await getCached(`${baseDir(base)}${name}.json`, fetchImpl, 'json', 2);
    return j && typeof j === 'object' ? j : null;
  } catch {
    return null;
  }
}
const byTicker = (json) => {
  const m = new Map();
  for (const r of Array.isArray(json && json.data) ? json.data : []) if (r && r.ticker) m.set(up(r.ticker), r);
  return m;
};
const risk = (it) => Object.fromEntries(RISK.map((c) => [c.key, num(it && it[c.key])]));
const logo = (t) => ({ logo: (t && t.logo) || null, logo_color: (t && t.logo_color) || null });

function statsOf(stats, cls) {
  const m = new Map();
  const items = stats && stats.items && typeof stats.items === 'object' ? stats.items : {};
  for (const [k, v] of Object.entries(items)) if (v && v.class === cls) m.set(up(k), v);
  return m;
}

function stocks(stats, shares, fund) {
  const st = statsOf(stats, 'share');
  const ti = byTicker(shares);
  const fd = fund && fund.data && typeof fund.data === 'object' ? fund.data : {};
  // все акции T-Invest и все акции из рейтинга: бумага без 36 мес. истории остаётся с пустыми коэффициентами
  const ids = [...new Set([...st.keys(), ...ti.keys()])];
  return ids.map((secid) => {
    const t = ti.get(secid);
    const f = fd[secid] || {};
    return {
      secid, name: (t && t.name) || (st.get(secid) || {}).name || secid, ...logo(t),
      sector: t && t.sector ? SECTOR_LABELS[t.sector] || t.sector : null,
      ...risk(st.get(secid)),
      pe: num(f.pe), pb: num(f.pb), ev_ebitda: num(f.ev_ebitda),
      roe: pct(f.roe), dividend_yield: pct(f.dividend_yield_ttm),
    };
  });
}

function funds(stats, etfs, rus) {
  const st = statsOf(stats, 'fund');
  const ti = byTicker(etfs);
  const ru = byTicker(rus);
  // биржевые фонды: из рейтинга и справочника T-Invest; RusETFs (в т. ч. ещё не торгуемые) — только обогащение
  const ids = [...new Set([...st.keys(), ...ti.keys()])];
  return ids.map((secid) => {
    const t = ti.get(secid);
    const r = ru.get(secid) || {};
    const fee = num(r.commission_pct) !== null ? r.commission_pct : t && t.fixed_commission;
    return {
      secid, name: (t && t.name) || r.name || (st.get(secid) || {}).name || secid, ...logo(t),
      issuer: r.issuer || null, asset_class: r.asset_class || null,
      commission: pct(fee), aum_rub: num(r.aum_rub),
      ...risk(st.get(secid)),
    };
  });
}

function bonds(file) {
  return (Array.isArray(file && file.data) ? file.data : []).filter((r) => r && r.secid).map((r) => ({
    secid: r.secid, name: r.name || r.secid, board: r.board || null, type: BOND_TYPES[r.board] || r.board || null,
    yield: num(r.yield), coupon_pct: num(r.coupon_pct), coupon_freq: num(r.coupon_freq),
    duration_days: num(r.duration_days), maturity_date: r.maturity_date || null, offer_date: r.offer_date || null,
    price_pct: num(r.price_pct), nkd: num(r.nkd), volume_rub: num(r.volume_rub), listing_level: num(r.listing_level),
    face_currency: r.face_currency || null,
  }));
}

/**
 * Данные скринера.
 * @param {'stocks'|'funds'|'bonds'} kind
 * @returns {Promise<{updated: string|null, source: string, columns: Array<{key, label, type, group}>,
 *   rows: Array<Object>}>}  updated — дата основного файла (symbol_stats.json; для облигаций — screener_bonds.json).
 *   Строки акций/фондов дополнительно несут logo / logo_color (для значка), облигаций — board.
 *   Нет файлов совсем — rows: [] (страница показывает пустое состояние).
 */
export async function loadScreener(kind, { base, fetchImpl } = {}) {
  if (!COLUMNS[kind]) throw new Error(`неизвестный скринер: ${kind}`);
  const columns = COLUMNS[kind];
  if (kind === 'bonds') {
    const f = await load(base, 'screener_bonds', fetchImpl);
    return { updated: (f && f.updated) || null, source: 'ISS MOEX', columns, rows: bonds(f) };
  }
  const names = kind === 'stocks' ? ['symbol_stats', 'tinvest_shares', 'fundamentals']
    : ['symbol_stats', 'tinvest_etfs', 'rusetfs_funds'];
  const [stats, a, b] = await Promise.all(names.map((n) => load(base, n, fetchImpl)));
  const rows = kind === 'stocks' ? stocks(stats, a, b) : funds(stats, a, b);
  const source = kind === 'stocks' ? 'ISS MOEX (расчёт FinServ), T-Invest API'
    : 'ISS MOEX (расчёт FinServ), RusETFs (rusetfs.com), T-Invest API';
  const updated = (stats && stats.updated) || (a && a.updated) || null;
  return { updated, source, columns, rows: sortRows(rows, 'secid', 'asc') };
}

/**
 * Фильтр строк (чистая функция). filters — объект {ключ: условие}:
 *   q: 'текст'           — подстрока в тикере или названии без учёта регистра;
 *   key: {min, max}      — диапазон включительно (числа или даты 'YYYY-MM-DD'); null в строке не проходит;
 *   key: [v1, v2]        — значение из списка (пустой список — без фильтра);
 *   key: 'значение'      — точное совпадение без учёта регистра.
 * Пустые условия (null, undefined, '') игнорируются.
 */
export function filterRows(rows, filters = {}) {
  const conds = Object.entries(filters || {}).filter(([, c]) => c !== null && c !== undefined && c !== ''
    && !(Array.isArray(c) && !c.length));
  return rows.filter((r) => conds.every(([k, c]) => {
    if (k === 'q') {
      const q = String(c).trim().toLowerCase();
      return !q || String(r.secid || '').toLowerCase().includes(q) || String(r.name || '').toLowerCase().includes(q);
    }
    const v = r[k];
    if (Array.isArray(c)) return c.includes(v);
    if (typeof c === 'object') {
      const lo = c.min ?? null;
      const hi = c.max ?? null;
      if (lo === null && hi === null) return true;
      if (v === null || v === undefined) return false;
      return (lo === null || v >= lo) && (hi === null || v <= hi);
    }
    return v !== null && v !== undefined && String(v).toLowerCase() === String(c).toLowerCase();
  }));
}

/**
 * Сортировка (чистая функция, новый массив, устойчивая): dir 'asc' | 'desc' (по умолчанию 'desc');
 * null — всегда в конце; строки — по русскому алфавиту.
 */
export function sortRows(rows, key, dir = 'desc') {
  const s = dir === 'asc' ? 1 : -1;
  return rows.map((r, i) => [r, i]).sort(([a, i], [b, j]) => {
    const x = a[key];
    const y = b[key];
    const xn = x === null || x === undefined;
    const yn = y === null || y === undefined;
    if (xn || yn) return xn && yn ? i - j : xn ? 1 : -1;
    const c = typeof x === 'string' || typeof y === 'string' ? String(x).localeCompare(String(y), 'ru') : x - y;
    return c ? c * s : i - j;
  }).map(([r]) => r);
}
