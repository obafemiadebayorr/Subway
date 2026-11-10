// hud/tether-sheet.js - Per-provider tether pod sheet
import { api, duration, h, icon, s, toast } from "./lib.js";
import { steerFromComposer } from "./ai-prompt.js";

let sheetEl = null;
let currentAnchor = null;
let currentOpts = null;

const CSS = `
.tt-pod-sheet {
  position: fixed; left: 0; right: 0; bottom: 0; max-height: 80vh;
  background: var(--bg-surface, var(--bg, Canvas)); color: var(--text, CanvasText);
  border-top: 0.0625rem solid var(--border-subtle, var(--border, currentColor));
  border-radius: 0.75rem 0.75rem 0 0; box-shadow: 0 -0.5rem 1.5rem var(--shadow, rgba(0, 0, 0, 0.2));
  display: flex; flex-direction: column; z-index: 100; overflow: hidden;
}
@media (min-width: 64rem) {
  .tt-pod-sheet {
    left: auto; right: 1.5rem; bottom: 1.5rem; width: 24rem; max-height: calc(100vh - 3rem);
    border: 0.0625rem solid var(--border-subtle, var(--border, currentColor));
    border-radius: 0.75rem; box-shadow: 0 0.5rem 2rem var(--shadow, rgba(0, 0, 0, 0.25));
  }
}
.tt-pod-sheet[hidden] { display: none; }
.tt-pod-sheet-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.75rem 1rem; border-bottom: 0.0625rem solid var(--border-subtle, var(--border, transparent));
}
.tt-pod-sheet-title {
  margin: 0; font-size: 1rem; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tt-pod-sheet-close {
  background: transparent; border: none; cursor: pointer; color: inherit;
  padding: 0.25rem 0.5rem; border-radius: 0.25rem; display: flex;
  align-items: center; justify-content: center; width: 2rem; height: 2rem;
}
.tt-pod-sheet-close svg { width: 1.25rem; height: 1.25rem; }
.tt-pod-sheet-body { padding: 0.75rem 1rem; overflow-y: auto; flex: 1 1 auto; }
.tt-pod-sheet-actions {
  display: flex; gap: 0.5rem; padding: 0.75rem 1rem;
  border-top: 0.0625rem solid var(--border-subtle, var(--border, transparent)); flex-wrap: wrap;
}
.tt-pod-sheet-list {
  display: flex; flex-direction: column; gap: 0.5rem; list-style: none; margin: 0; padding: 0;
}
.tt-pod-chat {
  display: flex; flex-direction: column; align-items: stretch;
  border-radius: 0.375rem;
  background: var(--bg-hover, rgba(127, 127, 127, 0.08));
}
.tt-pod-chat-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.5rem 0.75rem; cursor: pointer; user-select: none; touch-action: none;
  border-radius: 0.375rem; width: 100%; box-sizing: border-box;
  background: transparent; border: none; color: inherit; text-align: left; font: inherit;
}
.tt-pod-chat-row:focus-visible {
  outline: 0.125rem solid var(--border-focus, var(--accent, currentColor));
  outline-offset: 0.0625rem;
}
.tt-pod-chat.is-dragging .tt-pod-chat-row { opacity: 0.65; }
.tt-pod-chat-info {
  display: flex; flex-direction: column; gap: 0.125rem; min-width: 0; flex: 1 1 auto;
}
.tt-pod-chat-title {
  font-weight: 500; font-size: 0.875rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tt-pod-chat-meta { font-size: 0.75rem; color: var(--text-muted, gray); }
.tt-pod-chat-decisions {
  padding: 0 0.75rem 0.5rem; font-size: 0.8125rem; color: var(--text-secondary, var(--text, inherit));
  white-space: pre-wrap; word-break: break-word;
}
.tt-pod-chat-decisions[hidden] { display: none; }
.tt-pod-chat-decisions-status { color: var(--text-muted, gray); }
.tt-pod.is-drop-target { outline: 0.125rem dashed var(--accent, var(--border-focus, currentColor)); outline-offset: 0.125rem; }
.tt-pod-sheet-empty {
  font-size: 0.875rem; color: var(--text-muted, gray); text-align: center; padding: 1.5rem 0;
}
.tt-pod-sheet-btn {
  padding: 0.375rem 0.75rem; font-size: 0.875rem; border-radius: 0.375rem;
  border: 0.0625rem solid var(--border-subtle, var(--border, currentColor));
  background: var(--bg-surface, transparent); color: inherit; cursor: pointer;
}
.tt-pod-sheet-btn:disabled { opacity: 0.5; cursor: not-allowed; }
`;

function injectStyles() {
  if (document.getElementById("tt-pod-sheet-styles")) return;
  const tag = document.createElement("style");
  tag.id = "tt-pod-sheet-styles";
  tag.textContent = CSS;
  document.head.appendChild(tag);
}

function formatAge(timestamp) {
  if (!timestamp) return "";
  let epoch = typeof timestamp === "number" ? timestamp : Date.parse(timestamp) / 1000;
  if (epoch > 1e11) epoch = epoch / 1000;
  if (!epoch || Number.isNaN(epoch)) return String(timestamp);
  const now = Date.now() / 1000;
  const delta = Math.max(0, now - epoch);
  return `${duration(delta)} ago`;
}

function decisionsTextFromResult(res) {
  if (res == null) return "";
  if (typeof res === "string") return res;
  if (typeof res.text === "string") return res.text;
  if (typeof res.result === "string") return res.result;
  if (res.result != null && typeof res.result !== "string") return decisionsTextFromResult(res.result);
  if (Array.isArray(res.decisions)) return res.decisions.map(String).join("\n");
  if (Array.isArray(res.content)) {
    return res.content.map((c) => (c && typeof c.text === "string" ? c.text : "")).filter(Boolean).join("\n");
  }
  try { return JSON.stringify(res, null, 2); } catch { return String(res); }
}

async function fetchChatDecisions(chatId) {
  const attempts = [
    { tool: "read_chat", arguments: { chat_id: chatId, mode: "decisions", turns: 10 } },
    { name: "read_chat", params: { chat_id: chatId, mode: "decisions", turns: 10 } }
  ];
  let lastErr;
  for (const body of attempts) {
    try {
      const res = await api("/api/mcp/call", body);
      if (res && typeof res === "object" && res.error && !res.result && !res.text && !res.content && !res.decisions) {
        lastErr = new Error(typeof res.error === "string" ? res.error : "Could not load decisions");
        continue;
      }
      return decisionsTextFromResult(res);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("Could not load decisions");
}

export function mountTetherSheet(host) {
  if (!host) return null;
  injectStyles();
  if (sheetEl && sheetEl.parentElement) {
    sheetEl.remove();
  }
  sheetEl = h("div", {
    class: "tt-pod-sheet",
    role: "dialog",
    "aria-modal": "true",
    hidden: true
  });

  host.appendChild(sheetEl);

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && sheetEl && !sheetEl.hidden) {
      closeTetherSheet();
    }
  });

  return sheetEl;
}

export function closeTetherSheet() {
  if (!sheetEl) return;
  sheetEl.hidden = true;
  if (currentAnchor) {
    currentAnchor.setAttribute("aria-expanded", "false");
    if (typeof currentAnchor.focus === "function") {
      currentAnchor.focus();
    }
    currentAnchor = null;
  }
  currentOpts = null;
}

export async function openTetherSheet(opts) {
  if (!sheetEl) return;
  const { providerId, name, anchor, principalId } = opts || {};
  currentOpts = opts;
  currentAnchor = anchor || null;

  if (currentAnchor) {
    currentAnchor.setAttribute("aria-expanded", "true");
  }

  sheetEl.hidden = false;
  sheetEl.innerHTML = "";

  const titleText = name || providerId || "Provider";

  // Head
  const closeBtn = h("button", {
    type: "button",
    class: "tt-pod-sheet-close",
    "aria-label": "Close",
    on: {
      pointerup: () => closeTetherSheet()
    }
  }, icon("x"));

  const head = h("div", { class: "tt-pod-sheet-head" },
    h("h3", { class: "tt-pod-sheet-title" }, titleText),
    closeBtn
  );
  sheetEl.appendChild(head);

  // Body
  const body = h("div", { class: "tt-pod-sheet-body" },
    h("div", { class: "tt-pod-sheet-empty" }, "Loading chats...")
  );
  sheetEl.appendChild(body);

  // Actions
  const currentThreadEl = document.querySelector(".tt-composer-field [data-thread]");
  const currentThread = currentThreadEl ? currentThreadEl.dataset.thread : null;

  const principalBtn = h("button", {
    type: "button",
    class: "tt-pod-sheet-btn",
    disabled: !currentThread,
    title: currentThread ? "Make principal" : "No open thread",
    on: {
      pointerup: async () => {
        if (!currentThread) return;
        principalBtn.disabled = true;
        try {
          await api("/api/composer/principal", {
            thread: currentThread,
            principal_id: providerId
          });
          sheetEl.dispatchEvent(new CustomEvent("tt:principal", {
            bubbles: true,
            detail: { providerId, name: titleText }
          }));
        } catch (err) {
          console.error("Failed to set principal", err);
        } finally {
          principalBtn.disabled = false;
        }
      }
    }
  }, "Make principal");

  const newChatBtn = h("button", {
    type: "button",
    class: "tt-pod-sheet-btn",
    on: {
      pointerup: async () => {
        if (newChatBtn.disabled) return;
        newChatBtn.disabled = true;
        try {
          await api("/api/spawn", {
            role: `New ${name || providerId} chat`,
            provider: providerId,
            effort: "low",
            prompt: (document.querySelector(".tt-composer-field .cmp-input")?.value || "").trim() || "New chat: wait for the user's first message, then work on it."
          });
          toast("Queued for approval");
        } catch (err) {
          console.error("Failed to spawn new chat", err);
          const prev = newChatBtn.textContent;
          newChatBtn.textContent = "New chat failed";
          setTimeout(() => { if (sheetEl?.isConnected) newChatBtn.textContent = prev; }, 2000);
        } finally {
          newChatBtn.disabled = false;
        }
      }
    }
  }, "+ New chat");

  const actions = h("div", { class: "tt-pod-sheet-actions" },
    principalBtn,
    newChatBtn
  );
  sheetEl.appendChild(actions);

  // Fetch chats
  try {
    const res = await api("/api/chats?source=" + encodeURIComponent(providerId || "") + "&limit=50");
    const chats = (res && res.chats) || (Array.isArray(res) ? res : []);
    body.innerHTML = "";

    if (!chats.length) {
      body.appendChild(h("div", { class: "tt-pod-sheet-empty" }, "No recent chats"));
    } else {
      const list = h("ul", { class: "tt-pod-sheet-list" });

      for (const chat of chats) {
        const id = chat.id || chat.thread_id || chat.session_id || "";
        const itemTitle = chat.title || chat.name || id || "Untitled";
        const timeVal = chat.updated_at || chat.updated || chat.created_at || chat.time || chat.last_ts;
        const timeStr = formatAge(timeVal);
        const payload = { thread: id, title: itemTitle };

        const decisions = h("div", {
          class: "tt-pod-chat-decisions",
          hidden: "",
          "aria-live": "polite"
        });
        decisions.hidden = true;

        let decisionsLoaded = false;
        let decisionsLoading = false;
        let decisionsOpen = false;
        let didDrag = false;
        let rowBtn;

        const syncAria = () => {
          if (rowBtn) rowBtn.setAttribute("aria-expanded", decisionsOpen ? "true" : "false");
        };

        const setDecisionsStatus = (text) => {
          decisions.innerHTML = "";
          const status = document.createElement("div");
          status.className = "tt-pod-chat-decisions-status";
          status.textContent = text;
          decisions.appendChild(status);
        };

        const toggleDecisions = async () => {
          if (decisionsLoading) return;
          decisionsOpen = !decisionsOpen;
          decisions.hidden = !decisionsOpen;
          syncAria();
          if (!decisionsOpen || decisionsLoaded) return;
          decisionsLoading = true;
          setDecisionsStatus("Loading decisions...");
          try {
            const text = await fetchChatDecisions(id);
            decisions.innerHTML = "";
            decisions.textContent = text && String(text).trim() ? String(text) : "No decisions found.";
            decisionsLoaded = true;
          } catch (err) {
            console.error("Failed to load decisions", err);
            setDecisionsStatus("Could not load decisions");
          } finally {
            decisionsLoading = false;
          }
        };

        let dragState = null;
        let dropTarget = null;
        const clearDropTarget = () => {
          if (dropTarget) {
            dropTarget.classList.remove("is-drop-target");
            dropTarget = null;
          }
        };
        const podAtPoint = (x, y) => {
          const els = document.elementsFromPoint(x, y) || [];
          return els.find((el) => el && el.classList && el.classList.contains("tt-pod")) || null;
        };
        const onPointerMove = (e) => {
          if (!dragState || e.pointerId !== dragState.pointerId) return;
          const dx = e.clientX - dragState.startX;
          const dy = e.clientY - dragState.startY;
          if (!dragState.dragging && Math.hypot(dx, dy) > 7) {
            dragState.dragging = true;
            row.classList.add("is-dragging");
          }
          if (dragState.dragging) {
            clearDropTarget();
            dropTarget = podAtPoint(e.clientX, e.clientY);
            if (dropTarget) dropTarget.classList.add("is-drop-target");
          }
        };
        const finishDrag = (e, cancelled) => {
          if (!dragState || e.pointerId !== dragState.pointerId) return;
          rowBtn.removeEventListener("pointermove", onPointerMove);
          rowBtn.removeEventListener("pointerup", onPointerUp);
          rowBtn.removeEventListener("pointercancel", onPointerCancel);
          try { rowBtn.releasePointerCapture(dragState.pointerId); } catch (_) {}
          const wasDragging = dragState.dragging;
          clearDropTarget();
          row.classList.remove("is-dragging");
          dragState = null;
          if (wasDragging) {
            didDrag = true;
            if (!cancelled) {
              const pod = podAtPoint(e.clientX, e.clientY);
              if (pod) {
                const targetProviderId = pod.dataset.provider || pod.dataset.providerId || pod.getAttribute("data-provider") || providerId || "";
                const detail = { providerId: targetProviderId, payload };
                pod.dispatchEvent(new CustomEvent("tt:mail-drop", { bubbles: true, detail }));
              }
            }
          } else if (!cancelled && e.button === 0) {
            toggleDecisions();
          }
        };
        const onPointerUp = (e) => finishDrag(e, false);
        const onPointerCancel = (e) => finishDrag(e, true);

        const continueBtn = h("button", {
          type: "button",
          class: "tt-pod-sheet-btn",
          on: {
            pointerdown: (ev) => {
              ev.stopPropagation();
            },
            click: (ev) => {
              ev.stopPropagation();
              steerFromComposer({ kind: "chat", id, label: itemTitle || id, sub: providerId }, "threads");
              closeTetherSheet();
              requestAnimationFrame(() => {
                const i = document.querySelector('.tt-composer-field .cmp-input');
                if (i) i.focus();
              });
            }
          }
        }, "Continue");

        rowBtn = h("button", {
          type: "button",
          class: "tt-pod-chat-row",
          "aria-expanded": "false",
          on: {
            pointerdown: (e) => {
              if (e.button !== 0) return;
              didDrag = false;
              dragState = {
                pointerId: e.pointerId,
                startX: e.clientX,
                startY: e.clientY,
                dragging: false
              };
              try { rowBtn.setPointerCapture(e.pointerId); } catch (_) {}
              rowBtn.addEventListener("pointermove", onPointerMove);
              rowBtn.addEventListener("pointerup", onPointerUp);
              rowBtn.addEventListener("pointercancel", onPointerCancel);
            },
            click: (e) => {
              if (didDrag) {
                e.preventDefault();
                e.stopPropagation();
                didDrag = false;
                return;
              }
              // Keyboard activation (no pointer drag state) toggles decisions.
              if (!dragState) toggleDecisions();
            }
          }
        },
          h("div", { class: "tt-pod-chat-info" },
            h("div", { class: "tt-pod-chat-title" }, itemTitle),
            timeStr ? h("div", { class: "tt-pod-chat-meta" }, timeStr) : null
          ),
          continueBtn
        );

        const row = h("li", { class: "tt-pod-chat" }, rowBtn, decisions);
        list.appendChild(row);
      }
      body.appendChild(list);
    }
  } catch (err) {
    body.innerHTML = "";
    body.appendChild(h("div", { class: "tt-pod-sheet-empty" }, "Failed to load chats"));
  }
}
