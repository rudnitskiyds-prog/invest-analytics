// Главная FinServ v2 (web/CONTRACT.md, «Главная v2»): герой, лента стратегий, бенто «от идеи до портфеля»,
// корреляции тематических портфелей, сравнение двух бумаг, популярные бумаги, полоса-призыв.
// Расчётов нет: готовые поля файлов сборщика; корреляции и ряд просадок — функции web/lib/metrics.js.

import { DATA_BASE } from "./config.js";
import { $, h, notice, put } from "./dom.js";
import { engine } from "./engine.js";
import { fmtDate, fmtNum, fmtPct, fmtRub } from "./format.js";
import { assetBadge, IDX_SLUG, loadLogos, nameFor } from "./logo.js";
import { loadIndices, signedPct as idxPct, spark as idxSpark } from "./page-indices.js";
import { go, href, symbolHref } from "./router.js";
import { attachSearch, classLabel, POPULAR } from "./search.js";
import { isDemo } from "./state.js";
import { setCustomPortfolio } from "./tab-backtest.js";
import { loadScreener } from "../lib/screener.js";

const STEPS = [
  { path: "/portfolios/lazy", title: "Изучить", text: "Готовые портфели и их история." },
  { path: "/screener/stocks", title: "Сравнить", text: "Рейтинг акций по CAGR и коэффициентам." },
  { path: "/screener/funds", title: "Выбрать", text: "Фонды с наименьшей комиссией." },
  { path: "/tools/backtest", title: "Проверить на истории", text: "Рост 100 000 ₽ и просадки портфеля 60/40." },
  { path: "/tools/frontier", title: "Подобрать доли", text: "Доли активов во «Всепогодном портфеле»." },
];

const PAIRS = [["SBER", "GAZP"], ["TMOS", "EQMX"], ["LKOH", "ROSN"], ["YDEX", "T"], ["GOLD", "LQDT"]];
const HERO_SLUG = "60-40";
const EXPLORE = ["lazy-plus", "60-40", "permanent-browne"];
const DONUT_SLUG = "all-weather-dalio";
const DONUT_COLORS = ["#0ABAB5", "#E8837A", "#C9AE85", "#5B6676", "#7FD6D2", "#F2B8B2"];
const CORR_MAX = 6;
const NS = "http://www.w3.org/2000/svg";

let inited = false;
let pfShown = false;
let statsShown = false;
let pricesShown = false;
let fundsShown = false;

export function init() {
  if (inited) return;
  inited = true;
  attachSearch($("#home-search"), { onPick: (t) => go(`/symbol/${encodeURIComponent(t)}`) });

  $("#home-steps").replaceChildren(...STEPS.map((s, i) => h("li", { class: `bento-${i + 1}` },
    h("a", { class: "tile card", href: href(s.path) },
      h("span", { class: "tile-head" },
        h("span", { class: "tile-n", "aria-hidden": "true" }, `//0${i + 1}`),
        h("span", { class: "tile-arrow", "aria-hidden": "true" }, "→")),
      h("span", { class: "tile-title" }, s.title),
      h("span", { class: "tile-text" }, s.text),
      h("span", { class: "tile-ui", id: `tile-${i + 1}` }, skel())))));

  $("#home-popular").replaceChildren(...POPULAR.filter((p) => p.cls !== "index").map((p) => h("li", {},
    h("a", { class: "pop-card card", href: symbolHref(p.secid) },
      h("span", { class: "pop-top" }, assetBadge(p.secid, 40, p.cls),
        h("span", { class: "chip-cls badge" }, classLabel(p.cls))),
      h("span", { class: "pop-key" }, p.secid),
      h("span", { class: "pop-name" }, p.name),
      h("span", { class: "pop-side", id: `pop-${p.secid}` },
        h("span", { class: "pop-price" }, "—"),
        h("small", { class: "muted" }, "6 мес.: —"))))));

  initIndexStrip();
  initCompare();
}

// ---------------------------------------------------------------- полоса «Индексы рынка» (indices.json)

const STRIP = ["MCFTR", "IMOEX", "RGBITR", "CORP_CHAIN", "GOLD_CBR", "RUONIA"];

function initIndexStrip() {
  const list = h("ul", { class: "idx-strip", id: "home-indices" });
  const sec = h("section", { class: "home-block", id: "home-idx", "aria-labelledby": "h-home-idx", hidden: true },
    h("div", { class: "block-head" },
      h("h3", { id: "h-home-idx", class: "block-title" }, "Индексы ", h("span", { class: "hl" }, "рынка")),
      h("a", { class: "more", href: href("/indices") }, "Все индексы →")),
    list);
  $("#home-popular").closest("section")?.after(sec);
  loadIndices().then((d) => {
    const by = new Map((d?.items || []).map((i) => [i.key, i]));
    const items = STRIP.map((k) => by.get(k)).filter(Boolean);
    if (!items.length) return;
    list.replaceChildren(...items.map((it) => h("li", { "data-idx": IDX_SLUG[it.class] || "eq" },
      h("a", { class: "idx-mini idx-card card", href: symbolHref(it.key) },
        h("span", { class: "idx-top" }, assetBadge(it.key, 28, { kind: "index" }),
          h("span", { class: "idx-names" }, h("span", { class: "idx-name" }, it.short || it.name || it.key),
            h("span", { class: "idx-sub muted" }, it.class || ""))),
        idxSpark(it.series),
        h("span", { class: "idx-ret" }, h("span", { class: "idx-ret-val" }, idxPct(it.ret_1y)), h("span", { class: "idx-ret-lbl muted" }, "за 1 год"))))));
    sec.hidden = false;
  });
}

const skel = () => h("span", { class: "skeleton" }, h("span"), h("span"));

function initCompare() {
  const form = $("#home-compare");
  const a = $("#cmp-a");
  const b = $("#cmp-b");
  attachSearch(a, { onPick: () => b.focus() });
  attachSearch(b, { onPick: () => form.requestSubmit() });
  const status = $("#cmp-status");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const x = a.value.trim().toUpperCase();
    const y = b.value.trim().toUpperCase();
    if (!x || !y) {
      status.textContent = "Укажите обе бумаги.";
      (x ? b : a).focus();
      return;
    }
    if (x === y) {
      status.textContent = "Выберите две разные бумаги.";
      b.focus();
      return;
    }
    status.textContent = "";
    setCustomPortfolio({ [x]: 0.5, [y]: 0.5 },
      `В «Свой портфель» добавлены ${x} и ${y} по 50 %. Нажмите «Посчитать» — в таблице будут доходность, риск и просадки.`);
    go("/tools/backtest");
  });
  loadLogos().then(() => $("#home-pairs").append(...PAIRS.map(([x, y]) => h("button", {
    type: "button", class: "pair",
    onclick: () => { a.value = x; b.value = y; form.requestSubmit(); },
  }, assetBadge(x, 20), assetBadge(y, 20), h("span", {}, `${x} и ${y}`)))));
}

export function onShow() {
  init();
  if (!pfShown) showPortfolios();
  if (!statsShown) showLeaders();
  if (!pricesShown) showPrices();
  if (!fundsShown) showFunds();
}

// ---------------------------------------------------------------- общие мелочи

const signed = (x) => (Number.isFinite(x)
  ? h("span", { class: x > 0 ? "pos" : x < 0 ? "neg" : null }, (x > 0 ? "+" : "") + fmtPct(x, 1))
  : h("span", { class: "muted" }, "—"));

/** Линия без заливки по значениям (только масштабирование для рисования). */
function sparkline(values, { w = 240, h: hh = 56, cls = "spark", tone = null } = {}) {
  const v = values.filter(Number.isFinite);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${hh}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", cls + (tone ? ` spark-${tone}` : ""));
  svg.setAttribute("aria-hidden", "true");
  if (v.length < 2) return svg;
  const lo = Math.min(...v);
  const span = Math.max(...v) - lo || 1;
  const pts = v.map((y, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(hh - 3 - (y - lo) / span * (hh - 6)).toFixed(1)}`);
  const line = document.createElementNS(NS, "path");
  line.setAttribute("d", `M${pts.join(" L")}`);
  line.setAttribute("class", "spark-line");
  line.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(line);
  return svg;
}
const vals = (series) => (series || []).map((p) => p[1]);

/** Доходность за 6 мес.: row.ret_6m → report.returns["6M"] → YTD. */
function ret6(it) {
  const r6 = it.row?.ret_6m ?? it.report?.returns?.portfolio?.["6M"]?.ret;
  return Number.isFinite(r6) ? { v: r6, tag: "6М" } : { v: it.row?.ytd, tag: "YTD" };
}

// ---------------------------------------------------------------- популярные: цена (ISS) и изменение за 6 мес.

function fmtPrice(v, cls) {
  if (!Number.isFinite(v)) return "—";
  if (cls === "index") return fmtNum(v, 2);
  return `${fmtNum(v, v < 10 ? 3 : 2)} ₽`;
}

let statsP = null;
const loadStats = () => (statsP ||= (engine.symbol?.loadSymbolStats?.({ base: DATA_BASE }) ?? Promise.resolve(null)).catch(() => null));

async function showPrices() {
  const lastPrices = engine.symbol?.fetchLastPrices;
  if (typeof lastPrices !== "function") return;   // lib ещё не загружен — повторим при следующем показе
  pricesShown = true;
  const priced = POPULAR.filter((p) => p.cls !== "bond" && p.cls !== "metal");
  const [stats, prices] = await Promise.all([
    loadStats(),
    isDemo() ? null : lastPrices(priced.map((p) => p.secid)).catch(() => null),   // в демо сеть не используем
  ]);
  for (const p of POPULAR) {
    const box = $(`#pop-${CSS.escape(p.secid)}`);
    if (!box) continue;   // индексы в блоке не показываются (полоса «Индексы рынка»)
    const r6 = stats?.items?.[p.secid]?.ret_6m;
    if (Number.isFinite(r6)) box.querySelector("small").replaceChildren("6 мес.: ", signed(r6));
    const q = prices?.[p.secid];
    if (q) box.querySelector(".pop-price").textContent = fmtPrice(q.last ?? q.prevClose, p.cls);
    else if (isDemo()) box.querySelector(".pop-price").textContent = "Цена — в живом режиме";
  }
}

// ---------------------------------------------------------------- готовые портфели и всё, что из portfolios.json

async function showPortfolios() {
  pfShown = true;
  const strip = $("#home-portfolios");
  const hero = $("#home-hero-card");
  put(strip, ...Array.from({ length: 4 }, () => h("div", { class: "pf-card card skeleton" }, h("span"), h("span"), h("span"))));
  let data;
  try {
    const r = await fetch(`${DATA_BASE}portfolios.json`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    data = await r.json();
  } catch {
    pfShown = false;
    const msg = "Готовые портфели сейчас недоступны (файл portfolios.json не загрузился).";
    put(strip, notice("warn", msg));
    put(hero, h("p", { class: "muted" }, "Пример недоступен."));
    for (const id of ["#tile-1", "#tile-4", "#tile-5", "#home-corr"]) put($(id), h("span", { class: "muted" }, "Нет данных."));
    return;
  }
  const items = Object.entries(data.items || {}).filter(([, it]) => it?.row);
  put(strip, ...items.map(([slug, it]) => {
    const row = it.row;
    const r = ret6(it);
    return h("a", { class: "pf-card card", href: href(`/portfolio/${encodeURIComponent(slug)}`) },
      h("span", { class: "pf-top" },
        h("span", { class: "pf-group" }, row.group || ""),
        h("span", { class: "badge badge-sand" }, r.tag)),
      h("span", { class: "pf-name" }, row.name || slug),
      sparkline(vals(data.series?.[slug]), { tone: r.v < 0 ? "neg" : "pos" }),
      h("span", { class: "pf-stats" },
        h("span", {}, h("small", {}, r.tag === "6М" ? "За 6 месяцев" : "С начала года"), signed(r.v)),
        h("span", {}, h("small", {}, "CAGR, 10 лет"), signed(row.cagr_10y ?? row.cagr_all))));
  }));
  heroCard(hero, data);
  tileExplore(data);
  tileHistory(data);
  tileWeights(data);
  showCorr(data);
}

function heroCard(box, data) {
  const slug = data.items?.[HERO_SLUG] ? HERO_SLUG : Object.keys(data.items || {})[0];
  const it = data.items?.[slug];
  const series = data.series?.[slug] || [];
  if (!it?.row || series.length < 2) {
    put(box, h("p", { class: "muted" }, "Пример недоступен."));
    return;
  }
  const row = it.row;
  const [d0, v0] = series[0];
  const [d1, v1] = series[series.length - 1];
  put(box,
    h("figcaption", { class: "hc-head" },
      h("span", { class: "hc-label" }, row.name),
      h("span", { class: "hc-sub" }, `${fmtRub(v0)} в ${d0.slice(0, 4)} году → на ${fmtDate(d1)}`)),
    h("div", { class: "hc-value" }, fmtRub(v1)),
    h("div", { class: "hc-meta" }, signed(row.cagr_all), h("span", { class: "muted" }, " CAGR за весь период")),
    sparkline(vals(series), { w: 480, h: 120, cls: "spark spark-hero" }),
    h("div", { class: "hc-foot" },
      h("span", {}, h("small", {}, "Макс. просадка"), h("b", { class: "neg" }, fmtPct(row.max_drawdown, 1))),
      h("span", {}, h("small", {}, "Комиссии фондов"), h("b", {}, row.fee != null ? fmtPct(row.fee, 2) : "—")),
      h("a", { class: "btn btn-sm", href: href(`/portfolio/${encodeURIComponent(slug)}`) }, "Подробнее")));
}

function tileExplore(data) {
  const rows = EXPLORE.filter((s) => data.items?.[s]?.row).map((s) => {
    const it = data.items[s];
    return h("span", { class: "mini-row" },
      h("span", { class: "mini-name" }, it.row.name),
      sparkline(vals(data.series?.[s]), { w: 120, h: 28, cls: "spark spark-mini", tone: it.row.cagr_all < 0 ? "neg" : "pos" }),
      h("b", {}, signed(it.row.cagr_all)));
  });
  put($("#tile-1"), rows.length ? h("span", { class: "mini-list" }, ...rows, h("small", { class: "muted" }, "CAGR за весь период"))
    : h("span", { class: "muted" }, "Нет данных."));
}

function tileHistory(data) {
  const box = $("#tile-4");
  const series = data.series?.[HERO_SLUG] || [];
  const dd = engine.metrics?.drawdownSeries;
  if (series.length < 2) return put(box, h("span", { class: "muted" }, "Нет данных."));
  const v = vals(series);
  const ddv = typeof dd === "function" ? dd({ dates: series.map((p) => p[0]), values: v }).values : [];
  const row = data.items[HERO_SLUG].row;
  put(box,
    h("span", { class: "mini-cap" }, h("span", {}, "Рост 100 000 ₽"), h("b", {}, fmtRub(v[v.length - 1]))),
    sparkline(v, { w: 400, h: 70, cls: "spark spark-wide" }),
    ddv.length ? sparkline(ddv, { w: 400, h: 40, cls: "spark spark-dd", tone: "neg" }) : "",
    h("span", { class: "mini-cap" }, h("span", {}, "Просадки (график помесячный); макс. по дневным данным"), h("b", { class: "neg" }, fmtPct(row.max_drawdown, 1))));
}

function tileWeights(data) {
  const box = $("#tile-5");
  const rep = data.items?.[DONUT_SLUG]?.report;
  const w = Object.entries(rep?.weights || {}).filter(([, x]) => x > 0);
  if (!w.length) return put(box, h("span", { class: "muted" }, "Нет данных."));
  // кольцо: доли из файла → дуги (stroke-dasharray), без расчётов показателей
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 42 42");
  svg.setAttribute("class", "donut");
  svg.setAttribute("aria-hidden", "true");
  let off = 25;
  w.forEach(([, x], i) => {
    const c = document.createElementNS(NS, "circle");
    Object.entries({ cx: 21, cy: 21, r: 15.915, fill: "none", stroke: DONUT_COLORS[i % DONUT_COLORS.length], "stroke-width": 6,
      "stroke-dasharray": `${x * 100 - 0.6} ${100 - x * 100 + 0.6}`, "stroke-dashoffset": off }).forEach(([k, val]) => c.setAttribute(k, val));
    svg.append(c);
    off -= x * 100;
  });
  const short = rep.assets_short || {};
  put(box, h("span", { class: "donut-wrap" }, svg,
    h("span", { class: "legend" }, h("small", { class: "muted" }, rep.name || ""),
      ...w.map(([k, x], i) => h("span", { class: "lg-row" },
        h("i", { style: `background:${DONUT_COLORS[i % DONUT_COLORS.length]}` }),
        h("span", {}, short[k] || k), h("b", {}, fmtPct(x, 0)))))));
}

// ---------------------------------------------------------------- корреляции тематических портфелей

function corrColor(c) {
  // шкала: −1…0 — нежно-красный, 0…1 — тиффани; прозрачность по модулю
  const a = Math.min(1, Math.abs(c));
  return c >= 0 ? `rgb(10 186 181 / ${(0.08 + a * 0.82).toFixed(2)})` : `rgb(232 131 122 / ${(0.08 + a * 0.82).toFixed(2)})`;
}

function showCorr(data) {
  const box = $("#home-corr");
  const M = engine.metrics;
  const slugs = Object.keys(data.items || {}).filter((s) => data.items[s].row?.group === "Тематические" && data.series?.[s]?.length > 3)
    .slice(0, CORR_MAX);
  if (typeof M?.correlation !== "function" || slugs.length < 3) {
    put(box, h("p", { class: "muted" }, "Корреляции сейчас недоступны."));
    put($("#corr-seen"), h("p", { class: "muted" }, "Нет данных."));
    put($("#corr-means"), h("p", { class: "muted" }, "Нет данных."));
    return;
  }
  const ret = Object.fromEntries(slugs.map((s) => [s, M.toReturns({ dates: data.series[s].map((p) => p[0]), values: vals(data.series[s]) })]));
  const name = (s) => data.items[s].row.name || s;
  const pairs = [];
  const head = h("tr", {}, h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Портфель")),
    ...slugs.map((s, j) => h("th", { scope: "col", title: name(s) }, String(j + 1))));
  const body = slugs.map((a, i) => h("tr", {},
    h("th", { scope: "row" }, h("span", { class: "hm-n" }, String(i + 1)), name(a)),
    ...slugs.map((b, j) => {
      const c = i === j ? 1 : M.correlation(ret[a], ret[b]);
      if (j > i && Number.isFinite(c)) pairs.push({ a, b, c });
      return h("td", { style: `background:${corrColor(c)}`, class: c > 0.65 ? "hm-dark" : null }, Number.isFinite(c) ? fmtNum(c, 2) : "—");
    })));
  const span = (data.series[slugs[0]][0]?.[0] || "").slice(0, 4);
  put(box,
    h("div", { class: "hm-scroll" }, h("table", { class: "heatmap" }, h("thead", {}, head), h("tbody", {}, ...body))),
    h("figcaption", { class: "hm-legend" },
      h("span", {}, "−1"), h("i", { class: "hm-scale" }), h("span", {}, "+1"),
      h("span", { class: "muted" }, ` Месячные доходности с ${span} г. Источник: portfolios.json`)));
  pairs.sort((x, y) => y.c - x.c);
  const hi = pairs[0];
  const lo = pairs[pairs.length - 1];
  if (!hi) return;
  put($("#corr-seen"),
    h("p", {}, "Наибольшая корреляция — ", h("b", {}, name(hi.a)), " и ", h("b", {}, name(hi.b)), ": ", h("b", { class: "num" }, fmtNum(hi.c, 2)),
      hi.c >= 0.8 ? " (сильная связь)." : "."),
    h("p", {}, "Наименьшая — ", h("b", {}, name(lo.a)), " и ", h("b", {}, name(lo.b)), ": ", h("b", { class: "num" }, fmtNum(lo.c, 2)), "."));
  put($("#corr-means"),
    h("p", {}, `Корреляция ${fmtNum(hi.c, 2)}: портфели чаще движутся в одну сторону. При ${fmtNum(lo.c, 2)} связь слабее. ` +
      "Доли смеси подбирает граница Марковица, результат на истории показывает бэктест."));
}

// ---------------------------------------------------------------- рейтинг акций (плашка 02)

async function showLeaders() {
  const box = $("#tile-2");
  if (typeof engine.symbol?.loadSymbolStats !== "function") return;   // повторим при следующем показе
  statsShown = true;
  const [stats] = await Promise.all([loadStats(), loadLogos()]);
  if (!stats?.items) {
    put(box, h("span", { class: "muted" }, "Рейтинг недоступен (symbol_stats.json не загрузился)."));
    return;
  }
  const top = Object.entries(stats.items).filter(([, v]) => v?.class === "share" && Number.isFinite(v.score))
    .sort((a, b) => b[1].score - a[1].score).slice(0, 5);
  put(box, h("span", { class: "mini-table", role: "table", "aria-label": "Топ-5 акций по баллу рейтинга" },
    h("span", { class: "mt-row mt-head", role: "row" }, h("span", { role: "columnheader" }, "Акция"),
      h("span", { role: "columnheader" }, "CAGR"), h("span", { role: "columnheader" }, "Шарп")),
    ...top.map(([k, v]) => h("span", { class: "mt-row", role: "row" },
      h("span", { class: "mt-key", role: "cell", title: v.name || nameFor(k) || k }, assetBadge(k, 22, "share"), h("span", {}, k)),
      h("span", { role: "cell" }, signed(v.cagr)),
      h("span", { role: "cell" }, fmtNum(v.sharpe, 2))))));
}

// ---------------------------------------------------------------- фонды с наименьшей комиссией (плашка 03)

async function showFunds() {
  fundsShown = true;
  const box = $("#tile-3");
  let rows = [];
  try {
    const [res] = await Promise.all([loadScreener("funds", { base: DATA_BASE }), loadLogos()]);
    rows = res.rows.filter((r) => Number.isFinite(r.commission)).sort((a, b) => a.commission - b.commission).slice(0, 5);
  } catch { /* ниже — пустое состояние */ }
  if (!rows.length) return put(box, h("span", { class: "muted" }, "Данные о комиссиях фондов недоступны."));
  put(box, h("span", { class: "mini-table", role: "table", "aria-label": "Фонды с наименьшей комиссией" },
    h("span", { class: "mt-row mt-head mt-2", role: "row" }, h("span", { role: "columnheader" }, "Фонд"), h("span", { role: "columnheader" }, "Комиссия")),
    ...rows.map((r) => h("span", { class: "mt-row mt-2", role: "row" },
      h("span", { class: "mt-key", role: "cell", title: r.name || r.secid }, assetBadge(r.secid, 22, "fund"), h("span", {}, r.secid)),
      h("span", { role: "cell" }, fmtPct(r.commission, 2))))));
}

export function onGlobalChange() {}
export function rerender() {}
