// «Ребалансировки» (перенос pages/6_Ребалансировки.py; web/CONTRACT.md, раздел B). Контейнер #rebalance-root.
// Расчёты — web/lib/rebalance.js (schedule, driftReport, rebalanceOrders, toIcs) и ledger.js/quotes.js.

import { fact, registerFacts } from "./assistant.js";
import { baseOptions, colorOf, renderChart, themeColors } from "./charts.js";
import { $, downloadBlob, errorNotice, h, notice, put, tableWrap } from "./dom.js";
import { engine, requireEngine, UserError } from "./engine.js";
import { fmtDate, fmtNum, fmtPct, fmtRub, parseNum } from "./format.js";
import { focusMsg, missingText, portfolioSelect, selectedId, storageNote, valuate } from "./page-myportfolio.js";
import { store } from "./portfolio-store.js";
import { href } from "./router.js";

const SRC = "Ребалансировки: расчёт платформы по вашим операциям и котировкам Мосбиржи";
// выполнить fn, когда узел el окажется в странице (вкладки вставляют содержимое после асинхронных расчётов)
const whenIn = (el, fn, n = 300) => { if (el.isConnected) fn(); else if (n) requestAnimationFrame(() => whenIn(el, fn, n - 1)); };
const later = (fn) => setTimeout(fn, 0);
const today = () => new Date().toISOString().slice(0, 10);

let pid = null;
let token = 0;
let facts = [];
let flash = null;
let editRows = null;       // редактор долей: [[тикер, "%"]] — переживает перерисовку, пока не сохранён
let editPid = null;
let useCash = true;
let commPct = "0,05";

registerFacts("/my/rebalance", () => facts);

export function init() {
  store.subscribe(() => { if (!$("#panel-rebalance")?.hidden) draw(); });
}
export function onShow() { draw(); }
export function onGlobalChange() {}
export function rerender() { draw(); }
export const STATIC = true;

const say = (kind, text) => { flash = { kind, text }; };

function draw() {
  const root = $("#rebalance-root");
  if (!root) return;
  const my = ++token;
  facts = [];
  const msg = flash && notice(flash.kind, flash.text);
  flash = null;
  focusMsg(msg);
  const head = [h("div", { class: "panel-head" }, h("h2", {}, "Ребалансировки"),
    h("p", { class: "lead" }, "Ребалансировка возвращает доли бумаг к задуманным: продаёте то, что выросло, и докупаете то, что отстало. Здесь можно задать цели, увидеть отклонения и получить список заявок.")),
  ...storageNote(), msg];
  const pfs = store.portfolios();
  if (!pfs.length) {
    put(root, head, notice("info", "Сначала создайте портфель на странице «Мой портфель»."),
      h("p", {}, h("a", { class: "btn btn-primary", href: href("/my") }, "Перейти в «Мой портфель»")));
    return;
  }
  pid = selectedId();
  const pf = store.portfolio(pid);
  const tx = store.transactions(pid);
  const held = [...new Set(tx.map((t) => t.secid).filter(Boolean))].sort();
  if (editPid !== pid || !editRows) {
    editPid = pid;
    const tgt = Object.keys(pf.target).length ? pf.target : engine.rebalance.equalTarget(held);
    editRows = Object.entries(tgt).map(([k, v]) => [k, fmtNum(v * 100, 2).replace(/\s/g, "").replace(/,?0+$/, "")]);
  }
  const rest = h("div", { class: "rb-rest" });
  put(root, head, h("section", { class: "card my-bar" }, h("div", { class: "my-row" }, portfolioSelect(pid, () => { editRows = null; draw(); }, "Портфель", "pf-pick-rb")),
    pf.last_rebalance ? h("p", { class: "hint" }, `Последняя ребалансировка: ${fmtDate(pf.last_rebalance)}.`) : null),
  ruleCard(pf), rest);
  if (!Object.keys(pf.target).length) {
    put(rest, notice("info", "Задайте целевые доли и сохраните правило."));
    return;
  }
  try { requireEngine(); } catch (e) { return put(rest, errorNotice(e)); }
  if (!engine.rebalance) return put(rest, errorNotice(new UserError("Модуль web/lib/rebalance.js не загрузился — расчёты недоступны.")));
  put(rest, scheduleCard(pf));
  if (!held.length) {
    put(rest, scheduleCard(pf), notice("info", "В портфеле пока нет бумаг — отклонения и заявки появятся после покупок."), doneButton(pf));
    return;
  }
  put(rest, scheduleCard(pf), h("p", { class: "muted", role: "status" }, "Получаю котировки…"));
  valuate(tx, Object.keys(pf.target)).then((v) => { if (my === token) put(rest, scheduleCard(pf), ...analysis(pf, tx, v), doneButton(pf)); })
    .catch((e) => { if (my === token) put(rest, scheduleCard(pf), errorNotice(e)); });
}

// ---------------------------------------------------------------- цели и правило

function ruleCard(pf) {
  const labels = engine.backtest?.REBAL_LABELS || { none: "Без ребалансировки", M: "Ежемесячно", Q: "Ежеквартально", H: "Раз в полгода", A: "Раз в год (конец декабря)" };
  const body = h("tbody");
  const sum = h("p", { class: "hint", id: "rb-sum" });
  const upd = () => {
    const s = [...body.rows].reduce((a, tr) => { const v = parseNum(tr.querySelector("input.rb-pct").value); return a + (Number.isFinite(v) ? v : 0); }, 0);
    sum.textContent = `Сумма: ${fmtNum(s, 1)} %${Math.abs(s - 100) > 0.05 && s > 0 ? " — доли будут пересчитаны так, чтобы в сумме получилось 100 %" : ""}`;
    editRows = [...body.rows].map((tr) => [tr.querySelector("input.rb-tk").value, tr.querySelector("input.rb-pct").value]);
  };
  const addRow = ([tk = "", pc = ""] = []) => {
    const tr = h("tr", {},
      h("td", {}, h("input", { type: "text", class: "rb-tk", value: tk, placeholder: "SBER", "aria-label": "Тикер", autocomplete: "off", oninput: upd })),
      h("td", { class: "num" }, h("input", { type: "text", class: "rb-pct", value: pc, inputmode: "decimal", placeholder: "0", "aria-label": "Целевая доля, %", oninput: upd })),
      h("td", {}, h("button", { type: "button", class: "btn btn-sm", "aria-label": "Убрать строку", onclick: () => { tr.remove(); upd(); } }, "Убрать")));
    body.append(tr);
  };
  editRows.forEach(addRow);
  const rule = h("select", { id: "rb-rule" }, Object.entries(labels).map(([k, v]) => h("option", { value: k, selected: k === (pf.rebalance in labels ? pf.rebalance : "A") }, v)));
  const band = h("input", { type: "text", id: "rb-band", inputmode: "decimal", value: fmtNum((pf.band || 0) * 100, 1).replace(",0", "") });
  const err = h("div");
  const save = () => {
    const target = {};
    for (const tr of body.rows) {
      const tk = tr.querySelector("input.rb-tk").value.trim().toUpperCase();
      if (!tk) continue;
      const v = parseNum(tr.querySelector("input.rb-pct").value || "0");
      if (!Number.isFinite(v) || v < 0) return put(err, notice("error", `Доля для ${tk}: введите неотрицательное число.`));
      target[tk] = (target[tk] || 0) + v;
    }
    const b = parseNum(band.value || "0");
    if (!Number.isFinite(b) || b < 0 || b > 50) return put(err, notice("error", "Коридор — число от 0 до 50 процентных пунктов."));
    store.updatePortfolio(pid, { target: engine.rebalance.normalizeTarget(target), rebalance: rule.value, band: b / 100 });
    editRows = null;
    say("ok", "Правило сохранено.");
    draw();
  };
  upd();
  return h("section", { class: "card" }, h("h3", { class: "card-title" }, "Целевая структура и правило"),
    h("div", { class: "rb-grid" },
      h("div", {}, tableWrap(h("table", { class: "my-table rb-edit" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Тикер"), h("th", { scope: "col", class: "num" }, "Цель, %"), h("th", {}, h("span", { class: "sr-only" }, "Действия")))), body)),
        h("div", { class: "toolbar" }, h("button", { type: "button", class: "btn btn-sm", onclick: () => { addRow(); upd(); body.lastChild.querySelector("input").focus(); } }, "Добавить бумагу"), sum)),
      h("div", { class: "rb-rule" },
        h("div", { class: "field" }, h("label", { for: "rb-rule" }, "Периодичность"), rule),
        h("div", { class: "field" }, h("label", { for: "rb-band" }, "Коридор, п.п."), band),
        h("p", { class: "hint" }, "Коридор — на сколько процентных пунктов доля может отклониться от цели, прежде чем потребуется ребалансировка."),
        h("button", { type: "button", class: "btn btn-primary", onclick: save }, "Сохранить правило"))), err);
}

// ---------------------------------------------------------------- календарь

function scheduleCard(pf) {
  const dates = engine.rebalance.schedule(pf.rebalance, today(), 8);
  const days = (d) => Math.round((Date.parse(d) - Date.parse(today())) / 864e5);
  const body = dates.length
    ? tableWrap(h("table", { class: "my-table" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Дата"), h("th", { scope: "col", class: "num" }, "Через, дней"))),
      h("tbody", {}, dates.map((d) => h("tr", {}, h("td", {}, fmtDate(d)), h("td", { class: "num" }, fmtNum(days(d)))))))) : h("p", { class: "muted" }, "Правило «без ребалансировки» — плановых дат нет.");
  return h("section", { class: "card" }, h("h3", { class: "card-title" }, "Ближайшие даты"), h("div", { class: "rb-grid" }, body,
    dates.length ? h("div", {}, h("button", { type: "button", class: "btn", onclick: () => downloadBlob(`rebalance_${pf.name}.ics`,
      engine.rebalance.toIcs(dates, pf.name, "Проверить отклонение долей и выполнить заявки из платформы"), "text/calendar;charset=utf-8") }, "Добавить в календарь (.ics)"),
    h("p", { class: "hint" }, "Файл открывается в Apple Календаре, Google Calendar, Outlook; напоминание за день.")) : null));
}

// ---------------------------------------------------------------- отклонения и заявки

function analysis(pf, tx, v) {
  const R = engine.rebalance;
  const { rows, cash, quotes } = v;
  const target = pf.target;
  const val = Object.fromEntries(rows.map((r) => [r.secid, r.value]));
  const qty = Object.fromEntries(rows.map((r) => [r.secid, r.qty]));
  // Бумаги без цены нулём не считаем: это дало бы ложные отклонения и заявки.
  const noVal = Object.keys(val).filter((s) => !Number.isFinite(val[s]) && qty[s] > 1e-9);
  const noPx = Object.keys(target).filter((s) => !quotes[s] || !Number.isFinite(quotes[s].price));
  const excluded = [...new Set([...noVal, ...noPx])].sort();
  const names = missingText(excluded, quotes);
  const out = [];
  if (excluded.length) out.push(notice("warn", `Нет цены для: ${names}. Бумаги исключены из расчёта отклонений; заявки не рассчитаны.`));
  const values = {};
  for (const [s, x] of Object.entries(val)) if (!excluded.includes(s)) values[s] = Number.isFinite(x) ? x : 0;
  let tShown = Object.fromEntries(Object.entries(target).filter(([s]) => !excluded.includes(s)));
  let outside = 0;

  const dcard = [h("h3", { class: "card-title" }, "Отклонение от целевых долей")];
  const tNorm = R.normalizeTarget(tShown);
  if (!Object.keys(tNorm).length) dcard.push(notice("info", "Все бумаги целевой структуры без цены — рассчитать отклонения невозможно."));
  else {
    if (excluded.length) {
      tShown = tNorm;
      dcard.push(h("p", { class: "card-sub" }, "Факт и цель показаны в долях оценённой части портфеля (без бумаг без цены и их целей)."));
    }
    const current = R.factShares(Object.entries(values).map(([secid, value]) => ({ secid, value })), cash);
    const dr = R.driftReport(current, tShown, pf.band ?? 0.05);
    outside = dr.filter((r) => r.outOfBand).length;
    const canvas = h("canvas", { role: "img", "aria-label": "Целевые и фактические доли бумаг" });
    dcard.push(h("div", { class: "chart-box short" }, canvas));
    whenIn(canvas, () => drawDrift(canvas, dr));
    dcard.push(notice(outside ? "warn" : "ok", outside ? "Есть активы вне коридора — рекомендуется ребалансировка" : "Все доли в пределах коридора"));
    const th = (t, n) => h("th", { scope: "col", class: n ? "num" : "" }, t);
    dcard.push(tableWrap(h("table", { class: "my-table" }, h("thead", {}, h("tr", {}, th("Бумага"), th("Факт", 1), th("Цель", 1), th("Отклонение, п.п.", 1), th("Отн. отклонение", 1), th("Вне коридора"))),
      h("tbody", {}, dr.map((r) => h("tr", { class: r.outOfBand ? "is-out" : "" }, h("th", { scope: "row" }, r.secid), h("td", { class: "num" }, fmtPct(r.current, 1)),
        h("td", { class: "num" }, fmtPct(r.target, 1)), h("td", { class: "num" }, fmtNum(r.driftPp, 2)), h("td", { class: "num" }, fmtPct(r.relDrift, 1)),
        h("td", {}, r.outOfBand ? "Да" : "Нет")))))));
  }
  out.push(h("section", { class: "card" }, dcard));
  facts = [fact("rb.out", "Бумаг вне коридора", outside, "count", String(outside), SRC),
    fact("rb.band", "Коридор, п.п.", (pf.band ?? 0) * 100, "num", fmtNum((pf.band ?? 0) * 100, 1), SRC)];

  // ---- заявки
  const ocard = [h("h3", { class: "card-title" }, "Заявки для ребалансировки")];
  if (excluded.length) ocard.push(notice("info", `Заявки не рассчитаны: нет цены для ${names}. Без оценки этих бумаг нельзя определить целевые суммы по остальным.`));
  else {
    const cashBox = h("input", { type: "checkbox", id: "rb-cash", checked: useCash, onchange: () => { useCash = cashBox.checked; renderOrders(); } });
    const comm = h("input", { type: "text", id: "rb-comm", inputmode: "decimal", value: commPct, "aria-invalid": "false", onchange: () => { commPct = comm.value; renderOrders(); } });
    const res = h("div");
    const prices = Object.fromEntries(Object.keys({ ...values, ...target }).map((s) => [s, quotes[s]?.price]));
    const lots = Object.fromEntries(Object.keys(prices).map((s) => [s, quotes[s]?.lot]));
    const renderOrders = () => {
      const c = parseNum(comm.value);
      comm.setAttribute("aria-invalid", Number.isFinite(c) && c >= 0 && c <= 1 ? "false" : "true");
      if (!Number.isFinite(c) || c < 0 || c > 1) return put(res, notice("error", "Комиссия брокера — число от 0 до 1 процента."));
      const orders = R.rebalanceOrders(values, target, prices, lots, useCash ? cash : 0, c / 100);
      const tot = R.ordersTotals(orders);
      const th = (t, n) => h("th", { scope: "col", class: n ? "num" : "" }, t);
      put(res, tableWrap(h("table", { class: "my-table" }, h("thead", {}, h("tr", {}, th("Бумага"), th("Действие"), th("Лотов", 1), th("Бумаг", 1), th("Цена, ₽", 1), th("Сумма, ₽", 1),
        th("Текущая стоимость, ₽", 1), th("Целевая стоимость, ₽", 1), th("Доля после", 1), th("Комиссия, ₽", 1))),
      h("tbody", {}, orders.map((o) => h("tr", {}, h("th", { scope: "row" }, o.secid), h("td", { class: o.action === "Купить" ? "pos" : o.action === "Продать" ? "neg" : "" }, o.action),
        h("td", { class: "num" }, fmtNum(o.lots)), h("td", { class: "num" }, fmtNum(o.qty, Number.isInteger(o.qty) ? 0 : 4)), h("td", { class: "num" }, fmtNum(o.price, 2)),
        h("td", { class: "num" }, fmtNum(o.amount, 0)), h("td", { class: "num" }, fmtNum(o.value, 0)), h("td", { class: "num" }, fmtNum(o.targetValue, 0)),
        h("td", { class: "num" }, fmtPct(o.shareAfter, 1)), h("td", { class: "num" }, fmtNum(o.commission, 2))))))),
      h("p", { class: "hint" }, `Покупки ${fmtRub(tot.buy)}, продажи ${fmtRub(tot.sell)}, комиссии ${fmtRub(tot.fees)}. Количество округлено вниз до целых лотов.`));
    };
    ocard.push(h("div", { class: "form-grid rb-opts" },
      h("label", { class: "switch", for: "rb-cash" }, cashBox, h("span", {}, "Распределить свободные деньги")),
      h("div", { class: "field" }, h("label", { for: "rb-comm" }, "Комиссия брокера, %"), comm)), res);
    later(renderOrders);
  }
  out.push(h("section", { class: "card" }, ocard));
  out.push(h("p", { class: "hint" }, "Расчёт по вашим операциям и котировкам Мосбиржи. Не является индивидуальной инвестиционной рекомендацией."));
  return out;
}

function drawDrift(canvas, dr) {
  if (!window.Chart) return;
  renderChart(canvas, () => {
    const t = themeColors();
    const opts = baseOptions({ legend: true });
    opts.plugins.legend.labels = { ...opts.plugins.legend.labels, boxHeight: 10 };
    opts.plugins.tooltip.callbacks = { label: (it) => ` ${it.dataset.label}: ${fmtPct(it.parsed.y, 1)}` };
    opts.scales = {
      x: { grid: { display: false }, border: { color: t.grid }, ticks: { color: t.muted }, title: { display: true, text: "Бумага", color: t.muted } },
      y: { beginAtZero: true, grid: { color: t.grid }, border: { display: false }, ticks: { color: t.muted, callback: (x) => `${fmtNum(x * 100, 0)} %` }, title: { display: true, text: "Доля в портфеле, %", color: t.muted } },
    };
    return { type: "bar", data: { labels: dr.map((r) => r.secid), datasets: [
      { label: "Цель", data: dr.map((r) => r.target), backgroundColor: colorOf("Цель"), borderRadius: 4, maxBarThickness: 32 },
      { label: "Факт", data: dr.map((r) => r.current), backgroundColor: colorOf("Факт"), borderRadius: 4, maxBarThickness: 32 },
    ] }, options: opts };
  });
}

function doneButton(pf) {
  return h("div", { class: "form-actions" }, h("button", { type: "button", class: "btn", onclick: () => {
    store.updatePortfolio(pid, { last_rebalance: today() });
    say("ok", "Дата сохранена.");
    draw();
  } }, "Отметить ребалансировку выполненной"), pf.last_rebalance ? h("span", { class: "status" }, `Сейчас отмечено: ${fmtDate(pf.last_rebalance)}`) : null);
}
