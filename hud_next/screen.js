/* Screen sharing: view a linked station's screen (still image or live WebRTC), and share this one on request.
   Video goes peer-to-peer; the HUD only relays call setup (offer, answer, ICE, bye). Nothing is recorded. */
import { api } from "/hud/lib.js";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const say = (m, k = "info", d) => { try { (window.toast?.[k] || console.log)(m, d ? { description: d } : undefined); } catch (_) {} };
const ICE = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const waitSignal = (ms) => new Promise((resolve) => {
  let t = 0;
  const done = () => { clearTimeout(t); window.removeEventListener("subway:push", onPush); resolve(); };
  const onPush = (ev) => { const types = ev?.detail?.types || []; if (types.includes("screen_signal") || types.includes("screen_request")) done(); };
  window.addEventListener("subway:push", onPush);
  t = setTimeout(done, ms);
});
const remote = (body) => api("/api/screen/remote", body);

/* Quality presets. "detail" keeps text crisp and drops frames under load; "motion" keeps it fluid and drops resolution. */
const Q = {
  sharp: { label: "Sharp text", fps: 8, w: 2560, br: 4000000, hint: "detail" },
  balanced: { label: "Balanced", fps: 15, w: 1600, br: 2500000, hint: "detail" },
  smooth: { label: "Smooth motion", fps: 30, w: 1280, br: 3000000, hint: "motion" },
};
const qSelect = (who, cur, disabled) => `<select class="scr-q" data-scr-q="${who}" aria-label="Quality"${disabled ? " disabled" : ""}>${
  Object.entries(Q).map(([k, p]) => `<option value="${k}"${k === cur ? " selected" : ""}>${p.label}</option>`).join("")}</select>`;

/* ---------- viewer: this station watches a linked one ---------- */
let V = null; // { peer, name, el, sid, pc, live, closed }

function openPanel(peer, name, live, mount) {
  closeViewer();
  const el = document.createElement("div");
  el.className = "scr-ov" + (mount ? " embed" : "");
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "false");
  el.setAttribute("aria-label", `${name}'s screen`);
  el.innerHTML = `<div class="scr-box"><div class="scr-h"><b>${esc(name)}'s screen</b><span class="scr-st" aria-live="polite">Connecting…</span>
    ${live ? '<span class="scr-st" data-ctl hidden>You can control this screen</span>' : ""}
    ${live ? qSelect("view", "balanced", true) : ""}<button type="button" class="scr-btn" data-scr="close">${live ? "Stop" : "Close"}</button></div><div class="scr-body"></div></div>`;
  if (mount) mount.append(el); else document.body.append(el);
  V = { peer, name, el, live, closed: false };
  el.querySelector("[data-scr=close]").focus();
  return V;
}
const status = (me, t) => { const s = me?.el.querySelector(".scr-st"); if (s) s.textContent = t; };

function closeViewer() {
  const me = V;
  if (!me) return;
  V = null;
  me.closed = true;
  try { me.pc?.close(); } catch (_) {}
  if (me.sid && me.live) remote({ peer_id: me.peer, op: "signal", session_id: me.sid, kind: "bye", payload: {} }).catch(() => {});
  me.el.remove();
}

async function viewStill(peer, name, mount) {
  const me = openPanel(peer, name, false, mount);
  status(me, "Taking a still image…");
  try {
    const r = await remote({ peer_id: peer, op: "snapshot", max_w: 1600 });
    if (me.closed) return;
    me.el.querySelector(".scr-body").innerHTML = `<img alt="${esc(name)}'s screen" src="data:image/jpeg;base64,${String(r.image_b64 || "").replace(/[^A-Za-z0-9+/=]/g, "")}">
      <div class="scr-acts"><button type="button" class="scr-btn" data-scr="still" data-peer="${esc(peer)}" data-name="${esc(name)}">Refresh</button></div>`;
    status(me, "As of " + new Date((Number(r.ts) || Date.now() / 1000) * 1000).toLocaleTimeString());
  } catch (e) { status(me, e.message || String(e)); }
}

const send = (me, kind, payload) => remote({ peer_id: me.peer, op: "signal", session_id: me.sid, kind, payload }).catch(() => {});

function fallback(me) {
  if (me.closed || me.el.querySelector("[data-fallback]")) return;
  status(me, "Couldn't reach them directly (network).");
  me.el.querySelector(".scr-body").insertAdjacentHTML("beforeend", `<div class="scr-acts" data-fallback>
    <button type="button" class="scr-btn" data-scr="still" data-peer="${esc(me.peer)}" data-name="${esc(me.name)}">Get a still image instead</button></div>`);
}

/* ---------- viewer control: pointer and keys sent over the call's data channel ---------- */
const KEYS = new Set(["Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);

function setControl(me, on) {
  me.control = on;
  if (!on) { clearTimeout(me.txtT); me.txt = ""; }
  me.el.querySelector("[data-ctl]")?.toggleAttribute("hidden", !on);
  me.el.querySelector("video")?.classList.toggle("ctl", on);
}

const sendCtl = (me, msg) => { if (me.dc?.readyState === "open") me.dc.send(JSON.stringify(msg)); };

function flushText(me) {
  clearTimeout(me.txtT);
  const t = me.txt || "";
  me.txt = "";
  if (t) sendCtl(me, { type: "input", kind: "type", text: t });
}

/* Maps a pointer position on the letterboxed (object-fit: contain) video to source pixels; null in the letterbox. */
function videoPoint(v, e) {
  const vw = v.videoWidth, vh = v.videoHeight, W = v.clientWidth, H = v.clientHeight;
  if (!vw || !vh || !W || !H) return null;
  const s = Math.min(W / vw, H / vh);
  const drawnW = vw * s, drawnH = vh * s;
  const offX = (W - drawnW) / 2, offY = (H - drawnH) / 2;
  if (e.offsetX < offX || e.offsetY < offY || e.offsetX > offX + drawnW || e.offsetY > offY + drawnH) return null;
  return { x: Math.round((e.offsetX - offX) * vw / drawnW), y: Math.round((e.offsetY - offY) * vh / drawnH) };
}

function bindControl(me) {
  const v = me.el.querySelector("video");
  if (!v) return;
  v.addEventListener("pointerdown", (e) => {
    if (!me.control) return;
    const pt = videoPoint(v, e);
    if (!pt) return;
    v.focus({ preventScroll: true });
    sendCtl(me, { type: "input", kind: "click", x: pt.x, y: pt.y });
  });
  v.addEventListener("pointermove", (e) => {
    if (!me.control) return;
    const pt = videoPoint(v, e);
    if (pt) throttledMove(me, pt);
  });
  v.addEventListener("keydown", (e) => {
    if (!me.control) return;
    if (e.key === "Escape" && e.shiftKey) { flushText(me); v.blur(); e.preventDefault(); return; }
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      me.txt = (me.txt || "") + e.key;
      clearTimeout(me.txtT);
      me.txtT = setTimeout(() => flushText(me), 150);
      return;
    }
    if (KEYS.has(e.key)) { e.preventDefault(); flushText(me); sendCtl(me, { type: "input", kind: "key", key: e.key }); }
  });
  v.addEventListener("blur", () => flushText(me));
}

async function watchLive(peer, name, mount) {
  const me = openPanel(peer, name, true, mount);
  me.el.querySelector(".scr-body").innerHTML = `<video autoplay playsinline muted tabindex="0"></video>`;
  bindControl(me);
  let r;
  try { r = await remote({ peer_id: peer, op: "request" }); } catch (e) { status(me, e.message || String(e)); me.live = false; return; }
  if (me.closed) return;
  me.sid = r.id || r.session_id;
  status(me, `Waiting for ${name} to share…`);
  const pc = me.pc = new RTCPeerConnection(ICE);
  pc.ontrack = (ev) => { const v = me.el.querySelector("video"); if (v && v.srcObject !== ev.streams[0]) v.srcObject = ev.streams[0]; };
  pc.onicecandidate = (ev) => { if (ev.candidate) send(me, "ice", ev.candidate.toJSON()); };
  pc.ondatachannel = (ev) => {
    const dc = me.dc = ev.channel;
    const sel = () => me.el.querySelector("[data-scr-q=view]");
    dc.onopen = () => { const s = sel(); if (s) s.disabled = false; };
    dc.onclose = () => { const s = sel(); if (s) s.disabled = true; };
    dc.onmessage = (m) => { try { const d = JSON.parse(m.data); if (d.type === "quality" && Q[d.q] && sel()) sel().value = d.q; else if (d.type === "control") setControl(me, !!d.on); } catch (_) {} };
  };
  pc.onconnectionstatechange = () => {
    if (pc.connectionState === "connected") status(me, "Live · view only");
    else if (pc.connectionState === "failed") fallback(me);
  };
  let offeredAt = 0;
  while (!me.closed) {
    let p;
    try { p = await remote({ peer_id: peer, op: "poll", session_id: me.sid }); } catch (e) { status(me, e.message || String(e)); await sleep(2000); continue; }
    for (const m of p.messages || []) {
      if (me.closed) return;
      if (m.kind === "offer") {
        offeredAt = Date.now();
        await pc.setRemoteDescription(m.payload);
        const ans = await pc.createAnswer();
        await pc.setLocalDescription(ans);
        await send(me, "answer", { type: ans.type, sdp: ans.sdp });
        status(me, "Connecting…");
      } else if (m.kind === "ice") {
        try { await pc.addIceCandidate(m.payload); } catch (_) {}
      } else if (m.kind === "bye") {
        status(me, `${name} stopped sharing.`); me.live = false; try { pc.close(); } catch (_) {} return;
      }
    }
    if (p.state === "declined") { status(me, `${name} declined.`); me.live = false; return; }
    if (p.state === "expired") { status(me, "No answer. Try again later."); me.live = false; return; }
    if (offeredAt && pc.connectionState !== "connected" && Date.now() - offeredAt > 12000) { fallback(me); offeredAt = 0; }
    await waitSignal(pc.connectionState === "connected" ? 15000 : 3000);
  }
}

/* ---------- sharer: a linked station asks to watch this one ---------- */
const SH = { seen: new Set(), live: null }; // live: { sid, name, pc, stream }
const sig = (L, kind, payload) => api("/api/screen/signal", { session_id: L.sid, kind, payload }).catch(() => {});

function ask(s) {
  const el = document.createElement("div");
  el.className = "scr-ask";
  el.setAttribute("role", "alertdialog");
  el.setAttribute("aria-label", "Screen request");
  el.innerHTML = `<p><b>${esc(s.viewer_name || "A linked station")}</b> wants to see your screen.</p>
    <p class="scr-m">View only, nothing is recorded, and you pick the screen or window.</p>
    <label class="scr-m scr-ql">Quality ${qSelect("ask", "balanced")}</label>
    <div class="scr-acts"><button type="button" class="scr-btn pri" data-ask="share">Share screen</button><button type="button" class="scr-btn" data-ask="decline">Decline</button></div>`;
  document.body.append(el);
  const gone = setTimeout(() => el.remove(), 120000);
  el.addEventListener("click", (e) => {
    const a = e.target.closest("[data-ask]")?.dataset.ask;
    if (!a) return;
    clearTimeout(gone);
    el.remove();
    if (a === "decline") api("/api/screen/answer", { session_id: s.id, action: "decline" }).catch(() => {});
    else share(s, el.querySelector("[data-scr-q=ask]")?.value || "balanced");
  });
}

function banner(L) {
  document.querySelector(".scr-ban")?.remove();
  const b = document.createElement("div");
  b.className = "scr-ban";
  b.setAttribute("role", "status");
  b.innerHTML = `<span class="scr-dot" aria-hidden="true"></span><span>Being viewed by <b>${esc(L.name)}</b></span>${qSelect("share", L.q)}<button type="button" class="scr-btn ctl" data-scr="ctl" aria-pressed="false">Allow control</button><button type="button" class="scr-btn" data-scr="stopshare">Stop</button>`;
  document.body.append(b);
  paintControl(L);
}

function paintControl(L) {
  const b = document.querySelector(".scr-ban [data-scr=ctl]");
  if (!b) return;
  b.textContent = L.control ? "Control on — Revoke" : "Allow control";
  b.setAttribute("aria-pressed", String(!!L.control));
  controlBanner(L);
}

/* Persistent high-contrast banner while a linked station controls this screen. */
function controlBanner(L) {
  document.querySelector(".scr-ctlban")?.remove();
  if (!L.control) return;
  const b = document.createElement("div");
  b.className = "scr-ctlban";
  b.setAttribute("role", "alert");
  b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#c00;color:#fff;font-weight:700;padding:8px 12px;display:flex;gap:12px;align-items:center;justify-content:center";
  b.innerHTML = `<span>Being controlled by ${esc(L.name)} — </span><button type="button" data-scr="panic" style="background:#fff;color:#c00;font-weight:700;border:0;padding:4px 10px;cursor:pointer">[Stop]</button>`;
  b.querySelector("button").addEventListener("click", () => panicStop());
  document.body.append(b);
}

/* One-click / Ctrl+Alt+Shift+Esc: end remote control, release held keys on the station. */
function panicStop() {
  const L = SH.live;
  if (!L) return;
  api("/api/peer/screen/control/stop", { session_id: L.sid }).catch(() => {});
  api("/api/screen/control", { session_id: L.sid, on: false }).catch(() => {});
  L.control = false;
  paintControl(L);
  if (L.dc?.readyState === "open") L.dc.send(JSON.stringify({ type: "control", on: false }));
}
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.altKey && e.shiftKey && e.key === "Escape") { e.preventDefault(); panicStop(); }
});

/* Viewer side: coalesce pointermove to at most one per 50 ms (20 Hz). */
function throttledMove(me, pt) {
  const now = Date.now();
  if (now - (me.lastMove || 0) < 50) return;
  me.lastMove = now;
  sendCtl(me, { type: "input", kind: "move", x: pt.x, y: pt.y });
}

/* Sharer turns remote control on or off for this share; the viewer is told over the data channel. */
async function toggleControl() {
  const L = SH.live;
  if (!L) return;
  const on = !L.control;
  try { await api("/api/screen/control", { session_id: L.sid, on }); } catch (e) { say("Couldn't change remote control", "error", e.message); return; }
  if (SH.live !== L) return;
  L.control = on;
  paintControl(L);
  if (L.dc?.readyState === "open") L.dc.send(JSON.stringify({ type: "control", on }));
}

/* Replays one viewer input through the desktop tools. Max 30 messages per second; the rest are dropped. */
function relayInput(L, d) {
  if (!L.control || SH.live !== L) return;
  const now = Date.now();
  L.rl ||= { t: 0, n: 0 };
  if (now - L.rl.t >= 1000) { L.rl.t = now; L.rl.n = 0; }
  if (++L.rl.n > 30) return;
  const body = { session_id: L.sid, type: d.kind };
  if (d.kind === "click") { body.x = Number(d.x) | 0; body.y = Number(d.y) | 0; }
  else if (d.kind === "type") body.text = String(d.text ?? "").slice(0, 500);
  else if (d.kind === "key") body.key = String(d.key ?? "").slice(0, 32);
  else return;
  api("/api/screen/input", body).catch(() => {});
}

function stopShare(byViewer = false) {
  const L = SH.live;
  if (!L) return;
  SH.live = null;
  L.stream.getTracks().forEach((t) => t.stop());
  try { L.pc.close(); } catch (_) {}
  if (!byViewer) api("/api/screen/answer", { session_id: L.sid, action: "stop" }).catch(() => {});
  document.querySelector(".scr-ban")?.remove();
  document.querySelector(".scr-ctlban")?.remove();
  say(byViewer ? `${L.name} stopped watching` : "Stopped sharing your screen", "info");
}

/* Applies a preset to the live track and its sender, and tells the viewer which one is active. */
async function applyQuality(L, q) {
  const p = Q[q];
  if (!p || !L) return;
  L.q = q;
  const t = L.stream.getVideoTracks()[0];
  if (!t) return;
  try { t.contentHint = p.hint; } catch (_) {}
  try { await t.applyConstraints({ frameRate: { max: p.fps }, width: { max: p.w } }); } catch (_) {}
  const sender = L.pc.getSenders().find((x) => x.track === t);
  if (sender) {
    const prm = sender.getParameters();
    if (prm.encodings?.length) {
      prm.encodings[0].maxBitrate = p.br;
      prm.encodings[0].maxFramerate = p.fps;
      prm.degradationPreference = p.hint === "motion" ? "maintain-framerate" : "maintain-resolution";
      try { await sender.setParameters(prm); } catch (_) {
        delete prm.degradationPreference;
        try { await sender.setParameters(prm); } catch (_) {}
      }
    }
  }
  const sel = document.querySelector("[data-scr-q=share]");
  if (sel) sel.value = q;
  if (L.dc?.readyState === "open") L.dc.send(JSON.stringify({ type: "quality", q }));
}

async function share(s, q = "balanced") {
  if (SH.live) stopShare();
  const p = Q[q] || Q.balanced;
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: { ideal: p.fps, max: 30 }, width: { max: 2560 }, cursor: "always" }, audio: false });
  } catch (_) {
    api("/api/screen/answer", { session_id: s.id, action: "decline" }).catch(() => {});
    say("Screen sharing cancelled", "info");
    return;
  }
  try { await api("/api/screen/answer", { session_id: s.id, action: "accept" }); } catch (e) {
    stream.getTracks().forEach((t) => t.stop());
    say("Couldn't start sharing", "error", e.message);
    return;
  }
  const pc = new RTCPeerConnection(ICE);
  const L = SH.live = { sid: s.id, name: s.viewer_name || "a linked station", pc, stream, q: Q[q] ? q : "balanced", control: false };
  stream.getTracks().forEach((t) => { pc.addTrack(t, stream); t.onended = () => { if (SH.live === L) stopShare(); }; });
  pc.onicecandidate = (ev) => { if (ev.candidate) sig(L, "ice", ev.candidate.toJSON()); };
  // Control channel inside the call: the viewer asks for a quality here, peer-to-peer, never through the relay.
  L.dc = pc.createDataChannel("ctl");
  L.dc.onopen = () => applyQuality(L, L.q);
  L.dc.onmessage = (m) => { try { const d = JSON.parse(m.data); if (d.type === "quality" && Q[d.q] && SH.live === L) applyQuality(L, d.q); else if (d.type === "input") relayInput(L, d); } catch (_) {} };
  pc.onconnectionstatechange = () => { if (pc.connectionState === "connected") applyQuality(L, L.q); };
  try { stream.getVideoTracks()[0].contentHint = Q[L.q].hint; } catch (_) {}
  banner(L);
  const off = await pc.createOffer();
  await pc.setLocalDescription(off);
  await sig(L, "offer", { type: off.type, sdp: off.sdp });
  while (SH.live === L) {
    try {
      const r = await api("/api/screen/session?session_id=" + encodeURIComponent(L.sid));
      for (const m of r.messages || []) {
        if (m.kind === "answer") await pc.setRemoteDescription(m.payload);
        else if (m.kind === "ice") { try { await pc.addIceCandidate(m.payload); } catch (_) {} }
        else if (m.kind === "bye") { stopShare(true); return; }
      }
      if (r.state === "ended") { stopShare(true); return; }
    } catch (_) {}
    await waitSignal(pc.connectionState === "connected" ? 15000 : 3000);
  }
}

let reqChecking = false;
async function checkRequests() {
  if (reqChecking) return;
  reqChecking = true;
  try {
    const r = await api("/api/screen/active");
    for (const s of r.shares || []) {
      if (s.state === "pending" && !SH.seen.has(s.id)) { SH.seen.add(s.id); ask(s); }
    }
  } catch (_) {}
  reqChecking = false;
}
window.addEventListener("subway:push", (ev) => {
  const types = ev?.detail?.types || [];
  if (types.includes("screen_request") || types.includes("connect")) checkRequests();
});

/* Closing the tab ends a share at once (if the beacon is refused, the session idles out within 90 s). */
window.addEventListener("pagehide", () => {
  const L = SH.live;
  if (L) navigator.sendBeacon?.("/api/screen/answer", new Blob([JSON.stringify({ session_id: L.sid, action: "stop" })], { type: "application/json" }));
});

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-scr]");
  if (!b) return;
  const a = b.dataset.scr;
  if (a === "close") closeViewer();
  else if (a === "stopshare") stopShare();
  else if (a === "ctl") toggleControl();
  else if (a === "still") viewStill(b.dataset.peer, b.dataset.name);
  else if (a === "live") watchLive(b.dataset.peer, b.dataset.name);
});
document.addEventListener("change", (e) => {
  const s = e.target.closest("[data-scr-q]");
  if (!s || !Q[s.value]) return;
  if (s.dataset.scrQ === "share" && SH.live) applyQuality(SH.live, s.value);
  else if (s.dataset.scrQ === "view" && V?.dc?.readyState === "open") { V.dc.send(JSON.stringify({ type: "quality", q: s.value })); status(V, `Asked for ${Q[s.value].label.toLowerCase()}…`); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && V && !e.defaultPrevented && !e.target.closest?.(".cmp, .dock")) closeViewer(); });

const CSS = `
/* Non-modal: the bottom 104px stay free so the dock and composer keep working while watching. */
.scr-ov{position:fixed;inset:0 0 104px 0;z-index:90;display:flex;align-items:center;justify-content:center;padding:16px;pointer-events:none}
.scr-ov .scr-box{pointer-events:auto}
body:has(.scr-ov) .dock{z-index:92}
body:has(.scr-ov) .cmp{z-index:93}
.scr-box{background:var(--paper,var(--surface));border-radius:16px;width:100%;max-width:1200px;max-height:calc(100vh - 136px);display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px color-mix(in srgb,var(--ink) 25%,transparent)}
.scr-h{display:flex;align-items:center;gap:12px;padding:12px 16px;flex-wrap:wrap}
.scr-h b{flex:1;min-width:0}
.scr-st{font-size:13px;color:var(--muted)}
.scr-body{background:#111;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:200px;flex:1;overflow:auto}
.scr-body img,.scr-body video{display:block;max-width:100%;max-height:calc(100vh - 240px);object-fit:contain}
.scr-acts{display:flex;gap:8px;flex-wrap:wrap;padding:10px}
.scr-btn{min-height:40px;padding:0 16px;border-radius:10px;border:1px solid var(--hair);background:var(--ground);font:inherit;cursor:pointer}
.scr-btn.pri{background:var(--sky-deep,#2b5f8a);color:#fff;border-color:transparent}
.scr-ask{position:fixed;z-index:95;left:16px;right:16px;bottom:16px;background:var(--paper,var(--surface));border-radius:14px;padding:14px 16px;box-shadow:0 12px 40px color-mix(in srgb,var(--ink) 20%,transparent);font-size:14px}
.scr-ask p{margin:0 0 6px}.scr-m{color:var(--muted);font-size:13px}
.scr-ask .scr-acts{padding:6px 0 0}
.scr-q{min-height:36px;padding:0 10px;border-radius:10px;border:1px solid var(--hair);background:var(--surface);color:inherit;font:inherit;font-size:13px}
.scr-q:disabled{opacity:.5}
.scr-ql{display:flex;align-items:center;gap:8px;margin:6px 0}
.scr-ban .scr-q{min-height:32px;background:#2c2f2e;color:#fff;border-color:#444}
.scr-ov.embed{position:absolute;inset:0;z-index:6;pointer-events:auto;padding:0;display:flex}
.scr-ov.embed .scr-box{width:100%;height:100%;max-height:none;border-radius:0;box-shadow:none}
.scr-ov.embed .scr-h{padding:4px 8px;gap:6px}
.scr-ov.embed .scr-st{font-size:10px}
.scr-ov.embed .scr-body{min-height:0}
.scr-ov.embed .scr-body img,.scr-ov.embed .scr-body video{max-height:100%}
.scr-ov.embed .scr-btn{min-height:26px;font-size:11px;padding:0 10px}
.scr-ban{position:fixed;z-index:96;top:12px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:10px;padding:6px 6px 6px 14px;border-radius:18px;background:#1d1f1e;flex-wrap:wrap;justify-content:center;color:#fff;font-size:13px;max-width:calc(100vw - 32px)}
.scr-ban .scr-btn{min-height:32px;background:#c4492f;color:#fff;border:0}
.scr-ban .scr-btn.ctl{background:#2c2f2e;color:#fff;border:1px solid #555}
.scr-ban .scr-btn.ctl[aria-pressed=true]{background:#2f7d4f;border-color:#2f7d4f}
.scr-body video.ctl{cursor:crosshair}
.scr-body video:focus-visible{outline:2px solid #8ab4d8;outline-offset:-2px}
.scr-dot{width:8px;height:8px;border-radius:50%;background:#e5533a;flex-shrink:0;animation:scrp 1.4s infinite}
@keyframes scrp{50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){.scr-dot{animation:none}}
@media (min-width:768px){.scr-ask{left:auto;width:380px}}
`;
if (!document.getElementById("scr-css")) document.head.append(Object.assign(document.createElement("style"), { id: "scr-css", textContent: CSS }));
if (window.RTCPeerConnection && navigator.mediaDevices?.getDisplayMedia) checkRequests();

export function mountViewer(peer, name, live, mount) {
  return live ? watchLive(peer, name, mount) : viewStill(peer, name, mount);
}
export function closeViewerNow() { closeViewer(); }
