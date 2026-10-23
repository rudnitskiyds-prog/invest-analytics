"""
Преднастроенные пассивные стратегии в российской адаптации.

Замена классов активов индексами-аналогами:
  акции            -> MCFTR
  корп. облигации  -> RUCBITR + RUCBTRNS (склейка, CORP_CHAIN)
  гос. облигации   -> RGBITR / RUGBITR1Y / RUGBITR5+ / RUGBITR10Y
  золото           -> учётная цена ЦБ (GOLD_CBR)
  денежный рынок   -> индекс RUONIA
Подклассы (рост/дивиденды) заменяются единым индексом с равными долями.

Веса — редактируемые шаблоны по умолчанию. Источник составов — каталог
core/analytics/portfolios.py (PORTFOLIOS, записи с полем strategy).
"""
from __future__ import annotations

from core.analytics.portfolios import PORTFOLIOS

# Составы — из каталога портфелей core/analytics/portfolios.py (ленивые портфели с полем strategy);
# имена, порядок, веса и частота ребалансировки прежние (web/lib/strategies.js — копия).
STRATEGIES: dict[str, dict] = {
    p["strategy"]: {"weights": dict(p["weights"]), "rebalance": p["rebalance"], "note": p["note"]}
    for p in PORTFOLIOS.values() if p.get("strategy")
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
