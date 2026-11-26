/**
 * Раздел «Портфели и стратегии» (`#/portfolios/...`): загрузка и выборка из public/data/portfolios.json.
 *
 * Файл собирает ночной сборщик (scripts/collect_data.py -> core/analytics/portfolios.py):
 *   {updated, source, initial, groups, items: {slug: {row, report, stale?, stale_date?}},
 *    series: {slug: [[date, value], ...]}, series_benchmark: {slug: [[date, value], ...]},
 *    risk_return: {"1Y"|"5Y"|"10Y": [{id, name, group, ret, volatility, from, to}]},
 *    skipped: {slug: причина}, stale: {slug: {stale_date, reason}}, last}
 *
 * Финансовых расчётов здесь нет (всё посчитано в core/), только загрузка и выборка.
 * Модуль не трогает DOM; loadPortfolios принимает fetchImpl (тесты в Node), кэш — общий из data.js.
 * Доли — в долях (0.123 = 12,3 %).
 */
import { DEFAULT_DATA_BASE, getCached } from './data.js';

const baseDir = (base) => { const b = base || DEFAULT_DATA_BASE; return b.endsWith('/') ? b : b + '/'; };
const hasRank = (r) => typeof r === 'number' && Number.isFinite(r);
const itemsOf = (data) => (data && data.items && typeof data.items === 'object' ? data.items : {});

/**
 * portfolios.json целиком, как есть. Ошибка сети / 404 / неверный формат — Error с русским сообщением.
 */
export async function loadPortfolios({ base = DEFAULT_DATA_BASE, fetchImpl } = {}) {
  let json;
  try {
    json = await getCached(`${baseDir(base)}portfolios.json`, fetchImpl, 'json', 2);
  } catch (e) {
    throw new Error(`Не удалось загрузить данные портфелей (portfolios.json): ${e && e.message ? e.message : e}`);
  }
  if (!json || typeof json !== 'object' || !json.items || typeof json.items !== 'object') {
    throw new Error('Файл данных портфелей (portfolios.json) повреждён или имеет неожиданный формат');
  }
  return json;
}

/**
 * Список портфелей [{id, ...row}] — все или только группы `group` (row.group === group).
 * Сортировка: row.rank по убыванию; без rank (null/undefined/не число) — в конце, между собой по имени (ru).
 */
export function portfolioList(data, group = null) {
  const out = [];
  for (const [id, item] of Object.entries(itemsOf(data))) {
    const row = (item && item.row) || {};
    if (group !== null && group !== undefined && row.group !== group) continue;
    out.push({ id, ...row });
  }
  const byName = (a, b) => String(a.name ?? a.id).localeCompare(String(b.name ?? b.id), 'ru');
  return out.sort((a, b) => {
    const ra = hasRank(a.rank), rb = hasRank(b.rank);
    if (ra && rb) return b.rank - a.rank || byName(a, b);
    if (ra !== rb) return ra ? -1 : 1;
    return byName(a, b);
  });
}

/** Портфель по slug: {id, row, report, series, benchmark}; ряды — [[date, value], ...] ([] если нет); нет id — null. */
export function portfolioById(data, id) {
  const items = itemsOf(data);
  const item = Object.prototype.hasOwnProperty.call(items, id) ? items[id] : null;
  if (!item) return null;
  return {
    id,
    row: item.row || {},
    report: item.report ?? null,
    series: (data.series && data.series[id]) || [],
    benchmark: (data.series_benchmark && data.series_benchmark[id]) || [],
  };
}

/** Число портфелей: {all: N, [группа]: n, ...}; группы из data.groups присутствуют и при n = 0. */
export function groupCounts(data) {
  const counts = { all: 0 };
  for (const g of (Array.isArray(data && data.groups) ? data.groups : [])) counts[g] = 0;
  for (const item of Object.values(itemsOf(data))) {
    counts.all += 1;
    const g = item && item.row && item.row.group;
    if (g !== undefined && g !== null) counts[g] = (counts[g] || 0) + 1;
  }
  return counts;
}

/** Точки «риск–доходность» окна ('1Y' | '5Y' | '10Y'): [{id, name, group, ret, volatility, from, to}] или []. */
export function riskReturnPoints(data, window = '10Y') {
  const pts = data && data.risk_return && data.risk_return[window];
  return Array.isArray(pts) ? pts : [];
}

/**
 * Данные портфеля устарели (взяты из прошлого файла, т. к. сегодня пересчёт не удался).
 * В файле `stale` — объект {slug: {stale_date, reason}}; дополнительно учитываются items[id].stale
 * и (на случай старого формата) массив slug-ов.
 */
export function isStale(data, id) {
  const st = data && data.stale;
  if (Array.isArray(st)) { if (st.includes(id)) return true; }
  else if (st && typeof st === 'object' && Object.prototype.hasOwnProperty.call(st, id)) return true;
  const items = itemsOf(data);
  const item = Object.prototype.hasOwnProperty.call(items, id) ? items[id] : null;
  return Boolean(item && item.stale);
}
