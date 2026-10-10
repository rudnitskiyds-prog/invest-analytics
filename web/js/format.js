// Форматирование чисел по-русски: пробел (неразрывный) между разрядами, запятая в дробях.
// Проценты на входе — доли (0.123 → «12,3 %»).

const NBSP = " ";
const MINUS = "−";

const isNum = (x) => typeof x === "number" && Number.isFinite(x);

/** Число с фиксированным числом знаков и группировкой разрядов. */
export function fmtNum(x, digits = 0, { group = true } = {}) {
  if (!isNum(x)) return "—";
  let s = Math.abs(x).toFixed(digits);
  const neg = x < 0 && Number(s) !== 0;
  let [int, frac] = s.split(".");
  if (group) int = int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  s = frac ? `${int},${frac}` : int;
  return neg ? MINUS + s : s;
}

export const fmtPct = (x, digits = 1) => (isNum(x) ? `${fmtNum(x * 100, digits)}${NBSP}%` : "—");
export const fmtRub = (x) => (isNum(x) ? `${fmtNum(x, 0)}${NBSP}₽` : "—");
export const fmtCoef = (x, digits = 3) => fmtNum(x, digits);

/** «2011-01-31» → «31.01.2011». */
export function fmtDate(d) {
  if (!d || typeof d !== "string") return "—";
  const [y, m, dd] = d.slice(0, 10).split("-");
  return dd ? `${dd}.${m}.${y}` : d;
}

/** Метка оси времени: «01.2011». */
export function fmtMonth(d) {
  if (!d || typeof d !== "string") return "";
  const [y, m] = d.split("-");
  return `${m}.${y}`;
}

/** ISO-время обновления → «06.10.2026 16:20» (локальное время браузера). */
export function fmtDateTime(iso) {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso || "—";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(t.getDate())}.${p(t.getMonth() + 1)}.${t.getFullYear()} ${p(t.getHours())}:${p(t.getMinutes())}`;
}

/** Разбор пользовательского ввода: «1 000 000», «7,86», «0.5». NaN, если не число. */
export function parseNum(str) {
  if (typeof str === "number") return str;
  const s = String(str ?? "").replace(/[\s  ]/g, "").replace(MINUS, "-").replace(",", ".");
  if (s === "" || !/^-?\d*\.?\d*$/.test(s)) return NaN;
  return Number(s);
}

/** Значение показателя для таблицы (как format_metrics_table в Python). */
export function fmtMetric(key, v, percentFields) {
  if (!isNum(v)) return "—";
  if (percentFields.has(key)) return fmtPct(v, 1);
  if (key === "days_to_recover") return fmtNum(Math.round(v), 0);
  if (key === "periods_per_year") return String(v);
  return fmtCoef(v, 3);
}

/** Число для CSV (разделитель «;», запятая в дробях, без группировки). */
export function csvNum(x, digits = 6) {
  if (!isNum(x)) return "";
  return Number(x.toFixed(digits)).toString().replace(".", ",");
}
