// Состояние интерфейса в URL-параметрах: ссылкой можно поделиться.

export const DEFAULTS = {
  rf: "7.86",          // %, годовых
  bench: "MCFTR",
  freq: "M",
  // Бэктест
  from: "2011-01-31",
  till: "2025-12-31",
  cap: "1000000",
  rebal: "A",
  band: "0",
  comm: "0",
  contrib: "0",
  cfreq: "M",
  tax: "0",
  log: "0",
  st: "",             // индексы выбранных стратегий через запятую; "" → по умолчанию
  pf: "",             // свой портфель: «MCFTR:50,RGBITR:30,GOLD_CBR:20»
  pfon: "0",          // 1 — свой портфель участвует в бэктесте
  bt: "0",            // 1 — бэктест был запущен (при открытии ссылки — пересчитать)
  // Граница
  fa: "MCFTR,CORP_CHAIN,RGBITR,GOLD_CBR",
  fx: "",
  ffrom: "2011-01-31",
  ftill: "2025-12-31",
  ffreq: "M",
  mu: "arith",
  wmin: "0",
  wmax: "100",
  nr: "4000",
  fr: "0",
  // Коэффициенты бумаги
  sec: "MCFTR",
  sfrom: "2011-01-31",
  still: "2025-12-31",
  sc: "0",
};

const params = new URLSearchParams(location.search);

export function get(key) {
  return params.has(key) ? params.get(key) : DEFAULTS[key] ?? "";
}

/** Обновить параметры (значения по умолчанию из URL убираются — ссылки короче). */
export function set(obj) {
  for (const [k, v] of Object.entries(obj)) {
    const s = v == null ? "" : String(v);
    if (k in DEFAULTS && s === DEFAULTS[k]) params.delete(k);
    else params.set(k, s);
  }
  write();
}

function write() {
  const q = params.toString();
  // hash (маршрут) сохраняем: состояние инструментов — в ?…, страница — в #/…
  history.replaceState(null, "", (q ? `?${q}` : location.pathname) + location.hash);
}

/** Прочитать и удалить параметр (перенаправление старых ссылок ?tab=…). */
export function take(key) {
  if (!params.has(key)) return null;
  const v = params.get(key);
  params.delete(key);
  write();
  return v;
}

/** Есть ли в URL хотя бы один из параметров. */
export const hasAny = (keys) => keys.some((k) => params.has(k));

export const isDemo = () => params.get("demo") === "1";

/** Ссылка на эту же страницу с демо-режимом (для сообщений об ошибке источника). */
export function demoHref() {
  const p = new URLSearchParams(params);
  p.set("demo", "1");
  return `?${p.toString()}${location.hash}`;
}

export function liveHref() {
  const p = new URLSearchParams(params);
  p.delete("demo");
  const q = p.toString();
  return (q ? `?${q}` : location.pathname) + location.hash;
}
