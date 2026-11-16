/* Collaboration sessions (roadmap 38b, the user's design 2026-11-03): people's stations join one
   session by code, every member device is listed and any member can remove one, nothing is shared
   until switched on, and the host can end it. The backend lands in 2.0; until then every call
   fails and the page says so. ?collabDemo=1 renders a filled-in session for layout checks. */
import { h, api, field, icon, modal, toast } from "./lib.js";

const collab = { session: undefined, loading: false };
const UNAVAILABLE = "Collaboration sessions aren't available yet (coming in 2.0)";
const DEMO = {
  id: "demo", name: "Design review", code: "K7QM-42XD", role: "host",
  members: [
    { device_id: "d1", name: "MacBook Pro", person: "You", device_kind: "Mac", online: true, role: "host", self: true },
    { device_id: "d2", name: "iPad Pro", person: "Ada", device_kind: "Tablet", online: true, role: "member" },
    { device_id: "d3", name: "Pixel 9", person: "Tunde", device_kind: "Phone", online: false, role: "member" },
  ],
  scopes: { threads: false, workers: false, terminals: false },
};
const SCOPES = [
  ["threads", "Threads", "Members can see this station's threads and their graphs."],
  ["workers", "Workers", "Members can see workers and send them instructions."],
  ["terminals", "Terminals", "Members can see shared terminal output."],
];

function isDemo() {
  try { return new URLSearchParams(location.search).get("collabDemo") === "1"; } catch (_) { return false; }
}
function rerender() { document.dispatchEvent(new Event("subway:rerender")); }

async function load() {
  if (collab.loading) return;
  collab.loading = true;
  try { const r = await api("/api/collab/session"); collab.session = r.session || null; }
  catch (_) { collab.session = null; }
  collab.loading = false;
  rerender();
}

async function call(path, body) {
  if (isDemo()) { toast("Demo: nothing was sent"); return null; }
  try { const r = await api(path, body || {}); await load(); return r; }
  catch (_) { toast(UNAVAILABLE); return null; }
}

function confirmBox(title, text, okLabel) {
  return new Promise((resolve) => {
    let ok = false;
    const dlg = modal(title, h("div", { class: "rows" }, h("p", {}, text),
      h("div", { class: "btns" },
        h("button", { class: "btn", type: "button", on: { click: () => dlg.close() } }, "Cancel"),
        h("button", { class: "btn primary", type: "button", on: { click: () => { ok = true; dlg.close(); } } }, okLabel))));
    dlg.addEventListener("close", () => resolve(ok));
  });
}

function startView() {
  const name = h("input", { type: "text", placeholder: "e.g. Design review", maxlength: "60", autocomplete: "off" });
  const code = h("input", { type: "text", placeholder: "XXXX-XXXX", maxlength: "9", autocomplete: "off", spellcheck: "false",
    on: { input: (e) => {
      const v = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);
      e.target.value = v.length > 4 ? `${v.slice(0, 4)}-${v.slice(4)}` : v;
    } } });
  const start = h("form", { class: "card collab-card", on: { submit: (e) => {
    e.preventDefault();
    const n = name.value.trim();
    if (!n) { name.focus(); return; }
    call("/api/collab/start", { name: n });
  } } },
    h("h2", { class: "card-title" }, "Start a session"),
    h("div", { class: "card-sub" }, "Invite other people's stations with a join code."),
    field("Session name", name, true),
    h("div", { class: "btns" }, h("button", { class: "btn primary", type: "submit" }, "Start")));
  const join = h("form", { class: "card collab-card", on: { submit: (e) => {
    e.preventDefault();
    if (code.value.length !== 9) { toast("Enter the 8-character code"); code.focus(); return; }
    call("/api/collab/join", { code: code.value });
  } } },
    h("h2", { class: "card-title" }, "Join with a code"),
    h("div", { class: "card-sub" }, "Ask the host for their code."),
    field("Join code", code, true),
    h("div", { class: "btns" }, h("button", { class: "btn primary", type: "submit" }, "Join")));
  return h("div", { class: "collab-stack" },
    h("div", { class: "collab-grid" }, start, join),
    h("p", { class: "row-sub" }, "A session shares nothing until you turn it on. Threads, workers and terminals each have their own switch."));
}

function sessionView(s) {
  const isHost = s.role === "host";
  const members = Array.isArray(s.members) ? s.members : [];
  const scopes = s.scopes || (s.scopes = {});
  const copy = h("button", { class: "btn", type: "button", on: { click: async () => {
    try { await navigator.clipboard.writeText(s.code || ""); toast("Code copied"); } catch (_) { toast(s.code || ""); }
  } } }, "Copy");
  const head = h("div", { class: "card collab-head" },
    h("div", {}, h("h2", { class: "card-title" }, s.name || "Session"),
      h("div", { class: "card-sub" }, isHost ? "You're hosting this session" : "You're a member of this session")),
    h("div", { class: "collab-code" }, h("span", { class: "row-sub" }, "Join code"),
      h("code", { class: "collab-code-value" }, s.code || "—"), copy));

  const memberList = h("div", { class: "card" },
    h("h3", { class: "card-title" }, `Members (${members.length})`),
    h("ul", { class: "collab-members" }, members.map((m) => h("li", { class: "collab-member" },
      h("span", { class: `collab-dot${m.online ? " on" : ""}`, role: "img", "aria-label": m.online ? "Online" : "Offline", title: m.online ? "Online" : "Offline" }),
      h("div", { class: "collab-who" },
        h("div", { class: "collab-person" }, m.person || "Someone"),
        h("div", { class: "row-sub" }, [m.name, m.device_kind].filter(Boolean).join(" · "))),
      h("span", { class: "chip" }, m.role === "host" ? "Host" : "Member"),
      m.self ? h("span", { class: "row-sub" }, "This device")
        : h("button", { class: "btn", type: "button", on: { click: async () => {
          const ok = await confirmBox("Remove device",
            `Remove ${m.name || "this device"} (${m.person || "member"}) from the session? The session key is rotated.`, "Remove");
          if (ok) call("/api/collab/remove", { device_id: m.device_id });
        } } }, "Remove device")))));

  const share = h("div", { class: "card" }, h("h3", { class: "card-title" }, "What's shared"),
    h("div", { class: "rows" }, SCOPES.map(([key, label, hint]) => {
      const on = !!scopes[key];
      return h("div", { class: "collab-scope" },
        h("div", {}, h("div", { class: "collab-person" }, label), h("div", { class: "row-sub" }, hint)),
        h("button", { class: "btn" + (on ? " active" : ""), type: "button", role: "switch", "aria-checked": String(on),
          "aria-label": `Share ${label}`,
          on: { click: () => {
            if (isDemo()) { scopes[key] = !on; rerender(); return; }
            call("/api/collab/scope", { key, on: !on });
          } } }, on ? "On" : "Off"));
    })));

  const security = h("div", { class: "card" }, h("h3", { class: "card-title" }, "Security"),
    h("ul", { class: "collab-facts" },
      h("li", {}, "Each session has its own key."),
      h("li", {}, "Removing a device rotates the key."),
      h("li", {}, "Nothing is shared until you switch it on above.")));

  const footer = h("div", { class: "btns collab-footer" }, isHost
    ? h("button", { class: "btn danger", type: "button", on: { click: async () => {
      if (await confirmBox("End session", "End this session for everyone? Members are disconnected and the key is destroyed.", "End session")) call("/api/collab/end");
    } } }, "End session")
    : h("button", { class: "btn", type: "button", on: { click: async () => {
      if (await confirmBox("Leave session", "Leave this session? You'll need a new code to rejoin.", "Leave")) call("/api/collab/leave");
    } } }, "Leave"));

  return h("div", { class: "collab-stack" }, head,
    h("div", { class: "collab-grid" }, memberList, h("div", { class: "collab-stack" }, share, security)), footer);
}

export function collabPage(d) {
  if (isDemo()) {
    if (!collab.session || collab.session.id !== "demo") collab.session = JSON.parse(JSON.stringify(DEMO));
  } else if (collab.session === undefined) {
    load();
  }
  const banner = h("div", { class: "collab-banner", role: "note" }, icon("bell"),
    h("span", {}, "Preview: sessions between people arrive in 2.0. Your own devices are on the "),
    h("a", { href: "#devices" }, "Devices page"), h("span", {}, "."));
  let body;
  if (collab.session === undefined) body = h("div", { class: "card" }, h("div", { class: "row-sub" }, "Loading…"));
  else if (!collab.session) body = startView();
  else body = sessionView(collab.session);
  return h("div", { class: "collab-stack" }, banner, body);
}
