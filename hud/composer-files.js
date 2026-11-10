// PKG-F2 — file suggestions and packet weighing. Exports: fileSuggestions, weighPackets, exportThread.
const TOKEN = document.querySelector('meta[name="subway-token"]')?.content || "";
const DEBOUNCE_MS = 120;
const MAX_ROWS = 8;

function headers() {
  return { "X-Subway-Token": TOKEN, "Content-Type": "application/json" };
}

export async function fileSuggestions(query, projectDir) {
  const params = new URLSearchParams({ q: query || "" });
  if (projectDir) params.set("project_dir", projectDir);
  const res = await fetch(`/api/composer/project_files?${params}`, {
    method: "GET", cache: "no-store", headers: { "X-Subway-Token": TOKEN },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return (data.files || []).slice(0, MAX_ROWS).map((f) => ({
    path: f.path, lines: f.lines, locked: !!f.locked, locked_by: f.locked_by || null,
  }));
}

export async function weighPackets(specs) {
  const res = await fetch("/api/composer/context_slice", {
    method: "POST", cache: "no-store",
    headers: { "X-Subway-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ specs }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `Request failed (${res.status})`);
  const slices = data.slices || [];
  const missing = [];
  for (const s of slices) if (s.missing || s.error) missing.push(s.spec || s.path);
  return { tokens: data.tokens || 0, slices, missing };
}

export async function exportThread(threadId, format) {
  const params = new URLSearchParams({ thread: threadId || "", format: format || "md" });
  const res = await fetch(`/api/composer/thread_export?${params}`, {
    method: "GET", cache: "no-store", headers: { "X-Subway-Token": TOKEN },
  });
  if (res.status === 409) throw new Error("Thread is not exportable yet.");
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `Request failed (${res.status})`);
  const mime = format === "json" ? "application/json" : format === "patch" ? "text/x-diff" : "text/markdown";
  const ext = format === "json" ? "json" : format === "patch" ? "patch" : "md";
  return {
    filename: data.filename || `${threadId}.${ext}`,
    mime: data.mime || mime,
    body: data.body || "",
  };
}

// Debounced wrapper over fileSuggestions for the composer UI (120 ms, 8 rows).
let timer = null;
let lastArgs = null;
let lastPromise = null;
export function fileSuggestionsDebounced(query, projectDir) {
  lastArgs = { query, projectDir };
  if (timer) clearTimeout(timer);
  lastPromise = new Promise((resolve, reject) => {
    timer = setTimeout(() => {
      timer = null;
      fileSuggestions(lastArgs.query, lastArgs.projectDir).then(resolve, reject);
    }, DEBOUNCE_MS);
  });
  return lastPromise;
}
