/* Fast mail panel: messages sent through Subway's fastmail queue. */
import { h, api } from "./lib.js";

let cache = null, err = "";
let inFlight = false;
let fetchedAt = 0;
let lastOnDone = null;

export async function loadFastmail(onDone) {
  if (onDone) lastOnDone = onDone;
  if (inFlight) return;
  inFlight = true;
  try {
    cache = await api("/api/fastmail");
    err = "";
    fetchedAt = Date.now();
  } catch (e) {
    err = e.message || String(e);
  } finally {
    inFlight = false;
  }
  const done = typeof onDone === "function" ? onDone : lastOnDone;
  if (typeof done === "function") done();
}

function age(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

async function mute(stationId) {
  try {
    await api("/api/fastmail/mute", { station_id: stationId });
  } catch (e) { /* reload shows the truth either way */ }
  await loadFastmail(() => document.dispatchEvent(new Event("subway:rerender")));
}

export function fastmailRows() {
  if (err) {
    return [{ label: "Couldn't load fast mail", detail: err }];
  }
  if (!cache) {
    return [{ label: "Loading…" }];
  }

  const messages = Array.isArray(cache.messages) ? cache.messages : [];
  const muted = new Set(Array.isArray(cache.muted_senders) ? cache.muted_senders : []);
  const rows = [];

  const ageSecs = fetchedAt ? Math.floor((Date.now() - fetchedAt) / 1000) : 0;
  if (fetchedAt && Date.now() - fetchedAt > 30000) {
    rows.push({
      id: "fastmail:stale",
      label: `Updated ${ageSecs} s ago`,
      trailing: h("button", {
        class: "btn",
        type: "button",
        on: { click: () => loadFastmail(() => document.dispatchEvent(new Event("subway:rerender"))) },
      }, "Refresh"),
    });
  }

  if (!messages.length) {
    rows.push({ label: "No fast mail yet" });
    return rows;
  }

  for (const m of messages) {
    const peer = m.to || m.from || "unknown";
    const method = (m.delivery && m.delivery.method) || m.method || "unknown";
    const preview = String(m.reply_preview || m.text || "").slice(0, 120);
    const parts = [
      m.state || "unknown",
      age(m.created_at),
      method,
      m.thread_id ? `thread ${m.thread_id}` : "",
      preview,
    ].filter(Boolean);
    const isMuted = muted.has(peer);
    rows.push({
      id: `fastmail:${m.id || peer}`,
      label: isMuted ? `${peer} · muted` : peer,
      detail: parts.join(" · "),
      trailing: h("button", {
        class: "btn",
        type: "button",
        "aria-pressed": String(isMuted),
        "aria-label": isMuted ? `Unmute ${peer}` : `Mute ${peer}`,
        on: { click: () => mute(peer) },
      }, isMuted ? "Muted" : "Mute"),
    });
  }
  return rows;
}
