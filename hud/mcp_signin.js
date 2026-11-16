/* Settings > Shared MCP > Sign-in (roadmap 50c-9). Shows which token each HTTP server uses. */
import { h, api } from "./lib.js";

let cache = null, err = "", inFlight = false, lastFetch = 0, lastOnDone = null, pollTimer = null;
const busy = {};
const notes = {};

// POST helper. Check api() in the code packet: if it does not send its 2nd argument as a JSON POST body, change ONLY this line to match how api() sends a POST.
const post = (body) => api("/api/mcp/connect", body);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function loadSignin(onDone, force) {
  if (onDone) lastOnDone = onDone;
  if (inFlight) return;
  if (!force && cache && Date.now() - lastFetch < 15000) return;
  inFlight = true;
  let attempt = 0;
  try {
    for (;;) {
      try {
        cache = await api("/api/mcp/connect");
        err = "";
        lastFetch = Date.now();
        break;
      } catch (e) {
        // Retry a failed load up to 3 times (1 s, 2 s, 4 s) before showing the error.
        if (attempt >= 3) throw e;
        await sleep(1000 * 2 ** attempt);
        attempt += 1;
      }
    }
  } catch (e) {
    err = e.message || String(e);
  } finally {
    inFlight = false;
  }
  const waiting = cache && (cache.servers || []).some((s) => s.pending === "waiting");
  if (waiting && !pollTimer) {
    pollTimer = setTimeout(() => { pollTimer = null; loadSignin(lastOnDone, true); }, 2000);
  }
  if (typeof lastOnDone === "function") lastOnDone();
}

const urls = {};

async function run(key, action) {
  busy[key] = true;
  notes[key] = "";
  if (lastOnDone) lastOnDone();
  try {
    const res = await post({ action, server: key });
    if (res && res.error) notes[key] = res.error;
    else if (res && res.authorize_url) urls[key] = res.authorize_url;
    if (action !== "connect") delete urls[key];
  } catch (e) {
    notes[key] = e.message || String(e);
  } finally {
    busy[key] = false;
  }
  await loadSignin(lastOnDone, true);
}

// The github server does not let apps register themselves, so offer a one-click
// user-owned app. The API runs the manifest flow in the background.
async function createGithubApp(key) {
  busy[key] = true;
  notes[key] = "";
  if (lastOnDone) lastOnDone();
  try {
    const res = await post({ action: "github_app", key });
    if (res && res.error) notes[key] = res.error;
  } catch (e) {
    notes[key] = e.message || String(e);
  } finally {
    busy[key] = false;
  }
  await loadSignin(lastOnDone, true);
}

function needsOwnApp(s) {
  if (s.key !== "github") return false;
  if (!s.own_client) return true;
  return /register themselves/i.test(String(s.error || s.github_app_error || notes[s.key] || ""));
}

function installUrl(slug) {
  return `https://github.com/apps/${encodeURIComponent(slug)}/installations/new`;
}

function detailFor(s) {
  if (notes[s.key]) return notes[s.key];
  if (s.github_app_pending) return "Finish on GitHub, then install the app";
  if (s.github_app_error) return `GitHub App failed: ${s.github_app_error}`;
  if (s.pending === "waiting") return s.user_code ? `Enter code ${s.user_code} on the page that opened` : "Sign in on the page that opened in your browser";
  if (s.pending === "error") return `Sign-in failed: ${s.pending_error || "unknown error"}`;
  if (s.key === "github" && s.github_app_slug && s.source !== "subway") return "GitHub App created. Install it, then connect.";
  if (s.source === "subway") {
    const mins = typeof s.expires_at === "number" ? Math.round((s.expires_at * 1000 - Date.now()) / 60000) : null;
    return "Subway token" + (mins == null ? "" : mins >= 0 ? ` · expires in ${mins} min` : " · refreshes on next use");
  }
  if (s.source === "borrowed") return "Borrowed from Claude Code";
  return s.status === "disconnected" ? "Signed out by the server. Connect again." : "Not signed in";
}

export function signinRows() {
  if (err) {
    return [{
      id: "mcpsignin-err",
      label: "Couldn't load sign-ins",
      detail: err,
      trailing: h("button", {
        class: "btn", type: "button", "aria-label": "Retry loading sign-ins",
        on: { click: () => loadSignin(null, true) },
      }, "Retry"),
    }];
  }
  if (!cache) return [{ id: "mcpsignin-loading", label: "Loading…" }];
  const servers = cache.servers || [];
  if (!servers.length) return [{ id: "mcpsignin-none", label: "No HTTP MCP servers found" }];
  return servers.map((s) => {
    const action = s.pending === "waiting" ? "cancel" : s.source === "subway" ? "disconnect" : "connect";
    const text = { cancel: "Cancel", disconnect: "Disconnect", connect: "Connect" }[action];
    const attrs = {
      class: "btn", type: "button",
      "aria-busy": String(!!busy[s.key]),
      on: { click: () => run(s.key, action) },
    };
    if (busy[s.key]) attrs.disabled = true;
    let trailing;
    if (s.github_app_pending) {
      trailing = h("span", { style: "color:var(--muted)" }, "Waiting for GitHub…");
    } else if (needsOwnApp(s)) {
      trailing = h("button", {
        class: "btn", type: "button", disabled: !!busy[s.key],
        "aria-busy": String(!!busy[s.key]),
        on: { click: () => createGithubApp(s.key) },
      }, "Create my own GitHub App");
    } else if (s.pending === "waiting" && urls[s.key]) {
      trailing = h("span", { style: "display:flex;gap:8px;align-items:center" },
        h("a", { class: "btn", href: urls[s.key], target: "_blank", rel: "noopener noreferrer" }, "Open page"),
        h("button", attrs, text));
    } else if (s.key === "github" && s.github_app_slug && s.source !== "subway") {
      trailing = h("span", { style: "display:flex;gap:8px;align-items:center" },
        h("a", { class: "btn", href: installUrl(s.github_app_slug), target: "_blank", rel: "noopener noreferrer" }, "Install app"),
        h("button", attrs, text));
    } else {
      trailing = h("button", attrs, text);
    }
    return {
      id: `mcpsignin-${s.key}`,
      label: s.name || s.key,
      detail: detailFor(s) + (s.host ? ` · ${s.host}` : ""),
      trailing,
    };
  });
}
