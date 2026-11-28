// «Мой портфель» (перенос pages/5_Мой_портфель.py; web/CONTRACT.md, раздел B). Контейнер #my-root.
// Расчёты — web/lib (ledger.js, quotes.js, metrics.js, backtest.js); здесь только ввод, вызовы и вывод.

import { fact, registerFacts } from "./assistant.js";
import { colorOf, lineChart, renderChart, baseOptions, themeColors } from "./charts.js";
import { $, downloadBlob, errorNotice, h, notice, put, tableWrap } from "./dom.js";
import { engine, requireEngine, UserError } from "./engine.js";
import { globals, FREQ_LABELS } from "./fields.js";
import { fmtDate, fmtNum, fmtPct, fmtRub, parseNum } from "./format.js";
import { assetBadge } from "./logo.js";
import { store } from "./portfolio-store.js";
import { riskFree } from "./riskfree.js";
import { getMany } from "./source.js";
import { buildMetricsTable } from "./tab-backtest.js";

const SRC = "Мой портфель: расчёт платформы по вашим операциям и котировкам Мосбиржи";
const SEL_KEY = "finserv.portfolio.sel";
// выполнить fn, когда узел el окажется в странице (вкладки вставляют содержимое после асинхронных расчётов)
const whenIn = (el, fn, n = 300) => { if (el.isConnected) fn(); else if (n) requestAnimationFrame(() => whenIn(el, fn, n - 1)); };
const later = (fn) => setTimeout(fn, 0);
const today = () => new Date().toISOString().slice(0, 10);

// ---------------------------------------------------------------- общее для обеих страниц

export function selectedId() {
  const pfs = store.portfolios();
  let id = NaN;
  try { id = Number(localStorage.getItem(SEL_KEY)); } catch { /* без запоминания выбора */ }
  return pfs.find((p) => p.id === id)?.id ?? pfs[0]?.id ?? null;
}
export function selectPortfolio(id) {
  try { localStorage.setItem(SEL_KEY, String(id)); } catch { /* без запоминания выбора */ }
}

/** Заметка о хранении данных (одинаковая на обеих страницах). */
export function storageNote() {
  return [
    notice("info", "Данные портфеля хранятся только в вашем браузере — сделайте экспорт, чтобы не потерять."),
    store.warning() ? notice("warn", store.warning()) : null,
    store.persistent() ? null : notice("warn", "Браузер не разрешает сохранять данные на этом сайте (например, приватное окно): портфель живёт только в этой вкладке и пропадёт при её закрытии. Сделайте экспорт."),
  ];
}

/** Сообщение после действия: делаем фокусируемым и после перерисовки переводим на него фокус (читалка озвучит результат). */
export function focusMsg(msg) {
  if (!msg) return;
  msg.tabIndex = -1;
  queueMicrotask(() => msg.focus());
}

/** Выбор портфеля: <select> со всеми портфелями; domId — уникальный на странице (обе страницы лежат в одном документе). */
export function portfolioSelect(id, onPick, label = "Портфель", domId = "pf-pick") {
  const sel = h("select", { id: domId }, store.portfolios().map((p) => h("option", { value: p.id, selected: p.id === id }, p.name)));
  sel.addEventListener("change", () => { selectPortfolio(Number(sel.value)); onPick(Number(sel.value)); $(`#${domId}`)?.focus(); });
  return h("div", { class: "field" }, h("label", { for: domId }, label), sel);
}

/** Описание бумаг без цены: «SBER (нет котировки — …)» / «… (нет курса ЦБ)». */
export function missingText(list, quotes) {
  return list.map((s) => (quotes?.[s] && quotes[s].currency && quotes[s].currency !== "RUB"
    ? `${s} (нет курса ЦБ)` : `${s} (нет котировки — возможно, бумага погашена)`)).join(", ");
}

const heldOf = (tx) => [...new Set(tx.map((t) => t.secid).filter(Boolean))].sort();

/** Котировки и оценка портфеля. */
export async function valuate(tx, extra = []) {
  requireEngine();
  if (!engine.ledger || !engine.quotes) {
    throw new UserError("Расчёты портфеля недоступны: модули web/lib/ledger.js и quotes.js не загрузились." +
      (engine.ledgerError || engine.quotesError ? ` Подробности: ${(engine.ledgerError || engine.quotesError).message}` : ""));
  }
  const held = heldOf(tx);
  const quotes = held.length || extra.length ? await engine.quotes.getQuotes([...new Set([...held, ...extra])]) : {};
  const { rows, cash } = engine.ledger.valuation(tx, quotes);
  const missing = rows.filter((r) => !Number.isFinite(r.value) && r.qty > 1e-9).map((r) => r.secid).sort();
  return { held, quotes, rows, cash, missing };
}

/** Последняя известная цифра выровнять на даты dates (протяжка вперёд). */
function ffillOn(dates, s) {
  const out = [];
  let j = -1;
  for (const d of dates) {
    while (j + 1 < s.dates.length && s.dates[j + 1] <= d) j++;
    out.push(j >= 0 ? s.values[j] : NaN);
  }
  return out;
}

// ---------------------------------------------------------------- страница

let pid = null;
let tab = "pos";
let token = 0;
let facts = [];
let flash = null;                // сообщение после действия: {kind, text}
const histCache = new Map();

registerFacts("/my", () => facts);

export function init() {
  store.subscribe(() => { if (isShown()) draw(); });
}
const isShown = () => !$("#panel-my")?.hidden;

export function onShow() { draw(); }
export function onGlobalChange() { draw(); }
export function rerender() { draw(); }
export const STATIC = true;

function say(kind, text) { flash = { kind, text }; }

function draw() {
  const root = $("#my-root");
  if (!root) return;
  const my = ++token;
  facts = [];
  const pfs = store.portfolios();
  pid = selectedId();
  const msg = flash && notice(flash.kind, flash.text);
  flash = null;
  focusMsg(msg);
  const head = [
    h("div", { class: "panel-head" }, h("h2", {}, "Мой портфель"),
      h("p", { class: "lead" }, "Учёт ваших сделок: сколько сейчас стоит портфель, сколько денег вы внесли и какую доходность получили. Сравните результат с рынком и с готовыми стратегиями.")),
    ...storageNote(),
    msg,
    portfolioBar(pfs),
  ];
  if (!pfs.length) {
    put(root, head, h("div", { class: "card my-empty" }, h("h3", { class: "card-title" }, "Создайте портфель"),
      h("p", { class: "muted" }, "Введите название выше и нажмите «Создать». Потом добавьте операции: сначала «Пополнение», затем «Покупки».")),
    dataCard(null));
    return;
  }
  const tx = store.transactions(pid);
  const body = h("div", { class: "my-body" });
  put(root, head, txCard(tx), body, dataCard(tx));
  if (!tx.length) {
    put(body, notice("info", "Добавьте операции: сначала «Пополнение», затем «Покупки»."));
    return;
  }
  put(body, h("p", { class: "muted", role: "status" }, "Получаю котировки…"));
  valuate(tx).then((v) => { if (my === token) renderBody(body, tx, v, my); })
    .catch((e) => { if (my === token) put(body, errorNotice(e)); });
}

function portfolioBar(pfs) {
  const name = h("input", { type: "text", id: "pf-new", value: pfs.length ? "" : "Основной", maxlength: "60", placeholder: "Например, Основной", autocomplete: "off" });
  const create = () => {
    try {
      const id = store.createPortfolio(name.value, { benchmark: globals().bench });
      selectPortfolio(id);
      say("ok", `Портфель «${name.value.trim()}» создан.`);
      draw();
    } catch (e) { put($("#pf-err"), errorNotice(e)); }
  };
  const form = h("form", { class: "pf-create", onsubmit: (e) => { e.preventDefault(); create(); } },
    h("div", { class: "field" }, h("label", { for: "pf-new" }, "Новый портфель"), name),
    h("button", { type: "submit", class: "btn btn-primary" }, "Создать"));
  const del = pfs.length ? h("button", { type: "button", class: "btn btn-sm pf-del", onclick: () => {
    const p = pfs.find((x) => x.id === pid);
    if (!window.confirm(`Удалить портфель «${p.name}» и все его операции? Это нельзя отменить.`)) return;
    store.deletePortfolio(pid);
    say("ok", `Портфель «${p.name}» удалён.`);
    draw();
  } }, "Удалить портфель") : null;
  return h("section", { class: "card my-bar" },
    h("div", { class: "my-row" }, pfs.length ? portfolioSelect(pid, () => draw(), "Портфель", "pf-pick-my") : null, pfs.length ? del : null, form),
    h("div", { id: "pf-err" }));
}

// ---------------------------------------------------------------- добавление операции, импорт и экспорт

function txCard(tx) {
  const E = engine.ledger;
  const types = E?.TX_TYPES ? Object.entries(E.TX_TYPES) : [["DEPOSIT", "Пополнение"], ["BUY", "Покупка"]];
  const f = {
    date: h("input", { type: "date", id: "tx-date", value: today(), required: true }),
    type: h("select", { id: "tx-type" }, types.map(([k, v]) => h("option", { value: k }, v))),
    secid: h("input", { type: "text", id: "tx-secid", placeholder: "SBER", autocomplete: "off", maxlength: "20" }),
    qty: h("input", { type: "text", id: "tx-qty", inputmode: "decimal", placeholder: "0" }),
    price: h("input", { type: "text", id: "tx-price", inputmode: "decimal", placeholder: "0" }),
    amount: h("input", { type: "text", id: "tx-amount", inputmode: "decimal", placeholder: "0", "aria-describedby": "tx-amount-hint" }),
    fee: h("input", { type: "text", id: "tx-fee", inputmode: "decimal", placeholder: "0" }),
  };
  const err = h("div");
  const field = (label, el, id) => h("div", { class: "field" }, h("label", { for: id }, label), el);
  const form = h("form", { class: "tx-form", novalidate: true, onsubmit: (e) => {
    e.preventDefault();
    const n = (el) => (el.value.trim() === "" ? 0 : parseNum(el.value));
    const vals = { qty: n(f.qty), price: n(f.price), amount: n(f.amount), fee: n(f.fee) };
    if (Object.values(vals).some((v) => !Number.isFinite(v) || v < 0)) return put(err, notice("error", "Количество, цена, сумма и комиссия — неотрицательные числа."));
    try {
      store.addTx(pid, { date: f.date.value, type: f.type.value, secid: f.secid.value, ...vals });
      say("ok", "Операция добавлена.");
      draw();
    } catch (ex) { put(err, errorNotice(ex)); }
  } },
  h("div", { class: "form-grid" },
    field("Дата", f.date, "tx-date"), field("Тип", f.type, "tx-type"), field("Тикер", f.secid, "tx-secid"),
    field("Количество, шт.", f.qty, "tx-qty"), field("Цена, ₽", f.price, "tx-price"),
    field("Сумма, ₽", f.amount, "tx-amount"), field("Комиссия, ₽", f.fee, "tx-fee")),
  h("p", { class: "hint", id: "tx-amount-hint" }, "Для покупок и продаж сумму можно оставить пустой — будет количество × цена. Для пополнения и вывода укажите только сумму."),
  h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary" }, "Добавить")), err);
  return h("details", { class: "card my-details", open: tx.length === 0 }, h("summary", {}, "Добавить операцию"), form);
}

const SAMPLE_CSV = "date,type,secid,qty,price,amount,fee,note\n2025-01-10,DEPOSIT,,0,0,500000,0,Первое пополнение\n2025-01-13,BUY,SBER,100,280,,15,\n2025-03-05,BUY,LQDT,1000,1.9,,0,\n";

function dataCard(tx) {
  const csvIn = h("input", { type: "file", id: "csv-file", accept: ".csv,text/csv,text/plain" });
  const jsonIn = h("input", { type: "file", id: "json-file", accept: ".json,application/json" });
  const err = h("div");
  const readFile = async (input) => {
    const file = input.files?.[0];
    if (!file) throw new UserError("Сначала выберите файл.");
    return file.text();
  };
  const importCsv = async () => {
    try {
      const n = store.importCsv(pid, await readFile(csvIn));
      say("ok", `Импортировано ${fmtNum(n)} операций.`);
      draw();
    } catch (e) { put(err, errorNotice(e)); }
  };
  const importJson = async () => {
    try {
      const text = await readFile(jsonIn);
      let mode = "replace";
      if (!store.isEmpty()) {
        if (window.confirm("Заменить текущие данные?\n\nОК — заменить всё данными из файла.\nОтмена — выбрать другой вариант.")) mode = "replace";
        else if (window.confirm("Добавить портфели из файла к текущим? Портфели с одинаковым названием будут дополнены операциями из файла.")) mode = "merge";
        else return;
      }
      const n = store.importJson(text, mode);
      say("ok", `Данные загружены: операций в файле — ${fmtNum(n)}.`);
      draw();
    } catch (e) { put(err, errorNotice(e)); }
  };
  const csvBlock = tx ? h("div", { class: "data-col" },
    h("h4", {}, "Импорт операций из CSV"),
    h("p", { class: "hint" }, "Столбцы: date, type, secid, qty, price, amount, fee, note. Дата — ГГГГ-ММ-ДД; тип — BUY, SELL, DIVIDEND, COUPON, DEPOSIT, WITHDRAW, FEE или TAX."),
    h("div", { class: "field" }, h("label", { for: "csv-file" }, "Файл CSV"), csvIn),
    h("div", { class: "toolbar" },
      h("button", { type: "button", class: "btn btn-sm btn-primary", onclick: importCsv }, "Импортировать"),
      h("button", { type: "button", class: "btn btn-sm", onclick: () => downloadBlob("portfolio_sample.csv", `﻿${SAMPLE_CSV}`, "text/csv;charset=utf-8") }, "Скачать образец"))) : null;
  const exportBlock = h("div", { class: "data-col" },
    h("h4", {}, "Экспорт и перенос данных"),
    h("p", { class: "hint" }, "JSON — все портфели и операции: им можно восстановить данные в другом браузере. CSV — операции выбранного портфеля."),
    h("div", { class: "toolbar" },
      tx ? h("button", { type: "button", class: "btn btn-sm", disabled: !tx.length, onclick: () => {
        try { downloadBlob("portfolio.csv", `﻿${engine.ledger.toCsv(tx)}`, "text/csv;charset=utf-8"); } catch (e) { put(err, errorNotice(e)); }
      } }, "Экспорт CSV") : null,
      h("button", { type: "button", class: "btn btn-sm", disabled: store.isEmpty(), onclick: () => downloadBlob("finserv-portfolio.json", store.exportJson(), "application/json") }, "Экспорт JSON")),
    h("div", { class: "field" }, h("label", { for: "json-file" }, "Загрузить JSON"), jsonIn),
    h("button", { type: "button", class: "btn btn-sm", onclick: importJson }, "Загрузить из JSON"));
  return h("details", { class: "card my-details" }, h("summary", {}, "Импорт и экспорт"),
    h("div", { class: "data-grid" }, csvBlock, exportBlock), err);
}

// ---------------------------------------------------------------- итоги и вкладки

function renderBody(body, tx, v, my) {
  const E = engine;
  const { cash, missing, quotes } = v;
  const { total, netIn, gain, gainPct, xirrFlows, alloc } = E.ledger.portfolioSummary(v.rows, cash, E.ledger.externalFlows(tx), today());
  const irr = xirrFlows.length > 1 && !missing.length ? E.backtest.xirr(xirrFlows) : NaN;
  facts = [
    fact("my.total", "Стоимость портфеля", total, "rub", fmtRub(total), SRC),
    fact("my.net_in", "Внесено (нетто)", netIn, "rub", fmtRub(netIn), SRC),
    fact("my.gain", "Результат, ₽", gain, "rub", fmtRub(gain), SRC),
    gainPct != null ? fact("my.gain_pct", "Результат, %", gainPct, "pct", fmtPct(gainPct), SRC) : null,
    fact("my.xirr", "Годовая доходность с учётом пополнений (XIRR)", irr, "pct", fmtPct(irr), SRC),
    fact("my.cash", "Свободные деньги", cash, "rub", fmtRub(cash), SRC),
  ].filter(Boolean);

  const kpi = (name, value, sub, cls) => h("div", { class: "kpi" }, h("div", { class: "kpi-name" }, name),
    h("div", { class: `kpi-value ${cls || ""}` }, value), sub ? h("div", { class: "kpi-sub" }, sub) : null);
  const tabs = [["pos", "Позиции"], ["dyn", "Динамика и метрики"], ["cmp", "Сравнение со стратегиями"], ["ops", "Операции"]];
  const pane = h("div", { class: "my-pane", id: "my-pane", role: "tabpanel", tabindex: "0" });
  const bar = h("div", { class: "my-tabs", role: "tablist", "aria-label": "Разделы портфеля" });
  const show = (key) => {
    tab = key;
    for (const b of bar.children) {
      const on = b.dataset.tab === key;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    }
    pane.setAttribute("aria-labelledby", `my-tab-${key}`);
    const ctx = { tx, v, my, alloc };
    put(pane, h("p", { class: "muted", role: "status" }, "Загрузка…"));
    const fn = { pos: tabPositions, dyn: tabDynamics, cmp: tabCompare, ops: tabOps }[key];
    Promise.resolve().then(() => fn(ctx)).then((nodes) => { if (my === token && tab === key) put(pane, nodes); })
      .catch((e) => { if (my === token && tab === key) put(pane, errorNotice(e)); });
  };
  for (const [k, t] of tabs) bar.append(h("button", { type: "button", role: "tab", class: "my-tab", id: `my-tab-${k}`, "aria-controls": "my-pane", "data-tab": k, onclick: () => show(k) }, t));
  bar.addEventListener("keydown", (e) => {
    const btns = [...bar.children];
    const i = btns.indexOf(document.activeElement);
    const to = { ArrowRight: (i + 1) % btns.length, ArrowLeft: (i - 1 + btns.length) % btns.length, Home: 0, End: btns.length - 1 }[e.key];
    if (i < 0 || to == null) return;
    e.preventDefault();
    btns[to].focus();
    show(btns[to].dataset.tab);
  });

  put(body,
    missing.length ? notice("warn", `Нет цены для: ${missingText(missing, quotes)}. Эти бумаги не включены в стоимость портфеля и результат; XIRR не рассчитан.`) : null,
    h("div", { class: "kpis" },
      kpi("Стоимость портфеля", fmtRub(total)),
      kpi("Внесено (нетто)", fmtRub(netIn)),
      kpi("Результат", fmtRub(gain), gainPct != null ? fmtPct(gainPct) : null, gain < 0 ? "neg" : gain > 0 ? "pos" : ""),
      kpi("XIRR (годовая)", fmtPct(irr), "с учётом дат пополнений"),
      kpi("Свободные деньги", fmtRub(cash))),
    h("div", { class: "my-tabs-wrap" }, bar, pane),
    h("p", { class: "hint" }, "Расчёт по вашим операциям и котировкам Мосбиржи. Прошлая доходность не гарантирует будущей. Не является индивидуальной инвестиционной рекомендацией."));
  show(tabs.some(([k]) => k === tab) ? tab : "pos");
}

function tabPositions({ v, alloc }) {
  const { rows, quotes } = v;
  if (!rows.length) return notice("info", "Открытых позиций нет — только деньги.");
  const th = (t, num) => h("th", { scope: "col", class: num ? "num" : "" }, t);
  const n2 = (x) => fmtNum(x, 2);
  const table = h("table", { class: "my-table" },
    h("thead", {}, h("tr", {}, th("Бумага"), th("Кол-во", 1), th("Ср. цена, ₽", 1), th("Цена, ₽", 1), th("Стоимость, ₽", 1), th("Вложено, ₽", 1),
      th("Нереал. результат, ₽", 1), th("Реал. результат, ₽", 1), th("Доход (дивиденды, купоны), ₽", 1), th("Доля", 1), th("Доходность", 1))),
    h("tbody", {}, rows.map((r) => h("tr", {},
      h("th", { scope: "row" }, h("span", { class: "sec-cell" }, assetBadge(r.secid, 24, quotes[r.secid]?.market === "bonds" ? "bond" : null),
        h("span", {}, h("b", {}, r.secid), r.name ? h("span", { class: "sec-name" }, r.name) : null))),
      h("td", { class: "num" }, fmtNum(r.qty, Number.isInteger(r.qty) ? 0 : 4)), h("td", { class: "num" }, n2(r.avgCost)),
      h("td", { class: "num" }, Number.isFinite(r.price) ? n2(r.price) : "—"), h("td", { class: "num" }, Number.isFinite(r.value) ? fmtNum(r.value, 0) : "—"),
      h("td", { class: "num" }, fmtNum(r.invested, 0)), h("td", { class: "num" }, fmtNum(r.unrealized, 0)),
      h("td", { class: "num" }, fmtNum(r.realized, 0)), h("td", { class: "num" }, fmtNum(r.income, 0)),
      h("td", { class: "num" }, fmtPct(r.share, 1)), h("td", { class: "num" }, fmtPct(r.ret, 1))))));
  const canvas = h("canvas", { role: "img", "aria-label": "Доли бумаг и денег в портфеле" });
  const box = h("div", { class: "chart-box", style: { height: `${Math.max(180, alloc.length * 38 + 70)}px` } }, canvas);
  whenIn(canvas, () => drawAlloc(canvas, alloc));
  return [h("section", { class: "card" }, h("h3", { class: "card-title" }, "Позиции"), tableWrap(table)),
    alloc.length ? h("section", { class: "card" }, h("h3", { class: "card-title" }, "Доли в портфеле"), box) : null];
}

function drawAlloc(canvas, alloc) {
  if (!canvas.isConnected || !window.Chart) return;
  renderChart(canvas, () => {
    const t = themeColors();
    const opts = baseOptions({ legend: false });
    opts.indexAxis = "y";
    opts.plugins.tooltip.callbacks = { label: (it) => ` ${fmtPct(it.parsed.x, 1)} · ${fmtRub(alloc[it.dataIndex].value)}` };
    opts.scales = {
      x: { grid: { color: t.grid }, border: { display: false }, ticks: { color: t.muted, callback: (x) => `${fmtNum(x * 100, 0)} %` }, title: { display: true, text: "Доля в портфеле, %", color: t.muted } },
      y: { grid: { display: false }, ticks: { color: t.text } },
    };
    return { type: "bar", data: { labels: alloc.map((a) => a.label), datasets: [{ data: alloc.map((a) => a.share),
      backgroundColor: alloc.map((a) => colorOf(a.label)), maxBarThickness: 26, borderRadius: 4 }] }, options: opts };
  });
}

// ---- история стоимости (общая для вкладок «Динамика» и «Сравнение»)

async function history({ tx, v }) {
  const key = `${pid}|${tx.length}|${tx.at(-1)?.id}|${today()}`;
  if (!histCache.has(key)) {
    histCache.clear();
    histCache.set(key, (async () => {
      const first = tx[0].date;
      const start = new Date(Date.parse(first) - 14 * 864e5).toISOString().slice(0, 10);
      const prices = await engine.quotes.priceHistoryRub(v.held, start, today());
      return engine.ledger.valueHistory(tx, prices);
    })().catch((e) => { histCache.delete(key); throw e; }));
  }
  return histCache.get(key);
}

const histMissingNotes = (hist) => {
  const miss = hist.missingPrices || [];
  if (!miss.length) return null;
  const gaps = hist.dates.filter((_, i) => !Number.isFinite(hist.value[i]));
  const when = gaps.length ? ` (${fmtDate(gaps[0])} — ${fmtDate(gaps.at(-1))})` : "";
  return [notice("warn", `Нет цены для: ${miss.join(", ")} в части дней удержания${when}. На графике стоимости — разрывы.`),
    notice("info", "TWR, метрики и сравнение со стратегиями не рассчитаны: в истории стоимости есть дни без оценки части позиций.")];
};

async function tabDynamics(ctx) {
  let hist;
  try { hist = await history(ctx); } catch (e) { return notice("error", `Не удалось построить историю: ${e.message}`); }
  if (!hist?.dates?.length) return notice("info", "История стоимости пока пуста.");
  const name = store.portfolio(pid).name;
  const valCanvas = h("canvas", { role: "img", "aria-label": "График стоимости портфеля" });
  const out = [h("section", { class: "card" }, h("h3", { class: "card-title" }, "Стоимость портфеля"),
    h("div", { class: "chart-box" }, valCanvas))];
  whenIn(valCanvas, () => lineChart(valCanvas, { series: [{ name, dates: hist.dates, values: hist.value }], yTitle: "Стоимость, ₽", yFormat: "money" }));
  const notes = histMissingNotes(hist);
  if (notes) return [...out, ...notes];

  const g = globals();
  const bench = g.bench;
  const start = hist.dates[0];
  const { series, errors } = await getMany([bench], start, today());
  const twr = engine.ledger.twrPercent(hist.twr);
  const comp = [{ name, dates: hist.dates, values: twr }];
  let b = null;
  if (series[bench]) {
    const vals = ffillOn(hist.dates, series[bench]);
    const i0 = vals.findIndex(Number.isFinite);
    if (i0 >= 0) { b = { dates: hist.dates.slice(i0), values: vals.slice(i0) }; }
  }
  if (b) comp.push({ name: bench, ...engine.metrics.rebase(b, 100), bench: true });
  else out.push(notice("warn", `Бенчмарк ${bench}: ${errors[bench]?.message || "нет данных за период"} Показатели относительно рынка не рассчитаны.`));
  const twrCanvas = h("canvas", { role: "img", "aria-label": "Доходность портфеля без учёта пополнений против бенчмарка" });
  out.push(h("section", { class: "card" }, h("h3", { class: "card-title" }, "Доходность без учёта пополнений (TWR) против бенчмарка"),
    h("p", { class: "card-sub" }, "База = 100 на первую дату"), h("div", { class: "chart-box" }, twrCanvas)));
  whenIn(twrCanvas, () => lineChart(twrCanvas, { series: comp, yTitle: "База = 100", yFormat: "num" }));
  out.push(await metricsCard(comp, b ? bench : null, g));
  return out;
}

async function metricsCard(comp, bench, g) {
  const n = comp[0].dates.length;
  if (n <= 40) return h("p", { class: "hint" }, "Метрики появятся, когда история портфеля будет длиннее ~2 месяцев.");
  const RF = await riskFree(comp[0].dates[0], comp[0].dates.at(-1));
  const freq = n < 400 ? "D" : g.freq;
  const bseries = comp.find((c) => c.bench);
  const bs = bseries ? { dates: bseries.dates, values: bseries.values } : null;
  const reports = {};
  for (const c of comp) reports[c.name] = engine.metrics.computeAll({ dates: c.dates, values: c.values }, bs, RF.rf, freq);
  const cols = comp.map((c) => c.name);
  return h("section", { class: "card" }, h("h3", { class: "card-title" }, "Показатели эффективности"),
    h("p", { class: "card-sub" }, `${RF.note} Доходности ${FREQ_LABELS[freq]}${bench ? `, бенчмарк ${bench}` : ""}.`),
    tableWrap(buildMetricsTable(cols, reports, (c) => colorOf(c, { bench: c === bench }))));
}

async function tabCompare(ctx) {
  const STR = engine.strategies.STRATEGIES;
  let hist;
  try { hist = await history(ctx); } catch (e) { return notice("error", `Не удалось построить историю: ${e.message}`); }
  const intro = h("p", { class: "card-sub" }, "Сравнение доходности портфеля без учёта пополнений (TWR) с классическими пассивными стратегиями на том же отрезке.");
  if (hist?.missingPrices?.length) return [intro, notice("info", "Сравнение не рассчитано: в истории стоимости портфеля есть дни без оценки части позиций (см. вкладку «Динамика и метрики»).")];
  if (!hist?.dates?.length || hist.dates.length <= 20) return [intro, notice("info", "Для сравнения нужна история подлиннее — хотя бы пара недель.")];
  const names = Object.keys(STR);
  const picked = new Set(names.slice(0, 3));
  const list = h("div", { class: "pick-list", role: "group", "aria-label": "Стратегии" }, names.map((nm) => {
    const cb = h("input", { type: "checkbox", checked: picked.has(nm), onchange: () => { cb.checked ? picked.add(nm) : picked.delete(nm); run(); } });
    return h("label", { class: "pick" }, cb, h("span", {}, nm));
  }));
  const res = h("div");
  const pfName = store.portfolio(pid).name;
  let run_id = 0;
  async function run() {
    const mine = ++run_id;
    put(res, h("p", { class: "muted", role: "status" }, "Считаю…"));
    try {
      const s0 = hist.dates[0], s1 = hist.dates.at(-1);
      const g = globals();
      const keys = [...new Set([...[...picked].flatMap((p) => Object.keys(STR[p].weights)), g.bench])];
      const { series, errors } = await getMany(keys, s0, s1);
      if (mine !== run_id) return;
      const comp = [{ name: pfName, dates: hist.dates, values: engine.ledger.twrPercent(hist.twr) }];
      const warns = [];
      for (const p of picked) {
        const w = STR[p].weights;
        const miss = Object.keys(w).filter((k) => !series[k]);
        if (miss.length) { warns.push(`${p}: нет данных — ${miss.map((k) => errors[k]?.message || k).join(" ")}`); continue; }
        try {
          let frame = engine.data.alignFrame(Object.fromEntries(Object.keys(w).map((k) => [k, series[k]])), { mode: "common" });
          frame = engine.data.sliceFrame(frame, s0, s1);
          if (frame.dates.length <= 2) { warns.push(`${p}: нет общего периода данных.`); continue; }
          const r = engine.backtest.runBacktest(frame, { weights: w, initial: 100, rebalance: "A", start: s0, end: s1 });
          comp.push({ name: p, ...r.equity });
        } catch (e) { warns.push(`${p}: расчёт не выполнен — ${e.message}`); }
      }
      let b = null;
      if (series[g.bench]) {
        const vals = ffillOn(hist.dates, series[g.bench]);
        const i0 = vals.findIndex(Number.isFinite);
        if (i0 >= 0) b = engine.metrics.rebase({ dates: hist.dates.slice(i0), values: vals.slice(i0) }, 100);
      }
      if (b) comp.push({ name: g.bench, ...b, bench: true });
      else warns.push(`Бенчмарк ${g.bench}: нет данных за период.`);
      const canvas = h("canvas", { role: "img", "aria-label": "Портфель и стратегии: доходность без учёта пополнений" });
      put(res, warns.map((t) => notice("warn", t)),
        h("section", { class: "card" }, h("div", { class: "chart-box" }, canvas)),
        comp.length > 1 ? await metricsCard(comp, b ? g.bench : null, g) : null);
      lineChart(canvas, { series: comp.map((c) => ({ ...c, name: c.name, bench: c.bench })), yTitle: "База = 100", yFormat: "num" });
    } catch (e) { if (mine === run_id) put(res, errorNotice(e)); }
  }
  later(run);
  return [h("section", { class: "card" }, h("h3", { class: "card-title" }, "Сравнение со стратегиями"), intro, list), res];
}

function tabOps({ tx }) {
  const T = engine.ledger.TX_TYPES;
  const th = (t, n) => h("th", { scope: "col", class: n ? "num" : "" }, t);
  const table = h("table", { class: "my-table" },
    h("thead", {}, h("tr", {}, th("Дата"), th("Тип"), th("Тикер"), th("Кол-во", 1), th("Цена, ₽", 1), th("Сумма, ₽", 1), th("Комиссия, ₽", 1), h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Действия")))),
    h("tbody", {}, [...tx].reverse().map((t) => h("tr", {},
      h("td", {}, fmtDate(t.date)), h("td", {}, T[t.type] || t.type), h("td", {}, t.secid || "—"),
      h("td", { class: "num" }, t.qty ? fmtNum(t.qty, Number.isInteger(t.qty) ? 0 : 4) : "—"), h("td", { class: "num" }, t.price ? fmtNum(t.price, 2) : "—"),
      h("td", { class: "num" }, fmtNum(t.amount, 2)), h("td", { class: "num" }, t.fee ? fmtNum(t.fee, 2) : "—"),
      h("td", {}, h("button", { type: "button", class: "btn btn-sm", "aria-label": `Удалить операцию: ${T[t.type] || t.type}${t.secid ? ` ${t.secid}` : ""}, ${fmtRub(t.amount)}, ${fmtDate(t.date)}`, onclick: () => {
        if (!window.confirm("Удалить операцию?")) return;
        store.deleteTx(t.id);
        say("ok", "Операция удалена.");
        draw();
      } }, "Удалить"))))));
  return h("section", { class: "card" }, h("h3", { class: "card-title" }, "Операции"), tableWrap(table, { maxHeight: "480px" }));
}
