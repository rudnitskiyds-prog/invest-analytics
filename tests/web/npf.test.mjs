// web/lib/npf.js: загрузка кусков и расчёты «Пенсионной индустрии». Эталон — числа, посчитанные кодом исходного
// приложения «Монитор портфелей НПФ» (NPF_platform-1.html, сборка 15.11.2026) на тех же данных; сеть не нужна.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, response } from './_helpers.mjs';
import * as npf from '../../web/lib/npf.js';

const DIR = path.join(ROOT, 'public', 'data');
const fsFetch = async (url) => {
  if (/^https?:\/\//.test(url)) throw new Error(`Сеть в тестах запрещена: ${url}`);
  const p = path.join(DIR, url.split('public/data/').pop().split('?')[0]);
  return fs.existsSync(p) ? response(fs.readFileSync(p, 'utf8')) : response('', { status: 404 });
};
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps * Math.max(1, Math.abs(b)), `${a} ≠ ${b}`);

const progress = [];
const db = await npf.loadNpf({ fetchImpl: fsFetch, base: 'public/data/', onProgress: (p) => progress.push(p) });
const ix = npf.buildIndex(db);

test('loadNpf: куски склеены в исходную базу, прогресс доходит до 100%', () => {
  assert.equal(db.comp.length, 2525);
  assert.equal(db.struct.length, 3829);
  assert.equal(db.funds.length, 33);
  assert.equal(db.inst.length, 18016);
  assert.equal(db.names.length, 11637);
  assert.equal(db.built, '2026-11-15');
  assert.equal(progress.at(-1).loaded, progress.at(-1).total);
  assert.deepEqual(db.comp.map((c) => c.f), db.comp.map((c) => c.f).sort((a, b) => a - b));
});
test('loadNpf: ошибка HTTP не глотается', async () => {
  await assert.rejects(npf.loadNpf({ fetchImpl: async () => response('', { status: 404 }) }), /HTTP 404/);
});
test('buildIndex: месяцы и счётчики', () => {
  assert.equal(ix.months.length, 119);
  assert.equal(ix.months[0], '2016-12');
  assert.equal(ix.months.at(-1), '2026-10');
  const d = npf.dataOverview(ix, 0);
  assert.equal(d.nCompRows, 303152);
  assert.equal(d.nGroups, 592);
  assert.equal(d.coverage.length, 33);
});

test('Рынок: резервы, отчёты о структуре, март 2026 (эталон исходного приложения)', () => {
  const m = npf.marketOverview(ix, { kind: 0, src: 'struct', month: '2026-03' });
  assert.equal(m.rows.length, 32);
  assert.equal(m.withSh, 32);
  assert.equal(m.withAum, 27);
  near(m.totAum, 2222238482760.82);
  near(m.weighted[0], 38.53520153502708);
  near(m.weighted[2], 29.040727390127714);
  near(m.weighted[6], 8.921133737640236);
  near(m.simple[0], 42.61318125000001);
  assert.equal(m.top[0].f.name, 'НПФ «ГАЗФОНД»');
  near(m.top[1].aum, 584573551355.48);
});
test('Рынок: расчёт по составу, резервы / накопления / вместе', () => {
  const a = npf.marketOverview(ix, { kind: 0, src: 'comp', month: '2026-03' });
  assert.equal(a.wn, 26); near(a.wa, 2204502350072.53); near(a.weighted[0], 38.42143758492945); near(a.simple[0], 40.756240550378045);
  const b = npf.marketOverview(ix, { kind: 1, src: 'comp', month: '2025-12' });
  near(b.totAum, 3646640500756.11); near(b.weighted[0], 50.112548696416184); assert.equal(b.top[0].f.name, 'НПФ ВТБ');
  const c = npf.marketOverview(ix, { kind: 'all', src: 'struct', month: '2026-03' });   // struct при 'all' → comp
  assert.equal(c.src, 'comp'); near(c.totAum, 5771894924624.42); near(c.weighted[6], 7.4390921793840645);
  assert.equal(c.top[0].f.name, 'НПФ Сбербанка');
});
test('Рынок: месяц по умолчанию и сортировка', () => {
  const m = npf.marketOverview(ix, { kind: 0, src: 'struct' });
  assert.ok(m.avail.some((x) => x.month === m.month));
  const byName = npf.sortMarketRows(ix, m.rows, { sort: 'name', dir: 1 });
  assert.equal(byName.length, m.rows.length);
  const ser = npf.marketSeries(ix, { kind: 0, mode: 'share' });
  const last = ser.cols.at(-1);
  if (last.v) near(Object.values(last.v).reduce((a, b) => a + b, 0), 100, 1e-6);
});

test('Фонд: Сбербанк (резервы, состав) на последнюю дату — эталон', () => {
  const fi = ix.F.findIndex((f) => f.id === 'sber');
  assert.equal(fi, 24);
  const v = npf.fundView(ix, { fi, kind: 0, src: 'comp' });
  assert.equal(v.dates.length, 64);
  assert.equal(v.date, '2026-03-31');
  near(v.kpi.aum, 584573551355.48);
  assert.equal(v.kpi.nav, null);
  near(v.kpi.ofzValue, 338857395649.89);
  near(v.kpi.deltaPct, 2.4091274858093925);
  near(v.kpi.top5Share, 27.28900611608291);
  assert.equal(v.kpi.nPositions, 251); assert.equal(v.kpi.nRows, 251);
  assert.equal(v.pos[0].name, '26247RMFS'); near(v.pos[0].value, 48592156264.79);
  const s = npf.fundView(ix, { fi, kind: 0, src: 'struct' });
  assert.equal(s.dates.length, 87); assert.equal(s.date, '2026-10-31'); near(s.kpi.ofz, 58.98);
});
test('Фонд: серии, состав, эмитенты, сравнение дат, сверка, источники', () => {
  const fi = 24, v = npf.fundView(ix, { fi, kind: 0, src: 'comp' });
  const st = npf.fundStructureSeries(ix, v, { stack: 'share' });
  assert.equal(st.cols.length, 64);
  near(Object.values(st.cols.at(-1).v).reduce((a, b) => a + b, 0), 100, 1e-6);
  assert.equal(npf.fundVolumeSeries(ix, v).series[0].v.length, v.dates.length);
  const h = npf.fundHoldings(ix, v, { q: '26247RMFS' });
  assert.equal(h.list.length, 1); assert.equal(h.total, 251);
  assert.ok(npf.fundIssuers(ix, v).list[0].value >= npf.fundIssuers(ix, v).list[1].value);
  const c = npf.fundCompareDates(ix, v, {});
  assert.equal(c.to, v.date); assert.ok(c.rows.length > 0);
  const r = npf.fundRecon(ix, { fi, kind: 0, month: '2026-03' });
  assert.ok(r.supported && r.comp && r.struct && r.rows.length > 0);
  assert.equal(npf.fundRecon(ix, { fi, kind: 'all', month: '2026-03' }).supported, false);
  const so = npf.fundSources(ix, fi);
  assert.equal(so.comps.length, npf.fundKinds(ix, fi).reduce((a, k) => a + ix.compBy.get(fi + '|' + k).length, 0));
  assert.equal(so.stats.matched + so.stats.rounded + so.stats.recomputed + so.stats.noTotal, so.comps.length);
});
test('Фонд: без состава src принудительно struct; kind подменяется доступным', () => {
  const fi = ix.F.findIndex((f) => !f.comp);
  assert.ok(fi >= 0);
  const v = npf.fundView(ix, { fi, kind: 0, src: 'comp' });
  assert.equal(v.src, 'struct'); assert.equal(v.isComp, false);
});

test('Сравнение фондов', () => {
  const c = npf.compareFunds(ix, { kind: 0, metric: 'aum', src: 'struct' });
  assert.equal(c.src, 'comp'); assert.equal(c.funds.length, 5);
  assert.equal(c.series.length, 5); assert.ok(c.xs.includes(c.month));
  const s = npf.compareFunds(ix, { funds: [24, 20], kind: 0, metric: '0', src: 'struct', month: '2026-03' });
  assert.equal(s.bars.length, 2); assert.equal(s.src, 'struct');
});

test('Бумаги и эмитенты: март 2026 — эталон; поиск', () => {
  const o = npf.securitiesOverview(ix, { kind: 0, month: '2026-03' });
  near(o.total, 2204502350072.53);
  assert.equal(o.groups[0].name, 'Министерство финансов Российской Федерации');
  near(o.groups[0].value, 881468921596.4504);
  const d = npf.securityDetail(ix, { kind: 0, month: '2026-03', sel: { t: 'g', id: o.groups[0].id } });
  near(d.total, 881468921596.4504); assert.ok(d.nHolders > 10); assert.equal(d.series[0].name, 'Все фонды');
  const i = npf.securityDetail(ix, { kind: 0, month: '2026-03', sel: { t: 'i', id: o.instruments[0].key } });
  assert.ok(i.title && i.total > 0 && i.nHolders > 0);
  assert.equal(npf.securityDetail(ix, { kind: 0, month: '2026-03', sel: { t: 'i', id: 'i:XX0000000000' } }), null);
  const q = npf.securitiesOverview(ix, { kind: 0, month: '2026-03', q: 'сбербанк' });
  assert.ok(q.groups.length > 0);
  const nothing = npf.securitiesOverview(ix, { kind: 0, month: '2026-03', q: 'zzzzqqqq' });
  assert.equal(nothing.groups.length + nothing.instruments.length + nothing.historical.length, 0);
  const g = npf.searchGlobal(ix, 'сбер');
  assert.ok(g.funds.length > 0 && g.funds.length <= 6);
  assert.deepEqual(npf.searchGlobal(ix, 'с'), { funds: [], groups: [], insts: [] });
});

test('Таблицы для CSV', () => {
  assert.equal(npf.aumTable(ix).length, 2525);
  assert.ok(npf.structureTable(ix).length >= 3829);
});

test('combine: резервы + накопления складываются; без СЧА одного из портфелей СЧА неизвестна', () => {
  const mini = { notes: ['n0'] };
  const x = { db: mini };
  const p = (k, t, nav) => ({ k, d: '2025-12-31', c: { 0: t / 2 }, s: { 0: t / 2 }, t, nav, nl: 0, n: [] });
  const o = npf.combine(x, [p(0, 100, 90), p(1, 300, null)]);
  assert.equal(o.aum, 400); assert.equal(o.nav, null); assert.equal(o.c[0], 200);
  assert.equal(npf.combine(x, [p(0, 100, 90), p(1, 300, 280)]).nav, 370);
  assert.equal(npf.combine(x, [p(0, 1), undefined]), null);
  assert.deepEqual(npf.sharesComp({ aum: 200, c: { 0: 50 }, s: {} }, 'class'), { 0: 25 });
  assert.deepEqual(npf.sharesComp({ aum: null, c: { 0: 50 }, s: {} }, 'class'), {});
});

// ---------------------------------------------------------------- загрузчик: устойчивость
const tracked = (over = {}) => {
  const urls = []; let active = 0, maxActive = 0;
  const f = async (url, opts) => {
    urls.push(url); active++; maxActive = Math.max(maxActive, active);
    await new Promise((r) => setTimeout(r, 5));
    try { return over[url.split('/').pop().split('?')[0]]?.(url, opts) ?? fsFetch(url); } finally { active--; }
  };
  return { f, urls, max: () => maxActive };
};
test('loadNpf: версия ?v=sha256[:8] в URL кусков, manifest без версии, параллелизм ≤ 4', async () => {
  const t = tracked();
  await npf.loadNpf({ fetchImpl: t.f, base: 'public/data/' });
  const man = JSON.parse(fs.readFileSync(path.join(DIR, 'npf', 'manifest.json'), 'utf8'));
  assert.equal(t.urls[0], 'public/data/npf/manifest.json');
  for (const x of man.files) assert.ok(t.urls.includes(`public/data/npf/${x.name}?v=${x.sha256.slice(0, 8)}`), x.name);
  assert.equal(t.urls.length, man.files.length + 1);
  assert.ok(t.max() <= 4 && t.max() > 1, `параллелизм ${t.max()}`);
});
test('loadNpf: records из manifest не совпадают с куском → «устарел или повреждён»', async () => {
  const man = JSON.parse(fs.readFileSync(path.join(DIR, 'npf', 'manifest.json'), 'utf8'));
  man.files.find((x) => x.name === 'struct.json').records += 1;
  const t = tracked({ 'manifest.json': () => response(man) });
  await assert.rejects(npf.loadNpf({ fetchImpl: t.f, base: 'public/data/' }), /НПФ: файл struct\.json устарел или повреждён, обновите страницу/);
});
test('loadNpf: HTML шлюза со статусом 200 → «некорректный JSON»', async () => {
  const t = tracked({ 'names.json': () => response('<html>502 Bad Gateway</html>') });
  await assert.rejects(npf.loadNpf({ fetchImpl: t.f, base: 'public/data/' }), /НПФ: names\.json — некорректный JSON/);
});
test('loadNpf: после первой ошибки новые запросы не стартуют и signal отменяется', async () => {
  const sigs = [];
  const t = tracked({ 'meta.json': () => response('', { status: 500 }) });
  const f = (u, o) => { sigs.push(o.signal); return t.f(u, o); };
  await assert.rejects(npf.loadNpf({ fetchImpl: f, base: 'public/data/' }), /HTTP 500/);
  await new Promise((r) => setTimeout(r, 40));
  assert.ok(t.urls.length < 11, `запросов ${t.urls.length}`);
  assert.ok(sigs.every((s) => s.aborted));
});
test('getNpf: параллельные вызовы делят один набор запросов; ошибка сбрасывает кэш', async () => {
  npf.clearNpfCache();
  const t = tracked();
  const [a, b] = await Promise.all([npf.getNpf({ fetchImpl: t.f, base: 'public/data/' }), npf.getNpf({ fetchImpl: t.f, base: 'public/data/' })]);
  assert.equal(a, b);
  assert.equal(t.urls.length, 11);
  assert.equal(await npf.getNpf({ fetchImpl: t.f }), a);
  assert.equal(t.urls.length, 11);
  npf.clearNpfCache();
  const bad = tracked({ 'manifest.json': () => response('', { status: 503 }) });
  await assert.rejects(npf.getNpf({ fetchImpl: bad.f, base: 'public/data/' }), /HTTP 503/);
  const ok = tracked();
  assert.ok(await npf.getNpf({ fetchImpl: ok.f, base: 'public/data/' }));
  assert.equal(ok.urls.length, 11);
  npf.clearNpfCache();
});

// ---------------------------------------------------------------- арифметика для UI
test('rowShare / groupShare / topShare: доли от АУМ и защита от 0/NaN', () => {
  near(npf.rowShare(25, 200), 12.5);
  for (const aum of [0, -1, NaN, null, undefined, Infinity]) assert.equal(npf.rowShare(1, aum), null);
  assert.equal(npf.rowShare(NaN, 100), null);
  assert.equal(npf.rowShare('5', 100), null);
  near(npf.groupShare([10, { value: 30 }], 200), 20);
  assert.equal(npf.groupShare([10], 0), null);
  near(npf.topShare([{ value: 50 }, { value: 30 }, { value: 20 }], 2, 200), 40);
  assert.equal(npf.topShare([], 5, 100), 0);
  assert.equal(npf.topShare([{ value: 1 }], 5, null), null);
});
test('compareDelta / diffOf / barFrac / reconFlag', () => {
  const d = npf.compareDelta(100, 125);
  near(d.abs, 25); near(d.pct, 25);
  assert.deepEqual(npf.compareDelta(0, 5), { abs: 5, pct: null });
  assert.equal(npf.compareDelta(null, 5), null);
  assert.equal(npf.compareDelta(1, NaN), null);
  near(npf.diffOf(3, 5.5), 2.5); assert.equal(npf.diffOf(null, 1), null); assert.equal(npf.diffOf(1, NaN), null);
  near(npf.barFrac(-5, 10), .5); assert.equal(npf.barFrac(20, 10), 1); assert.equal(npf.barFrac(1, 0), null); assert.equal(npf.barFrac(1, NaN), null);
  assert.deepEqual(npf.reconFlag(-2), { diff: -2, abs: 2, flag: true });
  assert.equal(npf.reconFlag(1.99).flag, false);
  assert.equal(npf.reconFlag(3, 5).flag, false);
  assert.equal(npf.reconFlag(null), null); assert.equal(npf.reconFlag(NaN), null);
});
test('securityDetail: инструмент есть в INST, но ни в одном составе → пустой результат', () => {
  const ix2 = npf.buildIndex({ ...db, names: [...db.names, 'Фантом'], isins: [...db.isins, 'ZZ0000000000'], inst: [...db.inst, [db.names.length, db.isins.length, 0, 0, 0, db.groups.length]], groups: [...db.groups, 'Фантом'] });
  const gid = db.groups.length;
  for (const sel of [{ t: 'g', id: gid }, { t: 'i', id: npf.instKey(ix2, ix2.INST.at(-1)) }]) {
    const d = npf.securityDetail(ix2, { kind: 0, month: '2026-03', sel });
    assert.ok(d); assert.deepEqual(d.xs, []); assert.equal(d.first, null); assert.equal(d.last, null); assert.equal(d.maxMonth, null);
    assert.deepEqual(d.holders, []); assert.equal(d.total, 0); assert.deepEqual(d.series.map((x) => x.v), [[]]);
  }
});
