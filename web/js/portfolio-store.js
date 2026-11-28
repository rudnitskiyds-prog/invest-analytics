// Хранилище «Моего портфеля»: localStorage браузера (ключ finserv.portfolio.v1), методы — как у Python Ledger.
// Недоступен localStorage (приватное окно, запрет сайта) — данные живут в памяти вкладки, persistent() == false.
// Разбор и проверку CSV/операций делает web/lib/ledger.js (parseCsv, validateTx) — здесь только хранение.

import { engine, UserError } from "./engine.js";

export const STORE_KEY = "finserv.portfolio.v1";
export const TX_FIELDS = ["date", "type", "secid", "qty", "price", "amount", "fee", "note"];
const TYPES = ["BUY", "SELL", "DIVIDEND", "COUPON", "DEPOSIT", "WITHDRAW", "FEE", "TAX"];
const ISO = /^\d{4}-\d{2}-\d{2}$/;

const empty = () => ({ version: 1, nextId: 1, portfolios: [], transactions: [] });
let state = empty();
let persistent = true;
let loaded = false;
const listeners = new Set();

const num = (x, d = 0) => (Number.isFinite(Number(x)) && x !== "" && x != null ? Number(x) : d);

/** Проверка и нормализация структуры (для чтения из localStorage и импорта JSON). Бросает UserError с понятным текстом. */
export function normalize(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new UserError("Файл не похож на экспорт портфеля: ожидался объект JSON.");
  if (raw.version !== 1) throw new UserError(`Неизвестная версия данных (${raw.version ?? "не указана"}); поддерживается версия 1.`);
  if (!Array.isArray(raw.portfolios) || !Array.isArray(raw.transactions)) throw new UserError("В файле нет списков «portfolios» и «transactions».");
  const ids = new Set();
  const names = new Set();
  const portfolios = raw.portfolios.map((p, i) => {
    if (!p || !Number.isInteger(p.id) || p.id < 1 || ids.has(p.id)) throw new UserError(`Портфель №${i + 1}: неверный или повторяющийся id.`);
    const name = String(p.name ?? "").trim();
    if (!name) throw new UserError(`Портфель №${i + 1}: не задано название.`);
    if (names.has(name.toLowerCase())) throw new UserError(`Название портфеля «${name}» встречается дважды.`);
    ids.add(p.id);
    names.add(name.toLowerCase());
    const target = {};
    for (const [k, v] of Object.entries(p.target && typeof p.target === "object" ? p.target : {})) {
      if (Number.isFinite(Number(v)) && Number(v) >= 0) target[String(k).toUpperCase()] = Number(v);
    }
    return {
      id: p.id, name, created: String(p.created ?? ""), benchmark: String(p.benchmark || "MCFTR"), target,
      rebalance: String(p.rebalance || "A"), band: num(p.band, 0), last_rebalance: p.last_rebalance ? String(p.last_rebalance) : null,
    };
  });
  const txIds = new Set();
  const transactions = raw.transactions.map((t, i) => {
    const where = `Операция №${i + 1}`;
    if (!t || !Number.isInteger(t.id) || t.id < 1 || txIds.has(t.id)) throw new UserError(`${where}: неверный или повторяющийся id.`);
    if (!ids.has(t.portfolio_id)) throw new UserError(`${where}: портфеля с id ${t.portfolio_id} нет в файле.`);
    if (!ISO.test(String(t.date))) throw new UserError(`${where}: дата «${t.date}» — нужен формат ГГГГ-ММ-ДД.`);
    if (!TYPES.includes(t.type)) throw new UserError(`${where}: неизвестный тип «${t.type}».`);
    txIds.add(t.id);
    return {
      id: t.id, portfolio_id: t.portfolio_id, date: String(t.date), type: t.type, secid: t.secid ? String(t.secid).toUpperCase() : null,
      qty: num(t.qty), price: num(t.price), amount: num(t.amount), fee: num(t.fee), note: t.note ? String(t.note) : "",
    };
  });
  const nextId = Math.max(num(raw.nextId, 1), 1 + [...ids, ...txIds].reduce((a, x) => Math.max(a, x), 0));
  return { version: 1, nextId, portfolios, transactions };
}

const BAK_KEY = `${STORE_KEY}.bak`;
let lastRaw = null;      // строка localStorage, с которой синхронизировано состояние
let corrupt = null;      // нечитаемая/чужая версия данных: перед первой записью уйдёт в BAK_KEY
let warning = null;

/** Синхронизация с localStorage: вызывается перед каждым чтением и записью, чтобы вкладки не затирали друг друга. */
function load() {
  if (!persistent) return;
  let s;
  try { s = localStorage.getItem(STORE_KEY); } catch { persistent = false; loaded = true; return; }
  if (loaded && s === lastRaw) return;
  loaded = true;
  lastRaw = s;
  if (!s) return;
  try {
    state = normalize(JSON.parse(s));
    corrupt = null;
    warning = null;
  } catch (e) {
    if (!(e instanceof SyntaxError || e instanceof UserError)) throw e;
    corrupt = s;
    warning = `Сохранённые данные портфеля не удалось прочитать (${e instanceof UserError ? e.message : "повреждены"}). Перед первой записью они будут сохранены в резервную копию «${BAK_KEY}» в localStorage.`;
  }
}

function save() {
  try {
    if (corrupt != null) { localStorage.setItem(BAK_KEY, corrupt); corrupt = null; }
    lastRaw = JSON.stringify(state);
    localStorage.setItem(STORE_KEY, lastRaw);
  } catch {
    persistent = false;
  }
  for (const fn of listeners) fn();
}

const find = (id) => state.portfolios.find((p) => p.id === id);
const copy = (x) => JSON.parse(JSON.stringify(x));

export const store = {
  /** false — localStorage недоступен, данные только в памяти вкладки. */
  persistent() { load(); return persistent; },
  /** Текст предупреждения о нечитаемых сохранённых данных или null. */
  warning() { load(); return warning; },
  subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },

  portfolios() { load(); return copy(state.portfolios); },
  portfolio(id) { load(); const p = find(id); return p ? copy(p) : null; },

  createPortfolio(name, { benchmark = "MCFTR" } = {}) {
    load();
    name = String(name ?? "").trim();
    if (!name) throw new UserError("Введите название портфеля.");
    if (state.portfolios.some((p) => p.name.toLowerCase() === name.toLowerCase())) throw new UserError(`Портфель «${name}» уже есть — выберите другое название.`);
    const p = { id: state.nextId++, name, created: new Date().toISOString().slice(0, 10), benchmark, target: {}, rebalance: "A", band: 0.05, last_rebalance: null };
    state.portfolios.push(p);
    save();
    return p.id;
  },

  updatePortfolio(id, fields) {
    load();
    const p = find(id);
    if (!p) throw new UserError("Портфель не найден.");
    if ("name" in fields) {
      const name = String(fields.name ?? "").trim();
      if (!name) throw new UserError("Введите название портфеля.");
      if (state.portfolios.some((q) => q.id !== id && q.name.toLowerCase() === name.toLowerCase())) throw new UserError(`Портфель «${name}» уже есть.`);
      fields = { ...fields, name };
    }
    for (const k of ["name", "benchmark", "target", "rebalance", "band", "last_rebalance"]) if (k in fields) p[k] = copy(fields[k]);
    save();
  },

  deletePortfolio(id) {
    load();
    state.portfolios = state.portfolios.filter((p) => p.id !== id);
    state.transactions = state.transactions.filter((t) => t.portfolio_id !== id);
    save();
  },

  /** Операции портфеля по возрастанию даты (при равных датах — в порядке добавления). */
  transactions(pid) {
    load();
    return copy(state.transactions.filter((t) => t.portfolio_id === pid).sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : a.id - b.id)));
  },

  _tx(pid, t) {
    const type = String(t.type ?? "").toUpperCase();
    const qty = num(t.qty);
    const price = num(t.price);
    const tx = {
      id: 0, portfolio_id: pid, date: String(t.date ?? "").slice(0, 10), type, secid: t.secid ? String(t.secid).trim().toUpperCase() : null,
      qty, price, amount: t.amount === "" || t.amount == null || num(t.amount) === 0 ? qty * price : num(t.amount), fee: num(t.fee), note: t.note ? String(t.note) : "",
    };
    const errs = typeof engine.ledger?.validateTx === "function" ? engine.ledger.validateTx(tx) : [];
    if (errs.length) throw new UserError(errs.join(" "));
    return tx;
  },

  addTx(pid, t) {
    load();
    if (!find(pid)) throw new UserError("Портфель не найден.");
    const tx = this._tx(pid, t);
    tx.id = state.nextId++;
    state.transactions.push(tx);
    save();
    return tx.id;
  },

  deleteTx(id) {
    load();
    state.transactions = state.transactions.filter((t) => t.id !== id);
    save();
  },

  /** CSV (date,type,secid,qty,price,amount,fee,note) → операции портфеля; при ошибке в любой строке не добавляется ничего. */
  importCsv(pid, text) {
    load();
    if (!find(pid)) throw new UserError("Портфель не найден.");
    if (typeof engine.ledger?.parseCsv !== "function") throw new UserError("Разбор CSV недоступен: в web/lib/ledger.js нет parseCsv().");
    const rows = engine.ledger.parseCsv(text).map((r) => this._tx(pid, r));
    for (const r of rows) {
      r.id = state.nextId++;
      state.transactions.push(r);
    }
    save();
    return rows.length;
  },

  exportJson() { load(); return JSON.stringify(state, null, 2); },

  /** mode: "replace" — заменить все данные; "merge" — добавить портфели (с тем же названием — операции в существующий). Возвращает число добавленных операций. */
  importJson(text, mode = "replace") {
    load();
    let raw;
    try {
      raw = JSON.parse(text);
    } catch {
      throw new UserError("Файл не читается: это не JSON. Загрузите файл, полученный через «Экспорт JSON».");
    }
    const inc = normalize(raw);
    if (mode === "replace") {
      if (!corrupt && state.portfolios.length) corrupt = JSON.stringify(state);   // текущие данные — в .bak перед заменой
      state = inc;
    } else {
      const map = new Map();
      for (const p of inc.portfolios) {
        const ex = state.portfolios.find((q) => q.name.toLowerCase() === p.name.toLowerCase());
        if (ex) map.set(p.id, ex.id);
        else {
          const id = state.nextId++;
          state.portfolios.push({ ...p, id });
          map.set(p.id, id);
        }
      }
      for (const t of inc.transactions) state.transactions.push({ ...t, id: state.nextId++, portfolio_id: map.get(t.portfolio_id) });
    }
    save();
    return inc.transactions.length;
  },

  isEmpty() { load(); return !state.portfolios.length; },
};
