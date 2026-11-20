/* One server-push connection for the whole prototype (GET /api/events, SSE).
   Every frame fires window "subway:push" (coalesced to one per 120ms) with {types:[...]}.
   Pages reload on it instead of waiting for their poll. EventSource reconnects by itself. */
(() => {
  if (window.SubwayPush || typeof EventSource === "undefined") return;
  let es = null, timer = 0, types = new Set(), last = 0;
  const fire = () => { timer = 0; const t = [...types]; types = new Set(); window.dispatchEvent(new CustomEvent("subway:push", { detail: { types: t } })); };
  const hit = (type) => { last = Date.now(); types.add(type); if (!timer) timer = setTimeout(fire, 120); };
  const NAMES = ["worker", "workers", "worker_update", "worker_status", "worker_media", "permission", "approval", "audit", "status", "lock", "landing", "usage", "message", "screen_request", "screen_signal", "mail", "apps"];
  // Re-emit every stream event on window so other modules (chatcore's queue) share this one EventSource.
  const emit = (name, ev) => {
    let data = null;
    try { data = ev && ev.data ? JSON.parse(ev.data) : null; } catch (_) {}
    try { window.dispatchEvent(new CustomEvent("subway:event", { detail: { name, data } })); } catch (_) {}
  };

  async function postJson(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let d = null;
    try { d = await r.json(); } catch (_) {}
    if (!r.ok || (d && d.error)) throw new Error((d && d.error) || `HTTP ${r.status}`);
    return d;
  }

  const activeApprovals = new Set();
  const activePerms = new Set();
  const dismissed = new Set();

  const isEmbed = () => document.documentElement.classList.contains("embed") || window !== window.top;

  function showApprovalToast(w) {
    if (isEmbed()) return;
    const id = w && w.id;
    if (!id || activeApprovals.has(id) || dismissed.has("approve:" + id) || !window.toast) return;
    activeApprovals.add(id);
    window.toast.warning(`Approve worker: ${w.role || id}`, {
      id: "approve:" + id, duration: Infinity,
      description: [w.provider && String(w.provider).toUpperCase(), w.model, id].filter(Boolean).join(" · "),
      actions: [
        { label: "Approve", cls: "primary", onClick: () => postJson("/api/approve_worker", { id, worker_id: id }).then(() => window.toast.success("Worker approved", { description: w.role || id })).catch((e) => window.toast.error("Approve failed", { description: String(e.message || e) })) },
        { label: "Reject", cls: "danger", onClick: () => postJson("/api/reject_worker", { id, worker_id: id }).then(() => window.toast.info("Worker rejected", { description: w.role || id })).catch((e) => window.toast.error("Reject failed", { description: String(e.message || e) })) }
      ]
    });
  }

  function showPermToast(p) {
    if (isEmbed()) return;
    const id = p && (p.id || p.request_id);
    if (!id || activePerms.has(id) || dismissed.has("perm:" + id) || !window.toast) return;
    activePerms.add(id);
    const title = `Permission: ${p.kind || p.tool || p.type || "request"}`;
    const detail = String(p.detail ?? p.command ?? p.message ?? p.reason ?? "").trim();
    const desc = [p.role || p.worker_id || "", detail ? detail.slice(0, 120) : ""].filter(Boolean).join(" — ");
    window.toast.warning(title, {
      id: "perm:" + id, duration: Infinity, description: desc,
      actions: [
        { label: "Allow", cls: "primary", onClick: () => postJson("/api/answer_permission", { id, decision: "allow" }).then(() => window.toast.success("Allowed")).catch((e) => window.toast.error("Failed", { description: String(e.message || e) })) },
        { label: "Don't allow", cls: "danger", onClick: () => postJson("/api/answer_permission", { id, decision: "deny" }).then(() => window.toast.info("Denied")).catch((e) => window.toast.error("Failed", { description: String(e.message || e) })) },
        { label: "Always", cls: "secondary", onClick: () => postJson("/api/answer_permission", { id, decision: "allow_always" }).then(() => window.toast.success("Always allowed")).catch((e) => window.toast.error("Failed", { description: String(e.message || e) })) }
      ]
    });
  }

  let syncing = false, resyncQueued = false;
  async function syncPendingToasts() {
    if (isEmbed() || syncing) { if (syncing) resyncQueued = true; return; }
    syncing = true;
    try {
      const r = await fetch("/api/status?view=next", { cache: "no-store" });
      if (!r.ok) return;
      const d = await r.json();
      const pendW = (d.workers || []).filter((w) => /^pending_approval$/i.test(String(w.status || "")));
      const pendIds = new Set(pendW.map((w) => w.id));
      [...activeApprovals].filter((id) => !pendIds.has(id)).forEach((id) => { activeApprovals.delete(id); window.toast && window.toast.dismiss && window.toast.dismiss("approve:" + id); });
      pendW.forEach((w) => showApprovalToast(w));
      const perms = d.permissions || d.pending_permissions || [];
      const permIds = new Set(perms.map((p) => p.id || p.request_id).filter(Boolean));
      [...activePerms].filter((id) => !permIds.has(id)).forEach((id) => { activePerms.delete(id); window.toast && window.toast.dismiss && window.toast.dismiss("perm:" + id); });
      perms.forEach((p) => showPermToast(p));
    } finally {
      syncing = false;
      if (resyncQueued) { resyncQueued = false; syncPendingToasts(); }
    }
  }

  let syncTimer = 0;
  const queueSync = () => { clearTimeout(syncTimer); syncTimer = setTimeout(() => { syncTimer = 0; syncPendingToasts(); }, 150); };

  function open() {
    try { es = new EventSource("/api/events"); } catch (_) { return; }
    NAMES.forEach((n) => es.addEventListener(n, (ev) => {
      hit(n);
      emit(n, ev);
      if (["permission", "approval", "notification", "status"].includes(n)) queueSync();
    }));
    es.addEventListener("worker", (ev) => {
      try {
        const d = JSON.parse(ev.data);
        if (d.status === "pending_approval") showApprovalToast(d);
        else if (d.id && d.status && activeApprovals.has(d.id)) { activeApprovals.delete(d.id); window.toast && window.toast.dismiss && window.toast.dismiss("approve:" + d.id); }
      } catch (_) {}
      hit("worker");
    });
    es.onmessage = () => hit("message");
    es.onopen = () => { hit("connect"); emit("connect", null); syncPendingToasts(); };
  }
  open();
  document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - last > 5000) hit("visible"); });
  window.SubwayPush = { get connected() { return !!es && es.readyState === 1; } };
})();
