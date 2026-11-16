/* Reusable unified / side-by-side patch diff parser and renderer for hud_next.
   Styled after @kokonutui/ai-diff-review: collapsible file cards, split dir/base
   paths, per-file +/- change bars, and inset "well" scroll areas. */

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const DIFF_CSS = `
.df-wrap{display:flex;flex-direction:column;gap:12px;min-width:0}
.df-bar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.df-meta{font-size:12.5px;color:var(--muted);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.df-seg{display:inline-flex;gap:3px;padding:3px;border-radius:10px;background:var(--ground);box-shadow:inset 0 0 0 1px var(--hair)}
.df-seg button{border:0;background:none;padding:5px 10px;border-radius:7px;font:inherit;font-size:12px;font-weight:500;color:var(--muted);cursor:pointer}
.df-seg button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(17,17,17,.08)}
.df-file{background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair);overflow:hidden;min-width:0}
.df-fhead{display:flex;align-items:center;justify-content:space-between;gap:8px;background:var(--ground);border-bottom:1px solid var(--hair);font-size:12.5px;padding:0}
.df-ftoggle{flex:1;display:flex;align-items:center;gap:7px;min-width:0;border:0;background:none;font:inherit;font-size:12.5px;color:inherit;padding:8px 12px;cursor:pointer;text-align:left}
.df-chev{flex-shrink:0;color:var(--muted);transition:transform .2s cubic-bezier(0.23,1,0.32,1)}
.df-ftoggle[data-expanded="true"] .df-chev{transform:rotate(90deg)}
.df-fname{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.df-fdir{color:var(--muted);font-weight:500}
.df-new{flex-shrink:0;font-size:10.5px;font-weight:600;color:#1A7F37;background:rgba(34,160,107,.12);padding:1px 6px;border-radius:99px}
.df-counts{display:flex;align-items:center;gap:6px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;flex-shrink:0;padding-right:12px}
.df-addc{color:#1A7F37;font-weight:600}.df-delc{color:#CF222E;font-weight:600}
.df-cbar{display:inline-flex;gap:2px;align-items:center}
.df-cbar i{display:block;width:8px;height:3px;border-radius:1px;background:var(--hair)}
.df-cbar i.on-add{background:#1A7F37}
.df-cbar i.on-del{background:#CF222E}
.df-well{margin:0 8px 8px;border-radius:8px;background:var(--ground);box-shadow:inset 0 0 0 1px var(--hair);overflow:hidden}
.df-scroll{overflow-x:auto}
.df-tbl{width:100%;border-collapse:collapse;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:20px;table-layout:fixed}
.df-tbl td{padding:1px 8px;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}
.df-ln{width:42px;text-align:right;color:var(--muted);user-select:none;font-variant-numeric:tabular-nums;border-right:1px solid var(--hair);padding:1px 6px}
.df-sig{width:16px;text-align:center;user-select:none;opacity:.6;padding:1px 2px}
.df-hunk td{background:rgba(44,134,176,.08);color:var(--muted);font-size:11.5px;padding:4px 8px;border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}
.df-r-add td{background:rgba(34,160,107,.11)}.df-r-add .df-sig{color:#1A7F37}
.df-r-del td{background:rgba(226,56,43,.10)}.df-r-del .df-sig{color:#CF222E}
.df-c-add{background:rgba(34,160,107,.11)}.df-c-del{background:rgba(226,56,43,.10)}.df-c-empty{background:var(--ground);opacity:.55}
.df-split-mid{border-left:1px solid var(--hair)}
.df-empty{padding:20px;text-align:center;color:var(--muted);font-size:13px}
`;

export function parseUnifiedDiff(text) {
  const files = [];
  if (!text || !String(text).trim()) return files;
  let cur = null;
  let hunk = null;
  let oldLn = 0;
  let newLn = 0;

  for (const line of String(text).split(/\r?\n/)) {
    if (line.startsWith("diff --git ")) {
      const m = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
      cur = { name: m ? m[2] : line.slice(11), adds: 0, dels: 0, created: false, hunks: [] };
      files.push(cur);
      hunk = null;
      continue;
    }
    if (line.startsWith("+++ ") || line.startsWith("--- ")) {
      if (!cur) {
        cur = { name: line.slice(4).replace(/^[ab]\//, ""), adds: 0, dels: 0, created: false, hunks: [] };
        files.push(cur);
      } else if (line.startsWith("--- ") && line === "--- /dev/null") {
        cur.created = true;
      } else if (line.startsWith("+++ ") && line !== "+++ /dev/null") {
        cur.name = line.slice(4).replace(/^b\//, "");
      }
      continue;
    }
    if (line.startsWith("@@")) {
      if (!cur) {
        cur = { name: "patch", adds: 0, dels: 0, created: false, hunks: [] };
        files.push(cur);
      }
      const m = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      oldLn = m ? Number(m[1]) : 1;
      newLn = m ? Number(m[2]) : 1;
      hunk = { header: line, rows: [] };
      cur.hunks.push(hunk);
      continue;
    }
    if (!hunk) continue;
    if (line.startsWith("+")) {
      cur.adds++;
      hunk.rows.push({ t: "add", o: "", n: newLn++, s: line.slice(1) });
    } else if (line.startsWith("-")) {
      cur.dels++;
      hunk.rows.push({ t: "del", o: oldLn++, n: "", s: line.slice(1) });
    } else if (line.startsWith(" ") || line === "") {
      hunk.rows.push({ t: "ctx", o: oldLn++, n: newLn++, s: line.startsWith(" ") ? line.slice(1) : "" });
    }
  }
  return files;
}

function splitPath(p) {
  const s = String(p ?? "");
  const i = s.lastIndexOf("/");
  return i === -1 ? { dir: "", name: s } : { dir: s.slice(0, i + 1), name: s.slice(i + 1) };
}

function changeBar(adds, dels) {
  const total = adds + dels;
  const green = total ? Math.round((adds / total) * 5) : 0;
  const blocks = [];
  for (let k = 0; k < 5; k++) {
    const cls = k < green ? "on-add" : total > 0 ? "on-del" : "off";
    blocks.push(`<i class="${cls}"></i>`);
  }
  return `<span class="df-cbar" aria-hidden="true">${blocks.join("")}</span>`;
}

function renderFileHead(f, isCollapsed) {
  const { dir, name } = splitPath(f.name);
  return `<div class="df-fhead">
    <button type="button" class="df-ftoggle" data-df-toggle="${esc(f.name)}" aria-expanded="${!isCollapsed}" data-expanded="${!isCollapsed}">
      <svg class="df-chev" width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M5 3l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <span class="df-fname"><span class="df-fdir">${esc(dir)}</span><span class="df-fbase">${esc(name)}</span></span>
      ${f.created ? `<span class="df-new">new</span>` : ""}
    </button>
    <span class="df-counts"><span class="df-addc">+${f.adds}</span><span class="df-delc">−${f.dels}</span>${changeBar(f.adds, f.dels)}</span>
  </div>`;
}

function pairRows(rows) {
  const out = [];
  let i = 0;
  while (i < rows.length) {
    const r = rows[i];
    if (r.t === "ctx") {
      out.push([r, r]);
      i++;
      continue;
    }
    const dels = [];
    const adds = [];
    while (i < rows.length && rows[i].t === "del") dels.push(rows[i++]);
    while (i < rows.length && rows[i].t === "add") adds.push(rows[i++]);
    const n = Math.max(dels.length, adds.length);
    for (let k = 0; k < n; k++) out.push([dels[k] || null, adds[k] || null]);
  }
  return out;
}

function renderInlineTable(file) {
  const body = file.hunks.map((hk) =>
    `<tr class="df-hunk"><td colspan="4">${esc(hk.header)}</td></tr>` +
    hk.rows.map((r) => {
      const cls = r.t === "add" ? "df-r-add" : r.t === "del" ? "df-r-del" : "";
      const sig = r.t === "add" ? "+" : r.t === "del" ? "−" : " ";
      return `<tr class="${cls}"><td class="df-ln">${r.o}</td><td class="df-ln">${r.n}</td><td class="df-sig">${sig}</td><td>${esc(r.s)}</td></tr>`;
    }).join("")
  ).join("");
  return `<table class="df-tbl"><colgroup><col style="width:44px"><col style="width:44px"><col style="width:18px"><col></colgroup><tbody>${body}</tbody></table>`;
}

function renderSplitTable(file) {
  const body = file.hunks.map((hk) =>
    `<tr class="df-hunk"><td colspan="4">${esc(hk.header)}</td></tr>` +
    pairRows(hk.rows).map(([l, r]) => {
      const lc = l ? (l.t === "del" ? "df-c-del" : "") : "df-c-empty";
      const rc = r ? (r.t === "add" ? "df-c-add" : "") : "df-c-empty";
      return `<tr>` +
        `<td class="df-ln ${lc}">${l ? l.o : ""}</td><td class="${lc}">${l ? esc(l.s) : ""}</td>` +
        `<td class="df-ln df-split-mid ${rc}">${r ? r.n : ""}</td><td class="${rc}">${r ? esc(r.s) : ""}</td>` +
        `</tr>`;
    }).join("")
  ).join("");
  return `<table class="df-tbl"><colgroup><col style="width:44px"><col style="width:calc(50% - 44px)"><col style="width:44px"><col style="width:calc(50% - 44px)"></colgroup><tbody>${body}</tbody></table>`;
}

export function renderDiffHtml(diffText, mode = "inline", meta = {}) {
  const files = parseUnifiedDiff(diffText);
  if (!files.length) return `<div class="df-empty">No file changes in this patch.</div>`;
  const collapsed = meta.collapsed instanceof Set ? meta.collapsed : new Set();
  const totalAdds = files.reduce((a, f) => a + f.adds, 0);
  const totalDels = files.reduce((a, f) => a + f.dels, 0);
  const info = [
    `${files.length} file${files.length === 1 ? "" : "s"}`,
    `<span class="df-addc">+${totalAdds}</span>`,
    `<span class="df-delc">−${totalDels}</span>`,
    meta.truncated ? "<span>(truncated at 256 KB)</span>" : "",
  ].filter(Boolean).join(" · ");
  const bar = `<div class="df-bar">
    <div class="df-meta">${info}</div>
    <div class="df-seg" role="group" aria-label="Diff view mode">
      <button type="button" data-df-mode="inline" aria-pressed="${mode === "inline"}">Inline</button>
      <button type="button" data-df-mode="split" aria-pressed="${mode === "split"}">Side-by-side</button>
    </div>
  </div>`;
  const cards = files.map((f) => {
    const isCollapsed = collapsed.has(f.name);
    const body = isCollapsed ? "" : `<div class="df-well"><div class="df-scroll">${mode === "split" ? renderSplitTable(f) : renderInlineTable(f)}</div></div>`;
    return `<article class="df-file">${renderFileHead(f, isCollapsed)}${body}</article>`;
  }).join("");
  return `<div class="df-wrap">${bar}${cards}</div>`;
}
