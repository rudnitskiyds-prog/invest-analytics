// Раздел «Портфели и стратегии»: список (#/portfolios) и страница портфеля (#/portfolios/<id>).
// Данные и расчёты — web/lib/portfolios.js (владелец — api, файл public/data/portfolios.json от сборщика).
// Здесь ничего не считается: только выбор полей, форматирование и рисование (SVG).

import { DATA_BASE } from "./config.js";
import { $, h, notice, put, tableWrap } from "./dom.js";
import { loadPortfoliosLib } from "./engine.js";
import { fmtDate, fmtNum, fmtPct, fmtRub } from "./format.js";
import { go, href, setQuery, symbolHref } from "./router.js";

// ---------------------------------------------------------------- данные (кэш на сессию)

let memo = null;

/** {lib, data} — модуль и содержимое portfolios.json; при ошибке следующий заход повторит загрузку. */
export function portfoliosData() {
  memo ??= (async () => {
    const lib = await loadPortfoliosLib();
    const data = await lib.loadPortfolios({ base: DATA_BASE });
    if (!data || !data.items) throw new Error("файл portfolios.json пуст или имеет неожиданный формат");
    return { lib, data };
  })().catch((e) => {
    memo = null;
    throw e;
  });
  return memo;
}

// ---------------------------------------------------------------- подписи и форматирование

const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const GROUP_KEYS = { "Ленивые портфели": "lazy", "Тематические": "theme" };
const GROUP_ONE = { "Ленивые портфели": "Ленивый", "Тематические": "Тематический" };

export const groupKey = (data, name) => GROUP_KEYS[name] ?? `g${Math.max(0, (data.groups || []).indexOf(name))}`;
const groupByKey = (data, key) => (data.groups || []).find((g) => groupKey(data, g) === key) ?? null;
const groupOne = (name) => GROUP_ONE[name] ?? name ?? "";

/** «Всепогодный портфель (Далио)» → «Всепогодный портфель» — для коротких подписей на графиках. */
export const shortName = (name) => String(name ?? "").replace(/\s*\([^)]*\)\s*$/, "") || String(name ?? "");

/** Доля с знаком: «+1,8 %», «−0,9 %». */
const fmtSigned = (x, d = 1) => (isNum(x) ? `${x > 0 ? "+" : ""}${fmtPct(x, d)}` : "—");
const signCls = (x) => (isNum(x) ? (x > 0 ? "up" : x < 0 ? "dn" : "") : "na");
/** Доля в составе: «30 %», «12,5 %». */
const fmtShare = (w) => (isNum(w) ? fmtPct(w, Math.abs(w * 100 - Math.round(w * 100)) < 1e-6 ? 0 : 1) : "—");
const cell = (text, cls = "") => h("td", { class: `num ${text === "—" ? "na" : cls}`.trim() }, text);

function plural(n, one, few, many) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}

/** Ряд сборщика [[дата, значение], …] или {dates, values} → {dates, values}. */
function toSeries(x) {
  if (!x) return null;
  if (Array.isArray(x)) {
    const dates = [];
    const values = [];
    for (const p of x) {
      if (Array.isArray(p) && isNum(Number(p[1])) && p[1] !== null) {
        dates.push(String(p[0]));
        values.push(Number(p[1]));
      }
    }
    return dates.length ? { dates, values } : null;
  }
  if (Array.isArray(x.dates) && Array.isArray(x.values) && x.dates.length) return x;
  return null;
}

function loadError(e) {
  console.warn(e);
  return notice("error",
    "Не удалось загрузить данные портфелей. Проверьте подключение и обновите страницу; если ошибка повторяется, " +
    `сообщите разработчикам. Подробности: ${e?.message || e}`);
}

// ---------------------------------------------------------------- SVG

const SVG_NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) el.setAttribute(k, String(v));
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** «Круглый» шаг шкалы: 1, 2, 2.5, 5 × 10^k, не меньше span / maxTicks. */
function niceStep(span, maxTicks = 5) {
  const raw = span / Math.max(1, maxTicks);
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}

// ================================================================ СПИСОК

let listSeq = 0;
let listGroup = null;

export const STATIC = true;
export function init() {}
export function onGlobalChange() {}
export function rerender() {}

export async function onShow(route) {
  const seq = ++listSeq;
  const root = $("#pf-root");
  let g = route.query?.get("g") || null;
  if (route.legacyGroup) {
    // старый адрес #/portfolios/lazy → список с фильтром «Ленивые»
    g = route.legacyGroup;
    history.replaceState(null, "", `${location.pathname}${location.search}#/portfolios?g=${g}`);
  }
  if (!root.dataset.ready) put(root, h("p", { class: "muted" }, "Загрузка портфелей…"));
  let lib;
  let data;
  try {
    ({ lib, data } = await portfoliosData());
  } catch (e) {
    if (seq === listSeq) put(root, loadError(e));
    return;
  }
  if (seq !== listSeq) return;
  listGroup = groupByKey(data, g);
  renderList(root, lib, data);
  root.dataset.ready = "1";
}

function renderList(root, lib, data) {
  const counts = lib.groupCounts(data) || {};
  const all = lib.portfolioList(data) || [];
  const lazyName = groupByKey(data, "lazy");
  const lazy = lazyName ? lib.portfolioList(data, lazyName) || [] : [];

  // карточки-итоги (только то, что есть в данных)
  const since = all.map((r) => r.since).filter(Boolean).sort()[0];
  const best = lazy.map((r) => r.cagr_10y).filter(isNum);
  const bestLazy = best.length ? Math.max(...best) : null;
  const n = counts.all ?? all.length;
  $("#pf-lead").textContent =
    `${fmtNum(n, 0)} ${plural(n, "готовый портфель", "готовых портфеля", "готовых портфелей")} на индексах Мосбиржи и золоте Банка России: ` +
    "от «ленивых» до отраслевых. Доходность — с реинвестированием и ребалансировкой, рейтинг — по пяти годам. " +
    "Для большинства портфелей — инструкция, как собрать его из биржевых фондов.";
  const stats = h("div", { class: "stats" },
    h("div", { class: "stat hot" }, h("b", {}, fmtNum(n, 0)), h("span", {}, plural(n, "портфель в каталоге", "портфеля в каталоге", "портфелей в каталоге"))),
    since ? h("div", { class: "stat" }, h("b", {}, `с ${since.slice(0, 4)}`), h("span", {}, "история доходности")) : null,
    isNum(bestLazy) ? h("div", { class: "stat" }, h("b", {}, fmtPct(bestLazy, 1)), h("span", {}, "лучшая доходность ленивых, в год за 10 лет")) : null,
    data.last ? h("div", { class: "stat" }, h("b", {}, fmtDate(data.last)), h("span", {}, "данные обновлены")) : null);

  // фильтр-пилюли
  const chips = [[null, `Все · ${fmtNum(counts.all ?? all.length, 0)}`],
    ...(data.groups || []).map((gname) => [gname, `${gname} · ${fmtNum(counts[gname] ?? 0, 0)}`])];
  const tableBox = h("div");
  const filter = h("div", { class: "pf-tools", role: "group", "aria-label": "Фильтр по группе" },
    chips.map(([gname, label]) => h("button", {
      type: "button", class: "chip", "aria-pressed": String(gname === listGroup),
      onclick: (e) => {
        listGroup = gname;
        for (const b of filter.querySelectorAll(".chip")) b.setAttribute("aria-pressed", "false");
        e.currentTarget.setAttribute("aria-pressed", "true");
        setQuery({ g: gname ? groupKey(data, gname) : null });
        put(tableBox, listTable(lib, data, gname));
      },
    }, label)));
  put(tableBox, listTable(lib, data, listGroup));

  put(root, stats, filter, tableBox,
    h("p", { class: "note" },
      "Коэффициенты Шарпа, Сортино и Кальмара — за последний год, просадки — за всю историю; безрисковая ставка — доходность коротких ОФЗ. " +
      "Рейтинг 0–100 — среднее перцентилей коэффициентов Шарпа, Сортино, Омеги, Кальмара и Мартина за 5 лет; " +
      "портфели с историей короче 5 лет в рейтинге не участвуют. Не является инвестиционной рекомендацией."),
    h("div", { class: "pf-two" }, scatterCard(lib, data), ratingPanel(all)));
}

function listTable(lib, data, gname) {
  const rows = lib.portfolioList(data, gname) || [];
  if (!rows.length) return notice("info", "В этой группе пока нет портфелей.");
  const th = (t, cls = "num") => h("th", { scope: "col", class: cls }, t);
  const table = h("table", { class: "pf-list" },
    h("thead", {}, h("tr", {},
      h("th", { scope: "col" }, "Портфель"), th("1 день"), th("С начала года"), th("В год, 10 лет"),
      th("Макс. просадка"), th("Текущая"), th("Комиссия"), th("Шарп"), th("Сортино"), th("Кальмар"),
      h("th", { scope: "col", class: "num so", "aria-sort": "descending" }, "Рейтинг"))),
    h("tbody", {}, rows.map((r) => {
      const stale = lib.isStale?.(data, r.id);
      const feeCell = cell(fmtPct(r.fee, 2));
      if (isNum(r.fee) && isNum(r.fee_coverage) && r.fee_coverage < 1) feeCell.title = "Комиссия известна не для всех частей портфеля";
      if (!isNum(r.fee)) feeCell.title = "Нет биржевого фонда с известной комиссией";
      const rk = isNum(r.rank)
        ? h("td", { class: "num rk" }, h("span", { class: "rkbar", "aria-hidden": "true" }, h("i", { style: { width: `${Math.max(0, Math.min(100, r.rank))}%` } })),
          h("span", {}, fmtNum(r.rank, 0)))
        : h("td", { class: "num rk na", title: "История короче 5 лет — в рейтинге не участвует" }, "—");
      return h("tr", {
        "data-id": r.id,
        onclick: (e) => { if (!e.target.closest("a")) go(`/portfolios/${encodeURIComponent(r.id)}`); },
      },
      h("td", { class: "nm" },
        h("a", { href: href(`/portfolios/${encodeURIComponent(r.id)}`) }, r.name),
        h("small", {}, groupOne(r.group)),
        stale ? h("small", { class: "stale" }, `данные на ${fmtDate(r.last_date)}`) : null),
      cell(fmtSigned(r.ret_1d, 2), signCls(r.ret_1d)),
      cell(fmtSigned(r.ytd, 1), signCls(r.ytd)),
      cell(fmtPct(r.cagr_10y, 1), "cagr10"),
      cell(fmtPct(r.max_drawdown, 1), "dn"),
      cell(fmtPct(r.current_drawdown, 1)),
      feeCell,
      cell(fmtNum(r.sharpe, 2)), cell(fmtNum(r.sortino, 2)), cell(fmtNum(r.calmar, 2)),
      rk);
    })));
  return h("div", { class: "card pf-table-card" }, tableWrap(table));
}

// ---------------------------------------------------------------- риск и доходность (SVG)

function scatterCard(lib, data) {
  let pts = [];
  try {
    pts = (lib.riskReturnPoints(data, "10Y") || []).filter((p) => isNum(p.ret) && isNum(p.volatility));
  } catch (e) {
    console.warn(e);
  }
  const card = h("section", { class: "card", "aria-labelledby": "pf-rr-title" },
    h("h3", { class: "card-title", id: "pf-rr-title" }, "Риск и доходность"),
    h("p", { class: "card-sub" }, "За 10 лет: по горизонтали — волатильность, по вертикали — среднегодовая доходность. Нажмите на точку, чтобы открыть портфель."));
  if (!pts.length) {
    card.append(h("p", { class: "muted" }, "Нет портфелей с историей 10 лет."));
    return card;
  }
  card.append(scatterSvg(data, pts),
    h("div", { class: "leg" },
      h("span", {}, h("i", { style: { background: "#0A0F70" } }), "Ленивые"),
      h("span", {}, h("i", { style: { background: "#9C9EC6" } }), "Тематические"),
      h("span", {}, h("i", { style: { background: "#C7A253" } }), "Под курсором")));
  return card;
}

function scatterSvg(data, pts) {
  const W = 620;
  const H = 360;
  const L = 56;
  const R = 602;
  const T = 18;
  const B = 312;
  const xs = pts.map((p) => p.volatility);
  const ys = pts.map((p) => p.ret);
  const xStep = niceStep(Math.max(...xs), 7);
  const xMax = Math.ceil(Math.max(...xs) / xStep) * xStep || xStep;
  const yLo = Math.min(0, ...ys);
  const yHi = Math.max(0, ...ys);
  const yStep = niceStep(yHi - yLo, 4);
  const yMin = Math.floor(yLo / yStep) * yStep;
  const yMax = Math.ceil(yHi / yStep) * yStep || yStep;
  const X = (v) => L + (v / xMax) * (R - L);
  const Y = (v) => B - ((v - yMin) / (yMax - yMin)) * (B - T);
  const pctTick = (v) => `${fmtNum(v * 100, Number.isInteger(Math.round(v * 1e6) / 1e4) ? 0 : 1)} %`;

  const grid = [];
  for (let v = yMin; v <= yMax + 1e-9; v += yStep) {
    grid.push(s("line", { x1: L, x2: R, y1: Y(v), y2: Y(v), class: "gl" }),
      s("text", { x: L - 10, y: Y(v) + 4, class: "ax", "text-anchor": "end" }, pctTick(v)));
  }
  for (let v = 0; v <= xMax + 1e-9; v += xStep) {
    grid.push(s("text", { x: X(v), y: B + 20, class: "ax", "text-anchor": "middle" }, pctTick(v)));
  }
  grid.push(
    s("text", { x: (L + R) / 2, y: H - 4, class: "ax-t", "text-anchor": "middle" }, "Риск — волатильность, % в год"),
    s("text", { x: 14, y: (T + B) / 2, class: "ax-t", "text-anchor": "middle", transform: `rotate(-90 14 ${(T + B) / 2})` }, "Доходность, % в год"));

  // подписи без наложений: пробуем справа, слева, сверху, снизу; не влезло — только всплывающая подсказка
  const placed = [];
  const pointBoxes = pts.map((p) => ({ x0: X(p.volatility) - 8, x1: X(p.volatility) + 8, y0: Y(p.ret) - 8, y1: Y(p.ret) + 8 }));
  const hit = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
  const labels = [];
  const order = pts.map((p, i) => i).sort((a, b) => pts[b].ret - pts[a].ret);
  for (const i of order) {
    const p = pts[i];
    const cx = X(p.volatility);
    const cy = Y(p.ret);
    const text = shortName(p.name);
    const w = text.length * 6.8;
    const cands = [
      { x: cx + 11, y: cy + 4, a: "start", box: { x0: cx + 10, x1: cx + 12 + w, y0: cy - 8, y1: cy + 7 } },
      { x: cx - 11, y: cy + 4, a: "end", box: { x0: cx - 12 - w, x1: cx - 10, y0: cy - 8, y1: cy + 7 } },
      { x: cx, y: cy - 12, a: "middle", box: { x0: cx - w / 2, x1: cx + w / 2, y0: cy - 24, y1: cy - 9 } },
      { x: cx, y: cy + 22, a: "middle", box: { x0: cx - w / 2, x1: cx + w / 2, y0: cy + 10, y1: cy + 25 } },
    ];
    const ok = cands.find((c) => c.box.x0 >= 2 && c.box.x1 <= W - 2 && c.box.y0 >= 0 && c.box.y1 <= B
      && !placed.some((b) => hit(b, c.box)) && !pointBoxes.some((b, j) => j !== i && hit(b, c.box)));
    if (ok) {
      placed.push(ok.box);
      labels.push(s("text", { x: ok.x, y: ok.y, class: "pl", "text-anchor": ok.a }, text));
    }
  }
  const dots = pts.map((p) => {
    const lazy = groupKey(data, p.group) === "lazy";
    return s("a", { href: href(`/portfolios/${encodeURIComponent(p.id)}`), class: "pt-link",
      "aria-label": `${p.name}: доходность ${fmtPct(p.ret, 1)} в год, волатильность ${fmtPct(p.volatility, 1)}` },
    s("title", {}, `${p.name}\nДоходность: ${fmtPct(p.ret, 1)} в год\nВолатильность: ${fmtPct(p.volatility, 1)}`),
    s("circle", { cx: X(p.volatility), cy: Y(p.ret), r: 7, class: `pt ${lazy ? "lazy" : "theme"}` }));
  });
  return s("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img",
    "aria-label": "Диаграмма риск/доходность портфелей за 10 лет" }, grid, labels, dots);
}

// ---------------------------------------------------------------- как читать рейтинг

const RANK_PARTS = [["sharpe", "Шарп"], ["sortino", "Сортино"], ["omega", "Омега"], ["calmar", "Кальмар"], ["martin", "Мартин"]];

function ratingPanel(all) {
  const ex = all.find((r) => isNum(r.rank) && r.rank_detail);
  return h("section", { class: "dark-panel", "aria-labelledby": "pf-howto" },
    h("h3", { id: "pf-howto" }, "Как читать ", h("i", {}, "рейтинг")),
    h("p", {}, "Портфели сравниваются за одно окно — 5 лет. По каждому из пяти коэффициентов считается перцентиль " +
      "среди всех портфелей (100 — лучший), рейтинг — их среднее." + (ex ? ` Пример — ${ex.name}:` : "")),
    ex ? h("div", { class: "rw" }, RANK_PARTS.flatMap(([k, label]) => {
      const v = ex.rank_detail?.[k];
      return [h("span", {}, label), h("b", {}, fmtNum(v, 0)),
        h("div", { class: "b", "aria-hidden": "true" }, h("i", { style: { width: `${isNum(v) ? Math.max(0, Math.min(100, v)) : 0}%` } }))];
    })) : null,
    h("p", { style: { marginTop: "20px" } }, "Портфели с историей короче 5 лет в рейтинге не участвуют. Коэффициенты считаются по дневным доходностям."));
}

// ================================================================ СТРАНИЦА ПОРТФЕЛЯ

let detailSeq = 0;

export const detail = {
  STATIC: true,
  init() {},
  onGlobalChange() {},
  rerender() {},
  async onShow(route) {
    const seq = ++detailSeq;
    const id = route.params?.id || "";
    const page = $("#pf-page");
    $("#pf-title").textContent = "Портфель";
    put($("#pf-tags"));
    put($("#pf-crumbs"), h("a", { href: href("/portfolios") }, "Портфели"));
    put(page, h("p", { class: "muted" }, "Загрузка портфеля…"));
    let lib;
    let data;
    try {
      ({ lib, data } = await portfoliosData());
    } catch (e) {
      if (seq === detailSeq) put(page, loadError(e));
      return;
    }
    if (seq !== detailSeq) return;
    const p = lib.portfolioById(data, id);
    if (!p || !p.report) {
      $("#pf-title").textContent = "Портфель не найден";
      document.title = "Портфель не найден — FinServ";
      put(page, h("div", { class: "card prose pf-missing" },
        h("p", {}, `Портфеля «${id}» нет в каталоге — возможно, ссылка устарела.`),
        h("p", {}, h("a", { href: href("/portfolios") }, "Все портфели и стратегии"))));
      return;
    }
    renderDetail(lib, data, p);
  },
};

function renderDetail(lib, data, p) {
  const r = p.report;
  const row = p.row || {};
  const name = r.name || row.name || p.id;
  const group = r.group || row.group;
  document.title = `${name} — FinServ`;

  // заголовок: первое слово — сплошное, остальное — контурное (как в макете)
  const [first, ...rest] = String(name).split(" ");
  put($("#pf-title"), first, rest.length ? " " : null, rest.length ? h("em", {}, rest.join(" ")) : null);
  put($("#pf-crumbs"),
    h("a", { href: href("/portfolios") }, "Портфели"), " · ",
    group ? h("a", { href: href("/portfolios", { g: groupKey(data, group) }) }, group) : null, group ? " · " : null,
    h("b", {}, name));
  put($("#pf-tags"),
    group ? h("span", { class: "tag" }, groupOne(group)) : null,
    r.rebalance_label ? h("span", { class: "tag" }, `Ребалансировка ${r.rebalance_label}`) : null,
    r.benchmark_short ? h("span", { class: "tag" }, `Бенчмарк: ${r.benchmark_short}`) : null);

  const warn = [];
  if (lib.isStale?.(data, p.id)) {
    warn.push(notice("warn", `Данные портфеля на ${fmtDate(r.last_date || row.last_date)}: при последнем обновлении часть рядов не загрузилась, показан предыдущий расчёт.`));
  }
  for (const w of r.warnings || []) warn.push(notice("warn", String(w)));

  const desc = (r.description || r.rules) ? h("div", { class: "pf-desc" },
    r.description ? h("p", {}, r.description) : null,
    r.rules ? h("p", { class: "rules" }, h("b", {}, "Правила: "), r.rules) : null) : null;

  // ключевые цифры
  const M = r.metrics || {};
  const dd = r.drawdowns?.top?.[0];
  const maxDd = isNum(M.MAX?.max_drawdown) ? M.MAX.max_drawdown : row.max_drawdown;
  const sinceYear = String(r.since || row.since || "").slice(0, 4);
  const feeNote = isNum(r.fee) && isNum(r.fee_coverage) && r.fee_coverage < 1 ? " (известна не для всех частей)" : "";
  const kp = h("div", { class: "kp" },
    h("div", { class: "g" }, h("b", {}, fmtRub(r.final_value)),
      h("span", {}, `из ${fmtRub(r.initial ?? data.initial)}${sinceYear ? ` в ${sinceYear} году` : ""}`)),
    h("div", {}, h("b", {}, fmtPct(M.MAX?.cagr ?? row.cagr_all, 1)), h("span", {}, "в год за всё время")),
    h("div", {}, h("b", {}, fmtPct(M["10Y"]?.volatility, 1)), h("span", {}, M["10Y"] ? "волатильность, 10 лет" : "волатильность, 10 лет — история короче")),
    h("div", {}, h("b", {}, fmtPct(maxDd, 1)), h("span", {}, `макс. просадка${dd?.trough ? ` (${dd.trough.slice(0, 4)})` : ""}`)),
    h("div", {}, h("b", {}, fmtPct(r.fee, 2)), h("span", {}, isNum(r.fee) ? `комиссия фондов в год${feeNote}` : "комиссия — нет фондов с данными")));

  const three = h("div", { class: "pf-three" }, growthCard(name, r, p), compositionCard(r));
  const grid2 = h("div", { class: "pf-grid2" }, returnsCard(name, r), metricsCard(r));

  put($("#pf-page"), ...warn, desc, kp, three, grid2, replicateCard(r));
}

// ---------------------------------------------------------------- рост капитала (SVG)

function growthCard(name, r, p) {
  const pf = toSeries(p.series);
  const bm = toSeries(p.benchmark);
  const init = r.initial;
  const card = h("section", { class: "card", "aria-labelledby": "pf-growth" },
    h("h3", { class: "card-title", id: "pf-growth" }, `Рост ${fmtRub(init)}`));
  if (!pf) {
    card.append(h("p", { class: "muted" }, "Нет ряда стоимости портфеля."));
    return card;
  }
  card.append(h("div", { class: "chart-legend" },
    h("span", { class: "lg-pf" }, h("i"), `${name} — ${fmtRub(pf.values.at(-1))}`),
    bm ? h("span", { class: "lg-bm" }, h("i"), `${r.benchmark_short || r.benchmark} — ${fmtRub(bm.values.at(-1))}`) : null));
  card.append(growthSvg(name, r, pf, bm));
  card.append(h("p", { class: "hint" }, `С ${fmtDate(pf.dates[0])} по ${fmtDate(pf.dates.at(-1))}, месячные точки; ` +
    "доходность индексов с реинвестированием, без налогов и комиссий фондов."));
  return card;
}

function growthSvg(name, r, pf, bm) {
  const W = 760;
  const H = 300;
  const L = 86;
  const R = 664;
  const T = 16;
  const B = 266;
  const t = (d) => Date.parse(`${d}T00:00:00Z`);
  const t0 = t(pf.dates[0]);
  const t1 = t(pf.dates.at(-1));
  const vmax = Math.max(...pf.values, ...(bm ? bm.values : []));
  const step = niceStep(vmax, 4);
  const ymax = Math.ceil(vmax / step) * step;
  const X = (d) => L + ((t(d) - t0) / Math.max(1, t1 - t0)) * (R - L);
  const Y = (v) => B - (v / ymax) * (B - T);
  const big = ymax >= 2e6;
  const yTick = (v) => (big ? `${fmtNum(v / 1e6, step < 1e6 ? 1 : 0)} млн` : `${fmtNum(v / 1e3, 0)} тыс.`);
  const endTick = (v) => (v >= 1e6 ? `${fmtNum(v / 1e6, 2)} млн` : `${fmtNum(v / 1e3, 0)} тыс.`);

  const els = [];
  els.push(s("defs", {}, s("linearGradient", { id: "pf-ga", x1: 0, y1: 0, x2: 0, y2: 1 },
    s("stop", { offset: 0, "stop-color": "#C7A253", "stop-opacity": 0.32 }),
    s("stop", { offset: 1, "stop-color": "#C7A253", "stop-opacity": 0 }))));
  for (let v = 0; v <= ymax + 1e-6; v += step) {
    els.push(s("line", { x1: L, x2: R, y1: Y(v), y2: Y(v), class: "gl" }),
      s("text", { x: L - 10, y: Y(v) + 4, class: "ax", "text-anchor": "end" }, yTick(v)));
  }
  const y0 = Number(pf.dates[0].slice(0, 4));
  const y1 = Number(pf.dates.at(-1).slice(0, 4));
  const yStep = Math.max(1, Math.ceil((y1 - y0) / 8));
  for (let y = y0 + 1; y <= y1; y += yStep) {
    const d = `${y}-01-01`;
    if (t(d) < t0 || t(d) > t1) continue;
    els.push(s("text", { x: X(d), y: B + 20, class: "ax", "text-anchor": "middle" }, String(y)));
  }
  const path = (S) => S.dates.map((d, i) => `${i ? "L" : "M"}${X(d).toFixed(1)},${Y(S.values[i]).toFixed(1)}`).join(" ");
  const pPath = path(pf);
  els.push(s("path", { d: `${pPath} L${X(pf.dates.at(-1)).toFixed(1)},${B} L${L},${B} Z`, fill: "url(#pf-ga)" }));
  if (bm) els.push(s("path", { d: path(bm), class: "ln bm" }));
  els.push(s("path", { d: pPath, class: "ln pf" }));

  // подписи в конце линий — без наложения
  let ya = Y(pf.values.at(-1));
  let yb = bm ? Y(bm.values.at(-1)) : null;
  if (bm && Math.abs(ya - yb) < 16) {
    const mid = (ya + yb) / 2;
    if (ya <= yb) { ya = mid - 8; yb = mid + 8; } else { ya = mid + 8; yb = mid - 8; }
  }
  els.push(s("circle", { cx: X(pf.dates.at(-1)), cy: Y(pf.values.at(-1)), r: 4.5, fill: "#C7A253" }),
    s("text", { x: R + 10, y: ya + 4, class: "vl g" }, endTick(pf.values.at(-1))));
  if (bm) {
    els.push(s("circle", { cx: X(bm.dates.at(-1)), cy: Y(bm.values.at(-1)), r: 4.5, fill: "#37386C" }),
      s("text", { x: R + 10, y: yb + 4, class: "vl" }, endTick(bm.values.at(-1))));
  }
  els.push(s("text", { x: 14, y: (T + B) / 2, class: "ax-t", "text-anchor": "middle", transform: `rotate(-90 14 ${(T + B) / 2})` }, "Стоимость, ₽"));

  // подсказка по наведению: дата и значения
  const line = s("line", { class: "hover-line", y1: T, y2: B, x1: -10, x2: -10, visibility: "hidden" });
  const dot = s("circle", { r: 4, fill: "#C7A253", stroke: "#fff", "stroke-width": 2, visibility: "hidden" });
  const overlay = s("rect", { x: L, y: T, width: R - L, height: B - T, fill: "transparent" });
  els.push(line, dot, overlay);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `График роста ${fmtRub(r.initial)}: ${name} против ${r.benchmark_short || r.benchmark || "бенчмарка"}` }, els);
  const tip = h("div", { class: "svg-tip", hidden: true });
  const box = h("div", { class: "svg-chart" }, svg, tip);
  const bmAt = (d) => {
    if (!bm) return null;
    let lo = 0;
    let hi = bm.dates.length - 1;
    if (bm.dates[0] > d) return null;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (bm.dates[mid] <= d) lo = mid; else hi = mid - 1;
    }
    return bm.values[lo];
  };
  const move = (ev) => {
    const rect = svg.getBoundingClientRect();
    const vx = ((ev.clientX - rect.left) / rect.width) * W;
    const target = t0 + ((vx - L) / (R - L)) * (t1 - t0);
    let i = 0;
    let best = Infinity;
    for (let k = 0; k < pf.dates.length; k++) {
      const dist = Math.abs(t(pf.dates[k]) - target);
      if (dist < best) { best = dist; i = k; }
    }
    const d = pf.dates[i];
    const x = X(d);
    line.setAttribute("x1", x); line.setAttribute("x2", x); line.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x); dot.setAttribute("cy", Y(pf.values[i])); dot.setAttribute("visibility", "visible");
    const bv = bmAt(d);
    put(tip, h("div", {}, fmtDate(d)), h("div", {}, `${shortName(name)}: `, h("b", {}, fmtRub(pf.values[i]))),
      isNum(bv) ? h("div", {}, `${r.benchmark_short || r.benchmark}: `, h("b", {}, fmtRub(bv))) : null);
    tip.hidden = false;
    tip.style.left = `${Math.min(80, Math.max(20, (x / W) * 100))}%`;
    tip.style.top = `${(Math.min(Y(pf.values[i]), isNum(bv) ? Y(bv) : Infinity) / H) * 100 - 3}%`;
  };
  const leave = () => {
    tip.hidden = true;
    line.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
  };
  overlay.addEventListener("pointermove", move);
  overlay.addEventListener("pointerleave", leave);
  return box;
}

// ---------------------------------------------------------------- состав (кольцевая диаграмма)

const DONUT = ["#0A0F70", "#C7A253", "#7C7FB8", "#CFD0E3", "#37386C", "#E3C98A", "#2F5DB8", "#A98433"];

function compositionCard(r) {
  const weights = Object.entries(r.weights || {}).filter(([, w]) => isNum(w) && w > 0);
  const card = h("section", { class: "card", "aria-labelledby": "pf-comp" },
    h("h3", { class: "card-title", id: "pf-comp" }, "Состав"),
    h("p", { class: "card-sub" }, "Целевые доли и текущие после дрейфа цен"));
  if (!weights.length) {
    card.append(h("p", { class: "muted" }, "Состав недоступен."));
  } else {
    const C = 2 * Math.PI * 70;
    const gap = weights.length > 1 ? 3 : 0;
    let cum = 0;
    const arcs = weights.map(([, w], i) => {
      const el = s("circle", { r: 70, cx: 90, cy: 90, fill: "none", stroke: DONUT[i % DONUT.length], "stroke-width": 26,
        "stroke-dasharray": `${Math.max(0.5, w * C - gap).toFixed(1)} 999`, "stroke-dashoffset": (-cum * C).toFixed(1),
        transform: "rotate(-90 90 90)" });
      cum += w;
      return el;
    });
    const n = weights.length;
    const svg = s("svg", { viewBox: "0 0 180 180", role: "img", "aria-label": "Целевые доли активов" }, arcs,
      s("text", { x: 90, y: 88, "text-anchor": "middle", class: "dn1" }, String(n)),
      s("text", { x: 90, y: 108, "text-anchor": "middle", class: "dn2" }, plural(n, "актив", "актива", "активов")));
    const cur = r.current_weights || {};
    card.append(h("div", { class: "compw" }, svg,
      h("div", { class: "cp-list" }, weights.map(([k, w], i) => h("div", { class: "cp" },
        h("span", { class: "dot", style: { background: DONUT[i % DONUT.length] } }),
        h("span", {}, r.assets_short?.[k] || r.assets?.[k] || k),
        h("b", {}, fmtShare(w)),
        isNum(cur[k]) ? h("em", {}, `сейчас ${fmtPct(cur[k], 1)}`) : null)))));
  }
  if (r.one_fund?.note) card.append(h("div", { class: "one" }, r.one_fund.note));
  return card;
}

// ---------------------------------------------------------------- доходность по периодам

const PERIODS = [["1D", "1 день"], ["1W", "1 неделя"], ["1M", "1 месяц"], ["6M", "6 месяцев"], ["YTD", "С начала года"],
  ["1Y", "1 год"], ["3Y", "3 года"], ["5Y", "5 лет"], ["10Y", "10 лет"], ["ALL", "Всё время"]];

function returnsCard(name, r) {
  const pf = r.returns?.portfolio || {};
  const bm = r.returns?.benchmark || null;
  const retCell = (x) => {
    if (!x || !isNum(x.ret)) return h("td", { class: "num na" }, "—");
    return h("td", { class: `num ${signCls(x.ret)}` }, fmtSigned(x.ret, 1),
      isNum(x.cagr) ? h("small", { class: "cagr" }, `${fmtPct(x.cagr, 1)} в год`) : null);
  };
  const rows = PERIODS.filter(([k]) => pf[k] || bm?.[k]);
  return h("section", { class: "card", "aria-labelledby": "pf-ret" },
    h("h3", { class: "card-title", id: "pf-ret" }, "Доходность по периодам"),
    h("p", { class: "card-sub" }, `На ${fmtDate(r.last_date)}; для периодов больше года — ещё и в среднем за год.`),
    rows.length ? tableWrap(h("table", { class: "periods-table" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Период"),
        h("th", { scope: "col", class: "num" }, h("span", { class: "swatch", style: { borderColor: "#C7A253" } }), "Портфель"),
        bm ? h("th", { scope: "col", class: "num" }, h("span", { class: "swatch dashed", style: { borderColor: "#37386C" } }), r.benchmark_short || r.benchmark) : null)),
      h("tbody", {}, rows.map(([k, label]) => h("tr", {}, h("th", { scope: "row" }, label), retCell(pf[k]), bm ? retCell(bm[k]) : null)))))
      : h("p", { class: "muted" }, "Нет данных о доходности."));
}

// ---------------------------------------------------------------- коэффициенты по окнам

const WINDOWS = [["1Y", "1 год"], ["5Y", "5 лет"], ["10Y", "10 лет"], ["MAX", "Всё время"]];
const METRIC_ROWS = [
  ["cagr", "Доходность в год (CAGR)", "pct"], ["total_return", "Накопленная доходность", "pct"],
  ["volatility", "Волатильность (год.)", "pct"], ["max_drawdown", "Макс. просадка", "pct"],
  ["current_drawdown", "Текущая просадка", "pct"], ["ulcer_index", "Индекс язвы", "pct"],
  ["sharpe", "Коэф. Шарпа", "coef"], ["sortino", "Коэф. Сортино", "coef"], ["omega", "Коэф. Омега", "coef"],
  ["calmar", "Коэф. Кальмара", "coef"], ["martin", "Коэф. Мартина", "coef"],
  ["beta", "Бета", "coef"], ["alpha", "Альфа Дженсена (год.)", "pct"], ["r_squared", "R² с бенчмарком", "coef"],
  ["correlation", "Корреляция с бенчмарком", "coef"], ["up_capture", "Захват роста", "pct"], ["down_capture", "Захват падения", "pct"],
  ["rf", "Безрисковая ставка (средняя)", "pct2"],
];

function metricsCard(r) {
  const M = r.metrics || {};
  const wins = WINDOWS.filter(([k]) => M[k]);
  const fmt = (v, kind) => (kind === "pct" ? fmtPct(v, 1) : kind === "pct2" ? fmtPct(v, 2) : fmtNum(v, 2));
  const warns = [...new Set(wins.map(([k]) => M[k].rf_warning).filter(Boolean))];
  return h("section", { class: "card", "aria-labelledby": "pf-met" },
    h("h3", { class: "card-title", id: "pf-met" }, "Коэффициенты"),
    h("p", { class: "card-sub" }, `По дневным доходностям; безрисковая ставка — доходность коротких ОФЗ; бенчмарк — ${r.benchmark_short || r.benchmark || "—"}.`),
    ...warns.map((w) => notice("warn", String(w))),
    wins.length ? tableWrap(h("table", { class: "metrics-table" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Показатель"),
        wins.map(([k, label]) => h("th", { scope: "col", class: "num" }, label,
          M[k].from ? h("small", { class: "cagr", style: { display: "block", fontWeight: 400 } }, `с ${fmtDate(M[k].from)}`) : null)))),
      h("tbody", {}, METRIC_ROWS.map(([key, label, kind]) => h("tr", {}, h("th", { scope: "row" }, label),
        wins.map(([k]) => {
          const t = fmt(M[k][key], kind);
          return h("td", { class: `num${t === "—" ? " na" : ""}` }, t);
        }))))))
      : h("p", { class: "muted" }, "Нет данных."),
    M["10Y"] ? null : h("p", { class: "hint" }, "История портфеля короче 10 лет — окно «10 лет» не показано."));
}

// ---------------------------------------------------------------- как повторить

function replicateCard(r) {
  const rep = r.replicate || [];
  const card = h("section", { class: "card repl pf-block", "aria-labelledby": "pf-repl" },
    h("h3", { class: "card-title", id: "pf-repl" }, "Как повторить"),
    h("p", { class: "card-sub" }, "Биржевые фонды для повторения портфеля — точные или близкие, см. примечания. Первым указан фонд, по которому считается комиссия портфеля."));
  if (!rep.length) {
    card.append(h("p", { class: "muted" }, "Нет данных о фондах."));
    return card;
  }
  const fundCell = (funds) => {
    if (!funds?.length) return h("td", { class: "na" }, "нет подходящего биржевого фонда");
    const [f, ...alt] = funds;
    return h("td", {},
      h("a", { href: symbolHref(f.secid), class: "mono" }, h("b", {}, f.secid)), " ",
      h("small", {}, f.issuer || ""),
      alt.length ? h("span", { class: "fund-alt" }, "Также: ",
        alt.flatMap((a, i) => [i ? ", " : "", h("a", { href: symbolHref(a.secid), class: "mono" }, a.secid),
          ` (${[a.issuer, isNum(a.fee) ? fmtPct(a.fee, 2) : null].filter(Boolean).join(", ")})`])) : null);
  };
  card.append(tableWrap(h("table", {},
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Класс актива"), h("th", { scope: "col", class: "num" }, "Доля"),
      h("th", { scope: "col" }, "Фонд"), h("th", { scope: "col", class: "num" }, "Комиссия"))),
    h("tbody", {}, rep.map((a) => h("tr", {},
      h("td", {}, a.asset_short || a.asset_name || a.asset, a.note ? h("small", { class: "fund-alt" }, a.note) : null),
      h("td", { class: "num" }, fmtShare(a.weight)),
      fundCell(a.funds),
      cell(fmtPct(a.funds?.[0]?.fee, 2))))),
    h("tfoot", {}, h("tr", {}, h("td", {}, "Итого по портфелю"), h("td", { class: "num" }, ""), h("td", {},
      isNum(r.fee_coverage) && r.fee_coverage < 1 && isNum(r.fee) ? h("small", {}, "без частей, для которых нет фонда") : ""),
    h("td", { class: "num" }, fmtPct(r.fee, 2)))))));
  if (r.one_fund?.funds?.length) {
    const f = r.one_fund.funds[0];
    card.append(h("p", { class: "hint" }, "Одним фондом: ", h("a", { href: symbolHref(f.secid), class: "mono" }, f.secid),
      ` «${f.name}»${f.issuer ? `, ${f.issuer}` : ""}${isNum(f.fee) ? `, комиссия ${fmtPct(f.fee, 2)}` : ""}.`));
  }
  card.append(h("p", { class: "source" }, "Комиссии, УК и СЧА фондов — ",
    h("a", { href: "https://rusetfs.com/", rel: "noopener" }, "RusETFs"), " (резервно — T-Invest API). Не является инвестиционной рекомендацией."));
  return card;
}
