// Скринеры #/screener/stocks|funds|bonds: данные собирает сборщик, web/lib/screener.js их загружает,
// фильтрует и сортирует; здесь только элементы управления и вывод таблицы (web/CONTRACT.md, «Скринеры»).
// Состояние фильтров — в query внутри hash (#/screener/stocks?sector=it&sort=cagr), ссылкой можно поделиться.

import { DATA_BASE } from "./config.js";
import { downloadCsv, h, notice, put, tableWrap } from "./dom.js";
import { csvNum, fmtDate, fmtDateTime, fmtNum, fmtPct, parseNum } from "./format.js";
import { hint, TERMS } from "./glossary.js";
import { assetBadge, loadLogos } from "./logo.js";
import { href, parseHash, setQuery, symbolHref } from "./router.js";
import { filterRows, loadScreener, sortRows } from "../lib/screener.js";

const KINDS = { stocks: "Акции", funds: "Фонды", bonds: "Облигации" };
const TABS = { ...KINDS, indices: "Индексы" };

/** Переключатель вкладок скринеров (общий для таблиц и страницы индексов). */
export const screenerTabs = (kind) => h("nav", { class: "scr-tabs", "aria-label": "Вид бумаг" }, Object.entries(TABS).map(([k, label]) =>
  h("a", { href: href(`/screener/${k}`), class: "scr-tab", "aria-current": k === kind ? "page" : null }, label)));
const PAGE_SIZE = 50;

/** Ключ колонки → термин глоссария (подсказка «?»), если ключ не совпадает с термином. */
const TERM_ALIAS = {
  score: "rating", rank: "rating", fee: "ter", commission: "ter", commission_pct: "ter", yield: "ytm",
  coupon_pct: "coupon", div_yield: "dividend", dividend_yield: "dividend", cagr_10y: "cagr",
};

/**
 * Фильтры по видам. keys — возможные имена колонки (берётся первая, что есть в columns);
 * type: select — равенство, range — от–до, max — «до», year — дата не позже конца года.
 * Для pct-колонок пользователь вводит проценты, в фильтр уходят доли.
 */
const FILTERS = {
  stocks: [
    { id: "sector", type: "select", keys: ["sector"], label: "Сектор" },
    { id: "cagr", type: "range", keys: ["cagr"], label: "CAGR, %" },
    { id: "dd", type: "range", keys: ["max_drawdown"], label: "Макс. просадка, %" },
    { id: "pe", type: "range", keys: ["pe"], label: "P/E" },
    { id: "dy", type: "range", keys: ["div_yield", "dividend_yield"], label: "Див. доходность, %" },
  ],
  funds: [
    { id: "cls", type: "select", keys: ["asset_class"], label: "Класс активов" },
    { id: "uk", type: "select", keys: ["issuer", "management_company"], label: "УК" },
    { id: "fee", type: "max", keys: ["fee", "commission", "commission_pct"], label: "Комиссия до, %" },
  ],
  bonds: [
    { id: "type", type: "select", keys: ["type"], label: "Тип" },
    { id: "ytm", type: "range", keys: ["yield"], label: "Доходность, %" },
    { id: "dur", type: "range", keys: ["duration_days"], label: "Дюрация, дн." },
    { id: "mat", type: "year", keys: ["maturity_date"], label: "Погашение до (год)" },
    { id: "lvl", type: "select", keys: ["listing_level"], label: "Уровень листинга" },
  ],
};

const cache = new Map(); // kind → Promise<data>
let root = null;
let st = null;           // {kind, data, cols, filters, query, els}
let seq = 0;

// ---------------------------------------------------------------- общие помощники (их же использует page-portfolios.js)

const isNum = (x) => typeof x === "number" && Number.isFinite(x);

/** Крупные суммы: «13,5 млрд ₽». */
export function fmtBigRub(x) {
  if (!isNum(x)) return "—";
  const a = Math.abs(x);
  if (a >= 1e9) return `${fmtNum(x / 1e9, 1)} млрд ₽`;
  if (a >= 1e6) return `${fmtNum(x / 1e6, 1)} млн ₽`;
  return `${fmtNum(x, 0)} ₽`;
}

/** Значение ячейки по типу колонки контракта: pct | pct2 | num | rub | text | date. */
export function fmtCell(type, v) {
  if (v == null || v === "") return "—";
  if (type === "pct") return fmtPct(v, 1);
  if (type === "pct2") return fmtPct(v, 2);
  if (type === "rub") return fmtBigRub(v);
  if (type === "date") return fmtDate(String(v));
  if (type === "num") return isNum(v) ? fmtNum(v, Number.isInteger(v) ? 0 : Math.abs(v) >= 50 ? 1 : 2) : String(v);
  return String(v);
}

/** Значение для CSV: числа — запятая в дробях, без группировки. */
/** Значение для CSV: проценты (pct/pct2, доли) — ×100, чтобы совпадать с заголовком «…, %». */
export const csvCell = (v, type) => (isNum(v) ? csvNum(/^pct/.test(type) ? v * 100 : v) : v ?? "");
/** Заголовок CSV: у процентных колонок — «, %», если знака ещё нет. */
export const csvHead = (c) => (/^pct/.test(c.type) && !c.label.includes("%") ? `${c.label}, %` : c.label);

export const signClass = (v) => (isNum(v) && v !== 0 ? (v > 0 ? "pos" : "neg") : null);

/** Подсказка «?» по ключу колонки, если термин есть в глоссарии. */
export function colHint(key) {
  const t = TERMS[key] ? key : TERM_ALIAS[key];
  return t ? hint(t) : null;
}

/** Заголовок таблицы с сортировкой по клику (aria-sort на th, кнопка внутри — для клавиатуры). */
export function sortableHead(cols, sort, onSort) {
  return h("thead", {}, h("tr", {}, cols.map((c) => {
    const num = c.type !== "text";
    const on = sort.key === c.key;
    return h("th", {
      scope: "col", class: num ? "num" : null,
      "aria-sort": on ? (sort.dir === "asc" ? "ascending" : "descending") : null,
    },
    h("button", { type: "button", class: "th-sort", onclick: () => onSort(c.key, num) },
      c.label, h("span", { class: "th-arrow", "aria-hidden": "true" }, on ? (sort.dir === "asc" ? "▲" : "▼") : "")),
    colHint(c.key));
  })));
}

/** Следующее состояние сортировки: та же колонка — сменить направление, новая — числа по убыванию. */
export const nextSort = (sort, key, num) => (sort.key === key
  ? { key, dir: sort.dir === "asc" ? "desc" : "asc" }
  : { key, dir: num ? "desc" : "asc" });

export const skeleton = () => h("div", { class: "skeleton", "aria-busy": "true", "aria-label": "Загрузка" },
  ...Array.from({ length: 8 }, () => h("span")));

// ---------------------------------------------------------------- страница

export function init() {}

export function onShow(route) {
  root = document.getElementById("screener-root");
  if (!root) return;
  const kind = [route?.params?.kind, (route?.path || "").split("/")[2]].find((k) => k in KINDS) || "stocks";
  render(kind);
}

export function onGlobalChange() {}

export function rerender() {
  if (st?.data) renderResults();
}

const BADGE_KIND = { stocks: "share", funds: "fund", bonds: "bond" };

function load(kind) {
  if (!cache.has(kind)) {
    const p = loadScreener(kind, { base: DATA_BASE });
    cache.set(kind, p);
    p.catch(() => cache.delete(kind)); // ошибку можно повторить при следующем заходе
  }
  return cache.get(kind);
}

async function render(kind) {
  const my = ++seq;
  const body = h("div", { class: "scr-body" }, skeleton());
  put(root,
    h("div", { class: "panel-head" },
      h("h2", {}, "Скринеры"),
      screenerTabs(kind)),
    body);
  let data;
  try {
    [data] = await Promise.all([load(kind), loadLogos(kind === "bonds")]);
  } catch (e) {
    if (my !== seq) return;
    console.warn(e);
    put(body, notice("error", `Не удалось загрузить данные скринера: ${e?.message || e}. Обновите страницу чуть позже.`));
    return;
  }
  if (my !== seq) return;
  if (!data || !data.rows?.length || !data.columns?.length) {
    put(body, notice("info", `Данных по разделу «${KINDS[kind]}» пока нет: таблица собирается ночью из выгрузок Мосбиржи и T-Invest. Загляните завтра.`));
    return;
  }
  const { query } = parseHash();
  const has = new Set(data.columns.map((c) => c.key));
  const filters = FILTERS[kind]
    .map((f) => ({ ...f, key: f.keys.find((k) => has.has(k)) }))
    .filter((f) => f.key);
  st = { kind, data, filters, query, cols: loadCols(kind, data.columns), els: {} };
  put(body,
    h("p", { class: "scr-meta muted" },
      `Данные на ${fmtDateTime(data.updated)} · источник: ${data.source || "—"}`),
    controls(),
    st.els.count = h("p", { class: "scr-count muted", role: "status" }),
    st.els.table = h("div"),
    st.els.pager = h("nav", { class: "scr-pager", "aria-label": "Страницы таблицы" }));
  renderResults();
}

// ---------------------------------------------------------------- элементы управления

const q = (k) => st.query.get(k) ?? "";

function setQ(obj, { keepPage = false } = {}) {
  for (const [k, v] of Object.entries(obj)) {
    if (v == null || v === "") st.query.delete(k);
    else st.query.set(k, String(v));
  }
  if (!keepPage) st.query.delete("page");
  setQuery(Object.fromEntries([...Object.keys(obj), ...(keepPage ? [] : ["page"])].map((k) => [k, st.query.get(k)])));
  renderResults();
}

function controls() {
  const { data, filters } = st;
  const colType = Object.fromEntries(data.columns.map((c) => [c.key, c.type]));
  const fields = [
    h("label", { class: "field scr-search" }, h("span", {}, "Поиск"),
      h("input", {
        type: "search", value: q("q"), placeholder: "Тикер или название", autocomplete: "off",
        oninput: (e) => setQ({ q: e.target.value.trim() }),
      })),
  ];
  for (const f of filters) {
    if (f.type === "select") {
      const values = [...new Set(data.rows.map((r) => r[f.key]).filter((v) => v != null && v !== ""))]
        .sort((a, b) => String(a).localeCompare(String(b), "ru"));
      const sel = h("select", { onchange: (e) => setQ({ [f.id]: e.target.value }) },
        h("option", { value: "" }, "Все"),
        values.map((v) => h("option", { value: String(v) }, String(v))));
      sel.value = q(f.id);
      fields.push(h("label", { class: "field" }, h("span", {}, f.label), sel));
    } else {
      const num = (id, ph) => h("input", {
        type: "text", inputmode: "decimal", value: q(id), placeholder: ph, "aria-label": `${f.label} ${ph}`,
        onchange: (e) => setQ({ [id]: e.target.value.trim() }),
      });
      const box = f.type === "range"
        ? h("span", { class: "scr-range" }, num(`${f.id}_min`, "от"), "–", num(`${f.id}_max`, "до"))
        : num(`${f.id}_max`, f.type === "year" ? "год" : "до");
      fields.push(h("div", { class: "field" }, h("span", {}, f.label), box));
    }
    f.pct = colType[f.key] === "pct";
  }
  return h("form", { class: "scr-controls card", onsubmit: (e) => e.preventDefault() },
    h("div", { class: "scr-fields" }, fields),
    h("div", { class: "scr-actions" },
      columnPicker(),
      h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: resetAll }, "Сбросить фильтры"),
      h("button", { type: "button", class: "btn btn-sm", onclick: exportCsv }, "Скачать CSV")));
}

function resetAll() {
  const keep = ["sort", "dir"];
  const drop = [...st.query.keys()].filter((k) => !keep.includes(k));
  setQ(Object.fromEntries(drop.map((k) => [k, null])));
  render(st.kind);
}

// ---------------------------------------------------------------- выбор колонок (localStorage)

const colsKey = (kind) => `fs-screener-cols-${kind}`;

function loadCols(kind, columns) {
  const all = columns.map((c) => c.key);
  try {
    const saved = JSON.parse(localStorage.getItem(colsKey(kind)) || "null");
    if (Array.isArray(saved)) {
      const keep = all.filter((k) => saved.includes(k));
      if (keep.length) return new Set(keep);
    }
  } catch {
    /* приватный режим или испорченное значение — все колонки */
  }
  return new Set(all);
}

function saveCols() {
  try {
    localStorage.setItem(colsKey(st.kind), JSON.stringify([...st.cols]));
  } catch {
    /* не сохранится между визитами — не страшно */
  }
}

function columnPicker() {
  const lockedKey = st.data.columns[0].key; // первая колонка (тикер) показывается всегда
  return h("details", { class: "scr-cols" },
    h("summary", { class: "btn btn-sm" }, "Колонки"),
    h("div", { class: "scr-cols-list" }, st.data.columns.map((c) => h("label", { class: "check" },
      h("input", {
        type: "checkbox", checked: st.cols.has(c.key), disabled: c.key === lockedKey,
        onchange: (e) => {
          if (e.target.checked) st.cols.add(c.key);
          else st.cols.delete(c.key);
          saveCols();
          renderResults();
        },
      }), " ", c.label))));
}

// ---------------------------------------------------------------- фильтр, сортировка, таблица

/** Фильтры для filterRows: {q, key: значение | {min, max}}; проценты из полей — в доли, год погашения — в «YYYY-12-31». */
function currentFilters() {
  const out = { q: q("q") };
  const val = (f, id) => {
    const x = parseNum(q(id));
    return Number.isFinite(x) ? (f.pct ? x / 100 : x) : null;
  };
  for (const f of st.filters) {
    if (f.type === "select") {
      const v = q(f.id);
      if (v) out[f.key] = f.key === "listing_level" ? [Number(v)] : v;
    } else if (f.type === "year") {
      const y = parseNum(q(`${f.id}_max`));
      if (Number.isInteger(y)) out[f.key] = { max: `${y}-12-31` };
    } else {
      out[f.key] = { min: f.type === "range" ? val(f, `${f.id}_min`) : null, max: val(f, `${f.id}_max`) };
    }
  }
  return out;
}

function currentSort() {
  const key = q("sort");
  return { key: st.data.columns.some((c) => c.key === key) ? key : null, dir: q("dir") === "asc" ? "asc" : "desc" };
}

function selectedRows() {
  const sort = currentSort();
  const rows = filterRows(st.data.rows, currentFilters());
  return { rows: sort.key ? sortRows(rows, sort.key, sort.dir) : rows, sort };
}

function renderResults() {
  const { rows, sort } = selectedRows();
  const cols = st.data.columns.filter((c, i) => i === 0 || st.cols.has(c.key));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(pages, Math.max(1, parseInt(q("page"), 10) || 1));
  const part = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  st.els.count.textContent = rows.length === st.data.rows.length
    ? `Бумаг: ${fmtNum(rows.length)}`
    : `Найдено: ${fmtNum(rows.length)} из ${fmtNum(st.data.rows.length)}`;
  if (!rows.length) {
    put(st.els.table, h("p", { class: "no-data muted" }, "Под фильтры не попала ни одна бумага — ослабьте условия или сбросьте фильтры."));
    put(st.els.pager);
    return;
  }
  const onSort = (key, num) => {
    const n = nextSort(sort, key, num);
    setQ({ sort: n.key, dir: n.dir });
  };
  put(st.els.table, tableWrap(h("table", { class: "scr-table" },
    h("caption", { class: "sr-only" }, `${KINDS[st.kind]}: скринер`),
    sortableHead(cols, sort, onSort),
    h("tbody", {}, part.map((r) => h("tr", {
      class: "row-link",
      onclick: (e) => { if (!e.target.closest("a, button")) location.hash = symbolHref(r.secid); },
    }, cols.map((c, i) => (i === 0
      ? h("th", { scope: "row", class: "scr-first" },
        h("a", { href: symbolHref(r.secid), class: "scr-ticker" }, assetBadge(r.secid, 24, BADGE_KIND[st.kind]), h("span", {}, r[c.key] ?? r.secid)))
      : h("td", { class: [c.type !== "text" ? "num" : "", c.type === "pct" && /cagr|ret|yield|ytd/.test(c.key) ? signClass(r[c.key]) : ""].filter(Boolean).join(" ") || null },
        c.key === "issuer" && r.issuer
          ? h("span", { class: "scr-issuer" }, assetBadge(r.secid, 20, "fund"), h("span", {}, r.issuer))   // логотип УК — бренд ETF
          : fmtCell(c.type, r[c.key]))))))))));
  put(st.els.pager, pages > 1 ? [
    h("button", { type: "button", class: "btn btn-sm", disabled: page <= 1, onclick: () => setQ({ page: page - 1 }, { keepPage: true }) }, "← Назад"),
    h("span", { class: "muted" }, `Страница ${page} из ${pages}`),
    h("button", { type: "button", class: "btn btn-sm", disabled: page >= pages, onclick: () => setQ({ page: page + 1 }, { keepPage: true }) }, "Вперёд →"),
  ] : []);
}

function exportCsv() {
  const { rows } = selectedRows();
  const cols = st.data.columns.filter((c, i) => i === 0 || st.cols.has(c.key));
  downloadCsv(`screener-${st.kind}.csv`, [cols.map(csvHead), ...rows.map((r) => cols.map((c) => csvCell(r[c.key], c.type)))]);
}

// Данные берутся из public/data — engine (web/lib) для показа не нужен.
export const STATIC = true;
