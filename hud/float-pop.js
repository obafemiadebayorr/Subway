// Floats a composer menu above the composer (below only when there is no room), outside the input area.
export function floatOpen(panel, trigger, { align = "start", matchWidth = false } = {}) {
  let layer = panel.parentElement && panel.parentElement.classList.contains("cmp-layer") ? panel.parentElement : null;
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "cmp cmp-layer";
    layer.appendChild(panel);
  }
  if (layer.parentElement !== document.body) document.body.appendChild(layer);
  const gap = 8;
  const place = () => {
    if (panel.hidden || !trigger.isConnected) return;
    const root = trigger.closest(".cmp") || trigger;
    const r = root.getBoundingClientRect();
    const t = trigger.getBoundingClientRect();
    panel.style.maxHeight = "";
    if (matchWidth) panel.style.width = `${Math.round(t.width)}px`;
    const pw = panel.offsetWidth;
    const above = r.top - 2 * gap;
    const below = window.innerHeight - r.bottom - 2 * gap;
    const side = panel.offsetHeight <= above || above >= below ? "top" : "bottom";
    const room = Math.max(160, side === "top" ? above : below);
    panel.style.maxHeight = `${room}px`;
    const ph = Math.min(panel.offsetHeight, room);
    let left = align === "end" ? t.right - pw : t.left;
    left = Math.max(16, Math.min(left, window.innerWidth - pw - 16));
    panel.dataset.side = side;
    panel.style.left = `${Math.round(left)}px`;
    panel.style.top = `${Math.round(side === "top" ? r.top - gap - ph : r.bottom + gap)}px`;
  };
  place();
  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, true);
  const off = () => {
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place, true);
  };
  off.place = place;
  return off;
}
