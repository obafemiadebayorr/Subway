// Device frame inline SVGs + device-type map for the HUD Devices page.
// Screen rects are computed against each SVG's viewBox and used to place
// live/snapshot/activity content inside the transparent screen area.

function pctRect(x, y, w, h, vw, vh) {
  return {
    left: `${(x / vw) * 100}%`,
    top: `${(y / vh) * 100}%`,
    width: `${(w / vw) * 100}%`,
    height: `${(h / vh) * 100}%`,
  };
}

export const DEVICE_FRAMES = {
  laptop: {
    type: "laptop",
    viewBox: { w: 260, h: 172 },
    screen: { x: 30, y: 14, w: 200, h: 128 },
    label: "Laptop",
  },
  phone: {
    type: "phone",
    viewBox: { w: 220, h: 280 },
    screen: { x: 52, y: 16, w: 116, h: 248 },
    label: "Phone",
  },
  monitor: {
    type: "monitor",
    viewBox: { w: 260, h: 190 },
    screen: { x: 17, y: 13, w: 226, h: 131 },
    label: "Monitor",
  },
  "mac-mini": {
    type: "mac-mini",
    viewBox: { w: 260, h: 110 },
    screen: null, // Mac mini has no built-in display; render activity summary instead.
    label: "Mac mini",
  },
};

export const SCREEN_RECT = {
  laptop: pctRect(30, 14, 200, 128, 260, 172),
  phone: pctRect(52, 16, 116, 248, 220, 280),
  monitor: pctRect(17, 13, 226, 131, 260, 190),
  "mac-mini": null,
};

export const DEVICE_TYPE_ALIASES = {
  laptop: "laptop",
  macbook: "laptop",
  "macbook-pro": "laptop",
  "macbook-air": "laptop",
  phone: "phone",
  iphone: "phone",
  android: "phone",
  monitor: "monitor",
  display: "monitor",
  imac: "monitor",
  "mac-mini": "mac-mini",
  macmini: "mac-mini",
  mini: "mac-mini",
};

export function deviceKind(peer) {
  if (!peer || typeof peer !== "object") return "laptop";
  const st = peer.station && typeof peer.station === "object" ? peer.station : {};
  const platform = String(peer.platform || peer.kind || st.platform || "").trim().toLowerCase();
  const typeHint = String(peer.device_type || st.device_type || "").trim().toLowerCase();
  if (platform === "phone" || platform === "ios" || platform === "android" || typeHint === "phone" || typeHint === "iphone" || typeHint === "android") {
    return "phone";
  }
  const text = [peer.alias, peer.name, peer.model, peer.hardware, peer.device_model, st.name, st.model, st.hardware]
    .filter(Boolean)
    .join(" ");
  if (/mac\s*mini/i.test(text) || /headless/i.test(text) || peer.headless === true || st.headless === true || typeHint === "mac-mini" || typeHint === "macmini") {
    return "mac-mini";
  }
  if (/imac/i.test(text) || /desktop/i.test(text) || typeHint === "monitor" || typeHint === "imac" || typeHint === "desktop") {
    return "monitor";
  }
  return "laptop";
}

export function frameForDeviceType(deviceType) {
  const key = String(deviceType || "").trim().toLowerCase();
  const type = DEVICE_TYPE_ALIASES[key] || (DEVICE_FRAMES[key] ? key : "laptop");
  return DEVICE_FRAMES[type];
}

export function screenRectStyle(frame) {
  if (typeof frame === "string") return SCREEN_RECT[frameForDeviceType(frame).type];
  if (!frame || !frame.screen) return null;
  if (frame.type && SCREEN_RECT[frame.type]) return SCREEN_RECT[frame.type];
  const { x, y, w, h } = frame.screen;
  const vb = frame.viewBox || frame.image;
  if (!vb) return null;
  return pctRect(x, y, w, h, vb.w, vb.h);
}

export function frameSvg(kind) {
  const frame = frameForDeviceType(kind);
  const { w, h } = frame.viewBox;
  const stroke = 'var(--muted, #5E7275)';
  const line = 'var(--line, var(--hair, #2A3036))';
  const body = 'var(--panel, var(--cloud, #1C2328))';
  const open = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="${frame.label}">`;

  if (frame.type === "phone") {
    return (
      open +
      `<rect x="43.5" y="64" width="2.5" height="18" rx="1.2" fill="${stroke}" opacity="0.6"/>` +
      `<rect x="43.5" y="90" width="2.5" height="24" rx="1.2" fill="${stroke}" opacity="0.6"/>` +
      `<rect x="174" y="76" width="2.5" height="28" rx="1.2" fill="${stroke}" opacity="0.6"/>` +
      `<path fill-rule="evenodd" d="M68 10H152A22 22 0 0 1 174 32V248A22 22 0 0 1 152 270H68A22 22 0 0 1 46 248V32A22 22 0 0 1 68 10ZM69 16H151A17 17 0 0 1 168 33V247A17 17 0 0 1 151 264H69A17 17 0 0 1 52 247V33A17 17 0 0 1 69 16Z" fill="#12171e" stroke="${stroke}" stroke-opacity="0.45" stroke-width="1.2"/>` +
      `<rect x="52" y="16" width="116" height="248" rx="17" fill="none" stroke="currentColor" stroke-opacity="0.16" stroke-width="1"/>` +
      `<rect x="91" y="22" width="38" height="10" rx="5" fill="#12171e" stroke="${line}" stroke-width="0.8"/>` +
      `<circle cx="121" cy="27" r="1.8" fill="${stroke}" opacity="0.5"/>` +
      `</svg>`
    );
  }

  if (frame.type === "monitor") {
    return (
      open +
      `<path d="M114 152H146L149 178H111Z" fill="${body}" stroke="${stroke}" stroke-opacity="0.45" stroke-width="1.2"/>` +
      `<rect x="84" y="177" width="92" height="6" rx="3" fill="${body}" stroke="${stroke}" stroke-opacity="0.45" stroke-width="1.2"/>` +
      `<path fill-rule="evenodd" d="M19 8H241A7 7 0 0 1 248 15V145A7 7 0 0 1 241 152H19A7 7 0 0 1 12 145V15A7 7 0 0 1 19 8ZM20 13H240A3 3 0 0 1 243 16V141A3 3 0 0 1 240 144H20A3 3 0 0 1 17 141V16A3 3 0 0 1 20 13Z" fill="#12171e" stroke="${stroke}" stroke-opacity="0.45" stroke-width="1.2"/>` +
      `<rect x="17" y="13" width="226" height="131" rx="3" fill="none" stroke="currentColor" stroke-opacity="0.16" stroke-width="1"/>` +
      `<circle cx="130" cy="10.5" r="1.1" fill="${stroke}"/>` +
      `</svg>`
    );
  }

  if (frame.type === "mac-mini") {
    return (
      open +
      `<rect x="44" y="80" width="172" height="7" rx="3.5" fill="#12171e" stroke="${line}" stroke-width="1"/>` +
      `<rect x="24" y="24" width="212" height="56" rx="13" fill="${body}" stroke="${stroke}" stroke-opacity="0.5" stroke-width="1.4"/>` +
      `<line x1="38" y1="27" x2="222" y2="27" stroke="currentColor" stroke-opacity="0.12" stroke-width="1"/>` +
      `<rect x="106" y="48" width="4" height="12" rx="2" fill="#12171e" stroke="${stroke}" stroke-opacity="0.55" stroke-width="0.9"/>` +
      `<rect x="116" y="48" width="4" height="12" rx="2" fill="#12171e" stroke="${stroke}" stroke-opacity="0.55" stroke-width="0.9"/>` +
      `<circle cx="134" cy="54" r="3.2" fill="#12171e" stroke="${stroke}" stroke-opacity="0.55" stroke-width="0.9"/>` +
      `<circle cx="206" cy="54" r="2" fill="var(--accent, #2C86B0)"/>` +
      `</svg>`
    );
  }

  // laptop (default)
  return (
    open +
    `<path fill-rule="evenodd" d="M34 8H226A10 10 0 0 1 236 18V142A8 8 0 0 1 228 150H32A8 8 0 0 1 24 142V18A10 10 0 0 1 34 8ZM34 14H226A4 4 0 0 1 230 18V138A4 4 0 0 1 226 142H34A4 4 0 0 1 30 138V18A4 4 0 0 1 34 14Z" fill="#12171e" stroke="${stroke}" stroke-opacity="0.45" stroke-width="1.2"/>` +
    `<rect x="30" y="14" width="200" height="128" rx="4" fill="none" stroke="currentColor" stroke-opacity="0.16" stroke-width="1"/>` +
    `<path d="M114 14H146V18.5A3 3 0 0 1 143 21.5H117A3 3 0 0 1 114 18.5Z" fill="#12171e"/>` +
    `<circle cx="130" cy="17.5" r="1.2" fill="${stroke}"/>` +
    `<rect x="92" y="148" width="76" height="3" rx="1.5" fill="${line}"/>` +
    `<path d="M8 150H252A2 2 0 0 1 254 152V157A5 5 0 0 1 249 162H11A5 5 0 0 1 6 157V152A2 2 0 0 1 8 150Z" fill="${body}" stroke="${stroke}" stroke-opacity="0.45" stroke-width="1.2"/>` +
    `<path d="M112 150H148A3 3 0 0 1 145 153H115A3 3 0 0 1 112 150Z" fill="${line}"/>` +
    `<rect x="22" y="162" width="16" height="2" rx="1" fill="${stroke}" opacity="0.5"/>` +
    `<rect x="222" y="162" width="16" height="2" rx="1" fill="${stroke}" opacity="0.5"/>` +
    `</svg>`
  );
}
