"""
Сборщик данных для статического сайта.

Раз в день (GitHub Actions / Yandex Cloud Function / cron) забирает ряды, которые
браузер пользователя не может получить сам (у cbr.ru нет CORS), и сохраняет их
в public/data/*.json. Сайт читает эти файлы как обычные статические ресурсы.

    python -m scripts.collect_data            # все источники
    python -m scripts.collect_data --only cbr_gold ruonia

Формат каждого файла:
    {"id": ..., "title": ..., "unit": ..., "source": ..., "updated": ISO-время,
     "first": "YYYY-MM-DD", "last": "YYYY-MM-DD", "data": [["YYYY-MM-DD", value], ...]}

Если источник не ответил, прежний файл остаётся нетронутым, а в status.json
записывается ошибка — сайт продолжает работать на последних успешных данных.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import traceback
from pathlib import Path

import pandas as pd

from core.config import ROOT
from core.data import cbr

OUT = ROOT / "public" / "data"
START = "2008-01-01"


def _rows(s: pd.Series, digits: int) -> list:
    s = s.dropna().sort_index()
    return [[d.strftime("%Y-%m-%d"), round(float(v), digits)] for d, v in s.items()]


SOURCES: dict[str, dict] = {
    "cbr_gold": {
        "title": "Золото, учётная цена Банка России",
        "unit": "руб./г",
        "source": "https://www.cbr.ru/scripts/xml_metall.asp",
        "load": lambda: cbr.gold_price(START),
        "digits": 2,
        "min_rows": 3000,
    },
    "ruonia_rate": {
        "title": "Ставка RUONIA",
        "unit": "% годовых",
        "source": "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx (RuoniaXML)",
        "load": lambda: cbr.ruonia_rates(START),
        "digits": 4,
        "min_rows": 3000,
    },
    "ruonia_index": {
        "title": "Индекс денежного рынка (накопленная RUONIA, база 100)",
        "unit": "пункты",
        "source": "расчёт по RUONIA: I_t = I_{t-1} × (1 + r_{t-1} × дней / 365)",
        "load": lambda: cbr.ruonia_index(START),
        "digits": 5,
        "min_rows": 3000,
    },
    "key_rate": {
        "title": "Ключевая ставка Банка России",
        "unit": "доля годовых",
        "source": "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx (KeyRateXML)",
        "load": lambda: cbr.key_rate("2013-09-13"),
        "digits": 4,
        "min_rows": 2000,
    },
}


def collect(name: str, spec: dict) -> dict:
    s: pd.Series = spec["load"]()
    if len(s) < spec["min_rows"]:
        raise ValueError(f"слишком мало строк: {len(s)} < {spec['min_rows']} — ответ похож на ошибку")
    payload = {
        "id": name, "title": spec["title"], "unit": spec["unit"], "source": spec["source"],
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "first": s.index.min().strftime("%Y-%m-%d"), "last": s.index.max().strftime("%Y-%m-%d"),
        "data": _rows(s, spec["digits"]),
    }
    (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                                      encoding="utf-8")
    return {"ok": True, "rows": len(s), "last": payload["last"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="собрать только указанные источники")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)

    status_path = OUT / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    names = args.only or list(SOURCES)
    failed = 0
    for name in names:
        try:
            res = collect(name, SOURCES[name])
            print(f"OK   {name}: {res['rows']} строк, до {res['last']}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
            print(f"FAIL {name}: {res['error']}", file=sys.stderr)
            traceback.print_exc(limit=2)
        res["checked"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        prev = status.get(name, {})
        if not res["ok"] and prev.get("last"):
            res["last"] = prev["last"]          # последние успешные данные остаются на сайте
        status[name] = res
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    # код 1 только если не удалось собрать ничего — тогда GitHub пришлёт уведомление об ошибке
    return 1 if failed == len(names) else 0


if __name__ == "__main__":
    sys.exit(main())
