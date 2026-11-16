import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const DEFAULT_POLL_MS = 1000;
const DEFAULT_COALESCE_MS = 1500;
const MAX_EVENT_LINES = 20;

function envInt(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === null || raw === "") return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0) return fallback;
  return value;
}

// Mirrors subway_home.subway_dir: SUBWAY_DIR when set, else ~/.subway-context
// while it is a real folder, else ~/.subway. The spool lives in wake_spools.
function subwayDir() {
  if (process.env.SUBWAY_DIR) return process.env.SUBWAY_DIR;
  const home = os.homedir();
  const old = path.join(home, ".subway-context");
  try {
    if (fs.statSync(old).isDirectory() && !fs.lstatSync(old).isSymbolicLink()) return old;
  } catch (_) {
    // not there
  }
  return path.join(home, ".subway");
}

// Mirrors wake_driver.get_spool_dir: env overrides first, then the default.
function spoolDir() {
  const direct = process.env.SUBWAY_WAKE_SPOOL_DIR || process.env.SUBWAY_SPOOL_DIR;
  if (direct) return direct;
  return path.join(subwayDir(), "wake_spools");
}

function readLines(file) {
  let raw;
  try {
    if (!fs.statSync(file).isFile()) return [];
    raw = fs.readFileSync(file, "utf8");
  } catch (_) {
    return [];
  }
  const out = [];
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let rec;
    try {
      rec = JSON.parse(line);
    } catch (_) {
      continue;
    }
    if (!rec || typeof rec.ts !== "number" || !Number.isFinite(rec.ts)) continue;
    if (typeof rec.text !== "string" || !rec.text) continue;
    out.push({ ts: rec.ts, text: rec.text });
  }
  return out;
}

export default {
  id: "subway-wake",
  setup(ctx) {
    const state = {
      lastSession: null,
      cursor: Date.now() / 1000,
      inFlight: false,
      disposed: false,
      pollTimer: null,
      coalesceTimer: null,
      unsubscribe: null,
    };

    const pollMs = envInt("SUBWAY_WAKE_POLL_MS", DEFAULT_POLL_MS);
    const coalesceMs = envInt("SUBWAY_WAKE_COALESCE_MS", DEFAULT_COALESCE_MS);

    const files = () => {
      const dir = spoolDir();
      const ids = new Set([`opencode-${process.pid}`, `opencode-${process.ppid}`]);
      return Array.from(ids, (id) => path.join(dir, `${id}.push.jsonl`));
    };

    // Re-read the whole file on every poll, so truncation is harmless.
    const collect = () => {
      const events = [];
      for (const file of files()) {
        for (const ev of readLines(file)) {
          if (ev.ts > state.cursor) events.push(ev);
        }
      }
      events.sort((a, b) => a.ts - b.ts);
      return events;
    };

    const deliver = async (sessionID, events) => {
      const shown = events.slice(0, MAX_EVENT_LINES);
      const extra = events.length - shown.length;
      const lines = shown.map((ev) => `[SUBWAY EVENT] ${ev.text}`);
      if (extra > 0) lines.push(`... and ${extra} more`);
      await ctx.session.synthetic({
        sessionID,
        text: lines.join("\n"),
        description: "Subway event",
        resume: true,
      });
    };

    const poll = () => {
      if (state.disposed || state.inFlight) return;
      const events = collect();
      if (!events.length) return;
      // No session seen yet: hold the events, leave the cursor alone.
      if (!state.lastSession) return;
      const sessionID = state.lastSession;
      state.inFlight = true;
      state.coalesceTimer = setTimeout(() => {
        state.coalesceTimer = null;
        (async () => {
          try {
            const fresh = collect();
            const batch = fresh.length ? fresh : events;
            let maxTs = state.cursor;
            for (const ev of batch) {
              if (ev.ts > maxTs) maxTs = ev.ts;
            }
            state.cursor = maxTs;
            await deliver(sessionID, batch);
          } catch (_) {
            // never surface plugin errors to the host
          } finally {
            state.inFlight = false;
          }
        })();
      }, coalesceMs);
      if (state.coalesceTimer && typeof state.coalesceTimer.unref === "function") {
        state.coalesceTimer.unref();
      }
    };

    const dispose = () => {
      state.disposed = true;
      if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
      if (state.coalesceTimer) {
        clearTimeout(state.coalesceTimer);
        state.coalesceTimer = null;
      }
      if (typeof state.unsubscribe === "function") {
        try {
          state.unsubscribe();
        } catch (_) {
          // ignore
        }
        state.unsubscribe = null;
      }
    };

    try {
      if (ctx && ctx.event && typeof ctx.event.subscribe === "function") {
        state.unsubscribe = ctx.event.subscribe((evt) => {
          try {
            const sessionID = evt && evt.data && evt.data.sessionID;
            if (sessionID) state.lastSession = String(sessionID);
          } catch (_) {
            // ignore malformed events
          }
        });
      }
    } catch (_) {
      state.unsubscribe = null;
    }

    try {
      state.pollTimer = setInterval(poll, pollMs);
      if (state.pollTimer && typeof state.pollTimer.unref === "function") {
        state.pollTimer.unref();
      }
    } catch (_) {
      state.pollTimer = null;
    }

    return dispose;
  },
};
