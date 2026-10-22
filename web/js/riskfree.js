// Безрисковая ставка веб-версии: доходность коротких ОФЗ по датам периода.
// Ряд и среднее — web/lib/data.js (riskFreeSeries / riskFreeMean, владелец — api, контракт — web/CONTRACT.md).
// Здесь ставка не считается: только вызов по контракту, кэш на сессию и подпись для пользователя.

import { DATA_BASE } from "./config.js";
import { engine } from "./engine.js";
import { fmtDate, fmtPct } from "./format.js";

export const RF_UNAVAILABLE =
  "Безрисковая ставка недоступна — показатели рассчитаны при нулевой ставке, поэтому Шарп, Сортино, Трейнор, альфа, M² и другие показатели с безрисковой ставкой завышены.";

const memo = new Map();

const OPTS = { base: DATA_BASE };

/**
 * Подпись к расчёту: «Безрисковая ставка — доходность коротких ОФЗ, в среднем X % за период…».
 * splice — дата перехода с RGBEY на RUGBITR1Y (meta.splice_date risk_free.json), first — первая дата ряда.
 */
export function rfCaption(from, mean, { splice = "2011-01-01", first = "2002-12-30" } = {}) {
  if (!Number.isFinite(mean)) return RF_UNAVAILABLE;
  const notes = [];
  if (from && splice && from < splice) notes.push(`до ${splice.slice(0, 4)} — доходность всего рынка ОФЗ`);
  if (from && first && from < first) notes.push("до 2003 — по первому известному значению");   // данные с 30.12.2002
  return `Безрисковая ставка — доходность коротких ОФЗ, в среднем ${fmtPct(mean, 2)} за период` +
    (notes.length ? ` (${notes.join("; ")})` : "") + ".";
}

async function load(from, till) {
  const D = engine.data;
  if (!D || typeof D.riskFreeSeries !== "function" || typeof D.riskFreeMean !== "function") {
    throw new Error("в web/lib/data.js нет riskFreeSeries / riskFreeMean");
  }
  const [series, mean] = await Promise.all([D.riskFreeSeries(from, till, OPTS), D.riskFreeMean(from, till, OPTS)]);
  if (!series || !series.dates?.length) throw new Error("пустой ряд безрисковой ставки");
  let marks = {};
  try {
    const full = typeof D.loadRiskFree === "function" ? await D.loadRiskFree(OPTS) : null;
    if (full) marks = { splice: full.meta?.splice_date || "2011-01-01", first: full.series.dates[0] };
  } catch {
    /* подпись по умолчанию */
  }
  return { rf: series, mean: Number(mean), note: rfCaption(from, Number(mean), marks), ok: true };
}

/**
 * Безрисковая ставка за период [from, till] (даты 'ГГГГ-ММ-ДД').
 * {rf — ряд {dates, values} для computeAll, mean — средняя (для границы и CML), note — подпись, ok}.
 * Источник недоступен — rf = 0, mean = 0, note — понятное сообщение (без исключения).
 */
export async function riskFree(from, till) {
  const key = `${from ?? ""}|${till ?? ""}`;
  if (!memo.has(key)) {
    memo.set(key, load(from, till).catch(() => {
      memo.delete(key);   // повторить при следующем расчёте
      return { rf: 0, mean: 0, note: RF_UNAVAILABLE, ok: false };
    }));
  }
  return memo.get(key);
}

/** Подпись в панели параметров: «Доходность коротких ОФЗ (сейчас X % на дату); в расчётах — по датам периода». */
export async function rfHeaderNote() {
  let now = "сейчас недоступна";
  try {
    const D = engine.data;
    if (D && typeof D.riskFreeSeries === "function") {
      const s = await D.riskFreeSeries(null, null, OPTS);
      for (let i = (s?.values?.length ?? 0) - 1; i >= 0; i--) {
        if (Number.isFinite(s.values[i])) {
          now = `сейчас ${fmtPct(s.values[i], 2)} на ${fmtDate(s.dates[i])}`;
          break;
        }
      }
    }
  } catch {
    /* источник недоступен — «недоступна» */
  }
  return `Доходность коротких ОФЗ (${now}); в расчётах — по датам периода.`;
}
