// Подключение расчётных модулей web/lib (владелец — `api`).
// Импорт динамический: если модули недоступны, интерфейс не падает, а показывает сообщение.

export const engine = { ok: false, error: null };

let loading = null;

export function loadEngine() {
  loading ??= Promise.all([
    import("../lib/data.js"),
    import("../lib/metrics.js"),
    import("../lib/backtest.js"),
    import("../lib/frontier.js"),
    import("../lib/strategies.js"),
  ])
    .then(([data, metrics, backtest, frontier, strategies]) => {
      Object.assign(engine, { data, metrics, backtest, frontier, strategies, ok: true });
      return engine;
    })
    .catch((e) => {
      engine.error = e;
      return engine;
    });
  return loading;
}

export function requireEngine() {
  if (!engine.ok) {
    throw new UserError(
      "Модули расчёта (web/lib) не загрузились — расчёты недоступны. " +
        "Обновите страницу; если ошибка повторяется, сообщите разработчикам." +
        (engine.error ? ` Подробности: ${engine.error.message}` : ""),
    );
  }
  return engine;
}

/** Ошибка с понятным пользователю текстом (показывается как есть). */
export class UserError extends Error {
  constructor(message, { offerDemo = false } = {}) {
    super(message);
    this.offerDemo = offerDemo;
  }
}

// ---------------------------------------------------------------- адаптеры структур контракта

/** Пары [ключ, значение] из объекта или Map. */
export const entries = (x) => (x instanceof Map ? [...x.entries()] : Object.entries(x || {}));

/** Множество из Set / массива / объекта. */
export function toSet(x) {
  if (x instanceof Set) return x;
  if (Array.isArray(x)) return new Set(x);
  return new Set(Object.keys(x || {}));
}

/** Каталог активов как массив (по контракту — массив, на всякий случай поддержан и объект). */
export function catalogList() {
  const c = engine.data?.CATALOG;
  if (!c) return [];
  return Array.isArray(c) ? c : entries(c).map(([key, v]) => ({ key, ...v }));
}

export function catalogName(key) {
  return catalogList().find((a) => a.key === key)?.name ?? "";
}

export const assetLabel = (key) => {
  const n = catalogName(key);
  return n ? `${key} — ${n}` : key;
};
