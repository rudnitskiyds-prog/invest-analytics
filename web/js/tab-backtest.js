// Вкладка «Бэктест»: классические пассивные стратегии и свой портфель. Все расчёты — web/lib (runBacktest, computeAll…).

import { fact, registerFacts } from "./assistant.js";
import { colorOf, destroyChart, heatStyle, lineChart } from "./charts.js";
import { $, downloadCsv, errorNotice, h, nextFrame, notice, tableWrap, withBusy } from "./dom.js";
import { assetLabel, engine, entries, requireEngine, toSet, UserError } from "./engine.js";
import { bindCheckbox, bindNumber, bindValue, globals, readDates, readNumber, FREQ_LABELS } from "./fields.js";
import { csvNum, fmtDate, fmtMetric, fmtNum, fmtPct, fmtRub, parseNum } from "./format.js";
import { riskFree } from "./riskfree.js";
import { getMany } from "./source.js";
import { hint, SIMPLE_METRICS } from "./glossary.js";
import * as S from "./state.js";

export const PF_NAME = "Свой портфель";
const DEFAULT_PF = [["MCFTR", 50], ["RGBITR", 30], ["GOLD_CBR", 20]];

let strategyNames = [];
let last = null;          // последний результат (для перерисовки без пересчёта)
let running = false;
let facts = [];           // факты экрана для ассистента — из последнего render()

registerFacts("/tools/backtest", () => (last ? facts : []));

// ---------------------------------------------------------------- свой портфель (таблица)

function parsePf(str) {
  if (!str) return DEFAULT_PF.map((r) => [...r]);
  return str
    .split(",")
    .map((p) => p.split(":"))
    .filter((p) => p[0])
    .map(([k, v]) => [decodeURIComponent(k).trim().toUpperCase(), parseNum(v ?? "")]);
}

function pfRows() {
  return [...$("#bt-pf-table tbody").rows].map((tr) => {
    const [a, w] = tr.querySelectorAll("input");
    return [a.value.trim().toUpperCase(), parseNum(w.value)];
  });
}

function savePf() {
  const rows = pfRows().filter(([k]) => k);
  S.set({ pf: rows.map(([k, w]) => `${encodeURIComponent(k)}:${Number.isFinite(w) ? w : ""}`).join(",") });
  updatePfSum();
}

function updatePfSum() {
  const rows = pfRows().filter(([k, w]) => k && Number.isFinite(w) && w > 0);
  const sum = rows.reduce((a, [, w]) => a + w, 0);
  const el = $("#bt-pf-sum");
  el.textContent = `Итого: ${fmtNum(sum, 1)} %`;
  const bad = Math.abs(sum - 100) > 0.05;
  el.classList.toggle("bad", bad);
  el.title = bad ? "Сумма не равна 100 % — при расчёте доли будут нормированы" : "";
}

function addPfRow(key = "", w = "") {
  const tbody = $("#bt-pf-table tbody");
  const n = tbody.rows.length + 1;
  const tr = h(
    "tr",
    {},
    h("td", {}, h("input", {
      type: "text", list: "catalog-list", value: key, "aria-label": `Актив ${n}`,
      autocomplete: "off", spellcheck: "false", placeholder: "Тикер",
    })),
    h("td", { class: "num" }, h("input", {
      type: "text", inputmode: "decimal", value: Number.isFinite(w) ? String(w).replace(".", ",") : "",
      "aria-label": `Доля актива ${n}, %`, autocomplete: "off",
    })),
    h("td", {}, h("button", {
      type: "button", class: "btn btn-icon", "aria-label": `Удалить актив ${n}`, title: "Удалить",
      onclick: () => { tr.remove(); savePf(); },
    }, "×")),
  );
  tr.addEventListener("input", savePf);
  tbody.append(tr);
  return tr;
}

/** Задать свой портфель извне (граница Марковица, страница бумаги). weights — доли. */
export function setCustomPortfolio(weights, message = "Веса с границы Марковица перенесены в «Свой портфель». Нажмите «Посчитать».") {
  const tbody = $("#bt-pf-table tbody");
  tbody.replaceChildren();
  for (const [k, w] of Object.entries(weights)) {
    if (w > 1e-4) addPfRow(k, Math.round(w * 1000) / 10);
  }
  $("#bt-pf-on").checked = true;
  S.set({ pfon: "1" });
  savePf();
  last = null;
  $("#bt-out").replaceChildren(notice("info", message));
}

// ---------------------------------------------------------------- инициализация

export function init() {
  bindValue("bt-from", "from");
  bindValue("bt-till", "till");
  bindNumber("bt-cap", "cap", { min: 1000, max: 1e11, digits: 0 });
  bindValue("bt-rebal", "rebal");
  bindNumber("bt-band", "band", { min: 0, max: 50 });
  bindNumber("bt-comm", "comm", { min: 0, max: 5 });
  bindNumber("bt-contrib", "contrib", { min: 0, max: 1e10, digits: 0 });
  bindValue("bt-cfreq", "cfreq");
  bindNumber("bt-tax", "tax", { min: 0, max: 50 });
  bindCheckbox("bt-pf-on", "pfon");

  for (const [k, w] of parsePf(S.get("pf"))) addPfRow(k, w);
  updatePfSum();
  $("#bt-pf-add").addEventListener("click", () => addPfRow().querySelector("input").focus());

  $("#bt-form").addEventListener("submit", (e) => {
    e.preventDefault();
    run();
  });

  if (!engine.ok) {
    $("#bt-strategies").replaceChildren(notice("error", "Список стратегий недоступен: модули расчёта не загрузились."));
    return;
  }
  const { STRATEGIES } = engine.strategies;
  strategyNames = Object.keys(STRATEGIES);

  const sel = $("#bt-rebal");
  sel.replaceChildren(...entries(engine.backtest.REBAL_LABELS).map(([v, t]) => h("option", { value: v }, t)));
  sel.value = S.get("rebal");

  const picked = pickedIdx();
  const list = $("#bt-strategies");
  list.replaceChildren(
    ...strategyNames.map((name, i) => {
      const s = STRATEGIES[name];
      const box = h("input", { type: "checkbox", value: String(i), checked: picked.has(i) });
      box.addEventListener("change", () => {
        const idx = [...list.querySelectorAll("input:checked")].map((x) => x.value);
        S.set({ st: idx.length ? idx.join(",") : "none" });
      });
      return h(
        "label",
        { class: "check" },
        box,
        h("span", { class: "swatch", style: { background: colorOf(name) }, "aria-hidden": "true" }),
        h("span", { class: "check-body" },
          h("span", { class: "check-title" }, name),
          h("span", { class: "check-note" }, weightsText(s.weights))),
      );
    }),
  );
}

const CLASS_WORDS = {
  "Облигации гос.": "Гособлигации",
  "Облигации корп.": "Корпоративные облигации",
  "Денежный рынок": "Денежный рынок (почти как вклад)",
};

/** «Акции 30 % · Золото 30 % …» — доли суммируются по классу актива из каталога; коды индексов — в «Подробно». */
function weightsText(w) {
  const cls = Object.fromEntries((engine.data?.CATALOG || []).map((c) => [c.key, c.assetClass]));
  const groups = new Map();  // класс (или код, если его нет в каталоге) → { v, keys }
  for (const [k, v] of entries(w)) {
    const name = cls[k] ? (CLASS_WORDS[cls[k]] || cls[k]) : k;
    const g = groups.get(name) || { v: 0, keys: [] };
    g.v += v;
    if (cls[k]) g.keys.push(k);
    groups.set(name, g);
  }
  return [...groups].map(([name, { v, keys }]) => h("span", { class: "w-item" },
    `${name} ${fmtPct(v, Math.round(v * 1000) % 10 ? 1 : 0)}`,
    keys.length ? h("span", { class: "adv muted" }, ` (${keys.join(", ")})`) : null))
    .flatMap((x, i) => (i ? [" · ", x] : [x]));
}

function pickedIdx() {
  const st = S.get("st");
  if (st === "none") return new Set();
  if (!st) {
    // по умолчанию — все стратегии, кроме «Завещания Баффетта» (как в версии Streamlit)
    return new Set(strategyNames.map((n, i) => (n.includes("Баффетт") ? -1 : i)).filter((i) => i >= 0));
  }
  return new Set(st.split(",").map(Number).filter((i) => i >= 0 && i < strategyNames.length));
}

export function onShow() {
  if (last || running || !engine.ok) return;
  if (S.isDemo() || S.get("bt") === "1") run();
}

export function onGlobalChange() {
  if (last && !running) run();
}

// ---------------------------------------------------------------- расчёт

function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function readParams() {
  const { from, till } = readDates("from", "till");
  return {
    from, till,
    cap: readNumber("cap", "Начальный капитал", { min: 1000, max: 1e11 }),
    rebal: S.get("rebal"),
    band: readNumber("band", "Коридор", { min: 0, max: 50 }) / 100,
    comm: readNumber("comm", "Комиссия", { min: 0, max: 5 }) / 100,
    contrib: readNumber("contrib", "Пополнение", { min: 0, max: 1e10 }),
    cfreq: S.get("cfreq"),
    tax: readNumber("tax", "НДФЛ", { min: 0, max: 50 }) / 100,
  };
}

function collectPortfolios() {
  const { STRATEGIES } = engine.strategies;
  const ports = {};
  for (const i of pickedIdx()) ports[strategyNames[i]] = { ...STRATEGIES[strategyNames[i]].weights };
  if ($("#bt-pf-on").checked) {
    const rows = pfRows().filter(([k]) => k);
    const bad = rows.filter(([, w]) => !Number.isFinite(w) || w < 0);
    if (bad.length) throw new UserError(`Свой портфель: проверьте долю у ${bad.map(([k]) => k).join(", ")}.`);
    const w = {};
    for (const [k, v] of rows) if (v > 0) w[k] = (w[k] || 0) + v / 100;
    if (!Object.keys(w).length) throw new UserError("Свой портфель пуст: добавьте активы с ненулевой долей.");
    ports[PF_NAME] = w;
  }
  if (!Object.keys(ports).length) {
    throw new UserError("Выберите хотя бы одну стратегию или включите свой портфель.");
  }
  return ports;
}

async function run() {
  const out = $("#bt-out");
  if (running) return;
  running = true;
  try {
    await withBusy($("#bt-run"), $("#bt-status"), async (status) => {
      try {
        const E = requireEngine();
        let g = globals();
        const p = readParams();
        const ports = collectPortfolios();
        const keys = [...new Set([...Object.values(ports).flatMap((w) => Object.keys(w)), g.bench])];

        const { series, errors } = await getMany(keys, p.from, p.till, (d, t) =>
          status(`Загрузка рядов: ${d} из ${t}…`));
        status("Расчёт…");
        await nextFrame();

        const warnings = [];
        const results = {};
        for (const [name, w] of Object.entries(ports)) {
          const miss = Object.keys(w).filter((k) => !series[k]);
          if (miss.length) {
            warnings.push({ text: `${name}: нет данных — ${miss.map((k) => errors[k]?.message || k).join(" ")}`,
              offerDemo: miss.some((k) => errors[k]?.offerDemo) });
            continue;
          }
          try {
            let frame = E.data.alignFrame(Object.fromEntries(Object.keys(w).map((k) => [k, series[k]])),
              { mode: "common" });
            frame = E.data.sliceFrame(frame, p.from, p.till);
            if (!frame.dates.length || frame.dates.length < 2) {
              warnings.push({ text: `${name}: нет общего периода данных по всем активам в ${fmtDate(p.from)} — ${fmtDate(p.till)}.` });
              continue;
            }
            if (frame.dates[0] > addDays(p.from, 45)) {
              warnings.push({ text: `${name}: данные доступны только с ${fmtDate(frame.dates[0])} — бэктест начат с этой даты.` });
            }
            results[name] = E.backtest.runBacktest(frame, {
              weights: w, initial: p.cap, rebalance: p.rebal, band: p.band > 0 ? p.band : null,
              commission: p.comm, contribution: p.contrib, contributionFreq: p.cfreq, taxRate: p.tax,
              start: p.from, end: p.till,
            });
          } catch (e) {
            warnings.push({ text: `${name}: расчёт не выполнен — ${e.message}` });
          }
        }

        if (!Object.keys(results).length) {
          const offer = Object.values(errors).some((e) => e.offerDemo);
          out.replaceChildren(
            notice("error", "Не удалось рассчитать ни один портфель.", { offerDemo: offer }),
            ...warnings.map((w) => notice("warn", w.text, w)),
          );
          last = null;
          return;
        }

        // Бенчмарк: тот же период, что у результатов
        const starts = Object.values(results).map((r) => r.equity.dates[0]).sort();
        const ends = Object.values(results).map((r) => r.equity.dates.at(-1)).sort();
        let bench = null;
        if (series[g.bench]) {
          const b = series[g.bench];
          const dates = [];
          const values = [];
          b.dates.forEach((d, i) => {
            if (d >= starts[0] && d <= ends.at(-1)) { dates.push(d); values.push(b.values[i]); }
          });
          if (dates.length > 3) bench = { dates, values };
        }
        if (!bench) {
          warnings.push({ text: `Бенчмарк ${g.bench}: ${errors[g.bench]?.message || "нет данных за период"} Показатели относительно рынка не рассчитаны.`,
            offerDemo: errors[g.bench]?.offerDemo });
        }

        // безрисковая ставка — ряд доходности коротких ОФЗ по датам периода бэктеста
        const RF = await riskFree(starts[0], ends.at(-1));
        g = { ...g, rf: RF.rf, rfNote: RF.note };
        last = { results, bench, g, p, warnings };
        S.set({ bt: "1" });
        render();
      } catch (e) {
        last = null;
        out.replaceChildren(errorNotice(e));
      }
    });
  } finally {
    running = false;
  }
}

// ---------------------------------------------------------------- вывод

const yearsOf = (s) => (Date.parse(s.dates.at(-1)) - Date.parse(s.dates[0])) / (365.25 * 864e5);

/** Потоки для xirr: −внесённое на первую дату (капитал и, возможно, первое пополнение), −каждое пополнение, +конечная стоимость. */
function contribFlows(r) {
  const { dates, values } = r.invested;
  const flows = [{ date: dates[0], amount: -values[0] }];
  for (let i = 1; i < dates.length; i++) {
    if (values[i] > values[i - 1]) flows.push({ date: dates[i], amount: -(values[i] - values[i - 1]) });
  }
  flows.push({ date: r.equity.dates.at(-1), amount: r.equity.values.at(-1) });
  return flows;
}

function render() {
  const E = engine;
  const { results, bench, g, p, warnings } = last;
  const out = $("#bt-out");
  out.querySelectorAll("canvas").forEach(destroyChart);
  out.replaceChildren();

  const shown = [...warnings];
  if (g.freqForced) {
    shown.unshift({ text: "Демо-данные месячные: показатели рассчитаны по месячным доходностям." });
  }
  shown.forEach((w) => out.append(notice("warn", w.text, w)));

  const names = Object.keys(results);
  const benchName = g.bench;
  const benchScaled = bench ? E.metrics.rebase(bench, p.cap) : null;

  // показатели — одни и те же для карточек, «Если коротко» и таблицы
  const reports = {};
  for (const n of names) reports[n] = E.metrics.computeAll(results[n].equity, bench, g.rf, g.freq);
  if (bench) reports[benchName] = E.metrics.computeAll(bench, bench, g.rf, g.freq);

  // Годовой процент — только при фактическом сроке ≥ 1 года; при пополнениях CAGR стоимости включает взносы,
  // поэтому показываем денежно-взвешенную доходность (xirr). kind: "cagr" | "xirr" | "total" | null.
  const growth = (n, s, withContrib) => {
    const long = yearsOf(s) >= 1;
    if (withContrib) {
      if (!long) {
        // меньше года годовой процент не показываем — только прирост сверх внесённого
        const paid = results[n].invested.values.at(-1);
        return { kind: "gain", v: (s.values.at(-1) - paid) / paid };
      }
      const v = E.backtest.xirr(contribFlows(results[n]));
      return Number.isFinite(v) ? { kind: "xirr", v } : { kind: null };
    }
    return long ? { kind: "cagr", v: reports[n].cagr } : { kind: "total", v: reports[n].total_return };
  };
  const kpiSub = ({ kind, v }) => {
    if (kind === "cagr") return ["в среднем ", h("b", {}, fmtPct(v, 1)), " в год"];
    if (kind === "xirr") return ["с учётом пополнений ", h("b", {}, fmtPct(v, 1)), " в год"];
    if (kind === "total") return [h("b", {}, fmtPct(v, 1)), " за весь срок"];
    if (kind === "gain") return [h("b", {}, fmtPct(v, 1)), " — прирост сверх внесённого за весь срок"];
    return [];
  };

  // --- факты для ассистента: те же значения, что в карточках и «Если коротко»
  const SRC = "расчёт ИнвестАналитики по данным ISS MOEX и Банка России";
  const GROWTH_LABEL = { cagr: "в среднем в год", xirr: "в среднем в год с учётом пополнений",
    total: "доходность за весь срок", gain: "прирост сверх внесённого за весь срок" };
  facts = [];
  const pushFacts = (id, key, n, s, paid, gr) => {
    facts.push(
      fact(`${id}.final`, `${n}: итоговая стоимость`, s.values.at(-1), "rub", fmtRub(s.values.at(-1)), SRC),
      fact(`${id}.invested`, `${n}: внесено`, paid, "rub", fmtRub(paid), SRC),
      fact(`${id}.period`, `${n}: период`, null, "period",
        `${fmtDate(s.dates[0])} — ${fmtDate(s.dates.at(-1))}`, SRC),
      fact(`${id}.max_drawdown`, `${n}: максимальная просадка`, reports[key].max_drawdown, "pct",
        fmtPct(reports[key].max_drawdown, 1), SRC));
    if (gr.kind) facts.push(fact(`${id}.${gr.kind}`, `${n}: ${GROWTH_LABEL[gr.kind]}`, gr.v, "pct", fmtPct(gr.v, 1), SRC));
  };
  names.forEach((n, i) => pushFacts(`pf${i + 1}`, n, n, results[n].equity,
    p.contrib > 0 ? results[n].invested.values.at(-1) : p.cap, growth(n, results[n].equity, p.contrib > 0)));
  if (benchScaled) pushFacts("bench", benchName, `Бенчмарк ${benchName}`, benchScaled, p.cap, growth(benchName, bench, false));

  // --- карточки
  const kpis = h("div", { class: "kpis" });
  for (const n of names) {
    const r = results[n];
    const eq = r.equity;
    const sub = kpiSub(growth(n, eq, p.contrib > 0));
    if (p.contrib > 0) sub.push(`${sub.length ? " · " : ""}внесено ${fmtRub(r.invested.values.at(-1))}`);
    kpis.append(h("div", { class: "kpi", style: { "--kpi-color": colorOf(n) } },
      h("div", { class: "kpi-name" }, n),
      h("div", { class: "kpi-value" }, fmtRub(eq.values.at(-1))),
      h("div", { class: "kpi-sub" }, ...sub)));
  }
  if (benchScaled) {
    kpis.append(h("div", { class: "kpi bench", style: { "--kpi-color": colorOf(benchName, { bench: true }) } },
      h("div", { class: "kpi-name" }, `Бенчмарк ${benchName}`),
      h("div", { class: "kpi-value" }, fmtRub(benchScaled.values.at(-1))),
      h("div", { class: "kpi-sub" }, ...kpiSub(growth(benchName, bench, false)))));
  }
  out.append(kpis);

  // --- итог простыми словами
  out.append(h("section", { class: "card plain-summary" },
    h("h3", { class: "card-title" }, "Если коротко"),
    names.map((n) => {
      const eq = results[n].equity;
      const paid = p.contrib > 0 ? results[n].invested.values.at(-1) : p.cap;
      const gr = growth(n, eq, p.contrib > 0);
      const rate = {
        cagr: `, в среднем ${fmtPct(gr.v, 1)} в год`,
        total: `, это ${fmtPct(gr.v, 1)} за весь срок`,
        gain: `, прирост сверх внесённого за весь срок — ${fmtPct(gr.v, 1)}`,
        xirr: `. Ваши деньги с учётом пополнений приносили в среднем ${fmtPct(gr.v, 1)} в год`,
      }[gr.kind] || null;
      return h("p", {}, h("b", {}, n), ": вложили ", fmtRub(paid), " с ", fmtDate(eq.dates[0]), " — к ", fmtDate(eq.dates.at(-1)),
        " стало бы ", h("b", {}, fmtRub(eq.values.at(-1))), rate,
        ". В худший момент портфель был ниже прошлого максимума на ", h("b", {}, fmtPct(Math.abs(reports[n].max_drawdown), 0)), ".");
    }),
    h("p", { class: "hint" }, "Расчёт на исторических данных. Прошлая доходность не гарантирует будущей. Не является индивидуальной инвестиционной рекомендацией.")));

  const lineSeries = names.map((n) => ({ name: n, ...results[n].equity }));
  if (benchScaled) lineSeries.push({ name: benchName, ...benchScaled, bench: true });

  // --- стоимость
  const eqCanvas = h("canvas", { role: "img", "aria-label": "График стоимости портфелей и бенчмарка" });
  const logBox = h("input", { type: "checkbox", id: "bt-log", checked: S.get("log") === "1" });
  const drawEq = () => lineChart(eqCanvas, {
    series: lineSeries, yTitle: "Стоимость, млн ₽", yFormat: "rub", log: logBox.checked,
  });
  logBox.addEventListener("change", () => { S.set({ log: logBox.checked ? "1" : "0" }); drawEq(); });
  out.append(h("section", { class: "card" },
    h("div", { class: "chart-head" },
      h("div", {},
        h("h3", { class: "card-title" }, "Стоимость портфелей"),
        h("p", { class: "card-sub" }, `${fmtDate(names.map((n) => results[n].equity.dates[0]).sort()[0])} — ${fmtDate(names.map((n) => results[n].equity.dates.at(-1)).sort().at(-1))}, начальный капитал ${fmtRub(p.cap)}; бенчмарк приведён к тому же капиталу`)),
      h("label", { class: "switch" }, logBox, h("span", {}, "Логарифмическая шкала"))),
    h("div", { class: "chart-box" }, eqCanvas),
    p.contrib > 0
      ? h("p", { class: "hint" }, "При пополнениях CAGR по стоимости завышен: в стоимость входят внесённые средства.")
      : null));
  drawEq();

  // --- просадки
  const ddCanvas = h("canvas", { role: "img", "aria-label": "График просадок" });
  out.append(h("section", { class: "card" },
    h("h3", { class: "card-title" }, "Просадки", hint("max_drawdown")),
    h("p", { class: "card-sub" }, "Снижение стоимости от предыдущего максимума"),
    h("div", { class: "chart-box short" }, ddCanvas)));
  lineChart(ddCanvas, {
    series: lineSeries.map((s) => ({ ...E.metrics.drawdownSeries({ dates: s.dates, values: s.values }), name: s.name, bench: s.bench })),
    yTitle: "Просадка, %", yFormat: "pct",
  });

  // --- показатели эффективности
  const cols = Object.keys(reports);
  const metricsTable = buildMetricsTable(cols, reports, (c) => colorOf(c, { bench: c === benchName && !!bench }),
    { hideGrowth: p.contrib > 0 });
  const costs = names.map((n) => {
    const r = results[n];
    return `${n}: оборот ${fmtNum(r.turnover, 1)}×, комиссии ${fmtRub(r.costs)}, налог ${fmtRub(r.taxes)}`;
  }).join("; ");
  out.append(h("section", { class: "card" },
    h("div", { class: "chart-head" },
      h("div", {},
        h("h3", { class: "card-title" }, "Показатели эффективности"),
        h("p", { class: "card-sub simple-only" }, "Нажмите «?», чтобы узнать, что значит показатель. Все коэффициенты — в режиме «Подробно»."),
        h("p", { class: "card-sub adv" },
          `${g.rfNote} Доходности ${FREQ_LABELS[g.freq]}, бенчмарк ${benchName}.`)),
      h("div", { class: "toolbar" },
        h("button", { type: "button", class: "btn btn-sm", onclick: () => csvMetrics(cols, reports) }, "Скачать CSV — показатели"),
        h("button", { type: "button", class: "btn btn-sm", onclick: () => csvEquity(lineSeries) }, "Скачать CSV — стоимость"))),
    tableWrap(metricsTable),
    h("p", { class: "hint adv" }, `Оборот и издержки — ${costs}.`)));

  // --- по годам
  out.append(h("section", { class: "card adv" },
    h("h3", { class: "card-title" }, "Доходность по годам"),
    h("p", { class: "card-sub" }, "Первый и последний годы — неполные, от даты начала / до даты конца"),
    tableWrap(heatmap(lineSeries))));

  // --- журнал сделок
  const sel = h("select", { id: "bt-trades-sel" }, ...names.map((n) => h("option", { value: n }, n)));
  const tradesBox = h("div");
  const drawTrades = () => {
    const r = results[sel.value];
    tradesBox.replaceChildren(tradesTable(r));
  };
  sel.addEventListener("change", drawTrades);
  out.append(h("section", { class: "card adv" },
    h("div", { class: "chart-head" },
      h("div", {},
        h("h3", { class: "card-title" }, "Журнал сделок"),
        h("p", { class: "card-sub" }, "Покупки, ребалансировки и пополнения выбранного портфеля")),
      h("div", { class: "toolbar" },
        h("label", { for: "bt-trades-sel", class: "sr-only" }, "Портфель"),
        sel,
        h("button", { type: "button", class: "btn btn-sm", onclick: () => csvTrades(sel.value, results[sel.value]) }, "Скачать CSV — сделки"))),
    tradesBox));
  drawTrades();
}

// hideGrowth: при пополнениях CAGR и накопленная доходность ряда включают взносы — в «Просто» их не показываем
export function buildMetricsTable(cols, reports, colorFor, { simple = true, hideGrowth = false } = {}) {
  const GROWTH = new Set(["cagr", "total_return"]);
  const { LABELS_RU, PERCENT_FIELDS } = engine.metrics;
  const pf = toSet(PERCENT_FIELDS);
  const head = h("tr", {}, h("th", { scope: "col" }, "Показатель"),
    ...cols.map((c) => h("th", { scope: "col", class: "num" },
      h("span", { class: "swatch", style: { background: colorFor(c) }, "aria-hidden": "true" }), c)));
  const rows = entries(LABELS_RU).map(([key, label]) =>
    // aria-label на th — чтобы кнопка «?» не попадала в имя заголовка строки
    h("tr", { class: !simple || (SIMPLE_METRICS.has(key) && !(hideGrowth && GROWTH.has(key))) ? null : "adv" },
      h("th", { scope: "row", "aria-label": label }, h("span", {}, label), hint(key)),
      ...cols.map((c) => h("td", { class: "num" }, fmtMetric(key, reports[c][key], pf)))));
  return h("table", { class: "metrics-table" }, h("thead", {}, head), h("tbody", {}, rows));
}

/** Годовые доходности — web/lib (annualReturns) → Map «год → доходность». */
function annualReturns(s) {
  const { years, values } = engine.metrics.annualReturns({ dates: s.dates, values: s.values });
  return new Map(years.map((y, i) => [String(y), values[i]]));
}

function heatmap(lineSeries) {
  const data = lineSeries.map((s) => ({ name: s.name, r: annualReturns(s) }));
  const years = [...new Set(data.flatMap((d) => [...d.r.keys()]))].sort();
  let lim = 0;
  data.forEach((d) => d.r.forEach((v) => { if (Number.isFinite(v)) lim = Math.max(lim, Math.abs(v)); }));
  lim ||= 1;
  const cell = (v) => {
    if (!Number.isFinite(v)) return h("td", { class: "muted" }, "—");
    return h("td", { style: heatStyle(v, lim), title: fmtPct(v, 1) }, fmtPct(v, 0));
  };
  return h("table", { class: "heatmap" },
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Портфель")),
      ...years.map((y) => h("th", { scope: "col" }, y)))),
    h("tbody", {}, data.map((d) => h("tr", {}, h("th", { scope: "row" }, d.name),
      ...years.map((y) => cell(d.r.get(y)))))));
}

function tradesTable(r) {
  const rows = r.trades.map((t) => h("tr", {},
    h("td", {}, fmtDate(String(t.date).slice(0, 10))),
    h("td", { class: "mono", title: assetLabel(t.asset) }, t.asset),
    h("td", { class: "num" }, fmtRub(t.amount)),
    h("td", { class: "reason" }, t.reason)));
  const table = h("table", { class: "trades" },
    h("thead", {}, h("tr", {},
      h("th", { scope: "col" }, "Дата"), h("th", { scope: "col" }, "Актив"),
      h("th", { scope: "col", class: "num" }, "Сумма (+ покупка / − продажа)"), h("th", { scope: "col" }, "Причина"))),
    h("tbody", {}, rows));
  return h("div", {},
    h("p", { class: "stats-line" }, `Сделок: ${fmtNum(r.trades.length)} · ребалансировок: ${fmtNum(r.rebalanceDates.length)} · комиссии ${fmtRub(r.costs)} · налог ${fmtRub(r.taxes)}`),
    tableWrap(table, { maxHeight: "380px" }));
}

// ---------------------------------------------------------------- CSV

function csvEquity(lineSeries) {
  const all = [...new Set(lineSeries.flatMap((s) => s.dates))].sort();
  const maps = lineSeries.map((s) => new Map(s.dates.map((d, i) => [d, s.values[i]])));
  const rows = [["Дата", ...lineSeries.map((s) => `${s.name}, руб.`)]];
  for (const d of all) rows.push([d, ...maps.map((m) => csvNum(m.get(d), 2))]);
  downloadCsv("backtest_stoimost.csv", rows);
}

function csvMetrics(cols, reports) {
  const { LABELS_RU } = engine.metrics;
  const rows = [["Показатель", "Ключ", ...cols]];
  rows.push(["Начало", "start", ...cols.map((c) => reports[c].start)]);
  rows.push(["Конец", "end", ...cols.map((c) => reports[c].end)]);
  for (const [k, label] of entries(LABELS_RU)) rows.push([label, k, ...cols.map((c) => csvNum(reports[c][k], 8))]);
  downloadCsv("backtest_pokazateli.csv", rows);
}

function csvTrades(name, r) {
  const rows = [["Портфель", "Дата", "Актив", "Сумма, руб.", "Причина"]];
  for (const t of r.trades) rows.push([name, String(t.date).slice(0, 10), t.asset, csvNum(t.amount, 2), t.reason]);
  downloadCsv("backtest_sdelki.csv", rows);
}

export function rerender() {
  if (last) render();
}
