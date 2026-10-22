// Общие помощники тестов JS-движка: чтение фикстур без сети, эталон Python, сравнение чисел.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, '..', '..');
export const FIXTURES = path.join(ROOT, 'tests', 'fixtures');

/** Ответ в стиле fetch. */
export function response(body, { status = 200 } = {}) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => JSON.parse(text),
    text: async () => text,
  };
}

/**
 * fetchImpl, читающий tests/fixtures по имени файла из URL. Любой URL с http(s):// — ошибка теста
 * (сеть запрещена). Возвращает функцию с журналом запросов .calls.
 */
export function fixtureFetch() {
  const calls = [];
  const f = async (url) => {
    calls.push(url);
    if (/^https?:\/\//.test(url)) throw new Error(`Сеть в тестах запрещена: ${url}`);
    const name = url.split('/').pop();
    const p = path.join(FIXTURES, name);
    if (!fs.existsSync(p)) return response('not found', { status: 404 });
    return response(fs.readFileSync(p, 'utf8'));
  };
  f.calls = calls;
  return f;
}

/** fetchImpl по таблице маршрутов: (url) => body | {status} | undefined (404). */
export function routeFetch(handler) {
  const calls = [];
  const f = async (url) => {
    calls.push(url);
    const r = handler(url);
    if (r === undefined) return response('not found', { status: 404 });
    if (r instanceof Error) throw r;
    if (r && r.__status) return response(r.body ?? '', { status: r.__status });
    return response(r);
  };
  f.calls = calls;
  return f;
}

let _ref = null;
/** Эталон Python (tests/fixtures/web_reference.json; генератор tests/web/make_reference.py). */
export function reference() {
  _ref ??= JSON.parse(fs.readFileSync(path.join(FIXTURES, 'web_reference.json'), 'utf8'));
  return _ref;
}

/** null в JSON-эталоне = NaN/None в Python. */
export function close(actual, expected, tol, msg = '') {
  if (expected === null || expected === undefined) {
    assert.ok(actual === null || actual === undefined || Number.isNaN(actual),
      `${msg}: ожидалось NaN/null, получено ${actual}`);
    return;
  }
  assert.equal(typeof actual, 'number', `${msg}: ожидалось число ${expected}, получено ${actual}`);
  const d = Math.abs(actual - expected);
  const scale = Math.max(1, Math.abs(expected));
  assert.ok(d <= tol * scale, `${msg}: ${actual} vs ${expected} (|Δ|=${d.toExponential(2)}, допуск ${tol})`);
}

export function closeArr(actual, expected, tol, msg = '') {
  assert.equal(actual.length, expected.length, `${msg}: длина ${actual.length} vs ${expected.length}`);
  expected.forEach((e, i) => close(actual[i], e, tol, `${msg}[${i}]`));
}

/** Series из Python-JSON (null -> NaN). */
export function seriesFromRef(s) {
  return { dates: s.dates.slice(), values: s.values.map((v) => (v === null ? NaN : v)) };
}

export function frameFromRef(f) {
  return {
    dates: f.dates.slice(),
    cols: Object.fromEntries(Object.entries(f.cols).map(([k, v]) => [k, v.map((x) => (x === null ? NaN : x))])),
  };
}
