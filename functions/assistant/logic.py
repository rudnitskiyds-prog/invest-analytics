"""Чистые функции ассистента (без сети): валидация запроса, фильтр рекомендаций, сборка сообщений,
извлечение и сверка чисел ответа с фактами экрана. Тестируются без GigaChat."""
import json
import re

MAX_QUESTION = 1000
MAX_FACTS = 60
MAX_FIELD = 300

LABEL = "Ответ ИИ (GigaChat). Не является индивидуальной инвестиционной рекомендацией."

REFUSAL = (
    "Я не даю индивидуальных инвестиционных рекомендаций: не подсказываю, что купить, продать "
    "или куда вложить деньги. Могу объяснить показатели на этом экране — например, что означает "
    "коэффициент или почему у портфелей разная доходность и просадка."
)

FALLBACK_SYSTEM_PROMPT = (
    "Ты — помощник платформы FinServ. Объясняй показатели на экране пользователя простым языком. "
    "Все числа бери только из переданных фактов экрана и не считай новых. "
    "Не давай индивидуальных инвестиционных рекомендаций (что купить, продать, куда вложить). "
    "Отвечай по-русски, кратко."
)


class BadRequest(ValueError):
    """Ошибка запроса; текст — для пользователя (по-русски)."""


# Вопросы о том, что купить/продать/куда вложить/как распределить деньги — отказ без вызова модели.
# Объяснения («что такое просадка», «какой портфель меньше всего падал») не ловим.
_OBJ = r"(акци|облигац|фонд|бумаг|портфел|etf|бпиф|дол|золот)"
_DO = r"(купить|покупать|продать|продавать|брать|взять|выбрать|вложить|вкладывать|инвестировать|предпочесть|держать)"
_ADVICE = re.compile(
    r"что\s+(мне\s+|лучше\s+|сейчас\s+)*(купить|покупать|продать|продавать|брать)"
    r"|что\s+(мне\s+|лучше\s+|сейчас\s+)*выбрать\W{0,3}\s*" + _OBJ +
    r"|куда\s+(мне\s+|лучше\s+|сейчас\s+)*(вложить|вкладывать|инвестировать|вложиться|деть|положить)"
    r"|стоит\s+ли\s+(мне\s+|сейчас\s+)*(покупать|купить|продавать|продать|вкладывать|вложить|инвестировать|держать)"
    r"|имеет\s+смысл\s+(сейчас\s+)?(покупать|купить|продавать|продать|вкладывать|вложить|инвестировать|держать)"
    r"|(покупать|купить)\s+или\s+(продавать|продать)"
    r"|(покупать|продавать|брать)\s+ли\b"
    r"|^\s*(брать|покупать|купить|продавать|продать)\s+\S+\s*\?"
    r"|как(ой|ая|ую|ое|ие|их|ого|им)\s+(из\s+\w+\s+)?" + _OBJ + r"\w*\b[^?.!]{0,40}?"
    r"(" + _DO + r"|лучш\w*\s*(\?|для\b|мне\b|сейчас\b))"
    r"|посоветуй\w*.{0,40}(акци|облигац|фонд|бумаг|портфел|вложи|купи|прода)"
    r"|сколько\s+(мне\s+)?(вложить|инвестировать|вкладывать)(?!.{0,30}\b(надо|нужно)\s+было)"  # «надо было» — про прошлое
    r"|(во\s+)?что\s+(мне\s+)?(вложить|инвестировать)"
    r"|как\s+(мне\s+|лучше\s+)*(распределить|разложить|вложить|инвестировать)\b.{0,25}(\d|деньг|руб|сбережени|накоплени|капитал)"
    # «переложить деньги» — совет, только если деньги пользователя; «почему при ребалансировке вложить деньги» — нет
    r"|(переложить|вложить|положить|перевести|вкладывать|инвестировать)\b.{0,25}"
    r"\b(все\s+деньги|мои\s+деньги|свои\s+деньги|сбережени|накоплени)"
    r"|\b(мне|нам)\s+(переложить|вложить|положить|перевести|вкладывать|инвестировать)\b",
    re.IGNORECASE,
)


def is_advice_request(question: str) -> bool:
    return bool(_ADVICE.search(question.replace("ё", "е").replace("Ё", "Е")))


def _clip(value, limit=MAX_FIELD):
    return str(value)[:limit] if value is not None else ""


def parse_request(body) -> tuple:
    """body (str | bytes) -> (question, screen) с очищенными фактами; иначе BadRequest."""
    try:
        data = json.loads(body or "")
    except (ValueError, TypeError):
        raise BadRequest("Неверный формат запроса: ожидается JSON.")
    if not isinstance(data, dict):
        raise BadRequest("Неверный формат запроса: ожидается JSON-объект.")
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise BadRequest("Введите вопрос.")
    question = question.strip()
    if len(question) > MAX_QUESTION:
        raise BadRequest(f"Вопрос слишком длинный: не больше {MAX_QUESTION} знаков.")
    screen = data.get("screen") or {}
    if not isinstance(screen, dict):
        raise BadRequest("Неверный формат данных экрана.")
    facts = screen.get("facts") or []
    if not isinstance(facts, list):
        raise BadRequest("Неверный формат фактов экрана.")
    if len(facts) > MAX_FACTS:
        raise BadRequest(f"Слишком много фактов экрана: не больше {MAX_FACTS}.")
    clean = []
    for f in facts:
        if not isinstance(f, dict):
            raise BadRequest("Неверный формат фактов экрана.")
        value = f.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            value = None
        clean.append({
            "id": _clip(f.get("id"), 100),
            "label": _clip(f.get("label")),
            "value": value,
            "unit": _clip(f.get("unit"), 20),
            "text": _clip(f.get("text"), 100),
            "source": _clip(f.get("source")),
        })
    return question, {
        "route": _clip(screen.get("route"), 200),
        "title": _clip(screen.get("title"), 200),
        "facts": clean,
    }


def build_messages(system_prompt: str, question: str, screen: dict) -> list:
    """Системный промпт и одно сообщение пользователя: факты экрана (строка JSON), затем вопрос.
    Два сообщения user подряд GigaChat обрабатывал ~17 с вместо ~1 с — поэтому одно.
    Источники перечисляются один раз (`sources`), у факта — индекс источника: экономия токенов."""
    sources, facts = [], []
    for f in screen["facts"]:
        f = dict(f)
        src = f.pop("source", "")
        if src:
            if src not in sources:
                sources.append(src)
            f["source"] = sources.index(src)
        facts.append(f)
    facts_msg = (
        f"Факты экрана «{screen['title']}» ({screen['route']}). Это единственный источник чисел для ответа; "
        "source у факта — номер в списке sources:\n"
        + json.dumps({"sources": sources, "facts": facts}, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{facts_msg}\n\nВопрос: {question}"},
    ]


# --- числа ---------------------------------------------------------------------------------

_SPACES = "[    ]"
# ponytail: группы по три цифры через пробел склеиваются («3 120» -> 3120); редкий ложный случай.
_NUMBER = re.compile(
    r"(?<![\w.,])(?P<sign>[-−])?"
    r"(?P<num>\d{1,3}(?:" + _SPACES + r"\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?P<unit>" + _SPACES + r"?(?:%|₽|руб\.?))?"
)
# Номера законов и статей — не данные расчёта.
_LEGAL = re.compile(r"\d+-ФЗ|ст\.\s*\d+(?:\.\d+)*", re.IGNORECASE)


_PERIOD = re.compile(r"\s*(лет|год|месяц|недел|дн|день)", re.IGNORECASE)


def extract_numbers(text: str) -> list:
    """-> [(исходная строка, число, знаков после запятой, единица '%'|'₽'|'')]."""
    return [n[:4] for n in _numbers(text)]


def _numbers(text: str) -> list:
    """Как extract_numbers, плюс пятый элемент: за числом идёт срок («5 лет», «52 недели»)."""
    out = []
    text = _LEGAL.sub(" ", text or "")
    for m in _NUMBER.finditer(text):
        raw = re.sub(_SPACES, "", m.group("num")).replace(",", ".")
        value = float(raw)
        if m.group("sign"):
            value = -value
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        unit = (m.group("unit") or "").strip()
        unit = "%" if unit == "%" else ("₽" if unit else "")
        period = not unit and bool(_PERIOD.match(text, m.end()))
        out.append((m.group(0).strip(), value, decimals, unit, period))
    return out


def _is_noise(value: float, decimals: int, unit: str, period: bool = False) -> bool:
    """Годы, сроки («5 лет») и однозначные целые без единиц (номера пунктов, «3 портфеля») не сверяем."""
    if unit or decimals:
        return False
    return period or abs(value) < 10 or 1900 <= value <= 2100


def _candidates(fact: dict) -> list:
    """Только value и text: числа из label/source («52 недели», «2011–2025») не подтверждают ответ."""
    vals = []
    v = fact.get("value")
    if isinstance(v, (int, float)):
        vals.append(float(v))
        if fact.get("unit") == "pct":
            vals.append(float(v) * 100)
    vals.extend(n for _, n, _, _ in extract_numbers(fact.get("text", "")))
    return vals


# «просадка 15 %» при факте −0,15: модуль допустим только рядом со словом падения
_FALL = re.compile(r"просад|пада|упал|снизил|снижени|потер|минус|убыт", re.IGNORECASE)


_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")


def verify_numbers(answer: str, facts: list) -> tuple:
    """-> (facts_used, unverified_numbers). Допуск — половина последнего разряда числа в ответе.
    Предложения с пометкой «условно» (иллюстративные примеры по промпту) не сверяются."""
    cands = [(f.get("id", ""), _candidates(f)) for f in facts]
    used, unverified = [], []
    for sentence in _SENTENCE.split(_LEGAL.sub(" ", answer or "")):  # «ст. 6.1» до разбиения на предложения
        if "условн" in sentence.lower():
            continue
        fall = bool(_FALL.search(sentence))
        for raw, value, decimals, unit, period in _numbers(sentence):
            if _is_noise(value, decimals, unit, period):
                continue
            tol = 0.5 * 10 ** -decimals + 1e-9
            hits = [fid for fid, vals in cands
                    if any(abs(value - c) <= tol or (fall and c < 0 and abs(value + c) <= tol) for c in vals)]
            if hits:
                used.extend(h for h in hits if h and h not in used)
            elif raw not in unverified:
                unverified.append(raw)
    return used, unverified
