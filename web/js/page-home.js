// Главная: поиск, популярные бумаги, входы в разделы, лидеры рейтинга (если есть symbol_stats.json).

import { DATA_BASE } from "./config.js";
import { $, h, notice, put, tableWrap } from "./dom.js";
import { engine } from "./engine.js";
import { fmtCoef, fmtDate, fmtNum, fmtPct } from "./format.js";
import { go, href, symbolHref } from "./router.js";
import { attachSearch, classLabel, POPULAR } from "./search.js";
import { isDemo } from "./state.js";

const SECTIONS = [
  { path: "/tools", title: "Инструменты", text: "Бэктест портфелей и пассивных стратегий, граница Марковица, коэффициенты эффективности." },
  { path: "/screener/stocks", title: "Скринеры", text: "Акции, фонды и облигации Мосбиржи с фильтрами по доходности, риску и комиссиям." },
  { path: "/portfolios/lazy", title: "Портфели", text: "«Ленивые» портфели на российских индексах и фондах — состав и история." },
  { path: "/docs", title: "Методика", text: "Формулы коэффициентов, допущения бэктеста и источники данных." },
];

let inited = false;
let statsShown = false;

export function init() {
  if (inited) return;
  inited = true;
  attachSearch($("#home-search"), { onPick: (t) => go(`/symbol/${encodeURIComponent(t)}`) });
  $("#home-popular").replaceChildren(...POPULAR.map((p) => h("li", {},
    h("a", { class: "pop", href: symbolHref(p.secid) },
      h("span", { class: "pop-key" }, p.secid),
      h("span", { class: "pop-name" }, p.name),
      h("span", { class: "pop-cls" }, classLabel(p.cls))))));
  $("#home-sections").replaceChildren(...SECTIONS.map((s) => h("a", { class: "card entry", href: href(s.path) },
    h("span", { class: "entry-title" }, s.title),
    h("span", { class: "entry-text" }, s.text))));
}

export function onShow() {
  init();
  if (!statsShown) showLeaders();
}

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
    const top = (cls) => Object.entries(stats.items)
      .filter(([, v]) => v && (v.class === cls) && Number.isFinite(v.score))
      .sort((a, b) => b[1].score - a[1].score)
      .slice(0, 5);
    const table = (cls, title) => {
      const rows = top(cls);
      return h("section", { class: "card" },
        h("h3", { class: "card-title" }, title),
        rows.length
          ? tableWrap(h("table", {},
            h("thead", {}, h("tr", {},
              h("th", { scope: "col" }, "Бумага"), h("th", { scope: "col", class: "num" }, "Балл"),
              h("th", { scope: "col", class: "num" }, "CAGR"), h("th", { scope: "col", class: "num" }, "Шарп"))),
            h("tbody", {}, rows.map(([k, v]) => h("tr", {},
              h("th", { scope: "row" }, h("a", { href: symbolHref(k), class: "mono" }, k)),
              h("td", { class: "num" }, fmtNum(v.score, 0)),
              h("td", { class: "num" }, fmtPct(v.cagr, 1)),
              h("td", { class: "num" }, fmtCoef(v.sharpe, 2)))))))
          : h("p", { class: "muted" }, "Нет данных"));
    };
    put(box,
      h("h3", { class: "section-title" }, "Лидеры рейтинга риск/доходность"),
      h("p", { class: "hint" }, `Балл 0–100 — средний перцентиль по коэффициентам Шарпа, Сортино, Омеги, Кальмара и Мартина среди бумаг того же класса. ` +
        `Источник: ${stats.source || "ISS MOEX (расчёт ИнвестАналитики)"}, обновлено ${fmtDate(String(stats.updated || "").slice(0, 10))}.`),
      h("div", { class: "grid-2" }, table("share", "Акции — топ-5"), table("fund", "Фонды — топ-5")));
  } catch (e) {
    statsShown = false;
    put(box, notice("warn", "Рейтинг сейчас недоступен (файл symbol_stats.json не загрузился)."));
  }
}

export function onGlobalChange() {}
export function rerender() {}
