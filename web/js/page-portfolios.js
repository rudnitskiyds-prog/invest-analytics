// Портфели #/portfolios/lazy (листинг) и #/portfolio/{slug} — перенос pages/8_Портфели.py.
// Все числа — готовые из public/data/portfolios.json (сборщик); здесь только отбор и форматирование.

import { colorOf, heatStyle, lineChart, renderChart, themeColors } from "./charts.js";
import { DATA_BASE } from "./config.js";
import { downloadCsv, h, notice, put, tableWrap } from "./dom.js";
import { fmtDate, fmtDateTime, fmtNum, fmtPct, fmtRub } from "./format.js";
import { hint } from "./glossary.js";
import { go, href, parseHash, setQuery } from "./router.js";
import * as backtest from "./tab-backtest.js";
import { colHint, csvCell, csvHead, fmtBigRub, fmtCell, nextSort, signClass, skeleton, sortableHead } from "./page-screener.js";
import { sortRows } from "../lib/screener.js";
import { drawdownSeries } from "../lib/metrics.js";

const GROUPS = [["", "Все"], ["Ленивые портфели", "Ленивые"], ["Тематические", "Тематические"]];
const COLS = [
  { key: "name", label: "Портфель", type: "text" },
  { key: "ret_1d", label: "1 день", type: "pct" },
  { key: "ytd", label: "С начала года", type: "pct" },
  { key: "cagr_10y", label: "В год за 10 лет", type: "pct" },
  { key: "rank", label: "Рейтинг (5 лет)", type: "rank" },
  { key: "max_drawdown", label: "Макс. просадка", type: "pct" },
  { key: "current_drawdown", label: "Текущая просадка", type: "pct" },
  { key: "fee", label: "Комиссия фондов", type: "pct2" },
  { key: "sharpe", label: "Шарп", type: "ratio" },
  { key: "sortino", label: "Сортино", type: "ratio" },
  { key: "omega", label: "Омега", type: "ratio" },
  { key: "martin", label: "Мартин", type: "ratio" },
  { key: "calmar", label: "Кальмар", type: "ratio" },
  { key: "ulcer_index", label: "Индекс язвы", type: "pct2" },
  { key: "since", label: "История с", type: "date" },
];
const CSV_EXTRA = [{ key: "group", label: "Группа" }, { key: "cagr_all", label: "В год за всё время", type: "pct" },
  { key: "volatility_1y", label: "Волатильность за год", type: "pct" }, { key: "last_date", label: "Данные на" },
  { key: "benchmark", label: "Бенчмарк" }, { key: "fee_coverage", label: "Комиссия известна для доли", type: "pct" }];
const RR_PERIODS = [["1Y", "1 год"], ["5Y", "5 лет"], ["10Y", "10 лет"]];
const RETURN_PERIODS = [["1D", "1 день"], ["1W", "1 неделя"], ["1M", "1 месяц"], ["6M", "6 месяцев"], ["YTD", "С начала года"],
  ["1Y", "1 год"], ["3Y", "3 года*"], ["5Y", "5 лет*"], ["10Y", "10 лет*"], ["ALL", "Всё время*"]];
const METRIC_WINDOWS = [["1Y", "1 год"], ["5Y", "5 лет"], ["10Y", "10 лет"], ["MAX", "Всё время"]];
const METRIC_ROWS = [["total_return", "Доходность за период"], ["cagr", "Доходность в год"], ["volatility", "Волатильность в год"],
  ["max_drawdown", "Макс. просадка"], ["current_drawdown", "Текущая просадка"], ["sharpe", "Коэф. Шарпа"], ["sortino", "Коэф. Сортино"],
  ["omega", "Коэф. Омега"], ["calmar", "Коэф. Кальмара"], ["martin", "Коэф. Мартина"], ["ulcer_index", "Индекс язвы"],
  ["alpha", "Альфа Дженсена (год.)"], ["beta", "Бета"], ["r_squared", "R² с бенчмарком"], ["correlation", "Корреляция с бенчмарком"],
  ["up_capture", "Захват роста"], ["down_capture", "Захват падения"]];
const REL_KEYS = new Set(["alpha", "beta", "r_squared", "correlation", "up_capture", "down_capture"]);
const PCT_KEYS = new Set(["total_return", "cagr", "volatility", "max_drawdown", "current_drawdown", "ulcer_index", "alpha", "up_capture", "down_capture"]);
const MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
const REBAL_RU = { A: "раз в год", Q: "раз в квартал", M: "раз в месяц", H: "раз в полгода", none: "без ребалансировки" };

const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const cell = (type, v) => (type === "ratio" || type === "rank" ? (isNum(v) ? fmtNum(v, type === "rank" ? 0 : 2) : "—") : fmtCell(type, v));
const signedPct = (v) => (isNum(v) ? `${v > 0 ? "+" : ""}${fmtPct(v, 1)}` : "—");

let root = null;
let dataP = null;
let seq = 0;

function loadData() {
  if (!dataP) {
    dataP = fetch(`${DATA_BASE}portfolios.json`, { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    dataP.catch(() => { dataP = null; });
  }
  return dataP;
}

export function init() {}
export function onGlobalChange() {}
export function rerender() {}

export async function onShow(route) {
  root = document.getElementById("portfolios-root");
  if (!root) return;
  const my = ++seq;
  put(root, skeleton());
  let d;
  try {
    d = await loadData();
  } catch (e) {
    if (my !== seq) return;
    console.warn(e);
    put(root, h("div", { class: "panel-head" }, h("h2", {}, "Портфели")),
      notice("error", "Не удалось загрузить данные портфелей (portfolios.json). Обновите страницу чуть позже."));
    return;
  }
  if (my !== seq) return;
  if (!d?.items || !Object.keys(d.items).length) {
    put(root, h("div", { class: "panel-head" }, h("h2", {}, "Портфели")),
      notice("info", "Данных по портфелям пока нет: они пересчитываются ночью. Загляните завтра."));
    return;
  }
  const slug = route?.params?.slug;
  if (slug) renderPortfolio(d, slug);
  else renderListing(d);
}

// ================================================================== листинг

function renderListing(d) {
  const query = parseHash().query;
  const state = { g: query.get("g") || "", sort: { key: query.get("sort") || "rank", dir: query.get("dir") === "asc" ? "asc" : "desc" }, rr: query.get("rr") || "10Y" };
  const all = Object.entries(d.items).map(([slug, it]) => ({ slug, ...it.row, stale: d.stale?.[slug] }));
  const tableBox = h("div");
  const chartBox = h("div");
  const seg = (items, cur, onPick, label) => h("div", { class: "seg", role: "group", "aria-label": label },
    items.map(([v, t]) => h("button", { type: "button", class: "seg-btn", "aria-pressed": String(v === cur), onclick: () => onPick(v) }, t)));
  const groupSeg = h("div");

  const update = () => {
    setQuery({ g: state.g, sort: state.sort.key, dir: state.sort.dir, rr: state.rr });
    put(groupSeg, seg(GROUPS, state.g, (v) => { state.g = v; update(); }, "Группа"));
    const rows = sortRows(all.filter((r) => !state.g || r.group === state.g), state.sort.key, state.sort.dir);
    const partial = rows.filter((r) => isNum(r.fee_coverage) && r.fee_coverage > 0 && r.fee_coverage < 0.999);
    put(tableBox, rows.length ? [
      tableWrap(h("table", { class: "scr-table pf-list" },
        h("caption", { class: "sr-only" }, "Готовые портфели"),
        sortableHead(COLS, state.sort, (key, num) => { state.sort = nextSort(state.sort, key, num); update(); }),
        h("tbody", {}, rows.map((r) => h("tr", {
          class: "row-link",
          onclick: (e) => { if (!e.target.closest("a, button")) go(`/portfolio/${r.slug}`); },
        }, COLS.map((c, i) => (i === 0
          ? h("th", { scope: "row", class: "scr-first" }, h("a", { href: href(`/portfolio/${r.slug}`) }, r.name),
            r.stale ? h("span", { class: "muted" }, ` (данные на ${fmtDate(r.last_date)})`) : null)
          : h("td", { class: ["num", ["ret_1d", "ytd", "cagr_10y"].includes(c.key) ? signClass(r[c.key]) : ""].filter(Boolean).join(" ") },
            cell(c.type, r[c.key]) + (c.key === "fee" && partial.includes(r) ? "*" : ""))))))))),
      h("p", { class: "hint" }, `Портфелей: ${fmtNum(rows.length)}. Коэффициенты — за последний год, рейтинг — за 5 лет («—» — история короче 5 лет), просадки — за всю историю.`),
      partial.length ? h("p", { class: "hint" }, "* Комиссия известна не для всего портфеля: " +
        partial.map((r) => `${r.name} — ${fmtPct(r.fee_coverage, 0)}`).join("; ") + ".") : null,
    ] : h("p", { class: "no-data muted" }, "В этой группе пока нет портфелей с данными."));
    renderRiskReturn(chartBox, d, state, rows, seg, update);
  };

  const skipped = Object.entries(d.skipped || {});
  put(root,
    h("div", { class: "panel-head" },
      h("h2", {}, "Ленивые портфели"),
      h("p", { class: "lead" }, "Готовые портфели с постоянными долями активов: раз в период доли возвращаются к заданным. История построена по индексам полной доходности (с дивидендами и купонами), повторить портфель можно через биржевые фонды (БПИФ). Результаты в прошлом не гарантируют доходность в будущем.")),
    h("p", { class: "scr-meta muted" }, `Данные на ${fmtDate(d.last)} · обновлено ${fmtDateTime(d.updated)} · источник: ${d.source || "—"}`),
    h("div", { class: "scr-actions" }, groupSeg,
      h("button", { type: "button", class: "btn btn-sm", onclick: () => exportListing(all.filter((r) => !state.g || r.group === state.g)) }, "Скачать CSV")),
    tableBox,
    skipped.length ? h("details", { class: "card" }, h("summary", {}, `Нет данных: ${fmtNum(skipped.length)} портф.`),
      h("ul", {}, skipped.map(([s, reason]) => h("li", {}, h("b", {}, d.items[s]?.row?.name || s), ` — ${reason}`)))) : null,
    h("h3", { class: "section-title" }, "Риск и доходность"),
    chartBox);
  update();
}

function exportListing(rows) {
  const cols = [...COLS, ...CSV_EXTRA];
  downloadCsv("portfolios.csv", [cols.map(csvHead), ...rows.map((r) => cols.map((c) => csvCell(r[c.key], c.type)))]);
}

function renderRiskReturn(box, d, state, rows, seg, update) {
  const ids = new Set(rows.map((r) => r.slug));
  const pts = (d.risk_return?.[state.rr] || []).filter((p) => ids.has(p.id) && isNum(p.ret) && isNum(p.volatility));
  const label = RR_PERIODS.find(([k]) => k === state.rr)?.[1] || state.rr;
  const canvas = h("canvas", { role: "img", "aria-label": `Риск и доходность портфелей за ${label}` });
  put(box,
    seg(RR_PERIODS, state.rr, (v) => { state.rr = v; update(); }, "Период"),
    pts.length
      ? [h("p", { class: "hint" }, `Чем выше точка — тем больше доходность; чем правее — тем сильнее колебалась стоимость. Портфели с историей короче периода не показаны.`),
        h("div", { class: "chart-box" }, canvas)]
      : h("p", { class: "no-data muted" }, "Для этого периода не хватает истории."));
  if (!pts.length || !window.Chart) return;
  const groups = [...new Set(pts.map((p) => p.group))].sort();
  renderChart(canvas, () => {
    const t = themeColors();
    const axis = (text) => ({ title: { display: true, text, color: t.muted }, grid: { color: t.grid }, ticks: { color: t.muted, callback: (v) => `${fmtNum(v * 100, 0)} %` } });
    return {
      type: "scatter",
      data: {
        datasets: groups.map((g) => ({
          label: g, backgroundColor: colorOf(g), borderColor: "#fff", borderWidth: 1.5, pointRadius: 7, pointHoverRadius: 9,
          data: pts.filter((p) => p.group === g).map((p) => ({ x: p.volatility, y: p.ret, name: p.name })),
        })),
      },
      options: {
        plugins: {
          legend: { labels: { color: t.text } },
          tooltip: { callbacks: { label: (it) => ` ${it.raw.name}: доходность ${fmtPct(it.raw.y, 1)} в год, волатильность ${fmtPct(it.raw.x, 1)}` } },
        },
        scales: { x: axis(`Волатильность в год, % (за ${label})`), y: axis(`Доходность в год, % (за ${label})`) },
      },
    };
  });
}

// ================================================================== страница портфеля

function section(title, ...body) {
  return h("section", { class: "card" }, h("h3", { class: "card-title" }, title), ...body);
}

function renderPortfolio(d, slug) {
  const it = d.items[slug];
  if (!it?.report) {
    put(root, h("div", { class: "panel-head" }, h("h2", {}, "Портфель не найден")),
      notice("warn", `Портфеля «${slug}» нет в списке.`),
      h("p", {}, h("a", { href: href("/portfolios/lazy") }, "← Все портфели")));
    return;
  }
  const rep = it.report;
  const name = rep.name || slug;
  const bench = rep.benchmark_short || rep.benchmark || "Бенчмарк";
  const short = (k) => rep.assets_short?.[k] || k;
  const facts = [
    rep.since && ["История с", fmtDate(rep.since)],
    rep.last_date && ["Данные на", fmtDate(rep.last_date)],
    rep.rebalance && ["Ребалансировка", rep.rebalance === "none" ? "без ребалансировки" : rep.rebalance_label || REBAL_RU[rep.rebalance] || rep.rebalance],
    rep.benchmark && ["Бенчмарк", bench],
  ].filter(Boolean);

  document.title = `${name} — FinServ`;
  put(root,
    h("p", { class: "crumbs" }, h("a", { href: href("/portfolios/lazy") }, "Портфели"), rep.group ? ` / ${rep.group}` : "", ` / ${name}`),
    h("div", { class: "panel-head" }, h("h2", {}, name), rep.description ? h("p", { class: "lead" }, rep.description) : null),
    d.stale?.[slug] ? notice("warn", `Последнее обновление не удалось — показаны данные на ${fmtDate(rep.last_date)}.`) : null,
    (rep.warnings || []).map((w) => notice("warn", w)),
    h("section", { class: "card" },
      rep.rules ? h("p", {}, h("b", {}, "Правила. "), rep.rules) : null,
      h("dl", { class: "facts" }, facts.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v))))),
    h("div", { class: "pf-grid" }, composition(rep, short), growth(d, slug, rep, name, bench)),
    returnsBlock(rep, name, bench),
    monthlyBlock(rep),
    metricsBlock(rep, bench),
    drawdownsBlock(rep, d.series?.[slug] || [], name),
    rebalanceBlock(rep, short),
    replicateBlock(rep),
    toBacktest(rep, name),
    h("p", { class: "disclaimer muted" }, "Результаты в прошлом не гарантируют доходность в будущем. Это не инвестиционная рекомендация."));
}

function composition(rep, short) {
  const tw = rep.weights || {};
  const cw = rep.current_weights || {};
  const assets = [...Object.keys(tw), ...Object.keys(cw).filter((a) => !(a in tw))];
  if (!assets.length) return section("Состав", h("p", { class: "muted" }, "Состав портфеля не задан."));
  const bar = (v) => h("span", { class: "pf-bar" }, h("span", { style: { width: `${isNum(v) ? v * 100 : 0}%` } }));
  return section("Состав",
    h("p", { class: "card-sub" }, `Целевые доли задают правила портфеля; текущие — доли на ${fmtDate(rep.last_date)} с учётом движения активов после последней ребалансировки.`),
    tableWrap(h("table", {},
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Актив"), h("th", { scope: "col", class: "num" }, "Целевая"), h("th", { scope: "col", class: "num" }, "Текущая"))),
      h("tbody", {}, assets.map((a) => h("tr", {},
        h("th", { scope: "row" }, short(a), bar(tw[a])),
        h("td", { class: "num" }, fmtPct(tw[a], 1)),
        h("td", { class: "num" }, fmtPct(cw[a], 1))))))));
}

function growth(d, slug, rep, name, bench) {
  const ser = d.series?.[slug] || [];
  const bser = d.series_benchmark?.[slug] || [];
  if (ser.length < 2) return section(`Рост ${fmtRub(d.initial || 100000)}`, h("p", { class: "muted" }, "Недостаточно истории для графика."));
  const canvas = h("canvas", { role: "img", "aria-label": `Рост ${fmtRub(d.initial || 100000)}: ${name} и бенчмарк` });
  const last = ser[ser.length - 1];
  const blast = bser.length ? bser[bser.length - 1] : null;
  const draw = (log) => window.Chart && lineChart(canvas, {
    series: [{ name, dates: ser.map((p) => p[0]), values: ser.map((p) => p[1]) },
      ...(bser.length >= 2 ? [{ name: `${bench} — бенчмарк`, bench: true, dates: bser.map((p) => p[0]), values: bser.map((p) => p[1]) }] : [])],
    yTitle: "Стоимость, млн ₽", yFormat: "rub", log,
  });
  const box = section(`Рост ${fmtRub(d.initial || 100000)}`,
    h("p", {}, `Стоимость ${fmtRub(d.initial || 100000)}, вложенных ${fmtDate(ser[0][0])}, на ${fmtDate(last[0])}: портфель — `,
      h("b", {}, fmtRub(last[1])),
      blast && blast[0] === last[0] ? [", бенчмарк — ", h("b", {}, fmtRub(blast[1])), " (серый пунктир)"] : null, "."),
    h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => draw(e.target.checked) }), " Логарифмическая шкала"),
    h("div", { class: "chart-box" }, canvas),
    h("p", { class: "hint" }, "С учётом дивидендов и купонов (индексы полной доходности), без налогов и комиссий фондов."));
  requestAnimationFrame(() => draw(false)); // canvas должен быть в документе
  return box;
}

function returnsBlock(rep, name, bench) {
  const mine = rep.returns?.portfolio;
  const bm = rep.returns?.benchmark;
  if (!mine) return section("Доходность по периодам", h("p", { class: "muted" }, "Нет данных о доходности."));
  const val = (src, k) => {
    const x = src?.[k];
    if (!x) return null;
    return ["3Y", "5Y", "10Y", "ALL"].includes(k) && isNum(x.cagr) ? x.cagr : x.ret;
  };
  const td = (v) => h("td", { class: ["num", signClass(v)].filter(Boolean).join(" ") }, signedPct(v));
  return section("Доходность по периодам",
    tableWrap(h("table", { class: "periods-table" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Период"), h("th", { scope: "col", class: "num" }, name),
        bm ? h("th", { scope: "col", class: "num" }, `${bench} — бенчмарк`) : null)),
      h("tbody", {}, RETURN_PERIODS.map(([k, label]) => h("tr", {}, h("th", { scope: "row" }, label), td(val(mine, k)), bm ? td(val(bm, k)) : null))))),
    h("p", { class: "hint" }, "* — среднегодовая доходность (CAGR) для периодов от 3 лет и за всё время; для коротких периодов — изменение стоимости за период." +
      (rep.freq === "M" ? " Ряд месячный, поэтому «1 день» и «1 неделя» — изменение за последний месяц." : "")));
}

function monthlyBlock(rep) {
  const g = rep.monthly || {};
  if (!g.years?.length) return section("Доходность по месяцам", h("p", { class: "muted" }, "Недостаточно истории."));
  let lim = 0;
  (g.cells || []).forEach((row) => row.forEach((v) => { if (isNum(v)) lim = Math.max(lim, Math.abs(v)); }));
  const c = (v) => (isNum(v) ? h("td", { style: heatStyle(v, lim), title: fmtPct(v, 2) }, fmtPct(v, 1)) : h("td", { class: "muted" }, "—"));
  const order = g.years.map((y, i) => [y, i]).reverse();
  return section("Доходность по месяцам",
    h("p", { class: "card-sub" }, "Строки — годы (свежие сверху), столбцы — месяцы; справа — итог года, внизу — медиана по месяцу."),
    tableWrap(h("table", { class: "heatmap monthly" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Год"), MONTHS.map((m) => h("th", { scope: "col" }, m)), h("th", { scope: "col" }, "Год"))),
      h("tbody", {}, order.map(([y, i]) => h("tr", {}, h("th", { scope: "row" }, String(y)), (g.cells[i] || []).map(c),
        h("td", { class: "year-total" }, h("b", { class: signClass(g.yearTotal?.[i]) }, fmtPct(g.yearTotal?.[i], 1)))))),
      h("tfoot", {}, h("tr", {}, h("th", { scope: "row" }, "Медиана"),
        (g.medianByMonth || []).map((v) => h("td", { class: signClass(v) }, fmtPct(v, 1))), h("td"))))));
}

function metricsBlock(rep, bench) {
  const mt = rep.metrics || {};
  const bmt = rep.benchmark_metrics || {};
  const windows = METRIC_WINDOWS.filter(([k]) => mt[k]);
  if (!windows.length) return section("Коэффициенты эффективности", h("p", { class: "muted" }, "Недостаточно истории для расчёта коэффициентов."));
  const w = Object.keys(rep.weights || {}).filter((k) => rep.weights[k] > 0);
  const same = rep.benchmark && w.length === 1 && w[0] === rep.benchmark;
  const rows = METRIC_ROWS.filter(([k]) => !(same && REL_KEYS.has(k)) && windows.some(([wk]) => isNum(mt[wk][k])));
  const f = (k, v) => (!isNum(v) ? "—" : PCT_KEYS.has(k) ? fmtPct(v, k === "ulcer_index" ? 2 : 1) : fmtNum(v, 2));
  const box = h("div");
  const draw = (withB) => put(box, tableWrap(h("table", { class: "metrics-table" },
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Показатель"),
      windows.flatMap(([k, lab]) => [h("th", { scope: "col", class: "num" }, lab),
        withB ? h("th", { scope: "col", class: "num muted" }, `${lab} · ${bench}`) : null]))),
    h("tbody", {}, rows.map(([k, lab]) => h("tr", {}, h("th", { scope: "row" }, lab, colHint(k)),
      windows.flatMap(([wk]) => [h("td", { class: "num" }, f(k, mt[wk][k])),
        withB ? h("td", { class: "num muted" }, REL_KEYS.has(k) ? "" : f(k, bmt[wk]?.[k])) : null])))))));
  draw(false);
  const rates = windows.filter(([k]) => isNum(mt[k].rf)).map(([k, lab]) => `${lab} — ${fmtPct(mt[k].rf, 2)}`).join("; ");
  const spans = windows.map(([k, lab]) => `${lab} — ${fmtDate(mt[k].from)} — ${fmtDate(mt[k].to)}`).join("; ");
  const warns = [...new Set(windows.map(([k]) => mt[k].rf_warning).filter(Boolean))];
  return section("Коэффициенты эффективности",
    Object.keys(bmt).length ? h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => draw(e.target.checked) }), ` Сравнить с бенчмарком: ${bench}`) : null,
    warns.map((t) => notice("warn", t)),
    box,
    h("p", { class: "hint" }, `Окна: ${spans}. ${rates ? `Безрисковая ставка — доходность коротких ОФЗ, в среднем за окно: ${rates}.` : ""} ` +
      (same ? `Портфель целиком состоит из бенчмарка, поэтому показатели относительно него не показаны.` : `Альфа, бета, R² и захваты — относительно бенчмарка «${bench}».`) +
      " Формулы — в разделе «Методика»."));
}

function drawdownsBlock(rep, ser, name) {
  const dd = rep.drawdowns || {};
  const cur = dd.current || {};
  const mx = dd.top?.[0] || {};
  const kpi = (name, value, sub) => h("div", { class: "kpi" }, h("span", { class: "kpi-name" }, name), h("span", { class: "kpi-value" }, value), sub ? h("span", { class: "kpi-sub" }, sub) : null);
  return section("Просадки",
    h("div", { class: "kpis dd-kpis" },
      kpi(["Макс. глубина ", hint("max_drawdown")], fmtPct(dd.max, 1)),
      kpi("Дата пика", mx.peak ? fmtDate(mx.peak) : "—"),
      kpi("Дата дна", mx.trough ? fmtDate(mx.trough) : "—"),
      kpi("Длительность", isNum(mx.daysToRecover) ? `${fmtNum(mx.daysToRecover)} дн.` : mx.peak ? "не восстановлена" : "—", "от пика до восстановления"),
      kpi("Восстановление", mx.recovery ? fmtDate(mx.recovery) : mx.peak ? "не восстановлена" : "—"),
      kpi("Текущая", fmtPct(cur.depth, 1), cur.days ? `${fmtNum(cur.days)} дн. от пика ${fmtDate(cur.peak)}` : "на максимуме"),
      kpi(["Индекс язвы ", hint("ulcer_index")], fmtPct(dd.ulcer_index, 2), "за всю историю")),
    ddChart(ser, name),
    h("h4", { class: "sub-title" }, "Пять крупнейших просадок"),
    dd.top?.length ? tableWrap(h("table", {},
      h("thead", {}, h("tr", {}, ["Глубина", "Начало (максимум)", "Дно", "Восстановление", "Дней до дна", "Дней до восстановления"].map((t, i) =>
        h("th", { scope: "col", class: i === 0 || i > 3 ? "num" : null }, t)))),
      h("tbody", {}, dd.top.map((x) => h("tr", {},
        h("td", { class: "num neg" }, fmtPct(x.depth, 1)), h("td", {}, fmtDate(x.peak)), h("td", {}, fmtDate(x.trough)),
        h("td", {}, x.recovery ? fmtDate(x.recovery) : "не восстановлена"),
        h("td", { class: "num" }, fmtNum(x.daysToTrough)), h("td", { class: "num" }, fmtNum(x.daysToRecover)))))))
      : h("p", { class: "muted" }, "Просадок нет."),
    h("p", { class: "hint" }, "Просадка — падение стоимости от предыдущего максимума до дна. Восстановление — дата, когда стоимость вернулась к прежнему максимуму."));
}

function ddChart(ser, name) {
  if (ser.length < 2 || !window.Chart) return null;
  const dd = drawdownSeries({ dates: ser.map((p) => p[0]), values: ser.map((p) => p[1]) });
  const canvas = h("canvas", { role: "img", "aria-label": `Просадки: ${name}` });
  requestAnimationFrame(() => lineChart(canvas, { series: [{ name, ...dd }], yTitle: "Просадка, %", yFormat: "pct" }));
  return h("div", { class: "chart-box short" }, canvas);
}

function rebalanceBlock(rep, short) {
  const log = rep.rebalance_log || [];
  const assets = Object.keys(rep.weights || {});
  return h("details", { class: "card" },
    h("summary", { class: "card-title" }, `Журнал ребалансировок (${fmtNum(log.length)})`),
    log.length ? [tableWrap(h("table", {},
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Дата"), assets.map((a) => h("th", { scope: "col", class: "num" }, short(a))))),
      h("tbody", {}, [...log].reverse().map((e) => h("tr", {}, h("th", { scope: "row" }, fmtDate(e.date)),
        assets.map((a) => h("td", { class: "num" }, `${fmtPct(e.before?.[a], 1)} → ${fmtPct(e.after?.[a], 1)}`))))))),
    h("p", { class: "hint" }, "В дату ребалансировки доли возвращаются к целевым: продаётся то, что выросло, докупается то, что отстало. В ячейке — доля до и после.")]
      : h("p", { class: "muted" }, "Ребалансировок не было: доли не возвращаются к целевым."));
}

function fundTable(funds) {
  return tableWrap(h("table", {},
    h("thead", {}, h("tr", {}, ["Фонд", "Название", "УК", "Комиссия в год", "СЧА", "Торги"].map((t, i) => h("th", { scope: "col", class: i === 3 || i === 4 ? "num" : null }, t)))),
    h("tbody", {}, funds.map((f) => (typeof f === "string" ? { secid: f } : f || {})).map((f) => h("tr", {},
      h("th", { scope: "row" }, f.secid ? h("a", { href: href(`/symbol/${f.secid}`), }, f.secid) : "—"),
      h("td", {}, f.name || ""), h("td", {}, f.issuer || ""),
      h("td", { class: "num" }, fmtPct(f.fee, 2)), h("td", { class: "num" }, fmtBigRub(f.aum_rub)), h("td", {}, f.trade_status || ""))))));
}

function replicateBlock(rep) {
  const repl = rep.replicate || [];
  if (!repl.length) return section("Как повторить портфель", h("p", { class: "muted" }, "Подходящие биржевые фонды для этого портфеля пока не подобраны."));
  const one = rep.one_fund;
  return section("Как повторить портфель",
    h("p", { class: "card-sub" }, "Для каждого актива — биржевые фонды (БПИФ), которые следуют за тем же или близким индексом. Комиссию портфеля считаем по первому (основному) фонду каждого актива."),
    repl.map((it) => h("div", { class: "pf-repl" },
      h("h4", { class: "sub-title" }, `${it.asset_short || it.asset} — ${fmtPct(it.weight ?? rep.weights?.[it.asset], 1)}`),
      it.note ? h("p", { class: "hint" }, it.note) : null,
      it.funds?.length ? fundTable(it.funds) : h("p", { class: "muted" }, "Подходящего фонда нет."))),
    h("div", { class: "kpis" }, h("div", { class: "kpi" }, h("span", { class: "kpi-name" }, ["Средневзвешенная комиссия", hint("ter")]),
      h("span", { class: "kpi-value" }, isNum(rep.fee) ? `${fmtPct(rep.fee, 2)} в год` : "—"),
      isNum(rep.fee_coverage) && rep.fee_coverage < 0.999 ? h("span", { class: "kpi-sub" }, `известна для ${fmtPct(rep.fee_coverage, 0)} портфеля`) : null)),
    one && (one.funds?.length || one.note) ? [h("h4", { class: "sub-title" }, "Одним фондом"), one.note ? h("p", { class: "hint" }, one.note) : null, one.funds?.length ? fundTable(one.funds) : null] : null,
    notice("warn", "Фонды повторяют индексы неточно: комиссия управляющей компании, налог на дивиденды внутри фонда и ошибка слежения уменьшают результат по сравнению с историей индексов. Это не инвестиционная рекомендация."),
    h("p", { class: "source muted" }, "Комиссии, УК и СЧА фондов: ", h("a", { href: "https://rusetfs.com/", target: "_blank", rel: "noopener" }, "RusETFs (rusetfs.com)"), ", резервно — T-Invest API."));
}

function toBacktest(rep, name) {
  const weights = Object.fromEntries(Object.entries(rep.weights || {}).filter(([, v]) => isNum(v) && v > 0));
  if (!Object.keys(weights).length) return null;
  return h("section", { class: "card cta" },
    h("div", {}, h("h3", { class: "card-title" }, "Проверить портфель в бэктесте"),
      h("p", { class: "card-sub" }, "Состав попадёт в «Свой портфель» на странице бэктеста: там можно задать период, сумму, пополнения, комиссии и налоги и сравнить с другими стратегиями.")),
    h("button", {
      type: "button", class: "btn btn-primary", onclick: () => {
        backtest.setCustomPortfolio(weights, `Состав портфеля «${name}» перенесён в «Свой портфель». Нажмите «Посчитать».`);
        go("/tools/backtest");
      },
    }, "Открыть в бэктесте"));
}

// Данные берутся из public/data — engine (web/lib) для показа не нужен.
export const STATIC = true;
