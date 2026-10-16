// Стратегии и бенчмарки: те же составы, что в core/analytics/strategies.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { STRATEGIES, BENCHMARKS, allAssetKeys } from '../../web/lib/strategies.js';
import { CATALOG } from '../../web/lib/data.js';
import { reference } from './_helpers.mjs';

test('STRATEGIES: имена, порядок, веса и частота ребалансировки как в Python', () => {
  const ref = reference().strategies;
  assert.deepEqual(Object.keys(STRATEGIES), Object.keys(ref));
  for (const [name, s] of Object.entries(ref)) {
    assert.deepEqual(STRATEGIES[name].weights, s.weights, name);
    assert.equal(STRATEGIES[name].rebalance, s.rebalance, name);
  }
});

test('веса каждой стратегии в сумме дают 1', () => {
  for (const [name, s] of Object.entries(STRATEGIES)) {
    const sum = Object.values(s.weights).reduce((a, b) => a + b, 0);
    assert.ok(Math.abs(sum - 1) < 1e-12, `${name}: Σw = ${sum}`);
  }
});

test('BENCHMARKS как в Python; все активы стратегий есть в каталоге', () => {
  assert.deepEqual(BENCHMARKS, reference().benchmarks);
  const keys = new Set(CATALOG.map((a) => a.key));
  for (const k of allAssetKeys()) assert.ok(keys.has(k), `${k} нет в CATALOG`);
  assert.deepEqual(allAssetKeys(), [...allAssetKeys()].sort());
});
