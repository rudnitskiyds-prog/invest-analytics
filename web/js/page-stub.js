// Заглушки разделов этапа 3 (скринеры, ленивые портфели) и страница «не найдено».

import { $, h, put } from "./dom.js";
import { engine, entries } from "./engine.js";
import { fmtPct } from "./format.js";
import { href } from "./router.js";

const STREAMLIT = "версии Streamlit (запуск: streamlit run app.py), страница «Витрина»";

const STUBS = {
  "screener/stocks": {
    title: "Скринер акций",
    lead: "Таблица акций основного режима Мосбиржи (TQBR) с фильтрами, выбором колонок, сортировкой и выгрузкой.",
    will: ["сектор и отрасль (секторные индексы Мосбиржи, T-Invest API)", "доходности за периоды и дивидендная доходность",
      "мультипликаторы (T-Invest API)", "рейтинг риск/доходность, просадки, коэффициенты Шарпа, Сортино, Омеги, Мартина"],
    now: `Скринер акций уже есть в ${STREAMLIT}. В веб-версии каждую бумагу можно открыть на странице бумаги через поиск.`,
  },
  "screener/funds": {
    title: "Скринер фондов",
    lead: "БПИФ, ОПИФ и ЗПИФ на Мосбирже: тип, управляющая компания, СЧА, класс активов, комиссия.",
    will: ["комиссия, УК, СЧА и класс активов (RusETFs)", "доходности за периоды и коэффициенты", "сравнение комиссии с рынком"],
    now: `Скринер фондов с комиссиями RusETFs уже есть в ${STREAMLIT}.`,
  },
  "screener/bonds": {
    title: "Скринер облигаций",
    lead: "ОФЗ и корпоративные облигации (TQOB, TQCB): доходность, дюрация, купон, погашение, оферта, оборот.",
    will: ["доходность к погашению и оферте, дюрация", "купон, график выплат, амортизации", "уровень листинга и оборот, спред к КБД"],
    now: `Скринер облигаций уже есть в ${STREAMLIT}.`,
  },
  "portfolios/lazy": {
    title: "Ленивые портфели",
    lead: "Российские аналоги классических пассивных портфелей на индексах полной доходности и БПИФ.",
    will: ["листинг портфелей: 1Д, YTD, CAGR за 10 лет, рейтинг, просадки, коэффициенты", "новые портфели: «Вечный» на БПИФ, три фонда, ОФЗ-лесенка, «голубые фишки», дивидендные аристократы",
      "страница портфеля: состав, журнал ребалансировок, все блоки страницы бумаги"],
    now: "Шесть классических стратегий уже можно сравнить в бэктесте.",
    strategies: true,
  },
};

export function init() {}

export function onShow(route) {
  const box = $("#stub-body");
  const kind = route.params.kind;
  const s = STUBS[kind];
  if (route.notFound || !s) {
    $("#stub-title").textContent = "Страница не найдена";
    put(box, h("div", { class: "card prose" },
      h("p", {}, `Адреса «#/${kind}» нет. Возможно, ссылка устарела.`),
      h("p", {}, h("a", { href: href("/") }, "На главную"), " · ", h("a", { href: href("/tools") }, "Инструменты"))));
    return;
  }
  $("#stub-title").textContent = s.title;
  const screeners = kind.startsWith("screener/")
    ? h("nav", { class: "tool-nav stub-nav", "aria-label": "Скринеры" },
      [["screener/stocks", "Акции"], ["screener/funds", "Фонды"], ["screener/bonds", "Облигации"]].map(([k, t]) =>
        h("a", { href: href(`/${k}`), "aria-current": k === kind ? "page" : null }, t)))
    : null;
  const parts = [
    h("p", { class: "lead" }, s.lead),
    h("p", {}, h("span", { class: "badge" }, "скоро · этап 3")),
    h("h3", { class: "section-title" }, "Что будет в разделе"),
    h("ul", {}, s.will.map((t) => h("li", {}, t))),
    h("h3", { class: "section-title" }, "Что можно сделать сейчас"),
    h("p", {}, s.now),
    h("p", {}, h("a", { href: href("/tools/backtest") }, "Бэктест портфелей"), " · ",
      h("a", { href: href("/tools/metrics") }, "Коэффициенты бумаги"), " · ",
      h("a", { href: href("/tools") }, "Все инструменты")),
  ];
  if (s.strategies && engine.ok) {
    parts.push(h("h3", { class: "section-title" }, "Стратегии, доступные в бэктесте"),
      h("div", {}, entries(engine.strategies.STRATEGIES).map(([name, st]) => h("div", { class: "strategy" },
        h("div", { class: "f-name" }, name, st.note ? h("small", {}, st.note) : null),
        h("div", { class: "strategy-w" }, entries(st.weights).map(([k, w]) =>
          h("span", { class: "tag" }, h("b", {}, k), ` ${fmtPct(w, 1)}`)))))));
  }
  put(box, ...[screeners].filter(Boolean), h("div", { class: "card prose" }, ...parts));
}

export function onGlobalChange() {}
export function rerender() {}
