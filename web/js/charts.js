// Графики (Chart.js из web/vendor). Палитра фиксирована, цвет закреплён за сущностью,
// бенчмарк — серый пунктир, линии 2 px, одна ось Y, подписи осей с единицами.

import { fmtDate, fmtMonth, fmtNum, fmtPct, fmtRub } from "./format.js";

const PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
const PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const BENCH_LIGHT = "#6f6e69";
const BENCH_DARK = "#a3a29c";

// Последовательная синяя шкала (для облака портфелей по Шарпу)
const SEQ_BLUE_LIGHT = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const SEQ_BLUE_DARK = ["#123a6b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"];

const darkQuery = matchMedia("(prefers-color-scheme: dark)");
export const isDark = () => darkQuery.matches;
export const palette = () => (isDark() ? PALETTE_DARK : PALETTE_LIGHT);
export const benchColor = () => (isDark() ? BENCH_DARK : BENCH_LIGHT);
export const seqBlue = () => (isDark() ? SEQ_BLUE_DARK : SEQ_BLUE_LIGHT);

// ---------------------------------------------------------------- цвет за сущностью

const registry = new Map();

/** Закрепить цвета за сущностями в заданном порядке (вызывается один раз при старте). */
export function registerEntities(names) {
  for (const n of names) if (!registry.has(n)) registry.set(n, registry.size);
}

export function colorOf(name, { bench = false } = {}) {
  if (bench) return benchColor();
  if (!registry.has(name)) registry.set(name, registry.size);
  return palette()[registry.get(name) % 8];
}

// ---------------------------------------------------------------- тема

export function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  return { text: v("--text"), muted: v("--text-muted"), grid: v("--grid"), surface: v("--surface") };
}

const live = new Map(); // canvas → {chart, factory}

/** Отрисовать график; при смене темы он будет пересоздан с новыми цветами. */
export function renderChart(canvas, factory) {
  live.get(canvas)?.chart.destroy();
  const chart = new window.Chart(canvas, factory());
  live.set(canvas, { chart, factory });
  return chart;
}

export function destroyChart(canvas) {
  live.get(canvas)?.chart.destroy();
  live.delete(canvas);
}

darkQuery.addEventListener("change", () => {
  for (const [canvas, rec] of live) {
    if (!canvas.isConnected) {
      rec.chart.destroy();
      live.delete(canvas);
      continue;
    }
    rec.chart.destroy();
    rec.chart = new window.Chart(canvas, rec.factory());
  }
});

export function applyChartDefaults() {
  const C = window.Chart;
  if (!C) return false;
  C.defaults.font.family = getComputedStyle(document.body).fontFamily;
  C.defaults.font.size = 12;
  C.defaults.animation = false;
  C.defaults.responsive = true;
  C.defaults.maintainAspectRatio = false;
  C.defaults.locale = "ru-RU";
  return true;
}

function axisTitle(text, t) {
  return { display: !!text, text, color: t.muted, font: { size: 12, weight: "500" } };
}

export function baseOptions({ legend = true } = {}) {
  const t = themeColors();
  return {
    color: t.text,
    plugins: {
      legend: {
        display: legend,
        position: "top",
        align: "start",
        labels: { color: t.text, boxWidth: 18, boxHeight: 2, padding: 12, font: { size: 12 } },
      },
      tooltip: {
        backgroundColor: isDark() ? "rgba(30,32,36,.96)" : "rgba(255,255,255,.97)",
        titleColor: t.text,
        bodyColor: t.text,
        borderColor: t.grid,
        borderWidth: 1,
        padding: 10,
        boxPadding: 4,
        usePointStyle: false,
      },
    },
  };
}

/** На узком экране легенда Chart.js съедает высоту графика — показываем компактную HTML-легенду. */
function htmlLegend(canvas, datasets) {
  const box = canvas.parentElement;
  let el = box.nextElementSibling;
  if (!el || !el.classList.contains("html-legend")) {
    if (!datasets.length) return;
    el = document.createElement("ul");
    el.className = "html-legend";
    el.setAttribute("aria-label", "Легенда");
    box.after(el);
  }
  el.replaceChildren(...datasets.map((d) => {
    const li = document.createElement("li");
    const sw = document.createElement("span");
    sw.className = "lg-line" + (d.borderDash.length ? " dashed" : "");
    sw.style.borderColor = d.borderColor;
    li.append(sw, d.label);
    return li;
  }));
  el.hidden = !datasets.length;
}

// ---------------------------------------------------------------- линейный график по датам

const Y_FORMATS = {
  rub: { tick: (v) => fmtNum(v / 1e6, Math.abs(v) < 1e7 ? 1 : 0), tip: fmtRub },
  pct: { tick: (v) => `${fmtNum(v * 100, 0)} %`, tip: (v) => fmtPct(v, 1) },
  num: { tick: (v) => fmtNum(v, 0), tip: (v) => fmtNum(v, 1) },
};

/**
 * series: [{name, dates, values, bench?}] — общий календарь строится объединением дат.
 * yFormat: rub | pct | num; yTitle — с единицами («Стоимость, млн ₽»).
 */
export function lineChart(canvas, { series, yTitle, yFormat = "num", log = false, fill = false }) {
  const all = new Set();
  series.forEach((s) => s.dates.forEach((d) => all.add(d)));
  const labels = [...all].sort();
  const fmt = Y_FORMATS[yFormat];
  return renderChart(canvas, () => {
    const t = themeColors();
    const datasets = series.map((s) => {
      const m = new Map(s.dates.map((d, i) => [d, s.values[i]]));
      const color = colorOf(s.name, { bench: s.bench });
      return {
        label: s.name,
        data: labels.map((d) => (m.has(d) ? m.get(d) : null)),
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        borderDash: s.bench ? [6, 4] : [],
        pointRadius: 0,
        pointHoverRadius: 3,
        pointHitRadius: 4,
        spanGaps: true,
        tension: 0,
        fill: fill && !s.bench ? "origin" : false,
      };
    });
    const narrow = window.innerWidth < 600;
    htmlLegend(canvas, narrow && series.length >= 2 ? datasets : []);
    const opts = baseOptions({ legend: series.length >= 2 && !narrow });
    opts.interaction = { mode: "index", intersect: false };
    opts.plugins.tooltip.callbacks = {
      title: (items) => fmtDate(items[0]?.label),
      label: (it) => ` ${it.dataset.label}: ${fmt.tip(it.parsed.y)}`,
    };
    opts.scales = {
      x: {
        type: "category",
        title: axisTitle("Дата", t),
        grid: { display: false },
        border: { color: t.grid },
        ticks: {
          color: t.muted,
          autoSkip: true,
          maxTicksLimit: 8,
          maxRotation: 0,
          callback(v) {
            return fmtMonth(this.getLabelForValue(v));
          },
        },
      },
      y: {
        type: log ? "logarithmic" : "linear",
        title: axisTitle(yTitle, t),
        grid: { color: t.grid },
        border: { display: false },
        ticks: {
          color: t.muted,
          maxTicksLimit: 8,
          callback: (v) => {
            const x = Number(v);
            if (log) {
              // на лог. шкале подписываем только «круглые» значения 1–2–5
              const m = x / 10 ** Math.floor(Math.log10(Math.abs(x)));
              if (![1, 2, 5].includes(Math.round(m * 1000) / 1000)) return "";
            }
            return fmt.tick(x);
          },
        },
      },
    };
    return { type: "line", data: { labels, datasets }, options: opts };
  });
}
