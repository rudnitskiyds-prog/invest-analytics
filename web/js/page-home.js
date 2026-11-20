// Главная (тёмная тема): поиск бумаги, ставки и золото (файлы сборщика), итоги каталога, разделы,
// портфели за 10 лет и лучшие по рейтингу (web/lib/portfolios.js), лидеры рейтинга бумаг (symbol_stats.json).
// Только настоящие данные: чего нет — блок не показывается.

import { DATA_BASE } from "./config.js";
import { $, h, notice, put } from "./dom.js";
import { engine } from "./engine.js";
import { fmtDate, fmtNum, fmtPct } from "./format.js";
import { portfoliosData, shortName } from "./page-portfolios.js";
import { go, href, symbolHref } from "./router.js";
import { attachSearch, classLabel, POPULAR } from "./search.js";
import { isDemo } from "./state.js";

const isNum = (x) => typeof x === "number" && Number.isFinite(x);

const SECTIONS = [
  { path: "/portfolios", title: "Портфели и стратегии", text: "Ленивые и отраслевые портфели: доходность, просадки, рейтинг и как повторить через фонды.", hot: true },
  { path: "/symbol/SBER", title: "Страница бумаги", text: "Доходность по периодам, просадки, волатильность, дивиденды и купоны. Пример — SBER." },
  { path: "/tools/backtest", title: "Бэктест", text: "Проверка стратегии на истории: ребалансировка, пополнения, комиссии и налог." },
  { path: "/tools/frontier", title: "Граница Марковица", text: "Подбор структуры портфеля: эффективная граница, макс. Шарп, мин. дисперсия." },
  { path: "/docs", title: "Методика", text: "Формулы коэффициентов, безрисковая ставка, допущения бэктеста и источники данных." },
];

/** Ставки и золото: файл сборщика → подпись (данные Банка России и ISS, без пересчёта). */
const RATES = [
  { id: "risk_free", label: "Безрисковая ставка", note: "доходность коротких ОФЗ", kind: "share", gold: true },
  { id: "key_rate", label: "Ключевая ставка Банка России", kind: "share" },
  { id: "ruonia_rate", label: "Ставка RUONIA", note: "денежный рынок", kind: "pct" },
  { id: "cbr_gold", label: "Золото, ₽ за грамм", note: "учётная цена Банка России", kind: "rub" },
];

let inited = false;
let ratesShown = false;
let pfShown = false;
let statsShown = false;
let kpi = {};

export function init() {
  if (inited) return;
  inited = true;
  const input = $("#home-search");
  attachSearch(input, { onPick: (t) => go(`/symbol/${encodeURIComponent(t)}`) });
  $("#home-find").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = input.value.trim().toUpperCase();
    if (v) go(`/symbol/${encodeURIComponent(v)}`);
    else input.focus();
  });
  $("#home-popular").replaceChildren(...POPULAR.map((p) => h("li", {},
    h("a", { class: "pop", href: symbolHref(p.secid), title: `${p.name} — ${classLabel(p.cls)}` },
      h("span", { class: "pop-key" }, p.secid),
      h("span", { class: "pop-name" }, p.name),
      h("span", { class: "pop-cls" }, classLabel(p.cls))))));
  $("#home-sections").replaceChildren(...SECTIONS.map((s, i) => h("a", { class: `tile${s.hot ? " hot" : ""}`, href: href(s.path) },
    h("span", { class: "n" }, String(i + 1).padStart(2, "0")),
    h("b", {}, s.title),
    h("span", {}, s.text))));
}

export function onShow() {
  init();
  if (!ratesShown) showRates();
  if (!pfShown) showPortfolios();
  if (!statsShown) showLeaders();
}

export function onGlobalChange() {}
export function rerender() {}

// ---------------------------------------------------------------- ставки и золото

async function showRates() {
  const box = $("#home-rates");
  const load = engine.data?.loadCbrFile;
  if (typeof load !== "function") {
    put(box, h("h3", {}, "Ставки и золото"), notice("warn", "Модуль данных не загрузился — ставки недоступны."));
    return;
  }
  ratesShown = true;
  const res = await Promise.all(RATES.map(async (r) => {
    try {
      const { meta, series } = await load(r.id, { base: DATA_BASE });
      for (let i = series.values.length - 1; i >= 0; i--) {
        if (isNum(series.values[i])) return { ...r, v: series.values[i], d: series.dates[i], unit: meta?.unit };
      }
    } catch {
      /* файл недоступен — строка не показывается */
    }
    return null;
  }));
  const ok = res.filter(Boolean);
  if (!ok.length) {
    ratesShown = false;
    put(box, h("h3", {}, "Ставки и золото"), notice("warn", "Данные Банка России сейчас недоступны."));
    return;
  }
  const fmt = (r) => {
    if (r.kind === "rub") return `${fmtNum(r.v, 2)} ₽`;
    // ставка RUONIA в файле — в процентах, остальные — в долях
    if (r.kind === "pct" || /%/.test(r.unit || "")) return `${fmtNum(r.v, 2)} %`;
    return fmtPct(r.v, 2);
  };
  const last = ok.map((r) => r.d).sort().at(-1);
  put(box,
    h("h3", {}, "Ставки и золото"),
    h("div", { class: "sub" }, `Банк России и ISS Мосбиржи, последние данные на ${fmtDate(last)}`),
    h("dl", { class: "idx" }, ok.flatMap((r) => [
      h("dt", {}, r.label, h("small", {}, [r.note, r.d !== last ? `на ${fmtDate(r.d)}` : null].filter(Boolean).join(", "))),
      h("dd", { class: r.gold ? "gold" : null }, fmt(r)),
    ])),
    h("p", { class: "find-hint" }, "Безрисковая ставка используется во всех коэффициентах — ", h("a", { href: href("/docs") }, "методика"), "."));
}

// ---------------------------------------------------------------- итоги (только из данных)

function renderKpis() {
  const items = [];
  if (isNum(kpi.portfolios)) items.push([fmtNum(kpi.portfolios, 0), "готовых портфелей: доходность, риск, рейтинг"]);
  if (isNum(kpi.rated)) items.push([fmtNum(kpi.rated, 0), "акций и фондов в ежедневном рейтинге риск/доходность"]);
  const labels = engine.metrics?.LABELS_RU;
  const nMetrics = labels ? Object.keys(labels).length : null;
  if (isNum(nMetrics) && nMetrics > 0) items.push([fmtNum(nMetrics, 0), "показателей доходности и риска в таблице коэффициентов"]);
  if (kpi.since) items.push([`с ${kpi.since.slice(0, 4)}`, "история доходности портфелей"]);
  const sec = $("#home-kpis-sec");
  sec.hidden = !items.length;
  put($("#home-kpis"), items.map(([b, t]) => h("div", { class: "h-kpi" }, h("b", {}, b), h("span", {}, t))));
}

// ---------------------------------------------------------------- портфели

async function showPortfolios() {
  const box = $("#home-pf");
  pfShown = true;
  let lib;
  let data;
  try {
    ({ lib, data } = await portfoliosData());
  } catch (e) {
    pfShown = false;
    console.warn(e);
    put(box, notice("warn", "Данные портфелей сейчас недоступны — обновите страницу позже. ",
      { offerDemo: false }));
    box.querySelector(".notice")?.append(h("a", { href: href("/portfolios"), class: "notice-link" }, "Открыть раздел «Портфели»"));
    return;
  }
  const all = lib.portfolioList(data) || [];
  kpi.portfolios = lib.groupCounts(data)?.all ?? all.length;
  kpi.since = all.map((r) => r.since).filter(Boolean).sort()[0] || null;
  renderKpis();

  const lazyName = (data.groups || []).find((g) => g === "Ленивые портфели") ?? data.groups?.[0];
  const lazy = (lib.portfolioList(data, lazyName) || []).filter((r) => isNum(r.cagr_10y))
    .sort((a, b) => b.cagr_10y - a.cagr_10y);
  const top = all.filter((r) => isNum(r.rank)).slice(0, 5);
  const vmax = Math.max(...lazy.map((r) => r.cagr_10y), 0);

  const bars = lazy.length && vmax > 0 ? h("div", { class: "lpanel" },
    h("h3", {}, "Доходность ", h("i", {}, "в год"), ", %"),
    h("p", { class: "sub" }, `Ленивые портфели, среднегодовая доходность за 10 лет на ${fmtDate(data.last)}`),
    h("div", { class: "bars" }, lazy.map((r, i) => h("a", {
      href: href(`/portfolios/${encodeURIComponent(r.id)}`), class: i === 0 ? "g" : null,
      style: `--v: ${Math.max(18, (Math.max(0, r.cagr_10y) / vmax) * 100).toFixed(1)}%`,
      title: `${r.name}: ${fmtPct(r.cagr_10y, 1)} в год за 10 лет`,
      "aria-label": `${r.name}: ${fmtPct(r.cagr_10y, 1)} в год за 10 лет`,
    }, h("span", { class: "bn" }, shortName(r.name)), h("span", { class: "bv" }, fmtNum(r.cagr_10y * 100, 1))))),
    h("div", { class: "lbl", "aria-hidden": "true" }, lazy.map((r) => h("span", {}, shortName(r.name)))))
    : null;

  const rank = h("div", { class: "glass" },
    h("h3", {}, "Лучшие портфели по рейтингу"),
    h("div", { class: "sub" }, "Рейтинг 0–100 за 5 лет: Шарп, Сортино, Омега, Кальмар, Мартин"),
    top.length ? h("table", { class: "rank-tbl" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Портфель"), h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Шкала")),
        h("th", { scope: "col", class: "num" }, "Балл"))),
      h("tbody", {}, top.map((r) => h("tr", {},
        h("th", { scope: "row", title: r.name }, h("a", { href: href(`/portfolios/${encodeURIComponent(r.id)}`) }, shortName(r.name))),
        h("td", { class: "bar", "aria-hidden": "true" }, h("i", { style: { width: `${Math.max(2, Math.min(100, r.rank))}%` } })),
        h("td", { class: "num" }, fmtNum(r.rank, 0)))))) : h("p", { class: "muted" }, "Рейтинг пока не рассчитан."),
    h("p", { class: "find-hint" }, h("a", { href: href("/portfolios") }, "Все портфели и стратегии →")));

  put(box, h("div", { class: "h-two" }, bars, rank));
  if (!bars) box.firstChild.style.gridTemplateColumns = "minmax(0, 1fr)";
}

// ---------------------------------------------------------------- лидеры рейтинга бумаг

async function showLeaders() {
  const box = $("#home-leaders");
  const load = engine.symbol?.loadSymbolStats;
  if (isDemo() || typeof load !== "function") {
    box.replaceChildren();
    return;
  }
  statsShown = true;
  put(box, h("p", { class: "muted" }, "Загрузка рейтинга…"));
  try {
    const stats = await load({ base: DATA_BASE });
    if (!stats || !stats.items) {
      box.replaceChildren();
      return;
    }
    const items = Object.entries(stats.items).filter(([, v]) => v && Number.isFinite(v.score));
    kpi.rated = items.length;
    renderKpis();
    const top = (cls) => items.filter(([, v]) => v.class === cls).sort((a, b) => b[1].score - a[1].score).slice(0, 5);
    const card = (cls, title) => {
      const rows = top(cls);
      return h("section", { class: "glass" },
        h("h3", {}, title), h("div", { class: "sub" }, "Балл 0–100 и среднегодовая доходность"),
        rows.length
          ? h("table", { class: "rank-tbl" },
            h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Бумага"), h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Шкала")),
              h("th", { scope: "col", class: "num" }, "Балл"), h("th", { scope: "col", class: "num" }, "CAGR"))),
            h("tbody", {}, rows.map(([k, v]) => h("tr", {},
              h("th", { scope: "row" }, h("a", { href: symbolHref(k) }, k)),
              h("td", { class: "bar", "aria-hidden": "true" }, h("i", { style: { width: `${Math.max(2, Math.min(100, v.score))}%` } })),
              h("td", { class: "num" }, fmtNum(v.score, 0)),
              h("td", { class: "num dim" }, fmtPct(v.cagr, 1))))))
          : h("p", { class: "muted" }, "Нет данных"));
    };
    put(box,
      h("h3", { class: "h-title" }, "Лидеры рейтинга риск/доходность"),
      h("p", { class: "h-hint" }, "Балл 0–100 — средний перцентиль по коэффициентам Шарпа, Сортино, Омеги, Кальмара и Мартина среди бумаг того же класса. " +
        `Данные ISS Мосбиржи и T-Invest API, обновлено ${fmtDate(String(stats.updated || "").slice(0, 10))}.`),
      h("div", { class: "h-leaders" }, card("share", "Акции — топ-5"), card("fund", "Фонды — топ-5")));
  } catch (e) {
    statsShown = false;
    put(box, notice("warn", "Рейтинг бумаг сейчас недоступен (файл symbol_stats.json не загрузился)."));
  }
}
