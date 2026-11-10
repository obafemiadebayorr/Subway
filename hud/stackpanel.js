import { switchFace } from "./switch-button.js";
/* Stack panel — port of kokonutui/stack-panel (MIT, @dorianbaffier).
   A settings panel that drills into pages: a row opens its page from the right
   while its label flies up to become the title. Swipe right, press Back or
   Escape to return. Vanilla DOM + Web Animations, no React. */

const PARALLAX = 0.3;
const POP_DISTANCE = 0.35;
const POP_VELOCITY = 500;
const EASE = "cubic-bezier(0.23, 1, 0.32, 1)";

const reduced = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function toggle(on) {
  /* kokonutui switch face: the row itself stays the switch, the face shows state. */
  const t = switchFace(Boolean(on));
  t.classList.add("ksb-face");
  t.setAttribute("aria-hidden", "true");
  return t;
}

function check(on) {
  const c = el("span", "sp-check" + (on ? "" : " hidden"));
  c.setAttribute("aria-hidden", "true");
  c.innerHTML =
    '<svg viewBox="0 0 256 256" width="16" height="16" fill="currentColor"><path d="M232.49 80.49l-128 128a12 12 0 0 1-17 0l-56-56a12 12 0 1 1 17-17L96 183 224 55.51a12 12 0 0 1 17 17z"/></svg>';
  return c;
}

function chevron(dir) {
  const s = el("span", "sp-chev" + (dir === "left" ? " left" : ""));
  s.setAttribute("aria-hidden", "true");
  const d = dir === "left" ? "M168 48l-80 80 80 80" : "M96 48l80 80-80 80";
  s.innerHTML = `<svg viewBox="0 0 256 256" width="16" height="16" fill="none" stroke="currentColor" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
  return s;
}

function buildRow(row, onOpen) {
  const line = el(row.tone === "danger" ? "div" : "div", "sp-row");
  const label = el("span", "sp-label" + (row.tone === "danger" ? " danger" : ""), row.label);
  line.appendChild(label);
  if (row.detail) line.appendChild(el("span", "sp-detail", row.detail));
  if (row.trailing) line.appendChild(row.trailing);
  if (row.checked !== undefined) line.appendChild(toggle(Boolean(row.checked)));
  if (row.selected !== undefined) line.appendChild(check(Boolean(row.selected)));
  if (row.to) line.appendChild(chevron("right"));

  const interactive = Boolean(row.to || row.onSelect || row.checked !== undefined);
  if (!interactive) return line;

  const btn = el("button", "sp-row sp-btn");
  btn.type = "button";
  btn.dataset.row = row.id;
  if (row.checked !== undefined) btn.setAttribute("role", "switch");
  if (row.checked !== undefined) btn.setAttribute("aria-checked", String(Boolean(row.checked)));
  if (row.checked === undefined && row.to) btn.setAttribute("aria-haspopup", "true");
  if (row.tone === "danger") btn.dataset.tone = "danger";
  while (line.firstChild) btn.appendChild(line.firstChild);
  if (row.checked !== undefined) btn.dataset.checked = String(Boolean(row.checked));
  btn.addEventListener("click", () => {
    if (row.onSelect) row.onSelect();
    if (row.to) onOpen(row, label);
  });
  return btn;
}

function buildPage(page, onOpen) {
  const wrap = el("div", "sp-page");
  for (const section of page.sections || []) {
    const sec = el("section", "sp-section");
    if (section.title) sec.appendChild(el("h3", "sp-sec-title", section.title));
    const list = el("ul", "sp-list");
    for (const row of section.rows) {
      const li = el("li", "sp-item");
      li.appendChild(buildRow(row, onOpen));
      list.appendChild(li);
    }
    sec.appendChild(list);
    if (section.footer) sec.appendChild(el("p", "sp-foot", section.footer));
    wrap.appendChild(sec);
  }
  return wrap;
}

const scrollMemo = new Map();

/* pages: { id: { title, sections: [{ title?, rows: [...], footer? }] } }
   row:   { id, label, detail?, to?, trailing?: Node, checked?, selected?,
            onSelect?, tone? } */
export function stackPanel({ pages: pagesIn, root }) {
  let pages = pagesIn;
  const panel = el("div", "sp");
  panel.tabIndex = -1;

  const head = el("div", "sp-head");
  const back = el("button", "sp-back");
  back.type = "button";
  back.hidden = true;
  back.appendChild(chevron("left"));
  back.appendChild(el("span", "sp-back-label", ""));
  const title = el("span", "sp-title", pages[root] ? pages[root].title : "");
  head.appendChild(back);
  head.appendChild(title);

  const body = el("div", "sp-body");
  panel.appendChild(head);
  panel.appendChild(body);

  let stack = [root];
  let current = null;      // the page node on top
  let under = null;        // the page node beneath it
  let busy = false;
  let openers = [];
  /* The root page's height. Sub-pages never make the card shorter than this, so
     opening a short page does not shrink the card and reflow the page under it. */
  let floor = 0;

  const topId = () => stack[stack.length - 1];

  /* A detached node measures 0 (update() runs while the page tree is still being
     built), so never measure one. The ResizeObserver re-fits once the page is laid
     out, and again when its height changes (text reflow at a new width). */
  function fit(node) {
    if (!node || !node.isConnected) return;
    const h = node.offsetHeight;
    if (stack.length === 1) floor = h;
    else if (stack.length === 2 && under && under.isConnected && under.offsetHeight) floor = under.offsetHeight;
    body.style.height = Math.max(floor, h) + "px";
  }
  const sizes = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => { if (!busy) fit(current); })
    : null;
  function track() {
    if (!sizes) return;
    sizes.disconnect();
    if (current) sizes.observe(current);
    if (under) sizes.observe(under);
  }

  function paint() {
    const id = topId();
    const page = pages[id];
    if (!page) return;
    current = buildPage(page, push);
    body.textContent = "";
    body.appendChild(current);
    const saved = scrollMemo.get(`${root}:${id}`);
    if (saved != null) body.scrollTop = saved;
    body.style.transition = "none";
    fit(current);
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => {
        fit(current);
        if (saved != null) body.scrollTop = saved;
      });
      track();
    }
    title.textContent = page.title;
    const parentId = stack.length > 1 ? stack[stack.length - 2] : null;
    const parent = parentId ? pages[parentId] : null;
    back.hidden = !parent;
    if (parent) back.querySelector(".sp-back-label").textContent = parent.title;
    prune();
  }

  /* Only the page on top and the one it slid over may exist in the body. */
  function prune() {
    for (const node of [...body.querySelectorAll(".sp-page")]) {
      if (node !== current && node !== under) node.remove();
    }
  }

  function flyTitle(fromLabel) {
    if (!fromLabel || reduced()) {
      title.animate(
        [{ opacity: 0, transform: "translateX(-24px)" }, { opacity: 1, transform: "none" }],
        { duration: 280, easing: EASE }
      );
      return;
    }
    const box = panel.getBoundingClientRect();
    const own = title.getBoundingClientRect();
    const r = fromLabel.getBoundingClientRect();
    const scale = parseFloat(getComputedStyle(fromLabel).fontSize) /
      (parseFloat(getComputedStyle(title).fontSize) || 15);
    const dx = r.left - (own.left - box.left);
    const dy = r.top + r.height / 2 - (own.top - box.top + own.height / 2);
    title.style.transformOrigin = "left center";
    title.animate(
      [
        { transform: `translate(${dx}px, ${dy}px) scale(${scale})`, opacity: 1 },
        { transform: "none", opacity: 1 },
      ],
      { duration: 420, easing: EASE }
    );
  }

  function push(row, label) {
    if (busy || !row.to || !pages[row.to]) return;
    busy = true;
    const outgoing = current;
    const next = buildPage(pages[row.to], push);
    body.appendChild(next);
    const width = body.clientWidth || 360;
    under = outgoing;
    if (under) under.classList.add("sp-under");

    if (reduced()) {
      next.style.transform = "translateX(0)";
      if (under) under.style.transform = `translateX(${-PARALLAX * 100}%)`;
    } else {
      // Place it one width to the right with transitions off, then let it slide.
      next.classList.add("sp-entering");
      next.style.transform = `translateX(${width}px)`;
      if (under) under.style.transform = "translateX(0)";
      void next.offsetWidth;
      next.classList.remove("sp-entering");
      next.style.transform = "translateX(0)";
      if (under) under.style.transform = `translateX(${-PARALLAX * 100}%)`;
    }

    stack.push(row.to);
    openers.push(row.id);
    current = next;
    busy = false;

    if (under) {
      const parked = under;
      setTimeout(() => { if (parked === under) parked.style.visibility = "hidden"; }, 460);
    }
    title.textContent = pages[row.to].title;
    flyTitle(label);
    back.hidden = false;
    back.querySelector(".sp-back-label").textContent = pages[stack[stack.length - 2]].title;
    body.style.transition = "";
    fit(next);
    track();
    back.focus({ preventScroll: true });
    prune();
  }

  function pop() {
    if (busy || stack.length < 2) return;
    busy = true;
    const outgoing = current;
    const incoming = under;
    const opener = openers.pop();
    const width = body.clientWidth || 360;
    if (incoming) incoming.style.visibility = "";

    title.animate(
      [{ opacity: 0, transform: "translateX(40px)" }, { opacity: 1, transform: "none" }],
      { duration: 220, easing: EASE }
    );

    const done = () => {
      if (outgoing && outgoing.parentNode) outgoing.parentNode.removeChild(outgoing);
      stack.pop();
      current = incoming;
      under = null;
      if (incoming) {
        incoming.style.transform = "translateX(0)";
        incoming.classList.remove("sp-under");
        incoming.style.boxShadow = "";
      }
      const parentId = stack.length > 1 ? stack[stack.length - 2] : null;
      title.textContent = pages[topId()].title;
      back.hidden = !parentId;
      if (parentId) back.querySelector(".sp-back-label").textContent = pages[parentId].title;
      busy = false;
      body.style.transition = "";
      fit(current);
      track();
      if (opener && current) {
        const row = current.querySelector(`[data-row="${CSS.escape(opener)}"]`);
        if (row) row.focus({ preventScroll: true });
      }
    };

    if (reduced() || !incoming) {
      void width;
      done();
      return;
    }
    outgoing.style.transform = `translateX(${width}px)`;
    incoming.style.transform = "translateX(0)";
    setTimeout(done, 360);
  }

  back.addEventListener("click", pop);
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && stack.length > 1) {
      event.preventDefault();
      pop();
    }
  });

  /* Swipe the top page right to go back. */
  let drag = null;
  body.addEventListener("pointerdown", (event) => {
    if (stack.length < 2 || busy || event.button !== 0) return;
    if (event.target.closest("input, select, textarea, .ksb-face")) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false, t: performance.now() };
  });
  body.addEventListener("pointermove", (event) => {
    if (!drag || drag.id !== event.pointerId || !current) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) > 6 && Math.abs(dx) > Math.abs(dy)) {
      drag.moved = true;
      if (under) under.style.visibility = "";
    }
    if (!drag.moved) return;
    const width = body.clientWidth || 360;
    const x = Math.max(0, dx);
    current.style.transform = `translateX(${x}px)`;
    if (under) under.style.transform = `translateX(${-PARALLAX * (width - Math.min(x, width))}px)`;
    body.style.overflow = "hidden";
  });
  const settle = (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    const d = drag;
    drag = null;
    body.style.overflow = "";
    if (!d.moved || !current) return;
    const width = body.clientWidth || 360;
    const x = Math.max(0, event.clientX - d.x);
    const dt = Math.max((performance.now() - d.t) / 1000, 0.016);
    const vx = (event.clientX - d.x) / dt;
    if (x > width * POP_DISTANCE || vx > POP_VELOCITY) {
      pop();
    } else {
      current.style.transform = "translateX(0)";
      if (under) under.style.transform = `translateX(${-PARALLAX * 100}%)`;
    }
  };
  body.addEventListener("pointerup", settle);
  body.addEventListener("pointercancel", settle);
  body.addEventListener("scroll", () => {
    scrollMemo.set(`${root}:${topId()}`, body.scrollTop);
  }, { passive: true });

  /* Re-render in place after a settings change: same stack, no animation. */
  function update(nextPages) {
    pages = nextPages;
    stack = stack.filter((id) => pages[id]);
    if (!stack.length) stack = [root];
    const id = topId();
    body.textContent = "";
    under = null;
    if (stack.length > 1) {
      under = buildPage(pages[stack[stack.length - 2]], push);
      under.classList.add("sp-under");
      under.style.transform = `translateX(${-PARALLAX * 100}%)`;
      under.style.visibility = "hidden";
      body.appendChild(under);
    }
    current = buildPage(pages[id], push);
    body.appendChild(current);
    const saved = scrollMemo.get(`${root}:${id}`);
    if (saved != null) body.scrollTop = saved;
    body.style.transition = "none";
    fit(current);
    track();
    if (typeof requestAnimationFrame === "function" && saved != null) {
      requestAnimationFrame(() => {
        body.scrollTop = saved;
      });
    }
    title.textContent = pages[id].title;
    const parentId = stack.length > 1 ? stack[stack.length - 2] : null;
    back.hidden = !parentId;
    if (parentId) back.querySelector(".sp-back-label").textContent = pages[parentId].title;
    prune();
  }

  paint();
  panel.__sp = { update, stack: () => stack.slice() };
  return panel;
}

export default stackPanel;
