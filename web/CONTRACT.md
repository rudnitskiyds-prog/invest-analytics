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
  (`cbr_gold`, `ruonia_index`, `ruonia_rate`, `key_rate`; безрисковая ставка `risk_free` — по данным ISS,
  но тоже файлом сборщика), формат:
  `{"id","title","unit","source","updated","first","last","data":[["YYYY-MM-DD", value], …]}`.
  Базовый путь — `DATA_BASE` из `web/js/config.js` (по умолчанию `"../public/data/"`; при выкладке
  подменяется).
* **Демо-режим** `?demo=1` (только локально, при запуске сервера из корня репозитория): месячные
  ряды из `../tests/fixtures/iss_monthly.csv`, `cbr_monthly.csv`, `iss_monthly_extra.csv`.
  Котировки Мосбиржи в `web/` не коммитим (инвариант 4 CLAUDE.md).

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
                                           // IRDIVTR, MOEXEU, MEEUTR, RGBITR, RUGBITR1Y, RUGBITR5+, RUGBITR10Y,
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
// безрисковая ставка (как core/data/risk_free.py)
export async function loadRiskFree({base, fetchImpl}={}) -> {meta, series: Series}   // risk_free.json
export async function riskFreeSeries(start=null, end=null, {base, fetchImpl}={}) -> Series
       // годовые ставки в ДОЛЯХ по датам публикации на [start, end] — rf для computeAll/metricsTable;
       // если start не дата публикации — первой точкой ставится start со ставкой, действовавшей на него
       // (раньше 30.12.2002 — первое значение)
export async function riskFreeMean(start=null, end=null, {base, fetchImpl}={}) -> number
       // средняя по календарным дням (доля) — скаляр rf для границы Марковица и CML
export function rateSlice(rate, start, end) -> Series, timeMean(rate, start, end) -> number   // чистые помощники
```
Кэш ответов — в памяти (Map) на время сессии.

### Безрисковая ставка: `public/data/risk_free.json`

Константы 7,86 % больше нет. Rf — ряд: доходность к погашению индекса коротких ОФЗ RUGBITR1Y (ISS, поле
`YIELD` истории индекса), до первой даты RUGBITR1Y (`splice_date` = 2011-01-11) — RGBEY, эффективная
доходность всего рынка ОФЗ ММВБ (ISS engine `state`, market `index`, режим GNDX, поле `CLOSE`) без поправки
на спред; до 30.12.2002 — первое известное значение. Нулевые значения RGBEY (сбои архива) отброшены.
Файл — общий формат сборщика, `unit: "доля годовых"` (значения / 100, 4 знака), плюс:
```json
{"id":"risk_free", "unit":"доля годовых", "first":"2002-12-30", "last":"…", "splice_date":"2011-01-11",
 "sources":[{"id":"RGBEY","from":"2002-12-30","till":"2010-12-30", …}, {"id":"RUGBITR1Y","from":"2011-01-11", …}],
 "data":[["2002-12-30",0.1359], …]}
```
Ряд ставок в `metrics.js`/`metrics.py` выравнивается на даты доходностей по **началу периода**
(`periodStarts`: для i ≥ 1 — предыдущая дата, для первой — на один шаг раньше): берётся последняя
публикация ≤ начала периода. `annualRf` (M², Мартин) — среднее значений переданного ряда, поэтому
передавайте ряд за окно расчёта. Python: `core.data.risk_free.rate_series / mean_rate / describe / current`.

## web/lib/metrics.js  (`api`)

Порт `core/analytics/metrics.py` **с теми же конвенциями** (результат на одинаковых входах совпадает
с Python до 1e-9; асимметрия/эксцесс — как pandas `.skew()`/`.kurt()`; перцентили — линейная
интерполяция как numpy):
```js
export const PERIODS = {D:252, W:52, M:12, Q:4, A:1}
export function toReturns(values) -> number[]
export function totalReturn(s), cagr(s), maxDrawdown(s), drawdownSeries(s) -> Series, calmar(s)
export function perPeriodRate(rf, n, dates=null), periodStarts(dates)   // ставка за период; ряд — на начало периода
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
против бенчмарка), **Методика**. Параметры: безрисковая ставка (ряд доходности коротких ОФЗ за период — `riskFreeSeries`), бенчмарк
(MCFTR), частота метрик (M). Состояние — в URL-параметрах (можно поделиться ссылкой).
Графики — библиотека в `web/vendor/` (не CDN: внешние CDN в России работают нестабильно).

## Проверка

`tests/web/*.test.mjs` (node:test) прогоняются из pytest-обёртки `tests/test_web_engine.py`, чтобы
`pytest -q` покрывал и JS. Эталоны: результаты Python
на тех же фикстурах.

---

# Этап 1: многостраничный сайт и страница бумаги (контракт api ↔ site)

Ориентир — блоки страницы бумаги PortfoliosLab (`docs/STRUCTURE.md`, п. 2), данные только российские.
Маршрутизация — по hash (статический хостинг без настроек сервера): `#/`, `#/symbol/{TICKER}`,
`#/tools`, `#/tools/backtest`, `#/tools/frontier`, `#/tools/metrics` (бывшая вкладка «Коэффициенты бумаги»),
`#/docs` (методика), `#/screener/stocks|funds|bonds`, `#/portfolios/lazy` (последние — заглушки «этап 3»).
Старые ссылки `?tab=backtest` и т. п. перенаправляются на новые маршруты; параметры состояния — как раньше.

## Новые функции `web/lib/metrics.js` (api) — и те же в `core/analytics/metrics.py` (snake_case)

Все — чистые функции; `s` — Series цен, `r`/`rm` — массивы доходностей, `n` — периодов в году.
```js
export function ulcerIndex(s)                       // sqrt(mean(dd_t^2)), dd_t = s_t/max_{≤t} − 1 (доли, ≤ 0)
export function martin(s, rf = 0)                   // (CAGR − rf) / ulcerIndex(s); rf — годовая
export function rSquared(r, rm)                     // correlation(r, rm)^2
export function captureRatios(r, rm)                // {up, down}: up = Σ r_t / Σ rm_t по t: rm_t > 0,
                                                    //   down — по t: rm_t < 0 (арифметический вариант; NaN, если нет периодов)
export function rollingVolatility(s, window = 21, n = 252) -> Series   // ст. откл. доходностей окна × sqrt(n)
export function parkinson(ohlc, n = 252)            // ohlc = {dates, open, high, low, close}; годовая
export function garmanKlass(ohlc, n = 252)
export function rogersSatchell(ohlc, n = 252)
export function yangZhang(ohlc, n = 252)            // k = 0.34 / (1.34 + (N+1)/(N−1))
export function periodReturns(s, asOf = последняя дата)
  -> {'1D','1W','1M','6M','YTD','1Y','3Y','5Y','10Y','ALL': {ret, cagr|null, from}}  // cagr только для > 1 года;
                                                    // точка начала — последняя дата ≤ asOf − период; нет истории -> null
export function momentum(s)
  -> {sma50, sma200, aboveSma50, aboveSma200, high52, distHigh52, mom6, mom12, score}
     // score 0–100 = среднее пяти подбаллов: цена > SMA50 (0/100), цена > SMA200 (0/100),
     // SMA50 > SMA200 (0/100), mom6 и mom12 — clamp(50 + 250·mom, 0, 100); mom6/12 — доходность 6/12 мес. без последнего месяца
export function monthlyGrid(s)
  -> {years: number[], cells: (number|null)[][] /* год × 12 */, yearTotal: (number|null)[], medianByMonth: (number|null)[]}
export function topDrawdowns(s, k = 5)
  -> [{depth, peak, trough, recovery|null, daysToTrough, daysToRecover|null}]   // по глубине, без перекрытий
export function currentDrawdown(s) -> {depth, peak, days}
export function avgDrawdown(s)                       // средняя глубина эпизодов просадки
export function totalReturnSeries(close, dividends)  // dividends: [{exDate, value}] — реинвестирование в дату отсечки;
                                                    // exDate = следующий торговый день после last_buy_date
```
`computeAll` дополнительно отдаёт `martin, ulcer_index, r_squared, up_capture, down_capture`
(без бенчмарка последние три — NaN); `LABELS_RU`/`PERCENT_FIELDS` дополнены. Формулы — в четырёх местах
(инвариант 2 CLAUDE.md): методику (`pages/7_Методика.py`, вкладка «Методика» в `web/`) обновляет `site`.

Уточнения реализации (api, 08.10.2026):
* Порядок ключей `MetricsReport`/`computeAll`: …, `days_to_recover`, **`martin`, `ulcer_index`**, `beta`, …,
  `correlation`, **`r_squared`, `up_capture`, `down_capture`**. В `LABELS_RU`: `ulcer_index` после `max_drawdown`,
  `martin` после `calmar`, `r_squared`/`up_capture`/`down_capture` после `correlation` (теперь 29 строк).
  В `PERCENT_FIELDS` добавлены `ulcer_index`, `up_capture`, `down_capture` (16 полей). Мартин в `computeAll` —
  `martin(p, annualRf(rf))` на ресемплированных ценах.
* Python-имена: `ulcer_index, martin, r_squared, capture_ratios, rolling_volatility, parkinson, garman_klass,
  rogers_satchell, yang_zhang, period_returns, momentum, monthly_grid, top_drawdowns, current_drawdown,
  avg_drawdown, total_return_series` (`core/analytics/metrics.py`). Ключи словарей — как в JS (camelCase:
  `daysToTrough`, `distHigh52`, `yearTotal`, …), даты — строки `YYYY-MM-DD`, «нет значения» — `None`/`null`.
  OHLC в Python — DataFrame с колонками open/high/low/close или тот же dict, что в JS.
* OHLC-оценки: строки, где хоть одна из O/H/L/C не > 0, отбрасываются. Янг–Чжан — по парам соседних строк
  (t−1, t), где обе корректны: некорректная строка исключает пары с ней, соседи не склеиваются; N — число пар (≥ 2).
* Сплиты (Python и JS, паритет): `splitFactors(dates, splits) -> number[]` / `split_factors`,
  `adjustSplits(x, splits)` / `adjust_splits(x, splits)`. splits — `[{date|tradedate, before, after}]` (сплит 1:100 —
  before=1, after=100; консолидация VTBR 5000:1 — before=5000, after=1). Цены всех дат **строго до** даты сплита
  умножаются на before/after (ряд в текущих акциях), volume делится; x — Series `{dates, values}` или OHLC
  (Python — Series/DataFrame/тот же dict). Некорректные записи (нет даты, before/after ≤ 0) пропускаются.
  Дата сплита — первый день торгов в новых акциях (tradedate ISS), поэтому «строго до». Только для рядов из
  `/history`: свечи ISS уже скорректированы (см. ниже).
* `rollingVolatility` возвращает только точки с полным окном.
* `periodReturns`: 1D/1W — календарные дни, месяцы/годы — календарные с ограничением дня концом месяца
  (как `pd.DateOffset`); значение конца — последняя точка ≤ asOf.
* `export function periodStart(dates, period, asOf = последняя дата) -> string | null` (Python —
  `period_start(dates, period, as_of=None)`): дата начальной точки периода `'1D'|'1W'|'1M'|'6M'|'YTD'|'1Y'|'3Y'|'5Y'|'10Y'|'ALL'`
  — последняя дата ≤ asOf − период (YTD — ≤ 31.12 прошлого года, ALL — первая дата), нет такой — null;
  неизвестный период — исключение. `periodReturns` выбирает начало через неё; интерфейс использует её для
  периодов графика, а не считает границы сам.
* `momentum`: high52 — максимум цен за 365 дней до последней даты; `score` — среднее **доступных** подбаллов
  (при истории < 200 точек SMA200-подбаллы не участвуют); нет ни одного — `null`.
* `monthlyGrid`: если в первом месяце ряда одна точка (месячные данные), первый месяц — только база (ячейка `null`).
* `topDrawdowns`/`avgDrawdown`: эпизод — от последней точки максимума до первого возврата к нему; текущий
  невосстановленный эпизод входит (recovery = null).
* `totalReturnSeries`: дивиденд относится на первую дату ряда ≥ exDate.

## `web/lib/symbol.js` (api) — данные страницы бумаги

```js
export const SYMBOL_CLASSES = {share:'Акция', fund:'Фонд', bond:'Облигация', index:'Индекс', metal:'Металл', currency:'Валюта'}
export async function searchSecurities(q, {fetchImpl, limit=20}={})
  -> [{secid, name, shortName, isin, cls, primaryBoard, isTraded}]           // ISS /securities.json?q=
export async function fetchSecurityInfo(secid, {fetchImpl}={})
  -> {secid, name, shortName, isin, cls, typeLabel, issuer|null, listLevel|null, currency,
      issueDate|null, firstTradeDate|null, faceValue|null, matDate|null, couponPercent|null,
      couponPeriod|null, offerDate|null, board, engine, market, benchmark}   // ISS /securities/{id}.json (description+boards)
      // benchmark по умолчанию: share/fund -> MCFTR, bond -> RGBITR (ОФЗ) / RUCBTRNS (корп.), index -> IMOEX, metal -> GOLD_CBR
export async function fetchSnapshot(secid, {fetchImpl}={})
  -> {date, last, prevClose, change, changePct, high52, low52, valueRub, marketCap|null,
      bond: {yield, duration, accruedInt, couponValue, nextCoupon}|null}   // marketdata + securities текущего режима
export async function fetchOHLC(secid, from, till, {fetchImpl}={})
  -> {dates, open, high, low, close, volume}         // дневные свечи основного режима (индексы — history: open/high/low/close)
export async function loadDividends(secid, {base, fetchImpl}={})
  -> [{recordDate, exDate, value, currency, yield|null, cancelled}]      // public/data/dividends.json (T-Invest)
export async function loadCoupons(secid, {fetchImpl}={})
  -> {coupons:[{date, value, rate}], amortizations:[{date, value}], offers:[{date, type}]}   // ISS bondization
export async function loadFundInfo(secid, {base, fetchImpl}={})
  -> {info: {issuer, commission_pct, aum_rub, asset_class, ...}|null,
      market: {min, p25, median, p75, max, n, classMedian}}     // rusetfs_funds.json; рынок — торгуемые фонды того же класса
export function dividendStats(divs, close)          // Series цен
  -> {ttmValue, ttmYield, byYear:[{year, value}], byMonth:number[12], growthStreak, payoutsPerYear}
export async function loadSymbolStats({base, fetchImpl}={}) -> {updated, items: {SECID: {...}}} | null   // public/data/symbol_stats.json
```
Все сетевые функции принимают `fetchImpl` (тесты), кэш — общий из `data.js`.

Уточнения реализации (api, 08.10.2026):
* `data.js` дополнительно экспортирует `getCached, qs, block, todayIso` (общий кэш для `symbol.js`),
  `fetchBoards(secid)` (блок boards, кэш общий с `resolveSecurity`), `fetchHistoryPages(urlFor, fetchImpl)`
  (history: первая страница, затем остальные параллельно по TOTAL/PAGESIZE), `mapLimit`, `ISS_PARALLEL = 6`.
  `fetchIssIndex` грузит страницы history параллельно (URL и результат — прежние).
* **Сплиты в ISS (проверено в браузере на живом ISS 08.10.2026):**
  - свечи (`candles`, interval 24 и 31; режимы TQBR, EQBR, TQTF) **уже скорректированы биржей задним числом**:
    GMKN 25.03.2024 close 151.38 (сплит 1:100 от 08.04.2024, реальная цена ~15 054), GMKN EQBR 2010 ≈ 51,
    VTBR 08.07.2024 = 99.65 (консолидация 5000:1 от 15.07.2024, реальная 0.01992); PLZL, TRNFP, FXRU — так же;
    месячные свечи GMKN 12.2023–06.2024 без скачка. Корректировать свечи нельзя — будет двойная корректировка;
  - **не скорректирован только `/history`** (LEGALCLOSEPRICE GMKN 03.04.2024 = 15054, VTBR 10.07.2024 = 0.01992);
  - `/iss/statistics/engines/stock/splits.json` — `{"splits":{"columns":["tradedate","secid","before","after"],"data":[…]}}`,
    57 строк, без cursor; `tradedate` — первый день торгов в новых акциях (GMKN 2024-04-08, CLOSE 152.96); есть служебные
    строки вида FIXGMKN. `/splits/{secid}.json` — тот же формат.
  - `candleborders.json` — `{"borders":{"columns":["begin","end","interval","board_group_id"],"data":[…]}}`, `begin` со
    временем (`"1997-09-22 00:00:00"`); берётся строка `interval = 24`. У IMOEX дневные свечи — с 1997-09-22.
* `export async function fetchSplits(secid, {fetchImpl}) -> {splits: [{date, before, after}], ok}`: общий список
  `splits.json` (пагинация по `splits.cursor`, если появится, иначе по start, пока приходят новые строки), при
  недоступности — `splits/{secid}.json`; оба недоступны — `ok: false` (по одной попытке, без задержек; результат
  запоминается на сессию). `fetchSplits`/`adjustSplits`/`splitFactors` (и Python `iss.splits`, `metrics.adjust_splits`)
  — **утилиты только для рядов из `/history`**. Сейчас акции и фонды из `/history` нигде не берутся (history — только
  индексы, у которых сплитов нет, и облигации в Python-приложении), поэтому утилиты нигде не применяются.
* `fetchOHLC(secid, from, till, {fetchImpl, adjust = false})` -> `{dates, open, high, low, close, volume, splitAdjusted}`:
  - акции/фонды/облигации/металлы — свечи основного режима + прежние режимы того же рынка до `history_from`
    основного (EQBR и др. до 2013 г., **TQTF у фондов до июня 2026 г.**; LEGACY_MAIN_BOARDS или та же
    `board_group_id`), при совпадении даты — основной режим; диапазон делится на куски по 700 дней, куски грузятся
    параллельно (≤ 6), внутри куска — страницы по 500;
  - индексы — дневные свечи рынка index (`/engines/stock/markets/index/securities/{id}/candles.json?interval=24`)
    с даты начала свечей (`candleborders.json`, interval 24) и history только для периода до неё (страницы
    параллельно); `candleborders` недоступен — только history, как раньше;
  - сплиты не запрашиваются и не применяются (свечи скорректированы биржей), `splitAdjusted: true` всегда;
    `adjust: true` дополнительно применяет `fetchSplits` + `adjustSplits` — для свечей это двойная корректировка,
    опция оставлена только для отладки.
* `fetchSnapshot(secid, {fetchImpl, ohlc})`: `ohlc` — готовый результат `fetchOHLC` (high52/low52 и запасная
  цена — по нему, без отдельного запроса свечей); без `ohlc` — свечи за 380 дней, как раньше.
* `data.js` `fetchIssSecurity`/`loadAsset` берут те же свечи ISS — они тоже скорректированы на сплиты биржей
  (без подклейки TQTF у фондов — она есть только в `fetchOHLC`).
* `symbol.js` экспортирует также `classify({type, group, secid, market, sectype})` и `DEFAULT_BENCHMARKS`.
* `fetchLastPrices(secids, {fetchImpl}) -> {SECID: {last, prevClose, change, changePct, board, currency}}` — последние
  цены двумя параллельными запросами ISS (`markets/shares` и `markets/index`, `securities=A,B,…`,
  `iss.only=securities,marketdata`), без свечей. last — LAST → LCURRENTPRICE → PREVPRICE (индексы — CURRENTVALUE →
  LASTVALUE); prevClose — PREVPRICE (индексы — LASTVALUE); change — доля, changePct — в %; режим — TQBR, затем TQTF,
  затем любой; currency — ISO ('SUR' → 'RUB'), у индексов null. Не найдено (облигации, металлы, сбой) — ключа нет.
  Класс: `metal` (GLDRUB_TOM и др., *metal*), `index`, `bond`, `fund` (*ppif*/*etf*, SECTYPE J/9/A/B),
  `share` (*share*/*depositary*, SECTYPE 1/2/D), `currency`; иначе `null`.
* `fetchSecurityInfo`: `couponPercent` — в % годовых (как в ISS, по имени поля), `couponPeriod` — дней
  (COUPONPERIOD или round(364 / COUPONFREQUENCY)), `firstTradeDate` — самая ранняя `history_from` режимов
  того же рынка (для SBER — EQBR), `issuer` — `emitent_title` из `/securities.json?q=` (ошибка — null).
  Для IMOEX бенчмарк — MCFTR (не сам с собой); для валют — null.
* `fetchSnapshot`: `changePct` — доля; облигации: цены — % номинала, `bond.yield` — доля, `bond.duration` — лет.
* `fetchOHLC` для индексов: `volume` — NaN; пропуски O/H/L — NaN.
* `loadDividends`: дополнительно `paymentDate, declaredDate`; `yield` — доля (`yield_value` T-Invest в %);
  `exDate` — следующий рабочий день (пн–пт, без учёта праздников) после `last_buy_date`, без неё — `recordDate`.
* `dividendStats` (без отменённых и нерублёвых, на последнюю дату `close`): `byMonth` — **число** выплат по
  месяцам exDate; `growthStreak` — число подряд идущих лет, считая назад от последнего полного года, где сумма за год больше
  суммы предыдущего года; год первой выплаты ростом не считается (год без выплат внутри истории — сумма 0,
  рост после него засчитывается);
  `payoutsPerYear` — среднее число выплат за последние 3 полных года; дополнительно `upcoming` — объявленные
  выплаты с exDate позже даты расчёта.
* `loadCoupons`: `rate` — доля годовых (`valueprc / 100`), `value` — `value_rub` или `value`, неизвестный — null.
* `loadFundInfo`: статистика комиссий — в % годовых (как `commission_pct`); торгуемые — `trade_status`
  «Торгуется» (или не указан) с комиссией > 0; `classMedian` = `median`; дополнительно `marketMedian` —
  медиана по всем торгуемым фондам. Нет файла — `{info: null, market: {… null, n: 0}}`.

## Ночной пересчёт `public/data/symbol_stats.json` (api, сборщик)

Источник `symbol_stats` в `scripts/collect_data.py` (kind `"iss"`, после RusETFs и T-Invest, бюджет 10 мин,
отдельный шаг workflow «Рейтинги» с `timeout-minutes: 15`). Бумаги: акции TQBR (SECTYPE 1/2/D) и фонды
(J/9/A/B) с историей ≥ 36 мес. Месячные цены — ISS свечи `interval=31` (один запрос на бумагу), окно — до 10 лет.
По каждой: `sharpe, sortino, omega, calmar, martin, cagr, volatility, max_drawdown, months` (rf — ключевая ставка ЦБ,
среднее за окно) и `rank: {sharpe, sortino, omega, calmar, martin}` — перцентиль 0–100 внутри класса,
`score` — среднее пяти рангов. Формат: `{"updated","source":"ISS MOEX (расчёт ИнвестАналитики)","rf","window",
"classes":{"share":{"n":…},"fund":{"n":…}},"items":{"SBER":{"class":"share", …}}}`. Санити: ≥ 100 акций.
Котировки в файл не пишутся — только рассчитанные показатели.

Уточнения реализации (api, 08.10.2026):
* Источник `default: False`: полный прогон без аргументов его не запускает (тесты `main([])` не уходят в ISS);
  запуск — `--only symbol_stats`. Новый флаг `--skip ИСТОЧНИК…` (после `--only`); шаг «Сбор» — `--skip symbol_stats`.
  Шаг «Рейтинги» — `if: success() || failure()` (выполняется и при коде 1 «Сбора»), «Сохранить изменения» — после него, `if: always()`.
* Окно — полные месяцы: `till` = конец прошлого месяца, база — цена на конец месяца за 120 мес. до `till`;
  `window = {"from", "till", "max_months": 120, "min_months": 36}`; в рейтинг — бумаги с ≥ 36 месячными доходностями.
  Акции — по **полной доходности**: `total_return_series(месячные цены, дивиденды)`, дивиденды из
  `public/data/dividends.json` (рублёвые, не отменённые; exDate — как в `loadDividends`) относятся на первую
  месячную дату ряда ≥ exDate (свечи ISS и дивиденды T-Invest — в текущих акциях, отдельной корректировки на
  сплиты нет); фонды — по ценам (дивидендов фондов в файле нет). Верхнее поле `"total_return": true`
  (false — `dividends.json` не найден, акции тоже по ценам), в каждой записи `"tr": true|false`.
  Верхнее поле `"split_adjusted": true` — по источнику (месячные свечи ISS скорректированы биржей); сплиты в прогоне
  не запрашиваются. Фонды — месячные свечи TQTF + TQBR (`iss.monthly_closes(…, legacy_boards=("TQTF",))`).
  Python: `iss.splits(secid=None)` (утилита для /history), `iss.monthly_closes(secid, start, end=None, board="TQBR",
  engine, market, legacy_boards=(), adjust_splits=False)` (s.attrs["split_adjusted"] = True), `iss.close_series(…,
  adjust_splits=False)` — поведение по умолчанию прежнее.
* `rf` — средняя по календарным дням безрисковая ставка (доля; доходность коротких ОФЗ) из
  `public/data/risk_free.json`; нет файла — сбой источника. Верхнее `rf` — за всё окно (справочно),
  `items[*].rf` — за период самой бумаги (по нему и считаются её коэффициенты).
* Ранг: `(средний ранг по возрастанию − 1) / (N − 1) × 100` внутри класса, 1 знак; одна бумага — 50; нет значения — null.
  `score` — среднее доступных рангов. Коэффициенты округлены до 6 знаков, `months` — число месячных доходностей.
* Python: `core/analytics/ranking.py` (`symbol_metrics, percentile_ranks, rank_items, mean_rate, ex_dividends`),
  `core/data/iss.monthly_closes`; загрузчик подменяется через `scripts.collect_data._symbol_stats_loader`
  (методы `securities()` -> DataFrame SECID/SECTYPE, `monthly_closes(secid, start, end)` -> Series),
  часы бюджета — `scripts.collect_data._clock`. Сбой: > 20 % бумаг, 10 сбоев подряд, бюджет или < 100 акций.

## Страница бумаги в Streamlit: `core/analytics/symbol_page.py` (api) ↔ `pages/2_Карточка_бумаги.py` (site)

Python-аналог `web/lib/symbol.js` + расчётов `web/js/page-symbol.js`; интерфейс только вызывает эти функции
(инвариант 3) и кэширует их результаты сам (`st.cache_data`) — результаты picklable, функции без Streamlit.
```python
symbol_page.load_symbol(secid, years=None) -> {secid, info, ohlc, snapshot, dividends, coupons, fund, rating, errors}
symbol_page.load_benchmark(key, start=None) -> pd.Series      # только живые данные, сбой — исключение
symbol_page.report(data, bench, rf) -> {series, total_return, close, benchmark, periods, momentum, monthly, metrics,
    metrics_common, common_from, common_to, drawdowns, volatility, dividend_stats, errors}
public_data.symbol_stats() -> dict | None                     # public/data/symbol_stats.json целиком
```
* Ключи `info`/`snapshot`/`dividends`/`fund` — snake_case-аналоги `fetchSecurityInfo`/`fetchSnapshot`/`loadDividends`/
  `loadFundInfo` (`short_name`, `type_label`, `change_pct`, `ex_date`, `class_median`, …); `ohlc` — DataFrame
  open/high/low/close/volume (`iss.daily_ohlc` — порт `fetchOHLC`, подклейка EQBR/TQTF, у индексов candleborders + history);
  `coupons` — `iss.coupons` (только облигации); `rating` — `items[secid]` + `n_class`, `updated`, `total_return`
  (нет файла — None и текст в `errors["rating"]`, бумаги нет в рейтинге — None без ошибки).
* Ключи грузятся независимо (ошибка — None + `errors[ключ]`). Справочник ISS запрашивается один раз; его сбой —
  `info`/`ohlc`/`snapshot` = None с той же причиной, другие запросы ISS не делаются. `years` ограничивает начало
  свечей (у индексов — и history). Отказ candleborders запоминается на процесс на 10 мин (`iss.index_candle_begin`).
* `load_benchmark`: GOLD_CBR, RUONIA, CORP_CHAIN — `universe._load_live`, прочие — `iss.daily_ohlc(key)["close"]`;
  демо-подмены `universe.load_series` нет. Если бумага сама бенчмарк — интерфейс передаёт `bench=None`.
* `report`: `series` — полная доходность для акций с рублёвыми неотменёнными дивидендами (`total_return=True`), иначе
  цена; бенчмарк отклоняется (`errors["benchmark"]`, далее как без бенчмарка), если его последняя дата раньше последней
  даты бумаги > 10 календарных дней или медианный шаг дат > 5 дней. `benchmark` — бенчмарк на даты бумаги
  (последнее известное значение ≤ даты, с первой даты бенчмарка); по нему — `periods.benchmark`, `metrics`
  (`compute_all(series, benchmark, rf, 'D')` и `compute_all(benchmark, benchmark, …)`), ряды просадок и скользящей
  волатильности бенчмарка. `metrics_common` — те же отчёты только по общим датам (пересечение, без протягивания),
  `common_from`/`common_to` — его границы. `volatility` — rolling (21 день) + `close_to_close`, Паркинсон, Гарман–Класс,
  Роджерс–Сатчелл, Янг–Чжан по OHLC за `period_start('1Y')`; `dividend_stats` — порт `dividendStats` (snake_case:
  `ttm_value, ttm_yield, by_year, by_month, growth_streak, payouts_per_year, upcoming`), только акции.
* `ranking.next_weekday` — публичное имя (`_next_weekday` оставлен алиасом).

## Ассистент и выкладка в Yandex Cloud (исторически — GigaChat; теперь YandexGPT, см. ниже) (ветка `feature/yc-gigachat`)

> ⚠ Разделы про GigaChat (OAuth, сертификат Минцифры, `GIGACHAT_*`) устарели — вызов модели описан в разделе «Ассистент: YandexGPT вместо GigaChat» в конце файла.

Архитектура (вариант A): всё в Yandex Cloud, сайт закрыт логином и паролем.

```
браузер ──► API Gateway (один домен *.apigw.yandexcloud.net)
              ├─ /{path+}          → Object Storage, закрытый бакет (копия web/, public/data/, tests/fixtures/)
              ├─ /                 → редирект на /web/
              ├─ POST /api/assistant → Cloud Function `assistant` (functions/assistant/)
              └─ все маршруты защищены авторизатором: Cloud Function `auth` (functions/auth/), Basic-авторизация
```

Секреты — только в Yandex Lockbox / переменных функций и в GitHub Secrets; в репозитории и в браузере их нет.

### Cloud Function `assistant` (Python 3.12, `functions/assistant/index.py: handler(event, context)`) — зона `backend`
Запрос `POST /api/assistant`, JSON:
```json
{"question": "строка до 1000 знаков",
 "screen": {"route": "/tools/backtest", "title": "Бэктест портфелей",
            "facts": [{"id": "pf1.cagr", "label": "Лежебока плюс: в среднем в год", "value": 0.123, "unit": "pct",
                       "text": "12,3 %", "source": "расчёт ИнвестАналитики по ISS MOEX, 2011–2025"}]}}
```
Ответ 200: `{"answer": "текст", "facts_used": ["pf1.cagr"], "unverified_numbers": ["15 %"], "label": "Ответ ИИ (GigaChat). Не является индивидуальной инвестиционной рекомендацией."}`.
Ошибки: 400 (`{"error": "…"}` — пустой/длинный вопрос, > 60 фактов, неверный JSON), 429 (лимит), 502 (GigaChat недоступен) — тексты на русском.
* Числа считает только расчётное ядро: модель получает факты экрана и должна ссылаться на них. Сервис сверяет все числа
  ответа с `facts[].value`/`text` (нормализация: пробелы, запятая, %, ₽; допуск округления) — не найденные попадают
  в `unverified_numbers`, интерфейс помечает их.
* Нет индивидуальных рекомендаций (39-ФЗ, ст. 6.1): системный промпт `functions/assistant/prompts/system.md`
  (зона `ai`) + серверный фильтр вопросов «что купить/продать/куда вложить» → вежливый отказ без вызова модели.
* Персональные данные не нужны и не логируются: в журнале только маршрут, длины, коды ответа и время.
* GigaChat: OAuth `https://ngw.devices.sberbank.ru:9443/api/v2/oauth` (ключ авторизации `GIGACHAT_AUTH_KEY`,
  `GIGACHAT_SCOPE`, по умолчанию `GIGACHAT_API_PERS`), токен кэшируется до истечения; чат
  `https://gigachat.devices.sberbank.ru/api/v1/chat/completions`, модель `GIGACHAT_MODEL` (по умолчанию `GigaChat`).
  TLS — сертификат НУЦ Минцифры: путь в `GIGACHAT_CA_BUNDLE`, файл кладёт в пакет функции workflow выкладки.
* Лимиты: `RATE_LIMIT_PER_MIN` (по умолчанию 10) на IP в памяти экземпляра; `max_tokens` ответа ≤ 800.
* Зависимости — только `requests`.

### Cloud Function `auth` (`functions/auth/index.py: handler(event, context)`) — зона `backend`
Авторизатор API Gateway: проверяет заголовок `Authorization: Basic …` против `SITE_USERS`
(`логин:соль_hex:pbkdf2_sha256_hex` через запятую, 200 000 итераций, из Lockbox). Сравнение — постоянное по времени.
Окно входа: маршрут `/` без авторизатора вызывает ту же функцию — без пароля 401 + `WWW-Authenticate: Basic`,
с паролем 302 на `/web/`; браузер дальше сам шлёт Basic на все маршруты (они под авторизатором).

### `infra/` — зона `backend`
`infra/gateway.yaml` (OpenAPI-спецификация API Gateway с подстановками `${BUCKET}`, `${ASSISTANT_FUNCTION_ID}`,
`${AUTH_FUNCTION_ID}`, `${SA_ID}`), `infra/README.md` — пошаговая настройка облака для владельца (консоль или `yc`),
список секретов GitHub, бюджет-уведомление.

### Выкладка `.github/workflows/deploy-yc.yml` — зона `api`
Триггеры: push в `main`, завершение «Сбор данных», ручной запуск. Если секретов нет — job завершается успешно
с предупреждением. Шаги: синхронизация `web/`, `public/data/`, `tests/fixtures/` в бакет; новые версии функций
`assistant` (с сертификатом Минцифры) и `auth`; обновление спецификации шлюза. Два сервисных аккаунта: `ia-deploy` (ключ в `YC_SA_JSON_CREDENTIALS`, без доступа к секретам Lockbox) и
`ia-runtime` (`YC_SA_ID`: функции и шлюз). Секреты: `YC_SA_JSON_CREDENTIALS`,
`YC_CLOUD_ID`, `YC_FOLDER_ID`; переменные: `YC_BUCKET`, `YC_GATEWAY_ID`, `YC_SA_ID`, `YC_LOCKBOX_SECRET_ID`.

### Панель ассистента в `web/` — зона `site`
* `web/js/config.js`: `ASSISTANT_URL = "/api/assistant"` (тот же домен).
* `web/js/assistant.js`: кнопка «Спросить ИИ» (плавающая, видна на `/tools/*` и `/symbol/*` всегда — без фактов
  ассистент отвечает про термины) → панель (на телефоне — на весь экран) с полем вопроса, примерами вопросов и ответом.
  Ответ подписан «Ответ ИИ», числа из `unverified_numbers` подсвечены с пояснением «не найдено в расчёте».
* `value` — число или `null` (даты и периоды — только в `text`).
* Каждая страница регистрирует сборщик фактов: `registerFacts(route, () => facts[])` — берёт уже посчитанные
  интерфейсом значения (не считает заново). Если `fetch` недоступен (локально, демо) — понятное сообщение
  «Ассистент работает на опубликованном сайте».

## FinServ: редизайн, главная, логотипы компаний, скринеры, портфели (ветка `feature/finserv-redesign`)

Бренд — **FinServ** (везде вместо «ИнвестАналитика»: интерфейс, заголовки, источники «расчёт FinServ», промпт).
Режим «Просто / Подробно» убирается: одна версия, полная и понятная (подсказки «?» и «Если коротко» остаются).
Ориентир — информационная архитектура главной portfolioslab.com (набор и порядок блоков), но оформление,
тексты, графика и логотип — свои; тема **светлая**; данные — только российские.

### Дизайн-токены (`web/css/tokens.css`, подключается первым; все экраны используют только их)
```css
--font: "Montserrat", system-ui, sans-serif;   /* @font-face: web/vendor/fonts/montserrat-{cyrillic,latin}-wght-normal.woff2, 100–900 */
--bg: #F6F7F9;  --surface: #FFFFFF;  --surface-2: #F1F3F6;  --border: #E3E7ED;  --border-strong: #CBD2DC;
--text: #0E1726; --text-muted: #5B6676; --brand: #0F766E; --brand-strong: #0B5D57; --brand-soft: #E6F4F2;
--accent: #F59E0B; --pos: #15803D; --neg: #DC2626; --chart-grid: #E8ECF1;
--radius: 14px; --radius-sm: 8px; --shadow: 0 1px 2px rgb(14 23 38 / .06), 0 4px 16px rgb(14 23 38 / .06);
```
Тёмная тема не поддерживается (prefers-color-scheme игнорируем, `color-scheme: light`).

### Логотип FinServ — `web/img/finserv-logo.svg` (знак) и `web/img/finserv-wordmark.svg`
Свой: геометрический знак в скруглённом квадрате цвета `--brand`, читается и как «F», и как растущий график;
работает в 16 px (favicon) и 40 px (шапка).

### Логотипы компаний — `web/js/logo.js` (site) ← данные сборщика (api)
* api: в `public/data/tinvest_shares.json` и `tinvest_etfs.json` у каждой записи `logo` (имя файла из T-Invest
  `brand.logo_name`, например `"sber.png"`) и `logo_color` (`brand.logo_base_color`, `#RRGGBB`) — если есть.
* `logoFor(secid)` → `{url|null, color, initials}`; url = `https://invest-brands.cdn-tinkoff.ru/<имя без .png>x160.png`.
  `assetBadge(secid, size=24)` — круглый `<img>` (alt="", loading="lazy", ошибка загрузки → кружок с 1–2 буквами
  тикера на `logo_color` или нейтральном фоне). Индексы, металлы, ОФЗ — кружок с иконкой класса.
* Источник фиксируется в `docs/DATA_PERMISSIONS.md` как часть разрешения Т-Банка (T-Invest API).

### Скринеры `/screener/stocks|funds|bonds` — данные считает сборщик, браузер только фильтрует
* api, `web/lib/screener.js`:
  `loadScreener(kind, {base}) -> {updated, source, columns: [{key, label, type: "pct"|"num"|"rub"|"text"|"date", group}], rows: [{secid, name, ...}]}`
  * `stocks`: join `symbol_stats.json` (class share) + `tinvest_shares.json` (имя, сектор, лого) + `fundamentals.json`
    (P/E, P/B, EV/EBITDA, ROE, див. доходность) — колонки: тикер, название, сектор, CAGR, волатильность,
    макс. просадка, Шарп, Сортино, рейтинг (score), P/E, P/B, ROE, див. доходность.
  * `funds`: `symbol_stats` (class fund) + `rusetfs_funds.json` (УК, СЧА, комиссия, класс активов) + `tinvest_etfs.json`.
  * `bonds`: новый файл сборщика `public/data/screener_bonds.json` (ISS TQOB, TQCB; раз в сутки): secid, name,
    board, yield (доходность к погашению/оферте), duration_days, coupon_pct, coupon_freq, maturity_date, offer_date,
    listing_level, volume_rub (оборот за день), price_pct, nkd.
  * `filterRows(rows, filters)`, `sortRows(rows, key, dir)` — чистые функции (тест в tests/web).
  * **Фактические ключи колонок** (`COLUMNS[kind]` в `web/lib/screener.js`; pct — доли):
    * stocks: `secid, name, sector` (рус. подпись сектора T-Invest), `cagr, volatility, max_drawdown` (pct),
      `sharpe, sortino, score` (num), `pe, pb, ev_ebitda` (num), `roe, dividend_yield` (pct; из % T-Invest / 100).
      Строки — объединение акций symbol_stats и tinvest_shares (без 36 мес. истории — коэффициенты null).
    * funds: `secid, name, issuer, asset_class` (text), `commission` (pct; RusETFs commission_pct / 100, иначе
      T-Invest fixed_commission / 100), `aum_rub` (rub), `cagr … score` как у акций. Строки — фонды symbol_stats
      ∪ tinvest_etfs; RusETFs только обогащает (неторгуемые фонды RusETFs не попадают).
    * bonds: `secid, name, type` («ОФЗ» / «Корпоративная»), `face_currency` (text, валюта номинала), `yield, coupon_pct` (pct), `coupon_freq,
      duration_days` (num), `maturity_date, offer_date` (date), `price_pct` (num, % номинала), `nkd, volume_rub`
      (rub), `listing_level` (num).
    * `symbol_stats.json` items[secid] дополнительно: `name` (SHORTNAME ISS или null), `ret_6m` (доля; metrics.period_returns
      "6M" на конец прошлого месяца — у акций с дивидендами, у фондов по цене). Цен в файле нет (котировки
      не пишутся) — цену главная берёт из ISS в браузере. `portfolios.json` items[slug].row.`ret_6m` (доля, "6M").
      В loadScreener name из symbol_stats — запасной вариант после T-Invest/RusETFs.
    * Доп. поля строк вне columns: `logo, logo_color` (stocks/funds), `board` (bonds). Нет файла — поля null.
  * `filters`: `{q: 'текст'}` — подстрока тикера/названия; `{key: {min, max}}` — диапазон включительно (числа или
    ISO-даты, null не проходит); `{key: [..]}` — из списка; `{key: 'v'}` — точное совпадение без регистра.
    `sortRows`: dir `'asc'|'desc'` (по умолчанию desc), null всегда в конце, устойчивая, новый массив.
  * `screener_bonds.json` = `{updated, source: "ISS MOEX", boards: ["TQOB","TQCB"], data: [...]}`, сборщик
    `--only screener_bonds` (отдельный шаг workflow). Все непогашенные выпуски с ценой, без отсечки по обороту.
* site: `web/js/page-screener.js` — таблица с поиском, фильтрами по ключевым колонкам (сектор, диапазоны),
  выбором колонок, сортировкой, пагинацией (50), CSV; логотип слева от тикера; клик — страница бумаги;
  дата и источник данных над таблицей; пустые/ошибочные состояния.

### Портфели `/portfolios/lazy` и `/portfolio/{slug}` — перенос `pages/8_Портфели.py` (site)
`public/data/portfolios.json` уже есть (groups, items[slug].row/…, series, series_benchmark, risk_return):
листинг с сортировкой и диаграммой «риск — доходность», страница портфеля (состав, рост 100 000 ₽ против
бенчмарка, доходность по периодам, просадки, «Как повторить» с БПИФ и комиссиями, «Открыть в бэктесте»).
Расчётов в интерфейсе нет — только данные файла (если чего-то не хватает — список для api).

### Главная `/` (site) — блоки по порядку
1. Герой: заголовок-миссия, подзаголовок, кнопка «Проверить портфель» (→ бэктест), поиск по бумагам.
2. Лента карточек готовых портфелей (из portfolios.json): название, мини-график, доходность за 6 мес.
3. «От идеи до портфеля» — 5 шагов (изучить → сравнить → выбрать → проверить на истории → подобрать доли),
   каждый ведёт в раздел.
4. Блок про диверсификацию/корреляции (пример на реальных индексах: акции, ОФЗ, золото) → граница/бэктест.
5. Сравнение двух бумаг (два поля + популярные пары SBER/GAZP, TMOS/EQMX, …) → бэктест/сравнение.
6. «Популярные бумаги» (лого, тикер, название, цена, изменение за 6 мес.) и «Лидеры рейтинга» (symbol_stats).
7. Дисклеймер. Раздел «База знаний» — позже (в меню не показываем).

## Главная v2 и правки страниц (ветка `feature/home-v2`)

Ориентир — оформление главной portfolioslab.com (композиция, приёмы, плотность деталей), но в светлой гамме
и со своими текстами/данными/иллюстрациями (их тексты, графику и бренд не копируем). Тексты — сухие и предметные:
что показывает блок и на каких данных; без публицистики и метафор («яйца в корзине» и т. п.).

### Палитра (обновление `web/css/tokens.css`; старые имена переменных сохраняются, меняются значения)
```css
--brand: #0ABAB5;        /* тиффани — акцент, кнопки, выделенное слово в заголовках */
--brand-strong: #07807C; /* текст/ссылки цвета бренда (контраст AA на светлом) */
--brand-soft: #E3F7F6;
--rose: #E8837A;         /* нежно-красный: отрицательные значения в графиках, акцентные плашки */
--rose-soft: #FBEAE8;
--sand: #EFE4D2;         /* бежевый: плашки/бейджи, заметно отличается от фона */
--sand-strong: #8A6D45;  /* текст на бежевом */
--gray-1: #F4F5F7; --gray-2: #E6E8EC; --gray-3: #9AA1AC; --gray-4: #5B6676;
--bg: #F7F8FA;  --neg: #D8574C; --pos: #138A7A;
```
Фон страниц: `--bg` + мягкие радиальные свечения (тиффани и бежевый) в героях и разделителях секций;
на заднем плане героя — тонкая геометрия (SVG: сетка точек, концентрические дуги, изометрические линии, opacity ≤ 0.35).
Место под 3D-иллюстрацию (`web/img/hero-3d.webp`, опционально — если файла нет, остаётся SVG-геометрия).

### Главная — блоки по порядку
1. **Герой:** надзаголовок-плашка, заголовок капсом в 2 строки с выделенным словом цвета `--brand`
   (например «АНАЛИТИКА ПОРТФЕЛЯ / НА ДАННЫХ МОСБИРЖИ»), подзаголовок (что умеет платформа, одна фраза),
   кнопка «Проверить портфель», вторичная «Готовые портфели», поиск; фон — свечения + геометрия.
2. **Лента стратегий:** карточки готовых портфелей (portfolios.json): название, спарклайн линией
   (зелёный/красный по знаку), доходность за 6 мес. (`row.ret_6m`, запасной — YTD) с бейджем «6М».
3. **«ОТ ИДЕИ ДО ПОРТФЕЛЯ — В ОДНОМ МЕСТЕ»:** бенто-сетка из 5 плашек с номерами «01…05»; в каждой — мини-интерфейс
   на реальных данных, без расчётов в интерфейсе:
   01 Изучить — 3 спарклайна портфелей с подписями; 02 Сравнить — мини-таблица топ-5 акций по рейтингу
   (symbol_stats: тикер с логотипом, CAGR, Шарп); 03 Выбрать — мини-таблица фондов с наименьшей комиссией
   (screener funds); 04 Проверить на истории — линия «рост 100 000 ₽» портфеля и линия просадок под ней;
   05 Подобрать доли — кольцевая диаграмма долей портфеля (weights из portfolios.json) с легендой.
   Каждая плашка — ссылка в свой раздел.
4. **Корреляции** (замена блока про «корзину»): тепловая карта корреляций месячных доходностей
   5–6 тематических портфелей (series из portfolios.json; корреляция — через `web/lib/metrics.js` `correlation`,
   доходности — `toReturns`, это функции lib) и две карточки рядом: «Что видно» и «Что это значит» —
   с конкретными числами из карты (максимальная и минимальная корреляция), ссылки на границу и бэктест.
5. **Сравнение двух бумаг:** два поля с поиском + плашки популярных пар (с логотипами) → бэктест 50/50.
6. **Популярные бумаги:** карточки с логотипом, тикером, названием, ценой (fetchLastPrices), изменением за 6 мес.
7. **Полоса-призыв** (бежевая плашка): «Готовые портфели», «Скринеры», «Методика» — понятная навигация.
8. Подвал.

### Навигация
Шапка: логотип, меню «Инструменты ▾ (Бэктест, Граница Марковица, Коэффициенты бумаги) · Портфели · Скринеры ▾ (Акции,
Фонды, Облигации) · Методика», поиск; активный раздел подсвечен; хлебные крошки на внутренних страницах.

### Ассистент — на всех страницах
Плавающая кнопка «Спросить ИИ» внизу справа видна на всех маршрутах (на страницах без фактов ассистент отвечает
про термины и устройство платформы).

### Шрифт чисел и тикеров
Моноширинный шрифт не используется: `.mono`, `code` (кроме формул в методике) — Montserrat с
`font-variant-numeric: tabular-nums`. Таблицы — tabular-nums.

### Страница портфеля — правки
* Коэффициенты эффективности не отображаются — исправить (данные `report.metrics` / `benchmark_metrics`).
* Блок «Просадки»: аккуратные KPI-плашки в одну сетку (глубина, дата, длительность, восстановление), без наездов.
* Графики роста и просадок — линия без заливки.

## Ассистент: YandexGPT вместо GigaChat (ветка `feature/yandexgpt`)

Причина: из Yandex Cloud сервер чата GigaChat не отвечает ни на какие запросы (проверено `X-Diag`: OAuth работает, models/chat/chat_stream — ReadTimeout), с компьютера владельца — отвечает за ~1 с. YandexGPT живёт внутри того же облака.

Контракт `POST /api/assistant` (запрос/ответ, фильтр рекомендаций, сверка чисел, лимиты, журнал без персональных данных) **не меняется** — меняется только вызов модели в `functions/assistant/`.

### Вызов модели (зона `backend`)
* `POST https://llm.api.cloud.yandex.net/foundationModels/v1/completion`, заголовки: `Authorization: Bearer <IAM-токен>`, `x-folder-id: <folder_id>`, `Content-Type: application/json`.
* Тело: `{"modelUri": "gpt://<folder_id>/<модель>/latest", "completionOptions": {"stream": false, "temperature": 0.2, "maxTokens": "500"}, "messages": [{"role": "system", "text": …}, {"role": "user", "text": …}]}`.
  Модель — env `YANDEX_MODEL` (по умолчанию `yandexgpt`; `yandexgpt-lite` — дешевле и быстрее).
* Ответ: `result.alternatives[0].message.text`; статус альтернативы `ALTERNATIVE_STATUS_FINAL` (при `CONTENT_FILTER` — вежливое сообщение, 502 не нужен: ответ 200 с отказом `REFUSAL`).
* IAM-токен — из `context.token["access_token"]` (у функции есть сервисный аккаунт `ia-runtime`; `handler(event, context)` уже получает context). Ни ключа модели, ни OAuth, ни сертификата Минцифры больше нет.
* `folder_id` — env `YC_FOLDER_ID` (идентификатор, не секрет), задаётся при выкладке.
* Роль `ai.languageModels.user` у `ia-runtime` на каталог.
* Ошибки: 401/403 от API → 502 «Нет доступа к YandexGPT (роль сервисного аккаунта)»; 429 → 502 «Лимит запросов YandexGPT, попробуйте позже»; прочее → общий 502. `detail` в ответе 502 (шаг и код) остаётся.
* Формат сообщений: `build_messages` отдаёт `[{"role","text"}]` (system + один user: факты экрана и вопрос — как сейчас).
* `LABEL` = «Ответ ИИ (YandexGPT). Не является индивидуальной инвестиционной рекомендацией.»
* Удалить: OAuth, кэш токена Сбера, `GIGACHAT_*`, `verify=` с CA-пакетом, режим `X-Diag` (диагностика больше не нужна).

### Выкладка (зона `api`) и `infra/` (зона `backend`)
* Из `deploy-yc.yml` убрать шаг сертификата Минцифры и `--environment GIGACHAT_CA_BUNDLE`, `--secret GIGACHAT_AUTH_KEY`; добавить `--environment YC_FOLDER_ID=$YC_FOLDER_ID,YANDEX_MODEL=yandexgpt` для `assistant`. Таймаут функции остаётся 30 с.
* `setup.sh`/`README`: больше не спрашивать ключ GigaChat; роль `ai.languageModels.user` для `ia-runtime` на каталог; секрет Lockbox содержит только `SITE_USERS` (ключ `GIGACHAT_AUTH_KEY` в существующем секрете остаётся неиспользованным — его можно удалить в консоли).

## Индексы, логотипы по правилу, ровная вёрстка, сбор данных через сертификат Минцифры (ветка `feature/indices-logos-layout`)

### Сбор данных: ISS и сертификат Минцифры (зона `api`) — блокер остальных пунктов
С ~21.11.2026 `iss.moex.com` отдаёт сертификат УЦ Минцифры (цепочка Russian Trusted Sub CA → Russian Trusted Root CA), которого нет в certifi и в системных хранилищах, поэтому ежедневный сбор падает («ISS недоступен» — по сути ошибка проверки TLS) и `risk_free`, `symbol_stats`, `portfolios`, `screener_bonds` не обновляются. Проверено: с пакетом root+sub запрос к ISS проходит (HTTP 200).
* `core/data/iss.py` (и общий HTTP-код ISS, если он есть отдельно) использует пакет сертификатов из env `ISS_CA_BUNDLE`; если не задан — переменная `TINVEST_CA_BUNDLE` (тот же пакет «certifi + Минцифры», который уже собирает `collect-data.yml`); если не задан и она — поведение как раньше (certifi). Так же для ЦБ, если `cbr.ru` отдаёт тот же УЦ (проверь по коду/логам; в последнем прогоне «Сбор» (ЦБ, T-Invest, RusETFs) прошёл успешно — трогать не надо без причины).
* `collect-data.yml`: во всех шагах, ходящих в ISS (Сбор, Рейтинги, Портфели, облигации, индексы), выставить `ISS_CA_BUNDLE` на тот же файл, что и `TINVEST_CA_BUNDLE`; шаг «Сертификаты Минцифры» уже есть — сделай так, чтобы он выполнялся до всех этих шагов и пакет жил в `$RUNNER_TEMP`.
* Тесты — через monkeypatch `requests` (сеть недоступна); проверить, что `verify=` берётся из env.

### Логотипы по правилу (зоны `api` + `site` B)
* **Акция, облигация → логотип эмитента; фонд → логотип управляющей компании; индекс → без логотипа** (индексы — корзины, у них свой значок класса и цвет, см. ниже).
* api: акции и фонды — как уже сделано (`logo`, `logo_color` из T-Invest `brand` в `tinvest_shares.json`, `tinvest_etfs.json`; бренд ETF в T-Invest — управляющая компания, добавить `brand` (имя бренда/УК) рядом с `logo`). Облигации — новый шаг сборщика `tinvest_bonds` (T-Invest `Bonds`, только чтение): `public/data/tinvest_bonds.json` `{updated, source, data: [{ticker, isin, name, logo, logo_color, brand}]}` — только записи, где есть `logo`; `ticker` = SECID ISS. Токен — `TINVEST_TOKEN` (как у остальных шагов T-Invest); при ошибке шага остальные не падают.
  **Фактически:** шаг `--only tinvest_bonds` (`default=False`, порог ≥ 100 записей), файл `{updated, source: "T-Invest API", data: [{ticker, isin, name, logo, logo_color, brand}]}` — без записей без `logo`, один на тикер, `logo_color`/`brand`/`isin` могут отсутствовать. В `tinvest_shares.json` и `tinvest_etfs.json` у записей добавлено `brand` (имя бренда; у фонда — УК), если T-Invest его отдаёт.
* site B (`web/js/logo.js`): `logoFor(secid, kind)`: `kind` = share | bond | fund | index (если не передан — определять по наличию в файлах: tinvest_shares → share, tinvest_etfs → fund, tinvest_bonds → bond). `assetBadge(secid, size, {kind})`; у значка `title`/`alt=""` + имя эмитента/УК в `title`. В скринере фондов — колонка «УК» с логотипом УК и названием (RusETFs `issuer` — название, лого — из бренда ETF). У индексов в поиске/таблицах — значок-«корзина» цвета класса.

### Индексы — отдельный раздел `/indices` (api + site B)
* **api, фактически (ключи сверху — как в файле):** корень `{updated, source, as_of, items}`; `as_of` — максимальная последняя дата рядов; `items[]` — ровно ключи контракта (`key, name, short, class, kind, description, since, series, ret_ytd, ret_1y, cagr_5y, cagr_10y, max_drawdown, volatility_1y, etf`). Доли — в долях (0.1193 = 11,93 %); `ret_*`, `cagr_*`, `volatility_1y` могут быть `null` (короткая история); `series` — до 121 месячной точки (последний наблюдаемый день месяца), база 100 на первой дате окна; `max_drawdown` — за окно series (≤ 0); `volatility_1y` — по 12 последним месячным доходностям × √12; `etf_note` (необязательно, строка) — оговорка, если фонд лишь приближённо повторяет индекс (SUGB, SBLB, AMGB, LQDT, SBBC, DIVD); `ret_*`/`cagr_*` считаются по исходному (дневному) ряду, `updated` начального файла = `as_of`T00:00:00+00:00; при сборе пропущенные ключи — в status.json `skipped_keys`. `etf` — `[{secid, name}]`, только биржевые тикеры (ОПИФ и пустое поле каталога дают `[]`), имя из rusetfs_funds/tinvest_etfs (нет — тикер). `kind`: price — IMOEX, MOEXEU, GOLD_CBR, GLDRUB_TOM; остальные total_return. Состав — 14 ключей `core/analytics/indices.py: INDEX_INFO` (без RUCBITR/RUCBTRNS); индекс без данных пропускается, шаг падает при < 8 индексов. Код — `core/analytics/indices.py`, шаг `--only indices` (`default=False`). Начальный файл (демо) не содержит IMOEX и GLDRUB_TOM — их нет в фикстурах.
* **api (требование):** шаг сборщика `indices` → `public/data/indices.json`:
  `{updated, source, as_of, items: [{key, name, short, class, kind, description, since, series: [[YYYY-MM-DD, значение]], ret_ytd, ret_1y, cagr_5y, cagr_10y, max_drawdown, volatility_1y, etf: [{secid, name}]}]}`.
  * Состав — каталог `core/data/universe.py: CATALOG` (индексы и ряды: MCFTR, IMOEX, MEBCTR, IRDIVTR, MOEXEU/MEEUTR, RGBITR, RUGBITR1Y/5+/10Y, RUCBITR, RUCBTRNS, CORP_CHAIN, GOLD_CBR, GLDRUB_TOM, RUONIA и т. д. — что там реально есть); дубли склейки (RUCBITR/RUCBTRNS/CORP_CHAIN) не показывать: оставить CORP_CHAIN как «Корпоративные облигации».
  * `class`: «Акции» | «Облигации гос.» | «Облигации корп.» | «Золото» | «Денежный рынок» (как `assetClass` в каталоге); `kind`: «price» | «total_return» (ценовой / полной доходности); `description` — одно предложение простыми словами, что за корзина; `etf` — БПИФ-аналоги из поля каталога `etf`.
  * `series` — месячные значения, нормированные к 100 на первой дате окна, окно — до 10 лет (для коротких — всё); метрики — через существующие функции `core/analytics/metrics.py` (CAGR, просадка, волатильность), не новые формулы.
  * Метка «as_of» — последняя дата ряда. Если ISS недоступен, шаг не падает остальным (как другие), прежний файл не трогается.
  * **Начальный файл:** сразу сгенерировать `public/data/indices.json` из эталонных месячных рядов `tests/fixtures/` (тех же, что использует демо) с `source: "эталонные месячные ряды"` — чтобы раздел не был пустым до первого успешного ночного сбора; ночной сбор перезапишет его свежими данными.
* **site B** — `web/js/page-indices.js` (+ `web/css/indices.css`), роут `#/indices` уже подключён; контейнер `#indices-root`:
  * Заголовок «Индексы», пояснение (индекс — корзина бумаг, а не бумага; показывает рынок целиком), «Данные на {as_of} · источник».
  * Группы по классам, у каждого класса свой цвет и значок: Акции — тиффани, Облигации гос. — серо-синий `#6B7F99`, Облигации корп. — лавандовый-серый `#8E86B5`, Золото — янтарь `#E0A63A`, Денежный рынок — серый `#9AA1AC` (добавить токены `--idx-*` в `indices.css`).
  * Карточка индекса: значок класса, название, тип («ценовой»/«полной доходности»), спарклайн линией без заливки цвета класса (из `series`), крупно — доходность за 1 год, мелко — 5 и 10 лет в год (CAGR), макс. просадка, волатильность; «Как повторить: БПИФ …» (названия фондов, логотип УК рядом, ссылка на страницу бумаги); вся карточка — ссылка на `#/symbol/{key}` (страница бумаги открывается и для индексов).
  * Переключатель сортировки/группировки: «По классам» (по умолчанию) | «По доходности за год»; на мобильной — карточки в колонку.
  * Состояния: загрузка, нет данных, ошибка.
  * На главной вместо индексов в блоке «Популярные бумаги» — отдельная полоса «Индексы рынка» (6–8 карточек: MCFTR, IMOEX, RGBITR, RUCBITR/CORP_CHAIN, GOLD_CBR, RUONIA…) с мини-спарклайнами и ссылкой «Все индексы»; индексы убрать из «Популярных бумаг».
  * В поиске подсказки индексов помечены бейджем «Индекс».

### Ровная вёрстка и единый фон (зона `site` A)
* **Бэктест, блок «Параметры»:** все поля в одной сетке с одинаковой шириной колонок (`repeat(auto-fit, minmax(220px, 1fr))`), одинаковой высотой (44 px), подписи над полями в одном стиле и на одной линии, одинаковые отступы; чекбоксы/переключатели — отдельным рядом; на 375 px — одна колонка.
* **Фон:** сейчас видна граница фона героя и фона ниже (два отдельных фона). Сделать единый фон страницы: общий градиент/свечения на уровне `body` (или фиксированный слой под всей страницей), геометрия героя растянута на всю ширину вьюпорта и плавно гаснет к фону страницы (mask/градиент), у `.hero` нет собственного цвета фона и нет жёсткого нижнего края.
* **Выравнивание заголовков:** все заголовки секций и подзаголовки — только по левому краю одной колонки (`text-align: left`, без центрирования в `.block-head`, `.cta`, `.panel-head` и т. п.); проверить главную, скринеры, портфели, инструменты, методику.

## Мой портфель, ребалансировки, прокси биржи, логотипы УК (ветка `feature/my-portfolio-proxy-uklogos`)

### A. Прокси биржи и ЦБ через облачную функцию (зона `backend`) — чтобы живые данные работали в браузерах
`iss.moex.com` отдаёт сертификат УЦ Минцифры (браузеры без корневого сертификата не доверяют → цены на главной, страница бумаги, котировки «Моего портфеля» не загружаются). Запросы идут через ту же публичную точку входа (шлюз), функция делает их сама.
* Функция `iss-proxy` (`functions/iss-proxy/index.py`, Python 3.12, зависимости — только `requests`, сервисный аккаунт `ia-runtime`, без секретов): `GET /iss/{path+}` → `https://iss.moex.com/iss/{path}`; `GET /cbr/{path+}` → `https://www.cbr.ru/{path}` (нужны курсы валют: `scripts/XML_daily.asp?date_req=DD/MM/YYYY`, `scripts/XML_dynamic.asp`). Пакет сертификатов для ISS — **в репозитории**: `functions/iss-proxy/russian_trusted_ca.pem` (корневой + подчинённый УЦ Минцифры, это публичные сертификаты; отпечатки SHA-256 закреплены: root `D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31`, sub `BBBDE2103E790B999EC62BD03CF625A5A2E7C316E10AFE6A490EEDEAD8B3FD9B`; склейка — через перевод строки между сертификатами, файл в LF; тест на отпечатки). ЦБ — системные сертификаты (certifi).
* Безопасность: только GET; путь из белого списка (ISS: `engines/`, `history/engines/`, `securities`, `securities/`, `statistics/engines/`, `index.json`, `markets/`; ЦБ: `scripts/XML_daily.asp`, `scripts/XML_dynamic.asp`), без `..`, без схемы/хоста/`@` в пути, длина пути ≤ 300, query ≤ 1500 знаков, значения query — без управляющих символов; хост и схема зашиты в коде (SSRF невозможен); таймаут 20 с; ответ ≤ 2,5 МБ (`MAX_BYTES = 2_500_000`, иначе 502 JSON; base64 + лимит шлюза), значение одного параметра query ≤ 1500; кэш функции — ≤ 200 записей и ≤ 40 МБ суммарно, тела > 500 КБ не кэшируются; проксируются только статус 200/404/… ISS как есть, тип `application/json`/`text/xml`; в ответ — `Cache-Control: private, max-age=60`; 4xx/5xx ISS отдавать как 502 с коротким JSON (`{"error":"ISS недоступен"}`); журнал: только путь и код, без query.
* Шлюз (`infra/gateway.yaml`): маршруты `/iss/{path+}` и `/cbr/{path+}` (GET, `siteAuth`, интеграция с функцией, `${ISS_PROXY_FUNCTION_ID}` — новая подстановка; `envsubst` в `deploy-yc.yml` — расширить список переменных). Выкладка `deploy-yc.yml`: создание/версия функции `iss-proxy` (256m, 30s, без секретов), `ISS_PROXY_FUNCTION_ID` в GITHUB_ENV; `infra/README.md` — коротко.
* Браузер: `web/lib/data.js`: `export const ISS_BASE = globalThis.FINSERV_ISS_BASE ?? (location в браузере на *.apigw.yandexcloud.net ? "/iss" : "https://iss.moex.com/iss")` и `export const CBR_BASE` аналогично (`"/cbr"` / `"https://www.cbr.ru"`); `web/js/config.js` — то же (если константа используется). Сегодняшнее поведение вне шлюза (локально, тесты, node) не меняется. Эту единственную правку в `web/lib/data.js` делает `backend`.

### B. «Мой портфель» и «Ребалансировки» — перенос `pages/5_Мой_портфель.py`, `pages/6_Ребалансировки.py`
Маршруты `#/my` («Мой портфель», `page-myportfolio.js`, `#my-root`) и `#/my/rebalance` («Ребалансировки», `page-rebalance.js`, `#rebalance-root`) уже подключены (пункт меню «Мой портфель»). **Данные пользователя хранятся только в его браузере** (localStorage, ключ `finserv.portfolio.v1`, всё в try/catch; без него — в памяти вкладки) + экспорт/импорт JSON и CSV; на странице — заметка об этом.
* **Хранилище** (`web/js/portfolio-store.js`, зона `site`): `{version: 1, nextId, portfolios: [{id, name, created, benchmark, target: {SECID: доля}, rebalance, band, last_rebalance}], transactions: [{id, portfolio_id, date, type, secid, qty, price, amount, fee, note}]}`; методы как у Python `Ledger`: `portfolios()`, `createPortfolio(name, {benchmark})`, `updatePortfolio(id, fields)`, `deletePortfolio(id)`, `transactions(pid)`, `addTx(pid, tx)`, `deleteTx(id)`, `importCsv(pid, text) -> n`, `exportJson()`, `importJson(text)`, `subscribe(fn)`. Название портфеля уникально.
* **Расчёты (чистые функции, зона `api`)** — портируют `core/portfolio/ledger.py`, `core/portfolio/rebalance.py` и `core/data/quotes.py` **точно** (те же формулы, тот же порядок операций; Python остаётся эталоном для паритетных тестов):
  * `web/lib/ledger.js`: `TX_TYPES` (BUY/SELL/DIVIDEND/COUPON/DEPOSIT/WITHDRAW/FEE/TAX → русские подписи), `cashFlow(tx) -> number[]` (по каждой операции), `externalFlows(tx) -> [{date, amount}]` (по датам, отсортировано), `positions(tx) -> {positions: [{secid, qty, avgCost, invested, realized, income}], cash}`, `valuation(tx, quotes) -> {rows, cash}` (строки как столбцы Python-таблицы: `{secid, name, qty, avgCost, price, value, invested, unrealized, realized, income, share, ret}`; `value`/`price` = null, если нет цены), `valueHistory(tx, prices) -> {dates, value, twr, missingPrices}` (`prices` — Frame из `web/lib/data.js`: `{dates, cols}`), `parseCsv(text) -> tx[]` (столбцы `date,type,secid,qty,price,amount,fee,note`; валидация, понятные ошибки с номером строки), `toCsv(tx) -> string` (строковые поля, начинающиеся с `= + - @ \t \r`, получают префикс `'`; числа не трогаются), `portfolioSummary(valuationRows, cash, flows, today?) -> {total, netIn, gain, gainPct, xirrFlows, alloc}` (`gainPct` = null при netIn = 0; `xirrFlows` = отрицательные внешние потоки + `{date: today, amount: total}` — прямо в `backtest.xirr`; `alloc` = `[{label, value, share}]` по возрастанию стоимости, бумаги и «Деньги»), `twrPercent(twr) -> number[]` (× 100), `validateTx(tx) -> string[]`.
  * `web/lib/rebalance.js`: `schedule(rule, startIso, n = 8) -> ISO[]` (правила — те же ключи, что `REBAL_LABELS` в `web/lib/backtest.js`), `driftReport(current, target, band) -> rows`, `rebalanceOrders(values, target, prices, lots, cash, commission) -> rows`, `toIcs(dates, portfolio, description) -> string` (экранирование RFC 5545: `\ , ;` и переводы строк → `\n`, CR убирается, имя портфеля без CR/LF; Python `to_ics` так же), `normalizeTarget(map) -> {SECID: доля}` (сумма 1; отрицательные/нечисловые → 0; сумма ≤ 0 → `{}`), `equalTarget(secids) -> {SECID: 1/n}`, `factShares(rows [{secid, value}], cash = 0) -> {SECID: доля}` (знаменатель Σ value + cash), `ordersTotals(orders) -> {buy, sell, fees}`.
  * `web/lib/quotes.js`: `getQuotes(secids, {fetchImpl}) -> {SECID: {price, name, lot, board, market, currency}}` (цена в рублях за 1 бумагу; облигации — с НКД; валютные — по курсу ЦБ из `CBR_BASE`, при недоступности курса `price = null`), `priceHistoryRub(secids, startIso, endIso, {fetchImpl}) -> Frame`. ISS — через `ISS_BASE` и `getCached`.
* **Страницы (зона `site`)**:
  * «Мой портфель»: выбор/создание/удаление портфеля, форма добавления операции (дата, тип, тикер, количество, цена, сумма, комиссия; пояснение «для покупок/продаж сумму можно оставить пустой — будет количество × цена»), импорт CSV (+ образец), экспорт CSV/JSON, импорт JSON; карточки итогов (стоимость, внесено нетто, результат ₽ и %, XIRR годовая, свободные деньги — через `engine.backtest.xirr` по `externalFlows` + текущая стоимость; без XIRR, если у части бумаг нет цены — с предупреждением как в Streamlit); вкладки «Позиции» (таблица с логотипами + столбчатая диаграмма долей), «Динамика и метрики» (график стоимости, TWR против бенчмарка, таблица показателей — `buildMetricsTable`/`computeAll`, предупреждения о днях без цены), «Сравнение со стратегиями» (TWR против `STRATEGIES` через `runBacktest`), «Операции» (таблица, удаление). Все тексты — из Streamlit, простым языком; дисклеймер.
  * «Ребалансировки»: выбор портфеля (из того же хранилища), редактор целевых долей (тикер, % — сумма нормируется), периодичность (`REBAL_LABELS`) и коридор, «Сохранить правило»; «Ближайшие даты» с кнопкой «Добавить в календарь (.ics)»; отклонение от целей (графики Chart.js: цель/факт, предупреждение о выходе из коридора, таблица); «Заявки для ребалансировки» (переключатель «Распределить свободные деньги», комиссия брокера %, таблица, итоги покупок/продаж/комиссий, округление до лотов); «Отметить ребалансировку выполненной». Бумаги без цены исключаются из расчёта с предупреждением (как в Python).
  * Цены — только через `web/lib/quotes.js`; интерфейс ничего не считает сам (инвариант 3).

### C. Логотипы управляющих компаний (зоны `api` + `site`)
Факт: у T-Invest `brand` фонда — не логотип УК, а иконка продукта (у «Т-Капитал» TPAY — облако со стрелкой, TBEU — евро-сертификат; у FinEx — 22 разных иконки), у части УК («Первая» `logoam11.png` ×17, «Альфа Капитал» `RU000A100Q4.png`, «ВИМ» `vimlick.png`, «АТОН» `RU000A102XZ.png`) — логотип УК; 10 фондов «Первой» и 23 «Альфы» вообще не в справочнике T-Invest. Требование владельца: **у всех фондов одной УК один и тот же логотип УК**.
* api (готово): `public/data/uk_logos.json` `{updated, source, funds: {TICKER: "УК"}, uk: {"УК": {logo, logo_color, source, group, file}}, bonds_issuer: {SECID: {logo, logo_color}}}`; `source` УК — `"table"` (явная таблица `UK_LOGO_SOURCES` в `core/analytics/uk_logos.py`) | `"common"` (общий логотип ≥ 2 фондов УК) | `null` (логотипа нет: `logo`/`logo_color` = `null`, сайт показывает единую монограмму УК); `group: true` — логотип банковской группы УК (курируемый алиас), не самой УК. **`file`** — путь относительно `web/` (`img/uk/<slug>.svg|png`, svg приоритетнее) к файлу, который владелец положил вручную; сборщик находит его по `slug(УК)` и пишет в `uk[УК].file`, иначе `null`. **Приоритет для `logo.js`: `file` → CDN `logo` → монограмма.** `slug` — транслит имени УК: «Альфа Капитал» → `alfa-kapital`, «Райффайзен» → `rayffayzen`, «ААА» → `aaa` (функция `slug` в `core/analytics/uk_logos.py`; список slug-ов — в `uk_logos.json`, ключ `uk` не содержит slug, считается тем же правилом). Файл картинки — как в остальных файлах, `https://invest-brands.cdn-tinkoff.ru/<logo без .png>x160.png`. Шаг сборщика `--only uk_logos` (default=False, после `tinvest_bonds`), читает уже собранные файлы.
  Итоговая таблица «УК → логотип»: Т-Капитал → `tcs2.png` (акция T); Озон → `ISSUANCEOZON.png` (акция OZON); МКБ → `RU000A0JUG31.png` (акция «МКБ»); АТОН → `RU000A102XZ.png`; Первая → `logoam11.png` (common ×17); Альфа Капитал → `RU000A100Q4.png` (common ×4); ВИМ Инвестиции → `vimlick.png` (common ×3); **логотип группы** (`group: true`): БСПБ → `RU0009100945.png` (акция «Банк Санкт-Петербург», BSPB), РСХБ → `RU000A0JTVJ2.png` (облигации «Россельхозбанк»), Промсвязь → `RU000A101U38.png` (облигации «Промсвязьбанк», знак «ПСБ»). Без логотипа (монограмма; владелец может положить `web/img/uk/<slug>.svg|png`): FinEx, ITI FUNDS, ААА, АК БАРС, БКС, Восток-Запад, Доверительная, ДоходЪ, Ингосстрах, Контрада, М-Консалтинг, Открытие, Райффайзен, Ренессанс Капитал, Солид, Финам, Финстар (в акциях/облигациях T-Invest нет эмитента этих групп; «Солид-Лизинг» и «Ренессанс Страхование» — другие компании без подтверждённой связи, не использованы).
* api (готово): покрытие облигаций. `bonds_issuer` — облигации `screener_bonds.json` без записи в `tinvest_bonds.json`, получившие логотип эмитента: эмитент = короткое имя скринера до первой цифры («ВТБ Б1-295» → «ВТБ Б»); применяется при ≥ 2 облигациях с логотипом у того же ключа и ровно одном логотипе. Покрытие 1491 → 1755 из 2692 (+264). `logo.js` для облигации: `tinvest_bonds[SECID]`, затем `uk_logos.bonds_issuer[SECID]`.
* site: `logo.js`: для `kind = "fund"` логотип/монограмма берутся **по УК фонда** (`uk_logos.json`), иконки продуктов из `tinvest_etfs.json` для фондов не используются; `title` = название УК; в скринере фондов и во всех местах, где есть значок фонда (поиск, страница бумаги, популярные, «Как повторить» в индексах, «Мой портфель»), — одинаковый логотип у фондов одной УК.

## Пенсионная индустрия `/npf` (ветка `feature/npf-section`)

Данные и расчёты — `api`; страница (`#/npf`, вкладки Рынок / Фонд / Сравнение фондов / Бумаги и эмитенты / Данные и методика) — `site`. UI не считает финансы: все числа берёт из `web/lib/npf.js` (инвариант 3). Источник — приложение «Монитор портфелей НПФ» владельца проекта (раскрытие НПФ и Банк России); обновление **вручную** (`python -m scripts.import_npf <html>`), `meta.json.built` / `monitorUpdated` — даты сборки и обновления структуры. Методика 1:1 с исходным приложением: **АУМ** — активы до вычета обязательств (`≈` при оценке, `est`); **СЧА** — только подтверждённая (иначе `null`); доли — % от АУМ (0–100); срез месяца — последний внутри календарного месяца, пропуски не заполняются; «взвешенно» — по АУМ, «простое» — среднее по фондам. Деньги в рублях, доли в процентах.

### Файлы `public/data/npf/` (каждый ≤ 3 МБ — запас под лимит ответа Object Storage в API Gateway)

Грузятся `loadNpf` по `manifest.json`: `{built, monitorUpdated, files:[{name, part, bytes, records, sha256}]}`. `part`: `meta` | `names` | `pages` | `srcs` | `notes` | `struct` | `comp` (куски `comp-NN.json` — подряд идущие записи, порядок как в исходной базе; склеиваются по порядку manifest). Минифицированный JSON, вывод детерминирован. **Целостность:** `sha256` — для CI и целостности репозитория (проверяет `tests/test_npf_import.py`), на клиенте НЕ проверяется. Клиент (`loadNpf`) сверяет `records` каждого куска с фактической длиной (для `meta` — число фондов) → при расхождении ошибка «НПФ: файл X устарел или повреждён, обновите страницу»; кусок запрашивается как `<имя>?v=<первые 8 символов sha256>` (кэш не подмешивает старые куски; `manifest.json` — без версии, `cache: 'no-cache'`); ≤ 4 запросов одновременно; битый JSON (в т.ч. HTML шлюза со статусом 200) → «НПФ: <файл> — некорректный JSON»; при первой ошибке остальные запросы отменяются (`AbortSignal`), кэш `getNpf` сбрасывается; параллельные `getNpf` делят один набор запросов. Импорт (`scripts/import_npf.py`) пишет только свои файлы (`manifest.json`, `meta|names|pages|srcs|notes|struct.json`, `comp-NN.json`), отказывается работать в непустой папке без `manifest.json`, пишет атомарно (временная папка рядом, `manifest.json` последним), проверяет формат дат `YYYY-MM-DD`, числовые типы `t/nav/li/rep/value/qty/доли/sum` и `funds[].url` (только `http(s)://`).

| Файл (≈ байт) | Что внутри |
|---|---|
| `meta.json` (0,7 МБ) | `built`, `monitorUpdated`, `classes[17]` `{n, c(цвет), s(коротко)}`, `sectors[20]` `{n,c}`, `funds[33]` `{id,name,note,mid,url,comp(0/1),arch(0/1)}`, `labels`, `issues`, `sectorNote`, `aumNote`, `isins`, `issuers`, `groups` (эмитенты/контрагенты, str), `inst[18016]` = `[имя, isin, эмитент, класс, сектор, группа]` (индексы в `names`/`isins`/`issuers`/`classes`/`sectors`/`groups`) |
| `names.json` 0,86 · `pages.json` 0,58 · `srcs.json` 0,28 · `notes.json` 0,86 МБ | словари строк (индексы из `comp`/`struct`/`inst`) |
| `struct.json` (0,77 МБ) | отчёты о структуре: `{f, k(0/1), d, r:[класс, доля%,…], sr:[строка-метка, доля, класс или -1,…], sum, src, nt, rv?, is?}` |
| `comp-01…04.json` (по ≤ 2,5 МБ) | срезы составов: `{f, k, d, r:[инструмент, стоимость ₽, количество|null, страница,…], c:{класс: ₽}, s:{сектор: ₽}, t(итог ₽), st(статус сверки итога), n:[примечания], nl, src, nav?, li?, rep?, e?(оценка), nf?(нет полного АУМ), agg?, pub?}`; `f` — индекс фонда, `k` — 0 резервы / 1 накопления |

### `web/lib/npf.js` — экспорты

Константы и мелкие помощники: `KN` (`['Резервы','Накопления']`), `KNL`, `RES = 16`, `OFZ = 0`, `dmy('2026-03-31') → '31.03.2026'`, `monthLabel('2026-03') → 'мар 2026'`, `shortName(name)` (без «НПФ», ««»»). `kind`: `0 | 1 | 'all'`; `src`: `'comp'` (расчёт по составу) | `'struct'` (отчёт о структуре); `grp`: `'class'` | `'sector'`; `month`: `'YYYY-MM'` (null → выбор по умолчанию, как в исходном приложении; при смене kind/src UI передаёт null). Ниже `ix` — результат `buildIndex`.

**Загрузка**
* `loadNpf({fetchImpl, base = DEFAULT_DATA_BASE, onProgress}) → Promise<db>` — manifest → куски, `onProgress({file, loaded, total})` (байты по manifest), ошибка при HTTP ≠ 2xx. `db` — исходная база (ключи `built, monitorUpdated, classes, sectors, funds, names, isins, issuers, pages, inst, groups, srcs, notes, labels, issues, comp, struct, sectorNote, aumNote`).
* `getNpf(opts) → Promise<ix>` — `loadNpf` + `buildIndex` с кэшем в памяти (сбой не кэшируется); `clearNpfCache()`.
* `buildIndex(db) → ix` `{db, F (funds), CL (classes), SEC, INST, struct, months (все 'YYYY-MM' по возрастанию), compBy, structBy, compM, structM}`.

**Доступ**: `fundKinds(ix, fi)`, `fundSKinds(ix, fi)` (виды средств с составом / структурой), `kindsOf(ix, fi, kind, src)`, `combine(ix, parts)`, `rowsOf(ix, o)`, `groupSum(rows, key)`, `posKey(row)`, `isIsin(s)`, `monthComp(ix, fi, kind, m)`, `lastComp(ix, fi, kind, m)` (не глубже 12 мес.), `sharesComp(o, grp) → {индекс: %}`, `compAt(ix, fi, kind, date)`, `structAt(ix, fi, kind, date)`, `sharesAt(ix, fi, kind, src, grp, date)`, `fundDates(ix, fi, kind, src)`.

**Рынок**
* `marketMonths(ix, kind, src) → [{month, count}]`.
* `marketOverview(ix, {kind=0, src='struct', month=null}) → {kind, src (при 'all' → 'comp'), month, avail:[{month,count}], rows:[{i, f, sh:{класс:%}|null, rv, d, aum|null, aumD, est}] (по АУМ ↓), withSh, withAum, totAum, wn, wa, weighted[17] (% взвеш. по АУМ), simple[17], usedCols[17] (bool), maxSh, top (≤12 строк rows по АУМ)}`. KPI «Активы под наблюдением» = `totAum`; «Фондов со структурой» = `withSh`; «ОФЗ/Корп/Акции взвешенно» = `weighted[0|2|6]`, «простое среднее» = `simple[…]`; нижняя строка таблицы «Взвешенно по АУМ · wn фонд.» = `weighted`, `wa`, `wn`.
* `sortMarketRows(ix, rows, {sort:'aum'|'name'|индекс класса, dir:1|-1})`.
* `marketSeries(ix, {kind, mode:'share'|'value', from='2020-12'}) → {cols:[{month, n, aum, v:{класс: % | трлн ₽}|null (n < 3)}], used:[классы]}` — график «Структура рынка во времени».

**Фонд**
* `fundView(ix, {fi, kind, src, grp, idx}) → {fi, fund, kind, src, grp, isComp, dates, idx, date, cur, sr, sh, prevSh, prev, rows, pos, kpi, notes, coverage:{comp:[{kind,from,to,count}], struct:[…]}, tabs:{holdings, issuers, recon}, kinds:{comp, struct}}` — нормализует вход (нет состава → `struct`; `all`+struct → comp; нет дат → подставляет доступный вид; `idx` вне диапазона → последняя дата). `kpi` для состава: `{aum, est, nav|null, navLabel, agg, deltaPct, deltaAbs, hasPrev, ofzValue, ofzShare|null, top5Share, nPositions, nRows}`; для структуры: `{biggestClass, biggestShare, ofz, corp, stocks, sum, rv, lastAum, lastAumDate}`. `rows`/`pos` (строки активов и позиции, сгруппированные по ISIN) — только для состава.
* `mixList(ix, view) → [{i, cat, share}]` (кольцо «Структура на дату»), `fundChanges(ix, view) → [{i, cat, delta п.п.}]` (топ-6), `fundStructureSeries(ix, view, {stack:'share'|'value'}) → {cols:[{date, v}], used}`, `fundVolumeSeries(ix, view, {metric:'aum'|'nav'}) → {xs, series:[{k,name,v}], scale, unit, max}`.
* Вкладки: `fundHoldings(ix, view, {q, cat, sort:'desc'|'asc'|'name'}) → {list, total, sum}` («Состав»); `fundIssuers(ix, view) → {list:[{g,name,value,n,cls}], top10}`; `fundCompareDates(ix, view, {from, to}) → {from, to, ca, cb, rows:[{i,cat,a,b,valA,valB}], maxDelta}`; `fundRecon(ix, {fi, kind, month}) → {supported, comp, struct, rows:[{i,cat,a,b,diff}], srRows, history:{xs,v}|null}`; `structSourceRows(ix, structRecord)`; `fundSources(ix, fi) → {comps, structs, stats}`.

**Сравнение**: `defaultCompareFunds(ix, kind, asOf='2026-03')`, `compareFunds(ix, {funds, kind, metric:'aum'|'0'…, src, month}) → {funds, src, metric, xs, series:[{fi,name,v}], month, bars:[{fi, sh, dt}], used}` (метрика `'aum'` — только по составу, млрд ₽; иначе доля класса, %).

**Бумаги и эмитенты**: `instKey(ix, it)`, `monthHoldings(ix, kind, m)`, `securitiesMonths(ix, kind)`, `securitiesOverview(ix, {kind, month, q}) → {avail, month, nFunds, total, groups:[{id,name,value,nFunds,cls}], instruments:[{key,it,name,isin,issuer,cls,sec,g,value,nFunds}], historical}` (UI показывает первые 60), `securityDetail(ix, {kind, month, sel:{t:'g'|'i', id}}) → {title, rep, kindLabel, month, total, totalShare, holders, nHolders, instruments, xs, series, maxTotal, maxMonth, first, last} | null`, `searchGlobal(ix, q) → {funds:[{i,fund}], groups:[{i,name}], insts:[{it,key,name,isin,cls}]}` (от 2 символов; 6/7/8 результатов).

**Данные и методика**: `dataOverview(ix, kind) → {built, monitorUpdated, nFunds, nWithComp, nWithStruct, nComp, nCompRows, nStruct, nStructReview, nInstruments, nGroups, from, to, sectorNote, aumNote, months, coverage:[{fi, cells:[{month, v:'both'|'comp'|'struct'|null}]}]}`, `structureTable(ix)`, `aumTable(ix)` (строки для CSV).

### Карта «раздел страницы → функции»
**Арифметика для UI (`web/lib/npf.js`; все возвращают `null` при делении на 0 / нечисловых аргументах — UI показывает «—»):**
- `rowShare(value, aum) → number|null` — доля строки от АУМ, %: `value/aum·100`; `null` если `aum` не конечное число > 0 или `value` не число.
- `groupShare(items, aum) → number|null` — доля суммы набора (элементы — числа или объекты `{value}`) от АУМ, %.
- `topShare(list, n, aum) → number|null` — доля первых `n` элементов списка (уже по убыванию `value`) от АУМ, % (топ-5, топ-10 эмитентов).
- `compareDelta(a, b) → {abs, pct}|null` — изменение АУМ/стоимости `a → b`: `abs = b−a` (₽), `pct = (b/a−1)·100` (`null` при `a = 0`); `null`, если `a` или `b` не число.
- `diffOf(a, b) → number|null` — разность `b−a` (п. п. долей или ₽ стоимостей).
- `barFrac(value, max) → number|null` — длина столбика `|value|/max`, 0…1 (`null` при `max ≤ 0`).
- `reconFlag(diff, threshold = RECON_THRESHOLD) → {diff, abs, flag}|null` — `flag = |diff| ≥ threshold` (порог `RECON_THRESHOLD = 2` п. п.).
`securityDetail` для эмитента/бумаги, которых нет ни в одном составе, возвращает пустую карточку (`xs: []`, `holders: []`, `first/last/maxMonth: null`), а не бросает исключение.

Рынок: `marketOverview` + `sortMarketRows` + `marketSeries`. Фонд: `fundView` → `mixList`/`fundChanges`/`fundStructureSeries`/`fundVolumeSeries` (Обзор), `fundHoldings`, `fundIssuers`, `fundCompareDates`, `fundRecon`, `fundSources`. Сравнение: `compareFunds`. Бумаги и эмитенты: `securitiesOverview`, `securityDetail`; поиск в шапке — `searchGlobal`. Данные и методика: `dataOverview` (+ тексты методики — из исходного приложения, `site`). Переключатель вида средств — параметр `kind` везде. Загрузка при первом заходе на `#/npf`: `getNpf({base: DATA_BASE, onProgress})` (≈ 13 МБ, 11 запросов; показать индикатор). Эталоны чисел (из исходного приложения) — `tests/web/npf.test.mjs`.
