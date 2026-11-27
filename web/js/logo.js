// Логотипы компаний и фондов (web/CONTRACT.md, «Логотипы компаний»): поля logo / logo_color
// из tinvest_shares.json и tinvest_etfs.json (T-Invest API). Нет данных или картинки — кружок с буквами тикера.

import { DATA_BASE } from "./config.js";
import { h } from "./dom.js";

const CDN = "https://invest-brands.cdn-tinkoff.ru/";
const FILES = ["tinvest_shares.json", "tinvest_etfs.json"];
const INDEXES = new Set(["IMOEX", "MCFTR", "MOEXBC", "RTSI", "RGBI", "RGBITR", "RUCBITR", "RUCBTRNS", "MEBCTR", "RUONIA",
  "RUGBITR1Y", "RUGBITR5+", "RUGBITR10Y", "MOEXOG", "MOEXFN", "MOEXMM", "MOEXCN", "MOEXEU", "MOEXCH", "MOEXTN", "MOEXIT"]);

// Иконки классов без логотипа: индекс — столбики, облигация — купон «%», металл — слиток
const ICONS = {
  index: "M6 17V11M10.5 17V7M15 17v-4M19 17V9",
  bond: "M8 16 16 8M8.5 9.5a1 1 0 1 0 0-.01M15.5 15.5a1 1 0 1 0 0-.01",
  metal: "M5 16h14l-2.5-6h-9zM9.5 10l1-3h3l1 3",
};

const brands = new Map();   // SECID → {logo, color}
let loaded = null;

/** Загрузить справочник один раз; ошибки сети не мешают — останутся кружки с буквами. */
export function loadLogos() {
  loaded ||= Promise.all(FILES.map(async (f) => {
    try {
      const r = await fetch(DATA_BASE + f);
      return r.ok ? (await r.json())?.data || [] : [];
    } catch {
      return [];
    }
  })).then((lists) => {
    for (const row of lists.flat()) {
      if (row?.ticker) brands.set(String(row.ticker).toUpperCase(), { logo: row.logo || null, color: row.logo_color || null, name: row.name || null });
    }
    return brands;
  });
  return loaded;
}

/** Название бумаги из справочника T-Invest (после loadLogos) или null. */
export const nameFor = (secid) => brands.get(String(secid || "").toUpperCase())?.name || null;

/** {url|null, color|null, initials} — синхронно, по уже загруженному справочнику. */
export function logoFor(secid) {
  const key = String(secid || "").toUpperCase();
  const b = brands.get(key);
  const name = b?.logo ? String(b.logo).replace(/\.png$/i, "") : "";
  return {
    url: name ? `${CDN}${encodeURIComponent(name)}x160.png` : null,
    color: b?.color && /^#[0-9a-f]{6}$/i.test(b.color) ? b.color : null,
    initials: key.replace(/[^A-ZА-Я0-9]/g, "").slice(0, 2) || "?",
  };
}

function guessClass(key) {
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
  const { url, color, initials } = logoFor(key);
  const icon = !brands.has(key) && ICONS[cls || guessClass(key)];
  el.replaceChildren();
  el.removeAttribute("data-initials");
  el.style.background = "";
  el.style.color = "";
  if (icon) {
    el.classList.add("is-icon");
    el.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icon}"/></svg>`;
    return;
  }
  const letters = () => {
    el.replaceChildren();
    el.dataset.initials = initials;  // буквы — через CSS ::before, чтобы не попадать в текст заголовков
    if (color) { el.style.background = color; el.style.color = textOn(color); }
  };
  if (!url) return letters();
  const img = h("img", { src: url, alt: "", loading: "lazy", decoding: "async", width: el.dataset.size, height: el.dataset.size });
  img.addEventListener("error", letters, { once: true });
  el.append(img);
}

/** Круглый значок бумаги. size — px; cls — класс бумаги, если известен (index | bond | metal | share | fund). */
export function assetBadge(secid, size = 24, cls = null) {
  const key = String(secid || "").toUpperCase();
  const el = h("span", { class: "asset-badge", "aria-hidden": "true", "data-size": size, style: { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.38)}px` } });
  fill(el, key, cls);
  if (!brands.has(key)) loadLogos().then(() => { if (brands.has(key)) fill(el, key, cls); });
  return el;
}
