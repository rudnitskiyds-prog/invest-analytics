// Логотипы компаний и фондов (web/CONTRACT.md, «Логотипы компаний»): поля logo / logo_color
// из tinvest_shares.json и tinvest_etfs.json (T-Invest API). Нет данных или картинки — кружок с буквами тикера.

import { DATA_BASE } from "./config.js";
import { h } from "./dom.js";

const CDN = "https://invest-brands.cdn-tinkoff.ru/";
const FILES = { share: "tinvest_shares.json", fund: "tinvest_etfs.json", bond: "tinvest_bonds.json" };
const INDEXES = new Set(["IMOEX", "MCFTR", "MOEXBC", "RTSI", "RGBI", "RGBITR", "RUCBITR", "RUCBTRNS", "MEBCTR", "RUONIA", "CORP_CHAIN", "GOLD_CBR", "GLDRUB_TOM", "IRDIVTR", "MEEUTR",
  "RUGBITR1Y", "RUGBITR5+", "RUGBITR10Y", "MOEXOG", "MOEXFN", "MOEXMM", "MOEXCN", "MOEXEU", "MOEXCH", "MOEXTN", "MOEXIT"]);

// Иконки классов без логотипа: облигация — купон «%», металл — слиток, индекс — корзина (бумаги внутри)
const ICONS = {
  index: "M4 10h16l-1.6 9H5.6zM8 10l3-5M16 10l-3-5M10 13.5v2.5M14 13.5v2.5",
  bond: "M8 16 16 8M8.5 9.5a1 1 0 1 0 0-.01M15.5 15.5a1 1 0 1 0 0-.01",
  metal: "M5 16h14l-2.5-6h-9zM9.5 10l1-3h3l1 3",
};

/** Класс индекса из indices.json → слот цвета (--idx-* в indices.css). */
export const IDX_SLUG = { "Акции": "eq", "Облигации гос.": "gov", "Облигации корп.": "corp", "Золото": "gold", "Денежный рынок": "cash" };

const brands = { share: new Map(), fund: new Map(), bond: new Map() };   // SECID → {logo, color, name, brand}
let uk = null;                                                           // uk_logos.json (или null: нет файла / ошибка — запоминается)
const idxClass = new Map();                                              // key → класс индекса
const loads = {};

async function readRows(file) {
  try {
    const r = await fetch(DATA_BASE + file);
    const j = r.ok ? await r.json() : null;
    return j?.data || j?.items || [];
  } catch {
    return [];
  }
}

function loadKind(kind) {
  loads[kind] ||= readRows(FILES[kind]).then((rows) => {
    for (const row of rows) {
      if (!row?.ticker) continue;
      const rec = { logo: row.logo || null, color: row.logo_color || null, name: row.name || null, brand: row.brand || null };
      brands[kind].set(String(row.ticker).toUpperCase(), rec);
      if (kind === "bond" && row.isin) brands[kind].set(String(row.isin).toUpperCase(), rec);   // запасной ключ: ISIN, если SECID не нашёлся
    }
    return brands[kind];
  });
  return loads[kind];
}

// Логотипы УК и эмитентов облигаций (public/data/uk_logos.json): один запрос на сессию
function loadUk() {
  loads.uk ||= fetch(DATA_BASE + "uk_logos.json").then((r) => (r.ok ? r.json() : null)).catch(() => null).then((j) => { uk = j && typeof j === "object" ? j : null; });
  return loads.uk;
}
const ukOf = (key) => uk?.funds?.[key] || null;

/** Загрузить справочник один раз (акции и фонды; облигации — только при bonds=true). Ошибки сети не мешают — останутся кружки с буквами. */
export const loadLogos = (bonds = false) => Promise.all([loadKind("share"), loadKind("fund"), loadUk(), bonds && loadKind("bond")]).then(() => brands);

function loadIndexClasses() {
  loads.index ||= readRows("indices.json").then((rows) => rows.forEach((r) => r?.key && idxClass.set(String(r.key).toUpperCase(), r.class)));
  return loads.index;
}

const find = (key, kind) => {
  const order = kind ? [kind] : ["share", "fund", "bond"];
  for (const k of order) if (brands[k]?.has(key)) return { kind: k, ...brands[k].get(key) };
  return null;
};

/** Название бумаги из справочника T-Invest (после loadLogos) или null. */
export const nameFor = (secid) => find(String(secid || "").toUpperCase())?.name || null;

const hex = (c) => typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c) ? c : null;
const cdnUrl = (logo) => {
  const name = logo ? String(logo).replace(/\.png$/i, "") : "";
  return name ? `${CDN}${encodeURIComponent(name)}x160.png` : null;
};

// Единая монограмма УК: инициалы названия + стабильный цвет из хэша названия
function ukMono(name) {
  const words = String(name).replace(/["«»“”]/g, "").split(/[\s-]+/).filter((w) => w && !/^(УК|ООО|АО)$/i.test(w));
  const ini = (words.length > 1 ? Array.from(words[0])[0] + Array.from(words[1])[0] : Array.from(words[0] || name).slice(0, 2).join("")).toUpperCase();
  let n = 0;
  for (const ch of String(name)) n = (n * 31 + ch.codePointAt(0)) >>> 0;
  const hue = n % 360, s = 0.55, l = 0.42, f = (k) => {
    const a = s * Math.min(l, 1 - l), t = (k + hue / 30) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(t - 3, 9 - t, 1)))).toString(16).padStart(2, "0");
  };
  return { initials: ini, color: `#${f(0)}${f(8)}${f(4)}` };
}

/**
 * {url|null, color|null, initials, title|null} — синхронно, по уже загруженным справочникам.
 * Фонд — логотип (или монограмма) его УК из uk_logos.json, одинаковый у всех фондов УК; акция/облигация — логотип эмитента.
 */
export function logoFor(secid, kind = null) {
  const key = String(secid || "").toUpperCase();
  const ukName = (kind === "fund" || !kind) ? ukOf(key) : null;
  if (ukName) {
    const e = uk.uk?.[ukName] || {};
    const mono = ukMono(ukName);
    const file = typeof e.file === "string" && /^img\/uk\/[a-z0-9-]+\.(svg|png)$/.test(e.file) ? e.file : null;   // из JSON — только локальные логотипы
    return { url: file || cdnUrl(e.logo), color: hex(e.logo_color) || mono.color, initials: mono.initials, title: ukName, mono: mono.color };
  }
  const b = kind === "index" ? null : find(key, kind);
  const iss = kind === "index" || kind === "share" || kind === "fund" ? null : uk?.bonds_issuer?.[key];
  const useIss = !b?.logo && iss?.logo;
  return {
    url: cdnUrl(b?.logo || (useIss && iss.logo)),
    color: hex(b?.logo ? b.color : useIss ? iss.logo_color : b?.color),
    initials: key.replace(/[^A-ZА-Я0-9]/g, "").slice(0, 2) || "?",
    title: b ? (b.kind === "fund" ? b.brand : b.brand || b.name) || null : null,
  };
}

function guessClass(key) {
  if (idxClass.has(key)) return "index";   // данные indices.json важнее ручного списка
  if (INDEXES.has(key)) return "index";
  if (/^(SU\d|RU000)/.test(key)) return "bond";
  if (/^(GOLD_CBR|GLDRUB|SLVRUB|PLT|PLD)/.test(key)) return "metal";
  return null;
}

// Тёмный текст на светлом фирменном цвете, белый — на тёмном
function textOn(hex) {
  // относительная яркость WCAG 2.x; берём цвет букв с большим контрастом
  const lum = (h) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const L = lum(hex);
  return (L + 0.05) / (lum("#0E1726") + 0.05) >= 1.05 / (L + 0.05) ? "#0E1726" : "#FFFFFF";
}

function fill(el, key, cls) {
  const { url, color, initials, title, mono } = logoFor(key, cls === "share" || cls === "fund" || cls === "bond" || cls === "index" ? cls : null);
  const kind = cls || guessClass(key);
  const icon = (kind === "index" || !url) && ICONS[kind];
  if (title) el.title = title;
  else el.removeAttribute("title");
  el.replaceChildren();
  el.removeAttribute("data-initials");
  el.classList.remove("is-icon", "is-basket");
  delete el.dataset.idx;
  el.style.background = "";
  el.style.color = "";
  if (icon) {
    el.classList.add("is-icon");
    if (kind === "index") {
      el.classList.add("is-basket");
      const slug = IDX_SLUG[idxClass.get(key)];
      if (slug) el.dataset.idx = slug;
    }
    el.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icon}"/></svg>`;
    return;
  }
  const letters = () => {
    el.replaceChildren();
    el.dataset.initials = initials;  // буквы — через CSS ::before, чтобы не попадать в текст заголовков
    const c = mono || color;   // у фонда под монограммой — хэш-цвет УК, а не цвет фона логотипа
    if (c) { el.style.background = c; el.style.color = textOn(c); }
  };
  if (!url) return letters();
  const img = h("img", { src: url, alt: "", loading: "lazy", decoding: "async", width: el.dataset.size, height: el.dataset.size });
  img.addEventListener("error", letters, { once: true });
  el.append(img);
}

/** Круглый значок бумаги. size — px; kind — вид бумаги: строка или {kind} (share | fund | bond | index | metal). */
export function assetBadge(secid, size = 24, opts = null) {
  const key = String(secid || "").toUpperCase();
  const cls = (typeof opts === "string" ? opts : opts?.kind) || null;
  const el = h("span", { class: "asset-badge", "aria-hidden": "true", "data-size": size, style: { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.38)}px` } });
  fill(el, key, cls);
  const kind = cls || guessClass(key);
  const typed = cls === "share" || cls === "fund" || cls === "bond";
  const pending = kind === "index" ? loadIndexClasses().then(() => idxClass.has(key))
    : loadLogos(kind === "bond").then(async () => {
      if (!typed && !find(key)) await loadIndexClasses();   // неизвестная бумага: вдруг это индекс из indices.json
      return typed || find(key) || ukOf(key) || idxClass.has(key);
    });
  pending.then((ok) => { if (ok) fill(el, key, cls); });
  return el;
}
