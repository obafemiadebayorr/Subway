/* Shared helpers: DOM building, API calls, formatting, and the store. */
const TOKEN = document.querySelector('meta[name="subway-token"]').content;

/* Everything goes through text nodes, so agent-written text can never become markup. */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  applyAttrs(el, attrs);
  append(el, kids);
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  applyAttrs(el, attrs);
  append(el, kids);
  return el;
}

function applyAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "class") el.setAttribute("class", v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
}

function append(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
}

const ICONS = {
  sidebar: "M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40ZM40,56H80V200H40ZM216,200H96V56H216V200Z",
  overview: "M207.06,72.67A111.24,111.24,0,0,0,128,40h-.4C66.07,40.21,16,91,16,153.13V176a16,16,0,0,0,16,16H224a16,16,0,0,0,16-16V152A111.25,111.25,0,0,0,207.06,72.67ZM224,176H119.71l54.76-75.3a8,8,0,0,0-12.94-9.42L99.92,176H32V153.13c0-3.08.15-6.12.43-9.13H56a8,8,0,0,0,0-16H35.27c10.32-38.86,44-68.24,84.73-71.66V80a8,8,0,0,0,16,0V56.33A96.14,96.14,0,0,1,221,128H200a8,8,0,0,0,0,16h23.67c.21,2.65.33,5.31.33,8Z",
  threads: "M128,24A104,104,0,0,0,36.18,176.88L24.83,210.93a16,16,0,0,0,20.24,20.24l34.05-11.35A104,104,0,1,0,128,24Zm0,192a87.87,87.87,0,0,1-44.06-11.81,8,8,0,0,0-6.54-.67L40,216,52.47,178.6a8,8,0,0,0-.66-6.54A88,88,0,1,1,128,216Z",
  terminals: "M128,128a8,8,0,0,1-3,6.25l-40,32a8,8,0,1,1-10-12.5L107.19,128,75,102.25a8,8,0,1,1,10-12.5l40,32A8,8,0,0,1,128,128Zm48,24H136a8,8,0,0,0,0,16h40a8,8,0,0,0,0-16Zm56-96V200a16,16,0,0,1-16,16H40a16,16,0,0,1-16-16V56A16,16,0,0,1,40,40H216A16,16,0,0,1,232,56ZM216,200V56H40V200H216Z",
  usage: "M128,24a8,8,0,0,0-8,8V88a8,8,0,0,0,8,8,32,32,0,1,1-27.72,16,8,8,0,0,0-2.93-10.93l-48.5-28A8,8,0,0,0,37.92,76,104,104,0,1,0,128,24ZM48.09,91.1,83,111.26A48.09,48.09,0,0,0,80,128c0,1.53.08,3,.22,4.52L41.28,143A88.16,88.16,0,0,1,48.09,91.1Zm-2.67,67.31,39-10.44A48.1,48.1,0,0,0,120,175.32v40.31A88.2,88.2,0,0,1,45.42,158.41ZM136,215.63V175.32a48,48,0,0,0,0-94.65V40.36a88,88,0,0,1,0,175.27Z",
  workers: "M244.8,150.4a8,8,0,0,1-11.2-1.6A51.6,51.6,0,0,0,192,128a8,8,0,0,1-7.37-4.89,8,8,0,0,1,0-6.22A8,8,0,0,1,192,112a24,24,0,1,0-23.24-30,8,8,0,1,1-15.5-4A40,40,0,1,1,219,117.51a67.94,67.94,0,0,1,27.43,21.68A8,8,0,0,1,244.8,150.4ZM190.92,212a8,8,0,1,1-13.84,8,57,57,0,0,0-98.16,0,8,8,0,1,1-13.84-8,72.06,72.06,0,0,1,33.74-29.92,48,48,0,1,1,58.36,0A72.06,72.06,0,0,1,190.92,212ZM128,176a32,32,0,1,0-32-32A32,32,0,0,0,128,176ZM72,120a8,8,0,0,0-8-8A24,24,0,1,1,87.24,82a8,8,0,1,0,15.5-4A40,40,0,1,0,37,117.51,67.94,67.94,0,0,0,9.6,139.19a8,8,0,1,0,12.8,9.61A51.6,51.6,0,0,1,64,128,8,8,0,0,0,72,120Z",
  models: "M230.91,172A8,8,0,0,1,228,182.91l-96,56a8,8,0,0,1-8.06,0l-96-56A8,8,0,0,1,36,169.09l92,53.65,92-53.65A8,8,0,0,1,230.91,172ZM220,121.09l-92,53.65L36,121.09A8,8,0,0,0,28,134.91l96,56a8,8,0,0,0,8.06,0l96-56A8,8,0,1,0,220,121.09ZM24,80a8,8,0,0,1,4-6.91l96-56a8,8,0,0,1,8.06,0l96,56a8,8,0,0,1,0,13.82l-96,56a8,8,0,0,1-8.06,0l-96-56A8,8,0,0,1,24,80Zm23.88,0L128,126.74,208.12,80,128,33.26Z",
  leases: "M208,80H176V56a48,48,0,0,0-96,0V80H48A16,16,0,0,0,32,96V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V96A16,16,0,0,0,208,80ZM96,56a32,32,0,0,1,64,0V80H96ZM208,208H48V96H208V208Zm-68-56a12,12,0,1,1-12-12A12,12,0,0,1,140,152Z",
  ledger: "M184,112a8,8,0,0,1-8,8H112a8,8,0,0,1,0-16h64A8,8,0,0,1,184,112Zm-8,24H112a8,8,0,0,0,0,16h64a8,8,0,0,0,0-16Zm48-88V208a16,16,0,0,1-16,16H48a16,16,0,0,1-16-16V48A16,16,0,0,1,48,32H208A16,16,0,0,1,224,48ZM48,208H72V48H48Zm160,0V48H88V208H208Z",
  bell: "M221.8,175.94C216.25,166.38,208,139.33,208,104a80,80,0,1,0-160,0c0,35.34-8.26,62.38-13.81,71.94A16,16,0,0,0,48,200H88.81a40,40,0,0,0,78.38,0H208a16,16,0,0,0,13.8-24.06ZM128,216a24,24,0,0,1-22.62-16h45.24A24,24,0,0,1,128,216ZM48,184c7.7-13.24,16-43.92,16-80a64,64,0,1,1,128,0c0,36.05,8.28,66.73,16,80Z",
  theme: "M233.54,142.23a8,8,0,0,0-8-2,88.08,88.08,0,0,1-109.8-109.8,8,8,0,0,0-10-10,104.84,104.84,0,0,0-52.91,37A104,104,0,0,0,136,224a103.09,103.09,0,0,0,62.52-20.88,104.84,104.84,0,0,0,37-52.91A8,8,0,0,0,233.54,142.23ZM188.9,190.34A88,88,0,0,1,65.66,67.11a89,89,0,0,1,31.4-26A106,106,0,0,0,96,56,104.11,104.11,0,0,0,200,160a106,106,0,0,0,14.92-1.06A89,89,0,0,1,188.9,190.34Z",
  bolt: "M215.79,118.17a8,8,0,0,0-5-5.66L153.18,90.9l14.66-73.33a8,8,0,0,0-13.69-7l-112,120a8,8,0,0,0,3,13l57.63,21.61L88.16,238.43a8,8,0,0,0,13.69,7l112-120A8,8,0,0,0,215.79,118.17ZM109.37,214l10.47-52.38a8,8,0,0,0-5-9.06L62,132.71l84.62-90.66L136.16,94.43a8,8,0,0,0,5,9.06l52.8,19.8Z",
  pin: "M235.32,81.37,174.63,20.69a16,16,0,0,0-22.63,0L98.37,74.49c-10.66-3.34-35-7.37-60.4,13.14a16,16,0,0,0-1.29,23.78L85,159.71,42.34,202.34a8,8,0,0,0,11.32,11.32L96.29,171l48.29,48.29A16,16,0,0,0,155.9,224c.38,0,.75,0,1.13,0a15.93,15.93,0,0,0,11.64-6.33c19.64-26.1,17.75-47.32,13.19-60L235.33,104A16,16,0,0,0,235.32,81.37ZM224,92.69h0l-57.27,57.46a8,8,0,0,0-1.49,9.22c9.46,18.93-1.8,38.59-9.34,48.62L48,100.08c12.08-9.74,23.64-12.31,32.48-12.31A40.13,40.13,0,0,1,96.81,91a8,8,0,0,0,9.25-1.51L163.32,32,224,92.68Z",
  check: "M229.66,77.66l-128,128a8,8,0,0,1-11.32,0l-56-56a8,8,0,0,1,11.32-11.32L96,188.69,218.34,66.34a8,8,0,0,1,11.32,11.32Z",
  clock: "M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm64-88a8,8,0,0,1-8,8H128a8,8,0,0,1-8-8V72a8,8,0,0,1,16,0v48h48A8,8,0,0,1,192,128Z",
  pencil: "M227.31,73.37,182.63,28.68a16,16,0,0,0-22.63,0L36.69,152A15.86,15.86,0,0,0,32,163.31V208a16,16,0,0,0,16,16H92.69A15.86,15.86,0,0,0,104,219.31L227.31,96a16,16,0,0,0,0-22.63ZM92.69,208H48V163.31l88-88L180.69,120ZM192,108.68,147.31,64l24-24L216,84.68Z",
  refresh: "M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z",
  archive: "M224,48H32A16,16,0,0,0,16,64V88a16,16,0,0,0,16,16v88a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V104a16,16,0,0,0,16-16V64A16,16,0,0,0,224,48ZM208,192H48V104H208ZM224,88H32V64H224V88ZM96,136a8,8,0,0,1,8-8h48a8,8,0,0,1,0,16H104A8,8,0,0,1,96,136Z",
  trash: "M216,48H176V40a24,24,0,0,0-24-24H104A24,24,0,0,0,80,40v8H40a8,8,0,0,0,0,16h8V208a16,16,0,0,0,16,16H192a16,16,0,0,0,16-16V64h8a8,8,0,0,0,0-16ZM96,40a8,8,0,0,1,8-8h48a8,8,0,0,1,8,8v8H96Zm96,168H64V64H192ZM112,104v64a8,8,0,0,1-16,0V104a8,8,0,0,1,16,0Zm48,0v64a8,8,0,0,1-16,0V104a8,8,0,0,1,16,0Z",
  x: "M205.66,194.34a8,8,0,0,1-11.32,11.32L128,139.31,61.66,205.66a8,8,0,0,1-11.32-11.32L116.69,128,50.34,61.66A8,8,0,0,1,61.66,50.34L128,116.69l66.34-66.35a8,8,0,0,1,11.32,11.32L139.31,128Z",
  caret: "M213.66,101.66l-80,80a8,8,0,0,1-11.32,0l-80-80A8,8,0,0,1,53.66,90.34L128,164.69l74.34-74.35a8,8,0,0,1,11.32,11.32Z",
  dots: "M140,128a12,12,0,1,1-12-12A12,12,0,0,1,140,128Zm56-12a12,12,0,1,0,12,12A12,12,0,0,0,196,116ZM60,116a12,12,0,1,0,12,12A12,12,0,0,0,60,116Z",
  search: "M229.66,218.34l-50.07-50.06a88.11,88.11,0,1,0-11.31,11.31l50.06,50.07a8,8,0,0,0,11.32-11.32ZM40,112a72,72,0,1,1,72,72A72.08,72.08,0,0,1,40,112Z",
  plus: "M224,128a8,8,0,0,1-8,8H136v80a8,8,0,0,1-16,0V136H40a8,8,0,0,1,0-16h80V40a8,8,0,0,1,16,0v80h80A8,8,0,0,1,224,128Z",
  stats: "M224,200a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V56a8,8,0,0,1,16,0v108.69l50.34-50.35a8,8,0,0,1,11.32,0L144,148.69l58.34-58.35a8,8,0,0,1,11.32,11.32l-64,64a8,8,0,0,1-11.32,0L104,131.31,61.66,173.66,48,187.31V200h168A8,8,0,0,1,224,200Z",
};

export function icon(name) {
  return s("svg", { viewBox: "0 0 256 256", fill: "currentColor", "aria-hidden": "true" },
    s("path", { d: ICONS[name] }));
}

/* Transit-connection mark (svgrepo), used for the rail brand and for empty states. */
export function trainMark(cls = "") {
  return s("svg", { viewBox: "0 0 76 76", role: "img", "aria-label": "Transit connection", class: cls, fill: "var(--accent)" },
    s("path", { d: "M 26.9167,14.25C 31.2889,14.25 34.8333,17.7944 34.8333,22.1667L 34.7416,23.3755L 43.6674,26.6242C 44.7314,25.8142 46.0596,25.3333 47.5,25.3333C 50.9978,25.3333 53.8333,28.1689 53.8333,31.6667C 53.8333,35.1645 50.9978,38 47.5,38C 46.9087,38 46.3364,37.919 45.7935,37.7675L 39.3497,42.6232C 39.5019,43.1672 39.5833,43.7408 39.5833,44.3333C 39.5833,45.1598 39.425,45.9492 39.1371,46.6731L 42.6954,49.7662C 43.6794,49.3273 44.7696,49.0833 45.9167,49.0833C 50.2889,49.0833 53.8333,52.6278 53.8333,57C 53.8333,61.3723 50.2889,64.9167 45.9167,64.9167C 41.5444,64.9167 38,61.3723 38,57C 38,55.5189 38.4067,54.1328 39.1146,52.9473L 35.8505,50.1099C 35.0571,50.4676 34.1768,50.6667 33.25,50.6667C 29.7522,50.6667 26.9167,47.8311 26.9167,44.3333C 26.9167,40.8355 29.7522,38 33.25,38C 34.4155,38 35.5075,38.3148 36.4455,38.8641L 41.9484,34.7173C 41.4501,33.8125 41.1667,32.7727 41.1667,31.6667L 41.2267,30.7907L 32.6194,27.6579C 31.1792,29.1531 29.1566,30.0833 26.9167,30.0833C 22.5444,30.0833 19,26.5389 19,22.1667C 19,17.7944 22.5444,14.25 26.9167,14.25 Z M 26.9167,17.4167C 24.2933,17.4167 22.1667,19.5433 22.1667,22.1667C 22.1667,24.7901 24.2933,26.9167 26.9167,26.9167C 29.54,26.9167 31.6667,24.7901 31.6667,22.1667C 31.6667,19.5433 29.54,17.4167 26.9167,17.4167 Z M 47.5,28.5C 45.7511,28.5 44.3333,29.9178 44.3333,31.6667C 44.3333,33.4156 45.7511,34.8334 47.5,34.8334C 49.2489,34.8334 50.6666,33.4156 50.6666,31.6667C 50.6666,29.9178 49.2489,28.5 47.5,28.5 Z M 45.9166,52.25C 43.2933,52.25 41.1666,54.3767 41.1666,57C 41.1666,59.6234 43.2933,61.75 45.9166,61.75C 48.54,61.75 50.6666,59.6234 50.6666,57C 50.6666,54.3767 48.54,52.25 45.9166,52.25 Z M 33.25,41.1667C 31.5011,41.1667 30.0833,42.5844 30.0833,44.3333C 30.0833,46.0822 31.5011,47.5 33.25,47.5C 34.9989,47.5 36.4166,46.0822 36.4166,44.3333C 36.4166,42.5844 34.9989,41.1667 33.25,41.1667 Z" }));
}

export async function api(path, body) {
  const opts = { headers: { "X-Subway-Token": TOKEN }, cache: "no-store" };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

/* Pushed by the server the moment a worker or permission changes. Polling stays as the fallback. */
export async function listen(onEvent) {
  for (;;) {
    try {
      const res = await fetch("/api/events", { headers: { "X-Subway-Token": TOKEN }, cache: "no-store" });
      const reader = res.body.getReader();
      for (;;) {
        const { done } = await reader.read();
        if (done) break;
        onEvent();
      }
    } catch (e) { /* The connection state already shows this. */ }
    await new Promise((resolve) => { setTimeout(resolve, 1000); });
  }
}

export function duration(secs) {
  if (secs < 60) return `${Math.max(0, Math.round(secs))}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m`;
  const hours = Math.floor(m / 60);
  if (hours < 24) return `${hours}h ${m % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
export function tokens(n) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
  return String(n);
}
export const pct = (v) => `${Number.isInteger(v) ? v : Number(v).toFixed(1)}%`;

/* Elements carrying these attributes count on their own, without a re-render. */
export function ticking(tag, attrs, epoch, format) {
  const el = h(tag, { ...attrs, "data-epoch": epoch, "data-format": format });
  paintTick(el, Date.now() / 1000);
  return el;
}
function paintTick(el, now) {
  const at = Number(el.dataset.epoch);
  el.textContent = el.dataset.format === "age" ? `${duration(now - at)} ago` : duration(at - now);
}
export function tickAll() {
  const now = Date.now() / 1000;
  document.querySelectorAll("[data-epoch]").forEach((el) => paintTick(el, now));
}

/* Minimal store: a slice only notifies its subscribers when its data really changed. */
const state = {};
const subscribers = new Map();
const lastJson = new Map();
export const get = (key) => state[key];
export function subscribe(key, fn) {
  if (!subscribers.has(key)) subscribers.set(key, new Set());
  subscribers.get(key).add(fn);
  return () => subscribers.get(key).delete(fn);
}
export function set(key, value) {
  const json = JSON.stringify(value);
  state[key] = value;
  if (lastJson.get(key) === json) return;
  lastJson.set(key, json);
  (subscribers.get(key) || []).forEach((fn) => fn(value));
}

import { notify } from "./notifications.js";

export function toast(message) {
  notify({ message, kind: "info" });
}

export async function act(path, body) {
  try {
    const res = await api(path, body);
    toast(res.result || "Done");
    document.dispatchEvent(new Event("subway:refresh"));
    return res;
  } catch (e) {
    toast(e.message);
    return null;
  }
}

export function field(label, control, full) {
  const id = `f${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  return h("div", { class: `field${full ? " full" : ""}` }, h("label", { for: id }, label), control);
}

export function modal(title, body, headExtra) {
  const dlg = h("dialog", { "aria-label": title });
  const close = h("button", { class: "icon-btn dialog-close", type: "button", "aria-label": "Close", title: "Close", on: { click: () => dlg.close() } }, icon("x"));
  dlg.append(h("div", { class: "dialog-head" }, h("div", { class: "card-title wrap" }, title), h("div", { class: "btns" }, headExtra, close)),
    h("div", { class: "dialog-body" }, body));
  // Close on a click on the backdrop: press and release both outside the dialog box, so a text
  // selection that ends outside, or a click on the dialog's own padding, keeps it open.
  const outside = (e) => {
    const r = dlg.getBoundingClientRect();
    return e.target === dlg && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom);
  };
  let pressedOutside = false;
  dlg.addEventListener("pointerdown", (e) => { pressedOutside = outside(e); });
  dlg.addEventListener("click", (e) => { if (pressedOutside && outside(e)) dlg.close(); pressedOutside = false; });
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  return dlg;
}

export function ask(title, label) {
  return new Promise((resolve) => {
    const input = h("input", { autocomplete: "off" });
    let value = null;
    const form = h("form", { class: "form", on: { submit: (e) => { e.preventDefault(); value = input.value.trim(); dlg.close(); } } },
      field(label, input, true), h("div", { class: "btns field full" }, h("button", { class: "btn primary", type: "submit" }, "OK")));
    const dlg = modal(title, form);
    dlg.addEventListener("close", () => resolve(value));
    input.focus();
  });
}

export function providerColour(id) {
  if (!id) return "var(--accent)";
  const cleanId = String(id).toLowerCase().trim();
  if (typeof document !== "undefined" && document.documentElement) {
    const val = getComputedStyle(document.documentElement).getPropertyValue(`--series-${cleanId}`);
    if (val && val.trim()) return `var(--series-${cleanId})`;
  }
  if (["claude", "antigravity", "opencode", "copilot", "pi", "deepai"].includes(cleanId)) {
    return `var(--series-${cleanId})`;
  }
  let hash = 0;
  for (let i = 0; i < cleanId.length; i++) {
    hash = ((hash << 5) - hash) + cleanId.charCodeAt(i);
    hash |= 0;
  }
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 65%, 55%)`;
}
