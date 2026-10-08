// Точка входа: шапка с навигацией и поиском, hash-роутер, общие параметры, страницы.

import { applyChartDefaults, registerEntities } from "./charts.js";
import { $, $$, h, notice } from "./dom.js";
import { catalogList, engine, entries, loadEngine } from "./engine.js";
import { bindNumber } from "./fields.js";
import * as home from "./page-home.js";
import * as stub from "./page-stub.js";
import * as symbol from "./page-symbol.js";
import * as tools from "./page-tools.js";
import * as R from "./router.js";
import { attachSearch } from "./search.js";
import * as S from "./state.js";
import * as backtest from "./tab-backtest.js";
import * as frontier from "./tab-frontier.js";
import * as method from "./tab-method.js";
import * as security from "./tab-security.js";

const PAGES = { home, tools, backtest, frontier, security, method, symbol, stub };
const BENCH_KEYS = ["MCFTR", "IMOEX", "MEBCTR", "RGBITR", "RUONIA", "GOLD_CBR"];
let ready = false;
let active = null;     // текущий маршрут
let firstRoute = true;

function activePage() {
  return PAGES[active?.page] || home;
}

function show(route) {
  active = route;
  for (const p of $$(".panel")) p.hidden = p.id !== `panel-${route.panel}`;
  for (const a of $$("[data-nav]")) {
    if (a.dataset.nav === route.nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  for (const a of $$("[data-tool]")) {
    if (a.dataset.tool === route.tool) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  $("#tool-nav").hidden = !route.tool;
  // общие параметры: на инструментах — все, на странице бумаги — только безрисковая ставка
  $("#params-bar").hidden = !route.paramsScope;
  for (const el of $$("[data-param-scope='tools']")) el.hidden = route.paramsScope !== "all";
  document.title = `${route.title} — ИнвестАналитика`;
  if (S.isDemo()) $("#live-link").href = S.liveHref();

  // страница бумаги сразу показывает демо-сообщение / ошибку тикера, каталог инструментов (STATIC)
  // не зависит от lib; остальное — после загрузки lib
  if (ready || route.page === "symbol" || activePage().STATIC) activePage().onShow(route);

  if (!firstRoute) {
    // переход внутри сайта: наверх и фокус на заголовок страницы (для экранных дикторов)
    window.scrollTo({ top: 0 });
    const head = $(`#panel-${route.panel} h2`);
    if (head) {
      head.setAttribute("tabindex", "-1");
      head.focus({ preventScroll: true });
    }
  }
  firstRoute = false;
}

function setupGlobals() {
  bindNumber("g-rf", "rf", { min: -10, max: 100, onChange: () => activePage().onGlobalChange() });
  const bench = $("#g-bench");
  const labels = engine.ok ? Object.fromEntries(entries(engine.strategies.BENCHMARKS)) : {};
  const keys = [...new Set([...BENCH_KEYS, S.get("bench")])];
  bench.replaceChildren(...keys.map((k) => h("option", { value: k }, labels[k] ? `${k} — ${labels[k]}` : k)));
  bench.value = S.get("bench");
  bench.addEventListener("change", () => {
    S.set({ bench: bench.value });
    activePage().onGlobalChange();
  });
  const freq = $("#g-freq");
  freq.value = S.get("freq");
  if (S.isDemo()) {
    for (const o of freq.options) if (o.value !== "M") { o.disabled = true; o.textContent += " — нет в демо"; }
    freq.value = "M";
  }
  freq.addEventListener("change", () => {
    S.set({ freq: freq.value });
    activePage().onGlobalChange();
  });
}

function setupChrome() {
  // ссылка «К содержимому»: hash занят маршрутом — переводим фокус без смены адреса
  $(".skip-link").addEventListener("click", (e) => {
    e.preventDefault();
    $("#main").focus();
  });
  attachSearch($("#nav-search"), { onPick: (t) => R.go(`/symbol/${encodeURIComponent(t)}`) });
  const toggle = $("#nav-toggle");
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    $("#site-nav").classList.toggle("open", open);
  });
  $("#site-nav").addEventListener("click", (e) => {
    if (e.target.closest("a")) {
      toggle.setAttribute("aria-expanded", "false");
      $("#site-nav").classList.remove("open");
    }
  });
}

async function start() {
  R.migrateLegacy();
  setupChrome();
  show(R.current());
  R.onRoute(show);

  if (S.isDemo()) {
    $("#demo-banner").hidden = false;
    $("#live-link").href = S.liveHref();
  }
  const banner = $("#engine-banner");
  if (!applyChartDefaults()) {
    banner.append(notice("error", "Библиотека графиков не загрузилась (web/vendor/chart.umd.min.js). Таблицы будут доступны, графики — нет. Обновите страницу."));
  }

  await loadEngine();
  if (!engine.ok) {
    banner.append(notice("error",
      "Модули расчёта не загрузились — бэктест, граница и коэффициенты сейчас недоступны. " +
      "Обновите страницу; если ошибка повторяется, сообщите разработчикам. " +
      `Подробности: ${engine.error?.message || engine.error}`));
  } else {
    registerEntities([...Object.keys(engine.strategies.STRATEGIES), backtest.PF_NAME]);
    $("#catalog-list").replaceChildren(...catalogList().map((a) => h("option", { value: a.key }, a.name)));
  }
  setupGlobals();

  const toBacktest = (weights) => {
    backtest.setCustomPortfolio(weights);
    R.go("/tools/backtest");
    requestAnimationFrame(() => {
      window.scrollTo({ top: $("#bt-pf-table").getBoundingClientRect().top + window.scrollY - 80, behavior: "smooth" });
    });
  };
  for (const [name, mod] of Object.entries(PAGES)) {
    try {
      mod.init({ toBacktest });
    } catch (e) {
      console.warn(e);
      $(`#panel-${name} .out, #panel-${name} #method-dynamic`)?.replaceChildren(
        notice("error", `Раздел не инициализирован: ${e.message}`));
    }
  }
  ready = true;
  activePage().onShow(active);

  // смена темы: графики перерисовываются сами, тепловые карты и подписи — перестроить
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => activePage().rerender());
}

start().catch((e) => {
  console.error(e);
  document.getElementById("engine-banner")?.append(notice("error", `Ошибка запуска страницы: ${e.message}`));
});
