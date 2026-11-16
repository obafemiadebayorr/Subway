/* Linked stations (roadmap 66): link another of your stations, choose what each link may do here, unlink. */
import { act, api, field, h, icon, modal, toast } from "./lib.js";

const GRANTS = [
  ["chats", "Read chats", [["none", "No"], ["titles", "Titles only"], ["decisions", "Decisions"], ["full", "Everything"]]],
  ["mail", "Send mail", [["off", "No"], ["on", "Yes"]]],
  ["spawn", "Start workers", [["off", "No"], ["pending", "After approval"], ["direct", "Without approval"]]],
  ["approve", "Approve workers and permissions", [["off", "No"], ["on", "Yes"]]],
  ["land", "Change files", [["off", "No"], ["patch", "As a patch"], ["edits", "Direct edits"], ["branch", "On a branch"]]],
  ["files", "Files", [["off", "No"], ["read", "Read"], ["send", "Read and write"]]],
  ["apps", "Apps", [["off", "No"], ["browser", "Browser"], ["desktop", "Browser and desktop"]]],
  ["claims", "File claims", [["off", "No"], ["read", "See them"], ["claim", "See and claim"]]],
  ["events", "See workers", [["off", "No"], ["own", "Its own"], ["all", "All"]]],
  ["screen", "See my screen", [["off", "No"], ["snapshot", "Still image on request"], ["live", "Live view I approve"]]],
];
const PRESETS = [["personal", "Personal"], ["collaborator", "Collaborator"], ["observer", "Observer"]];

const state = { peers: undefined, open: new Set(), busy: "" };

async function load(force = false) {
  if (state.peers !== undefined && !force) return;
  if (!force) state.peers = null;
  try { state.peers = ((await api("/api/peers")) || {}).peers || []; }
  catch (e) { state.peers = { error: e.message || String(e) }; }
  document.dispatchEvent(new Event("subway:rerender"));
}

async function save(path, body) {
  const res = await act(path, body);
  if (res) await load(true);
  return res;
}

function grantRow(peer, key, label, choices) {
  const cur = (peer.grants || {})[key];
  const sel = h("select", { "aria-label": label, on: { change: (e) => save("/api/peers/grants", { peer_id: peer.peer_id, grants: { [key]: e.target.value } }) } },
    choices.map(([v, l]) => h("option", { value: v, selected: v === cur ? true : undefined }, l)));
  return h("label", { class: "link-grant" }, h("span", { class: "link-grant-label" }, label), sel);
}

function unlinkModal(peer) {
  const dlg = modal("Unlink station", h("div", { class: "rows" },
    h("p", { class: "row-sub" }, `Unlink “${peer.name}”? Both stations drop the link, and it must be linked again with a new code.`),
    h("div", { class: "btns" },
      h("button", { class: "btn danger", type: "button", on: { click: async () => { if (await save("/api/peer/unlink", { peer_id: peer.peer_id })) dlg.close(); } } }, "Unlink"),
      h("button", { class: "btn", type: "button", on: { click: () => dlg.close() } }, "Cancel"))));
}

function peerCard(peer, anyController) {
  const open = state.open.has(peer.peer_id);
  const toggle = () => { open ? state.open.delete(peer.peer_id) : state.open.add(peer.peer_id); document.dispatchEvent(new Event("subway:rerender")); };
  const warn = (peer.warnings || []);
  const presetBtns = h("div", { class: "seg", role: "group", "aria-label": "Preset" },
    PRESETS.map(([v, l]) => h("button", { type: "button", on: { click: () => save("/api/peers/grants", { peer_id: peer.peer_id, grants: v }) } }, l)));
  const ctl = h("label", { class: "link-grant" }, h("span", { class: "link-grant-label" }, "Only controller"),
    h("input", { type: "checkbox", checked: peer.controller ? true : undefined,
      on: { change: (e) => save("/api/peers/controller", { peer_id: e.target.checked ? peer.peer_id : null }) } }));
  return h("div", { class: "link-peer" },
    h("div", { class: "row device-row" },
      h("div", {},
        h("div", { class: "row-title" }, peer.name || peer.peer_id, peer.alias ? h("span", { class: "tag" }, `@${peer.alias}`) : null,
          peer.controller ? h("span", { class: "tag" }, "controller") : null),
        h("div", { class: "row-sub" }, warn.length ? `${warn.length} warning${warn.length > 1 ? "s" : ""}` : "No warnings")),
      h("div", { class: "btns" },
        h("button", { class: "btn", type: "button", "data-scr": "still", "data-peer": peer.peer_id, "data-name": peer.name || "" }, "View screen"),
        h("button", { class: "btn", type: "button", "data-scr": "live", "data-peer": peer.peer_id, "data-name": peer.name || "" }, "Watch live"),
        h("button", { class: "btn", type: "button", "aria-expanded": String(open), on: { click: toggle } }, open ? "Done" : "Access"),
        h("button", { class: "btn danger", type: "button", on: { click: () => unlinkModal(peer) } }, "Unlink"))),
    open ? h("div", { class: "link-settings" },
      h("div", { class: "link-presets" }, h("span", { class: "row-sub" }, "Start from a preset"), presetBtns),
      h("div", { class: "link-grants" }, GRANTS.map(([k, l, c]) => grantRow(peer, k, l, c)), ctl),
      warn.length ? h("ul", { class: "link-warnings", role: "list" }, warn.map((w) => h("li", {}, h("span", {}, w)))) : null,
      anyController && !peer.controller ? h("div", { class: "row-sub" }, "Another station is the controller.") : null) : null);
}

async function linkModal() {
  const input = h("textarea", { rows: 3, placeholder: "subway://pair?…", required: true, autocomplete: "off" });
  const preset = h("select", {}, PRESETS.map(([v, l]) => h("option", { value: v }, l)));
  const out = h("div", { class: "row-sub", "aria-live": "polite" });
  const form = h("form", { class: "form", on: { submit: async (e) => {
    e.preventDefault();
    out.textContent = "Linking through Subway Tunnel…";
    const res = await act("/api/peer/link", { link: input.value.trim(), preset: preset.value });
    if (res && res.ok) { toast(`Linked ${res.peer && res.peer.name || "station"}`); dlg.close(); load(true); }
    else out.textContent = "";
  } } },
    field("Link from the other station", input, true), field("Access for it here", preset),
    h("div", { class: "row-sub field full" }, "On the other station: Devices → Linked stations → Show my link. Codes work once and expire."),
    out,
    h("div", { class: "btns field full" }, h("button", { class: "btn primary", type: "submit" }, "Link")));
  const dlg = modal("Link a station", form);
  input.focus();
}

async function showMyLink() {
  let res;
  try { res = await api("/api/pair/qr", {}); }
  catch (e) { toast(/not set up/.test(e.message) ? "Start the tunnel first: subway tunnel --url wss://…" : e.message); return; }
  const text = h("textarea", { rows: 3, readonly: true }, res.link);
  modal("This station's link", h("div", { class: "rows" },
    h("p", { class: "row-sub" }, "Paste it into the other station (Devices → Linked stations → Link a station, or subway link add '<link>'). It works once and expires in a few minutes."),
    text,
    h("div", { class: "btns" }, h("button", { class: "btn primary", type: "button", on: { click: async () => {
      try { await navigator.clipboard.writeText(res.link); toast("Copied"); } catch (_) { text.select(); }
    } } }, "Copy"))));
}

export function linkedStationsSection() {
  load();
  const peers = state.peers;
  const anyController = Array.isArray(peers) && peers.some((p) => p.controller);
  const body = peers === undefined || peers === null ? h("div", { class: "empty" }, "Loading…")
    : peers.error ? h("div", { class: "empty" }, peers.error)
    : !peers.length ? h("div", { class: "empty" }, "No linked stations yet. Link another Mac, a Mac mini or a home server to work as one fleet.")
    : h("div", { class: "rows" }, peers.map((p) => peerCard(p, anyController)));
  return h("section", { class: "card linked-stations-settings" },
    h("div", { class: "card-head" },
      h("div", {}, h("h2", { class: "card-title" }, "Linked stations"),
        h("div", { class: "card-sub" }, "Your other stations, and what each may do on this one. Grants are checked here; nothing the other station sends widens them.")),
      h("div", { class: "btns" },
        h("button", { class: "btn", type: "button", on: { click: showMyLink } }, "Show my link"),
        h("button", { class: "btn primary", type: "button", on: { click: linkModal } }, icon("plus"), h("span", {}, "Link a station")))),
    body);
}
