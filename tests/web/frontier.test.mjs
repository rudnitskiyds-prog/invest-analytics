// Граница Марковица: паритет входов и оптимальных весов с Python, свойства решения.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { estimateInputs, efficientFrontier, solveQP } from '../../web/lib/frontier.js';
import { sliceFrame } from '../../web/lib/data.js';
import { reference, close, closeArr, frameFromRef } from './_helpers.mjs';

const REF = reference();
const FR = REF.frontier;
const px = (() => {
  const df = sliceFrame(frameFromRef(REF.demo_frame), FR.start, FR.end);
  return { dates: df.dates, cols: Object.fromEntries(FR.assets.map((a) => [a, df.cols[a]])) };
})();
const inp = estimateInputs(px, { freq: 'M', rf: FR.rf });

describe('estimateInputs: паритет с frontier.estimate_inputs до 1e-9', () => {
  test('μ (арифм. × 12) и Σ (выборочная × 12)', () => {
    assert.deepEqual(inp.assets, FR.assets);
    closeArr(inp.mu, FR.mu, 1e-9, 'mu');
    inp.cov.forEach((row, i) => closeArr(row, FR.cov[i], 1e-9, `cov[${i}]`));
    assert.equal(inp.rf, FR.rf);
  });
  test("muMethod = 'cagr'", () => {
    closeArr(estimateInputs(px, { freq: 'M', rf: FR.rf, muMethod: 'cagr' }).mu, FR.mu_cagr, 1e-9, 'mu_cagr');
  });
  test('квартальная частота', () => {
    const q = estimateInputs(px, { freq: 'Q', rf: FR.rf });
    closeArr(q.mu, FR.mu_q, 1e-9, 'mu_q');
    q.cov.forEach((row, i) => closeArr(row, FR.cov_q[i], 1e-9, `cov_q[${i}]`));
  });
  test('меньше 3 периодов → ошибка', () => {
    const short = sliceFrame(px, '2011-01-31', '2011-02-28');
    assert.throws(() => estimateInputs(short, { freq: 'M' }), /Недостаточно данных/);
  });
});

for (const c of FR.cases) {
  describe(`граница при wMin=${c.wMin}, wMax=${c.wMax}`, () => {
    const res = efficientFrontier(inp, { wMin: c.wMin, wMax: c.wMax, nPoints: 40, nRandom: 4000, seed: 42 });
    const sumW = (w) => Object.values(w).reduce((s, v) => s + v, 0);

    test('веса minVar совпадают с Python до 1e-3', () => {
      for (const a of FR.assets) close(res.minVar.w[a], c.minVar[a], 1e-3, `minVar.${a}`);
    });
    test('веса maxSharpe совпадают с Python до 1e-3', () => {
      for (const a of FR.assets) close(res.maxSharpe.w[a], c.maxSharpe[a], 1e-3, `maxSharpe.${a}`);
    });
    test('Σw = 1 и wMin ≤ w ≤ wMax для minVar, maxSharpe и всех точек границы', () => {
      for (const p of [res.minVar, res.maxSharpe, ...res.frontier]) {
        close(sumW(p.w), 1, 1e-9, 'Σw');
        for (const [a, v] of Object.entries(p.w)) {
          assert.ok(v >= c.wMin - 1e-9 && v <= c.wMax + 1e-9, `${a} = ${v} вне [${c.wMin}, ${c.wMax}]`);
        }
      }
    });
    test('ни один случайный портфель не имеет σ ниже minVar и Шарпа выше maxSharpe', () => {
      assert.ok(res.random.length > 0);
      const minVol = Math.min(...res.random.map((p) => p.vol));
      const maxSh = Math.max(...res.random.map((p) => p.sharpe));
      assert.ok(minVol >= res.minVar.vol - 1e-9, `σ случайного ${minVol} < σ minVar ${res.minVar.vol}`);
      assert.ok(maxSh <= res.maxSharpe.sharpe + 1e-9, `Шарп случайного ${maxSh} > maxSharpe ${res.maxSharpe.sharpe}`);
      for (const p of res.frontier) assert.ok(p.sharpe <= res.maxSharpe.sharpe + 1e-9, 'точка границы с Шарпом выше maxSharpe');
    });
    test('граница монотонна: доходность и σ не убывают; первая точка — minVar', () => {
      assert.equal(res.frontier.length, 40);
      close(res.frontier[0].vol, res.minVar.vol, 1e-9, 'σ первой точки');
      for (let i = 1; i < res.frontier.length; i++) {
        assert.ok(res.frontier[i].ret >= res.frontier[i - 1].ret - 1e-12, `ret[${i}]`);
        assert.ok(res.frontier[i].vol >= res.frontier[i - 1].vol - 1e-9, `vol[${i}] ${res.frontier[i].vol} < ${res.frontier[i - 1].vol}`);
      }
    });
    test('точки активов: ret = μ, vol = √Σii', () => {
      res.assets.forEach((p, i) => {
        assert.equal(p.key, FR.assets[i]);
        close(p.ret, inp.mu[i], 1e-15, 'ret');
        close(p.vol, Math.sqrt(inp.cov[i][i]), 1e-15, 'vol');
      });
    });
    test('случайные портфели детерминированы по seed', () => {
      const again = efficientFrontier(inp, { wMin: c.wMin, wMax: c.wMax, nPoints: 3, nRandom: 50, seed: 42 });
      const other = efficientFrontier(inp, { wMin: c.wMin, wMax: c.wMax, nPoints: 3, nRandom: 50, seed: 7 });
      const first = efficientFrontier(inp, { wMin: c.wMin, wMax: c.wMax, nPoints: 3, nRandom: 50, seed: 42 });
      assert.deepEqual(again.random, first.random);
      assert.notDeepEqual(other.random, first.random);
    });
  });
}

describe('граничные случаи', () => {
  test('один актив: все веса = 1', () => {
    const one = { assets: ['A'], mu: [0.1], cov: [[0.04]], rf: 0.05 };
    const r = efficientFrontier(one, { nPoints: 5, nRandom: 10 });
    assert.equal(r.minVar.w.A, 1);
    close(r.maxSharpe.w.A, 1, 1e-12, 'maxSharpe');
    close(r.minVar.vol, 0.2, 1e-12, 'vol');
    close(r.minVar.sharpe, 0.25, 1e-12, 'sharpe');
  });

  test('два некоррелированных актива: minVar аналитически w1 = σ2² / (σ1² + σ2²)', () => {
    const two = { assets: ['A', 'B'], mu: [0.1, 0.05], cov: [[0.04, 0], [0, 0.01]], rf: 0 };
    const r = efficientFrontier(two, { nPoints: 5, nRandom: 100 });
    close(r.minVar.w.A, 0.2, 1e-9, 'w_A');
    // касательный при rf = 0: w ∝ Σ⁻¹μ = (2.5, 5) → (1/3, 2/3)
    close(r.maxSharpe.w.A, 1 / 3, 1e-6, 'maxSharpe w_A');
  });

  test('безрисковый актив (нулевая дисперсия): без исключений, σ minVar = 0, веса корректны', () => {
    // Задача макс. Шарпа некорректна (Шарп → ∞ при w_A → 0): Python возвращает None,
    // JS — «всё в Cash» с Шарпом NaN. Проверяем только отсутствие исключения и допустимость весов.
    const z = { assets: ['A', 'Cash'], mu: [0.12, 0.06], cov: [[0.04, 0], [0, 0]], rf: 0.05 };
    const r = efficientFrontier(z, { nPoints: 5, nRandom: 100 });
    close(r.minVar.w.Cash, 1, 1e-9, 'всё в Cash');
    close(r.minVar.vol, 0, 1e-12, 'σ = 0');
    close(r.maxSharpe.w.A + r.maxSharpe.w.Cash, 1, 1e-9, 'Σw maxSharpe');
  });

  test('почти безрисковый актив (σ² = 1e-8): maxSharpe как в Python (SLSQP) до 1e-3', () => {
    const z = { assets: ['A', 'Cash'], mu: [0.12, 0.06], cov: [[0.04, 0], [0, 1e-8]], rf: 0.05 };
    const r = efficientFrontier(z, { nPoints: 5, nRandom: 100 });
    close(r.maxSharpe.w.A, 1.74922065e-06, 1e-3, 'w_A');
    close(r.maxSharpe.sharpe, 100.0006124980036, 1e-6, 'sharpe');
  });

  test('одинаковые активы (вырожденная Σ): решение существует, Σw = 1', () => {
    const d = { assets: ['A', 'B'], mu: [0.1, 0.1], cov: [[0.04, 0.04], [0.04, 0.04]], rf: 0 };
    const r = efficientFrontier(d, { nPoints: 3, nRandom: 10 });
    close(r.minVar.w.A + r.minVar.w.B, 1, 1e-9, 'Σw');
    close(r.minVar.vol, 0.2, 1e-9, 'vol');
  });

  test('несовместимые ограничения (wMax·n < 1) → ошибка', () => {
    assert.throws(() => efficientFrontier(inp, { wMax: 0.2, nRandom: 10 }), /несовместимы/);
    assert.throws(() => solveQP([[1]], [0], 0, 0.5, 0.9), /несовместимы/);
  });
});
