// Поиск тикера: поле-combobox с подсказками (web/lib/symbol.js → searchSecurities).
// В демо-режиме и при недоступности ISS подсказки — из статичного списка популярных бумаг.

import { h, put } from "./dom.js";
import { engine } from "./engine.js";
import { isDemo } from "./state.js";

/** Популярные бумаги (статичный список для главной и офлайн-подсказок). */
export const POPULAR = [
  { secid: "SBER", name: "Сбербанк", cls: "share" },
  { secid: "GAZP", name: "Газпром", cls: "share" },
  { secid: "LKOH", name: "ЛУКОЙЛ", cls: "share" },
  { secid: "YDEX", name: "Яндекс", cls: "share" },
  { secid: "T", name: "Т-Технологии", cls: "share" },
  { secid: "MOEX", name: "Московская биржа", cls: "share" },
  { secid: "LQDT", name: "Фонд денежного рынка", cls: "fund" },
  { secid: "TMOS", name: "Фонд на индекс Мосбиржи", cls: "fund" },
  { secid: "EQMX", name: "Фонд на индекс Мосбиржи", cls: "fund" },
  { secid: "GOLD", name: "Фонд на золото", cls: "fund" },
  { secid: "SU26238RMFS4", name: "ОФЗ 26238", cls: "bond" },
  { secid: "IMOEX", name: "Индекс Мосбиржи", cls: "index" },
  { secid: "MCFTR", name: "Индекс Мосбиржи полной доходности", cls: "index" },
];

const CLASS_RU = { share: "Акция", fund: "Фонд", bond: "Облигация", index: "Индекс", metal: "Металл", currency: "Валюта" };
export const classLabel = (cls) => engine.symbol?.SYMBOL_CLASSES?.[cls] ?? CLASS_RU[cls] ?? "";

function localSuggest(q) {
  const u = q.toUpperCase();
  const low = q.toLowerCase();
  return POPULAR.filter((p) => p.secid.startsWith(u) || p.name.toLowerCase().includes(low)).slice(0, 8);
}

let uid = 0;

/**
 * Превратить <input> в поле поиска с подсказками.
 * onPick(secid) — выбор подсказки или Enter (тогда — введённый текст в верхнем регистре).
 */
export function attachSearch(input, { onPick, limit = 8 } = {}) {
  const id = `search-list-${++uid}`;
  const list = h("ul", { class: "search-list", id, role: "listbox", "aria-label": "Подсказки" });
  list.hidden = true;
  const status = h("span", { class: "sr-only", role: "status", "aria-live": "polite" });
  input.after(list, status);
  input.parentElement.classList.add("search-box");
  Object.entries({
    role: "combobox", "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": id,
    autocomplete: "off", spellcheck: "false", autocapitalize: "characters",
  }).forEach(([k, v]) => input.setAttribute(k, v));

  let items = [];
  let active = -1;
  let seq = 0;
  let timer = 0;

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };

  const setActive = (i) => {
    active = i;
    [...list.children].forEach((li, j) => li.setAttribute("aria-selected", j === i ? "true" : "false"));
    if (i >= 0 && list.children[i]) {
      input.setAttribute("aria-activedescendant", list.children[i].id);
      list.children[i].scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  };

  const pick = (secid) => {
    seq++; // поздний ответ поиска не должен заново открыть список
    clearTimeout(timer);
    close();
    const v = String(secid || "").trim().toUpperCase();
    if (!v) return;
    input.value = v;
    onPick?.(v);
  };

  const render = (rows, note = "") => {
    items = rows;
    put(list,
      ...rows.map((r, i) => h("li", {
        id: `${id}-o${i}`, role: "option", "aria-selected": "false", class: "search-opt",
        onmousedown: (e) => { e.preventDefault(); pick(r.secid); },
      },
      h("span", { class: "so-key" }, r.secid),
      h("span", { class: "so-name" }, r.shortName || r.name || ""),
      h("span", { class: "so-cls" }, classLabel(r.cls) + (r.isTraded === false ? " · не торгуется" : "")))),
      note ? h("li", { class: "search-note", role: "presentation" }, note) : null,
    );
    const open = rows.length > 0 || !!note;
    list.hidden = !open;
    input.setAttribute("aria-expanded", open ? "true" : "false");
    status.textContent = rows.length ? `Подсказок: ${rows.length}` : note;
    setActive(-1);
  };

  const suggest = async () => {
    const q = input.value.trim();
    const my = ++seq;
    if (q.length < 1) return close();
    const search = engine.symbol?.searchSecurities;
    if (isDemo() || typeof search !== "function" || q.length < 2) {
      const rows = localSuggest(q);
      return render(rows, rows.length ? "" : (isDemo() ? "Демо-режим: поиск по ISS недоступен, нажмите Enter для перехода" : ""));
    }
    try {
      const rows = await search(q, { limit });
      if (my !== seq) return;
      render(rows || [], rows?.length ? "" : "Ничего не найдено — Enter откроет страницу по введённому тикеру");
    } catch {
      if (my !== seq) return;
      const rows = localSuggest(q);
      render(rows, "Поиск ISS недоступен — показаны популярные бумаги");
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(suggest, 250);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) { suggest(); return; }
      if (!items.length) return;
      const d = e.key === "ArrowDown" ? 1 : -1;
      setActive((active + d + items.length) % items.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      clearTimeout(timer);
      pick(active >= 0 && items[active] ? items[active].secid : input.value);
    } else if (e.key === "Escape") {
      if (!list.hidden) { e.preventDefault(); close(); }
    }
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
  return { close };
}
