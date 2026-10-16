"""
Данные Банка России: учётные цены на драгметаллы, RUONIA, ключевая ставка.

* gold_price() — учётная цена золота ЦБ, руб./г (ряд золота по умолчанию);
* ruonia_rates() / ruonia_index() — ставка RUONIA и накопленный индекс
  денежного рынка (аналог фонда ликвидности LQDT);
* key_rate() — ключевая ставка (для переменной безрисковой ставки).
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from typing import Optional

import pandas as pd
import requests

from core.config import CBR_BASE, TTL_HISTORY_OPEN
from core.data.cache import HttpCache

_cache = HttpCache()
_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0 invest-platform/0.1"})

METAL_CODES = {"gold": 1, "silver": 2, "platinum": 3, "palladium": 4}


def _fetch(url: str, params: Optional[dict] = None, data: Optional[str] = None,
           headers: Optional[dict] = None, ttl=TTL_HISTORY_OPEN) -> str:
    k = HttpCache.key(url, {**(params or {}), "_body": data or ""})
    hit = _cache.get(k, ttl)
    if hit is not None:
        return hit
    if data is None:
        r = _s.get(url, params=params, timeout=60)
    else:
        r = _s.post(url, data=data.encode("utf-8"), headers=headers, timeout=60)
    r.raise_for_status()
    enc = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "windows-1251"
    text = r.content.decode(enc, errors="replace")
    _cache.put(k, text)
    return text


def _parse_xml(text: str) -> ET.Element:
    """Парсинг XML-строки без учёта объявленной кодировки (текст уже декодирован)."""
    return ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text))


def _ttl(end: dt.date):
    return None if end < dt.date.today() - dt.timedelta(days=1) else TTL_HISTORY_OPEN


def _num(x: str) -> float:
    return float(x.replace(",", ".").replace(" ", ""))


def metal_price(metal: str = "gold", start: str = "2008-01-01", end: Optional[str] = None) -> pd.Series:
    """Учётная цена ЦБ, руб. за грамм. Запрос режется на куски по 5 лет."""
    code = METAL_CODES[metal]
    s0 = pd.Timestamp(start).date()
    e0 = pd.Timestamp(end).date() if end else dt.date.today()
    out = {}
    cur = s0
    while cur <= e0:
        nxt = min(dt.date(cur.year + 5, cur.month, cur.day) - dt.timedelta(days=1), e0)
        xml = _fetch(f"{CBR_BASE}/scripts/xml_metall.asp",
                     {"date_req1": cur.strftime("%d/%m/%Y"), "date_req2": nxt.strftime("%d/%m/%Y")},
                     ttl=_ttl(nxt))
        root = _parse_xml(xml)
        for rec in root.findall("Record"):
            if int(rec.get("Code")) != code:
                continue
            d = pd.to_datetime(rec.get("Date"), dayfirst=True)
            buy = rec.find("Buy")
            if buy is not None and buy.text:
                out[d] = _num(buy.text)
        cur = nxt + dt.timedelta(days=1)
    s = pd.Series(out, dtype=float).sort_index()
    s.name = f"CBR_{metal.upper()}"
    return s


def gold_price(start: str = "2008-01-01", end: Optional[str] = None) -> pd.Series:
    return metal_price("gold", start, end)


def _soap(method: str, body: str, end: dt.date) -> ET.Element:
    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
<soap12:Body><{method} xmlns="http://web.cbr.ru/">{body}</{method}></soap12:Body></soap12:Envelope>"""
    text = _fetch(f"{CBR_BASE}/DailyInfoWebServ/DailyInfo.asmx", data=envelope,
                  headers={"Content-Type": "application/soap+xml; charset=utf-8"}, ttl=_ttl(end))
    return _parse_xml(text)


def _range_body(start: str, end: Optional[str], a="fromDate", b="ToDate"):
    s = pd.Timestamp(start).strftime("%Y-%m-%dT00:00:00")
    e_date = pd.Timestamp(end).date() if end else dt.date.today()
    return f"<{a}>{s}</{a}><{b}>{e_date.isoformat()}T00:00:00</{b}>", e_date


def ruonia_rates(start: str = "2010-01-01", end: Optional[str] = None) -> pd.Series:
    """Ставка RUONIA, % годовых, по датам."""
    body, e = _range_body(start, end)
    root = _soap("RuoniaXML", body, e)
    out = {}
    for el in root.iter():
        if el.tag.split("}")[-1] == "ro":
            d = el.find("D0")
            v = el.find("ruo")
            if d is not None and v is not None:
                out[pd.Timestamp(d.text[:10])] = float(v.text)
    s = pd.Series(out, dtype=float).sort_index()
    s.name = "RUONIA_RATE"
    return s


def ruonia_index(start: str = "2010-01-01", end: Optional[str] = None, base: float = 100.0) -> pd.Series:
    """Индекс денежного рынка: I_t = I_{t-1} × (1 + RUONIA_{t-1} × дней / 365)."""
    r = ruonia_rates(start, end)
    if r.empty:
        return r
    days = r.index.to_series().diff().dt.days.shift(-1).fillna(1)
    growth = 1 + r / 100 * days / 365
    idx = base * growth.cumprod().shift(1).fillna(1.0)
    idx.name = "RUONIA"
    return idx


def key_rate(start: str = "2013-09-13", end: Optional[str] = None) -> pd.Series:
    """Ключевая ставка ЦБ, доля годовых."""
    body, e = _range_body(start, end)
    root = _soap("KeyRateXML", body, e)
    out = {}
    for el in root.iter():
        if el.tag.split("}")[-1] == "KR":
            d, v = el.find("DT"), el.find("Rate")
            if d is not None and v is not None:
                out[pd.Timestamp(d.text[:10])] = float(v.text) / 100
    s = pd.Series(out, dtype=float).sort_index()
    s.name = "KEY_RATE"
    return s
