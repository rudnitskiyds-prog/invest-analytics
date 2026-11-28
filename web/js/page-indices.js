// Раздел «Индексы»: корзины бумаг отдельно от бумаг. Данные — public/data/indices.json (сборщик, web/CONTRACT.md).
// Расчётов нет: доходности, просадка и волатильность берутся из файла, здесь только форматирование.
import { DATA_BASE } from "./config.js";
import { $, h, notice, put } from "./dom.js";
import { fmtDate, fmtPct } from "./format.js";
import { assetBadge, IDX_SLUG } from "./logo.js";
import { symbolHref } from "./router.js";

const NS = "http://www.w3.org/2000/svg";
const CLASSES = Object.keys(IDX_SLUG);   // порядок групп
const SORTS = { class: "По классам", ret: "По доходности за год" };

let promise = null;
let sort = "class";

/** indices.json (кэш на сессию; ошибку можно повторить). Нет файла — null. */
export function loadIndices() {
  promise ||= fetch(DATA_BASE + "indices.json").then((r) => (r.ok ? r.json() : null)).catch(() => null)
    .then((d) => { if (!Array.isArray(d?.items) || !d.items.length) promise = null; return Array.isArray(d?.items) && d.items.length ? d : null; });
  return promise;
}

/** Линия без заливки по ряду [[дата, значение]] (только масштабирование для рисования). */
export function spark(series, { w = 240, h: hh = 56 } = {}) {
  const v = (series || []).map((p) => p?.[1]).filter(Number.isFinite);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${hh}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "idx-spark");
  svg.setAttribute("aria-hidden", "true");
  if (v.length < 2) return svg;
  const lo = Math.min(...v);
  const span = Math.max(...v) - lo || 1;
  const pts = v.map((y, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(hh - 3 - (y - lo) / span * (hh - 6)).toFixed(1)}`);
  const line = document.createElementNS(NS, "path");
  line.setAttribute("d", `M${pts.join(" L")}`);
  line.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(line);
  return svg;
}

export const signedPct = (x, digits = 1) => (Number.isFinite(x)
  ? h("span", { class: x > 0 ? "pos" : x < 0 ? "neg" : null }, (x > 0 ? "+" : "") + fmtPct(x, digits))
  : h("span", { class: "muted" }, "—"));

const slug = (it) => IDX_SLUG[it.class] || "eq";
const kindLabel = (it) => (it.kind === "total_return" ? "полной доходности" : it.kind === "price" ? "ценовой" : "");

function card(it) {
  const funds = (it.etf || []).filter((f) => f?.secid).slice(0, 3);
  return h("article", { class: "idx-card card", "data-idx": slug(it) },
    h("div", { class: "idx-top" },
      assetBadge(it.key, 36, { kind: "index" }),
      h("div", { class: "idx-names" },
        h("h4", { class: "idx-name" }, h("a", { href: symbolHref(it.key), class: "idx-link" }, it.short || it.name || it.key)),
        h("span", { class: "idx-sub muted" }, [it.key, kindLabel(it)].filter(Boolean).join(" · ")))),
    spark(it.series),
    h("div", { class: "idx-ret" },
      h("span", { class: "idx-ret-val" }, signedPct(it.ret_1y)),
      h("span", { class: "idx-ret-lbl muted" }, "за 1 год")),
    h("p", { class: "idx-cagr muted" },
      h("span", {}, "5 лет: ", signedPct(it.cagr_5y), " в год"),
      h("span", {}, "10 лет: ", signedPct(it.cagr_10y), " в год")),
    h("dl", { class: "idx-risk" },
      h("div", {}, h("dt", { title: "Рассчитано по месячным данным" }, "Макс. просадка", h("small", {}, " (по мес. данным)")), h("dd", {}, Number.isFinite(it.max_drawdown) ? fmtPct(it.max_drawdown, 1) : "—")),
      h("div", {}, h("dt", { title: "Рассчитано по месячным данным" }, "Волатильность", h("small", {}, " (по мес. данным)")), h("dd", {}, Number.isFinite(it.volatility_1y) ? fmtPct(it.volatility_1y, 1) : "—"))),
    it.description ? h("p", { class: "idx-desc" }, it.description) : null,
    funds.length ? h("div", { class: "idx-repeat" },
      h("span", { class: "idx-repeat-lbl" }, "Похожие БПИФ"),
      h("ul", {}, funds.map((f) => h("li", {},
        h("a", { href: symbolHref(f.secid), class: "idx-fund", title: f.name || f.secid },
          assetBadge(f.secid, 18, { kind: "fund" }), h("b", {}, f.secid), f.name ? h("span", { class: "muted" }, f.name) : null)))),
      it.etf_note ? h("p", { class: "idx-note muted" }, it.etf_note) : null) : null);
}

const grid = (items) => h("div", { class: "idx-grid" }, items.map(card));

function fillBody(body, items) {
  if (sort === "ret") {
    const rank = (x) => (Number.isFinite(x.ret_1y) ? x.ret_1y : -Infinity);
    return put(body, grid([...items].sort((a, b) => rank(b) - rank(a))));
  }
  const known = new Set(CLASSES);
  const groups = [...CLASSES, ...new Set(items.map((i) => i.class).filter((c) => !known.has(c)))];
  return put(body, groups.map((c) => {
    const part = items.filter((i) => i.class === c);
    return part.length ? h("section", { class: "idx-group", "data-idx": IDX_SLUG[c] || "eq", "aria-label": c },
      h("h3", { class: "idx-group-title" }, h("i", { "aria-hidden": "true" }), c), grid(part)) : null;
  }));
}

function render(root, data) {
  const body = h("div", { class: "idx-body" });
  const btns = Object.entries(SORTS).map(([k, label]) => h("button", {
    type: "button", class: "idx-sort-btn", "aria-pressed": String(k === sort),
    onclick: () => {   // кнопки не пересоздаём: фокус остаётся на нажатой
      sort = k;
      btns.forEach((b, i) => b.setAttribute("aria-pressed", String(Object.keys(SORTS)[i] === k)));
      fillBody(body, data.items);
    },
  }, label));
  fillBody(body, data.items);
  put(root,
    h("div", { class: "panel-head" },
      h("h2", {}, "Индексы"),
      h("p", { class: "lead" }, "Индекс — это корзина бумаг, а не одна бумага: он показывает, как ведёт себя рынок целиком. Купить индекс напрямую нельзя, но похожие на него фонды (БПИФ) есть."),
      h("p", { class: "idx-meta muted" }, `Данные на ${fmtDate(data.as_of)} · источник: ${data.source || "—"}`)),
    h("div", { class: "idx-sort", role: "group", "aria-label": "Сортировка" }, btns), body);
}

export function init() {}
export async function onShow() {
  const root = $("#indices-root");
  if (!root.children.length) put(root, h("div", { class: "panel-head" }, h("h2", {}, "Индексы")), h("p", { class: "muted", role: "status" }, "Загружаем индексы…"));
  const data = await loadIndices();
  if (!data) {
    put(root, h("div", { class: "panel-head" }, h("h2", {}, "Индексы")),
      notice("info", "Данных по индексам пока нет: файл indices.json собирается ночью из выгрузок Мосбиржи и ЦБ. Загляните завтра."));
    return;
  }
  render(root, data);
}
export function onGlobalChange() {}
export function rerender() {}
export const STATIC = true;
