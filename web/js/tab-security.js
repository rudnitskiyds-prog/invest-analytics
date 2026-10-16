// Вкладка «Коэффициенты бумаги»: тикер/индекс против бенчмарка. Расчёт — computeAll из web/lib.

import { colorOf, destroyChart, lineChart } from "./charts.js";
import { $, errorNotice, h, notice, tableWrap, withBusy } from "./dom.js";
import { assetLabel, engine, requireEngine, UserError } from "./engine.js";
import { bindValue, globals, readDates, FREQ_LABELS } from "./fields.js";
import { fmtDate, fmtPct } from "./format.js";
import { getMany } from "./source.js";
import * as S from "./state.js";
import { buildMetricsTable } from "./tab-backtest.js";

let last = null;
let running = false;

export function init() {
  const key = bindValue("sec-key", "sec");
  key.addEventListener("input", () => S.set({ sec: key.value.trim().toUpperCase() }));
  bindValue("sec-from", "sfrom");
  bindValue("sec-till", "still");
  $("#sec-form").addEventListener("submit", (e) => {
    e.preventDefault();
    run();
  });
}

export function onShow() {
  if (last || running || !engine.ok) return;
  if (S.isDemo() || S.get("sc") === "1") run();
}

export function onGlobalChange() {
  if (last && !running) run();
}

async function run() {
  if (running) return;
  running = true;
  const out = $("#sec-out");
  try {
    await withBusy($("#sec-run"), $("#sec-status"), async (status) => {
      try {
        const E = requireEngine();
        const g = globals();
        const { from, till } = readDates("sfrom", "still");
        const key = S.get("sec").trim().toUpperCase();
        if (!key) throw new UserError("Введите тикер или ключ индекса, например MCFTR или SBER.");
        const keys = key === g.bench ? [key] : [key, g.bench];
        const { series, errors } = await getMany(keys, from, till, (d, t) => status(`Загрузка рядов: ${d} из ${t}…`));
        if (!series[key]) throw errors[key];
        const warn = [];
        if (g.freqForced) warn.push("Демо-данные месячные: показатели рассчитаны по месячным доходностям.");
        let s = series[key];
        let b = series[g.bench] || null;
        if (!b) warn.push(`Бенчмарк ${g.bench}: ${errors[g.bench]?.message || "нет данных"} Показатели относительно рынка не рассчитаны.`);
        if (b && key !== g.bench) {
          // общий период: обе линии с одной даты (база = 100)
          const f = E.data.sliceFrame(E.data.alignFrame({ [key]: s, [g.bench]: b }, { mode: "common" }), from, till);
          if (f.dates.length < 3) throw new UserError(`${key} и ${g.bench}: нет общего периода данных.`);
          s = { dates: f.dates, values: f.cols[key] };
          b = { dates: f.dates, values: f.cols[g.bench] };
        } else if (key === g.bench) {
          b = s;
        }
        if (s.dates.length < 3) throw new UserError(`${key}: слишком мало данных за выбранный период.`);
        last = { key, s, b, g, from, till, warn, offerDemo: !!errors[g.bench]?.offerDemo };
        S.set({ sc: "1" });
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

function render() {
  const E = engine;
  const { key, s, b, g, warn, offerDemo } = last;
  const out = $("#sec-out");
  out.querySelectorAll("canvas").forEach(destroyChart);
  out.replaceChildren();
  warn.forEach((t) => out.append(notice("warn", t, { offerDemo })));
  const same = key === g.bench;

  const base100 = (x) => E.metrics.rebase(x, 100);
  const lines = [{ name: key, ...base100(s), bench: same }];
  if (b && !same) lines.push({ name: g.bench, ...base100(b), bench: true });

  out.append(notice("info", `${assetLabel(key)}. Период: ${fmtDate(s.dates[0])} — ${fmtDate(s.dates.at(-1))}.` +
    (S.isDemo() ? "" : " Для акций и паёв — цена закрытия без дивидендов; индексы полной доходности (…TR) учитывают дивиденды и купоны.")));

  const priceCanvas = h("canvas", { role: "img", "aria-label": `Цена ${key} против бенчмарка, база 100` });
  out.append(h("section", { class: "card" },
    h("h3", { class: "card-title" }, same ? `${key}: динамика` : `${key} против ${g.bench}`),
    h("p", { class: "card-sub" }, "Значения приведены к 100 на начальную дату"),
    h("div", { class: "chart-box" }, priceCanvas)));
  lineChart(priceCanvas, { series: lines, yTitle: "Значение, база = 100", yFormat: "num" });

  const ddCanvas = h("canvas", { role: "img", "aria-label": "Просадки" });
  out.append(h("section", { class: "card" },
    h("h3", { class: "card-title" }, "Просадки"),
    h("p", { class: "card-sub" }, "Снижение от предыдущего максимума"),
    h("div", { class: "chart-box short" }, ddCanvas)));
  lineChart(ddCanvas, {
    series: lines.map((l) => ({ ...E.metrics.drawdownSeries({ dates: l.dates, values: l.values }), name: l.name, bench: l.bench })),
    yTitle: "Просадка, %", yFormat: "pct",
  });

  const reports = { [key]: E.metrics.computeAll(s, b, g.rf, g.freq) };
  if (b && !same) reports[g.bench] = E.metrics.computeAll(b, b, g.rf, g.freq);
  const cols = Object.keys(reports);
  out.append(h("section", { class: "card" },
    h("h3", { class: "card-title" }, "Показатели эффективности"),
    h("p", { class: "card-sub" }, `Rf = ${fmtPct(g.rf, 2)}, доходности ${FREQ_LABELS[g.freq]}, бенчмарк ${g.bench}.`),
    tableWrap(buildMetricsTable(cols, reports, (c) => colorOf(c, { bench: c === g.bench })))));
}

export function rerender() {
  if (last) render();
}
