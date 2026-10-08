// Hash-роутер: статический хостинг без настроек сервера. Маршрут — в #/…, параметры
// страницы бумаги — в query внутри hash (#/symbol/SBER?b=IMOEX), состояние инструментов —
// как раньше в ?… (state.js), чтобы старые ссылки продолжали работать.

import * as S from "./state.js";

/**
 * Маршруты из web/CONTRACT.md (этап 1).
 * page — имя модуля страницы (main.js), panel — id секции без «panel-», nav — пункт меню,
 * paramsScope — какие общие параметры показывать: "all" (инструменты) | "rf" (страница бумаги) | null.
 */
export const ROUTES = [
  { re: /^\/$/, page: "home", panel: "home", nav: "home", title: "Главная" },
  { re: /^\/tools$/, page: "tools", panel: "tools", nav: "tools", title: "Инструменты" },
  { re: /^\/tools\/backtest$/, page: "backtest", panel: "backtest", nav: "tools", tool: "backtest", paramsScope: "all", title: "Бэктест портфелей" },
  { re: /^\/tools\/frontier$/, page: "frontier", panel: "frontier", nav: "tools", tool: "frontier", paramsScope: "all", title: "Граница Марковица" },
  { re: /^\/tools\/metrics$/, page: "security", panel: "security", nav: "tools", tool: "metrics", paramsScope: "all", title: "Коэффициенты бумаги" },
  { re: /^\/docs$/, page: "method", panel: "method", nav: "docs", title: "Методика" },
  { re: /^\/symbol\/([^/]+)$/, page: "symbol", panel: "symbol", nav: null, paramsScope: "rf", title: (m) => m[1] },
  { re: /^\/screener\/(stocks|funds|bonds)$/, page: "stub", panel: "stub", nav: "screener", title: "Скринеры" },
  { re: /^\/portfolios\/lazy$/, page: "stub", panel: "stub", nav: "portfolios", title: "Ленивые портфели" },
];

const NOT_FOUND = { page: "stub", panel: "stub", nav: null, title: "Страница не найдена", notFound: true };

/** Старые ссылки ?tab=… → новые маршруты. */
const LEGACY_TABS = { backtest: "/tools/backtest", frontier: "/tools/frontier", security: "/tools/metrics", method: "/docs" };
/** Параметры, по которым понятно, что ссылка вела на бэктест (вкладка по умолчанию не писалась в URL). */
const LEGACY_BACKTEST_KEYS = ["from", "till", "cap", "rebal", "band", "comm", "contrib", "cfreq", "tax", "log", "st", "pf", "pfon", "bt"];
const LEGACY_FRONTIER_KEYS = ["fa", "fx", "ffrom", "ftill", "ffreq", "mu", "wmin", "wmax", "nr", "fr"];
const LEGACY_SECURITY_KEYS = ["sec", "sfrom", "still", "sc"];

/** Разбор hash: «#/symbol/SBER?b=IMOEX» → {path: "/symbol/SBER", query}. */
export function parseHash(hash = location.hash) {
  let raw = hash.replace(/^#/, "");
  const qi = raw.indexOf("?");
  const query = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : "");
  if (qi >= 0) raw = raw.slice(0, qi);
  let path = "/" + raw.replace(/^\/+/, "").replace(/\/+$/, "");
  try {
    path = decodeURIComponent(path);
  } catch {
    /* оставить как есть */
  }
  return { path, query };
}

/** Найти маршрут по пути. */
export function match(path) {
  for (const r of ROUTES) {
    const m = path.match(r.re);
    if (m) {
      const params = {};
      if (r.page === "symbol") params.ticker = m[1].trim().toUpperCase();
      if (r.page === "stub") params.kind = path.slice(1);
      return { ...r, params, title: typeof r.title === "function" ? r.title(m) : r.title };
    }
  }
  return { ...NOT_FOUND, params: { kind: path.slice(1) } };
}

export function current() {
  const { path, query } = parseHash();
  return { path, query, ...match(path) };
}

/** Ссылка на маршрут (относительная: ?параметры инструментов сохраняются). */
export function href(path, query = null) {
  const q = query ? new URLSearchParams(Object.entries(query).filter(([, v]) => v != null && v !== "")).toString() : "";
  return `#${path}${q ? `?${q}` : ""}`;
}

export function symbolHref(ticker) {
  return href(`/symbol/${encodeURIComponent(String(ticker).trim().toUpperCase())}`);
}

/** Перейти на маршрут (новая запись истории → сработает hashchange). */
export function go(path, query = null) {
  const target = href(path, query);
  if (location.hash === target) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = target;
}

/** Обновить query внутри hash без перехода (состояние страницы — ссылкой можно поделиться). */
export function setQuery(obj) {
  const { path, query } = parseHash();
  for (const [k, v] of Object.entries(obj)) {
    if (v == null || v === "") query.delete(k);
    else query.set(k, String(v));
  }
  const q = query.toString();
  history.replaceState(null, "", `${location.pathname}${location.search}#${path}${q ? `?${q}` : ""}`);
}

/**
 * Перенаправить старые ссылки одностраничной версии: ?tab=backtest → #/tools/backtest и т. п.
 * Ссылки без tab, но с параметрами бэктеста (вкладка по умолчанию) → #/tools/backtest.
 */
export function migrateLegacy() {
  const tab = S.take("tab");
  if (location.hash && location.hash !== "#") return;
  let path = tab ? LEGACY_TABS[tab] : null;
  if (!path) {
    if (S.hasAny(LEGACY_BACKTEST_KEYS)) path = "/tools/backtest";
    else if (S.hasAny(LEGACY_FRONTIER_KEYS)) path = "/tools/frontier";
    else if (S.hasAny(LEGACY_SECURITY_KEYS)) path = "/tools/metrics";
  }
  history.replaceState(null, "", `${location.pathname}${location.search}#${path || "/"}`);
}

export function onRoute(cb) {
  window.addEventListener("hashchange", () => cb(current()));
}
