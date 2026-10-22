// Паритет Python ↔ JS для страницы бумаги (вызывается из tests/test_symbol_page.py, не node:test).
// Аргумент — путь к JSON-заданию:
//   {dataDir, dividends: [{secid, dates, values}], funds: [secid, …]}
// Вывод (stdout) — JSON {dividends: [{secid, asOf, divs, stats}], funds: {secid: loadFundInfo}}.
// Файлы читаются с диска (fetchImpl), сеть не используется.
import fs from 'node:fs';
import path from 'node:path';
import { dividendStats, loadDividends, loadFundInfo } from '../../web/lib/symbol.js';
import { response } from './_helpers.mjs';

const task = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const fetchImpl = async (url) => {
  if (/^https?:\/\//.test(url)) throw new Error(`Сеть в тестах запрещена: ${url}`);
  const p = path.join(task.dataDir, url.split('/').pop());
  if (!fs.existsSync(p)) return response('not found', { status: 404 });
  return response(fs.readFileSync(p, 'utf8'));
};
const base = 'local/';
const out = { dividends: [], funds: {} };
for (const c of task.dividends || []) {
  const divs = await loadDividends(c.secid, { base, fetchImpl });
  const close = c.dates ? { dates: c.dates, values: c.values } : null;
  out.dividends.push({ secid: c.secid, asOf: c.dates ? c.dates[c.dates.length - 1] : null, divs,
    stats: dividendStats(divs, close) });
}
for (const s of task.funds || []) out.funds[s] = await loadFundInfo(s, { base, fetchImpl });
process.stdout.write(JSON.stringify(out));
