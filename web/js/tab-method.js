// Вкладка «Методика»: формулы — статично в index.html; здесь каталог, стратегии и источники данных.

import { DATA_BASE } from "./config.js";
import { $, h, notice, tableWrap } from "./dom.js";
import { catalogList, engine, entries } from "./engine.js";
import { fmtDate, fmtDateTime, fmtPct } from "./format.js";

const SOURCE_RU = { iss: "ISS Мосбиржи", cbr: "Банк России", chain: "ISS Мосбиржи (склейка)", iss_tr: "ISS Мосбиржи" };
const CBR_FILES = ["cbr_gold", "ruonia_index", "ruonia_rate", "key_rate"];

let rendered = false;

export function init() {}

export function onShow() {
  if (rendered) return;
  rendered = true;
  const box = $("#method-dynamic");
  if (!engine.ok) {
    box.replaceChildren(notice("error", "Каталог активов и составы стратегий недоступны: модули расчёта не загрузились."));
    return;
  }
  // каталог
  const cat = catalogList();
  box.append(
    h("h3", { class: "section-title" }, "Каталог базовых активов"),
    h("div", { class: "card" }, tableWrap(h("table", {},
      h("thead", {}, h("tr", {}, ...["Ключ", "Название", "Класс", "Фонд-аналог", "Источник"].map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", {}, cat.map((a) => h("tr", {},
        h("td", { class: "mono" }, a.key), h("td", {}, a.name), h("td", {}, a.assetClass ?? ""),
        h("td", {}, a.etf ?? ""), h("td", {}, SOURCE_RU[a.source] ?? a.source ?? ""))))))),
  );
  // стратегии
  const { STRATEGIES } = engine.strategies;
  box.append(
    h("h3", { class: "section-title" }, "Преднастроенные стратегии"),
    h("div", { class: "card" }, entries(STRATEGIES).map(([name, s]) => h("div", { class: "strategy" },
      h("div", { class: "f-name" }, name, s.note ? h("small", {}, s.note) : null),
      h("div", { class: "strategy-w" }, entries(s.weights).map(([k, w]) =>
        h("span", { class: "tag" }, h("b", {}, k), ` ${fmtPct(w, 1)}`)))))),
  );
  // источники
  const tbody = h("tbody", {}, CBR_FILES.map((id) => h("tr", { "data-id": id },
    h("td", { class: "mono" }, id), h("td", { colspan: "3", class: "muted" }, "Загрузка…"))));
  box.append(
    h("h3", { class: "section-title" }, "Источники данных"),
    h("div", { class: "card prose" },
      h("ul", {},
        h("li", {}, h("b", {}, "ISS Московской биржи"), " — индексы (история закрытий), акции, паи и биржевое золото (дневные свечи основного режима). Запросы идут напрямую из браузера, данные с задержкой до 15 минут."),
        h("li", {}, h("b", {}, "Банк России"), " — учётная цена золота, RUONIA и ключевая ставка. У сайта ЦБ нет доступа из браузера (CORS), поэтому ряды ежедневно собираются сборщиком в файлы JSON."),
      ),
      tableWrap(h("table", {},
        h("thead", {}, h("tr", {}, ...["Файл", "Ряд", "Данные по", "Обновлено"].map((t) => h("th", { scope: "col" }, t)))),
        tbody))),
  );
  for (const id of CBR_FILES) {
    engine.data.loadCbrFile(id, { base: DATA_BASE })
      .then(({ meta }) => {
        $(`tr[data-id="${id}"]`, tbody).replaceChildren(
          h("td", { class: "mono" }, id), h("td", {}, `${meta.title ?? ""}${meta.unit ? `, ${meta.unit}` : ""}`),
          h("td", {}, fmtDate(meta.last)), h("td", {}, fmtDateTime(meta.updated)));
      })
      .catch(() => {
        $(`tr[data-id="${id}"]`, tbody).replaceChildren(
          h("td", { class: "mono" }, id), h("td", { colspan: "3", class: "muted" }, "Файл недоступен"));
      });
  }
}

export function onGlobalChange() {}
export function rerender() {}
