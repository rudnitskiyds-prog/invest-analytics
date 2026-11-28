"""Логотипы управляющих компаний (public/data/uk_logos.json) и распространение логотипа эмитента на облигации.
Логотип фонда в T-Invest — иконка продукта, поэтому у всех фондов одной УК показываем один логотип УК.
Используется сборщиком (scripts/collect_data.py, шаг uk_logos); читает уже собранные файлы public/data."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Optional

# УК (поле issuer RusETFs) -> источник логотипа. Виды: {"logo": файл, "color": "#…"} — файл T-Invest;
# {"share": тикер} / {"etf": тикер} — логотип этой бумаги; {"bond_issuer": имя} — логотип облигаций эмитента
# по ТОЧНОМУ имени (до первого пробела-серии: «Озон 1P-02» -> «Озон»). УК без записи — по частому общему
# логотипу фондов (≥ 2), иначе без логотипа (сайт покажет монограмму). Проверено глазами: tcs2 — знак «Т»
# в щите, ISSUANCEOZON — OZON, RU000A102XZ — красный знак АТОН.
UK_LOGO_SOURCES: dict[str, dict] = {
    "Т-Капитал": {"share": "T"},        # у фондов — иконки продуктов (TPAY, TBEU, …)
    "Озон": {"share": "OZON"},
    "МКБ": {"share": "CBOM"},           # акция названа ровно «МКБ»
    "АТОН": {"logo": "RU000A102XZ.png"},  # один фонд в справочнике; цвет — из справочника
    # курируемые алиасы «УК -> эмитент банковской группы» ("group": True — логотип группы, не самой УК);
    # знак каждого проверен глазами
    "БСПБ": {"share": "BSPB", "group": True},                          # Банк Санкт-Петербург
    "РСХБ": {"bond_issuer": "Россельхозбанк", "group": True},
    "Промсвязь": {"bond_issuer": "Промсвязьбанк", "group": True},
}
# Без логотипа остаются УК без эмитента группы в данных T-Invest (Райффайзен, Открытие, АК БАРС, БКС, Финам,
# Ингосстрах, Ренессанс Капитал, FinEx, …) — файлы кладёт владелец: web/img/uk/<slug>.png|svg
FILES_DIR = "img/uk"
# "Первая" (logoam11.png ×17), "Альфа Капитал" (RU000A100Q4.png ×4), "ВИМ Инвестиции" (vimlick.png ×3)
# получаются правилом «общий логотип ≥ 2 фондов»

MIN_COMMON = 2        # логотип общий минимум у стольких фондов УК — иначе это иконка продукта
MIN_ISSUER_BONDS = 2  # ключ эмитента подтверждён минимум стольким числом облигаций с логотипом
SOURCE = "T-Invest (логотипы), RusETFs (УК фондов)"


_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh sch _ y _ e yu ya".split()))


def slug(name: str) -> str:
    """Имя файла логотипа УК: латиница, [a-z0-9-]. «Альфа Капитал» -> «alfa-kapital», «ДоходЪ» -> «dokhod»."""
    t = "".join(_TR.get(c, c) for c in name.lower()).replace("_", "")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def issuer_key(name: str) -> str:
    """Эмитент облигации по имени: до первой цифры (серийная часть: выпуск, дата, купон), без хвостовых
    разделителей. «ВТБ Б1-243» -> «ВТБ Б», «Озон 1P-02» -> «Озон», «ОФЗ 26245» -> «ОФЗ»."""
    return re.sub(r"[\s\-–.,]+$", "", re.split(r"\d", name or "", maxsplit=1)[0])


def _rec(r: dict) -> Optional[tuple[str, str]]:
    return (r["logo"], r.get("logo_color") or "") if r.get("logo") else None


def _resolve(src: dict, etfs: dict, shares: dict, bonds_by_name: dict, colors: dict) -> Optional[tuple[str, str]]:
    if "logo" in src:
        return src["logo"], src.get("color") or colors.get(src["logo"], "")
    if "share" in src:
        return _rec(shares.get(src["share"], {}))
    if "etf" in src:
        return _rec(etfs.get(src["etf"], {}))
    if "bond_issuer" in src:
        iss = src["bond_issuer"]   # имя облигации = эмитент или «эмитент <серия>» (граница слова)
        v = {x for n, xs in bonds_by_name.items() if n == iss or n.startswith(iss + " ") for x in xs}
        return next(iter(v)) if len(v) == 1 else None
    return None


def build(rusetfs_rows: list[dict], etfs: list[dict], shares: list[dict], bonds: list[dict],
          screener_bonds: Optional[list[dict]] = None, web_dir=None) -> dict:
    """Тело uk_logos.json без updated/source: {funds, uk, bonds_issuer}; web_dir — корень web/ для поиска явных файлов УК.
    bonds_issuer — облигации скринера без записи в tinvest_bonds, получившие логотип эмитента: эмитент —
    ключ issuer_key имени из скринера; применяется при ≥ MIN_ISSUER_BONDS подтверждающих облигациях
    и ровно одном логотипе у ключа."""
    etf_by = {r["ticker"]: r for r in etfs}
    share_by = {r["ticker"]: r for r in shares}
    colors = {r["logo"]: r.get("logo_color") or "" for r in [*etfs, *shares, *bonds] if r.get("logo")}
    bonds_by_name: dict[str, set] = defaultdict(set)
    for r in bonds:
        if _rec(r):
            bonds_by_name[r["name"]].add(_rec(r))
    funds, uk = {}, {}
    by_uk: dict[str, list] = defaultdict(list)
    for r in rusetfs_rows:
        if r.get("ticker") and r.get("issuer"):
            funds[r["ticker"]] = r["issuer"]
            by_uk[r["issuer"]].append(r["ticker"])
    for name, tickers in by_uk.items():
        res, why = None, None
        if name in UK_LOGO_SOURCES:
            res, why = _resolve(UK_LOGO_SOURCES[name], etf_by, share_by, bonds_by_name, colors), "table"
        else:
            c = Counter(_rec(etf_by[t]) for t in tickers if t in etf_by and _rec(etf_by[t]))
            if c and c.most_common(1)[0][1] >= MIN_COMMON:
                res, why = c.most_common(1)[0][0], "common"
        e = ({"logo": res[0], "logo_color": res[1], "source": why} if res
             else {"logo": None, "logo_color": None, "source": None})
        e["group"] = bool(UK_LOGO_SOURCES.get(name, {}).get("group"))
        # явный файл владельца (web/img/uk/<slug>.png|svg) — приоритетнее CDN; путь относительно web/
        e["file"] = next((f"{FILES_DIR}/{slug(name)}.{x}" for x in ("svg", "png")
                          if web_dir and (web_dir / FILES_DIR / f"{slug(name)}.{x}").is_file()), None)
        uk[name] = e
    out = {"funds": dict(sorted(funds.items())), "uk": dict(sorted(uk.items())), "bonds_issuer": {}}
    if screener_bonds:
        have = {r["ticker"] for r in bonds}
        known = {r["ticker"]: _rec(r) for r in bonds if _rec(r)}
        by_key: dict[str, list] = defaultdict(list)
        for r in screener_bonds:
            if r["secid"] in known:
                by_key[issuer_key(r["name"])].append(known[r["secid"]])
        for r in screener_bonds:
            if r["secid"] in have:
                continue
            k = issuer_key(r["name"])
            v = by_key.get(k, [])
            if len(k) >= 3 and len(v) >= MIN_ISSUER_BONDS and len(set(v)) == 1:
                out["bonds_issuer"][r["secid"]] = {"logo": v[0][0], "logo_color": v[0][1]}
    return out
