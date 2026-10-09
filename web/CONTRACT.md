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
(инвариант 3 CLAUDE.md): методику (`pages/7_Методика.py`, вкладка «Методика» в `web/`) обновляет `site`.

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
* `rf` — средняя по календарным дням ключевая ставка (доля) за окно из `public/data/key_rate.json`; нет файла — сбой источника.
  Одна ставка на всё окно, в т. ч. для бумаг с более короткой историей.
* Ранг: `(средний ранг по возрастанию − 1) / (N − 1) × 100` внутри класса, 1 знак; одна бумага — 50; нет значения — null.
  `score` — среднее доступных рангов. Коэффициенты округлены до 6 знаков, `months` — число месячных доходностей.
* Python: `core/analytics/ranking.py` (`symbol_metrics, percentile_ranks, rank_items, mean_rate, ex_dividends`),
  `core/data/iss.monthly_closes`; загрузчик подменяется через `scripts.collect_data._symbol_stats_loader`
  (методы `securities()` -> DataFrame SECID/SECTYPE, `monthly_closes(secid, start, end)` -> Series),
  часы бюджета — `scripts.collect_data._clock`. Сбой: > 20 % бумаг, 10 сбоев подряд, бюджет или < 100 акций.

## Страница бумаги в Streamlit: `core/analytics/symbol_page.py` (api) ↔ `pages/2_Карточка_бумаги.py` (site)

Python-аналог `web/lib/symbol.js` + расчётов `web/js/page-symbol.js`; интерфейс только вызывает эти функции
(инвариант 4) и кэширует их результаты сам (`st.cache_data`) — результаты picklable, функции без Streamlit.
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
