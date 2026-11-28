// ISS_BASE / CBR_BASE (web/lib/data.js): умолчание, переопределение globalThis.FINSERV_*, шлюз Yandex Cloud.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

let n = 0;
const load = (file = 'lib/data.js') => import(`../../web/${file}?b=${++n}`);   // свежий экземпляр модуля: константы считаются при загрузке
afterEach(() => { delete globalThis.FINSERV_ISS_BASE; delete globalThis.FINSERV_CBR_BASE; delete globalThis.location; });

for (const file of ['lib/data.js', 'js/config.js']) {
  test(`${file}: по умолчанию — прямые адреса биржи и ЦБ (node, локально, тесты)`, async () => {
    const m = await load(file);
    assert.equal(m.ISS_BASE, 'https://iss.moex.com/iss');
    if (m.CBR_BASE !== undefined) assert.equal(m.CBR_BASE, 'https://www.cbr.ru');
  });
  test(`${file}: переопределение через globalThis.FINSERV_ISS_BASE / FINSERV_CBR_BASE`, async () => {
    globalThis.FINSERV_ISS_BASE = 'http://localhost:9/iss'; globalThis.FINSERV_CBR_BASE = 'http://localhost:9/cbr';
    const m = await load(file);
    assert.equal(m.ISS_BASE, 'http://localhost:9/iss');
    if (m.CBR_BASE !== undefined) assert.equal(m.CBR_BASE, 'http://localhost:9/cbr');
  });
  test(`${file}: на шлюзе *.apigw.yandexcloud.net — /iss и /cbr; чужой хост — прямые адреса`, async () => {
    globalThis.location = { hostname: 'd5dabc123.apigw.yandexcloud.net' };
    const m = await load(file);
    assert.equal(m.ISS_BASE, '/iss'); assert.equal(m.CBR_BASE, '/cbr');
    globalThis.location = { hostname: 'evil.apigw.yandexcloud.net.example.com' };
    assert.equal((await load(file)).ISS_BASE, 'https://iss.moex.com/iss');
    globalThis.location = { hostname: 'localhost' };
    assert.equal((await load(file)).CBR_BASE, 'https://www.cbr.ru');
  });
  test(`${file}: переопределение важнее шлюза`, async () => {
    globalThis.location = { hostname: 'x.apigw.yandexcloud.net' }; globalThis.FINSERV_ISS_BASE = 'http://t/iss';
    assert.equal((await load(file)).ISS_BASE, 'http://t/iss');
  });
}

test('запросы data.js идут на переопределённый ISS_BASE', async () => {
  globalThis.FINSERV_ISS_BASE = 'http://proxy.test/iss';
  const D = await load('lib/data.js');
  const calls = [];
  const f = async (u) => { calls.push(u); return { ok: true, json: async () => ({ history: { columns: ['TRADEDATE', 'CLOSE'], data: [['2024-01-03', 1]] } }) }; };
  await D.fetchIssIndex('IMOEX', '2024-01-01', '2024-02-01', { fetchImpl: f });
  assert.ok(calls.length && calls.every((u) => u.startsWith('http://proxy.test/iss/')), calls.join());
});
