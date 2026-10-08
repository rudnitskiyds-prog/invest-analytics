// Страница бумаги #/symbol/{TICKER}: блоки docs/STRUCTURE.md, п. 2, по порядку.
// Каждый блок загружается и рисуется независимо: ошибка одного не ломает страницу.
// Все расчёты — web/lib (metrics.js, symbol.js) по контракту этапа 1 (web/CONTRACT.md);
// здесь только выбор данных, срезы по датам и вывод.

import { barChart, colorOf, destroyChart, heatStyle, lineChart, priceDigits } from "./charts.js";
import { DATA_BASE } from "./config.js";
import { $, h, notice, put, tableWrap } from "./dom.js";
import { assetLabel, engine, libFn, requireSymbol, toSet, UserError } from "./engine.js";
import { FREQ_LABELS, globals } from "./fields.js";
import { fmtDate, fmtMetric, fmtNum, fmtPct, fmtRub } from "./format.js";
import { go, href, setQuery } from "./router.js";
import { attachSearch, classLabel } from "./search.js";
import { getSeries } from "./source.js";
import * as S from "./state.js";
import * as backtest from "./tab-backtest.js";

const BENCH_OPTIONS = ["MCFTR", "IMOEX", "MEBCTR", "IRDIVTR", "RGBITR", "RUCBTRNS", "GOLD_CBR", "RUONIA"];

const CHART_PERIODS = [["1M", "1М", "1 месяц"], ["6M", "6М", "6 месяцев"], ["YTD", "YTD", "с начала года"],
  ["1Y", "1Г", "1 год"], ["5Y", "5Л", "5 лет"], ["10Y", "10Л", "10 лет"], ["ALL", "Всё", "вся история"]];
const RETURN_PERIODS = [["1D", "1 день"], ["1W", "1 неделя"], ["1M", "1 месяц"], ["6M", "6 месяцев"], ["YTD", "С начала года"],
  ["1Y", "1 год"], ["3Y", "3 года"], ["5Y", "5 лет"], ["10Y", "10 лет"], ["ALL", "Всё время"]];
const MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
const RATING_KEYS = [["sharpe", "Шарп"], ["sortino", "Сортино"], ["omega", "Омега"], ["calmar", "Кальмар"], ["martin", "Мартин"]];
const RISK_KEYS = ["sharpe", "sortino", "omega", "calmar", "martin"];
const REL_KEYS = ["alpha", "beta", "r_squared", "up_capture", "down_capture", "correlation", "tracking_error", "information_ratio"];
const REL_LABELS = {
  alpha: "Альфа Дженсена (год.)", beta: "Бета", r_squared: "R²", up_capture: "Захват роста (upside capture)",
  down_capture: "Захват падения (downside capture)", correlation: "Корреляция", tracking_error: "Ошибка слежения",
  information_ratio: "Информационный коэф.",
};
const RISK_LABELS = { sharpe: "Коэф. Шарпа", sortino: "Коэф. Сортино", omega: "Коэф. Омега", calmar: "Коэф. Кальмара", martin: "Коэф. Мартина" };

/** Ошибка «данных нет» — показывается спокойным текстом, а не как сбой. */
class NoData extends Error {}

let gen = 0;
let route = null;
const memo = new Map();

function once(key, fn, ttlMs = 0) {
  const hit = memo.get(key);
  if (hit && ttlMs && Date.now() - hit.at > ttlMs) memo.delete(key);
  if (!memo.has(key)) {
    const p = Promise.resolve().then(fn);
    p.catch(() => memo.delete(key)); // при следующем открытии — повторить запрос
    p.at = Date.now();
    memo.set(key, p);
  }
  return memo.get(key);
}

const today = () => new Date().toISOString().slice(0, 10);
const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const msg = (e) => String(e?.message || e);

function humanize(e) {
  const m = msg(e);
  if (/failed to fetch|networkerror|load failed|network/i.test(m)) return "нет связи с источником (сеть недоступна или запрос заблокирован)";
  if (/\b404\b/.test(m)) return "источник не нашёл данные (404)";
  if (/\b5\d\d\b/.test(m)) return "источник временно недоступен";
  return m;
}

// ---------------------------------------------------------------- данные (web/lib/symbol.js)

const infoOf = (id) => once(`info:${id}`, () => libFn("symbol", "fetchSecurityInfo")(id));
// котировки устаревают: снимок храним не дольше 5 минут; свечи уже загружены — передаём их (без лишнего запроса)
const snapOf = (id) => once(`snap:${id}`, async () => {
  const ohlc = await ohlcOf(id).catch(() => null);
  return libFn("symbol", "fetchSnapshot")(id, ohlc ? { ohlc } : {});
}, 5 * 60 * 1000);
const ohlcOf = (id) => once(`ohlc:${id}`, async () => {
  const o = await libFn("symbol", "fetchOHLC")(id, "1990-01-01", today());
  if (!o || !o.dates || !o.dates.length) throw new NoData(`ISS не вернул истории торгов ${id}.`);
  return o;
});
const divsOf = (id) => once(`div:${id}`, () => libFn("symbol", "loadDividends")(id, { base: DATA_BASE }));
const statsOf = () => once("stats", () => libFn("symbol", "loadSymbolStats")({ base: DATA_BASE }));

/** Цены бумаги: close (цена закрытия) и ret — ряд для доходностей (для акций — с дивидендами). */
const priceOf = (id, cls) => once(`price:${id}`, async () => {
  const o = await ohlcOf(id);
  const close = engine.data.dropNaN({ dates: o.dates, values: o.close });
  if (close.dates.length < 3) throw new NoData(`Слишком короткая история торгов ${id}.`);
  let ret = close;
  let withDivs = false;
  let divs = [];
  let divNote = "";
  if (cls === "share") {
    try {
      divs = ((await divsOf(id)) || []).filter((d) => !d.cancelled);
      // в полную доходность — только дивиденды в валюте цены (рубли)
      const rub = divs.filter((d) => !d.currency || /^(RUB|SUR)$/i.test(d.currency));
      if (rub.length) {
        ret = libFn("metrics", "totalReturnSeries")(close, rub);
        withDivs = true;
      } else divNote = "Дивидендов в базе нет — доходность рассчитана по цене.";
    } catch (e) {
      divNote = `Доходность рассчитана по цене, без дивидендов: ${humanize(e)}.`;
    }
  }
  return { close, ret, withDivs, divs, divNote, ohlc: o };
});

const benchOf = (key, from) => once(`bench:${key}:${from}`, () => getSeries(key, from, today()));

/** Ряд для доходностей второй бумаги (сравнение на графике). */
const cmpOf = (id) => once(`cmp:${id}`, async () => {
  let cls = null;
  try {
    cls = (await infoOf(id))?.cls ?? null;
  } catch {
    /* без справки — по цене */
  }
  const p = await priceOf(id, cls);
  return { ...p, cls };
});

// ---------------------------------------------------------------- даты

/** Дата начала периода — web/lib/metrics.js (та же логика, что periodReturns). */
// истории меньше периода (null) — с первой даты ряда, как вкладка «Всё»
const periodStart = (dates, code) => libFn("metrics", "periodStart")(dates, code) ?? dates[0] ?? null;

function priceUnit(cls, currency) {
  if (cls === "bond") return "% от номинала";
  if (cls === "index") return "пунктов";
  if (!currency || /^(RUB|SUR)$/i.test(currency)) return "₽";
  return currency;
}

const fmtPrice = (v, unit) => (isNum(v) ? `${fmtNum(v, priceDigits(v))}${unit === "₽" ? " ₽" : unit === "пунктов" ? "" : unit.startsWith("%") ? " %" : ` ${unit}`}` : "—");
const metric = (key, v) => fmtMetric(key, v, toSet(engine.metrics?.PERCENT_FIELDS));
/** Крупные суммы компактно: «6,50 трлн ₽», «12,3 млрд ₽»; точное значение — в подсказке. */
function fmtBigRub(v) {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  const [k, unit] = a >= 1e12 ? [1e12, "трлн"] : a >= 1e9 ? [1e9, "млрд"] : a >= 1e6 ? [1e6, "млн"] : [1, ""];
  if (k === 1) return fmtRub(v);
  return h("span", { title: fmtRub(v) }, `${fmtNum(v / k, v / k < 10 ? 2 : 1)} ${unit} ₽`);
}
const signed = (v, digits = 1) => (isNum(v) ? (v > 0 ? "+" : "") + fmtPct(v, digits) : "—");
const signClass = (v) => (isNum(v) ? (v > 0 ? "pos" : v < 0 ? "neg" : "") : "");

// ---------------------------------------------------------------- каркас блоков

function skeleton(lines = 3) {
  return h("div", { class: "skeleton", "aria-hidden": "true" }, Array.from({ length: lines }, () => h("span")));
}

/**
 * Создать блок и запустить его заполнение. fn(body, live) — асинхронная; live() === false,
 * если пользователь уже ушёл на другую бумагу. fn может вернуть "skip" — блок убирается.
 */
function block(container, { id, title, sub = "" }, fn) {
  const body = h("div", { class: "blk-body" }, skeleton(id === "chart" ? 6 : 3));
  const subEl = h("p", { class: "card-sub" }, sub);
  subEl.hidden = !sub;
  const sec = h("section", { class: "card sym-block", id: `sym-${id}`, "aria-labelledby": `sym-${id}-t`, "aria-busy": "true" },
    h("h3", { class: "card-title", id: `sym-${id}-t` }, title), subEl, body);
  container.append(sec);
  const my = gen;
  const live = () => my === gen;
  const setSub = (t) => { subEl.textContent = t; subEl.hidden = !t; };
  Promise.resolve()
    .then(() => fn(body, live, setSub))
    .then((r) => {
      if (!live()) return;
      if (r === "skip") sec.remove();
      sec.removeAttribute("aria-busy");
    })
    .catch((e) => {
      if (!live()) return;
      sec.removeAttribute("aria-busy");
      body.querySelectorAll("canvas").forEach(destroyChart);
      if (e instanceof NoData) put(body, h("p", { class: "muted no-data" }, `Нет данных. ${e.message}`));
      else {
        if (!(e instanceof UserError)) console.warn(e);
        put(body, notice("warn", `Блок недоступен: ${humanize(e)}`));
      }
    });
  return sec;
}

const kpi = (name, value, sub = "", cls = "") => h("div", { class: `kpi ${cls}` },
  h("div", { class: "kpi-name" }, name), h("div", { class: "kpi-value" }, value), sub ? h("div", { class: "kpi-sub" }, sub) : null);

function chartCard(canvasLabel, size = "") {
  const canvas = h("canvas", { role: "img", "aria-label": canvasLabel });
  return { canvas, box: h("div", { class: `chart-box ${size}`.trim() }, canvas) };
}

function swatch(name, bench = false) {
  return h("span", { class: `swatch${bench ? " dashed" : ""}`, style: { background: bench ? "transparent" : colorOf(name), borderColor: colorOf(name, { bench }) }, "aria-hidden": "true" });
}

// ---------------------------------------------------------------- маршрут

export function init() {}

export function onShow(r) {
  route = { ticker: r.params.ticker, query: r.query };
  render();
}

export function onGlobalChange() {
  if (route) render();
}

export function rerender() {
  if (route) render();
}

function render() {
  const my = ++gen;
  const root = $("#sym-root");
  root.querySelectorAll("canvas").forEach(destroyChart);
  const t = route.ticker;
  $("#sym-title").textContent = t;
  $("#sym-lead").textContent = "";
  document.title = `${t} — ИнвестАналитика`;

  if (S.isDemo()) {
    put(root, h("div", { class: "notice notice-info", role: "status" },
      h("p", {}, h("strong", {}, "Страница бумаги работает только на живых данных ISS Мосбиржи. "),
        "Ей нужны дневные свечи, текущие котировки и справочник бумаги, а в демо-режиме есть только месячные ряды индексов из эталонных фикстур."),
      h("p", {}, h("a", { href: S.liveHref(), class: "notice-link" }, `Открыть ${t} на живых данных`), " · ",
        h("a", { href: href("/tools/metrics"), class: "notice-link" }, "Коэффициенты индексов в демо"))));
    return;
  }
  if (!/^[A-Z0-9][A-Z0-9_.-]{0,31}$/.test(t)) {
    put(root, notice("error", `«${t}» не похоже на тикер Мосбиржи. Воспользуйтесь поиском в шапке.`));
    return;
  }
  if (!engine.ok && !engine.error) {
    // модули lib ещё грузятся — main.js вызовет onShow повторно, когда они будут готовы
    put(root, skeleton(4));
    return;
  }
  try {
    requireSymbol();
  } catch (e) {
    put(root, notice("error", e.message), h("p", {}, h("a", { href: href("/tools/metrics") }, `Коэффициенты ${t} в инструменте «Коэффициенты бумаги»`)));
    return;
  }

  const head = h("div", { class: "sym-head" }, skeleton(2));
  const toolbar = h("div", { class: "sym-toolbar card" });
  const blocks = h("div", { class: "sym-blocks" });
  put(root, head, toolbar, blocks);

  // запросы — сразу и параллельно; блоки ждут только то, что им нужно
  const pInfo = infoOf(t);
  snapOf(t).catch(() => {});
  ohlcOf(t).catch(() => {});
  pInfo.then((info) => { if (my === gen) renderHead(head, t, info); })
    .catch((e) => {
      if (my !== gen) return;
      const why = e instanceof NoData || !msg(e) ? "ISS не вернул описания бумаги" : humanize(e);
      put(head, notice("warn", `Справка по ${t} недоступна: ${why}. Остальные блоки строятся по истории торгов.`));
    });

  pInfo.catch(() => null).then((info) => {
    if (my !== gen) return;
    const cls = info?.cls ?? null;
    const benchKey = (route.query.get("b") || info?.benchmark || engine.symbol?.DEFAULT_BENCHMARKS?.[cls] || "MCFTR").toUpperCase();
    const cmp = (route.query.get("cmp") || "").toUpperCase();
    renderToolbar(toolbar, t, benchKey, cmp);
    buildBlocks(blocks, { t, info, cls, benchKey, cmp });
  });
}

function renderHead(head, t, info) {
  if (!info) throw new NoData("");
  $("#sym-title").textContent = info.name || info.shortName || t;
  $("#sym-lead").textContent = `${t}${info.shortName && info.shortName !== info.name ? ` · ${info.shortName}` : ""}`;
  document.title = `${info.shortName || t} (${t}) — ИнвестАналитика`;
  const facts = [
    ["Тип", info.typeLabel || classLabel(info.cls)],
    ["ISIN", info.isin],
    [info.cls === "fund" ? "Управляющая компания" : "Эмитент", info.issuer],
    ["Начало торгов", info.firstTradeDate ? fmtDate(info.firstTradeDate) : null],
    ["Дата выпуска", info.cls === "bond" && info.issueDate ? fmtDate(info.issueDate) : null],
    ["Уровень листинга", info.listLevel != null ? String(info.listLevel) : null],
    ["Валюта", /^(SUR|RUB)$/i.test(info.currency || "") ? "рубль" : info.currency],
    ["Режим торгов", info.board],
  ].filter(([, v]) => v != null && v !== "");
  put(head,
    h("dl", { class: "facts" }, facts.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v)))));
}

function renderToolbar(box, t, benchKey, cmp) {
  const sel = h("select", { id: "sym-bench" });
  const keys = [...new Set([benchKey, ...BENCH_OPTIONS])];
  put(sel, ...keys.map((k) => h("option", { value: k }, assetLabel(k))));
  sel.value = benchKey;
  sel.addEventListener("change", () => {
    setQuery({ b: sel.value });
    route.query.set("b", sel.value);
    render();
  });
  const cmpInput = h("input", { id: "sym-cmp", type: "text", value: cmp, placeholder: "Тикер, например GAZP" });
  const apply = (v) => {
    const x = String(v || "").trim().toUpperCase();
    if (x === (route.query.get("cmp") || "")) return;
    setQuery({ cmp: x });
    if (x) route.query.set("cmp", x);
    else route.query.delete("cmp");
    render();
  };
  const field = h("div", { class: "field" }, h("label", { for: "sym-cmp" }, "Сравнить с бумагой"), cmpInput);
  put(box,
    h("div", { class: "field" }, h("label", { for: "sym-bench" }, "Бенчмарк"), sel),
    field,
    cmp ? h("button", { type: "button", class: "btn btn-sm", onclick: () => apply("") }, `Убрать ${cmp}`) : null,
    h("p", { class: "hint" }, `Коэффициенты — по дневным доходностям, безрисковая ставка ${fmtPct(globals().rf, 2)} годовых (меняется в параметрах выше).`),
  );
  attachSearch(cmpInput, { onPick: apply });
}

// ---------------------------------------------------------------- блоки

function buildBlocks(root, ctx) {
  const { t, info, cls, benchKey, cmp } = ctx;
  const pPrice = priceOf(t, cls);
  const pBench = pPrice.then((p) => benchOf(benchKey, p.ret.dates[0]));
  pBench.catch(() => {});
  const g = globals();
  const pReports = Promise.all([pPrice, pBench.catch((e) => ({ error: e }))]).then(([p, b]) => {
    const bench = b && !b.error ? b : null;
    const computeAll = engine.metrics.computeAll;
    const sec = computeAll(p.ret, bench, g.rf, "D");
    const bm = bench && benchKey !== t ? computeAll(bench, bench, g.rf, "D") : null;
    return { sec, bm, bench, benchError: b?.error ?? null, p };
  });
  pReports.catch(() => {});
  const withDivsNote = (p) => (p.withDivs ? "С учётом дивидендов (реинвестирование в дату отсечки)." : p.divNote || "");
  const unit = priceUnit(cls, info?.currency);

  // Ключевые цифры
  block(root, { id: "key", title: "Ключевые цифры" }, async (body, live, setSub) => {
    const s = await snapOf(t);
    if (!live()) return;
    if (!s) throw new NoData("ISS не вернул текущих котировок.");
    setSub(s.date ? `Данные ISS Мосбиржи на ${fmtDate(String(s.date).slice(0, 10))} (с задержкой до 15 минут)` : "");
    const range = isNum(s.low52) && isNum(s.high52) && s.high52 > s.low52 && isNum(s.last)
      ? h("div", { class: "range", role: "img", "aria-label": `Цена в диапазоне 52 недель: от ${fmtPrice(s.low52, unit)} до ${fmtPrice(s.high52, unit)}` },
        h("span", { class: "range-dot", style: { left: `${Math.max(0, Math.min(100, ((s.last - s.low52) / (s.high52 - s.low52)) * 100))}%` } }))
      : null;
    put(body, h("div", { class: "kpis" },
      kpi("Цена", fmtPrice(s.last, unit), s.prevClose != null ? `закрытие накануне ${fmtPrice(s.prevClose, unit)}` : ""),
      kpi("Изменение за день", h("span", { class: signClass(s.changePct) }, signed(s.changePct, 2)),
        isNum(s.change) ? `${s.change > 0 ? "+" : ""}${fmtPrice(s.change, unit)}` : ""),
      h("div", { class: "kpi" }, h("div", { class: "kpi-name", title: "Минимум и максимум внутридневных цен (свечи ISS) за 52 недели" }, "Диапазон 52 недель"),
        h("div", { class: "kpi-value kpi-range" }, `${fmtPrice(s.low52, unit)} — ${fmtPrice(s.high52, unit)}`), range),
      kpi("Оборот за день", fmtBigRub(s.valueRub)),
      s.marketCap != null ? kpi("Капитализация", fmtBigRub(s.marketCap)) : null));
  });

  // График цены
  block(root, { id: "chart", title: "График", sub: "" }, async (body, live, setSub) => {
    const p = await pPrice;
    let b = null;
    let c = null;
    const notes = [];
    try {
      b = benchKey === t ? null : await pBench;
    } catch (e) {
      notes.push(notice("warn", `Бенчмарк ${benchKey}: ${humanize(e)}`));
    }
    if (cmp && cmp !== t) {
      try {
        c = await cmpOf(cmp);
      } catch (e) {
        notes.push(notice("warn", `${cmp}: ${humanize(e)}`));
      }
    }
    if (!live()) return;
    let per = route.query.get("p") || "1Y";
    if (!CHART_PERIODS.some(([k]) => k === per)) per = "1Y";
    let mode = route.query.get("m") === "price" ? "price" : "growth";
    const { canvas, box } = chartCard(`График ${t}`);
    const perGroup = h("div", { class: "seg", role: "group", "aria-label": "Период графика" });
    const modeGroup = h("div", { class: "seg", role: "group", "aria-label": "Что показывать" });
    const caption = h("p", { class: "card-sub" });

    const draw = () => {
      [...perGroup.children].forEach((x) => x.setAttribute("aria-pressed", x.dataset.v === per ? "true" : "false"));
      [...modeGroup.children].forEach((x) => x.setAttribute("aria-pressed", x.dataset.v === mode ? "true" : "false"));
      const xDays = per === "1M" || per === "6M";
      if (mode === "price") {
        const from = periodStart(p.close.dates, per);
        const s = engine.data.sliceSeries(p.close, from, null);
        caption.textContent = `Цена закрытия, ${fmtDate(s.dates[0])} — ${fmtDate(s.dates.at(-1))}`;
        lineChart(canvas, { series: [{ name: t, ...s }], yTitle: `Цена, ${unit}`, yFormat: "price", xDays });
        return;
      }
      const cols = { [t]: p.ret };
      if (c) cols[cmp] = c.ret;
      if (b) cols[benchKey] = b;
      let f = engine.data.alignFrame(cols, { mode: "common" });
      f = engine.data.sliceFrame(f, periodStart(f.dates, per), null);
      const reb = engine.metrics.rebase(f, 100);
      const series = [{ name: t, dates: reb.dates, values: reb.cols[t] }];
      if (c) series.push({ name: cmp, dates: reb.dates, values: reb.cols[cmp] });
      if (b) series.push({ name: benchKey, dates: reb.dates, values: reb.cols[benchKey], bench: true });
      caption.textContent = `Рост 100 ₽ вложений, ${fmtDate(reb.dates[0])} — ${fmtDate(reb.dates.at(-1))}. ` +
        `${p.withDivs ? `${t} — с учётом дивидендов. ` : ""}${c?.withDivs ? `${cmp} — с учётом дивидендов. ` : ""}` +
        (b ? `Бенчмарк — ${assetLabel(benchKey)}.` : "");
      lineChart(canvas, { series, yTitle: "Стоимость, база = 100", yFormat: "num", xDays });
    };
    perGroup.append(...CHART_PERIODS.map(([k, label, full]) => h("button", {
      type: "button", class: "seg-btn", "data-v": k, "aria-label": full, title: full,
      onclick: () => { per = k; setQuery({ p: k === "1Y" ? "" : k }); route.query.set("p", k); draw(); },
    }, label)));
    modeGroup.append(
      h("button", { type: "button", class: "seg-btn", "data-v": "growth", onclick: () => { mode = "growth"; setQuery({ m: "" }); route.query.delete("m"); draw(); } }, "Рост 100 ₽"),
      h("button", { type: "button", class: "seg-btn", "data-v": "price", onclick: () => { mode = "price"; setQuery({ m: "price" }); route.query.set("m", "price"); draw(); } }, "Цена"));
    setSub("");
    if (p.ohlc?.splitAdjusted === false) notes.push(h("p", { class: "hint" }, "Цены без корректировки на сплиты."));
    put(body, ...notes, h("div", { class: "chart-head" }, perGroup, modeGroup), caption, box);
    draw();
  });

  // Доходность по периодам
  block(root, { id: "periods", title: "Доходность по периодам" }, async (body, live, setSub) => {
    const p = await pPrice;
    const pr = libFn("metrics", "periodReturns");
    const mine = pr(p.ret);
    let bm = null;
    let warn = null;
    if (benchKey !== t) {
      try {
        bm = pr(await pBench);
      } catch (e) {
        warn = notice("warn", `Бенчмарк ${benchKey}: ${humanize(e)}`);
      }
    }
    if (!live()) return;
    setSub(`${withDivsNote(p)} Для периодов больше года — также CAGR (среднегодовая доходность).`);
    const cell = (x) => {
      if (!x || !isNum(x.ret)) return h("td", { class: "num muted" }, "—");
      return h("td", { class: "num", title: x.from ? `с ${fmtDate(x.from)}` : null },
        h("span", { class: signClass(x.ret) }, fmtPct(x.ret, 1)),
        isNum(x.cagr) ? h("small", { class: "cagr" }, `CAGR ${fmtPct(x.cagr, 1)}`) : null);
    };
    put(body, ...[warn].filter(Boolean), tableWrap(h("table", { class: "periods-table" },
      h("caption", { class: "sr-only" }, `Доходность ${t} по периодам`),
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Период"),
        h("th", { scope: "col", class: "num" }, swatch(t), t),
        bm ? h("th", { scope: "col", class: "num" }, swatch(benchKey, true), benchKey) : null)),
      h("tbody", {}, RETURN_PERIODS.map(([k, label]) => h("tr", {}, h("th", { scope: "row" }, label),
        cell(mine?.[k]), bm ? cell(bm[k]) : null))))));
  });

  // Моментум
  block(root, { id: "momentum", title: "Моментум" }, async (body, live, setSub) => {
    const p = await pPrice;
    const m = libFn("metrics", "momentum")(p.close);
    if (!live()) return;
    if (!m || !isNum(m.score)) throw new NoData("Для расчёта нужна история не меньше года.");
    setSub("По цене закрытия. Балл 0–100 — среднее пяти признаков тренда (формула — в методике).");
    const yes = (v, a, b) => h("span", { class: v ? "pos" : "neg" }, v ? a : b);
    put(body,
      h("div", { class: "meter-row" },
        h("div", { class: "meter-value" }, fmtNum(m.score, 0), h("small", {}, " из 100")),
        h("div", { class: "meter", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(m.score)), "aria-label": "Балл моментума" },
          h("span", { style: { width: `${Math.max(0, Math.min(100, m.score))}%` } }))),
      h("ul", { class: "facts-list" },
        h("li", {}, "Цена против SMA 50: ", yes(m.aboveSma50, "выше", "ниже"), ` (SMA 50 = ${fmtPrice(m.sma50, unit)})`),
        h("li", {}, "Цена против SMA 200: ", yes(m.aboveSma200, "выше", "ниже"), ` (SMA 200 = ${fmtPrice(m.sma200, unit)})`),
        h("li", {}, "SMA 50 против SMA 200: ", isNum(m.sma50) && isNum(m.sma200) ? yes(m.sma50 > m.sma200, "выше (восходящий тренд)", "ниже (нисходящий тренд)") : "—"),
        h("li", {}, "До максимума цены закрытия за 52 недели: ", h("b", {}, fmtPct(m.distHigh52, 1)), ` (максимум ${fmtPrice(m.high52, unit)})`),
        h("li", {}, "Моментум 6 мес. (без последнего месяца): ", h("b", { class: signClass(m.mom6) }, fmtPct(m.mom6, 1))),
        h("li", {}, "Моментум 12 мес. (без последнего месяца): ", h("b", { class: signClass(m.mom12) }, fmtPct(m.mom12, 1)))));
  });

  // Помесячная доходность
  block(root, { id: "monthly", title: "Помесячная доходность" }, async (body, live, setSub) => {
    const p = await pPrice;
    const g2 = libFn("metrics", "monthlyGrid")(p.ret);
    if (!live()) return;
    if (!g2 || !g2.years?.length) throw new NoData("Недостаточно истории.");
    setSub(`${withDivsNote(p)} Строки — годы, столбцы — месяцы; внизу — медиана по месяцу.`);
    let lim = 0;
    g2.cells.forEach((row) => row.forEach((v) => { if (isNum(v)) lim = Math.max(lim, Math.abs(v)); }));
    const cell = (v, digits = 1) => (isNum(v)
      ? h("td", { style: heatStyle(v, lim), title: fmtPct(v, 2) }, fmtPct(v, digits))
      : h("td", { class: "muted" }, "—"));
    const order = g2.years.map((y, i) => [y, i]).reverse(); // свежие годы сверху
    put(body, tableWrap(h("table", { class: "heatmap monthly" },
      h("caption", { class: "sr-only" }, `Доходность ${t} по месяцам`),
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Год"), ...MONTHS.map((m) => h("th", { scope: "col" }, m)), h("th", { scope: "col" }, "Год"))),
      h("tbody", {}, order.map(([y, i]) => h("tr", {}, h("th", { scope: "row" }, String(y)),
        ...g2.cells[i].map((v) => cell(v)), h("td", { class: "year-total", title: fmtPct(g2.yearTotal?.[i], 2) }, h("b", { class: signClass(g2.yearTotal?.[i]) }, fmtPct(g2.yearTotal?.[i], 1)))))),
      h("tfoot", {}, h("tr", {}, h("th", { scope: "row" }, "Медиана"),
        ...(g2.medianByMonth || []).map((v) => h("td", { class: signClass(v) }, fmtPct(v, 1))), h("td"))))));
  });

  // Рейтинг
  block(root, { id: "rating", title: "Рейтинг риск/доходность" }, async (body, live, setSub) => {
    const stats = await statsOf();
    if (!live()) return;
    if (!stats || !stats.items) throw new NoData("Рейтинг появится после ночного пересчёта (public/data/symbol_stats.json).");
    const it = stats.items[t];
    const n = it ? stats.classes?.[it.class]?.n : null;
    const src = `Источник: ${stats.source || "ISS MOEX (расчёт ИнвестАналитики)"}, обновлено ${fmtDate(String(stats.updated || "").slice(0, 10))}.`;
    setSub(it
      ? `Перцентиль 0–100 среди ${it.class === "fund" ? "фондов" : "акций"}${n ? ` (${fmtNum(n)} бумаг)` : ""}, торгуемых сейчас; 100 — лучший показатель класса. ` +
        `Рейтинг — по месячным доходностям ${it.tr ? "с реинвестированием дивидендов (T-Invest)" : "по цене"}; цены — месячные свечи ISS, скорректированные биржей на сплиты и консолидации; ` +
        `Rf — средняя ключевая ставка ЦБ за период истории бумаги в окне; поэтому значения могут отличаться от коэффициентов ниже, рассчитанных по дневным данным. ${src}`
      : src);
    if (!it) {
      put(body, h("p", { class: "muted no-data" },
        `${t} нет в рейтинге: в него входят акции и фонды основного режима торгов с историей не менее 36 месяцев.`));
      return;
    }
    const bar = (label, rank, raw, key) => h("div", { class: "wrow" },
      h("span", { class: "wname" }, label),
      h("span", { class: "wbar", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(rank ?? 0)), "aria-label": `${label}: перцентиль` },
        h("span", { style: { width: `${isNum(rank) ? rank : 0}%` } })),
      h("span", { class: "wval", title: `Значение коэффициента (по месячным данным): ${metric(key, raw)}` }, isNum(rank) ? fmtNum(rank, 0) : "—"));
    put(body,
      h("div", { class: "meter-row" },
        h("div", { class: "meter-value" }, fmtNum(it.score, 0), h("small", {}, " из 100")),
        h("div", { class: "meter", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(it.score ?? 0)), "aria-label": "Итоговый балл рейтинга" },
          h("span", { style: { width: `${isNum(it.score) ? it.score : 0}%` } }))),
      h("div", { class: "weights-list rating-list" }, RATING_KEYS.map(([k, label]) => bar(label, it.rank?.[k], it[k], k))),
      h("p", { class: "hint" }, `Коэффициенты рейтинга (Шарп, Сортино, Омега, Кальмар, Мартин) — по месячным доходностям за ${it.months ? `${fmtNum(it.months)} мес.` : "до 10 лет"}: CAGR ${fmtPct(it.cagr, 1)}, ` +
        `волатильность ${fmtPct(it.volatility, 1)}, макс. просадка ${fmtPct(it.max_drawdown, 1)}.`));
  });

  // Относительно бенчмарка
  block(root, { id: "relative", title: `Относительно бенчмарка ${benchKey}` }, async (body, live, setSub) => {
    const R = await pReports;
    if (!live()) return;
    if (benchKey === t) throw new NoData("Бумага совпадает с бенчмарком — выберите другой бенчмарк.");
    if (!R.bench) throw new UserError(`бенчмарк ${benchKey} не загрузился — ${humanize(R.benchError)}`);
    setSub(`${withDivsNote(R.p)} Дневные доходности за общий период ${fmtDate(R.sec.start)} — ${fmtDate(R.sec.end)}.`);
    put(body, h("div", { class: "kpis" }, REL_KEYS.map((k) => kpi(REL_LABELS[k], metric(k, R.sec[k])))));
  });

  // Риск-доходность
  block(root, { id: "riskret", title: "Риск-доходность" }, async (body, live, setSub) => {
    const R = await pReports;
    if (!live()) return;
    const cols = [[t, R.sec, false]];
    if (R.bm) cols.push([benchKey, R.bm, true]);
    setSub(`Rf = ${fmtPct(g.rf, 2)}, доходности ${FREQ_LABELS.D}, период ${fmtDate(R.sec.start)} — ${fmtDate(R.sec.end)}. ${withDivsNote(R.p)}`);
    put(body,
      R.bench ? null : notice("warn", `Бенчмарк ${benchKey}: ${humanize(R.benchError)}`),
      tableWrap(h("table", { class: "metrics-table" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Показатель"),
          ...cols.map(([n, , bench]) => h("th", { scope: "col", class: "num" }, swatch(n, bench), n)))),
        h("tbody", {}, ["cagr", "volatility", "max_drawdown", ...RISK_KEYS].map((k) => h("tr", {},
          h("th", { scope: "row" }, RISK_LABELS[k] || engine.metrics.LABELS_RU?.[k] || k),
          ...cols.map(([, rep]) => h("td", { class: "num" }, metric(k, rep[k])))))))));
  });

  // Хвостовые риски
  block(root, { id: "tail", title: "Хвостовые риски: VaR и CVaR 95 %" }, async (body, live, setSub) => {
    const R = await pReports;
    if (!live()) return;
    setSub("Исторический метод по дневным доходностям: потеря за день, которую превышают 5 % худших дней (VaR), и средняя потеря в этих днях (CVaR).");
    const pair = (n, rep, bench) => h("div", { class: `kpi${bench ? " bench" : ""}`, style: { "--kpi-color": colorOf(n, { bench }) } },
      h("div", { class: "kpi-name" }, n),
      h("div", { class: "kpi-value" }, `${metric("var_95", rep.var_95)}`),
      h("div", { class: "kpi-sub" }, "VaR 95 % · CVaR ", h("b", {}, metric("cvar_95", rep.cvar_95))));
    put(body, h("div", { class: "kpis" }, pair(t, R.sec, false), R.bm ? pair(benchKey, R.bm, true) : null));
  });

  // Комиссия фонда
  if (cls === "fund") {
    block(root, { id: "fund", title: "Комиссия фонда против рынка" }, async (body, live, setSub) => {
      const fi = await libFn("symbol", "loadFundInfo")(t, { base: DATA_BASE });
      if (!live()) return;
      setSub("");
      const src = h("p", { class: "source" }, "Источник: ", h("a", { href: "https://rusetfs.com/", rel: "noopener", target: "_blank" }, "RusETFs (rusetfs.com)"),
        " — комиссии, УК и СЧА фондов; данные обновляются раз в сутки.");
      if (!fi?.info) {
        put(body, h("p", { class: "muted no-data" }, `${t} нет в базе фондов RusETFs.`), src);
        return;
      }
      const f = fi.info;
      const m = fi.market || {};
      const pct = (v) => (isNum(v) ? `${fmtNum(v, 2)} %` : "—");
      const pos = (v) => (isNum(v) && isNum(m.min) && isNum(m.max) && m.max > m.min ? `${((v - m.min) / (m.max - m.min)) * 100}%` : null);
      const scale = isNum(f.commission_pct) && pos(f.commission_pct)
        ? h("div", { class: "ter-scale", role: "img", "aria-label": `Комиссия ${pct(f.commission_pct)}; рынок: от ${pct(m.min)} до ${pct(m.max)}, медиана ${pct(m.median)}` },
          isNum(m.p25) && isNum(m.p75) ? h("span", { class: "ter-iqr", style: { left: pos(m.p25), width: `calc(${pos(m.p75)} - ${pos(m.p25)})` } }) : null,
          isNum(m.median) ? h("span", { class: "ter-med", style: { left: pos(m.median) } }) : null,
          h("span", { class: "ter-dot", style: { left: pos(f.commission_pct) } }))
        : null;
      put(body,
        h("div", { class: "kpis" },
          kpi("Комиссия фонда (TER), год.", pct(f.commission_pct), f.asset_class ? `класс: ${f.asset_class}` : ""),
          kpi("Медиана класса", pct(m.median), m.n ? `по ${fmtNum(m.n)} торгуемым фондам того же класса` : "нет данных рынка"),
          isNum(m.marketMedian) ? kpi("Медиана всех фондов", pct(m.marketMedian)) : null,
          kpi("Управляющая компания", f.issuer || "—"),
          isNum(f.aum_rub) ? kpi("СЧА", fmtBigRub(f.aum_rub)) : null),
        scale,
        scale ? h("div", { class: "ter-legend" }, h("span", {}, pct(m.min)), h("span", {}, "фонды того же класса: межквартильный диапазон и медиана · точка — этот фонд"), h("span", {}, pct(m.max))) : null,
        src);
    });
  }

  // Дивиденды / купоны
  if (cls === "share") {
    block(root, { id: "divs", title: "Дивиденды" }, async (body, live, setSub) => {
      const p = await pPrice;
      const divs = ((await divsOf(t)) || []).filter((d) => !d.cancelled);
      if (!live()) return;
      setSub("Источник: T-Invest API (используется с разрешения Т-Банка). Доходность TTM — выплаты за последние 12 месяцев к текущей цене.");
      if (!divs.length) throw new NoData(`Выплат ${t} в базе нет.`);
      const st = libFn("symbol", "dividendStats")(divs, p.close);
      const { canvas, box } = chartCard(`Дивиденды ${t} по годам`, "short");
      const recent = [...divs].sort((a, b) => String(b.recordDate).localeCompare(String(a.recordDate))).slice(0, 10);
      put(body,
        h("div", { class: "kpis" },
          kpi("Выплаты за 12 мес.", isNum(st.ttmValue) ? `${fmtNum(st.ttmValue, 2)} ₽` : "—"),
          kpi("Доходность TTM", fmtPct(st.ttmYield, 1)),
          kpi("Рост выплат подряд", isNum(st.growthStreak) ? `${fmtNum(st.growthStreak)} ${yearsWord(st.growthStreak)}` : "—"),
          kpi("Выплат в год", isNum(st.payoutsPerYear) ? fmtNum(st.payoutsPerYear, st.payoutsPerYear % 1 ? 1 : 0) : "—")),
        h("h4", { class: "sub-title" }, "Выплаты по годам, ₽ на акцию"),
        box,
        h("h4", { class: "sub-title" }, "Число выплат по месяцам отсечки (за всю историю)"),
        tableWrap(h("table", { class: "month-grid" },
          h("caption", { class: "sr-only" }, "Число дивидендных выплат по месяцам"),
          h("thead", {}, h("tr", {}, MONTHS.map((m) => h("th", { scope: "col", class: "num" }, m)))),
          h("tbody", {}, h("tr", {}, (st.byMonth || []).map((v) => h("td", { class: `num${v ? "" : " muted"}` }, v ? fmtNum(v) : "—")))))),
        st.upcoming?.length ? h("p", { class: "notice notice-info" }, "Объявлено: ",
          st.upcoming.map((d) => `${fmtNum(d.value, 2)} ₽, отсечка ${fmtDate(d.recordDate || d.exDate)}`).join("; "), ".") : null,
        h("h4", { class: "sub-title" }, "Последние выплаты"),
        tableWrap(h("table", {},
          h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Дата отсечки"), h("th", { scope: "col" }, "Последний день с дивидендом"),
            h("th", { scope: "col", class: "num" }, "На акцию"), h("th", { scope: "col", class: "num" }, "Доходность"))),
          h("tbody", {}, recent.map((d) => h("tr", {},
            h("td", {}, fmtDate(d.recordDate)), h("td", {}, fmtDate(d.exDate)),
            h("td", { class: "num" }, `${fmtNum(d.value, 2)} ${d.currency && !/^(RUB|SUR)$/i.test(d.currency) ? d.currency : "₽"}`),
            h("td", { class: "num" }, fmtPct(d.yield, 1))))))));
      const by = st.byYear || [];
      barChart(canvas, { labels: by.map((x) => String(x.year)), values: by.map((x) => x.value), name: t, yTitle: "Выплаты, ₽ на акцию", xTitle: "Год", yFormat: "money" });
    });
  } else if (cls === "bond") {
    block(root, { id: "coupons", title: "Купоны, амортизации и оферты" }, async (body, live, setSub) => {
      const c = await libFn("symbol", "loadCoupons")(t);
      if (!live()) return;
      setSub("Источник: ISS Мосбиржи (bondization). Будущие купоны с неизвестной ставкой показаны по последней известной.");
      if (!c || !c.coupons?.length) throw new NoData("ISS не вернул графика купонов.");
      const now = today();
      const { canvas, box } = chartCard(`Купоны ${t}`, "short");
      const future = c.coupons.filter((x) => x.date >= now).slice(0, 8);
      put(body, box,
        h("div", { class: "grid-2" },
          h("div", {}, h("h4", { class: "sub-title" }, "Ближайшие купоны"),
            future.length ? tableWrap(h("table", {},
              h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Дата"), h("th", { scope: "col", class: "num" }, "Купон, ₽"), h("th", { scope: "col", class: "num" }, "Ставка"))),
              h("tbody", {}, future.map((x) => h("tr", {}, h("td", {}, fmtDate(x.date)), h("td", { class: "num" }, isNum(x.value) ? fmtNum(x.value, 2) : "—"), h("td", { class: "num" }, fmtPct(x.rate, 2)))))))
              : h("p", { class: "muted" }, "Выплат впереди нет.")),
          h("div", {}, h("h4", { class: "sub-title" }, "Амортизации и оферты"),
            (c.amortizations?.length || c.offers?.length) ? h("ul", { class: "facts-list" },
              (c.amortizations || []).map((a) => h("li", {}, `${fmtDate(a.date)} — амортизация ${isNum(a.value) ? `${fmtNum(a.value, 2)} ₽` : ""}`)),
              (c.offers || []).map((o) => h("li", {}, `${fmtDate(o.date)} — оферта${o.type ? ` (${o.type})` : ""}`)))
              : h("p", { class: "muted" }, "Нет."))));
      const muted = c.coupons.map((x, i) => (x.date >= now ? i : -1)).filter((i) => i >= 0);
      barChart(canvas, { labels: c.coupons.map((x) => fmtDate(x.date)), values: c.coupons.map((x) => x.value), name: t,
        yTitle: "Купон, ₽", xTitle: "Дата выплаты", yFormat: "money", muted });
    });
  }

  // Просадки
  block(root, { id: "drawdowns", title: "Просадки" }, async (body, live, setSub) => {
    const p = await pPrice;
    const M = engine.metrics;
    let b = null;
    try {
      if (benchKey !== t) b = await pBench;
    } catch {
      /* без бенчмарка */
    }
    if (!live()) return;
    const top = libFn("metrics", "topDrawdowns")(p.ret, 5) || [];
    const cur = libFn("metrics", "currentDrawdown")(p.ret);
    const avg = libFn("metrics", "avgDrawdown")(p.ret);
    const ulcer = libFn("metrics", "ulcerIndex")(p.ret);
    setSub(`Снижение от предыдущего максимума, ${fmtDate(p.ret.dates[0])} — ${fmtDate(p.ret.dates.at(-1))}. ${withDivsNote(p)}`);
    const { canvas, box } = chartCard(`Просадки ${t}`, "short");
    put(body,
      h("div", { class: "kpis" },
        kpi("Максимальная", fmtPct(M.maxDrawdown(p.ret), 1)),
        kpi("Текущая", fmtPct(cur?.depth, 1), cur && cur.days ? `${fmtNum(cur.days)} дн. от пика ${fmtDate(cur.peak)}` : "на максимуме"),
        kpi("Средняя", fmtPct(avg, 1)),
        kpi("Индекс язвы", fmtPct(ulcer, 2))),
      box,
      h("h4", { class: "sub-title" }, "Пять крупнейших просадок"),
      top.length ? tableWrap(h("table", {},
        h("thead", {}, h("tr", {}, ["Глубина", "Пик", "Дно", "Восстановление", "Дней до дна", "Дней до восстановления"].map((x, i) =>
          h("th", { scope: "col", class: i === 0 || i >= 4 ? "num" : null }, x)))),
        h("tbody", {}, top.map((d) => h("tr", {},
          h("td", { class: "num neg" }, fmtPct(d.depth, 1)), h("td", {}, fmtDate(d.peak)), h("td", {}, fmtDate(d.trough)),
          h("td", {}, d.recovery ? fmtDate(d.recovery) : h("span", { class: "muted" }, "не восстановлена")),
          h("td", { class: "num" }, fmtNum(d.daysToTrough)), h("td", { class: "num" }, d.daysToRecover != null ? fmtNum(d.daysToRecover) : "—"))))))
        : h("p", { class: "muted" }, "Просадок нет."));
    const lines = [{ name: t, ...M.drawdownSeries(p.ret) }];
    if (b) lines.push({ name: benchKey, ...M.drawdownSeries(engine.data.sliceSeries(b, p.ret.dates[0], null)), bench: true });
    lineChart(canvas, { series: lines, yTitle: "Просадка, %", yFormat: "pct" });
  });

  // Волатильность
  block(root, { id: "vol", title: "Волатильность" }, async (body, live, setSub) => {
    const p = await pPrice;
    const M = engine.metrics;
    let b = null;
    try {
      if (benchKey !== t) b = await pBench;
    } catch {
      /* без бенчмарка */
    }
    if (!live()) return;
    const roll = libFn("metrics", "rollingVolatility");
    const o = p.ohlc;
    const from = periodStart(o.dates, "1Y");
    const idx = o.dates.map((d, i) => (d >= from ? i : -1)).filter((i) => i >= 0);
    const pick = (a) => idx.map((i) => a?.[i]);
    const y1 = { dates: pick(o.dates), open: pick(o.open), high: pick(o.high), low: pick(o.low), close: pick(o.close) };
    const close1y = engine.data.dropNaN({ dates: y1.dates, values: y1.close });
    const est = (name, fnName) => {
      try {
        return [name, libFn("metrics", fnName)(y1, 252)];
      } catch (e) {
        return [name, NaN];
      }
    };
    const rows = [
      ["Close-to-close", M.volatility(M.toReturns(close1y), 252)],
      est("Паркинсон", "parkinson"), est("Гарман–Класс", "garmanKlass"),
      est("Роджерс–Сатчелл", "rogersSatchell"), est("Янг–Чжан", "yangZhang"),
    ];
    setSub("Скользящая волатильность — стандартное отклонение дневных доходностей за 21 торговый день, в годовом выражении. " +
      `Оценки по OHLC — за последний год (${fmtDate(y1.dates[0])} — ${fmtDate(y1.dates.at(-1))}).`);
    const { canvas, box } = chartCard(`Скользящая волатильность ${t}`, "short");
    put(body, box,
      h("h4", { class: "sub-title" }, "Годовая волатильность за 1 год"),
      tableWrap(h("table", { class: "vol-table" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Оценка"), h("th", { scope: "col", class: "num" }, "Волатильность, год."))),
        h("tbody", {}, rows.map(([n, v]) => h("tr", {}, h("th", { scope: "row" }, n), h("td", { class: "num" }, fmtPct(v, 1))))))),
      cls === "index" ? h("p", { class: "hint" }, "Для индексов OHLC берутся из истории ISS; в днях без внутридневных данных оценки могут быть занижены.") : null);
    const lines = [{ name: t, ...roll(p.ret, 21, 252) }];
    if (b) lines.push({ name: benchKey, ...roll(engine.data.sliceSeries(b, p.ret.dates[0], null), 21, 252), bench: true });
    lineChart(canvas, { series: lines, yTitle: "Волатильность, % год.", yFormat: "pct" });
  });

  // Облигация
  if (cls === "bond") {
    block(root, { id: "bond", title: "Облигация" }, async (body, live, setSub) => {
      const s = await snapOf(t).catch(() => null);
      if (!live()) return;
      const bd = s?.bond || {};
      setSub("Доходность к погашению (или к оферте, если она назначена), дюрация и НКД — ISS Мосбиржи. Цена облигации — в % от номинала.");
      const facts = [
        ["Доходность", fmtPct(bd.yield, 2)],
        ["Дюрация", isNum(bd.duration) ? `${fmtNum(bd.duration, 2)} г.` : "—"],
        ["НКД", isNum(bd.accruedInt) ? `${fmtNum(bd.accruedInt, 2)} ₽` : "—"],
        ["Купон", isNum(bd.couponValue) ? `${fmtNum(bd.couponValue, 2)} ₽` : "—"],
        ["Ставка купона", isNum(info?.couponPercent) ? `${fmtNum(info.couponPercent, 2)} % год.` : "—"],
        ["Купонный период", info?.couponPeriod ? `${fmtNum(info.couponPeriod)} дн.` : "—"],
        ["Ближайший купон", fmtDate(bd.nextCoupon)],
        ["Номинал", isNum(info?.faceValue) ? `${fmtNum(info.faceValue, 2)} ₽` : "—"],
        ["Погашение", fmtDate(info?.matDate)],
        ["Оферта", info?.offerDate ? fmtDate(info.offerDate) : "нет"],
      ];
      put(body, h("dl", { class: "facts" }, facts.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v)))));
    });
  }

  // Призыв
  const cta = h("section", { class: "card cta", "aria-labelledby": "sym-cta-t" },
    h("div", {},
      h("h3", { class: "card-title", id: "sym-cta-t" }, `Собрать портфель с ${t}`),
      h("p", { class: "card-sub" }, "Бумага попадёт в «Свой портфель» в бэктесте — добавьте другие активы и сравните с классическими стратегиями.")),
    h("button", {
      type: "button", class: "btn btn-primary", onclick: () => {
        backtest.setCustomPortfolio({ [t]: 1 },
          `${t} добавлена в «Свой портфель» с долей 100 %. Добавьте другие активы, задайте доли и нажмите «Запустить бэктест».`);
        go("/tools/backtest");
      },
    }, "Собрать портфель с этой бумагой"));
  root.append(cta);
}

function yearsWord(n) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return "лет";
  if (b === 1) return "год";
  if (b >= 2 && b <= 4) return "года";
  return "лет";
}

