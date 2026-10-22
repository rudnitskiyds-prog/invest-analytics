"""
Чтение файлов сборщика (public/data/*.json, scripts/collect_data.py) для Python-приложения.

Файлы T-Invest API (дивиденды, фундаментальные показатели, справочники акций и фондов)
появляются только после сбора с токеном, справочник фондов RusETFs (rusetfs_funds.json) —
после сбора без токена; их отсутствие или порча — не ошибка:
функции возвращают пустой результат, а вызывающий код откатывается на прежние источники.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Optional

import pandas as pd

from core import config

# Русские названия секторов T-Invest API (поле Share.sector); неизвестные — как есть
SECTOR_LABELS = {
    "financial": "Финансы", "energy": "Энергетика", "materials": "Сырьё",
    "industrials": "Промышленность", "utilities": "Электроэнергетика", "telecom": "Телеком",
    "it": "ИТ", "consumer": "Потребительский", "health_care": "Здравоохранение",
    "real_estate": "Недвижимость", "other": "Другое", "ecomaterials": "Экоматериалы",
    "green_energy": "Зелёная энергетика", "green_buildings": "Зелёное строительство",
    "electrocars": "Электромобили",
}


@lru_cache(maxsize=32)
def _read(path: str, mtime: float) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load(name: str) -> Optional[Any]:
    """Поле data файла public/data/<name>.json или None, если файла нет / он битый."""
    p = config.PUBLIC_DATA_DIR / f"{name}.json"
    try:
        return _read(str(p), p.stat().st_mtime).get("data")
    except (OSError, ValueError, AttributeError):
        return None


def load_file(name: str) -> Optional[Any]:
    """Весь файл public/data/<name>.json (не только поле data) или None, если файла нет / он битый."""
    p = config.PUBLIC_DATA_DIR / f"{name}.json"
    try:
        return _read(str(p), p.stat().st_mtime)
    except (OSError, ValueError):
        return None


def symbol_stats() -> Optional[dict]:
    """Ночные рейтинги public/data/symbol_stats.json ({updated, source, total_return, split_adjusted, rf, window,
    classes, items}; формат — web/CONTRACT.md) — как loadSymbolStats в web/lib/symbol.js.
    Нет файла, он битый или без словаря items — None. Возвращается кэшируемый объект — не изменять."""
    js = load_file("symbol_stats")
    if not isinstance(js, dict) or not isinstance(js.get("items"), dict):
        return None
    return js


def dividends(secid: str) -> pd.DataFrame:
    """Выплаты из dividends.json в формате iss.dividends: secid, registryclosedate (строка
    YYYY-MM-DD), value, currencyid. Отменённые и записи без даты реестра / суммы отброшены."""
    data = load("dividends")
    rows = (data or {}).get(secid.upper(), []) if isinstance(data, dict) else []
    rec = [{"secid": secid.upper(), "registryclosedate": r.get("record_date"), "value": r.get("value"),
            "currencyid": r.get("currency") or "RUB"}
           for r in rows if not r.get("cancelled") and r.get("record_date") and r.get("value") is not None]
    return pd.DataFrame(rec, columns=["secid", "registryclosedate", "value", "currencyid"])


def fundamentals() -> pd.DataFrame:
    """fundamentals.json -> DataFrame, индекс — тикер, колонки — короткие имена полей."""
    data = load("fundamentals")
    if not isinstance(data, dict) or not data:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(data, orient="index")
    df.index.name = "secid"
    return df


def share_sectors() -> pd.Series:
    """Тикер -> сектор (русское название) из tinvest_shares.json."""
    data = load("tinvest_shares")
    if not isinstance(data, list):
        return pd.Series(dtype=object)
    s = {r["ticker"]: SECTOR_LABELS.get(r.get("sector"), r.get("sector"))
         for r in data if r.get("ticker") and r.get("sector")}
    return pd.Series(s, dtype=object)


# колонки fund_info() (индекс — ticker); поля записи rusetfs_funds.json (core/data/rusetfs.py)
FUND_INFO_COLUMNS = ["name", "issuer", "asset_class", "asset_subclass", "commission_pct", "aum_rub",
                     "trade_status", "trading_start", "active_management"]


def fund_info() -> pd.DataFrame:
    """Справочник фондов RusETFs из rusetfs_funds.json: индекс ticker, колонки FUND_INFO_COLUMNS.
    commission_pct — % годовых, aum_rub — СЧА, руб. (float, NaN — нет данных);
    active_management — True/False/None. Нет файла / он битый — пустой DataFrame с этими колонками."""
    data = load("rusetfs_funds")
    rows = [r for r in data if isinstance(r, dict) and r.get("ticker")] if isinstance(data, list) else []
    if not rows:
        return pd.DataFrame(columns=FUND_INFO_COLUMNS, index=pd.Index([], name="ticker", dtype=object))
    df = pd.DataFrame(rows).drop_duplicates("ticker").set_index("ticker")
    df = df.reindex(columns=FUND_INFO_COLUMNS)
    for c in ("commission_pct", "aum_rub"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["active_management"] = df["active_management"].astype(object).where(df["active_management"].notna(), None)
    return df


def _tinvest_commissions() -> pd.Series:
    """Тикер -> fixed_commission из tinvest_etfs.json, % годовых (как отдаёт T-Invest API).
    0 -> NaN: proto3 не передаёт нулевые поля, 0 неотличим от «нет данных»."""
    data = load("tinvest_etfs")
    if not isinstance(data, list):
        return pd.Series(dtype=float)
    s = {r["ticker"]: r.get("fixed_commission") for r in data if isinstance(r, dict) and r.get("ticker")}
    s = pd.Series(s, dtype=float)
    return s.where(s != 0)


def etf_commissions() -> pd.Series:
    """Тикер -> комиссия фонда, % годовых. Приоритет — RusETFs (rusetfs_funds.json, commission_pct > 0);
    тикеры, которых там нет или у которых комиссия не указана, дополняются T-Invest
    (tinvest_etfs.json, fixed_commission; 0 -> NaN). Ноль RusETFs не перекрывает ненулевое значение
    T-Invest (у реальных фондов комиссия > 0, ноль — скорее пропуск) и остаётся, только если
    у T-Invest значения нет. Нет обоих файлов — пустая Series."""
    ti = _tinvest_commissions()
    fi = fund_info()
    rus = fi["commission_pct"].dropna() if not fi.empty else pd.Series(dtype=float)
    if rus.empty:
        return ti
    if ti.empty:
        return rus.astype(float).rename(None).rename_axis(None)
    out = rus[rus > 0].combine_first(ti).combine_first(rus).astype(float)
    return out.rename(None).rename_axis(None)
