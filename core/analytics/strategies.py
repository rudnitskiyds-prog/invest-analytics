"""
Преднастроенные пассивные стратегии в российской адаптации.

Замена классов активов индексами-аналогами:
  акции            -> MCFTR
  корп. облигации  -> RUCBITR + RUCBTRNS (склейка, CORP_CHAIN)
  гос. облигации   -> RGBITR / RUGBITR1Y / RUGBITR5+ / RUGBITR10Y
  золото           -> учётная цена ЦБ (GOLD_CBR)
  денежный рынок   -> индекс RUONIA
Подклассы (рост/дивиденды) заменяются единым индексом с равными долями.

Веса — редактируемые шаблоны по умолчанию.
"""
from __future__ import annotations

STRATEGIES: dict[str, dict] = {
    "Лежебока плюс (Спирин)": {
        "weights": {"MCFTR": 0.30, "CORP_CHAIN": 0.30, "GOLD_CBR": 0.30, "RUONIA": 0.10},
        "rebalance": "A",
        "note": "30% EQMX / 30% OBLG / 30% GOLD / 10% LQDT",
    },
    "Портфель 60/40": {
        "weights": {"MCFTR": 0.60, "RGBITR": 0.40},
        "rebalance": "A",
        "note": "60% акции / 40% облигации",
    },
    "Постоянный портфель (Браун)": {
        "weights": {"MCFTR": 0.25, "RUGBITR10Y": 0.25, "GOLD_CBR": 0.25, "RUONIA": 0.25},
        "rebalance": "A",
        "note": "25% акции / 25% длинные облигации / 25% золото / 25% денежный рынок",
    },
    "Всепогодный портфель (Далио)": {
        "weights": {"MCFTR": 0.30, "RUGBITR10Y": 0.40, "RUGBITR5+": 0.15, "GOLD_CBR": 0.075,
                    "MOEXEU": 0.075},
        "rebalance": "A",
        "note": "30% акции / 40% длинные / 15% среднесрочные облигации / 7,5% золото / "
                "7,5% сырьё -> индекс электроэнергетики MOEXEU (нет рублёвого сырьевого индекса)",
    },
    "Портфель 40/40/20": {
        "weights": {"MCFTR": 0.40, "CORP_CHAIN": 0.40, "GOLD_CBR": 0.20},
        "rebalance": "A",
        "note": "40% акции (рост + дивиденды -> MCFTR) / 40% облигации -> корп. облигации / 20% золото",
    },
    "Завещание Баффетта 90/10": {
        "weights": {"MCFTR": 0.90, "RUGBITR1Y": 0.10},
        "rebalance": "A",
        "note": "90% акции / 10% краткосрочные гособлигации",
    },
}

BENCHMARKS = {
    "MCFTR": "Индекс МосБиржи полной доходности",
    "RGBITR": "Индекс гособлигаций (TR)",
    "RUONIA": "Денежный рынок (RUONIA)",
    "GOLD_CBR": "Золото (ЦБ)",
}


def all_asset_keys() -> list[str]:
    keys = set()
    for s in STRATEGIES.values():
        keys |= set(s["weights"])
    return sorted(keys)
