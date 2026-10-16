// Загрузка рядов: живые данные (ISS / файлы ЦБ) или демо-фикстуры. Расчётов здесь нет —
// только вызовы web/lib/data.js и перевод ошибок на понятный язык.

import { DATA_BASE, DEMO_BASE } from "./config.js";
import { requireEngine, UserError } from "./engine.js";
import { isDemo } from "./state.js";

let demoFrame = null;

async function getDemoFrame() {
  const { data } = requireEngine();
  demoFrame ??= data.loadDemoFrame({ base: DEMO_BASE }).catch((e) => {
    demoFrame = null;
    throw new UserError(
      "Демо-данные не найдены. Демо-режим работает только при локальном запуске сервера " +
        `из корня репозитория (python3 -m http.server). Подробности: ${e.message}`,
    );
  });
  return demoFrame;
}

function columnSeries(frame, key) {
  const col = frame.cols[key];
  if (!col) return null;
  const dates = [];
  const values = [];
  col.forEach((v, i) => {
    if (typeof v === "number" && Number.isFinite(v)) {
      dates.push(frame.dates[i]);
      values.push(v);
    }
  });
  return dates.length ? { dates, values } : null;
}

function clip(s, from, till) {
  const dates = [];
  const values = [];
  s.dates.forEach((d, i) => {
    if ((!from || d >= from) && (!till || d <= till)) {
      dates.push(d);
      values.push(s.values[i]);
    }
  });
  return { dates, values };
}

export async function demoKeys() {
  const f = await getDemoFrame();
  const keys = Object.keys(f.cols);
  if (!keys.includes("CORP_CHAIN") && keys.includes("RUCBITR") && keys.includes("RUCBTRNS")) {
    keys.push("CORP_CHAIN");
  }
  return keys;
}

function humanize(e) {
  const m = String(e?.message || e);
  if (/failed to fetch|networkerror|load failed|network/i.test(m)) {
    return "нет связи с источником (сеть недоступна или запрос заблокирован)";
  }
  if (/\b404\b/.test(m)) return "источник не нашёл данные (404)";
  if (/\b5\d\d\b/.test(m)) return "источник временно недоступен";
  return m;
}

/** Ряд актива или тикера ISS → Series. */
export async function getSeries(key, from, till) {
  const { data } = requireEngine();
  if (isDemo()) {
    const f = await getDemoFrame();
    let s = columnSeries(f, key);
    if (!s && key === "CORP_CHAIN") {
      const a = columnSeries(f, "RUCBITR");
      const b = columnSeries(f, "RUCBTRNS");
      if (a && b) s = data.chain(a, b, "2018-12-29");
    }
    if (!s) {
      const avail = (await demoKeys()).join(", ");
      throw new UserError(`${key}: нет в демо-данных. В демо доступны: ${avail}.`);
    }
    return clip(s, from, till);
  }
  try {
    const s = await data.loadAsset(key, from, till, { dataBase: DATA_BASE });
    if (!s || !s.dates || !s.dates.length) {
      throw new UserError(`${key}: источник вернул пустой ряд за ${from} — ${till}. Проверьте тикер и период.`);
    }
    return s;
  } catch (e) {
    if (e instanceof UserError) throw e;
    throw new UserError(`${key}: не удалось загрузить данные — ${humanize(e)}.`, { offerDemo: true });
  }
}

/**
 * Несколько рядов параллельно (не более 4 запросов одновременно, чтобы не перегружать ISS).
 * Возвращает {series: {key: Series}, errors: {key: UserError}}.
 */
export async function getMany(keys, from, till, onProgress = () => {}) {
  const series = {};
  const errors = {};
  let done = 0;
  const queue = [...new Set(keys)];
  const total = queue.length;
  onProgress(0, total);
  async function worker() {
    while (queue.length) {
      const k = queue.shift();
      try {
        series[k] = await getSeries(k, from, till);
      } catch (e) {
        errors[k] = e instanceof UserError ? e : new UserError(`${k}: ${humanize(e)}`, { offerDemo: true });
      }
      onProgress(++done, total);
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, total) }, worker));
  return { series, errors };
}
