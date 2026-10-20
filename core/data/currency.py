"""
Справочник валют без сетевых зависимостей.

* CURRENCY_CODES — внутренние коды валют ЦБ для XML_dynamic.asp (VAL_NM_RQ);
* normalize_currency(code) — код валюты ISS → ISO ('SUR' / 'RUR' / пусто / NaN → 'RUB').

Вынесено отдельно, чтобы core/data/iss.py не зависел от клиента ЦБ.
"""
from __future__ import annotations

import pandas as pd

CURRENCY_CODES = {"USD": "R01235", "EUR": "R01239", "CNY": "R01375", "CHF": "R01775", "GBP": "R01035"}

# Обозначения рубля в ISS: 'SUR' (FACEUNIT / CURRENCYID), реже 'RUR'
_RUB_ALIASES = {"SUR", "RUR", "RUB"}


def normalize_currency(code) -> str:
    """Код валюты ISS → ISO: 'SUR' / 'RUR' / None / '' / NaN → 'RUB', иначе upper()."""
    try:
        if pd.isna(code):
            return "RUB"
    except (TypeError, ValueError):  # не скаляр — pd.isna вернул массив
        pass
    c = str(code).strip().upper()
    if not c or c in _RUB_ALIASES or c == "NAN":
        return "RUB"
    return c
