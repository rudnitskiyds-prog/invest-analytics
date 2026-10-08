// Каталог инструментов (docs/STRUCTURE.md, п. 3): готовые — ссылки, остальные — «скоро» с этапом.

import { $, h } from "./dom.js";
import { href, symbolHref } from "./router.js";

const BT = "/tools/backtest";
const FR = "/tools/frontier";
const MT = "/tools/metrics";
const SYM = "symbol"; // есть на странице бумаги

/** [название, ссылка | SYM | null, этап (если ещё нет)] */
const GROUPS = [
  { title: "Анализ эффективности", items: [
    ["Анализ портфеля (полный отчёт)", null, 2],
    ["Динамика портфеля (бэктест с пополнениями)", BT],
    ["Коэффициент Шарпа", MT], ["Коэффициент Мартина", SYM], ["Коэффициент Трейнора", MT],
    ["Коэффициент Сортино", MT], ["Коэффициент Омега", MT], ["Коэффициент Кальмара", MT],
    ["Коэффициент Саммерса (Омега относительно рынка)", null, 2], ["Альфа Дженсена", MT],
  ] },
  { title: "Риски", items: [
    ["Просадки", MT], ["CVaR", MT], ["Индекс язвы", SYM], ["VaR", MT],
    ["Волатильность close-to-close", MT], ["Волатильность Паркинсона", SYM], ["Волатильность Гармана–Класса", SYM],
    ["Волатильность Роджерса–Сатчелла", SYM], ["Волатильность Янга–Чжана", SYM], ["Бета", MT],
  ] },
  { title: "Оптимизация", items: [
    ["Среднее–дисперсия: граница, макс. Шарп, мин. дисперсия", FR],
    ["Защита от обвалов (мин. CVaR)", null, 2], ["Сбалансированный рост", null, 2],
    ["Максимизация роста (макс. геометрическая доходность)", null, 2], ["Паритет риска", null, 2],
    ["HRP", null, 2], ["HERC", null, 2], ["Максимальная диверсификация", null, 2],
    ["Бэктест с переоптимизацией по скользящему окну (walk-forward)", null, 2],
  ] },
  { title: "Диверсификация и сравнение", items: [
    ["Анализ диверсификации", null, 2], ["Корреляции активов (матрица)", null, 2],
    ["Сравнение бумаг", null, 2], ["Сравнение 2–10 портфелей", BT],
  ] },
];

let rendered = false;

export function init() {}

export function onShow() {
  if (rendered) return;
  rendered = true;
  $("#tools-groups").replaceChildren(...GROUPS.map((g) => h("section", { class: "card tool-group", "aria-label": g.title },
    h("h3", { class: "card-title" }, g.title),
    h("ul", { class: "tool-list" }, g.items.map(([name, to, stage]) => {
      if (to === SYM) {
        return h("li", {}, h("a", { href: symbolHref("SBER") }, name),
          h("span", { class: "badge badge-ok" }, "на странице бумаги"));
      }
      if (to) return h("li", {}, h("a", { href: href(to) }, name), h("span", { class: "badge badge-ok" }, "готово"));
      return h("li", { class: "soon" }, h("span", {}, name), h("span", { class: "badge" }, `скоро · этап ${stage}`));
    })))));
}

export function onGlobalChange() {}
export function rerender() {}
