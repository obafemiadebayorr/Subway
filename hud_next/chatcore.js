// hud_next/chatcore.js - Single data layer for Home panel and Composer (window.SubwayChat)

// Cache duration for chats list before background revalidation (ms)
const CHAT_LIST_STALE_MS = 30000;
// Stale duration for per-chat context before re-fetching (ms)
const CHAT_CONTEXT_STALE_MS = 15000;
// Stale duration for per-chat read cache before background revalidation (ms)
const CHAT_CACHE_STALE_MS = 10000;
// Turns loaded when a chat opens; the panel grows it with "Load older messages"
const CHAT_DEFAULT_TURNS = 20;
// Initial retry delay for SSE stream reconnection on readyState 2 (ms)
const SSE_RETRY_DELAY_MS = 1500;
// Backoff factor for SSE stream reconnection
const SSE_BACKOFF_FACTOR = 1.5;
// Maximum backoff delay for SSE stream reconnection (ms)
const SSE_BACKOFF_MAX_MS = 15000;
// Queues refresh on "mail" events from the HUD event stream (push.js), debounced by this much (ms). No polling.
const QUEUE_EVENT_DEBOUNCE_MS = 250;

// Terminal states for mail items
const MAIL_TERMINAL_STATES = new Set(["done", "dropped", "cancelled", "failed"]);

// Helper for making requests with {cache: "no-store"} and surfacing JSON errors as {ok: false, error}
async function request(url, options = {}) {
  const fetchFn = globalThis.fetch;
  if (typeof fetchFn !== "function") {
    return { ok: false, error: "fetch is not available" };
  }
  const opts = {
    ...options,
    cache: "no-store",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  };
  try {
    const res = await fetchFn(url, opts);
    let data;
    try {
      data = await res.json();
    } catch (_) {
      data = {};
    }
    if (!res.ok) {
      return { ok: false, error: (data && (data.error || data.message)) || `HTTP ${res.status}` };
    }
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: (err && err.message) || String(err) };
  }
}

class SubwayChatStore {
  constructor() {
    this._chatList = [];
    this._chatListAt = 0;
    this._listSubscribers = new Set();
    this._chatStates = new Map(); // id -> state
    this._chatSubscribers = new Map(); // id -> Set of callbacks
    this._streams = new Map(); // id -> { es, source, retryDelay, retryTimer }
    this._openChats = new Map(); // id -> source
    this._visible = true;
    this._microtaskQueued = new Set();
    this._skillsCache = null;
    this._hubServersCache = null;
    this._queueRefreshTimer = 0;
    // push.js re-emits each /api/events event as "subway:event"; postmail emits "mail" on every item change.
    const bus = typeof window !== "undefined" ? window : globalThis;
    if (bus && typeof bus.addEventListener === "function") {
      bus.addEventListener("subway:event", (e) => {
        const n = e && e.detail && e.detail.name;
        if (n === "mail" || n === "connect") this._refreshQueues();
        if (n === "message" || n === "status" || n === "connect") {
          for (const id of this._openChats.keys()) {
            const st = this._chats.get(id);
            if (st && st.starting) this._refetchChat(id);
          }
        }
      });
    }
    this._queueItems = new Map(); // chatId -> items
    this._queueSubscribers = new Map(); // chatId -> Set of callbacks

    // Queue API namespace
    this.queue = {
      list: (chatId) => this._queueList(chatId),
      add: (chatId, params) => this._queueAdd(chatId, params),
      edit: (mailId, fields) => this._queueEdit(mailId, fields),
      cancel: (mailId, reason) => this._queueCancel(mailId, reason),
      sendNow: (mailId) => this._queueSendNow(mailId),
      subscribe: (chatId, fn) => this._subscribeQueue(chatId, fn),
    };

    // Cached actions refresh helpers
    this.skills.refresh = () => this.skills({ refresh: true });
    this.hubServers.refresh = () => this.hubServers({ refresh: true });
  }

  // --- Subscriptions & Batching ---

  _notifyChat(id) {
    if (!this._chatSubscribers.has(id) || this._chatSubscribers.get(id).size === 0) return;
    if (this._microtaskQueued.has(id)) return;
    this._microtaskQueued.add(id);
    const queueMicro = typeof queueMicrotask === "function" ? queueMicrotask : (fn) => Promise.resolve().then(fn);
    queueMicro(() => {
      this._microtaskQueued.delete(id);
      const state = this.get(id);
      const subs = this._chatSubscribers.get(id);
      if (subs) {
        for (const fn of Array.from(subs)) {
          try { fn(state); } catch (_) { /* ignore callback errors */ }
        }
      }
    });
  }

  _notifyList() {
    const list = this._chatList;
    for (const fn of Array.from(this._listSubscribers)) {
      try { fn(list); } catch (_) { /* ignore callback errors */ }
    }
  }

  subscribeList(fn) {
    this._listSubscribers.add(fn);
    return () => this._listSubscribers.delete(fn);
  }

  subscribe(id, fn) {
    if (!this._chatSubscribers.has(id)) {
      this._chatSubscribers.set(id, new Set());
    }
    this._chatSubscribers.get(id).add(fn);
    return () => {
      const set = this._chatSubscribers.get(id);
      if (set) {
        set.delete(fn);
        if (set.size === 0) this._chatSubscribers.delete(id);
      }
    };
  }

  get(id) {
    let state = this._chatStates.get(id);
    if (!state) {
      state = {
        id,
        source: "",
        turns: [],
        total: 0,
        cut: null,
        reverted: 0,
        context: null,
        d: null,
        live: { text: "", running: false, user: "", activity: [] },
        pending: [],
        conversation: null,
        error: null,
        _readAt: 0,
        _ctxAt: 0,
        _turns: CHAT_DEFAULT_TURNS,
      };
      this._chatStates.set(id, state);
    }
    return state;
  }

  // Apply a /api/chat_read or /api/chat_stream payload. The stream sends a short tail,
  // so with merge=true the older rows already loaded are kept in front of it.
  _apply(state, d, merge = false) {
    d = d || {};
    let turns = Array.isArray(d.turns) ? d.turns : [];
    const prev = state.turns || [];
    if (merge && turns.length && prev.length > turns.length) {
      const f = turns[0];
      const k = prev.findIndex((m) => m.role === f.role && m.text === f.text);
      if (k > 0) turns = prev.slice(0, k).concat(turns);
    }
    state.turns = turns;
    state.total = d.total !== undefined ? d.total : turns.length;
    if (merge) {
      if (d.cut !== undefined) state.cut = d.cut;
      if (d.reverted !== undefined) state.reverted = d.reverted;
    } else {
      state.cut = d.cut !== undefined ? d.cut : null;
      state.reverted = d.reverted !== undefined ? d.reverted : 0;
    }
    if (d.live || merge) {
      const L = d.live || {};
      state.live = {
        text: L.text || "",
        running: Boolean(L.running !== undefined ? L.running : L.working),
        user: L.user || "",
        activity: L.activity || [],
      };
    }
    if (d.conversation) state.conversation = d.conversation;
    const checkpoints = d.checkpoints || (state.d && state.d.checkpoints) || [];
    state.d = { ...d, turns, total: state.total, cut: state.cut, reverted: state.reverted, checkpoints };
    state.error = null;
    state._readAt = Date.now();
  }

  // Paint a chat at once from a saved copy; it stays stale so open() still fetches.
  seed(id, d) {
    const state = this.get(id);
    if (state.d || !d) return state;
    this._apply(state, d);
    state._readAt = 0;
    return state;
  }

  refresh(id) {
    const state = this.get(id);
    state._readAt = 0;
    return this._refetchChat(id);
  }

  // --- Chat list (GET /api/chats with stale-while-revalidate) ---

  async listChats({ force = false } = {}) {
    const now = Date.now();
    const isStale = now - this._chatListAt >= CHAT_LIST_STALE_MS;

    if (!force && this._chatListAt > 0) {
      if (isStale) {
        this._revalidateChats().catch(() => {});
      }
      return { ok: true, data: this._chatList };
    }
    return this._revalidateChats();
  }

  async _revalidateChats() {
    const res = await request("/api/chats?limit=2000");
    if (!res.ok) {
      return res;
    }
    const d = res.data;
    const raw = Array.isArray(d) ? d : (d && (d.threads || d.chats)) || [];
    this._chatList = raw.filter((c) => !c.worker && !c.subagent);
    this._chatListAt = Date.now();
    this._notifyList();
    return { ok: true, data: this._chatList };
  }

  // --- Chat open / close / visibility / SSE ---

  async open(id, source = "", opts = {}) {
    const state = this.get(id);
    if (source) state.source = source;
    this._openChats.set(id, state.source);
    const turns = Math.max(1, Number(opts.turns) || state._turns || CHAT_DEFAULT_TURNS);
    const grew = turns > state._turns;
    state._turns = turns;

    const hasCache = state._readAt > 0;
    const isStale = grew || !hasCache || (Date.now() - state._readAt >= CHAT_CACHE_STALE_MS);

    // Emit cached state at once if any
    if (hasCache) {
      this._notifyChat(id);
    }

    // Attach ONE EventSource to /api/chat_stream (reuse if already open)
    if (this._visible) {
      this._attachStream(id, state.source);
    }

    // When missing or stale, fetch read and context IN PARALLEL
    if (isStale) {
      const readPromise = request(`/api/chat_read?id=${encodeURIComponent(id)}&source=${encodeURIComponent(state.source || "")}&turns=${turns}`);
      const ctxPromise = request(`/api/chat_context?id=${encodeURIComponent(id)}`);

      const [readRes, ctxRes] = await Promise.all([readPromise, ctxPromise]);

      if (readRes.ok) {
        this._apply(state, readRes.data);
        state.starting = Boolean(readRes.data && readRes.data.starting);
        if (state.starting) state.error = null;
      } else {
        state.error = readRes.error;
      }

      if (ctxRes.ok) {
        state.context = ctxRes.data;
        state._ctxAt = Date.now();
      }

      this._updatePendingSteps(id);
      this._notifyChat(id);
    }

    return state;
  }

  close(id) {
    this._openChats.delete(id);
    this._detachStream(id);
  }

  setVisible(bool) {
    if (bool && !this._visible) this._refreshQueues();
    const visible = Boolean(bool);
    if (this._visible === visible) return;
    this._visible = visible;

    if (!this._visible) {
      for (const id of Array.from(this._streams.keys())) {
        this._detachStream(id);
      }
    } else {
      for (const [id, source] of this._openChats.entries()) {
        this._attachStream(id, source);
      }
    }
  }

  _attachStream(id, source, initialDelay = SSE_RETRY_DELAY_MS) {
    if (this._streams.has(id)) {
      return; // Reuse if already open
    }
    const ES = globalThis.EventSource;
    if (typeof ES !== "function") return;

    const url = `/api/chat_stream?id=${encodeURIComponent(id)}&source=${encodeURIComponent(source || "all")}`;
    const es = new ES(url);
    const streamInfo = {
      es,
      source,
      retryDelay: initialDelay,
      retryTimer: null,
    };
    this._streams.set(id, streamInfo);

    es.addEventListener("chat", (ev) => {
      let d;
      try {
        d = typeof ev.data === "string" ? JSON.parse(ev.data) : ev.data;
      } catch (_) {
        return;
      }
      const state = this.get(id);
      this._apply(state, d, true);
      this._updatePendingSteps(id);
      this._notifyChat(id);
    });

    es.addEventListener("delta", (ev) => {
      let d;
      try {
        d = typeof ev.data === "string" ? JSON.parse(ev.data) : ev.data;
      } catch (_) {
        return;
      }
      const state = this.get(id);
      streamInfo.pendingDelta = {
        text: d && d.text !== undefined ? d.text : (streamInfo.pendingDelta ? streamInfo.pendingDelta.text : undefined),
        running: d && d.running !== undefined ? Boolean(d.running) : (streamInfo.pendingDelta ? streamInfo.pendingDelta.running : undefined),
        deltaText: d ? d.text : "",
      };
      if (streamInfo.rafPending) return;
      streamInfo.rafPending = true;
      const flush = () => {
        streamInfo.rafPending = false;
        const pd = streamInfo.pendingDelta;
        streamInfo.pendingDelta = null;
        if (!pd) return;
        if (pd.text !== undefined) state.live.text = pd.text;
        if (pd.running !== undefined) state.live.running = Boolean(pd.running);
        this._updatePendingSteps(id, { isDelta: true, deltaText: pd.deltaText || "" });
        this._notifyChat(id);
      };
      if (typeof requestAnimationFrame === "function") requestAnimationFrame(flush);
      else flush();
    });

    es.addEventListener("error", () => {
      if (es.readyState === 2) {
        this._detachStream(id);
        if (this._openChats.has(id) && this._visible) {
          const delay = streamInfo.retryDelay;
          streamInfo.retryDelay = Math.min(delay * SSE_BACKOFF_FACTOR, SSE_BACKOFF_MAX_MS);
          streamInfo.retryTimer = setTimeout(async () => {
            if (!this._openChats.has(id) || !this._visible) return;
            await this._refetchChat(id);
            if (this._openChats.has(id) && this._visible) {
              this._attachStream(id, this._openChats.get(id), streamInfo.retryDelay);
            }
          }, delay);
        }
      }
    });
  }

  _detachStream(id) {
    const streamInfo = this._streams.get(id);
    if (streamInfo) {
      if (streamInfo.retryTimer) {
        clearTimeout(streamInfo.retryTimer);
      }
      if (streamInfo.es && typeof streamInfo.es.close === "function") {
        streamInfo.es.close();
      }
      this._streams.delete(id);
    }
  }

  async _refetchChat(id) {
    const state = this.get(id);
    const res = await request(`/api/chat_read?id=${encodeURIComponent(id)}&source=${encodeURIComponent(state.source || "")}&turns=${state._turns}`);
    if (res.ok) {
      this._apply(state, res.data);
      state.starting = Boolean(res.data && res.data.starting);
      if (state.starting) state.error = null;
      this._updatePendingSteps(id);
      this._notifyChat(id);
    } else if (!state.d) {
      state.error = res.error;
      this._notifyChat(id);
    }
  }

  // --- Send and pending delivery steps ---

  async send(id, { text, attachments = [], model = "", effort = "", provider = "", source = "" } = {}) {
    const state = this.get(id);
    const key = `send_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const at = Date.now();
    const pendingEntry = {
      key,
      text,
      at,
      steps: [],
      error: null,
      baseTotal: state.total || 0,
      baseTurnsCount: (state.turns || []).length,
    };
    Object.defineProperty(pendingEntry, "step", {
      get() { return this.steps[this.steps.length - 1] || null; },
    });
    Object.defineProperty(pendingEntry, "status", {
      get() { return this.steps[this.steps.length - 1] || null; },
    });

    state.pending.push(pendingEntry);

    const payload = {
      chat_id: id,
      text,
      source: source || state.source || "",
      model: model || "",
      effort: effort || "",
      provider: provider || "",
      attachments: attachments || [],
    };

    const res = await request("/api/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      pendingEntry.steps.push("sent");
      this._updatePendingSteps(id);
      this._notifyChat(id);
      return { ok: true, data: res.data };
    } else {
      pendingEntry.error = res.error;
      this._notifyChat(id);
      return { ok: false, error: res.error };
    }
  }

  _updatePendingSteps(id, extra = {}) {
    const state = this.get(id);
    if (!state.pending || state.pending.length === 0) return;

    const turns = state.turns || [];
    const live = state.live || { text: "", running: false, user: "", activity: [] };

    for (const p of state.pending) {
      if (p.error || p.steps.includes("replied")) continue;

      const pText = (p.text || "").trim();

      // 1. "in chat": user turn/live.user matching text arrives
      if (p.steps.includes("sent") && !p.steps.includes("in chat")) {
        const matchLive = live.user && (
          pText.startsWith(live.user.trim().slice(0, 60)) ||
          live.user.trim().startsWith(pText.slice(0, 60))
        );
        const matchTurn = turns.some((t) =>
          t.role === "user" &&
          t.text &&
          (t.text.trim().startsWith(pText.slice(0, 60)) || pText.startsWith(t.text.trim().slice(0, 60)))
        );
        if (matchLive || matchTurn) {
          p.steps.push("in chat");
        }
      }

      // 2. "replying": first non-empty delta or new assistant turn
      if (p.steps.includes("sent") && !p.steps.includes("replying")) {
        const hasDelta = (extra.isDelta && extra.deltaText && extra.deltaText.trim()) ||
                         (live.text && live.text.trim());
        const hasNewAssistantTurn = turns.slice(p.baseTurnsCount).some((t) => t.role === "assistant") ||
                                    (turns.some((t) => t.role === "assistant") && (state.total || turns.length) > p.baseTotal);
        if (hasDelta || hasNewAssistantTurn) {
          if (!p.steps.includes("in chat")) {
            p.steps.push("in chat");
          }
          p.steps.push("replying");
        }
      }

      // 3. "replied": running false and an assistant turn newer than the send
      if (p.steps.includes("sent") && !p.steps.includes("replied")) {
        const notRunning = !live.running;
        const hasAssistantReply = turns.slice(p.baseTurnsCount).some((t) => t.role === "assistant") ||
                                  (turns.some((t) => t.role === "assistant") && (state.total || turns.length) > p.baseTotal);
        if (notRunning && hasAssistantReply) {
          if (!p.steps.includes("in chat")) p.steps.push("in chat");
          if (!p.steps.includes("replying")) p.steps.push("replying");
          p.steps.push("replied");
          p.done = true;
        }
      }
    }
  }

  // --- Actions returning {ok, data|error} ---

  // POST /api/chat/new needs the provider and the first message (text); it starts the session with it.
  async newChat({ provider = "", source = "", text = "", model = "", effort = "", project_dir = "", no_folder = false } = {}) {
    const res = await request("/api/chat/new", {
      method: "POST",
      body: JSON.stringify({ provider: provider || source, text, model, effort, project_dir, no_folder: Boolean(no_folder) }),
    });
    if (res.ok) {
      this.listChats({ force: true }).catch(() => {});
    }
    return res;
  }

  async swap(id, options = {}) {
    let to_provider = "";
    let model = "";
    let effort = "";
    let text = "";
    if (typeof options === "string") {
      to_provider = options;
    } else if (options && typeof options === "object") {
      to_provider = options.provider || options.to_provider || "";
      model = options.model || "";
      effort = options.effort || "";
      text = options.text || "";
    }
    return request("/api/conversation/swap", {
      method: "POST",
      body: JSON.stringify({
        chat_id: id,
        to_provider,
        model,
        effort,
        text,
      }),
    });
  }

  async cut(id, tid) {
    const res = await request("/api/thread_cut", {
      method: "POST",
      body: JSON.stringify({ id, tid }),
    });
    if (res.ok) {
      const state = this.get(id);
      state._readAt = 0;
    }
    return res;
  }

  async uncut(id) {
    const res = await request("/api/thread_cut", {
      method: "POST",
      body: JSON.stringify({ id, tid: "" }),
    });
    if (res.ok) {
      const state = this.get(id);
      state._readAt = 0;
    }
    return res;
  }

  async fork(id, tid, provider = "", text = "") {
    const res = await request("/api/thread_fork", {
      method: "POST",
      body: JSON.stringify({ id, tid, provider, text }),
    });
    if (res.ok) {
      this.listChats({ force: true }).catch(() => {});
    }
    return res;
  }

  async compact(id) {
    const res = await request("/api/chat_compact", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    if (res.ok) {
      const state = this.get(id);
      state._ctxAt = 0;
    }
    return res;
  }

  async upload(file) {
    if (!file) return { ok: false, error: "file is required" };
    let b64 = file.data_base64 || file.base64 || "";
    if (!b64 && typeof file.arrayBuffer === "function") {
      try {
        const buf = await file.arrayBuffer();
        if (typeof Buffer !== "undefined") {
          b64 = Buffer.from(buf).toString("base64");
        } else if (typeof btoa === "function") {
          let binary = "";
          const bytes = new Uint8Array(buf);
          for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
          }
          b64 = btoa(binary);
        }
      } catch (e) {
        return { ok: false, error: (e && e.message) || String(e) };
      }
    }
    return request("/api/composer/upload", {
      method: "POST",
      body: JSON.stringify({
        name: file.name || "upload",
        type: file.type || "application/octet-stream",
        data_base64: b64,
      }),
    });
  }

  async skills({ refresh = false } = {}) {
    if (!refresh && this._skillsCache !== null) {
      return { ok: true, data: this._skillsCache };
    }
    const res = await request("/api/skills");
    if (res.ok) {
      this._skillsCache = res.data;
    }
    return res;
  }

  async apps({ refresh = false } = {}) {
    if (!refresh && this._appsCache !== null) {
      return { ok: true, data: this._appsCache };
    }
    const res = await request("/api/apps");
    if (res.ok) {
      this._appsCache = res.data;
    }
    return res;
  }

  async connectApp(id) {
    const res = await request("/api/apps/connect", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    this._appsCache = null;
    return res;
  }

  async disconnectApp(id) {
    const res = await request("/api/apps/disconnect", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    this._appsCache = null;
    return res;
  }

  async hubServers({ refresh = false } = {}) {
    if (!refresh && this._hubServersCache !== null) {
      return { ok: true, data: this._hubServersCache };
    }
    const res = await request("/api/mcp/hub/servers");
    if (res.ok) {
      this._hubServersCache = res.data;
    }
    return res;
  }

  // --- Queue (server-side, postmail) ---

  async _queueList(chatId) {
    const res = await request("/api/mail?state=active&limit=500");
    if (!res.ok) return res;
    const rawItems = res.data && res.data.items ? res.data.items : (Array.isArray(res.data) ? res.data : []);
    const allItems = Array.isArray(rawItems) ? rawItems : Object.values(rawItems);

    let chatItems = allItems;
    if (chatId) {
      chatItems = allItems.filter((it) => {
        const delivChat = it.deliver && it.deliver.chat_id;
        const condChat = it.condition && it.condition.chat_id;
        return delivChat === chatId || condChat === chatId;
      });
    }

    // "*" holds every item (the composer's All tab); a chat id holds that chat's items.
    const key = chatId || "*";
    this._queueItems.set(key, chatItems);
    this._notifyQueue(key);

    return { ok: true, data: chatItems };
  }

  // chatId "" queues a worker task for this thread; a chat id delivers the text into that chat.
  // condition_type/condition override the default (chat items wait for the chat to be idle).
  async _queueAdd(chatId, { text, source = "", model = "", effort = "", provider = "", attachments = [], when = {},
                            condition_type = "", condition = null, position = null } = {}) {
    let ctype = condition_type;
    let cond = condition;
    if (!ctype) {
      if (when && when.kind === "at") {
        ctype = "time";
        cond = { run_at: when.run_at, repeat: when.repeat || "once" };
      } else if (chatId) {
        ctype = "chat_idle";
        cond = { chat_id: chatId, source: (source || "").toLowerCase() };
      } else {
        ctype = "time";
        cond = { delay_seconds: 0, repeat: "once" };
      }
    }
    const payload = {
      role: chatId ? "Chat message" : "Queued task",
      prompt: text,
      condition_type: ctype,
      condition: cond || {},
      model: model || "",
      effort: effort || "",
    };
    if (provider) payload.provider = provider;
    if (position !== null && position !== undefined) payload.position = position;
    if (chatId) {
      payload.deliver = {
        kind: "chat",
        chat_id: chatId,
        source: (source || "").toLowerCase(),
        model: model || "",
        effort: effort || "",
        attachments: attachments || [],
      };
    }
    const res = await request("/api/mail", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (res.ok && chatId) {
      this._queueList(chatId).catch(() => {});
    }
    return res;
  }

  async _queueEdit(mailId, fields = {}) {
    return request("/api/mail/edit", {
      method: "POST",
      body: JSON.stringify({ mail_id: mailId, updates: fields, ...fields }),
    });
  }

  async _queueCancel(mailId, reason = "Cancelled by user/agent") {
    return request("/api/mail/cancel", {
      method: "POST",
      body: JSON.stringify({ mail_id: mailId, reason }),
    });
  }

  async _queueSendNow(mailId) {
    const now = Math.floor(Date.now() / 1000);
    return this._queueEdit(mailId, {
      condition_type: "time",
      condition: {
        type: "time",
        run_at: now,
        repeat: "once",
      },
    });
  }

  // Refresh every watched queue once after a burst of events (or on reconnect / becoming visible).
  _refreshQueues() {
    clearTimeout(this._queueRefreshTimer);
    this._queueRefreshTimer = setTimeout(() => {
      this._queueRefreshTimer = 0;
      for (const key of Array.from(this._queueSubscribers.keys())) {
        this._queueList(key === "*" ? "" : key).catch(() => {});
      }
    }, QUEUE_EVENT_DEBOUNCE_MS);
  }

  _notifyQueue(chatId) {
    const subs = this._queueSubscribers.get(chatId);
    if (subs) {
      const items = this._queueItems.get(chatId) || [];
      for (const fn of Array.from(subs)) {
        try { fn(items); } catch (_) {}
      }
    }
  }

  _subscribeQueue(chatId, fn) {
    if (!this._queueSubscribers.has(chatId)) {
      this._queueSubscribers.set(chatId, new Set());
    }
    this._queueSubscribers.get(chatId).add(fn);
    return () => {
      const subs = this._queueSubscribers.get(chatId);
      if (subs) {
        subs.delete(fn);
        if (subs.size === 0) this._queueSubscribers.delete(chatId);
      }
    };
  }
}

const SubwayChat = new SubwayChatStore();

if (typeof window !== "undefined") {
  window.SubwayChat = SubwayChat;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = SubwayChat;
}
