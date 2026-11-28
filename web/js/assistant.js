// Ассистент «Спросить ИИ»: плавающая кнопка и диалог. Ответ даёт Cloud Function `assistant` (web/CONTRACT.md,
// раздел «Ассистент GigaChat…»). Числа модель берёт только из фактов экрана — их отдают страницы через registerFacts:
// уже посчитанные интерфейсом значения, без пересчёта.

import { ASSISTANT_URL } from "./config.js";
import { $, h, put } from "./dom.js";
import * as R from "./router.js";
import * as S from "./state.js";

const MAX_LEN = 1000;
const MAX_FACTS = 60;      // больше функция не примет (400)
const TIMEOUT_MS = 45_000;
const DISCLAIMER = "Ответ ИИ может ошибаться. Цифры — из расчётов платформы. Не является индивидуальной инвестиционной рекомендацией.";
const OFFLINE = "Ассистент работает на опубликованном сайте. Здесь (демо-режим или локальный запуск) запрос к ИИ недоступен.";
const UNVERIFIED = "Этого числа нет в расчёте — проверьте";
// на этих маршрутах кнопка видна и без фактов — ассистент отвечает про термины

const EXAMPLES = {
  backtest: ["Почему у портфелей разная итоговая сумма?", "Что такое максимальная просадка?",
    "Какой портфель меньше всего падал?", "Что значит доходность с учётом пополнений?"],
  frontier: ["Что такое касательный портфель?", "Чем портфель минимальной дисперсии отличается от касательного?",
    "Что показывает коэффициент Шарпа?", "Почему результат зависит от выбранного периода?"],
  security: ["Объясни эти показатели простыми словами", "Что такое бета и альфа?",
    "Насколько бумага рискованнее бенчмарка?", "Что значит волатильность?"],
  symbol: ["Как изменилась цена за последний год?", "Что такое CAGR?",
    "Что значит диапазон 52 недель?", "Чем доходность с дивидендами отличается от доходности по цене?"],
};

const collectors = new Map();  // route ("/tools/backtest" или префикс "/symbol/") → () => facts[]
let route = null;
let ui = null;
let busy = false;

/** Зарегистрировать сборщик фактов экрана. route — путь или префикс, оканчивающийся на «/». */
export function registerFacts(routeKey, fn) {
  collectors.set(routeKey, fn);
  if (ui) sync();
}

/** Факт по контракту функции `assistant`; text — как на экране (web/js/format.js). */
export const fact = (id, label, value, unit, text, source) => ({ id, label, value, unit, text, source });

function collectorFor(path) {
  for (const [k, fn] of collectors) if (k.endsWith("/") ? path.startsWith(k) : path === k) return fn;
  return null;
}

function facts() {
  const fn = route && collectorFor(route.path);
  if (!fn) return null;
  try {
    return (fn() || []).filter((f) => f && f.id && f.text && f.text !== "—").slice(0, MAX_FACTS);
  } catch (e) {
    console.warn(e);
    return [];
  }
}

// Кнопка видна на всех маршрутах (CONTRACT «Ассистент — на всех страницах»); без фактов — вопросы о терминах.
function sync() {
  ui.fab.hidden = false;
  document.body.classList.add("has-ai");
}

export function initAssistant() {
  const fab = h("button", { type: "button", class: "ai-fab", hidden: true, "aria-haspopup": "dialog" },
    h("span", { "aria-hidden": "true" }, "✦ "), "Спросить ИИ");
  const field = h("textarea", {
    id: "ai-q", rows: "3", maxlength: String(MAX_LEN), "aria-describedby": "ai-count ai-pd",
    placeholder: "Например: что такое просадка?",
  });
  const count = h("span", { id: "ai-count", class: "hint ai-count" });
  const send = h("button", { type: "submit", class: "btn btn-primary" }, "Спросить");
  const status = h("p", { class: "hint ai-status", role: "status" });
  const answer = h("div", { class: "ai-answer", "aria-live": "polite" });
  const examples = h("div", { class: "ai-examples", role: "group", "aria-label": "Примеры вопросов" });
  const form = h("form", { class: "ai-form" },
    h("label", { for: "ai-q" }, "Ваш вопрос"), field,
    h("p", { id: "ai-pd", class: "hint ai-pd" }, "Не вводите личные данные: паспорт, телефон, номера счетов — вопрос передаётся в GigaChat."),
    h("div", { class: "ai-row" }, count, send));
  const dlg = h("dialog", { class: "ai-dialog", "aria-labelledby": "ai-title", "aria-describedby": "ai-disc" },
    h("div", { class: "ai-head" },
      h("h2", { id: "ai-title" }, "Спросить ИИ"),
      h("button", { type: "button", class: "btn btn-icon", "aria-label": "Закрыть", onclick: () => dlg.close() }, "×")),
    h("p", { id: "ai-disc", class: "hint ai-disc" }, DISCLAIMER),
    h("p", { id: "ai-screen", class: "hint" }),
    examples, form, status, answer);
  document.body.append(fab, dlg);
  ui = { fab, dlg, field, count, send, status, answer, examples };

  const updateCount = () => { count.textContent = `${field.value.length} / ${MAX_LEN}`; };
  field.addEventListener("input", updateCount);
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) form.requestSubmit();
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    ask(field.value);
  });
  let opener = null;
  fab.addEventListener("click", () => {
    opener = document.activeElement;
    renderExamples();
    const f = facts() || [];
    $("#ai-screen").textContent = f.length
      ? `ИИ видит ${f.length} ${plural(f.length, "показатель", "показателя", "показателей")} с этой страницы.`
      : "На странице пока нет рассчитанных показателей — ИИ ответит про термины.";
    updateCount();
    dlg.showModal();
    field.focus();
  });
  // клик по затемнению: вне прямоугольника диалога (клик по его отступам тоже приходит с target === dlg)
  dlg.addEventListener("click", (e) => {
    if (e.target !== dlg) return;
    const r = dlg.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dlg.close();
  });
  dlg.addEventListener("close", () => (opener && opener.isConnected ? opener : fab).focus());

  route = R.current();
  window.addEventListener("hashchange", () => { route = R.current(); sync(); });
  sync();
}

/** 1 показатель, 2 показателя, 5 показателей. */
export function plural(n, one, few, many) {
  const d = n % 10, c = n % 100;
  if (d === 1 && c !== 11) return one;
  return d >= 2 && d <= 4 && !(c >= 12 && c <= 14) ? few : many;
}

function renderExamples() {
  const list = EXAMPLES[route?.page] || [];
  put(ui.examples, list.map((q) => h("button", {
    type: "button", class: "btn btn-sm ai-ex", onclick: () => { ui.field.value = q; ask(q); },
  }, q)));
}

function offline() {
  return S.isDemo() || location.protocol === "file:";
}

async function ask(raw) {
  const question = String(raw || "").trim();
  if (busy) return;
  if (!question) {
    ui.status.textContent = "Введите вопрос.";
    ui.field.focus();
    return;
  }
  if (offline()) {
    showError(OFFLINE);
    return;
  }
  busy = true;
  ui.send.disabled = true;
  ui.status.textContent = "ИИ готовит ответ…";
  put(ui.answer);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const screen = { route: route.path, title: document.title.replace(/ — FinServ$/, ""), facts: facts() || [] };
    let res;
    try {
      res = await fetch(ASSISTANT_URL, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: ctl.signal,
        body: JSON.stringify({ question, screen }),
      });
    } catch (e) {
      showError(e.name === "AbortError" ? "ИИ не ответил за 45 секунд. Попробуйте ещё раз." : OFFLINE);
      return;
    }
    const data = await res.json().catch(() => null);
    if (res.ok && data && typeof data.answer === "string") {
      ui.status.textContent = "";
      showAnswer(data);
      return;
    }
    // 402 / исчерпанный лимит — у функции свой понятный текст
    if (data?.error && (res.status === 402 || /лимит/i.test(data.error))) {
      showError(String(data.error));
      return;
    }
    const msg = {
      400: `Вопрос не принят: ${data?.error || "проверьте текст вопроса."}`,
      429: "Слишком много вопросов подряд. Подождите минуту и спросите снова.",
      502: "ИИ сейчас недоступен. Попробуйте чуть позже.",
    }[res.status];
    // 404/405/501 — шлюза нет (локальный сервер): функция работает только на опубликованном сайте
    showError(msg || ([404, 405, 501].includes(res.status) ? OFFLINE : `Не удалось получить ответ (код ${res.status}). Попробуйте позже.`));
  } finally {
    clearTimeout(timer);
    busy = false;
    ui.send.disabled = false;
  }
}

function showError(text) {
  ui.status.textContent = "";
  put(ui.answer, h("div", { class: "notice notice-warn", role: "alert" }, h("span", { class: "notice-text" }, text)));
}

function showAnswer(data) {
  const nums = (Array.isArray(data.unverified_numbers) ? data.unverified_numbers : [])
    .map((x) => String(x).trim()).filter(Boolean);
  const body = h("div", { class: "ai-text" }, ...highlight(data.answer, nums));
  put(ui.answer,
    h("p", { class: "ai-label" }, data.label || "Ответ ИИ"),
    body,
    nums.length ? h("p", { class: "hint ai-unv" }, h("mark", {}, "Отмеченные числа"), ` не найдены в расчёте платформы — проверьте их.`) : null);
}

/** Текст ответа → узлы; числа из unverified_numbers обёрнуты в <mark>. Пробелы в числе — любые (обычный, неразрывный);
 *  только целое число: «12» не подсвечивается внутри «12,3 %». */
export function highlight(text, nums) {
  if (!nums.length) return [text];
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "[\\s\\u00a0\\u202f]*");
  const re = new RegExp(`(?<![\\d,.])(${[...nums].sort((a, b) => b.length - a.length).map(esc).join("|")})(?![\\d,])`, "g");
  return text.split(re).map((part, i) => (i % 2
    ? h("mark", { class: "ai-unverified", title: UNVERIFIED }, part, h("span", { class: "sr-only" }, ` (${UNVERIFIED})`))
    : part));
}
