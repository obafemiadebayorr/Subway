/* Device pairing card.
 *
 * A pairing request is worthless if nobody sees it: the desktop notification is gone in a few
 * seconds, and the request expires in ten minutes. So this card stays pinned on top of every
 * HUD view until the person approves or denies — it is the persistent half of the prompt, the
 * modal dialog being the other.
 */
import { api, h } from "./lib.js";

const POLL_MS = 3000;
const MAX_AGE_S = 600;

let host = null;
let timer = null;

function styles() {
  if (document.getElementById("pairing-card-style")) return;
  const css = `
  .pair-card { position: fixed; top: 14px; left: 50%; transform: translateX(-50%); z-index: 9999;
    width: min(560px, calc(100vw - 32px)); background: #14141a; border: 1px solid #e0a63c;
    border-radius: 14px; box-shadow: 0 18px 50px rgba(0,0,0,.55); padding: 16px; color: #f2f2f5;
    font-family: inherit; }
  .pair-card h3 { margin: 0 0 4px; font-size: 15px; color: #e0a63c; display: flex; align-items: center; gap: 8px; }
  .pair-card .pair-sub { font-size: 12px; color: #9a9aa8; margin-bottom: 12px; }
  .pair-row { border-top: 1px solid #26262f; padding: 12px 0 4px; }
  .pair-row:first-of-type { border-top: 0; }
  .pair-dev { font-size: 14px; font-weight: 600; }
  .pair-meta { font-size: 12px; color: #9a9aa8; margin-top: 2px; }
  .pair-code { font-family: Menlo, monospace; font-size: 22px; letter-spacing: 3px; margin: 8px 0; color: #f2f2f5; }
  .pair-actions { display: flex; gap: 8px; }
  .pair-btn { border: 0; border-radius: 999px; padding: 9px 16px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .pair-approve { background: #7c8cff; color: #0b0b0f; }
  .pair-deny { background: #2a1518; color: #e5545e; }
  .pair-err { font-size: 12px; color: #e5545e; margin-top: 8px; }
  .pair-ok { font-size: 12px; color: #39c07f; margin-top: 8px; }
  .pair-close { margin-left: auto; background: transparent; color: #9a9aa8; border: 0; cursor: pointer; font-size: 16px; }
  `;
  const el = document.createElement("style");
  el.id = "pairing-card-style";
  el.textContent = css;
  document.head.append(el);
}

function ensureHost() {
  if (host && host.isConnected) return host;
  host = h("div", { class: "pair-card", role: "alertdialog", "aria-live": "assertive" });
  host.hidden = true;
  document.body.append(host);
  return host;
}

export function renderRequests(requests, { onApprove, onDeny } = {}) {
  const box = ensureHost();
  const fresh = (requests || []).filter((r) => (r.age_s ?? 0) < MAX_AGE_S);
  if (!fresh.length) {
    box.hidden = true;
    box.replaceChildren();
    return;
  }
  box.hidden = false;
  box.replaceChildren(
    h("h3", {}, "A device wants to connect"),
    h("div", { class: "pair-sub" }, "Approve only if this is your device. The request expires in 10 minutes."),
    ...fresh.map((r) =>
      h("div", { class: "pair-row" },
        h("div", { class: "pair-dev" }, r.device || "Unknown device"),
        h("div", { class: "pair-meta" },
          [r.platform || "unknown", r.ip || "", `${Math.round(r.age_s || 0)}s ago`].filter(Boolean).join(" · ")),
        h("div", { class: "pair-code" }, r.code || "------"),
        h("div", { class: "pair-actions" },
          h("button", {
            class: "pair-btn pair-approve",
            on: { click: () => onApprove && onApprove(r) },
          }, "Approve"),
          h("button", {
            class: "pair-btn pair-deny",
            on: { click: () => onDeny && onDeny(r) },
          }, "Deny"),
        ),
      ),
    ),
  );
}

function cardError(text) {
  const box = ensureHost();
  box.append(h("div", { class: "pair-err" }, text));
}

function cardNote(text) {
  const box = ensureHost();
  box.hidden = false;
  const note = h("div", { class: "pair-ok" }, text);
  box.append(note);
  setTimeout(() => note.remove(), 4000);
}

let last = [];

async function poll() {
  if (document.visibilityState !== "visible") return;
  try {
    const data = await api("/api/pair/list");
    last = data.requests || [];
    renderRequests(last, { onApprove: approve, onDeny: deny });
  } catch (e) {
    // A failed poll must not spam the page; the next tick retries.
  }
}

async function approve(r) {
  try {
    await api("/api/pair/approve", { request_id: r.request_id });
    last = last.filter((x) => x.request_id !== r.request_id);
    renderRequests(last, { onApprove: approve, onDeny: deny });
    cardNote(`Paired ${r.device || "device"}`);
  } catch (e) {
    cardError(`Could not approve: ${e.message}`);
  }
}

async function deny(r) {
  try {
    await api("/api/pair/deny", { request_id: r.request_id });
    last = last.filter((x) => x.request_id !== r.request_id);
    renderRequests(last, { onApprove: approve, onDeny: deny });
  } catch (e) {
    cardError(`Could not deny: ${e.message}`);
  }
}

export function startPairingCard() {
  styles();
  ensureHost();
  poll();
  if (timer) clearInterval(timer);
  timer = setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") poll();
  });
  return () => clearInterval(timer);
}
