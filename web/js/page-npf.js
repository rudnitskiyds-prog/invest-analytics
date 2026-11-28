// Раздел «Пенсионная индустрия» #/npf[/<раздел>[/<id>]] (web/CONTRACT.md, «Пенсионная индустрия»).
// Все числа считает web/lib/npf.js (инвариант 3), здесь — состояние, вызовы и вывод. Данные (~13 МБ) грузятся при первом заходе.
// Разделы: market · fund[/<id фонда>] · compare · securities[/<i:ISIN | g:N>] · data. Источник идеи и методики — приложение «Монитор портфелей НПФ».

import { DATA_BASE } from "./config.js";
import { baseOptions, destroyChart, palette, renderChart, themeColors } from "./charts.js";
import { $, downloadBlob, downloadCsv, h, notice, put, tableWrap } from "./dom.js";
import { csvNum, fmtNum } from "./format.js";

const SECTIONS = [["market", "Рынок"], ["fund", "Фонд"], ["compare", "Сравнение фондов"], ["securities", "Бумаги и эмитенты"], ["data", "Данные и методика"]];
const KINDS = [[0, "Резервы · ПР"], [1, "Накопления · ПН"], ["all", "Вместе"]];
const SRCS = [["struct", "Отчёты о структуре"], ["comp", "Расчёт по составу"]];
const INK = "#0E1726";

let N = null;             // web/lib/npf.js (подгружается динамически вместе с данными)
let ix = null;            // индекс базы
let loading = 0;          // номер текущей загрузки
let started = false;      // загрузка уже запускалась (повторные onShow её не перезапускают; ошибку чинит «Повторить»)
let shell = null;         // {head, bar, tabs, pane}
let searchEl = null;      // поле поиска живёт между перерисовками
let lastHash = "";
let timer = null;
const charts = [];
const jobs = [];

const S = {
  kind: 0, section: "market", routeId: null,
  market: { src: "struct", month: null, sort: "aum", dir: -1, mode: "share" },
  fund: { fi: null, src: "comp", grp: "class", idx: -1, tab: "overview", stack: "share", metric: "aum", q: "", cat: "", sort: "desc", from: null, to: null, all: false, date: null },
  cmp: { funds: [], metric: "0", src: "struct", month: null },
  sec: { q: "", month: null },
};

// ---------------------------------------------------------------- форматирование
const f1 = (x, d = 1) => fmtNum(x, d);
const sg = (x, d = 2) => (x == null || !Number.isFinite(x) ? "—" : (x > 0 ? "+" : "") + fmtNum(x, d));
const bn = (v) => (v == null ? "—" : Math.abs(v) >= 1e9 ? `${fmtNum(v / 1e9, 2)} млрд ₽` : `${fmtNum(v / 1e6, 1)} млн ₽`);
const longDate = (iso) => { const t = new Date(`${iso}T00:00:00`); return Number.isNaN(t.getTime()) ? iso : t.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }); };
const upDown = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const dot = (c) => h("i", { class: "npf-dot", style: { background: c }, "aria-hidden": "true" });
const dmy = (d) => N.dmy(d);
const ml = (m) => N.monthLabel(m);

// ---------------------------------------------------------------- маршрут и состояние
export function init() {}
export function onShow(route) {
  const p = route?.params || {};
  const known = SECTIONS.some(([k]) => k === p.section);
  S.section = known ? p.section : "market";
  S.routeId = p.id ?? null;
  S.note = p.section && !known ? `Раздел «${p.section}» не найден, показан «Рынок».` : null;
  if (ix && shell && lastHash === location.hash && shell.pane.childNodes.length) return;   // повторный вызов (STATIC) без смены адреса
  if (ix) { applyRoute(); draw(); lastHash = location.hash; } else if (!started) load();
}
export function onHide() { stopTimer(); }
export function onGlobalChange() {}
export function rerender() {}
export const STATIC = true;

/** Применить id из адреса (фонд) к состоянию. */
function applyRoute() {
  if (S.section !== "fund") return;
  const fi = S.routeId ? ix.F.findIndex((f) => f.id === S.routeId) : -1;
  if (fi >= 0 && fi !== S.fund.fi) resetFund(fi);
  else if (S.fund.fi == null) resetFund(Math.max(0, ix.F.findIndex((f) => f.id === "sber")));
  if (S.routeId && fi < 0) S.note = `Фонд «${S.routeId}» не найден, показан ${ix.F[S.fund.fi].name}.`;
}
function resetFund(fi, patch = {}) {
  Object.assign(S.fund, { fi, idx: -1, from: null, to: null, q: "", cat: "", all: false, ...patch });
  if (!ix.F[fi].comp) S.fund.src = "struct";
}
/** Перейти внутри страницы без hashchange (иначе сайт уводит фокус на заголовок); «назад» работает через hashchange. */
function nav(section, id = null, focusId = null) {
  const target = `#/npf/${section}${id ? `/${encodeURIComponent(id)}` : ""}`;
  if (location.hash !== target) history.pushState(null, "", `${location.pathname}${location.search}${target}`);
  lastHash = location.hash;
  S.section = section; S.routeId = id; S.note = null;
  applyRoute();
  stopTimer();
  draw(false, focusId);
  window.scrollTo({ top: 0 });
}
const openFund = (fi, patch, focusId = "npf-pane") => { resetFund(fi, patch); nav("fund", ix.F[fi].id, focusId); };
const secKey = (sel) => (sel.t === "g" ? `g:${sel.id}` : sel.id);
const stopTimer = () => { if (timer) { clearInterval(timer); timer = null; } };
const link = (href, fn, attrs, ...kids) => h("a", { href, ...attrs, onclick: (e) => { if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return; e.preventDefault(); fn(); } }, ...kids);
const fundLink = (fi, text, cls) => link(`#/npf/fund/${encodeURIComponent(ix.F[fi].id)}`, () => openFund(fi), { class: cls }, text);

// ---------------------------------------------------------------- загрузка данных
function head(meta) {
  return h("div", { class: "panel-head npf-head" }, h("h2", {}, "Пенсионная индустрия"),
    h("p", { class: "lead" }, "Монитор портфелей негосударственных пенсионных фондов: как фонды размещают пенсионные резервы и накопления, сколько у них активов и какие бумаги они держат."),
    h("p", { class: "npf-meta hint" }, meta ? `Данные на ${longDate(meta.built)} · структура обновлена ${longDate(meta.monitorUpdated)} · ` : "Данные загружаются · ",
      "Источник: ", h("a", { href: "https://www.cbr.ru/", target: "_blank", rel: "noopener" }, "Раскрытие НПФ и Банк России")));
}

async function load() {
  started = true;
  const root = $("#npf-root");
  const my = ++loading;
  const bar = h("progress", { max: "100", value: "0", "aria-label": "Загрузка данных НПФ" });
  const txt = h("p", { class: "muted", role: "status" }, "Загружаю данные НПФ (≈ 13 МБ)…");
  put(root, head(null), h("div", { class: "card npf-loading" }, txt, bar, h("div", { class: "skeleton", "aria-hidden": "true" }, h("span"), h("span"), h("span"))));
  try {
    N = await import("../lib/npf.js");
    const x = await N.getNpf({ base: DATA_BASE, onProgress: ({ loaded, total }) => {
      if (my !== loading) return;
      const pct = Math.round(loaded / total * 100);
      bar.value = pct; txt.textContent = `Загружаю данные НПФ: ${pct} %`;
    } });
    if (my !== loading) return;
    ix = x;
    build(root);
  } catch (e) {
    if (my !== loading) return;
    console.warn(e);
    put(root, head(null), notice("error", `Не удалось загрузить данные НПФ: ${e.message || e}`),
      h("button", { type: "button", class: "btn btn-primary", id: "npf-retry", onclick: () => load() }, "Повторить"));
    $("#npf-retry")?.focus();
  }
}

function build(root) {
  shell = { bar: h("div", { class: "npf-barwrap" }), tabs: h("div"), pane: h("div", { class: "npf-pane", id: "npf-pane", role: "tabpanel", tabindex: "0" }) };
  searchEl = buildSearch();
  const hadFocus = root.contains(document.activeElement);   // фокус был на заголовке заглушки загрузки
  put(root, head(ix.db), shell.bar, shell.tabs, shell.pane);
  applyRoute();
  draw();
  lastHash = location.hash;
  if (hadFocus) shell.pane.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- общая перерисовка
function draw(keepTimer = false, focusId = null) {
  if (!ix) return;
  const act = document.activeElement;
  const fid = focusId || (act && $("#npf-root")?.contains(act) ? act.id : "");
  const caret = !focusId && act && typeof act.selectionStart === "number" ? act.selectionStart : null;
  if (!keepTimer) stopTimer();
  charts.splice(0).forEach(destroyChart);
  jobs.length = 0;
  let content;
  try { content = { market: sMarket, fund: sFund, compare: sCompare, securities: sSecurities, data: sData }[S.section](); } catch (e) {
    console.warn(e);
    content = notice("error", `Не удалось построить раздел: ${e.message || e}`);
  }
  shell.pane.setAttribute("aria-labelledby", `npf-tab-${S.section}`);
  if (location.hash.startsWith("#/npf")) document.title = `Пенсионная индустрия — ${SECTIONS.find(([k]) => k === S.section)[1]} — FinServ`;
  if (S.note) content = [notice("warn", S.note), ...[].concat(content)];
  put(shell.bar, toolbar());
  put(shell.tabs, tabBar(SECTIONS.map(([k, t]) => [k, t]), S.section, (k) => nav(k, k === "fund" ? ix.F[S.fund.fi ?? 0]?.id : null, `npf-tab-${k}`), "Разделы страницы", "npf-tab", "npf-pane"));
  put(shell.pane, content);
  jobs.splice(0).forEach((fn) => fn());
  const el = fid && document.getElementById(fid);
  if (el && !el.disabled) {
    el.focus({ preventScroll: true });
    if (caret != null) try { el.setSelectionRange(caret, caret); } catch { /* не текстовое поле */ }
  }
}

// ---------------------------------------------------------------- элементы управления
function tabBar(items, cur, pick, label, prefix, paneId) {
  const bar = h("div", { class: "my-tabs npf-tabs", role: "tablist", "aria-label": label });
  for (const [k, t, dis] of items) {
    bar.append(h("button", { type: "button", role: "tab", class: "my-tab", id: `${prefix}-${k}`, "aria-selected": String(k === cur), "aria-controls": paneId,
      tabindex: k === cur ? "0" : "-1", disabled: dis, title: dis ? "Недоступно для этого фонда" : null, onclick: () => pick(k) }, t));
  }
  bar.addEventListener("keydown", (e) => {
    const b = [...bar.querySelectorAll("button:not([disabled])")];
    const i = b.indexOf(document.activeElement);
    const to = { ArrowRight: (i + 1) % b.length, ArrowLeft: (i - 1 + b.length) % b.length, Home: 0, End: b.length - 1 }[e.key];
    if (i < 0 || to == null) return;
    e.preventDefault();
    b[to].focus();
    b[to].click();
  });
  return bar;
}

function seg(label, id, items, cur, pick) {
  return h("div", { class: "seg npf-seg", role: "group", "aria-label": label },
    items.map(([v, t, dis, title]) => h("button", { type: "button", class: "seg-btn", id: `${id}-${v}`, "aria-pressed": String(String(cur) === String(v)), disabled: dis, title, onclick: () => pick(v) }, t)));
}
const field = (label, id, ctl, cls) => h("div", { class: `field ${cls || ""}` }, h("label", { for: id }, label), ctl);
const segField = (label, node) => h("div", { class: "field" }, h("span", { class: "label" }, label), node);
function select(id, options, cur, onPick, label) {
  const el = h("select", { id, "aria-label": label || null }, options.map(([v, t]) => h("option", { value: v, selected: String(v) === String(cur) }, t)));
  el.value = String(cur);
  el.addEventListener("change", () => onPick(el.value));
  return el;
}
function monthPick(id, avail, cur, onPick, label = "Месяц", withArrows = true) {
  const i = avail.findIndex((x) => x.month === cur);
  const sel = select(id, [...avail].reverse().map((x) => [x.month, `${ml(x.month)} · ${x.count} фонд.`]), cur, onPick);
  const arrow = (d, t, aria) => h("button", { type: "button", class: "btn npf-arrow", id: `${id}-${d < 0 ? "prev" : "next"}`, "aria-label": aria, disabled: !avail[i + d], onclick: () => onPick(avail[i + d].month) }, t);
  return h("div", { class: "field" }, h("label", { for: id }, label),
    h("div", { class: "npf-monthrow" }, withArrows ? arrow(-1, "←", "Предыдущий месяц") : null, sel, withArrows ? arrow(1, "→", "Следующий месяц") : null));
}
const legend = (items) => h("ul", { class: "npf-legend", "aria-label": "Легенда" }, items.map(([name, c]) => h("li", {}, dot(c), name)));
const kpi = (name, value, sub) => h("div", { class: "kpi" }, h("div", { class: "kpi-name" }, name), h("div", { class: "kpi-value" }, value), sub != null ? h("div", { class: "kpi-sub" }, sub) : null);
const badge = (text, kind) => h("span", { class: `npf-badge ${kind || ""}` }, text);
const th = (t, cls, extra) => h("th", { scope: "col", class: cls || "", ...extra }, t);
const empty = (t) => h("p", { class: "npf-empty muted" }, t);
const card = (title, sub, ...body) => h("section", { class: "card npf-card" }, h("h3", { class: "card-title" }, title), sub ? h("p", { class: "card-sub" }, sub) : null, ...body);
const cardHead = (title, sub, ...actions) => h("div", { class: "npf-cardhead" }, h("div", {}, h("h3", { class: "card-title" }, title), sub ? h("p", { class: "card-sub" }, sub) : null), actions.length ? h("div", { class: "toolbar" }, ...actions) : null);
const csvBtn = (id, fn) => h("button", { type: "button", class: "btn btn-sm", id, onclick: fn }, "Скачать CSV");
const noteP = (t) => h("p", { class: "hint npf-note" }, t);

function toolbar() {
  const fund = S.section === "fund" ? ix.F[S.fund.fi] : null;
  const kinds = KINDS.map(([v, t]) => {
    let dis = false;
    if (fund) dis = v === "all" ? N.fundKinds(ix, S.fund.fi).length < 2 : !(N.fundKinds(ix, S.fund.fi).includes(v) || N.fundSKinds(ix, S.fund.fi).includes(v));
    return [v, t, dis, v === 0 ? "Пенсионные резервы (НПО)" : v === 1 ? "Пенсионные накопления (ОПС)" : "Резервы и накопления вместе"];
  });
  let srcNode = null;
  if (S.section === "market") srcNode = seg("Источник долей", "npf-src", SRCS.map(([v, t]) => [v, t, v === "struct" && S.kind === "all", v === "struct" && S.kind === "all" ? "Отчёты о структуре не содержат сумм, сложить портфели нельзя" : null]), S.market.src, setSrc);
  else if (S.section === "fund") srcNode = seg("Источник структуры фонда", "npf-src", [["comp", "Состав портфеля", !fund.comp], ["struct", "Отчёт о структуре", !fund.mid || S.kind === "all"]], S.fund.src, setSrc);
  else if (S.section === "compare") srcNode = seg("Источник долей", "npf-src", SRCS.map(([v, t]) => [v, t, v === "struct" && (S.cmp.metric === "aum" || S.kind === "all")]), S.cmp.metric === "aum" ? "comp" : S.cmp.src, setSrc);
  return h("section", { class: "card npf-bar" }, h("div", { class: "npf-bar-row" },
    segField("Вид средств", seg("Вид средств", "npf-kind", kinds, S.kind, setKind)),
    srcNode ? segField(S.section === "fund" ? "Источник структуры" : "Источник долей", srcNode) : null, searchEl));
}

function setKind(k) {
  stopTimer();
  const date = S.section === "fund" ? S.fund.date : null;
  S.kind = k; S.market.month = null; S.sec.month = null;
  if (S.section === "fund") {
    const s = S.fund, f = ix.F[s.fi];
    if (k === "all" && s.src === "struct" && f.comp) s.src = "comp";
    const nd = N.fundDates(ix, s.fi, k, s.src);
    s.idx = date ? Math.max(nd.indexOf(date), nd.findIndex((x) => x.slice(0, 7) === date.slice(0, 7))) : -1;
    if (date && nd.indexOf(date) >= 0) s.idx = nd.indexOf(date);
  }
  draw(false, `npf-kind-${k}`);
}
function setSrc(v) {
  stopTimer();
  if (S.section === "market") { S.market.src = v; S.market.month = null; }
  else if (S.section === "compare") S.cmp.src = v;
  else {
    const s = S.fund, m = s.date?.slice(0, 7);
    s.src = v; s.from = s.to = null;
    const nd = N.fundDates(ix, s.fi, S.kind, v);
    s.idx = m ? nd.findIndex((x) => x.slice(0, 7) === m) : -1;
  }
  draw(false, `npf-src-${v}`);
}

// ---------------------------------------------------------------- поиск
function buildSearch() {
  const input = h("input", { type: "search", id: "npf-q", role: "combobox", "aria-expanded": "false", "aria-controls": "npf-list", "aria-autocomplete": "list", autocomplete: "off", placeholder: "Фонд, эмитент, бумага или ISIN" });
  const list = h("ul", { class: "npf-drop", id: "npf-list", role: "listbox", "aria-label": "Результаты поиска", hidden: true });
  let items = [], hl = -1;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); hl = -1; };
  const mark = (i) => {
    hl = i;
    [...list.querySelectorAll("[role=option]")].forEach((li, j) => { li.setAttribute("aria-selected", String(j === i)); if (j === i) { input.setAttribute("aria-activedescendant", li.id); li.scrollIntoView({ block: "nearest" }); } });
  };
  const pick = (i) => {
    const x = items[i];
    if (!x) return;
    const q = input.value.trim();
    input.value = ""; close();
    if (x.t === "f") openFund(x.i);
    else {
      S.sec.q = x.t === "q" ? q : "";
      nav("securities", x.t === "g" ? `g:${x.i}` : x.t === "i" ? x.key : null);
    }
  };
  const run = () => {
    const q = input.value.trim();
    if (q.length < 2) return close();
    const r = N.searchGlobal(ix, q);
    items = [...r.funds.map((x) => ({ t: "f", i: x.i, name: x.fund.name, sub: x.fund.comp ? "состав и структура" : "только структура", g: "Фонды" })),
      ...r.groups.map((x) => ({ t: "g", i: x.i, name: x.name.length > 70 ? `${x.name.slice(0, 69)}…` : x.name, sub: "эмитент", g: "Эмитенты" })),
      ...r.insts.map((x) => ({ t: "i", key: x.key, name: x.name, sub: x.isin || ix.CL[x.cls].s, g: "Бумаги" })),
      { t: "q", name: `Искать «${q}» во всех составах`, sub: "Enter", g: "Поиск" }];
    put(list, items.map((x, n) => {
      const li = h("li", { role: "option", id: `npf-opt-${n}`, class: "npf-opt", "aria-selected": "false", onmousedown: (e) => { e.preventDefault(); pick(n); } },
        h("span", {}, x.name), h("small", {}, `${x.g} · ${x.sub}`));
      return li;
    }));
    list.hidden = false; input.setAttribute("aria-expanded", "true");
    mark(0);
  };
  input.addEventListener("input", run);
  input.addEventListener("focus", () => { if (input.value.trim().length >= 2) run(); });
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    const open = !list.hidden, n = items.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { if (!open) return; e.preventDefault(); mark((hl + (e.key === "ArrowDown" ? 1 : -1) + n) % n); }
    else if (e.key === "Enter") { e.preventDefault(); if (open) pick(hl); else if (input.value.trim().length >= 2) { run(); pick(items.length - 1); } }
    else if (e.key === "Escape") { close(); }
  });
  return h("div", { class: "field npf-search" }, h("label", { for: "npf-q" }, "Поиск по фонду, эмитенту, бумаге или ISIN"), h("div", { class: "npf-searchbox" }, input, list));
}

// ---------------------------------------------------------------- графики (Chart.js)
function mount(canvas, factory) {
  charts.push(canvas);
  jobs.push(() => { if (window.Chart && canvas.isConnected) renderChart(canvas, factory); });
}
const chartBox = (aria, height = 300) => { const canvas = h("canvas", { role: "img", "aria-label": aria }); return { canvas, box: h("div", { class: "chart-box", style: { height: `${height}px` } }, canvas) }; };
const pointer = (onPick) => (onPick ? (e, _els, chart) => { const p = chart.getElementsAtEventForMode(e, "index", { intersect: false }, true); if (p.length) onPick(p[0].index); } : undefined);
function scales(t, xs, ys, stacked) {
  return {
    x: { stacked, type: "category", grid: { display: false }, border: { color: t.grid }, ticks: { color: t.muted, autoSkip: true, maxTicksLimit: window.innerWidth < 600 ? 4 : 10, maxRotation: 0, minRotation: 0 }, ...xs },
    y: { stacked, grid: { color: t.grid }, border: { display: false }, ticks: { color: t.muted, maxTicksLimit: 7 }, ...ys },
  };
}
/** Столбцы с накоплением по классам/секторам; vals[i] — {индекс: значение} или null. */
function stackChart(canvas, { labels, tips, cats, used, vals, pct, unit, sel, onPick }) {
  mount(canvas, () => {
    const t = themeColors(), o = baseOptions({ legend: false });
    o.interaction = { mode: "index", intersect: false };
    o.plugins.tooltip.itemSort = (a, b) => b.parsed.y - a.parsed.y;
    o.plugins.tooltip.filter = (it) => Number.isFinite(it.parsed.y) && Math.abs(it.parsed.y) > 1e-9;
    o.plugins.tooltip.callbacks = { title: (it) => tips?.[it[0].dataIndex] ?? it[0].label, label: (it) => ` ${it.dataset.label}: ${f1(it.parsed.y, pct ? 1 : 2)} ${pct ? "%" : unit}` };
    o.scales = scales(t, {}, { title: { display: true, text: pct ? "Доля, %" : unit, color: t.muted }, ...(pct ? { suggestedMax: 100 } : {}), ticks: { color: t.muted, maxTicksLimit: 7, callback: (v) => f1(v, pct || Math.abs(v) >= 10 ? 0 : 1) } }, true);
    o.onClick = pointer(onPick);
    return { type: "bar", data: { labels, datasets: used.map((k) => ({ label: cats[k].s || cats[k].n, data: vals.map((v) => v?.[k] ?? null), backgroundColor: cats[k].c, stack: "s", barPercentage: 0.95, categoryPercentage: 1,
      borderColor: (c) => (c.dataIndex === sel ? INK : "transparent"), borderWidth: (c) => (c.dataIndex === sel ? 1.5 : 0) })) }, options: o };
  });
}
/** Линии без заливки; v[i] === null — разрыв (нет отчёта). */
function lineChart(canvas, { labels, tips, series, tick, tip, yTitle, onPick, sel }) {
  mount(canvas, () => {
    const t = themeColors(), o = baseOptions({ legend: false });
    o.interaction = { mode: "index", intersect: false };
    o.plugins.tooltip.itemSort = (a, b) => b.parsed.y - a.parsed.y;
    o.plugins.tooltip.filter = (it) => Number.isFinite(it.parsed.y);
    o.plugins.tooltip.callbacks = { title: (it) => tips?.[it[0].dataIndex] ?? it[0].label, label: (it) => ` ${it.dataset.label}: ${tip(it.parsed.y)}` };
    o.scales = scales(t, {}, { beginAtZero: true, title: { display: true, text: yTitle, color: t.muted }, ticks: { color: t.muted, maxTicksLimit: 7, callback: (v) => tick(Number(v)) } }, false);
    o.onClick = pointer(onPick);
    return { type: "line", data: { labels, datasets: series.map((s) => ({ label: s.name, data: s.v, borderColor: s.color, backgroundColor: s.color, borderWidth: 2, tension: 0, fill: false, spanGaps: false,
      pointRadius: (c) => (c.dataIndex === sel ? 5 : s.v.filter((x) => x != null).length < 3 ? 4 : 0), pointHoverRadius: 4, pointHitRadius: 6 })) }, options: o };
  });
}
function donutChart(canvas, parts) {
  mount(canvas, () => {
    const o = baseOptions({ legend: false });
    o.cutout = "62%";
    o.plugins.tooltip.callbacks = { label: (it) => ` ${it.label}: ${f1(it.parsed, 2)} %` };
    return { type: "doughnut", data: { labels: parts.map((p) => p.name), datasets: [{ data: parts.map((p) => p.v), backgroundColor: parts.map((p) => p.c), borderColor: "#fff", borderWidth: 2 }] }, options: o };
  });
}
const seriesColor = (j) => palette()[j % 8];
/** Доля от АУМ текстом («1,23 %»); при нулевом/нечисловом АУМ — fallback. Арифметика — в web/lib/npf.js. */
const shareTxt = (value, aum, d, fallback = "—") => { const x = N.rowShare(value, aum); return x == null ? fallback : `${f1(x, d)} %`; };
const barW = (frac) => `${(frac ?? 0) * 100}%`;
const barRow = (label, valText, frac, color) => h("div", { class: "npf-barrow" }, h("div", { class: "npf-bartxt" }, label, h("b", { class: "num" }, valText)),
  h("div", { class: "npf-bar-track", "aria-hidden": "true" }, h("i", { style: { width: barW(frac), background: color || null } })));

// ================================================================ РЫНОК
function sMarket() {
  const s = S.market, CL = ix.CL;
  const o = N.marketOverview(ix, { kind: S.kind, src: s.src, month: s.month });
  s.src = o.src; s.month = o.month;
  if (!o.avail.length) return notice("info", "Для выбранного вида средств данных нет.");
  const kname = S.kind === "all" ? "резервы и накопления" : N.KNL[S.kind].toLowerCase();
  const pick = (m) => { s.month = m; draw(false, "npf-m-month"); };
  const rows = N.sortMarketRows(ix, o.rows, { sort: s.sort, dir: s.dir });
  const arrow = (k) => (s.sort === k ? (s.dir < 0 ? " ↓" : " ↑") : "");
  const sortTh = (k, text, cls, title) => h("th", { scope: "col", class: cls, "aria-sort": s.sort === k ? (s.dir < 0 ? "descending" : "ascending") : "none", title },
    h("button", { type: "button", class: "npf-sortbtn", id: `npf-sort-${k}`, onclick: () => { if (s.sort === k) s.dir *= -1; else { s.sort = k; s.dir = k === "name" ? 1 : -1; } draw(); } }, ...text, arrow(k)));
  const heat = (v) => {
    if (v == null || Math.abs(v) < 0.005) return h("td", { class: "heat muted" }, "·");
    const a = (Math.min(1, Math.abs(v) / o.maxSh) * 0.62).toFixed(3);
    return h("td", { class: "heat", style: { background: v < 0 ? `rgba(232,131,122,${a})` : `rgba(10,186,181,${a})` } }, f1(v, 1));
  };
  const used = CL.map((_, k) => k).filter((k) => o.usedCols[k]);
  const table = h("table", { class: "npf-table npf-heat" },
    h("thead", {}, h("tr", {}, sortTh("name", ["Фонд"]), sortTh("aum", ["АУМ, млрд ₽"], "num"),
      used.map((k) => sortTh(k, [dot(CL[k].c), " ", CL[k].s], "c", CL[k].n)), th("Дата"))),
    h("tbody", {}, rows.map((r) => h("tr", {},
      h("th", { scope: "row" }, fundLink(r.i, r.f.name), r.rv ? [" ", badge("сверка", "warn")] : null),
      h("td", { class: "num", title: r.aumD ? `Состав на ${dmy(r.aumD)}` : null }, r.aum ? `${r.est ? "≈ " : ""}${f1(r.aum / 1e9, 1)}` : h("span", { class: "muted" }, "—")),
      used.map((k) => (r.sh ? heat(r.sh[k]) : h("td", { class: "heat muted" }, ""))),
      h("td", { class: "sm" }, r.d ? dmy(r.d) : "нет на месяц")))),
    h("tfoot", {}, h("tr", {}, h("th", { scope: "row" }, `Взвешенно по АУМ · ${o.wn} фонд.`), h("td", { class: "num" }, f1(o.wa / 1e9, 1)), used.map((k) => heat(o.weighted[k])), h("td", { class: "sm" }, "по составу"))));
  const csv = () => downloadCsv(`npf-market-${o.month}.csv`, [["Фонд", "Месяц", "Вид средств", "Источник", "АУМ, руб.", "Дата отчёта", ...CL.map((c) => `${c.n}, %`)],
    ...rows.map((r) => [r.f.name, o.month, S.kind === "all" ? "Вместе" : N.KNL[S.kind], o.src === "struct" ? "Отчёт о структуре" : "Расчёт по составу", csvNum(r.aum, 2), r.d || "", ...CL.map((_, k) => (r.sh ? csvNum(r.sh[k] || 0, 4) : ""))])]);

  const ser = N.marketSeries(ix, { kind: S.kind, mode: s.mode });
  const stack = chartBox("Структура рынка по месяцам: доли классов активов в портфелях фондов с раскрытым составом", 300);
  stackChart(stack.canvas, { labels: ser.cols.map((c) => ml(c.month)), tips: ser.cols.map((c) => `${ml(c.month)} · ${c.n} фонд., ${f1(c.aum / 1e12, 2)} трлн ₽`), cats: CL, used: ser.used, vals: ser.cols.map((c) => c.v),
    pct: s.mode === "share", unit: "трлн ₽", sel: ser.cols.findIndex((c) => c.month === o.month), onPick: (i) => { const m = ser.cols[i]?.month; if (m && o.avail.some((x) => x.month === m)) pick(m); } });
  const mx = o.top[0]?.aum || 1;
  return [
    h("div", { class: "card npf-filters" }, monthPick("npf-m-month", o.avail, o.month, pick),
      h("p", { class: "hint" }, `Сводная картина по ${ix.F.length} фондам · ${kname} · ${ml(o.month)}`)),
    h("div", { class: "kpis" },
      kpi("Активы под наблюдением", `${f1(o.totAum / 1e12, 2)} трлн ₽`, `${fmtNum(o.totAum, 2)} ₽ · ${o.withAum} фондов с составом · последние срезы не позже ${ml(o.month)}`),
      kpi("Фондов со структурой", String(o.withSh), o.src === "struct" ? "по отчётам фондов о структуре" : "по расчёту из перечня бумаг"),
      kpi("ОФЗ и госбумаги · взвешенно", `${f1(o.weighted[0], 1)} %`, `простое среднее ${f1(o.simple[0], 1)} %`),
      kpi("Корпоративные облигации", `${f1(o.weighted[2], 1)} %`, `простое среднее ${f1(o.simple[2], 1)} %`),
      kpi("Акции", `${f1(o.weighted[6], 1)} %`, `простое среднее ${f1(o.simple[6], 1)} %`)),
    h("div", { class: "npf-grid32" },
      h("section", { class: "card npf-card" }, cardHead("Структура рынка во времени", "Сумма портфелей фондов с раскрытым составом. Число фондов меняется по месяцам. Нажмите на столбец, чтобы выбрать месяц.",
        seg("Показатель", "npf-mode", [["share", "Доли, %"], ["value", "Объём, трлн ₽"]], s.mode, (v) => { s.mode = v; draw(false, `npf-mode-${v}`); })),
      stack.box, legend(ser.used.map((k) => [CL[k].n, CL[k].c]))),
      h("section", { class: "card npf-card" }, cardHead("Крупнейшие фонды", "АУМ по последнему доступному составу, млрд ₽"),
        o.top.length ? o.top.map((r) => barRow(fundLink(r.i, r.f.name), `${r.est ? "≈ " : ""}${f1(r.aum / 1e9, 1)}`, N.barFrac(r.aum, mx))) : empty("Нет составов на выбранный месяц"))),
    h("section", { class: "card npf-card" }, cardHead("Структура портфелей по фондам", "Доли классов активов, %. Нажмите на заголовок для сортировки, на название — чтобы открыть фонд.", csvBtn("npf-m-csv", csv)),
      tableWrap(table, { maxHeight: "640px" }),
      noteP(`${o.src === "struct" ? "Источник долей — официальные отчёты фондов о структуре портфеля, приведённые к единым 16 классам. База расчёта у фондов различается: у части в структуру входят обязательства отрицательной строкой." : "Доли рассчитаны из перечня бумаг: стоимость позиций класса / АУМ (активы до вычета обязательств)."} Точка означает нулевую долю, пустая ячейка — отсутствие отчёта за месяц. Взвешенная строка построена только по фондам с раскрытым составом.`)),
  ];
}

// ================================================================ ФОНД
function sFund() {
  const s = S.fund, F = ix.F, CL = ix.CL;
  const v = N.fundView(ix, { fi: s.fi, kind: S.kind, src: s.src, grp: s.grp, idx: s.idx });
  S.kind = v.kind; s.src = v.src; s.grp = v.grp; s.idx = v.idx; s.date = v.date;
  const f = v.fund, fi = v.fi;
  const cov = (c) => `${N.KN[c.kind]}: ${dmy(c.from)}–${dmy(c.to)} · ${c.count}`;
  const header = h("section", { class: "card npf-card" },
    h("div", { class: "npf-cardhead" }, h("div", {}, h("p", { class: "hint" }, "Фонд"), h("h3", { class: "card-title npf-fundname" }, f.name)),
      h("div", { class: "toolbar" }, field("Выбрать фонд", "npf-f-sel", select("npf-f-sel", F.map((x, i) => [i, `${x.name}${x.comp ? "" : " · только структура"}`]), fi, (val) => openFund(+val, {}, "npf-f-sel"))),
        /^https?:\/\//i.test(f.url) ? h("a", { class: "btn", href: f.url, target: "_blank", rel: "noopener noreferrer" }, "Сайт фонда ↗") : null)),
    h("div", { class: "npf-badges" },
      f.comp ? badge(`Состав · ${v.coverage.comp.map(cov).join(" · ")}`, "ok") : badge("Состав не публикуется", "warn"),
      f.mid ? badge(`Структура · ${v.coverage.struct.map(cov).join(" · ")}`) : badge("Отчётов о структуре нет"), f.arch ? badge("архивный фонд") : null),
    f.note ? h("p", { class: "hint npf-fnote" }, f.note) : null);
  const grp = h("div", { class: "card npf-filters" }, segField("Группировка", seg("Группировка", "npf-grp", [["class", "Классы активов"], ["sector", "Секторы", !v.isComp]], s.grp, (g) => { s.grp = g; s.cat = ""; draw(false, `npf-grp-${g}`); })));
  if (!v.date) return [header, grp, h("div", { class: "card" }, empty("Для выбранного вида средств данных нет. Переключите вид средств или источник."))];

  const choose = (i) => { s.idx = Math.max(0, Math.min(v.dates.length - 1, i)); draw(); };
  const n = v.dates.length;
  const play = () => {
    if (timer) { stopTimer(); draw(false, "npf-t-play"); return; }
    if (s.idx >= n - 1) s.idx = 0;
    timer = setInterval(() => { if (s.idx >= n - 1) { stopTimer(); draw(false, "npf-t-play"); return; } s.idx++; draw(true, "npf-t-play"); }, 800);
    draw(true, "npf-t-play");
  };
  const timeline = h("section", { class: "card npf-timeline" },
    h("div", {}, h("div", { class: "hint" }, "Срез на дату"), h("div", { class: "npf-date", id: "npf-date", role: "status" }, dmy(v.date))),
    h("div", { class: "toolbar" },
      h("button", { type: "button", class: "btn npf-arrow", id: "npf-t-prev", "aria-label": "Предыдущая дата", disabled: s.idx === 0, onclick: () => choose(s.idx - 1) }, "←"),
      h("button", { type: "button", class: "btn", id: "npf-t-play", onclick: play }, timer ? "❚❚ Пауза" : "▶ Воспроизвести"),
      h("button", { type: "button", class: "btn npf-arrow", id: "npf-t-next", "aria-label": "Следующая дата", disabled: s.idx === n - 1, onclick: () => choose(s.idx + 1) }, "→")),
    h("input", { type: "range", id: "npf-t-range", min: "0", max: String(n - 1), value: String(s.idx), "aria-label": "Отчётная дата (таймлайн)", "aria-valuetext": dmy(v.date), oninput: (e) => choose(+e.target.value) }),
    select("npf-t-sel", [...v.dates].reverse().map((x) => [x, dmy(x)]), v.date, (x) => choose(v.dates.indexOf(x)), "Дата среза"),
    h("span", { class: "hint" }, `${n} дат`));

  let kp;
  const k = v.kpi;
  if (v.isComp && v.cur) {
    kp = [
      kpi("АУМ · активы до обязательств", k.aum ? `${k.est ? "≈ " : ""}${f1(k.aum / 1e9, 2)} млрд ₽` : "—", k.aum ? `${f1(k.aum / 1e6, 1)} млн ₽${k.est ? " · оценка" : ""}` : "Полная стоимость не установлена"),
      kpi("СЧА · чистые активы", k.nav != null ? `${f1(k.nav / 1e9, 2)} млрд ₽` : "Нет данных", k.navLabel),
      kpi("К предыдущей дате", h("span", { class: upDown(k.deltaPct) }, k.deltaPct == null ? "—" : `${sg(k.deltaPct)} %`), k.deltaPct != null ? `${sg(k.deltaAbs / 1e6, 1)} млн ₽ · не доходность` : k.hasPrev ? "Нет базы АУМ" : "Первый срез"),
      kpi("Доля ОФЗ и госбумаг", k.ofzShare != null ? `${f1(k.ofzShare, 1)} %` : "—", k.agg ? "Не раскрыта отдельно" : bn(k.ofzValue)),
      kpi(k.agg ? "Топ-5 групп" : "Топ-5 позиций", k.top5Share != null ? `${f1(k.top5Share, 1)} %` : "—", `${k.nPositions} позиций · ${k.nRows} строк`)];
  } else if (v.sr) {
    kp = [kpi("Крупнейший класс", `${f1(k.biggestShare, 1)} %`, CL[k.biggestClass]?.n || ""), kpi("ОФЗ и госбумаги", `${f1(k.ofz, 1)} %`, "по отчёту фонда"),
      kpi("Корпоративные облигации", `${f1(k.corp, 1)} %`, "по отчёту фонда"), kpi("Акции", `${f1(k.stocks, 1)} %`, "по отчёту фонда"),
      kpi("Сумма долей в отчёте", `${f1(k.sum, 2)} %`, [k.rv ? badge("требует сверки", "warn") : badge("извлечено автоматически", "ok"), k.lastAum ? ` · АУМ ${f1(k.lastAum / 1e9, 1)} млрд ₽ на ${dmy(k.lastAumDate)}` : ""])];
  }
  const tabs = [["overview", "Обзор"], ["holdings", "Состав портфеля", !v.tabs.holdings], ["issuers", "Эмитенты", !v.tabs.issuers], ["compare", "Сравнение дат"], ["recon", "Сверка со структурой", !v.tabs.recon], ["sources", "Источники"]];
  if (tabs.find((t) => t[0] === s.tab)?.[2]) s.tab = "overview";
  const body = { overview: fOverview, holdings: fHoldings, issuers: fIssuers, compare: fCompare, recon: fRecon, sources: fSources }[s.tab](v, choose);
  return [header, grp, timeline, h("div", { class: "kpis" }, kp),
    v.notes.length ? h("details", { class: "card npf-notes" }, h("summary", {}, `Примечания к данным на ${dmy(v.date)} · ${v.notes.length}`), h("ul", {}, v.notes.map((t) => h("li", {}, t)))) : null,
    tabBar(tabs, s.tab, (t) => { s.tab = t; draw(false, `npf-ftab-${t}`); }, "Разделы фонда", "npf-ftab", "npf-fpane"),
    h("div", { id: "npf-fpane", role: "tabpanel", "aria-labelledby": `npf-ftab-${s.tab}`, class: "npf-fpane" }, body)];
}

function fOverview(v, choose) {
  const s = S.fund, CA = v.grp === "sector" ? ix.SEC : ix.CL;
  const mix = N.mixList(ix, v), ch = N.fundChanges(ix, v);
  const st = N.fundStructureSeries(ix, v, { stack: s.stack });
  const pct = !v.isComp || s.stack === "share";
  const stk = chartBox("Как менялась структура портфеля фонда по отчётным датам", 290);
  stackChart(stk.canvas, { labels: st.cols.map((c) => dmy(c.date)), cats: CA, used: st.used, vals: st.cols.map((c) => c.v), pct, unit: "млрд ₽", sel: v.idx, onPick: (i) => choose(i) });
  const parts = mix.filter((x) => x.share > 0).map((x) => ({ name: x.cat.n, c: x.cat.c, v: x.share }));
  const don = chartBox("Структура портфеля на выбранную дату", 200);
  if (parts.length) donutChart(don.canvas, parts);
  const out = [h("div", { class: "npf-grid32" },
    h("section", { class: "card npf-card" }, cardHead("Как менялась структура", "Каждый столбец — отчётная дата. Нажмите, чтобы выбрать.",
      v.isComp ? seg("Показатель", "npf-stack", [["share", "Доли, %"], ["value", "Объём, млрд ₽"]], s.stack, (x) => { s.stack = x; draw(false, `npf-stack-${x}`); }) : null), stk.box, legend(st.used.map((k) => [CA[k].n, CA[k].c]))),
    h("section", { class: "card npf-card" }, cardHead(`Структура на ${dmy(v.date)}`, v.isComp ? "% от АУМ · расчёт по составу" : "% · по отчёту фонда"),
      v.sh ? h("div", { class: "npf-donutrow" }, don.box, h("ul", { class: "npf-mix" }, mix.map((x) => h("li", {}, h("span", {}, dot(x.cat.c), x.cat.n), h("b", {}, `${f1(x.share, 2)} %`))))) : empty("Полный АУМ не установлен — доли не рассчитываются.")))];
  const second = [];
  if (v.isComp) {
    const vol = N.fundVolumeSeries(ix, v, { metric: s.metric });
    const vb = chartBox("Стоимость портфеля фонда во времени", 300);
    lineChart(vb.canvas, { labels: vol.xs.map(dmy), series: vol.series.map((x) => ({ name: x.name, color: seriesColor(x.k), v: x.v })), tick: (y) => f1(y, vol.max < 20 ? 1 : 0), tip: (y) => `${f1(y, 2)} ${vol.unit}`, yTitle: vol.unit,
      sel: vol.xs.indexOf(v.date), onPick: (i) => { const j = v.dates.indexOf(vol.xs[i]); if (j >= 0) choose(j); } });
    second.push(h("section", { class: "card npf-card" }, cardHead("Стоимость портфеля во времени", `${s.metric === "nav" ? "СЧА — только подтверждённые значения" : "АУМ — активы до вычета обязательств"} · ${vol.unit}`,
      seg("Показатель", "npf-metric", [["aum", "АУМ"], ["nav", "СЧА"]], s.metric, (x) => { s.metric = x; draw(false, `npf-metric-${x}`); })), vb.box, legend(vol.series.map((x) => [x.name, seriesColor(x.k)]))));
  }
  second.push(h("section", { class: "card npf-card" }, cardHead("Изменения с предыдущего среза", s.idx ? `${dmy(v.dates[s.idx - 1])} → ${dmy(v.date)}` : "Первый доступный срез"),
    ch.length ? ch.map((x) => h("div", { class: "npf-barrow" }, h("div", { class: "npf-bartxt" }, h("span", {}, dot(x.cat.c), " ", x.cat.n), h("b", { class: `num ${upDown(x.delta)}` }, `${sg(x.delta)} п. п.`)))) : empty("Нет изменений для показа"),
    noteP("Изменение долей и стоимости не равно инвестиционной доходности: оно включает переоценку, взносы, выплаты и переводы.")));
  if (v.isComp && v.pos) {
    second.push(h("section", { class: "card npf-card" }, cardHead(v.cur.agg ? "Крупнейшие группы" : "Крупнейшие позиции", "Одинаковые бумаги объединены по ISIN",
      h("button", { type: "button", class: "btn btn-sm", id: "npf-o-all", onclick: () => { s.tab = "holdings"; draw(false, "npf-ftab-holdings"); } }, "Весь состав →")),
      v.pos.slice(0, 8).map((r) => barRow(h("span", { title: r.issuer }, r.name), shareTxt(r.value, v.cur.aum, 2, bn(r.value)), N.barFrac(r.value, v.pos[0].value), ix.CL[r.cls].c))));
  }
  return [...out, h("div", { class: "npf-grid2" }, second)];
}

function fHoldings(v) {
  const s = S.fund, CA = v.grp === "sector" ? ix.SEC : ix.CL;
  if (!v.cur) {
    return card("Состав портфеля", "Состав портфеля доступен при источнике «Состав портфеля».",
      h("button", { type: "button", class: "btn", id: "npf-h-switch", onclick: () => { const m = v.date.slice(0, 7); s.src = "comp"; s.idx = N.fundDates(ix, v.fi, S.kind, "comp").findIndex((x) => x.slice(0, 7) === m); draw(false, "npf-src-comp"); } }, "Переключить на состав"));
  }
  const gk = (r) => (v.grp === "sector" ? r.sec : r.cls);
  const tbody = h("tbody"), count = h("p", { class: "card-sub", role: "status" }), more = h("div", { class: "npf-more" });
  let list = [];
  const multi = v.cur.parts.length > 1;
  const drawRows = () => {
    const r = N.fundHoldings(ix, v, { q: s.q, cat: s.cat, sort: s.sort });
    list = r.list;
    count.textContent = `${list.length} из ${r.total} строк · ${bn(r.sum)}`;
    const lim = s.all ? list.length : 300;
    put(tbody, list.slice(0, lim).map((x) => h("tr", {},
      h("th", { scope: "row", class: "name" }, h("b", {}, x.name), h("br"), h("small", {}, x.issuer)),
      h("td", { class: "mono" }, x.isin ? link("#/npf/securities", () => { S.sec.q = x.isin; S.sec.month = v.date.slice(0, 7); nav("securities"); }, { title: "Кто ещё держит" }, x.isin) : "—"),
      h("td", {}, dot(ix.CL[x.cls].c), " ", ix.CL[x.cls].s), h("td", { class: "sm" }, ix.SEC[x.sec].n),
      h("td", { class: "num" }, typeof x.qty === "number" ? fmtNum(x.qty, Number.isInteger(x.qty) ? 0 : 4) : "—"), multi ? h("td", {}, N.KN[x.k]) : null,
      h("td", { class: "num" }, f1(x.value / 1e6, 3)), h("td", { class: "num" }, shareTxt(x.value, v.cur.aum, 3)),
      h("td", { class: "sm npf-src" }, x.src, x.page ? [h("br"), x.page] : null))));
    if (!list.length) put(tbody, h("tr", {}, h("td", { colspan: "9", class: "npf-empty" }, "Ничего не найдено")));
    put(more, list.length > lim ? h("button", { type: "button", class: "btn", id: "npf-h-all", onclick: () => { s.all = true; drawRows(); } }, `Показать все ${list.length} строк`) : null);
  };
  const csv = () => downloadCsv(`${v.fund.id}-${v.date}.csv`, [["Дата", "Фонд", "Средства", "Актив", "ISIN / код", "Эмитент", "Класс", "Сектор", "Количество", "Стоимость, руб.", "Доля от АУМ, %", "Источник", "Страница / строка"],
    ...list.map((x) => [dmy(v.date), v.fund.name, N.KN[x.k], x.name, x.isin, x.issuer, ix.CL[x.cls].n, ix.SEC[x.sec].n, csvNum(x.qty, 6), csvNum(x.value, 2), csvNum(N.rowShare(x.value, v.cur.aum), 6), x.src, x.page])]);
  const q = h("input", { type: "search", id: "npf-h-q", value: s.q, placeholder: "Название, ISIN или эмитент", autocomplete: "off", oninput: (e) => { s.q = e.target.value; drawRows(); } });
  const present = new Set(v.rows.map(gk));
  const cat = select("npf-h-cat", [["", "Все группы"], ...CA.map((c, i) => [i, c.n]).filter(([i]) => present.has(i))], s.cat, (x) => { s.cat = x; drawRows(); }, "Группа");
  const srt = select("npf-h-sort", [["desc", "По стоимости ↓"], ["asc", "По стоимости ↑"], ["name", "По названию"]], s.sort, (x) => { s.sort = x; drawRows(); }, "Сортировка");
  drawRows();
  return h("section", { class: "card npf-card" }, cardHead(`Подробный состав на ${dmy(v.date)}`, null, csvBtn("npf-h-csv", csv)), count,
    h("div", { class: "npf-filtrow" }, field("Поиск", "npf-h-q", q), field("Группа", "npf-h-cat", cat), field("Сортировка", "npf-h-sort", srt)),
    tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Актив / эмитент"), th("ISIN / код"), th("Класс"), th("Сектор"), th("Количество", "num"), multi ? th("Средства") : null,
      th("Стоимость, млн ₽", "num"), th("Доля от АУМ", "num"), th("Источник"))), tbody), { maxHeight: "720px" }), more);
}

function fIssuers(v) {
  if (!v.cur) return card("Эмитенты", null, empty("Выберите источник «Состав портфеля»."));
  const r = N.fundIssuers(ix, v), mx = r.list[0]?.value || 1, month = v.date.slice(0, 7);
  return card(`Концентрация по эмитентам и контрагентам на ${dmy(v.date)}`, `${r.list.length} групп · топ-10 занимают ${(N.rowShare(r.top10, v.cur.aum) == null ? bn(r.top10) : `${shareTxt(r.top10, v.cur.aum, 1)} АУМ`)}. Группировка по ОГРН и ISIN — аналитическая.`,
    tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("№"), th("Эмитент / контрагент"), th("Инструменты"), th("Строк", "num"), th("Стоимость, млн ₽", "num"), th("Доля от АУМ", "num"), th("Доля среди эмитентов"))),
      h("tbody", {}, r.list.slice(0, 200).map((x, i) => h("tr", {}, h("td", { class: "muted" }, i + 1),
        h("th", { scope: "row", class: "name" }, link(`#/npf/securities/${encodeURIComponent(`g:${x.g}`)}`, () => { S.sec.month = month; nav("securities", `g:${x.g}`); }, {}, x.name)),
        h("td", { class: "sm" }, Object.entries(x.cls).sort((a, b) => b[1] - a[1]).map(([c]) => h("span", { class: "npf-inl" }, dot(ix.CL[c].c), ix.CL[c].s))),
        h("td", { class: "num" }, x.n), h("td", { class: "num" }, f1(x.value / 1e6, 1)), h("td", { class: "num" }, shareTxt(x.value, v.cur.aum, 2)),
        h("td", {}, h("div", { class: "npf-bar-track", "aria-hidden": "true" }, h("i", { style: { width: barW(N.barFrac(x.value, mx)) } }))))))), { maxHeight: "720px" }),
    noteP("Нажмите на эмитента, чтобы увидеть, какие ещё фонды держат его бумаги."));
}

function fCompare(v) {
  const s = S.fund, CA = v.grp === "sector" ? ix.SEC : ix.CL;
  const c = N.fundCompareDates(ix, v, { from: s.from, to: s.to });
  s.from = c.from; s.to = c.to;
  const opt = () => v.dates.map((x) => [x, dmy(x)]);
  const ca = c.ca, cb = c.cb;
  const dl = N.compareDelta(ca?.aum, cb?.aum);
  const diffNote = ca?.aum && cb?.aum && dl
    ? `АУМ: ${ca.est ? "≈ " : ""}${f1(ca.aum / 1e9, 2)} → ${cb.est ? "≈ " : ""}${f1(cb.aum / 1e9, 2)} млрд ₽, изменение ${sg(dl.abs / 1e9)} млрд ₽ (${sg(dl.pct, 1)} %). СЧА: ${ca.nav != null ? f1(ca.nav / 1e9, 2) : "нет данных"} → ${cb.nav != null ? f1(cb.nav / 1e9, 2) : "нет данных"} млрд ₽. Это изменение объёма, а не доходность.` : null;
  return card("Сравнение двух дат", v.isComp ? "Доли от АУМ, расчёт по составу" : "Доли по отчётам фонда о структуре",
    h("div", { class: "npf-filtrow" }, field("От", "npf-c-from", select("npf-c-from", opt(), c.from, (x) => { s.from = x; draw(false, "npf-c-from"); })), field("До", "npf-c-to", select("npf-c-to", opt(), c.to, (x) => { s.to = x; draw(false, "npf-c-to"); }))),
    diffNote ? noteP(diffNote) : null,
    c.rows.length ? tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th(v.grp === "sector" ? "Сектор" : "Класс активов"),
      v.isComp ? [th("Начало, млн ₽", "num"), th("Конец, млн ₽", "num"), th("Изменение, млн ₽", "num")] : null, th("Доля в начале", "num"), th("Доля в конце", "num"), th("Изменение, п. п.", "num"), th("Сдвиг"))),
      h("tbody", {}, c.rows.map((r) => { const d = N.diffOf(r.a, r.b), dv = N.diffOf(r.valA, r.valB), w = (N.barFrac(d, c.maxDelta) ?? 0) * 50;
        return h("tr", {}, h("th", { scope: "row" }, dot(r.cat.c), " ", r.cat.n),
          v.isComp ? [h("td", { class: "num" }, f1(r.valA / 1e6, 1)), h("td", { class: "num" }, f1(r.valB / 1e6, 1)), h("td", { class: "num" }, sg(dv == null ? null : dv / 1e6, 1))] : null,
          h("td", { class: "num" }, `${f1(r.a, 2)} %`), h("td", { class: "num" }, `${f1(r.b, 2)} %`), h("td", { class: `num ${upDown(d)}` }, sg(d)),
          h("td", {}, h("div", { class: "npf-diverge", "aria-hidden": "true" }, h("i", { class: d >= 0 ? "up" : "down", style: { left: `${d >= 0 ? 50 : 50 - w}%`, width: `${w}%` } }))));
      })))) : empty("Нет полного АУМ на одну или обе даты — сравнение долей недоступно."));
}

function fRecon(v) {
  const month = v.date.slice(0, 7), CL = ix.CL;
  const r = N.fundRecon(ix, { fi: v.fi, kind: S.kind, month });
  if (!r.supported) return card("Сверка со структурой", null, empty("Сверка выполняется отдельно по резервам или накоплениям: отчёты о структуре не содержат сумм и не складываются. Выберите вид средств сверху."));
  let body;
  if (!r.comp || !r.struct) body = empty(`На ${ml(month)} нет пары «состав + отчёт о структуре» для вида «${N.KNL[S.kind]}». ${r.comp ? "Есть только состав." : r.struct ? "Есть только отчёт о структуре." : ""}`);
  else {
    body = [tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Класс активов"), th([`По составу, % АУМ`, h("br"), h("span", { class: "sm" }, dmy(r.comp.d))], "num"),
      th(["По отчёту о структуре, %", h("br"), h("span", { class: "sm" }, dmy(r.struct.d))], "num"), th("Расхождение, п. п.", "num"))),
      h("tbody", {}, r.rows.map((x) => { const warn = N.reconFlag(x.diff)?.flag; return h("tr", {}, h("th", { scope: "row" }, dot(x.cat.c), " ", x.cat.n), h("td", { class: "num" }, x.a == null ? "—" : f1(x.a, 2)), h("td", { class: "num" }, f1(x.b, 2)),
        h("td", { class: `num ${warn ? "npf-warn" : ""}` }, sg(x.diff), warn ? " ▲" : "")); })))),
    h("details", { class: "npf-det" }, h("summary", {}, "Исходные строки отчёта о структуре"),
      tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Строка в отчёте фонда"), th("Доля, %", "num"), th("Единый класс"))),
        h("tbody", {}, r.srRows.map((x) => h("tr", {}, h("th", { scope: "row" }, x.label), h("td", { class: "num" }, f1(x.share, 2)),
          h("td", {}, x.cls >= 0 ? [dot(CL[x.cls].c), " ", CL[x.cls].n] : h("span", { class: "muted" }, "не учитывается (подстрока или итог)"))))))))];
  }
  const out = [card(`Сверка: состав против отчёта о структуре · ${ml(month)}`, `Два независимых раскрытия одного портфеля, приведённые к единым классам · ${N.KNL[S.kind]}`, body,
    noteP("Расхождения ожидаемы: фонды считают структуру от разной базы (с обязательствами или без), по-разному относят РЕПО, дебиторскую задолженность и деньги у брокера. Расхождение от 2 п. п. выделено значком ▲ и жирным шрифтом."))];
  if (r.history) {
    const lb = chartBox("Максимальное расхождение по классам во времени", 220);
    lineChart(lb.canvas, { labels: r.history.xs.map(ml), series: [{ name: "Макс. расхождение", color: seriesColor(0), v: r.history.v }], tick: (y) => f1(y, 1), tip: (y) => `${f1(y, 2)} п. п.`, yTitle: "п. п.", sel: r.history.xs.indexOf(month) });
    out.push(card("Максимальное расхождение по классам во времени", "п. п., без обязательств и нераскрытого остатка", lb.box));
  }
  return out;
}

function fSources(v) {
  const r = N.fundSources(ix, v.fi), sr = v.sr;
  const stn = { matched: "✓ Совпадает", rounded: "В пределах округления", no_total: "Нет итога в источнике", recomputed: "Пересчитан по строкам" };
  const comps = r.comps.length ? card(`Составы портфеля · ${r.comps.length} срезов`, `${r.stats.matched} денежных итогов сошлись · ${r.stats.rounded} в пределах округления · ${r.stats.recomputed} пересчитаны · ${r.stats.noTotal} без итога в источнике · ${fmtNum(r.stats.rows, 0)} строк`,
    tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Дата"), th("Средства"), th("Файл"), th("Опубликовано"), th("Строк", "num"), th("АУМ, млн ₽", "num"), th("СЧА, млн ₽", "num"), th("Обязательства, млн ₽", "num"), th("Итог источника, млн ₽", "num"), th("Сверка итога"))),
      h("tbody", {}, r.comps.map((c) => h("tr", {}, h("td", {}, dmy(c.d)), h("td", {}, N.KN[c.k]), h("td", { class: "sm npf-src" }, c.srcName), h("td", { class: "sm" }, c.pub || "—"), h("td", { class: "num" }, c.rowsN),
        h("td", { class: "num" }, c.nf ? "—" : `${c.e ? "≈ " : ""}${f1(c.t / 1e6, 1)}`), h("td", { class: "num" }, c.nav != null ? f1(c.nav / 1e6, 1) : "—"), h("td", { class: "num" }, c.li != null ? f1(c.li / 1e6, 3) : "—"),
        h("td", { class: "num" }, c.rep != null ? f1(c.rep / 1e6, 1) : "—"), h("td", {}, badge(stn[c.st] || c.st, c.st === "matched" ? "ok" : c.st === "recomputed" ? "warn" : "")))))), { maxHeight: "520px" })) : null;
  const srcRows = (x) => tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Строка в отчёте фонда"), th("Доля, %", "num"), th("Единый класс"))),
    h("tbody", {}, N.structSourceRows(ix, x).map((y) => h("tr", {}, h("th", { scope: "row" }, y.label), h("td", { class: "num" }, f1(y.share, 2)), h("td", {}, y.cls >= 0 ? [dot(ix.CL[y.cls].c), " ", ix.CL[y.cls].n] : h("span", { class: "muted" }, "не учитывается (подстрока или итог)")))))));
  const structs = r.structs.length ? card(`Отчёты о структуре · ${r.structs.length}`, `${r.stats.structAuto} извлечены автоматически без замечаний · ${r.stats.structReview} требуют сверки с оригиналом`,
    sr ? h("details", { class: "npf-det", open: true }, h("summary", {}, `Исходные строки отчёта на ${dmy(v.date)}`), srcRows(sr)) : null,
    tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Дата"), th("Средства"), th("Источник"), th("Сумма долей, %", "num"), th("Статус"), th("Замечания"))),
      h("tbody", {}, r.structs.map((x) => h("tr", {}, h("td", {}, dmy(x.d)), h("td", {}, N.KN[x.k]),
        h("td", { class: "sm npf-src" }, /^https?:/.test(x.srcName) ? h("a", { href: x.srcName, target: "_blank", rel: "noopener" }, (() => { try { return decodeURI(x.srcName).split("/").pop() || x.srcName; } catch { return x.srcName; } })()) : x.srcName),
        h("td", { class: "num" }, f1(x.sum, 2)), h("td", {}, badge(x.rv ? "требует сверки" : "авто", x.rv ? "warn" : "ok")), h("td", { class: "sm" }, x.issueTexts.join("; ")))))), { maxHeight: "520px" })) : null;
  return [comps, structs, !comps && !structs ? card("Источники", null, empty("Для фонда нет данных.")) : null];
}

// ================================================================ СРАВНЕНИЕ ФОНДОВ
function sCompare() {
  const s = S.cmp, CL = ix.CL;
  const r = N.compareFunds(ix, { funds: s.funds, kind: S.kind, metric: s.metric, src: s.src, month: s.month });
  s.funds = r.funds; s.month = r.month;
  if (s.metric !== "aum") s.src = r.src;
  const isAum = r.metric === "aum", ci = +r.metric;
  const toggle = (i) => { const j = s.funds.indexOf(i); if (j >= 0) s.funds.splice(j, 1); else if (s.funds.length < 8) s.funds.push(i); draw(false, `npf-chip-${i}`); };
  const chips = h("div", { class: "npf-chips", role: "group", "aria-label": "Фонды для сравнения" }, ix.F.map((f, i) => {
    const j = s.funds.indexOf(i);
    return h("button", { type: "button", class: "npf-chip", id: `npf-chip-${i}`, "aria-pressed": String(j >= 0), onclick: () => toggle(i) }, j >= 0 ? dot(seriesColor(j)) : null, N.shortName(f.name));
  }));
  const lb = chartBox(isAum ? "АУМ фондов по месяцам, млрд ₽" : `Доля класса «${CL[ci]?.n}» в портфелях фондов по месяцам`, 330);
  const maxV = Math.max(0, ...r.series.flatMap((x) => x.v.filter((y) => y != null)));
  lineChart(lb.canvas, { labels: r.xs.map(ml), series: r.series.map((x, j) => ({ name: x.name, color: seriesColor(j), v: x.v })), tick: (y) => f1(y, isAum ? (maxV < 20 ? 1 : 0) : 0), tip: (y) => (isAum ? `${f1(y, 2)} млрд ₽` : `${f1(y, 2)} %`),
    yTitle: isAum ? "АУМ, млрд ₽" : "Доля, %", sel: r.xs.indexOf(r.month), onPick: (i) => { s.month = r.xs[i]; draw(); } });
  const short = (fi) => N.shortName(ix.F[fi].name);
  const strips = r.bars.map((b, j) => h("div", { class: "npf-strip" }, h("div", { class: "npf-stripname" }, dot(seriesColor(j)), fundLink(b.fi, ix.F[b.fi].name)),
    b.sh ? h("div", { class: "npf-stripbar", role: "img", "aria-label": `${short(b.fi)}: ${Object.entries(b.sh).filter(([, x]) => x > 0).sort((a, c) => c[1] - a[1]).slice(0, 4).map(([k, x]) => `${CL[k].s} ${f1(x, 1)} %`).join(", ")}` },
      CL.map((c, k) => ((b.sh[k] || 0) > 0 ? h("i", { style: { flex: `${b.sh[k]} 0 0`, background: c.c }, title: `${ix.F[b.fi].name} · ${dmy(b.dt)} · ${c.n}: ${f1(b.sh[k], 2)} %` }) : null)))
      : h("span", { class: "hint" }, "нет данных на этот месяц")));
  const table = tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Класс активов"), r.bars.map((b) => th(short(b.fi), "num")))),
    h("tbody", {}, r.used.map((k) => h("tr", {}, h("th", { scope: "row" }, dot(CL[k].c), " ", CL[k].n), r.bars.map((b) => h("td", { class: "num" }, b.sh ? f1(b.sh[k] || 0, 1) : "—")))))));
  return [
    h("div", { class: "card npf-filters" }, field("Показатель", "npf-x-metric", select("npf-x-metric", [["aum", "АУМ, млрд ₽"], ...CL.map((c, k) => [String(k), `Доля: ${c.n}`]).filter(([k]) => +k !== N.RES)], s.metric, (x) => { s.metric = x; if (x === "aum") s.src = "comp"; draw(false, "npf-x-metric"); })),
      h("p", { class: "hint" }, `До 8 фондов на одном графике · ${S.kind === "all" ? "резервы и накопления вместе" : N.KNL[S.kind].toLowerCase()}`)),
    h("section", { class: "card npf-card" }, cardHead("Фонды", `Выбрано ${s.funds.length} из 8. Цвет закреплён за позицией в списке выбора.`,
      h("button", { type: "button", class: "btn btn-sm", id: "npf-x-clear", onclick: () => { s.funds = []; draw(false, "npf-x-clear"); } }, "Сбросить")), chips),
    h("section", { class: "card npf-card" }, cardHead(isAum ? "АУМ, млрд ₽" : `Доля класса «${CL[ci].n}», %`, `${isAum || r.src === "comp" ? "Расчёт по составу портфеля" : "По отчётам фондов о структуре"} · разрыв линии означает отсутствие отчёта. Нажмите на график, чтобы выбрать месяц.`),
      r.xs.length ? [lb.box, legend(r.series.map((x, j) => [x.name, seriesColor(j)]))] : empty("Нет данных для выбранных фондов.")),
    h("section", { class: "card npf-card" }, cardHead(`Структура портфелей на ${r.month ? ml(r.month) : "—"}`, "Каждая полоса — 100 % портфеля фонда",
      r.xs.length ? select("npf-x-month", [...r.xs].reverse().map((m) => [m, ml(m)]), r.month, (x) => { s.month = x; draw(false, "npf-x-month"); }, "Месяц") : null),
      strips, legend(r.used.map((k) => [CL[k].n, CL[k].c])), table),
  ];
}

// ================================================================ БУМАГИ И ЭМИТЕНТЫ
const parseSel = (id) => (id.startsWith("g:") ? { t: "g", id: +id.slice(2) } : { t: "i", id });

function sSecurities() {
  const s = S.sec, CL = ix.CL, db = ix.db;
  const o = N.securitiesOverview(ix, { kind: S.kind, month: s.month, q: S.routeId ? "" : s.q });
  if (!o.avail.length) return notice("info", "Для выбранного вида средств нет составов.");
  s.month = o.month;
  const pickMonth = (m) => { s.month = m; draw(false, "npf-s-month"); };
  let qTimer;
  const qIn = h("input", { type: "search", id: "npf-s-q", value: s.q, placeholder: "ISIN, название бумаги или эмитент", autocomplete: "off",
    oninput: (e) => { clearTimeout(qTimer); const val = e.target.value; qTimer = setTimeout(() => { s.q = val; if (S.routeId) nav("securities"); else draw(); }, 250); } });
  const bar = h("div", { class: "card npf-filters" }, field("Поиск по бумагам и эмитентам", "npf-s-q", qIn), monthPick("npf-s-month", o.avail, o.month, pickMonth, "Месяц", false),
    h("p", { class: "hint" }, `Сквозной поиск по составам всех фондов · ${o.nFunds} фондов на ${ml(o.month)} · ${f1(o.total / 1e12, 2)} трлн ₽`));
  if (S.routeId) return [bar, ...secDetail(o)];
  const mg = o.groups[0]?.value || 1, mi = o.instruments[0]?.value || 1, q = s.q.trim();
  const gRows = o.groups.slice(0, 60).map((g) => h("tr", {}, h("th", { scope: "row", class: "name" }, link(`#/npf/securities/${encodeURIComponent(`g:${g.id}`)}`, () => nav("securities", `g:${g.id}`), {}, g.name),
    h("div", { class: "npf-bar-track", "aria-hidden": "true" }, h("i", { style: { width: barW(N.barFrac(g.value, mg)) } }))), h("td", { class: "num" }, g.nFunds), h("td", { class: "num" }, f1(g.value / 1e9, 2)), h("td", { class: "num" }, shareTxt(g.value, o.total, 2))));
  const iRows = o.instruments.slice(0, 60).map((x) => h("tr", {}, h("th", { scope: "row", class: "name" }, link(`#/npf/securities/${encodeURIComponent(x.key)}`, () => nav("securities", x.key), {}, x.name), h("br"),
    h("small", {}, dot(CL[x.cls].c), ` ${CL[x.cls].s} · ${db.groups[x.g] || ""}`), h("div", { class: "npf-bar-track", "aria-hidden": "true" }, h("i", { style: { width: barW(N.barFrac(x.value, mi)), background: CL[x.cls].c } }))),
    h("td", { class: "mono" }, x.isin || "—"), h("td", { class: "num" }, x.nFunds), h("td", { class: "num" }, f1(x.value / 1e9, 2))));
  return [bar,
    o.historical.length ? card(`На ${ml(o.month)} в портфелях не найдено, но встречалось раньше`, null, h("div", { class: "npf-chips" }, o.historical.map((x) => link(`#/npf/securities/${encodeURIComponent(x.key)}`, () => nav("securities", x.key), { class: "npf-chip" }, db.names[x.it[0]], db.isins[x.it[1]] ? ` · ${db.isins[x.it[1]]}` : "")))) : null,
    q && !o.groups.length && !o.instruments.length && !o.historical.length ? h("div", { class: "card" }, empty(`По запросу «${q}» ничего не найдено ни в одном срезе.`)) : null,
    h("div", { class: "npf-grid2" },
      card(q ? "Эмитенты и контрагенты по запросу" : "Крупнейшие эмитенты и контрагенты", `${o.groups.length} найдено · доля от суммарного АУМ наблюдаемых фондов${o.groups.length > 60 ? " · показаны первые 60" : ""}`,
        tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Эмитент / контрагент"), th("Фондов", "num"), th("млрд ₽", "num"), th("Доля", "num"))), h("tbody", {}, gRows.length ? gRows : h("tr", {}, h("td", { colspan: "4", class: "npf-empty" }, "Нет совпадений")))), { maxHeight: "640px" })),
      card(q ? "Бумаги по запросу" : "Крупнейшие бумаги", `${o.instruments.length} найдено${q ? "" : " · без денег, депозитов, РЕПО и дебиторской задолженности"}${o.instruments.length > 60 ? " · показаны первые 60" : ""}`,
        tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Бумага"), th("ISIN"), th("Фондов", "num"), th("млрд ₽", "num"))), h("tbody", {}, iRows.length ? iRows : h("tr", {}, h("td", { colspan: "4", class: "npf-empty" }, "Нет совпадений")))), { maxHeight: "640px" }))),
    noteP("Эмитенты сгруппированы по ОГРН и ISIN; это аналитическая группировка, а не юридическая консолидация групп компаний. Денежные остатки, депозиты и РЕПО отнесены к банку или контрагенту.")];
}

function secDetail(o) {
  const s = S.sec, CL = ix.CL, db = ix.db;
  const d = N.securityDetail(ix, { kind: S.kind, month: o.month, sel: parseSel(S.routeId) });
  const back = link("#/npf/securities", () => nav("securities", null, "npf-tab-securities"), { class: "btn", id: "npf-s-back" }, "← К списку");
  if (!d) return [back, notice("warn", "Эмитент или бумага не найдены в базе."), ];
  const rep = d.rep, isI = parseSel(S.routeId).t === "i";
  const lb = chartBox("Вложения фондов в выбранный объект во времени, млрд ₽", 320);
  lineChart(lb.canvas, { labels: d.xs.map(ml), series: d.series.map((x, j) => ({ name: x.name, color: j === 0 ? INK : seriesColor(j - 1), v: x.v })), tick: (y) => f1(y, d.maxTotal / 1e9 < 20 ? 2 : 1), tip: (y) => `${f1(y, 2)} млрд ₽`,
    yTitle: "млрд ₽", sel: d.xs.indexOf(o.month), onPick: (i) => { if (o.avail.some((x) => x.month === d.xs[i])) { s.month = d.xs[i]; draw(); } } });
  const toFund = (x) => openFund(x.fi, { src: "comp", tab: "holdings", q: isI && db.isins[rep[1]] ? db.isins[rep[1]] : "", idx: N.fundDates(ix, x.fi, x.k, "comp").findIndex((dd) => dd.slice(0, 7) === o.month) }, "npf-pane");
  const holders = card(`Держатели на ${ml(o.month)}`, null, h("div", { class: "toolbar" }, csvBtn("npf-s-csv", () => downloadCsv(`holders-${o.month}.csv`, [["Объект", "Месяц", "Фонд", "Средства", "Дата состава", "Стоимость, руб.", "Доля в портфеле фонда, %", "Количество"],
    ...d.holders.map((x) => [d.title, o.month, ix.F[x.fi].name, N.KN[x.k], x.c.d, csvNum(x.value, 2), x.share == null ? "" : csvNum(x.share, 4), x.hasQty ? csvNum(x.qty, 6) : ""])]))),
    tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Фонд"), th("Средства"), th("Стоимость, млн ₽", "num"), th("Доля в портфеле фонда", "num"), isI ? th("Количество", "num") : null, th("Дата"))),
      h("tbody", {}, d.holders.length ? d.holders.map((x) => h("tr", {}, h("th", { scope: "row" }, link(`#/npf/fund/${encodeURIComponent(ix.F[x.fi].id)}`, () => { S.kind = x.k; toFund(x); }, {}, ix.F[x.fi].name)), h("td", {}, N.KN[x.k]),
        h("td", { class: "num" }, f1(x.value / 1e6, 1)), h("td", { class: "num" }, x.share == null ? "—" : `${f1(x.share, 2)} %`), isI ? h("td", { class: "num" }, x.hasQty ? fmtNum(x.qty, 0) : "—") : null, h("td", { class: "sm" }, dmy(x.c.d))))
        : h("tr", {}, h("td", { colspan: "6", class: "npf-empty" }, "На выбранный месяц держателей нет")))), { maxHeight: "560px" }));
  const insts = !isI ? card("Инструменты эмитента", `${d.instruments.length} на ${ml(o.month)}`, tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Инструмент"), th("ISIN"), th("Фондов", "num"), th("млн ₽", "num"))),
    h("tbody", {}, d.instruments.slice(0, 150).map((x) => h("tr", {}, h("th", { scope: "row", class: "name" }, link(`#/npf/securities/${encodeURIComponent(x.key)}`, () => nav("securities", x.key), {}, x.name), h("br"), h("small", {}, dot(CL[x.cls].c), ` ${CL[x.cls].s}`)),
      h("td", { class: "mono" }, x.isin || "—"), h("td", { class: "num" }, x.nFunds), h("td", { class: "num" }, f1(x.value / 1e6, 1)))))), { maxHeight: "560px" })) : null;
  return [back,
    h("section", { class: "card npf-card" }, h("p", { class: "hint" }, d.kindLabel), h("h3", { class: "card-title npf-fundname" }, d.title),
      isI ? h("p", { class: "hint" }, h("span", { class: "mono" }, db.isins[rep[1]] || "без ISIN"), " · ", dot(CL[rep[3]].c), ` ${CL[rep[3]].n} · ${ix.SEC[rep[4]].n} · `,
        link(`#/npf/securities/${encodeURIComponent(`g:${rep[5]}`)}`, () => nav("securities", `g:${rep[5]}`), {}, db.groups[rep[5]] || "")) : null),
    h("div", { class: "kpis" },
      kpi(`В портфелях на ${ml(o.month)}`, `${f1(d.total / 1e9, 2)} млрд ₽`, `${f1(d.totalShare, 2)} % АУМ наблюдаемых фондов`), kpi("Держателей", String(d.nHolders), "фондов на выбранный месяц"),
      kpi("Максимум за историю", `${f1(d.maxTotal / 1e9, 2)} млрд ₽`, ml(d.maxMonth)), kpi("Период присутствия", `${ml(d.first)} – ${ml(d.last)}`, "в раскрытых составах")),
    card("Вложения фондов во времени, млрд ₽", "Сумма по всем фондам и крупнейшие держатели. Изменение числа раскрывающих фондов влияет на итог. Нажмите на график, чтобы выбрать месяц.", lb.box, legend(d.series.map((x, j) => [x.name, j === 0 ? INK : seriesColor(j - 1)]))),
    h("div", { class: isI ? "" : "npf-grid2" }, holders, insts)];
}

// ================================================================ ДАННЫЕ И МЕТОДИКА
const CLASS_MAP = [["ОФЗ; прочие государственные облигации", 0], ["Региональные и муниципальные облигации", 1], ["Корпоративные облигации", 2], ["Ипотечные ценные бумаги", 3], ["Долговые бумаги без разбивки", 4],
  ["Облигации международных финансовых организаций", 5], ["Акции", 6], ["Паи инвестиционных фондов", 7], ["Депозиты", 8], ["Денежные средства", 9], ["РЕПО", 10], ["Драгоценные металлы", 11], ["Недвижимость", 12],
  ["Дебиторская задолженность", 13], ["Прочие активы; иные ценные бумаги (состав не раскрыт)", 14], ["Раскрытые обязательства (учитываются отдельно, в АУМ не входят)", 15], ["Разница с итогом источника; оценённый нераскрытый остаток", 16]];
const COVER = { both: "состав и структура", comp: "только состав", struct: "только структура" };

function sData() {
  const d = N.dataOverview(ix, S.kind), CL = ix.CL, db = ix.db;
  const csvStruct = () => downloadCsv("npf-structure-all.csv", [["Фонд", "Вид средств", "Дата", "Источник", "Статус", ...CL.map((c) => `${c.n}, %`)], ...N.structureTable(ix).map((r) => [r.fund, r.kind, r.date, r.source, r.status, ...r.shares.map((x) => csvNum(x, 4))])]);
  const csvAum = () => downloadCsv("npf-aum-nav.csv", [["Фонд", "Вид средств", "Дата", "АУМ, руб.", "Оценка", "СЧА, руб.", "Обязательства, руб.", "Итог источника, руб.", "Строк", "Сверка итога", "Файл"],
    ...N.aumTable(ix).map((r) => [r.fund, r.kind, r.date, csvNum(r.aum, 2), r.est ? "да" : "", csvNum(r.nav, 2), csvNum(r.liabilities, 2), csvNum(r.reported, 2), r.rows, r.status, r.file])]);
  const years = [...new Set(d.months.map((m) => m.slice(0, 4)))];
  const cov = h("table", { class: "npf-cov" }, h("caption", { class: "sr-only" }, "Покрытие данных по фондам и месяцам"),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Фонд"), years.map((y) => h("th", { scope: "colgroup", colspan: String(d.months.filter((m) => m.startsWith(y)).length), class: "yr" }, y)))),
    h("tbody", {}, d.coverage.map((r) => h("tr", {}, h("th", { scope: "row" }, fundLink(r.fi, ix.F[r.fi].name)),
      r.cells.map((c) => h("td", { class: `cv ${c.v || "none"}`, title: `${ix.F[r.fi].name} · ${ml(c.month)}: ${c.v ? COVER[c.v] : "нет данных"}` }, h("span", { class: "sr-only" }, c.v ? COVER[c.v] : "нет данных")))))));
  const det = (title, ...body) => h("details", { class: "npf-det" }, h("summary", {}, title), ...body);
  return [
    h("div", { class: "card npf-filters" }, h("div", { class: "toolbar" },
      h("button", { type: "button", class: "btn", id: "npf-d-struct", onclick: csvStruct }, "Структура всех фондов · CSV"), h("button", { type: "button", class: "btn", id: "npf-d-aum", onclick: csvAum }, "АУМ и СЧА · CSV"),
      h("button", { type: "button", class: "btn", id: "npf-d-json", onclick: () => downloadBlob("npf-platform-db.json", JSON.stringify(db), "application/json") }, "Вся база · JSON")),
      h("p", { class: "hint" }, `Единая база: составы портфелей и отчёты о структуре · сборка ${longDate(d.built)}`)),
    h("div", { class: "kpis" }, kpi("Фондов", String(d.nFunds), `${d.nWithComp} с составом · ${d.nWithStruct} со структурой`), kpi("Срезов состава", fmtNum(d.nComp, 0), `${fmtNum(d.nCompRows, 0)} строк активов`),
      kpi("Отчётов о структуре", fmtNum(d.nStruct, 0), `${d.nStructReview} требуют сверки`), kpi("Уникальных инструментов", fmtNum(d.nInstruments, 0), `${fmtNum(d.nGroups, 0)} эмитентов и контрагентов`),
      kpi("Период", `${ml(d.from)} – ${ml(d.to)}`, `структура обновлена ${longDate(d.monitorUpdated)}`)),
    card(`Покрытие по месяцам · ${S.kind === "all" ? "любой вид средств" : N.KNL[S.kind].toLowerCase()}`, "Нажмите на название, чтобы открыть фонд. Таблица прокручивается по горизонтали.",
      h("ul", { class: "npf-legend" }, h("li", {}, h("i", { class: "npf-sw both" }), "Состав и структура"), h("li", {}, h("i", { class: "npf-sw comp" }), "Только состав"), h("li", {}, h("i", { class: "npf-sw struct" }), "Только структура"), h("li", {}, h("i", { class: "npf-sw none" }), "Нет данных")),
      h("div", { class: "table-wrap", tabindex: "0" }, cov)),
    h("div", { class: "npf-grid2" },
      card("Методика", null,
        det("Два источника данных", h("p", {}, h("b", {}, "Состав портфеля"), " — перечень бумаг и иных активов со стоимостью, который фонды раскрывают по п. 1.2.5 Указания Банка России № 5175-У. Из него рассчитаны АУМ, доли классов, секторы, эмитенты и концентрация."),
          h("p", {}, h("b", {}, "Отчёт о структуре"), " — доли видов активов, которые публикует сам фонд. Ряд длиннее и свежее, доступен и для фондов без раскрытого состава, но не содержит сумм и бумаг."),
          h("p", {}, "На странице фонда источник переключается, а вкладка «Сверка со структурой» сопоставляет оба раскрытия за один месяц.")),
        det("АУМ и СЧА", h("p", {}, h("b", {}, "АУМ"), " — стоимость активов портфеля до вычета отдельно раскрытых обязательств. При частичном раскрытии берётся опубликованный или оценённый полный объём, если его можно установить; остаток показан классом «Нераскрытая часть и остаток». Знак ≈ означает оценку."),
          h("p", {}, h("b", {}, "СЧА"), " показывается только когда чистый итог подтверждён отчётом: раскрыты обязательства и опубликованный итог сходится с активами за их вычетом. Иначе — «Нет данных»."), h("p", {}, d.aumNote)),
        det("Секторы", h("p", {}, `Адаптированная аналитическая классификация на основе секторов GICS; это не официальные присвоения MSCI / S&P. ${d.sectorNote}`)),
        det("Эмитенты и сквозной поиск", h("p", {}, "Строки разных фондов объединены в эмитента по ОГРН, а при его отсутствии — по ISIN и нормализованному названию. Название группы — самое частое написание в отчётах. Ошибки реквизитов в исходных отчётах могут приводить к неточному объединению отдельных строк.")),
        det("Месячное выравнивание", h("p", {}, "Фонды публикуют срезы на разные дни (последний рабочий или календарный день). Для сравнения между фондами используется последний срез внутри календарного месяца. Пропуски не заполняются.")),
        det("Что не вошло из исходного атласа", h("p", {}, "Для компактности (≈14 МБ вместо 300 МБ) не перенесены построчные служебные поля сверки: исходные ячейки, формулы долей, протоколы распознавания, таблица рейтингов и дюрации Сургутнефтегаза. Все строки активов, суммы, количества, ссылки на файл и страницу, статусы сверки итогов и примечания к срезам сохранены. Исходный файл атласа остаётся архивом полной сверки."))),
      card("Единые классы активов", "Категории состава и 250 исходных названий строк из отчётов о структуре приведены к одним классам и цветам.",
        tableWrap(h("table", { class: "npf-table" }, h("thead", {}, h("tr", {}, th("Единый класс"), th("Категории состава"))), h("tbody", {}, CLASS_MAP.map(([t, k]) => h("tr", {}, h("th", { scope: "row" }, dot(CL[k].c), " ", CL[k].n), h("td", { class: "sm" }, t)))))))),
  ];
}
