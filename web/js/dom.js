// Небольшие помощники для DOM: создание элементов, сообщения, индикатор загрузки, выгрузка CSV.

import { UserError } from "./engine.js";
import { demoHref, isDemo } from "./state.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** h("div", {class: "x", onclick: fn}, child1, "текст", …) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Сообщение: kind = info | warn | error | ok. */
export function notice(kind, text, { offerDemo = false } = {}) {
  const box = h("div", { class: `notice notice-${kind}`, role: kind === "error" ? "alert" : "status" });
  box.append(h("span", { class: "notice-text" }, text));
  if (offerDemo && !isDemo()) {
    box.append(
      " ",
      h("a", { href: demoHref(), class: "notice-link" }, "Открыть демо-режим на исторических данных"),
    );
  }
  return box;
}

/** Показать ошибку в контейнере (без трейсбека). */
export function errorNotice(e) {
  if (e instanceof UserError) return notice("error", e.message, { offerDemo: e.offerDemo });
  console.warn(e); // для разработчика — в консоль как предупреждение
  return notice("error", `Не удалось выполнить расчёт: ${e?.message || e}`, { offerDemo: !isDemo() });
}

// ---------------------------------------------------------------- индикатор загрузки

let busyCount = 0;

export async function withBusy(button, statusEl, fn) {
  busyCount++;
  document.body.classList.add("is-busy");
  const label = button?.textContent;
  if (button) {
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
  }
  try {
    return await fn((msg) => {
      if (statusEl) statusEl.textContent = msg || "";
    });
  } finally {
    if (button) {
      button.disabled = false;
      button.classList.remove("is-loading");
      button.removeAttribute("aria-busy");
      button.textContent = label;
    }
    if (statusEl) statusEl.textContent = "";
    if (--busyCount === 0) document.body.classList.remove("is-busy");
  }
}

/** Дать браузеру отрисовать индикатор перед тяжёлым синхронным расчётом. */
export const nextFrame = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));

// ---------------------------------------------------------------- CSV

export function downloadCsv(filename, rows) {
  const text = rows.map((r) => r.map(csvCell).join(";")).join("\r\n");
  const blob = new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" });
  const a = h("a", { href: URL.createObjectURL(blob), download: filename });
  document.body.append(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 0);
}

function csvCell(v) {
  const s = v == null ? "" : String(v);
  return /[;"\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Таблица в обёртке с собственной прокруткой (страница не скроллится по горизонтали). */
export function tableWrap(table, { maxHeight } = {}) {
  return h("div", { class: "table-wrap", style: maxHeight ? { maxHeight } : null, tabindex: "0" }, table);
}
