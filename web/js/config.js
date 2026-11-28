// Пути к данным. При выкладке подменяются (см. web/CONTRACT.md).
export const DATA_BASE = "../public/data/";
const onGateway = typeof location !== "undefined" && /\.apigw\.yandexcloud\.net$/.test(location.hostname || "");
export const ISS_BASE = globalThis.FINSERV_ISS_BASE ?? (onGateway ? "/iss" : "https://iss.moex.com/iss");
export const CBR_BASE = globalThis.FINSERV_CBR_BASE ?? (onGateway ? "/cbr" : "https://www.cbr.ru");

// Демо-режим (?demo=1): месячные фикстуры из репозитория, только при локальном запуске.
export const DEMO_BASE = "../tests/fixtures/";

// Ассистент YandexGPT: Cloud Function за API Gateway на том же домене (web/CONTRACT.md).
export const ASSISTANT_URL = "/api/assistant";
