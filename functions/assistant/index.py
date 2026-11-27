"""Cloud Function `assistant`: POST /api/assistant через API Gateway -> GigaChat. Контракт — web/CONTRACT.md."""
import base64
import json
import os
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

import requests

from logic import (FALLBACK_SYSTEM_PROMPT, LABEL, REFUSAL, BadRequest, build_messages,
                   is_advice_request, parse_request, verify_numbers)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
TIMEOUT = (5, 12)  # подключение, чтение: OAuth + чат + повтор укладываются в 30 с функции
MAX_TOKENS = 800


def _load_prompt() -> str:
    try:
        text = (Path(__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8").strip()
        return text or FALLBACK_SYSTEM_PROMPT
    except OSError:
        return FALLBACK_SYSTEM_PROMPT


SYSTEM_PROMPT = _load_prompt()

# Состояние экземпляра функции (живёт между вызовами одного экземпляра).
_token = {"value": None, "expires_at": 0.0}  # expires_at — секунды
_hits = defaultdict(deque)


class Upstream(Exception):
    """GigaChat недоступен или ответил ошибкой; текст — для журнала («chat: 402»), status — HTTP-код GigaChat."""

    def __init__(self, stage: str, reason, status: int = 0):
        super().__init__(f"{stage}: {reason}")
        self.status = status


def _reason(e: Exception):
    """HTTP-код ответа GigaChat, если он есть, иначе имя исключения."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    return (code, code) if code else (type(e).__name__, 0)


def _verify():
    # Относительный путь (как кладёт workflow) — от папки функции, а не от текущего каталога.
    ca = os.environ.get("GIGACHAT_CA_BUNDLE")
    return str(Path(__file__).parent / ca) if ca else True


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


def _get_token() -> str:
    if _token["value"] and time.time() < _token["expires_at"] - 60:
        return _token["value"]
    key = os.environ.get("GIGACHAT_AUTH_KEY")
    if not key:
        raise Upstream("oauth", "GIGACHAT_AUTH_KEY не задан")
    try:
        r = requests.post(
            OAUTH_URL,
            headers={"Authorization": f"Basic {key}", "RqUID": str(uuid.uuid4()),
                     "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data={"scope": os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")},
            timeout=TIMEOUT, verify=_verify(),
        )
        r.raise_for_status()
        data = r.json()
        _token["value"] = data["access_token"]
        _token["expires_at"] = data["expires_at"] / 1000  # GigaChat отдаёт миллисекунды
    except (requests.RequestException, ValueError, KeyError) as e:
        raise Upstream("oauth", *_reason(e)) from None
    return _token["value"]


def _chat(messages: list) -> str:
    payload = {"model": os.environ.get("GIGACHAT_MODEL", "GigaChat"), "messages": messages,
               "max_tokens": MAX_TOKENS, "temperature": 0.2}
    for attempt in range(2):
        token = _get_token()  # Upstream("oauth", …) уходит наверх как есть
        try:
            r = requests.post(CHAT_URL, json=payload, timeout=TIMEOUT, verify=_verify(),
                              headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            if r.status_code == 401 and attempt == 0:  # токен отозван раньше срока — берём новый
                _token["value"] = None
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except (requests.RequestException, ValueError, KeyError, IndexError, AttributeError) as e:
            raise Upstream("chat", *_reason(e)) from None
    raise Upstream("chat", 401, 401)


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
        answer = _chat(build_messages(SYSTEM_PROMPT, question, screen))
    except Upstream as e:
        text = ("Бесплатный лимит GigaChat исчерпан. Попробуйте позже." if e.status == 402
                else "Ассистент временно недоступен: GigaChat не отвечает. Попробуйте позже.")
        # detail — шаг и код/имя ошибки («oauth: 401», «chat: SSLError»), без секретов: нужен, пока нет журналов
        return done(502, {"error": text, "detail": str(e)}, upstream=str(e))

    facts_used, unverified = verify_numbers(answer, screen["facts"])
    return done(200, {"answer": answer, "facts_used": facts_used, "unverified_numbers": unverified, "label": LABEL},
                answer_len=len(answer), unverified=len(unverified))
