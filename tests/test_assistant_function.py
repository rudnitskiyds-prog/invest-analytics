"""Cloud Function `assistant` (functions/assistant/): чистая логика и handler без сети.

functions/assistant — не пакет (в облаке index.py лежит в корне архива и делает `from logic import …`),
поэтому модули грузятся по пути: logic — под своим именем, index — под уникальным. YandexGPT подменяется
через monkeypatch `requests.post`.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

requests = pytest.importorskip("requests", reason="requests не установлен")

FN = Path(__file__).resolve().parent.parent / "functions" / "assistant"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


logic = _load("logic", FN / "logic.py")
index = _load("assistant_index", FN / "index.py")

NBSP, NNBSP = " ", " "
SRC = "расчёт FinServ по ISS MOEX, 2011–2025"
FACTS = [
    {"id": "pf1.cagr", "label": "Лежебока плюс: в среднем в год", "value": 0.123, "unit": "pct",
     "text": f"12,3{NBSP}%", "source": SRC},
    {"id": "pf1.final", "label": "Лежебока плюс: итоговая стоимость", "value": 5618575, "unit": "rub",
     "text": f"5{NBSP}618{NBSP}575{NBSP}₽", "source": SRC},
    {"id": "pf1.max_drawdown", "label": "Лежебока плюс: максимальная просадка", "value": -0.22, "unit": "pct",
     "text": "−22,0 %", "source": SRC},
]


def _body(question="Почему CAGR ниже, чем у индекса?", facts=FACTS, **extra):
    return json.dumps({"question": question,
                       "screen": {"route": "/tools/backtest", "title": "Бэктест портфелей", "facts": facts}, **extra},
                      ensure_ascii=False)


# ============================================================ parse_request
def test_parse_request_valid_cleans_facts():
    facts = FACTS + [{"id": "x" * 500, "label": "L" * 1000, "value": True, "unit": "pct", "text": "t" * 500,
                      "source": None}, {"id": "p", "value": "2011-01-01 — 2025-12-31", "text": "01.01.2011 — 31.12.2025"}]
    q, screen = logic.parse_request(_body("  что такое просадка?  ", facts))
    assert q == "что такое просадка?"
    assert screen["route"] == "/tools/backtest" and screen["title"] == "Бэктест портфелей"
    assert screen["facts"][0] == FACTS[0]
    odd = screen["facts"][3]
    assert len(odd["id"]) == 100 and len(odd["label"]) == logic.MAX_FIELD and len(odd["text"]) == 100
    assert odd["value"] is None, "bool — не число"
    assert odd["source"] == ""
    assert screen["facts"][4]["value"] is None, "строковое value (период) не принимается за число"
    assert set(odd) == {"id", "label", "value", "unit", "text", "source"}


def test_parse_request_bytes_and_no_screen():
    q, screen = logic.parse_request(json.dumps({"question": "Что такое CAGR?"}).encode())
    assert q == "Что такое CAGR?" and screen == {"route": "", "title": "", "facts": []}


@pytest.mark.parametrize("body, needle", [
    ("", "JSON"), (None, "JSON"), ("{не json", "JSON"), ("[1, 2]", "JSON-объект"),
    (json.dumps({"question": "   "}), "Введите вопрос"), (json.dumps({"question": 5}), "Введите вопрос"),
    (json.dumps({"screen": {}}), "Введите вопрос"),
    (json.dumps({"question": "я" * 1001}), "1000"),
    (json.dumps({"question": "?", "screen": [1]}), "экрана"),
    (json.dumps({"question": "?", "screen": {"facts": {"a": 1}}}), "фактов"),
    (json.dumps({"question": "?", "screen": {"facts": ["строка"]}}), "фактов"),
    (_body("?", [FACTS[0]] * 61), "60"),
])
def test_parse_request_errors(body, needle):
    with pytest.raises(logic.BadRequest, match=needle):
        logic.parse_request(body)


def test_parse_request_limits_inclusive():
    logic.parse_request(json.dumps({"question": "я" * 1000}))
    _, screen = logic.parse_request(_body("?", [FACTS[0]] * 60))
    assert len(screen["facts"]) == 60


# ============================================================ фильтр рекомендаций
@pytest.mark.parametrize("q", [
    "Что купить?", "что мне лучше купить сейчас", "Куда вложить миллион?", "куда лучше инвестировать 100 тысяч",
    "Стоит ли продавать Сбер?", "стоит ли мне сейчас покупать золото", "Покупать ли ОФЗ?",
    "Какие акции купить?", "какие фонды лучше брать",
    "Посоветуй облигации", "Сколько вложить в акции?", "Во что вложить деньги?", "Что ПРОДАТЬ из портфеля?",
    "Стоит ли держать LQDT?",
])
def test_advice_detected(q):
    assert logic.is_advice_request(q), q


@pytest.mark.parametrize("q", [
    "Что такое просадка?", "Почему CAGR ниже, чем у индекса?", "Что значит коэффициент Шарпа?",
    "Чем касательный портфель отличается от портфеля минимальной дисперсии?",
    "Почему у портфелей разная итоговая сумма?", "Что значит доходность с учётом пополнений?",
    "Как считается волатильность?", "Что показывает бета?",
])
def test_advice_not_detected(q):
    assert not logic.is_advice_request(q), q


# ============================================================ числа
def test_extract_numbers_formats():
    assert logic.extract_numbers("12 %") == [("12 %", 12.0, 0, "%")]
    assert logic.extract_numbers("0,41 и 12.3%") == [("0,41", 0.41, 2, ""), ("12.3%", 12.3, 1, "%")]
    for sp in (" ", NBSP, NNBSP):
        raw = f"5{sp}618{sp}575{sp}₽"
        assert logic.extract_numbers(raw) == [(raw, 5618575.0, 0, "₽")], repr(raw)
    assert logic.extract_numbers("−22 %")[0][1] == -22.0
    assert logic.extract_numbers("100 руб.")[0][3] == "₽"
    assert logic.extract_numbers("pf1.cagr") == [], "цифра внутри идентификатора — не число"
    assert logic.extract_numbers("по 39-ФЗ, ст. 6.1") == [], "номера закона и статьи вырезаются"
    assert logic.extract_numbers("") == [] and logic.extract_numbers(None) == []


@pytest.mark.parametrize("answer, used", [
    ("Портфель рос в среднем на 12 % в год.", ["pf1.cagr"]),        # 12 % ≈ 0.123 pct (допуск округления)
    ("CAGR — 12,3 %.", ["pf1.cagr"]),
    ("Итог — 5 618 575 ₽.", ["pf1.final"]),
    (f"Итог — 5{NBSP}618{NBSP}575{NBSP}₽.", ["pf1.final"]),
    (f"Итог — 5{NNBSP}618{NNBSP}575 ₽.", ["pf1.final"]),
    ("Просадка −22 %.", ["pf1.max_drawdown"]),
])
def test_verify_numbers_found(answer, used):
    assert logic.verify_numbers(answer, FACTS) == (used, [])


def test_verify_numbers_invented_and_noise():
    used, unv = logic.verify_numbers("Было 12,3 %, а будет 15 % и 15 %.", FACTS)
    assert used == ["pf1.cagr"] and unv == ["15 %"], "выдуманное число — один раз в unverified"
    # 12,4 вне допуска ±0,05 от 12,3
    assert logic.verify_numbers("12,4 %", FACTS) == ([], ["12,4 %"])
    # годы, однозначные числа и ссылки на закон не сверяются
    assert logic.verify_numbers("С 2011 по 2025 год, 3 портфеля; 39-ФЗ, ст. 6.1.", FACTS) == ([], [])
    assert logic.verify_numbers("Период 2011-2025.", FACTS) == ([], [])


def test_verify_numbers_from_text_and_source():
    facts = [{"id": "a", "value": None, "unit": "", "text": f"1{NBSP}000{NBSP}000{NBSP}₽", "label": "", "source": ""}]
    assert logic.verify_numbers("вложено 1 000 000 ₽", facts) == (["a"], [])


# ============================================================ build_messages
def test_build_messages_single_user_message():
    """Одно сообщение user: system + один user (факты и вопрос)."""
    _, screen = logic.parse_request(_body("Что такое просадка?"))
    msgs = logic.build_messages("SYS", "Что такое просадка?", screen)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["text"] == "SYS"
    user = msgs[1]["text"]
    assert user.endswith("\n\nВопрос: Что такое просадка?")
    assert "Бэктест портфелей" in user and "/tools/backtest" in user
    payload = json.loads(user.split("\n")[1])
    assert payload["sources"] == [SRC], "источник перечислен один раз"
    assert payload["facts"] == [{**f, "source": 0} for f in screen["facts"]]
    assert screen["facts"][0]["source"] == SRC, "build_messages не меняет исходные факты"


def test_build_messages_sources_indexed():
    facts = [{"id": "a", "source": "S1"}, {"id": "b", "source": ""}, {"id": "c", "source": "S2"}, {"id": "d", "source": "S1"}]
    msgs = logic.build_messages("SYS", "?", {"route": "/r", "title": "T", "facts": facts})
    payload = json.loads(msgs[1]["text"].split("\n")[1])
    assert payload == {"sources": ["S1", "S2"],
                       "facts": [{"id": "a", "source": 0}, {"id": "b"}, {"id": "c", "source": 1}, {"id": "d", "source": 0}]}


def test_system_prompt_from_file():
    text = (FN / "prompts" / "system.md").read_text(encoding="utf-8").strip()
    assert text and index.SYSTEM_PROMPT == text
    assert index.SYSTEM_PROMPT != logic.FALLBACK_SYSTEM_PROMPT


# ============================================================ handler
class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)  # как в requests


def _ok(text="Ответ.", status="ALTERNATIVE_STATUS_FINAL"):
    return FakeResp(200, {"result": {"alternatives": [{"message": {"role": "assistant", "text": text},
                                                       "status": status}]}})


class FakeContext:
    def __init__(self, token="iam1"):
        self.token = {"access_token": token, "expires_in": 43200, "token_type": "Bearer"} if token else None


class YGPT:
    """Подмена requests.post для YandexGPT; ответы — по очереди из responses, иначе обычный ответ."""

    def __init__(self, answer="Ответ."):
        self.calls = []
        self.responses = []
        self.answer = answer

    def __call__(self, url, **kw):
        assert url == index.CHAT_URL, url
        self.calls.append(kw)
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return _ok(f"  {self.answer}  ")


@pytest.fixture
def ygpt(monkeypatch):
    monkeypatch.setenv("YC_FOLDER_ID", "b1gfolder")
    monkeypatch.setenv("YANDEX_MODEL", "yandexgpt-lite")
    monkeypatch.delenv("RATE_LIMIT_PER_MIN", raising=False)
    index._hits.clear()
    g = YGPT()
    monkeypatch.setattr(index.requests, "post", g)
    yield g
    index._hits.clear()


def _event(body=None, ip="10.0.0.1", b64=False):
    body = _body() if body is None else body
    if b64:
        body = base64.b64encode(body.encode()).decode()
    return {"httpMethod": "POST", "body": body, "isBase64Encoded": b64,
            "headers": {"Content-Type": "application/json"},
            "requestContext": {"identity": {"sourceIp": ip}}}


def _call(event, context="default"):
    r = index.handler(event, FakeContext() if context == "default" else context)
    return r["statusCode"], json.loads(r["body"]), r


def test_handler_ok(ygpt):
    ygpt.answer = "CAGR портфеля 12,3 %, итог 5 618 575 ₽, а в будущем — 15 %."
    status, data, r = _call(_event())
    assert status == 200
    assert r["headers"]["Content-Type"].startswith("application/json")
    assert data["answer"] == ygpt.answer, "ответ модели обрезан от пробелов"
    assert data["label"] == logic.LABEL and "Ответ ИИ (YandexGPT)" in data["label"]
    assert data["facts_used"] == ["pf1.cagr", "pf1.final"]
    assert data["unverified_numbers"] == ["15 %"]
    assert len(ygpt.calls) == 1
    chat = ygpt.calls[0]
    assert chat["json"]["modelUri"] == "gpt://b1gfolder/yandexgpt-lite/latest"
    assert chat["json"]["completionOptions"] == {"stream": False, "temperature": 0.2, "maxTokens": "500"}
    msgs = chat["json"]["messages"]
    assert msgs[0] == {"role": "system", "text": index.SYSTEM_PROMPT}
    assert len(msgs) == 2 and msgs[-1]["role"] == "user"
    assert msgs[-1]["text"].endswith("Вопрос: Почему CAGR ниже, чем у индекса?")
    assert chat["headers"]["Authorization"] == "Bearer iam1"
    assert chat["headers"]["x-folder-id"] == "b1gfolder"
    assert chat["timeout"] == index.CHAT_TIMEOUT


def test_handler_default_model(ygpt, monkeypatch):
    monkeypatch.delenv("YANDEX_MODEL")
    assert _call(_event())[0] == 200
    assert ygpt.calls[0]["json"]["modelUri"] == "gpt://b1gfolder/yandexgpt/latest"


def test_handler_base64_body(ygpt):
    status, data, _ = _call(_event(b64=True))
    assert status == 200 and data["answer"] == "Ответ."


def test_handler_refuses_advice_without_model(ygpt):
    status, data, _ = _call(_event(_body("Стоит ли продавать Сбер?")))
    assert status == 200
    assert data["answer"] == logic.REFUSAL and data["label"] == logic.LABEL
    assert data["facts_used"] == [] and data["unverified_numbers"] == []
    assert ygpt.calls == [], "на запрос рекомендации YandexGPT не вызывается"


@pytest.mark.parametrize("body, b64", [
    ("{битый", False), (_body(""), False), (_body("я" * 1001), False), (_body("?", [FACTS[0]] * 61), False),
    ("%%%не-base64%%%", True),
])
def test_handler_400(ygpt, body, b64):
    ev = _event(body)
    if b64:
        ev["isBase64Encoded"] = True
    status, data, _ = _call(ev)
    assert status == 400 and data["error"]
    assert ygpt.calls == []


def test_handler_400_bad_utf8_in_base64(ygpt):
    ev = _event(base64.b64encode(b"\xff\xfe{").decode())
    ev["isBase64Encoded"] = True
    status, data, _ = _call(ev)
    assert status == 400 and "JSON" in data["error"]


def test_handler_429(ygpt, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    assert _call(_event(ip="1.1.1.1"))[0] == 200
    assert _call(_event(ip="1.1.1.1"))[0] == 200
    status, data, _ = _call(_event(ip="1.1.1.1"))
    assert status == 429 and "минуту" in data["error"]
    assert _call(_event(ip="2.2.2.2"))[0] == 200, "лимит — на IP"
    # через минуту окно освобождается
    for k in list(index._hits["1.1.1.1"]):
        index._hits["1.1.1.1"].remove(k)
        index._hits["1.1.1.1"].append(k - 61)
    assert _call(_event(ip="1.1.1.1"))[0] == 200


@pytest.mark.parametrize("chat", [
    requests.ConnectionError("down"), FakeResp(500, {}), FakeResp(200, ValueError("not json")),
    FakeResp(200, {"result": {"alternatives": []}}), FakeResp(200, {"error": "x"}),
    FakeResp(200, {"result": {"alternatives": [{"status": "ALTERNATIVE_STATUS_FINAL"}]}}),
])
def test_handler_502_chat(ygpt, chat):
    ygpt.responses = [chat]
    status, data, _ = _call(_event())
    assert status == 502 and "недоступен" in data["error"]
    assert data["detail"].startswith("chat: ")


def test_handler_502_timeout_detail(ygpt):
    ygpt.responses = [requests.ReadTimeout()]
    status, data, _ = _call(_event())
    assert status == 502 and data["detail"] == "chat: ReadTimeout"


def test_handler_502_no_context(ygpt):
    status, data, _ = _call(_event(), context=None)
    assert status == 502 and "Нет доступа к YandexGPT" in data["error"]
    assert data["detail"].startswith("auth")
    assert ygpt.calls == []


def test_handler_502_context_without_token(ygpt):
    status, data, _ = _call(_event(), context=FakeContext(token=None))
    assert status == 502 and data["detail"].startswith("auth")
    assert ygpt.calls == []


def test_handler_502_no_folder(ygpt, monkeypatch):
    monkeypatch.delenv("YC_FOLDER_ID")
    status, data, _ = _call(_event())
    assert status == 502 and "Нет доступа к YandexGPT" in data["error"] and data["detail"].startswith("auth")
    assert ygpt.calls == []


@pytest.mark.parametrize("code", [401, 403])
def test_handler_502_no_access(ygpt, code):
    ygpt.responses = [FakeResp(code, {"error": "denied"})]
    status, data, _ = _call(_event())
    assert status == 502 and "Нет доступа" in data["error"]
    assert data["detail"] == f"chat: {code}"
    assert len(ygpt.calls) == 1, "без повторов"


def test_handler_502_rate_limit(ygpt, capsys):
    ygpt.responses = [FakeResp(429, {})]
    status, data, _ = _call(_event())
    assert status == 502 and "Лимит запросов" in data["error"] and data["detail"] == "chat: 429"
    log = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert log["upstream"] == "chat: 429" and log["status"] == 502


def test_handler_content_filter_is_refusal(ygpt, capsys):
    ygpt.responses = [_ok("Что-то про числа 99 %", status="ALTERNATIVE_STATUS_CONTENT_FILTER")]
    status, data, _ = _call(_event())
    assert status == 200
    assert data["answer"] == logic.REFUSAL and data["label"] == logic.LABEL
    assert data["facts_used"] == [] and data["unverified_numbers"] == []
    log = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert log["status"] == 200 and log["refused"] is True


def test_token_not_in_response_or_log(ygpt, capsys):
    ygpt.responses = [FakeResp(500, {}), _ok()]
    ctx = FakeContext("SECRET-IAM-TOKEN")
    for _ in range(2):
        _, _, r = _call(_event(), context=ctx)
        assert "SECRET-IAM-TOKEN" not in r["body"]
    out = capsys.readouterr().out
    assert "SECRET-IAM-TOKEN" not in out and "b1gfolder" not in out


def test_log_without_question_text(ygpt, capsys):
    secret_q = "Почему у Ивана Петровича просадка уникальнаястрока?"
    _call(_event(_body(secret_q), ip="203.0.113.7"))
    _call(_event(_body("Куда вложить уникальнаястрока2?"), ip="203.0.113.7"))
    _call(_event("{битый", ip="203.0.113.7"))
    out = capsys.readouterr().out
    lines = [json.loads(x) for x in out.strip().splitlines()]
    assert len(lines) == 3
    assert "уникальнаястрока" not in out and "Ивана" not in out
    assert "203.0.113.7" not in out
    assert "Ответ." not in out and "iam1" not in out
    assert lines[0]["status"] == 200 and lines[0]["route"] == "/tools/backtest"
    assert lines[0]["question_len"] == len(secret_q) and lines[0]["facts"] == 3
    assert lines[1].get("refused") is True
    assert lines[2]["status"] == 400


# ============================================================ набор оценки tests/ai_eval/cases.jsonl
def test_eval_cases_valid_for_function():
    path = Path(__file__).resolve().parent / "ai_eval" / "cases.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(cases) >= 50
    assert len({c["id"] for c in cases}) == len(cases), "id случаев уникальны"
    for c in cases:
        q, screen = logic.parse_request(json.dumps({"question": c["question"], "screen": c["screen"]},
                                                   ensure_ascii=False))
        assert screen["route"].startswith("/"), c["id"]
    refuse = [c for c in cases if c["expect"]["must_refuse"]]
    assert refuse, "есть случаи с обязательным отказом"
    # ни один «обычный» вопрос не режется серверным фильтром
    blocked = [c["id"] for c in cases if not c["expect"]["must_refuse"] and logic.is_advice_request(c["question"])]
    assert not blocked, blocked
    # все просьбы о рекомендации ловит сервер; прогнозы, незаконное и ПДн — отказ модели по промпту
    by_id = {c["id"]: c for c in cases}
    for cid in ("ban-01", "ban-02", "ban-03", "ban-06", "ban-07", "ban-08", "ban-13"):
        assert logic.is_advice_request(by_id[cid]["question"]), cid


# ============================================================ второй круг: правки по ревью
CRITIC_ADVICE = [
    "какой фонд выбрать", "какую акцию купить", "какой портфель лучше выбрать", "в какой фонд вложить",
    "покупать или продавать сбер", "имеет смысл покупать сбер", "брать сбер?", "какую долю золота взять",
    "как распределить 100 000 рублей", "Покупать или продавать TMOS на этой неделе?",
    "Какие доли акций и облигаций мне выбрать в 35 лет?", "переложить в неё все деньги?",
    "Какой фонд лучший для пенсии?",
]
CRITIC_NOT_ADVICE = [
    "что такое просадка", "почему CAGR ниже", "какой портфель меньше всего падал",
    "что выбрать для расчёта: месяц или день?", "какой портфель показал лучшую доходность",
    "какая доля золота в 40/40/20",
]


@pytest.mark.parametrize("q", CRITIC_ADVICE)
def test_advice_detected_round2(q):
    assert logic.is_advice_request(q), q


@pytest.mark.parametrize("q", CRITIC_NOT_ADVICE)
def test_advice_not_detected_round2(q):
    assert not logic.is_advice_request(q), q


def test_verify_numbers_round2():
    dd = [{"id": "dd", "label": "Просадка", "value": -0.352, "unit": "pct", "text": "−35,2 %", "source": ""}]
    assert logic.verify_numbers("Портфель падал на 35 %.", dd) == (["dd"], []), "модуль отрицательного факта"
    assert logic.verify_numbers("За 15 лет портфель вырос.", dd) == ([], []), "срок не сверяется"
    assert logic.verify_numbers("За 52 недели и 3 месяца.", dd) == ([], [])
    assert logic.verify_numbers("Условно: 100 000 ₽ превратятся в 120 000 ₽. Просадка 35,2 %.", dd) == (["dd"], [])
    lo = [{"id": "lo", "label": "Цена: минимум за 52 недели", "value": 250.5, "unit": "rub", "text": "250,5 ₽",
           "source": "ISS MOEX, 2011–2025"}]
    assert logic.verify_numbers("Бумага упала на 52 %.", lo) == ([], ["52 %"]), "числа из label не подтверждают"
    assert logic.verify_numbers("Минимум 250,5 ₽.", lo) == (["lo"], [])


def test_verify_numbers_names_and_parameters_from_labels_are_not_flagged():
    """Числа из названий и параметров на экране («60/40», «10 000 ₽/мес») — не выдумка; единица должна совпасть."""
    facts = [
        {"id": "pf1.final", "label": "Портфель 60/40: итоговая стоимость", "value": 6012400.0, "unit": "rub",
         "text": "6 012 400 ₽", "source": ""},
        {"id": "pf1.invested", "label": "Портфель 60/40: внесено (1 000 000 ₽ сразу и 10 000 ₽/мес)",
         "value": 2800000.0, "unit": "rub", "text": "2 800 000 ₽", "source": ""},
    ]
    ans = "Для портфеля 60/40 при взносах 1 000 000 ₽ сразу и 10 000 ₽ в месяц итог — 6 012 400 ₽."
    assert logic.verify_numbers(ans, facts) == (["pf1.final"], [])
    # та же цифра с другой единицей или другой величиной подтверждения не получает
    assert logic.verify_numbers("Доходность 60 %.", facts)[1] == ["60 %"]
    assert logic.verify_numbers("Портфель вырос на 1 000 000 ₽ за год.", facts)[1] == []   # сумма из подписи
    assert logic.verify_numbers("Портфель вырос на 2 000 000 ₽.", facts)[1] == ["2 000 000 ₽"]
    # NBSP в подписи и в ответе — тот же текст
    nb = [{"id": "x", "label": "Внесено 1\u00a0000\u00a0000 ₽", "value": 1.0, "unit": "rub", "text": "1 ₽", "source": ""}]
    assert logic.verify_numbers("Внесли 1 000 000 ₽.", nb)[1] == []


def test_verify_numbers_ignores_numbers_from_the_question():
    """Модель повторяет число из вопроса («потеряю 220 000 ₽?») — это число пользователя, а не выдумка."""
    facts = [{"id": "dd", "label": "Просадка", "value": -0.22, "unit": "pct", "text": "−22 %", "source": ""}]
    ans = "Просадка 22 % не означает потерю 220 000 ₽: всё зависит от суммы."
    assert logic.verify_numbers(ans, facts, "Просадка 22 % — значит я потеряю 220 000 ₽?") == (["dd"], [])
    # без вопроса (или другое число) — по-прежнему непроверенное
    assert logic.verify_numbers(ans, facts)[1] == ["220 000 ₽"]
    assert logic.verify_numbers("Это даст 330 000 ₽.", facts, "Просадка 22 % — значит я потеряю 220 000 ₽?")[1] == ["330 000 ₽"]


def test_timeouts_leave_headroom_in_function_limit():
    assert sum(index.CHAT_TIMEOUT) <= 25   # таймаут функции 30 с: запас на запуск, разбор и ответ


def test_handler_502_token_not_a_dict(ygpt):
    """context.token — не dict (объект/строка): аккуратный 502, а не необработанный AttributeError."""
    class Ctx:
        token = "строка, а не словарь"
    status, data, _ = _call(_event(), context=Ctx())
    assert status == 502 and data["detail"].startswith("auth")
    assert ygpt.calls == []


@pytest.mark.parametrize("status_text", ["ALTERNATIVE_STATUS_FINAL", "ALTERNATIVE_STATUS_TRUNCATED_FINAL"])
def test_handler_502_empty_answer_text(ygpt, status_text):
    ygpt.responses = [_ok(text="   ", status=status_text)]
    status, data, _ = _call(_event())
    assert status == 502 and data["detail"] == "chat: empty"


def test_handler_415(ygpt):
    for headers in ({}, {"Content-Type": "text/plain"}, {"content-type": "application/x-www-form-urlencoded"}):
        ev = _event()
        ev["headers"] = headers
        status, data, _ = _call(ev)
        assert status == 415 and "application/json" in data["error"], headers
    ev = _event()
    del ev["headers"]
    assert _call(ev)[0] == 415
    ev = _event()
    ev["headers"] = {"content-type": "Application/JSON; charset=utf-8"}
    assert _call(ev)[0] == 200, "регистр имени и значения заголовка не важен"
    assert len(ygpt.calls) == 1


# ============================================================ третий круг: ребалансировка и модуль отрицательных фактов
@pytest.mark.parametrize("q", [
    "Что значит переложить деньги при ребалансировке?",
    "Почему при ребалансировке нужно продать часть акций и вложить деньги в облигации?",
    "Как пополнения вкладывать деньги каждый месяц влияют на XIRR?",
    "Сколько инвестировать надо было в 2015, чтобы получить итог?",
])
def test_advice_not_detected_round3(q):
    assert not logic.is_advice_request(q), q


@pytest.mark.parametrize("q", ["Какой портфель лучше?", "Сколько мне вложить?", "Куда вложить сбережения?"])
def test_advice_detected_round3(q):
    assert logic.is_advice_request(q), q


DD22 = [{"id": "dd", "label": "Максимальная просадка", "value": -0.22, "unit": "pct", "text": "−22,0 %", "source": ""}]


@pytest.mark.parametrize("answer, expected", [
    ("Портфель вырос на 22 %.", ([], ["22 %"])),          # рост при факте просадки −22 % — не подтверждение
    ("Просадка составила 22 %.", (["dd"], [])),           # модуль допустим рядом со словом «просадка»
    ("Итог −22 %.", (["dd"], [])),
])
def test_verify_numbers_negative_fact_round3(answer, expected):
    assert logic.verify_numbers(answer, DD22) == expected
