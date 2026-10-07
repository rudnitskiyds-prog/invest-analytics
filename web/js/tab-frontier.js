// Вкладка «Граница Марковица». Оценка входов и оптимизация — web/lib/frontier.js.

import { benchColor, destroyChart, palette, renderChart, seqBlue, themeColors, baseOptions } from "./charts.js";
import { $, errorNotice, h, nextFrame, notice, tableWrap, withBusy } from "./dom.js";
import { assetLabel, catalogList, engine, requireEngine, UserError } from "./engine.js";
import { bindNumber, bindValue, globals, readDates, readNumber } from "./fields.js";
import { fmtCoef, fmtDate, fmtNum, fmtPct } from "./format.js";
import { getMany } from "./source.js";
import * as S from "./state.js";

let last = null;
let running = false;
let toBacktest = () => {};

export function init(opts = {}) {
  toBacktest = opts.toBacktest || toBacktest;
  bindValue("fr-extra", "fx");
  bindValue("fr-from", "ffrom");
  bindValue("fr-till", "ftill");
  const freqSel = bindValue("fr-freq", "ffreq");
  bindValue("fr-mu", "mu");
  bindNumber("fr-wmin", "wmin", { min: 0, max: 50 });
  bindNumber("fr-wmax", "wmax", { min: 5, max: 100 });
  bindNumber("fr-nr", "nr", { min: 0, max: 30000, digits: 0 });
  if (S.isDemo()) {
    for (const o of freqSel.options) if (o.value !== "M") { o.disabled = true; o.textContent += " — нет в демо"; }
    freqSel.value = "M";
  }

  $("#fr-form").addEventListener("submit", (e) => {
    e.preventDefault();
    run();
  });

  const box = $("#fr-assets");
  if (!engine.ok) {
    box.replaceChildren(notice("error", "Каталог активов недоступен: модули расчёта не загрузились."));
    return;
  }
  const picked = new Set(S.get("fa").split(",").filter(Boolean));
  box.replaceChildren(
    ...catalogList().map((a) => {
      const cb = h("input", { type: "checkbox", value: a.key, checked: picked.has(a.key) });
      cb.addEventListener("change", () => {
        const keys = [...box.querySelectorAll("input:checked")].map((x) => x.value);
        S.set({ fa: keys.join(",") });
        syncRuoniaHint();
      });
      return h("label", { class: "chip", title: a.name }, cb, h("span", { class: "chip-key" }, a.key),
        h("span", { class: "chip-name" }, shortName(a.name)));
    }),
  );
  syncRuoniaHint();
}

function shortName(n) {
  return n.length > 34 ? `${n.slice(0, 32)}…` : n;
}

function syncRuoniaHint() {
  const on = S.get("fa").split(",").includes("RUONIA");
  $("#fr-ruonia-hint").classList.toggle("notice", on);
  $("#fr-ruonia-hint").classList.toggle("notice-warn", on);
}

export function onShow() {
  if (last || running || !engine.ok) return;
  if (S.isDemo() || S.get("fr") === "1") run();
}

export function onGlobalChange() {
  if (last && !running) run();
}

/** Веса из контракта: массив (по порядку активов) или объект {ключ: доля}. */
function weightsObj(w, assets) {
  if (!w) return {};
  if (Array.isArray(w) || ArrayBuffer.isView(w)) return Object.fromEntries(assets.map((k, i) => [k, Number(w[i])]));
  return { ...w };
}

async function run() {
  if (running) return;
  running = true;
  const out = $("#fr-out");
  try {
    await withBusy($("#fr-run"), $("#fr-status"), async (status) => {
      try {
        const E = requireEngine();
        const g = globals();
        const { from, till } = readDates("ffrom", "ftill");
        const wMin = readNumber("wmin", "Мин. доля", { min: 0, max: 50 }) / 100;
        const wMax = readNumber("wmax", "Макс. доля", { min: 5, max: 100 }) / 100;
        const nRandom = Math.round(readNumber("nr", "Случайных портфелей", { min: 0, max: 30000 }));
        let freq = S.get("ffreq");
        if (S.isDemo()) freq = "M";
        const muMethod = S.get("mu");
        const extra = S.get("fx").split(/[,;\s]+/).map((t) => t.trim().toUpperCase()).filter(Boolean);
        const keys = [...new Set([...S.get("fa").split(",").filter(Boolean), ...extra])];
        if (keys.length < 2) throw new UserError("Нужно минимум 2 актива.");
        if (wMin * keys.length > 1 + 1e-9 || wMax * keys.length < 1 - 1e-9) {
          throw new UserError(`Ограничения на доли несовместимы с числом активов (${keys.length}): ` +
            "мин. доля × число активов должна быть не больше 100 %, макс. доля × число активов — не меньше 100 %.");
        }

        const { series, errors } = await getMany(keys, from, till, (d, t) => status(`Загрузка рядов: ${d} из ${t}…`));
        const warnings = Object.values(errors).map((e) => ({ text: e.message, offerDemo: e.offerDemo }));
        const ok = keys.filter((k) => series[k]);
        if (ok.length < 2) {
          out.replaceChildren(notice("error", "Недостаточно данных: загружено меньше двух активов.",
            { offerDemo: warnings.some((w) => w.offerDemo) }), ...warnings.map((w) => notice("warn", w.text, w)));
          last = null;
          return;
        }
        status("Оптимизация…");
        await nextFrame();
        let frame = E.data.alignFrame(Object.fromEntries(ok.map((k) => [k, series[k]])), { mode: "common" });
        frame = E.data.sliceFrame(frame, from, till);
        if (frame.dates.length < 6) throw new UserError("Слишком короткий общий период данных для оценки ковариаций.");
        const youngest = ok.reduce((a, k) => (series[k].dates[0] > series[a].dates[0] ? k : a), ok[0]);
        const inputs = E.frontier.estimateInputs(frame, { freq, rf: g.rf, muMethod });
        const fr = E.frontier.efficientFrontier(inputs, { nPoints: 50, wMin, wMax, nRandom });
        last = { fr, inputs, frame, youngest, warnings, g, freq };
        S.set({ fr: "1" });
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

function seqColor(t) {
  const sc = seqBlue();
  const x = Math.max(0, Math.min(1, t)) * (sc.length - 1);
  const i = Math.min(sc.length - 2, Math.floor(x));
  const f = x - i;
  const p = (c) => [1, 3, 5].map((k) => parseInt(c.slice(k, k + 2), 16));
  const a = p(sc[i]);
  const b = p(sc[i + 1]);
  return `rgba(${a.map((v, j) => Math.round(v + (b[j] - v) * f)).join(",")},0.7)`;
}

const labelPlugin = {
  id: "pointLabels",
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    const t = themeColors();
    chart.data.datasets.forEach((ds, di) => {
      if (!ds.pointLabels || !chart.isDatasetVisible(di)) return;
      const meta = chart.getDatasetMeta(di);
      ctx.save();
      ctx.font = `600 12px ${getComputedStyle(document.body).fontFamily}`;
      ctx.textAlign = ds.labelAlign || "center";
      ctx.textBaseline = "bottom";
      ctx.lineWidth = 3;
      ctx.strokeStyle = t.surface;
      ctx.fillStyle = t.text;
      meta.data.forEach((pt, i) => {
        const text = ds.pointLabels[i];
        if (!text) return;
        const wdt = ctx.measureText(text).width;
        const align = ds.labelAlign || "center";
        // левый край подписи; подпись не выходит за пределы холста
        let left = align === "right" ? pt.x - 10 - wdt : align === "left" ? pt.x + 10 : pt.x - wdt / 2;
        left = Math.max(2, Math.min(chart.width - wdt - 2, left));
        const y = pt.y - (align !== "center" ? -4 : 9);
        ctx.textAlign = "left";
        ctx.strokeText(text, left, y);
        ctx.fillText(text, left, y);
      });
      ctx.restore();
    });
  },
};

function render() {
  const { fr, inputs, frame, youngest, warnings, g, freq } = last;
  const out = $("#fr-out");
  out.querySelectorAll("canvas").forEach(destroyChart);
  out.replaceChildren();
  warnings.forEach((w) => out.append(notice("warn", w.text, w)));
  const assets = inputs.assets || fr.assets.map((a) => a.key);

  out.append(notice("info",
    `Общий период данных: ${fmtDate(frame.dates[0])} — ${fmtDate(frame.dates.at(-1))} ` +
    `(ограничен самым «молодым» активом: ${youngest}). Rf = ${fmtPct(g.rf, 2)}; ` +
    `доходности ${({ M: "месячные", W: "недельные", D: "дневные" })[freq]}.`));

  const front = [...fr.frontier].sort((a, b) => a.vol - b.vol);
  const mv = fr.minVar;
  const ms = fr.maxSharpe;
  const vols = [...fr.random.map((p) => p.vol), ...fr.assets.map((a) => a.vol), ...front.map((p) => p.vol)];
  const xmax = Math.max(...vols) * 1.05;
  const sh = fr.random.map((p) => p.sharpe).filter(Number.isFinite);
  const sMin = sh.length ? Math.min(...sh) : 0;
  const sMax = sh.length ? Math.max(...sh) : 1;

  const canvas = h("canvas", { role: "img", "aria-label": "Граница эффективности: облако случайных портфелей, граница, CML, оптимальные портфели и активы" });
  const pct = (v) => `${fmtNum(v * 100, 0)} %`;
  renderChart(canvas, () => {
    const t = themeColors();
    const P = palette();
    const opts = baseOptions({ legend: true });
    opts.interaction = { mode: "nearest", intersect: true };
    opts.plugins.legend.labels.usePointStyle = true;
    opts.plugins.legend.labels.boxHeight = 8;
    if (window.innerWidth < 600) Object.assign(opts.plugins.legend.labels, { font: { size: 11 }, padding: 6, boxWidth: 8 });
    opts.plugins.tooltip.callbacks = {
      title: (items) => items[0]?.dataset.label,
      label: (it) => {
        const r = it.raw;
        const name = it.dataset.pointLabels?.[it.dataIndex];
        const base = `σ ${fmtPct(r.x)} · r ${fmtPct(r.y)}`;
        const s = Number.isFinite(r.s) ? ` · Шарп ${fmtCoef(r.s)}` : "";
        return ` ${name && it.dataset.isAssets ? `${name}: ` : ""}${base}${s}`;
      },
    };
    opts.scales = {
      x: {
        type: "linear", min: 0, max: xmax,
        title: { display: true, text: "Волатильность (σ), % годовых", color: t.muted },
        grid: { color: t.grid }, border: { color: t.grid },
        ticks: { color: t.muted, callback: (v) => pct(v), maxTicksLimit: 8 },
      },
      y: {
        type: "linear",
        title: { display: true, text: "Ожидаемая доходность, % годовых", color: t.muted },
        grid: { color: t.grid }, border: { display: false },
        ticks: { color: t.muted, callback: (v) => pct(v), maxTicksLimit: 8 },
      },
    };
    const white = t.surface;
    return {
      type: "scatter",
      data: {
        datasets: [
          {
            label: "Мин. дисперсия", data: [{ x: mv.vol, y: mv.ret, s: mv.sharpe }],
            pointStyle: "rectRot", pointRadius: 9, pointHoverRadius: 10, backgroundColor: P[2],
            borderColor: white, borderWidth: 2, pointLabels: ["Мин. дисперсия"], labelAlign: "right", order: 0,
          },
          {
            label: "Макс. Шарп", data: [{ x: ms.vol, y: ms.ret, s: ms.sharpe }],
            pointStyle: "triangle", pointRadius: 10, pointHoverRadius: 11, backgroundColor: P[7],
            borderColor: white, borderWidth: 2, pointLabels: ["Макс. Шарп"], order: 0,
          },
          {
            label: "Активы", data: fr.assets.map((a) => ({ x: a.vol, y: a.ret, s: a.sharpe })),
            pointRadius: 6, pointHoverRadius: 7, backgroundColor: P[6], borderColor: white, borderWidth: 2,
            pointLabels: fr.assets.map((a) => a.key), isAssets: true, order: 1,
          },
          {
            label: "Эффективная граница", data: front.map((p) => ({ x: p.vol, y: p.ret, s: p.sharpe })),
            showLine: true, borderColor: P[1], backgroundColor: P[1], borderWidth: 2, pointStyle: "line",
            pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 5, order: 2,
          },
          {
            label: "CML", data: [{ x: 0, y: g.rf }, { x: xmax, y: g.rf + ms.sharpe * xmax }],
            showLine: true, borderColor: benchColor(), backgroundColor: benchColor(), borderWidth: 2, pointStyle: "line",
            borderDash: [6, 4], pointRadius: 0, pointHitRadius: 0, pointHoverRadius: 0, order: 3,
          },
          {
            label: "Случайные портфели", data: fr.random.map((p) => ({ x: p.vol, y: p.ret, s: p.sharpe })),
            pointRadius: 2.2, pointHoverRadius: 4, borderWidth: 0,
            backgroundColor: fr.random.map((p) => seqColor((p.sharpe - sMin) / (sMax - sMin || 1))),
            order: 4,
          },
        ],
      },
      options: opts,
      plugins: [labelPlugin],
    };
  });
  const sc = seqBlue();
  out.append(h("section", { class: "card" },
    h("h3", { class: "card-title" }, "Граница эффективности"),
    h("p", { class: "card-sub" }, `Случайных портфелей: ${fmtNum(fr.random.length)}; CML — касательная из безрисковой ставки через портфель макс. Шарпа`),
    h("div", { class: "chart-box tall" }, canvas),
    fr.random.length ? h("div", { class: "legend-scale" },
      h("span", {}, "Цвет облака — коэф. Шарпа:"),
      h("span", {}, fmtCoef(sMin, 2)),
      h("span", { class: "bar", style: { background: `linear-gradient(90deg, ${sc.join(",")})` }, "aria-hidden": "true" }),
      h("span", {}, fmtCoef(sMax, 2))) : null));

  // --- два оптимальных портфеля
  const card = (title, pt) => {
    const w = weightsObj(pt.w, assets);
    return h("section", { class: "card" },
      h("h3", { class: "card-title" }, title),
      h("p", { class: "stats-line" }, "Доходность ", h("b", {}, fmtPct(pt.ret)), " · σ ", h("b", {}, fmtPct(pt.vol)),
        " · Шарп ", h("b", {}, fmtCoef(pt.sharpe))),
      weightsList(w),
      h("div", { class: "form-actions", style: { marginTop: "12px" } },
        h("button", { type: "button", class: "btn btn-sm", onclick: () => toBacktest(w) }, "В бэктест")));
  };
  out.append(h("div", { class: "grid-2" },
    card("Портфель минимальной дисперсии", mv),
    card("Касательный портфель (макс. Шарп)", ms)));

  // --- выбор по целевой волатильности
  if (front.length) {
    const vmin = front[0].vol;
    const vmax = front.at(-1).vol;
    const slider = h("input", {
      type: "range", id: "fr-target", min: String(Math.floor(vmin * 1000)), max: String(Math.ceil(vmax * 1000)),
      step: "1", value: String(Math.round(Math.min(Math.max(ms.vol, vmin), vmax) * 1000)),
      "aria-describedby": "fr-target-out",
    });
    const output = h("output", { id: "fr-target-out", for: "fr-target" });
    const res = h("div");
    const update = () => {
      const target = Number(slider.value) / 1000;
      output.textContent = fmtPct(target);
      let best = front[0];
      for (const p of front) if (Math.abs(p.vol - target) < Math.abs(best.vol - target)) best = p;
      const w = weightsObj(best.w, assets);
      res.replaceChildren(
        h("p", { class: "stats-line" }, "Ближайший портфель на границе: доходность ", h("b", {}, fmtPct(best.ret)),
          " · σ ", h("b", {}, fmtPct(best.vol)), " · Шарп ", h("b", {}, fmtCoef(best.sharpe))),
        weightsList(w),
        h("div", { class: "form-actions", style: { marginTop: "12px" } },
          h("button", { type: "button", class: "btn btn-primary btn-sm", onclick: () => toBacktest(w) }, "В бэктест")));
    };
    slider.addEventListener("input", update);
    out.append(h("section", { class: "card" },
      h("h3", { class: "card-title" }, "Портфель на границе по целевому риску"),
      h("div", { class: "field" },
        h("label", { for: "fr-target" }, "Целевая волатильность, % годовых"),
        h("div", { class: "slider-row" }, slider, output)),
      res));
    update();
  }

  // --- входные параметры
  out.append(h("section", { class: "card" },
    h("h3", { class: "card-title" }, "Входные параметры по активам"),
    h("p", { class: "card-sub" }, "Годовые оценки по историческим доходностям выбранной частоты"),
    tableWrap(h("table", {},
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Актив"),
        h("th", { scope: "col", class: "num" }, "Ожид. доходность"), h("th", { scope: "col", class: "num" }, "Волатильность"),
        h("th", { scope: "col", class: "num" }, "Шарп"))),
      h("tbody", {}, fr.assets.map((a) => h("tr", {},
        h("td", {}, assetLabel(a.key)), h("td", { class: "num" }, fmtPct(a.ret)),
        h("td", { class: "num" }, fmtPct(a.vol)), h("td", { class: "num" }, fmtCoef(a.sharpe)))))))));
}

function weightsList(w) {
  const rows = Object.entries(w).filter(([, v]) => v > 1e-4).sort((a, b) => b[1] - a[1]);
  return h("div", { class: "weights-list" }, rows.map(([k, v]) => h("div", { class: "wrow" },
    h("span", { class: "wkey", title: assetLabel(k) }, k),
    h("span", { class: "wbar", "aria-hidden": "true" }, h("span", { style: { width: `${Math.min(100, v * 100)}%` } })),
    h("span", { class: "wval" }, fmtPct(v)))));
}

export function rerender() {
  if (last) render();
}
