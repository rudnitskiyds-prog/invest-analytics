"""
Чтение файлов сборщика (public/data/*.json, scripts/collect_data.py) для Python-приложения.

Файлы T-Invest API (дивиденды, фундаментальные показатели, справочники акций и фондов)
появляются только после сбора с токеном; их отсутствие или порча — не ошибка:
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


def etf_commissions() -> pd.Series:
    """Тикер -> fixed_commission из tinvest_etfs.json, % годовых (как отдаёт T-Invest API)."""
    data = load("tinvest_etfs")
    if not isinstance(data, list):
        return pd.Series(dtype=float)
    s = {r["ticker"]: r.get("fixed_commission") for r in data if r.get("ticker")}
    return pd.Series(s, dtype=float)
