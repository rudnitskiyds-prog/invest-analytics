"""
Данные Банка России: учётные цены на драгметаллы, RUONIA, ключевая ставка,
официальные курсы иностранных валют.

* metal_price() / gold_price() — учётная цена драгметалла ЦБ, руб./г (золото используется в НИР);
* ruonia_rates() / ruonia_index() — ставка RUONIA и накопленный индекс
  денежного рынка (аналог фонда ликвидности LQDT);
* key_rate() — ключевая ставка (для переменной безрисковой ставки);
* normalize_currency(code), CURRENCY_CODES — реэкспорт из core/data/currency.py;
* currency_rate(code, start, end) — официальный курс ЦБ, руб. за 1 единицу валюты (ряд по датам);
* currency_rate_on(code, date) — курс ЦБ на дату (последний установленный на эту дату или ранее);
* rates_on(codes, date) — курсы для набора валют, по одному запросу на валюту; при ошибке — NaN.

Курсы берутся из официальной XML-выгрузки ЦБ (scripts/XML_dynamic.asp);
курс = Value / Nominal (у части валют, напр. CNY, номинал бывает 10). Запросы курсов — с таймаутом
FX_TIMEOUT и отрицательным кэшем FX_DOWN_TTL после сбоя соединения / таймаута (исключение
CBRUnavailable; HTTP 4xx/5xx пробрасывается как HTTPError без метки). В офлайн-режиме (IP_OFFLINE=1)
курсы по сети не запрашиваются (CBROffline), rates_on сразу возвращает NaN (кэш читается).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

import pandas as pd
import requests

from core.config import CBR_BASE, TTL_HISTORY_OPEN
from core.data.cache import HttpCache
from core.data.currency import CURRENCY_CODES, normalize_currency  # noqa: F401 (реэкспорт)

log = logging.getLogger(__name__)

_cache = HttpCache()
_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0 invest-platform/0.1"})

METAL_CODES = {"gold": 1, "silver": 2, "platinum": 3, "palladium": 4}

# Курсы валют нужны «на сейчас» для витрин и портфеля: короткий таймаут и «отрицательный кэш» —
# после сбоя сети запросы курсов не повторяются FX_DOWN_TTL секунд (метка хранится в HTTP-кэше,
# поэтому переживает и перезапуск скрипта Streamlit, и перезапуск процесса).
FX_PATH = "/scripts/XML_dynamic.asp"
FX_TIMEOUT = 10
FX_DOWN_TTL = 5 * 60
_FX_DOWN_KEY = "cbr:fx_unavailable"


class CBRUnavailable(ConnectionError):
    """Сервер ЦБ недоступен (сбой сети или действует отрицательный кэш)."""


class CBROffline(CBRUnavailable):
    """Офлайн-режим (IP_OFFLINE=1): сетевые запросы курсов не выполняются."""


def _offline() -> bool:
    return os.environ.get("IP_OFFLINE") == "1"


def _fetch(url: str, params: Optional[dict] = None, data: Optional[str] = None,
           headers: Optional[dict] = None, ttl=TTL_HISTORY_OPEN) -> str:
    k = HttpCache.key(url, {**(params or {}), "_body": data or ""})
    hit = _cache.get(k, ttl)
    if hit is not None:
        return hit
    fx = url.endswith(FX_PATH)
    if fx and _offline():
        raise CBROffline("офлайн-режим (IP_OFFLINE=1): курсы ЦБ не запрашиваются")
    if fx and _cache.get(_FX_DOWN_KEY, FX_DOWN_TTL) is not None:
        raise CBRUnavailable(f"ЦБ недоступен (повторный запрос курсов не раньше чем через {FX_DOWN_TTL // 60} мин.)")
    try:
        if data is None:
            r = _s.get(url, params=params, timeout=FX_TIMEOUT if fx else 60)
        else:
            r = _s.post(url, data=data.encode("utf-8"), headers=headers, timeout=60)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout) as e:
        # отрицательный кэш — только при недоступности сервера; HTTPError (4xx/5xx) — без метки
        if fx:
            _cache.put(_FX_DOWN_KEY, str(time.time()))
            raise CBRUnavailable(f"ЦБ недоступен: {e}") from e
        raise
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


# --------------------------------------------------------------------- курсы валют
def currency_rate(code: str, start: str = "2008-01-01", end: Optional[str] = None) -> pd.Series:
    """Официальный курс ЦБ, руб. за 1 единицу валюты: Value / Nominal из XML_dynamic.asp.

    Индекс — даты, на которые курс установлен (DatetimeIndex, по возрастанию); на выходные
    курс отдельно не публикуется — действует последний установленный (выравнивать ffill).
    Для рубля — ряд из единиц по рабочим дням (без сети). Неизвестная валюта → ValueError.
    Запрос режется на куски по 5 лет (закрытые куски кэшируются бессрочно).
    """
    cur_code = normalize_currency(code)
    s0 = pd.Timestamp(start).date()
    e0 = pd.Timestamp(end).date() if end else dt.date.today()
    if cur_code == "RUB":
        s = pd.Series(1.0, index=pd.bdate_range(s0, e0), dtype=float)
        s.name = "RUB"
        return s
    if cur_code not in CURRENCY_CODES:
        raise ValueError(f"Неизвестная валюта для курса ЦБ: {code!r}")
    vid = CURRENCY_CODES[cur_code]
    out = {}
    cur = s0
    while cur <= e0:
        try:
            nxt = dt.date(cur.year + 5, cur.month, cur.day) - dt.timedelta(days=1)
        except ValueError:  # 29 февраля
            nxt = dt.date(cur.year + 5, cur.month, 28)
        nxt = min(nxt, e0)
        xml = _fetch(f"{CBR_BASE}/scripts/XML_dynamic.asp",
                     {"date_req1": cur.strftime("%d/%m/%Y"), "date_req2": nxt.strftime("%d/%m/%Y"),
                      "VAL_NM_RQ": vid},
                     ttl=_ttl(nxt))
        root = _parse_xml(xml)
        for rec in root.findall("Record"):
            nom, val = rec.find("Nominal"), rec.find("Value")
            if val is None or not val.text:
                continue
            n = _num(nom.text) if nom is not None and nom.text else 1.0
            out[pd.to_datetime(rec.get("Date"), dayfirst=True)] = _num(val.text) / (n or 1.0)
        cur = nxt + dt.timedelta(days=1)
    s = pd.Series(out, dtype=float).sort_index()
    s.index = pd.DatetimeIndex(s.index)
    s.name = cur_code
    return s


def currency_rate_on(code: str, date=None) -> float:
    """Курс ЦБ (руб. за 1 единицу) на дату `date` (по умолчанию — сегодня):
    последний установленный на эту дату или ранее, поиск в окне 14 дней назад.
    Для рубля — 1.0 без обращения к сети. Нет данных в окне → LookupError."""
    cur_code = normalize_currency(code)
    if cur_code == "RUB":
        return 1.0
    d = pd.Timestamp(date).normalize() if date is not None else pd.Timestamp.today().normalize()
    s = currency_rate(cur_code, (d - pd.Timedelta(days=14)).date().isoformat(), d.date().isoformat())
    s = s.loc[:d].dropna()
    if s.empty:
        raise LookupError(f"Нет курса ЦБ {cur_code} на {d.date()} и за 14 дней до этой даты")
    return float(s.iloc[-1])


def rates_on(codes, date=None) -> dict:
    """Курсы ЦБ на дату для набора валют: {код ISO: руб. за 1 единицу}.
    Один запрос на валюту; рубль — 1.0 без сети. При ошибке — NaN и предупреждение в лог
    с причиной: неизвестная валюта / ЦБ не публиковал курс / ЦБ недоступен (сеть, отрицательный кэш)."""
    out = {}
    for c in {normalize_currency(x) for x in codes}:
        if c != "RUB" and c not in CURRENCY_CODES:
            log.warning("Курс %s: неизвестная валюта, курс ЦБ не запрашивается", c)
            out[c] = float("nan")
            continue
        try:
            out[c] = currency_rate_on(c, date)
        except CBROffline as e:
            log.info("Курс %s: %s — NaN", c, e)
            out[c] = float("nan")
        except LookupError as e:
            log.warning("Курс %s: ЦБ не публиковал курс на дату (%s)", c, e)
            out[c] = float("nan")
        except Exception as e:  # noqa: BLE001 — сеть, отрицательный кэш, разбор XML
            log.warning("Курс %s: ЦБ недоступен (%s)", c, e)
            out[c] = float("nan")
    return out
