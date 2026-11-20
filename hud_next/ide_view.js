// Read-only IDE view of a linked station (Item 77 Unit 4).

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

export const IDE_POLL_MS = 3000;

export async function fetchIdeState(peerId) {
  const r = await fetch("/api/peers/ide?peer_id=" + encodeURIComponent(peerId));
  const data = await r.json().catch(() => null);
  if (!r.ok) return { error: data?.error || `status ${r.status}`, status: r.status };
  return data || { present: false };
}

export async function fetchPeerFile(peerId, filePath) {
  const r = await fetch(
    "/api/peers/file?peer_id=" + encodeURIComponent(peerId) + "&path=" + encodeURIComponent(filePath)
  );
  const data = await r.json().catch(() => null);
  if (!r.ok) return { error: data?.error || `status ${r.status}`, status: r.status };
  return data || { error: "empty reply" };
}

export function renderIdeView(container, peerId, peerName, ideState, opts = {}) {
  if (!container) return;
  container.textContent = "";
  const root = el("div", "dv-ide");
  const head = el("div", "dv-ide-head");
  head.append(el("span", "dv-ide-title", `${peerName || "Station"} — IDE`));
  if (typeof opts.onClose === "function") {
    const close = el("button", "dv-btn ghost", "Close");
    close.type = "button";
    close.addEventListener("click", opts.onClose);
    head.append(close);
  }
  root.append(head);

  if (!ideState) {
    root.append(el("div", "dv-dim", "Loading IDE…"));
    container.append(root);
    return;
  }
  if (ideState.error) {
    root.append(el("div", "dv-dim", ideState.status === 403 ? "IDE access not granted (ide:read required)." : ideState.error));
    container.append(root);
    return;
  }
  if (!ideState.present) {
    root.append(el("div", "dv-dim", ideState.expired ? "IDE snapshot expired." : "No active IDE reported."));
    container.append(root);
    return;
  }

  const wins = Array.isArray(ideState.windows) ? ideState.windows : [];
  if (wins.length) {
    const wbar = el("div", "dv-ide-sec");
    wbar.append(el("div", "dv-ide-lbl", "Windows"));
    const list = el("div", "dv-ide-chips");
    for (const w of wins) {
      const label = w.title || w.workspace || w.id || "window";
      list.append(el("span", "dv-ide-chip" + (w.focused ? " active" : ""), label));
    }
    wbar.append(list);
    root.append(wbar);
  }

  const act = ideState.active && typeof ideState.active === "object" ? ideState.active : {};
  const tabs = Array.isArray(ideState.tabs) ? ideState.tabs : [];
  const fileView = el("div", "dv-ide-fileview");

  const openFile = async (fpath) => {
    if (!fpath) return;
    fileView.textContent = `Loading ${fpath}…`;
    const res = await fetchPeerFile(peerId, fpath);
    fileView.textContent = "";
    if (res.error) {
      fileView.append(el("div", "dv-dim", res.error));
      return;
    }
    fileView.append(el("div", "dv-ide-lbl", fpath + " (read-only)"));
    const pre = el("pre", "dv-ide-code", String(res.content ?? res.text ?? ""));
    fileView.append(pre);
  };

  if (tabs.length) {
    const tsec = el("div", "dv-ide-sec");
    tsec.append(el("div", "dv-ide-lbl", "Tabs"));
    const tbar = el("div", "dv-ide-chips");
    for (const t of tabs) {
      const fpath = t.file || "";
      const isAct = Boolean(t.active || (act.file && fpath === act.file));
      const btn = el("button", "dv-ide-tab" + (isAct ? " active" : ""), (t.title || fpath || "tab") + (t.dirty ? " •" : ""));
      btn.type = "button";
      btn.disabled = !fpath;
      if (fpath) btn.addEventListener("click", () => openFile(fpath));
      tbar.append(btn);
    }
    tsec.append(tbar);
    root.append(tsec);
  }

  if (act.file) {
    const asec = el("div", "dv-ide-sec");
    asec.append(el("div", "dv-ide-lbl", "Active file"));
    const btn = el("button", "dv-ide-link", act.file);
    btn.type = "button";
    btn.addEventListener("click", () => openFile(act.file));
    asec.append(btn);
    if (act.selection) {
      const s = act.selection;
      const loc = s.start_line != null ? `L${s.start_line}:${s.start_col ?? 0}` : s.line != null ? `L${s.line}:${s.col ?? 0}` : "";
      if (loc) asec.append(el("span", "dv-dim", ` (${loc})`));
    }
    root.append(asec);
  }

  const terms = Array.isArray(ideState.terminals) ? ideState.terminals : [];
  if (terms.length) {
    const sec = el("div", "dv-ide-sec");
    sec.append(el("div", "dv-ide-lbl", "Terminals"));
    const list = el("div", "dv-ide-chips");
    for (const t of terms) list.append(el("span", "dv-ide-chip" + (t.active ? " active" : ""), t.name || "terminal"));
    sec.append(list);
    root.append(sec);
  }

  const agents = Array.isArray(ideState.agents) ? ideState.agents : [];
  if (agents.length) {
    const sec = el("div", "dv-ide-sec");
    sec.append(el("div", "dv-ide-lbl", "Agent panels"));
    const list = el("div", "dv-ide-chips");
    for (const a of agents) {
      const lbl = [a.name || a.provider, a.status].filter(Boolean).join(" · ");
      list.append(el("span", "dv-ide-chip", lbl || "agent"));
    }
    sec.append(list);
    root.append(sec);
  }

  root.append(fileView);
  container.append(root);
}
