# Контракт веб-версии (web/)

Статический сайт без сборки: HTML + CSS + ES-модули JavaScript. Открывается любым статическим
сервером, выкладывается в Yandex Object Storage. Расчёты идут в браузере пользователя.

## Владение

| Путь | Владелец |
|---|---|
| `web/lib/*.js` — данные, коэффициенты, бэктест, граница Марковица, стратегии | `api` |
| `web/index.html`, `web/css/`, `web/js/`, `web/vendor/` — интерфейс | `site` |
| `tests/web/` и `tests/test_web_*.py` | `tests` |

`web/js/` импортирует только `web/lib/` и `web/vendor/`. `web/lib/` не трогает DOM и не знает
об интерфейсе: чистые функции + загрузчики данных с инъекцией `fetch` (для тестов в Node).

## Источники данных в браузере

* **ISS Мосбиржи** — напрямую из браузера (CORS разрешён, проверено 06.10.2026).
  Индексы — только через `history` (свечи многих индексов на ISS начинаются с 2016 г.):
  `https://iss.moex.com/iss/history/engines/stock/markets/index/securities/{SECID}.json?iss.meta=off&from=…&till=…&history.columns=TRADEDATE,CLOSE&start=N`,
  пагинация по блоку `history.cursor` (INDEX, TOTAL, PAGESIZE).
  Прочие бумаги (акции, паи, `GLDRUB_TOM`) — дневные свечи основного режима:
  `/iss/securities/{SECID}.json?iss.only=boards` → строка с `is_primary=1` → engine/market/boardid →
  `/iss/engines/{e}/markets/{m}/boards/{b}/securities/{SECID}/candles.json?interval=24&from=&till=&start=N`
  (страницы по 500 строк, без cursor).
* **Банк России** — у cbr.ru нет CORS. Ряды берутся из файлов сборщика `public/data/*.json`
  (`cbr_gold`, `ruonia_index`, `ruonia_rate`, `key_rate`), формат:
  `{"id","title","unit","source","updated","first","last","data":[["YYYY-MM-DD", value], …]}`.
  Базовый путь — `DATA_BASE` из `web/js/config.js` (по умолчанию `"../public/data/"`; при выкладке
  подменяется).
* **Демо-режим** `?demo=1` (только локально, при запуске сервера из корня репозитория): месячные
  ряды из `../tests/fixtures/iss_monthly.csv`, `cbr_monthly.csv`, `iss_monthly_extra.csv`.
  Котировки Мосбиржи в `web/` не коммитим (инвариант 5 CLAUDE.md).

## Типы

```js
/** @typedef {{dates: string[], values: number[]}} Series   // даты "YYYY-MM-DD" по возрастанию */
/** @typedef {{dates: string[], cols: Object<string, number[]>}} Frame  // общий календарь */
```
Доли и доходности — **в долях** (0.123 = 12,3 %). Ставки годовые в долях.

## web/lib/data.js  (`api`)

```js
export const CATALOG                       // [{key, name, assetClass, source:'iss'|'cbr'|'chain', etf}]
                                           // ключи как в core/data/universe.py: MCFTR, IMOEX, MEBCTR,
                                           // IRDIVTR, MOEXEU, RGBITR, RUGBITR1Y, RUGBITR5+, RUGBITR10Y,
                                           // RUCBITR, RUCBTRNS, CORP_CHAIN, GOLD_CBR, GLDRUB_TOM, RUONIA
export async function fetchIssIndex(secid, from, till, {fetchImpl}={}) -> Series
export async function fetchIssSecurity(secid, from, till, {fetchImpl}={}) -> Series   // close
export async function loadCbrFile(id, {base, fetchImpl}={}) -> {meta, series: Series}
export function chain(oldS, newS, switchDate) -> Series          // как universe.chain
export async function loadAsset(key, from, till, {fetchImpl, dataBase, demo}={}) -> Series
                                           // GOLD_CBR -> cbr_gold, RUONIA -> ruonia_index,
                                           // CORP_CHAIN -> chain(RUCBITR, RUCBTRNS, '2018-12-29'),
                                           // индексы каталога -> fetchIssIndex, прочее -> fetchIssSecurity
export async function loadDemoFrame({base='../tests/fixtures/', fetchImpl}={}) -> Frame  // месячные фикстуры
export function alignFrame(seriesByKey, {mode='common'}={}) -> Frame
                                           // объединённый календарь, ffill; 'common' — обрезка
                                           // до даты, когда есть все ряды
export function resample(frameOrSeries, freq) -> same type   // 'D' | 'W' | 'M' | 'Q' | 'A': последнее значение периода
export function sliceFrame(frame, from, till) -> Frame
```
Кэш ответов — в памяти (Map) на время сессии.

## web/lib/metrics.js  (`api`)

Порт `core/analytics/metrics.py` **с теми же конвенциями** (результат на одинаковых входах совпадает
с Python до 1e-9; асимметрия/эксцесс — как pandas `.skew()`/`.kurt()`; перцентили — линейная
интерполяция как numpy):
```js
export const PERIODS = {D:252, W:52, M:12, Q:4, A:1}
export function toReturns(values) -> number[]
export function totalReturn(s), cagr(s), maxDrawdown(s), drawdownSeries(s) -> Series, calmar(s)
export function volatility(r, n), sharpe(r, rf, n), downsideDeviation(r, mar, n), sortino(r, rf, n)
export function beta(r, rm), correlation(r, rm), jensenAlpha(r, rm, rf, n), treynor(r, rm, rf, n)
export function trackingError(r, rm, n), informationRatio(r, rm, n), omega(r, threshold, n), schwager(r)
export function varHistoric(r, level=0.95), cvarHistoric(r, level=0.95)
export function computeAll(series, benchmark|null, rf, freq='M') -> MetricsReport
       // ключи как MetricsReport в Python: start, end, periods_per_year, total_return, cagr, volatility,
       // max_drawdown, sharpe, sortino, calmar, omega, schwager, var_95, cvar_95, skew, kurtosis,
       // positive_share, best_period, worst_period, days_to_recover, beta, alpha, treynor, m2,
       // tracking_error, information_ratio, correlation  (без бенчмарка последние — NaN)
export const LABELS_RU, PERCENT_FIELDS     // как в Python, порядок строк таблицы = порядок LABELS_RU
export function annualReturns(seriesOrFrame) -> {years, values} | {years, cols}  // по календарным годам, база 1-го года — первая точка (ui/common.annual_returns)
export function rebase(seriesOrFrame, base=100) -> same type  // / первое конечное значение × base; ведущие NaN остаются NaN
```
Ряды разных активов выравниваются по общим датам (inner join), как `pd.concat(join='inner')`.

## web/lib/backtest.js  (`api`)

Порт `core/analytics/backtest.py`, та же семантика (ребалансировка в последний торговый день периода,
коридор, комиссия, пополнения, НДФЛ):
```js
export const REBAL_LABELS
export function runBacktest(frame, {weights, initial=1e6, rebalance='A', band=null, commission=0,
                                    contribution=0, contributionFreq='M', taxRate=0, start, end})
  -> {equity: Series, invested: Series, weights: Frame, trades: [{date, asset, amount, reason}],
      rebalanceDates: string[], costs, taxes, turnover}
```

## web/lib/frontier.js  (`api`)

Без scipy: ограничения `Σw=1`, `wMin ≤ w ≤ wMax`. Любой корректный метод (проекция на «урезанный
симплекс» + градиентный спуск / активные множества). Точность весов портфелей мин. дисперсии и
макс. Шарпа — до 1e-3 от Python (`core/analytics/frontier.py`) на тех же входах.
```js
export function estimateInputs(frame, {freq='M', rf=0, muMethod='arith'}={}) -> {assets, mu, cov, rf}
export function efficientFrontier(inputs, {nPoints=40, wMin=0, wMax=1, nRandom=4000, seed=42}={})
  -> {frontier: [{ret, vol, sharpe, w}], random: [{ret, vol, sharpe}],
      minVar: {ret, vol, sharpe, w}, maxSharpe: {ret, vol, sharpe, w},
      assets: [{key, ret, vol, sharpe}]}
```

## web/lib/strategies.js  (`api`)

`STRATEGIES` и `BENCHMARKS` — те же составы, что в `core/analytics/strategies.py`.

## Интерфейс (`site`)

Одна страница, вкладки: **Бэктест**, **Граница Марковица**, **Коэффициенты бумаги** (тикер/индекс
против бенчмарка), **Методика**. Параметры: безрисковая ставка (по умолчанию 7,86 %), бенчмарк
(MCFTR), частота метрик (M). Состояние — в URL-параметрах (можно поделиться ссылкой).
Графики — библиотека в `web/vendor/` (не CDN: внешние CDN в России работают нестабильно).

## Проверка

`tests/web/*.test.mjs` (node:test) прогоняются из pytest-обёртки `tests/test_web_engine.py`, чтобы
`pytest -q` покрывал и JS. Эталоны: табл. 4 НИР (`scripts/reproduce_nir.py`) и результаты Python
на тех же фикстурах.
