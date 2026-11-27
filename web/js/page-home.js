// Главная FinServ (web/CONTRACT.md, «Главная»): герой с поиском, готовые портфели (portfolios.json),
// путь «от идеи до портфеля», диверсификация, сравнение двух бумаг, популярные бумаги и лидеры рейтинга.
// Расчётов нет: только готовые поля файлов сборщика и web/lib.

import { DATA_BASE } from "./config.js";
import { $, h, notice, put } from "./dom.js";
import { engine } from "./engine.js";
import { fmtDate, fmtNum, fmtPct, fmtRub } from "./format.js";
import { assetBadge, loadLogos, nameFor } from "./logo.js";
import { go, href, symbolHref } from "./router.js";
import { attachSearch, classLabel, POPULAR } from "./search.js";
import { isDemo } from "./state.js";
import { setCustomPortfolio } from "./tab-backtest.js";

const STEPS = [
  { path: "/screener/stocks", title: "Изучить", text: "Найдите акции, фонды и облигации по доходности, риску и комиссиям." },
  { path: "/tools/metrics", title: "Сравнить", text: "Сопоставьте бумаги с индексом: доходность, просадки, коэффициенты." },
  { path: "/portfolios/lazy", title: "Выбрать", text: "Возьмите за основу готовый портфель или соберите свой." },
  { path: "/tools/backtest", title: "Проверить на истории", text: "Посмотрите, что стало бы с деньгами с пополнениями и ребалансировкой." },
  { path: "/tools/frontier", title: "Подобрать доли", text: "Граница Марковица подскажет доли с лучшим соотношением риска и доходности." },
];

const PAIRS = [["SBER", "GAZP"], ["TMOS", "EQMX"], ["LKOH", "ROSN"], ["YDEX", "T"], ["GOLD", "LQDT"]];
const HERO_SLUG = "60-40";
const STRIP_MAX = 10;

let inited = false;
let pfShown = false;
let statsShown = false;

export function init() {
  if (inited) return;
  inited = true;
  attachSearch($("#home-search"), { onPick: (t) => go(`/symbol/${encodeURIComponent(t)}`) });

  $("#home-steps").replaceChildren(...STEPS.map((s, i) => h("li", {},
    h("a", { class: "step card", href: href(s.path) },
      h("span", { class: "step-n", "aria-hidden": "true" }, String(i + 1)),
      h("span", { class: "step-title" }, s.title),
      h("span", { class: "step-text" }, s.text)))));

  $("#home-popular").replaceChildren(...POPULAR.map((p) => h("li", {},
    h("a", { class: "asset-row", href: symbolHref(p.secid) },
      assetBadge(p.secid, 36, p.cls),
      h("span", { class: "ar-main" },
        h("span", { class: "ar-key" }, p.secid),
        h("span", { class: "ar-name" }, p.name)),
      h("span", { class: "ar-side", id: `pop-${p.secid}` },
        h("span", { class: "ar-price" }, "—"),
        h("small", { class: "muted" }, classLabel(p.cls)))))));

  initCompare();
}

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
  $("#home-pairs").append(...PAIRS.map(([x, y]) => h("button", {
    type: "button", class: "pair",
    onclick: () => { a.value = x; b.value = y; form.requestSubmit(); },
  }, `${x} и ${y}`)));
}

export function onShow() {
  init();
  if (!pfShown) showPortfolios();
  if (!statsShown) showLeaders();
  if (!pricesShown) showPrices();
}

// ---------------------------------------------------------------- популярные: цена (ISS) и изменение за 6 мес. (symbol_stats)

let pricesShown = false;

function fmtPrice(v, cls) {
  if (!Number.isFinite(v)) return "—";
  if (cls === "index") return fmtNum(v, 2);
  return `${fmtNum(v, v < 10 ? 3 : 2)} ₽`;
}

async function showPrices() {
  const lastPrices = engine.symbol?.fetchLastPrices;
  if (typeof lastPrices !== "function") return;   // lib ещё не загружен — повторим при следующем показе
  pricesShown = true;
  const priced = POPULAR.filter((p) => p.cls !== "bond" && p.cls !== "metal");
  const [stats, prices] = await Promise.all([
    engine.symbol.loadSymbolStats?.({ base: DATA_BASE }).catch(() => null),
    isDemo() ? null : lastPrices(priced.map((p) => p.secid)).catch(() => null),   // в демо сеть не используем
  ]);
  for (const p of POPULAR) {
    const box = $(`#pop-${CSS.escape(p.secid)}`);
    const r6 = stats?.items?.[p.secid]?.ret_6m;
    if (Number.isFinite(r6)) box.querySelector("small").replaceChildren(signed(r6), " за 6 мес., на конец прошлого месяца");
    const q = prices?.[p.secid];
    if (q) box.querySelector(".ar-price").textContent = fmtPrice(q.last ?? q.prevClose, p.cls);
  }
}

// ---------------------------------------------------------------- готовые портфели

/** Мини-график: точки ряда [[дата, стоимость], …] → SVG-линия с заливкой (только масштабирование для рисования). */
function sparkline(series, { w = 240, h: hh = 64, cls = "spark" } = {}) {
  const v = series.map((p) => p[1]).filter(Number.isFinite);
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${hh}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  if (v.length < 2) return svg;
  const lo = Math.min(...v);
  const span = Math.max(...v) - lo || 1;
  const pts = v.map((y, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(hh - 3 - (y - lo) / span * (hh - 6)).toFixed(1)}`);
  const area = document.createElementNS(NS, "path");
  area.setAttribute("d", `M0,${hh} L${pts.join(" L")} L${w},${hh} Z`);
  area.setAttribute("class", "spark-area");
  const line = document.createElementNS(NS, "path");
  line.setAttribute("d", `M${pts.join(" L")}`);
  line.setAttribute("class", "spark-line");
  line.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(area, line);
  return svg;
}

const signed = (x) => h("span", { class: x > 0 ? "pos" : x < 0 ? "neg" : null }, (x > 0 ? "+" : "") + fmtPct(x, 1));

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
    put(strip, notice("warn", "Готовые портфели сейчас недоступны (файл portfolios.json не загрузился)."));
    put(hero, h("p", { class: "muted" }, "Пример недоступен."));
    return;
  }
  const items = Object.entries(data.items || {}).filter(([, it]) => it?.row);
  put(strip, ...items.slice(0, STRIP_MAX).map(([slug, it]) => {
    const row = it.row;
    return h("a", { class: "pf-card card", href: href(`/portfolio/${encodeURIComponent(slug)}`) },
      h("span", { class: "pf-group" }, row.group || ""),
      h("span", { class: "pf-name" }, row.name || slug),
      sparkline(data.series?.[slug] || []),
      // ret_6m появляется после ночного сбора; до того — YTD
      h("span", { class: "pf-stats" },
        Number.isFinite(row.ret_6m)
          ? h("span", {}, h("small", {}, "За 6 месяцев"), signed(row.ret_6m))
          : h("span", {}, h("small", {}, "С начала года"), signed(row.ytd)),
        h("span", {}, h("small", {}, "Среднегодовая, 10 лет"), signed(row.cagr_10y ?? row.cagr_all))));
  }));
  if (items.length > STRIP_MAX) {
    strip.append(h("a", { class: "pf-card pf-more card", href: href("/portfolios/lazy") }, `Ещё ${items.length - STRIP_MAX} →`));
  }
  heroCard(hero, data);
}

function heroCard(box, data) {
  const it = data.items?.[HERO_SLUG] || Object.values(data.items || {})[0];
  const slug = data.items?.[HERO_SLUG] ? HERO_SLUG : Object.keys(data.items || {})[0];
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
      h("span", { class: "hc-sub" }, `Если бы вы вложили ${fmtRub(v0)} в ${d0.slice(0, 4)} году`)),
    h("div", { class: "hc-value" }, fmtRub(v1)),
    h("div", { class: "hc-meta" }, signed(row.cagr_all), h("span", { class: "muted" }, " в год в среднем · на ", fmtDate(d1))),
    sparkline(series, { w: 480, h: 150, cls: "spark spark-hero" }),
    h("div", { class: "hc-foot" },
      h("span", {}, h("small", {}, "Худшая просадка"), h("b", { class: "neg" }, fmtPct(row.max_drawdown, 1))),
      h("span", {}, h("small", {}, "Комиссии фондов"), h("b", {}, row.fee != null ? fmtPct(row.fee, 2) : "—")),
      h("a", { class: "btn btn-sm", href: href(`/portfolio/${encodeURIComponent(slug)}`) }, "Подробнее")));
}

// ---------------------------------------------------------------- лидеры рейтинга

async function showLeaders() {
  const box = $("#home-leaders");
  const head = box.firstElementChild;
  const load = engine.symbol?.loadSymbolStats;
  if (isDemo() || typeof load !== "function") {
    put(box, head, h("p", { class: "muted" }, isDemo()
      ? "В демо-режиме рейтинг не загружается — откройте сайт с живыми данными."
      : "Рейтинг сейчас недоступен."));
    return;
  }
  statsShown = true;
  try {
    const [stats] = await Promise.all([load({ base: DATA_BASE }), loadLogos()]);
    if (!stats?.items) throw new Error("нет данных");
    const top = (cls) => Object.entries(stats.items)
      .filter(([, v]) => v?.class === cls && Number.isFinite(v.score))
      .sort((a, b) => b[1].score - a[1].score)
      .slice(0, 5);
    const list = (cls) => h("ol", { class: "asset-list leaders" }, top(cls).map(([k, v]) => h("li", {},
      h("a", { class: "asset-row", href: symbolHref(k) },
        assetBadge(k, 36, cls),
        h("span", { class: "ar-main" },
          h("span", { class: "ar-key" }, k, " ", h("span", { class: "ar-title" }, v.name || nameFor(k) || "")),
          h("span", { class: "ar-name" }, `CAGR ${fmtPct(v.cagr, 1)} · Шарп ${fmtNum(v.sharpe, 2)}`)),
        h("span", { class: "ar-score", title: "Балл 0–100" }, fmtNum(v.score, 0))))));
    const lists = { share: list("share"), fund: list("fund") };
    const body = h("div", {}, lists.share);
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Класс бумаг" });
    for (const [cls, label] of [["share", "Акции"], ["fund", "Фонды"]]) {
      seg.append(h("button", {
        type: "button", class: "seg-btn", "aria-pressed": cls === "share" ? "true" : "false",
        onclick: (e) => {
          for (const b of seg.children) b.setAttribute("aria-pressed", b === e.currentTarget ? "true" : "false");
          body.replaceChildren(lists[cls]);
        },
      }, label));
    }
    head.querySelector(".seg")?.remove();
    head.append(seg);
    put(box, head, body,
      h("p", { class: "source" }, `Балл 0–100 — средний перцентиль коэффициентов Шарпа, Сортино, Омеги, Кальмара и Мартина среди бумаг класса. ` +
        `Источник: ${stats.source || "ISS MOEX (расчёт FinServ)"}, обновлено ${fmtDate(String(stats.updated || "").slice(0, 10))}.`));
  } catch {
    statsShown = false;
    put(box, head, notice("warn", "Рейтинг сейчас недоступен (файл symbol_stats.json не загрузился)."));
  }
}

export function onGlobalChange() {}
export function rerender() {}
