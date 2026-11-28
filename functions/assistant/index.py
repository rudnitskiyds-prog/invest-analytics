"""Cloud Function `assistant`: POST /api/assistant через API Gateway -> YandexGPT. Контракт — web/CONTRACT.md."""
import base64
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import requests

from logic import (FALLBACK_SYSTEM_PROMPT, LABEL, REFUSAL, BadRequest, build_messages,
                   is_advice_request, parse_request, verify_numbers)

CHAT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
CHAT_TIMEOUT = (3, 20)  # подключение, чтение; таймаут функции — 30 с, лимит шлюза ~35 с: нужен запас на запуск и разбор
MAX_TOKENS = 500


def _load_prompt() -> str:
    try:
        text = (Path(__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8").strip()
        return text or FALLBACK_SYSTEM_PROMPT
    except OSError:
        return FALLBACK_SYSTEM_PROMPT


SYSTEM_PROMPT = _load_prompt()

# Состояние экземпляра функции (живёт между вызовами одного экземпляра).
_hits = defaultdict(deque)


class Upstream(Exception):
    """YandexGPT недоступен или ответил ошибкой; текст — для журнала («chat: 429»), status — HTTP-код ответа."""

    def __init__(self, stage: str, reason, status: int = 0):
        super().__init__(f"{stage}: {reason}")
        self.status = status


def _reason(e: Exception):
    """HTTP-код ответа YandexGPT, если он есть, иначе имя исключения."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    return (code, code) if code else (type(e).__name__, 0)


def _response(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


def _rate_limited(ip: str, now: float) -> bool:
    limit = int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
    q = _hits[ip]
    while q and now - q[0] >= 60:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    if len(_hits) > 10000:  # не копим память на долгоживущем экземпляре
        for k in [k for k, v in _hits.items() if not v or now - v[-1] >= 60]:
            del _hits[k]
    return False


def _chat(messages: list, context):
    """-> текст ответа или None, если сработал фильтр содержимого."""
    tok = getattr(context, "token", None)
    token = tok.get("access_token") if isinstance(tok, dict) else None
    folder = os.environ.get("YC_FOLDER_ID")
    if not token:
        raise Upstream("auth", "нет IAM-токена в context (сервисный аккаунт функции)", 403)
    if not folder:
        raise Upstream("auth", "YC_FOLDER_ID не задан", 403)
    payload = {"modelUri": f"gpt://{folder}/{os.environ.get('YANDEX_MODEL', 'yandexgpt')}/latest",
               "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": str(MAX_TOKENS)},
               "messages": messages}
    try:
        r = requests.post(CHAT_URL, json=payload, timeout=CHAT_TIMEOUT,
                          headers={"Authorization": f"Bearer {token}", "x-folder-id": folder})
        r.raise_for_status()
        alt = r.json()["result"]["alternatives"][0]
        if "CONTENT_FILTER" in str(alt.get("status", "")):
            return None
        text = alt["message"]["text"].strip()
        if not text:  # пустой ответ (например, обрезан без текста) — не отдаём пользователю как успех
            raise Upstream("chat", "empty")
        return text
    except (requests.RequestException, ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
        raise Upstream("chat", *_reason(e)) from None


def _log(**fields):
    # Только маршрут, длины, код и время — без текста вопроса, IP и ответа.
    print(json.dumps(fields, ensure_ascii=False))


def handler(event, context):
    started = time.time()
    route, q_len, n_facts = "", 0, 0

    def done(status, payload, **extra):
        _log(route=route, question_len=q_len, facts=n_facts, status=status,
             ms=int((time.time() - started) * 1000), **extra)
        return _response(status, payload)

    ip = ((event.get("requestContext") or {}).get("identity") or {}).get("sourceIp") or "unknown"
    if _rate_limited(ip, started):
        return done(429, {"error": "Слишком много вопросов подряд. Подождите минуту и попробуйте снова."})

    ctype = next((v for k, v in (event.get("headers") or {}).items() if k.lower() == "content-type"), "")
    if not str(ctype).lower().startswith("application/json"):
        # заодно защита от отправки формы с чужого сайта (браузер приложил бы сохранённый логин)
        return done(415, {"error": "Неверный формат запроса: нужен Content-Type: application/json."})

    body = event.get("body") or ""
    try:
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        question, screen = parse_request(body)
    except BadRequest as e:
        return done(400, {"error": str(e)})
    except (ValueError, UnicodeDecodeError):
        return done(400, {"error": "Неверный формат запроса: ожидается JSON."})

    route, q_len, n_facts = screen["route"], len(question), len(screen["facts"])
    if is_advice_request(question):
        return done(200, {"answer": REFUSAL, "facts_used": [], "unverified_numbers": [], "label": LABEL},
                    refused=True)

    try:
        answer = _chat(build_messages(SYSTEM_PROMPT, question, screen), context)
    except Upstream as e:
        text = ("Нет доступа к YandexGPT (роль сервисного аккаунта)." if e.status in (401, 403)
                else "Лимит запросов YandexGPT, попробуйте позже." if e.status == 429
                else "Ассистент временно недоступен: YandexGPT не отвечает. Попробуйте позже.")
        # detail — шаг и код/имя ошибки («auth: …», «chat: 429», «chat: ReadTimeout»), без секретов
        return done(502, {"error": text, "detail": str(e)}, upstream=str(e))
    if answer is None:  # CONTENT_FILTER
        return done(200, {"answer": REFUSAL, "facts_used": [], "unverified_numbers": [], "label": LABEL},
                    refused=True, content_filter=True)

    facts_used, unverified = verify_numbers(answer, screen["facts"], question)
    return done(200, {"answer": answer, "facts_used": facts_used, "unverified_numbers": unverified, "label": LABEL},
                answer_len=len(answer), unverified=len(unverified))
