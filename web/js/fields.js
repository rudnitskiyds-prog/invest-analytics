// Привязка полей формы к состоянию в URL и проверка ввода.

import { fmtNum, parseNum } from "./format.js";
import { $ } from "./dom.js";
import * as S from "./state.js";
import { UserError } from "./engine.js";

/**
 * Числовое текстовое поле (русский ввод: «1 000 000», «7,86»).
 * opts: {min, max, digits — для переформатирования при потере фокуса, onChange}
 */
export function bindNumber(id, key, { min = -Infinity, max = Infinity, digits = null, onChange } = {}) {
  const el = $(`#${id}`);
  const show = (raw) => {
    const v = parseNum(raw);
    el.value = Number.isFinite(v) ? (digits == null ? String(raw).replace(".", ",") : fmtNum(v, digits)) : raw;
  };
  show(S.get(key));
  const validate = () => {
    const v = parseNum(el.value);
    const ok = Number.isFinite(v) && v >= min && v <= max;
    el.setAttribute("aria-invalid", ok ? "false" : "true");
    el.title = ok ? "" : `Введите число от ${fmtNum(min, 2).replace(/,00$/, "")} до ${fmtNum(max, 2).replace(/,00$/, "")}`;
    return ok ? v : NaN;
  };
  el.addEventListener("input", () => {
    const v = validate();
    if (Number.isFinite(v)) S.set({ [key]: String(v) });
  });
  el.addEventListener("change", () => {
    const v = validate();
    if (Number.isFinite(v)) {
      show(String(v));
      onChange?.(v);
    }
  });
  validate();
  return el;
}

export function bindValue(id, key, { onChange } = {}) {
  const el = $(`#${id}`);
  el.value = S.get(key);
  el.addEventListener("change", () => {
    S.set({ [key]: el.value });
    onChange?.(el.value);
  });
  return el;
}

export function bindCheckbox(id, key, { onChange } = {}) {
  const el = $(`#${id}`);
  el.checked = S.get(key) === "1";
  el.addEventListener("change", () => {
    S.set({ [key]: el.checked ? "1" : "0" });
    onChange?.(el.checked);
  });
  return el;
}

/** Прочитать число из состояния с проверкой диапазона; ошибка — понятным текстом. */
export function readNumber(key, label, { min = -Infinity, max = Infinity } = {}) {
  const v = parseNum(S.get(key));
  if (!Number.isFinite(v) || v < min || v > max) {
    throw new UserError(`Поле «${label}»: введите число от ${fmtNum(min, 0)} до ${fmtNum(max, 0)}.`);
  }
  return v;
}

export function readDates(fromKey, tillKey) {
  const from = S.get(fromKey);
  const till = S.get(tillKey);
  const re = /^\d{4}-\d{2}-\d{2}$/;
  if (!re.test(from) || !re.test(till)) throw new UserError("Укажите даты начала и конца периода.");
  if (from >= till) throw new UserError("Дата начала должна быть раньше даты конца.");
  return { from, till };
}

/** Общие параметры расчёта (шапка). В демо-режиме ряды месячные — частота не выше месячной. */
export function globals() {
  let rf = parseNum(S.get("rf"));
  if (!Number.isFinite(rf)) rf = 7.86;
  let freq = S.get("freq");
  const freqForced = S.isDemo() && (freq === "D" || freq === "W");
  if (freqForced) freq = "M";
  return { rf: rf / 100, bench: S.get("bench") || "MCFTR", freq, freqForced };
}

export const FREQ_LABELS = { D: "дневные", W: "недельные", M: "месячные", Q: "квартальные", A: "годовые" };
