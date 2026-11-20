(function () {
  "use strict";
  const STEPS = ["preflight", "push", "ci", "merge", "release", "mirror"];
  const LABELS = ["Preflight", "Push", "CI", "Merge", "Release", "Mirror"];
  const ADVANCE_LABEL = { push: "Push", ci: "Watch CI", merge: "Merge PR", release: "Run release", mirror: "Publish mirror" };

  function fmtElapsed(ms) {
    if (ms < 0) ms = 0;
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    return m + "m " + (s % 60) + "s";
  }

  let states = {}; // lane -> last state
  let laneNames = [];
  let releaseLanes = [];
  let pill = null;
  let styleInjected = false;
  let prevStates = null;
  let loopTimer = null;
  let seeded = false;

  function injectStyle() {
    if (styleInjected || document.getElementById("sc-style")) return;
    styleInjected = true;
    const st = document.createElement("style");
    st.id = "sc-style";
    st.textContent = `
      .sc-card { padding:16px 20px; border-radius:14px; border:1px solid var(--hair); background:var(--surface); font:inherit; color:var(--ink); }
      .sc-steps { display:flex; gap:8px; list-style:none; padding:0; margin:8px 0; }
      .sc-step { flex:1; }
      .sc-step::before { content:""; display:block; height:4px; border-radius:2px; background:var(--hair); margin-bottom:6px; }
      .sc-step.is-running::before { background:var(--accent); animation: sc-pulse 1.2s infinite; }
      .sc-step.is-passed::before { background:#1E9E50; }
      .sc-step.is-failed::before { background:#E2453C; }
      .sc-step.is-needs_you::before { background:var(--accent); }
      @keyframes sc-pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
      @media (prefers-reduced-motion: reduce) { .sc-step.is-running::before { animation:none; } }
      @media (max-width:600px) { .sc-steps { flex-direction:column; } }
      .sc-pill { display:inline-flex; padding:4px 10px; border-radius:999px; font-size:12px; border:1px solid var(--hair); margin-left:auto; text-decoration:none; color:var(--ink); }
      .sc-pill[hidden], .sc-pill:empty { display:none; }
      .sc-card button.primary { background:var(--accent); color:#fff; border-radius:999px; padding:8px 14px; border:none; cursor:pointer; }
      .sc-btn { border-radius:8px; padding:6px 12px; border:1px solid var(--hair); background:var(--surface); color:var(--ink); font-size:12.5px; cursor:pointer; }
      .sc-btn.primary { background:var(--accent); color:#fff; border-color:transparent; }
      .sc-acts { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; }
      .sc-log { font-family:"JetBrains Mono",monospace; font-size:12px; max-height:240px; overflow:auto; }
      .sc-toast { position:fixed; bottom:20px; right:20px; padding:10px 14px; background:var(--surface); border:1px solid var(--hair); border-radius:10px; z-index:9999; }
    `;
    document.head.appendChild(st);
  }

  function toast(text) {
    if (typeof window.toast === "function") { window.toast(text); return; }
    const d = document.createElement("div");
    d.className = "sc-toast";
    d.textContent = text;
    document.body.appendChild(d);
    setTimeout(() => d.remove(), 4000);
  }

  function fetchState(lane) {
    return fetch("/api/ship/state?lane=" + encodeURIComponent(lane))
      .then((r) => { if (r.status === 400) return null; return r.json(); })
      .catch(() => null);
  }

  function ensurePill() {
    if (pill) return pill;
    const header = document.querySelector("header");
    if (!header) return null;
    pill = document.createElement("a");
    pill.className = "sc-pill";
    pill.href = "#landing";
    pill.hidden = true;
    pill.addEventListener("click", (e) => {
      e.preventDefault();
      location.hash = "#landing";
      setTimeout(() => document.querySelector(".sc-card")?.scrollIntoView({ block: "center" }), 300);
    });
    header.appendChild(pill);
    return pill;
  }

  function updatePill() {
    const p = ensurePill();
    if (!p) return;
    let urgent = null; // {level, lane, step, detail}
    for (const lane of laneNames) {
      const s = states[lane];
      if (!s || !s.status || s.status === "idle") continue;
      const level = s.status === "failed" ? 3 : s.status === "needs_you" ? 2 : 1;
      if (!urgent || level > urgent.level) {
        urgent = { level, lane, step: s.step, status: s.status, detail: s.steps && s.steps[s.step] ? s.steps[s.step].detail : "" };
      }
    }
    if (!urgent) { p.hidden = true; return; }
    p.hidden = false;
    const stepLabel = urgent.step && urgent.step !== "done" ? urgent.step.charAt(0).toUpperCase() + urgent.step.slice(1) : urgent.step;
    if (urgent.status === "running") p.textContent = "Shipping " + urgent.lane + " · " + stepLabel;
    else if (urgent.status === "needs_you") p.textContent = "Ship needs you: " + stepLabel;
    else if (urgent.status === "failed") p.textContent = "Ship blocked: " + String(urgent.detail || "").slice(0, 40);
  }

  function renderCard(slot, lane) {
    const s = states[lane] || {};
    const status = s.status || "idle";
    if (status === "idle") return null;
    const card = document.createElement("section");
    card.className = "sc-card";
    const title = document.createElement("h3");
    title.textContent = "Ship " + lane;
    card.appendChild(title);

    if (s.step === "done" || (s.steps && s.steps.mirror && s.steps.mirror.status === "passed")) {
      const line = document.createElement("div");
      line.textContent = lane + " shipped · npm · mirror";
      card.appendChild(line);
      if (s.pr) {
        const a = document.createElement("a");
        a.href = s.pr; a.textContent = "PR"; a.target = "_blank";
        card.appendChild(a);
      }
      return card;
    }

    const ol = document.createElement("ol");
    ol.className = "sc-steps";
    STEPS.forEach((stepName, i) => {
      const info = (s.steps && s.steps[stepName]) || { status: "idle" };
      const st = info.status || "idle";
      const li = document.createElement("li");
      li.className = "sc-step is-" + st;
      const label = document.createElement("div");
      label.textContent = LABELS[i];
      li.appendChild(label);
      const stateText = document.createElement("div");
      stateText.textContent = st;
      li.appendChild(stateText);
      // elapsed
      if (info.started) {
        const start = new Date(info.started).getTime();
        const end = info.ended ? new Date(info.ended).getTime() : (st === "running" ? Date.now() : start);
        const el = document.createElement("div");
        el.textContent = fmtElapsed(end - start);
        li.appendChild(el);
      }
      if (st === "failed") {
        const detail = document.createElement("div");
        detail.textContent = String(info.detail || "").split("\n")[0];
        li.appendChild(detail);
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Log";
        details.appendChild(summary);
        const pre = document.createElement("pre");
        pre.className = "sc-log";
        const tail = info.log_tail || "";
        const lines = tail.split("\n").slice(-20).join("\n");
        pre.textContent = lines;
        details.appendChild(pre);
        li.appendChild(details);
        const acts = document.createElement("div");
        acts.className = "sc-acts";
        const fixBtn = document.createElement("button");
        fixBtn.className = "sc-btn primary";
        fixBtn.textContent = "Spin fixer";
        fixBtn.addEventListener("click", () => {
          fetch("/api/spawn", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              role: "Ship " + lane + " fixer",
              lane,
              prompt: "Fix failing ship step '" + stepName + "' on lane " + lane + ":\n\n" + lines,
            }),
          }).then(() => toast("Fixer worker queued for " + lane));
        });
        acts.appendChild(fixBtn);
        const rerunBtn = document.createElement("button");
        rerunBtn.className = "sc-btn";
        rerunBtn.textContent = "Re-run";
        rerunBtn.addEventListener("click", () => {
          fetch("/api/ship/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lane }),
          }).then(() => refreshAll());
        });
        acts.appendChild(rerunBtn);
        li.appendChild(acts);
      }
      if (st === "needs_you" && s.step === stepName) {
        const btn = document.createElement("button");
        btn.className = "sc-btn primary";
        btn.textContent = ADVANCE_LABEL[stepName];
        btn.addEventListener("click", () => {
          const label = ADVANCE_LABEL[stepName];
          if (stepName === "release" || stepName === "mirror") {
            if (!window.confirm("Run " + label + "?")) return;
          }
          fetch("/api/ship/advance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lane, step: stepName, confirm: true }) })
            .then(() => refreshAll());
        });
        li.appendChild(btn);
      }
      ol.appendChild(li);
    });
    card.appendChild(ol);
    return card;
  }

  function refreshAll() {
    const promises = laneNames.map((lane) => fetchState(lane).then((s) => {
      if (s) {
        const prev = states[lane];
        states[lane] = s;
        if (prevStates !== null) {
          // toast on transitions
          const prevStepStatus = prev && prev.steps && prev.steps[prev.step] ? prev.steps[prev.step].status : null;
          const curStepStatus = s.steps && s.steps[s.step] ? s.steps[s.step].status : null;
          if (prevStepStatus !== "failed" && curStepStatus === "failed") toast(lane + " step failed");
          if (prevStepStatus !== "needs_you" && curStepStatus === "needs_you") toast(lane + " needs you");
          // completion
          if (prev && prev.step !== "done" && s.step === "done") toast(lane + " shipped");
        }
      }
    }));
    Promise.all(promises).then(() => {
      // render cards
      // find slots? We render into stored slots. Simpler: re-render all known slots.
      if (window.__scSlots) {
        for (const slot of window.__scSlots) {
          slot.replaceChildren();
          for (const lane of laneNames) {
            const st = states[lane];
            if (!st || st.status === "idle") {
              if (releaseLanes.indexOf(lane) !== -1) {
                const btn = document.createElement("button");
                btn.className = "sc-btn primary";
                btn.textContent = "Ship " + lane;
                btn.addEventListener("click", () => {
                  fetch("/api/ship/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lane }) })
                    .then(() => refreshAll());
                });
                slot.appendChild(btn);
              }
              continue;
            }
            const c = renderCard(slot, lane);
            if (c) slot.appendChild(c);
          }
        }
      }
      updatePill();
      scheduleLoop();
    });
  }

  function anyActive() {
    return laneNames.some((l) => {
      const s = states[l];
      return s && (s.status === "running" || s.status === "needs_you");
    });
  }

  function scheduleLoop() {
    if (loopTimer) { clearTimeout(loopTimer); loopTimer = null; }
    if (!anyActive()) return;
    if (document.visibilityState !== "visible") return;
    loopTimer = setTimeout(() => {
      if (document.visibilityState === "visible" && anyActive()) refreshAll();
    }, 5000);
  }

  function mountCards(slot, lanes, releaseLaneNames) {
    injectStyle();
    if (!window.__scSlots) window.__scSlots = [];
    if (window.__scSlots.indexOf(slot) === -1) window.__scSlots.push(slot);
    laneNames = lanes || [];
    releaseLanes = releaseLaneNames || [];
    if (!seeded) {
      seeded = true;
      fetch("/api/lanes").then((r) => r.json()).then((d) => {
        if (d && Array.isArray(d.lanes)) {
          // seed names if not provided
          if (!laneNames.length) laneNames = d.lanes.map((l) => l.name);
        }
        refreshAll();
        prevStates = JSON.parse(JSON.stringify(states));
      }).catch(() => { refreshAll(); });
    } else {
      refreshAll();
    }
    ensurePill();
  }

  function initOnLoad() {
    injectStyle();
    ensurePill();
    if (seeded) return;
    seeded = true;
    fetch("/api/lanes").then((r) => r.json()).then((d) => {
      if (d && Array.isArray(d.lanes) && !laneNames.length) laneNames = d.lanes.map((l) => l.name);
      refreshAll();
      prevStates = JSON.parse(JSON.stringify(states));
    }).catch(() => {});
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initOnLoad);
  else initOnLoad();

  window.SubwayShip = { mountCards };
})();
