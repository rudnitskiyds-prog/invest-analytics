// Точка входа: шапка, вкладки, общие параметры, подключение расчётных модулей.

import { applyChartDefaults, registerEntities } from "./charts.js";
import { $, $$, h, notice } from "./dom.js";
import { catalogList, engine, entries, loadEngine } from "./engine.js";
import { bindNumber } from "./fields.js";
import * as S from "./state.js";
import * as backtest from "./tab-backtest.js";
import * as frontier from "./tab-frontier.js";
import * as method from "./tab-method.js";
import * as security from "./tab-security.js";

const TABS = { backtest, frontier, security, method };
const BENCH_KEYS = ["MCFTR", "IMOEX", "MEBCTR", "RGBITR", "RUONIA", "GOLD_CBR"];
let ready = false;

function showTab(name, { focus = false } = {}) {
  if (!TABS[name]) name = "backtest";
  for (const b of $$(".tab")) {
    const on = b.dataset.tab === name;
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
  }
  for (const p of $$(".panel")) p.hidden = p.id !== `panel-${name}`;
  S.set({ tab: name });
  if (ready) TABS[name].onShow();
}

function setupTabs() {
  const tabs = $$(".tab");
  tabs.forEach((b, i) => {
    b.addEventListener("click", () => showTab(b.dataset.tab));
    b.addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        showTab(tabs[e.key === "Home" ? 0 : tabs.length - 1].dataset.tab, { focus: true });
      } else if (d) {
        e.preventDefault();
        showTab(tabs[(i + d + tabs.length) % tabs.length].dataset.tab, { focus: true });
      }
    });
  });
}

function activeTab() {
  return TABS[S.get("tab")] || backtest;
}

function setupGlobals() {
  bindNumber("g-rf", "rf", { min: -10, max: 100, onChange: () => activeTab().onGlobalChange() });
  const bench = $("#g-bench");
  const labels = engine.ok ? Object.fromEntries(entries(engine.strategies.BENCHMARKS)) : {};
  const keys = [...new Set([...BENCH_KEYS, S.get("bench")])];
  bench.replaceChildren(...keys.map((k) => h("option", { value: k }, labels[k] ? `${k} — ${labels[k]}` : k)));
  bench.value = S.get("bench");
  bench.addEventListener("change", () => {
    S.set({ bench: bench.value });
    activeTab().onGlobalChange();
  });
  const freq = $("#g-freq");
  freq.value = S.get("freq");
  if (S.isDemo()) {
    for (const o of freq.options) if (o.value !== "M") { o.disabled = true; o.textContent += " — нет в демо"; }
    freq.value = "M";
  }
  freq.addEventListener("change", () => {
    S.set({ freq: freq.value });
    activeTab().onGlobalChange();
  });
}

async function start() {
  setupTabs();
  showTab(S.get("tab"));

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
    showTab("backtest");
    window.scrollTo({ top: $("#bt-pf-table").getBoundingClientRect().top + window.scrollY - 80, behavior: "smooth" });
  };
  for (const [name, mod] of Object.entries(TABS)) {
    try {
      mod.init({ toBacktest });
    } catch (e) {
      console.warn(e);
      $(`#panel-${name} .out, #panel-${name} #method-dynamic`)?.replaceChildren(
        notice("error", `Раздел не инициализирован: ${e.message}`));
    }
  }
  ready = true;
  activeTab().onShow();

  // смена темы: графики перерисовываются сами, тепловую карту и подписи — перестроить
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => activeTab().rerender());
}

start().catch((e) => {
  console.error(e);
  document.getElementById("engine-banner")?.append(notice("error", `Ошибка запуска страницы: ${e.message}`));
});
