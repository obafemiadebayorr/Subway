// Subway Tunnel station connector logic. No network I/O except optional LAN TCP listener.
import crypto from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { Handshake, MAX_MESSAGE, generateKeypair, keypairFromSecret, seal, open } from '../noise/noise.js';

const B32 = 'abcdefghijklmnopqrstuvwxyz234567';
const SID_LEN = 26;
const PROLOGUE = new TextEncoder().encode('subway-tunnel/1');
// Spec said 48 KiB, but base64 of 48 KiB is already 65536 chars, over the Noise limit of 65535.
// 32 KiB raw -> ~43.7 KB base64 + JSON + tag stays safely under it (and is still <= 48 KiB).
export const CHUNK_BYTES = 32 * 1024;
export const MAX_INFLIGHT = 8;
export const REQUEST_TIMEOUT_MS = 60_000;
const STRIP = new Set(['host', 'origin', 'referer', 'cookie', 'connection', 'content-length', 'transfer-encoding']);
const KEEP_RES = ['content-type', 'etag', 'idempotent-replayed', 'retry-after'];
const te = new TextEncoder();
const td = new TextDecoder();

export function base32(bytes) {
  let bits = 0, value = 0, out = '';
  for (const byte of bytes) {
    value = (value << 8) | byte; bits += 8;
    while (bits >= 5) { out += B32[(value >>> (bits - 5)) & 31]; bits -= 5; }
  }
  if (bits > 0) out += B32[(value << (5 - bits)) & 31];
  return out;
}

export function subwayDir(env = process.env, home = os.homedir()) {
  if (env.SUBWAY_DIR) return env.SUBWAY_DIR;
  const oldDir = path.join(home, '.subway-context');
  try {
    const st = fs.lstatSync(oldDir);
    if (st.isDirectory() && !st.isSymbolicLink()) return oldDir;
  } catch { /* missing */ }
  return path.join(home, '.subway');
}

function writeAtomic(file, data) {
  const tmp = `${file}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
  fs.writeFileSync(tmp, data, { mode: 0o600 });
  fs.renameSync(tmp, file);
  fs.chmodSync(file, 0o600);
}

function readIf(file) {
  try { return fs.readFileSync(file); } catch { return null; }
}

function edPublicInfo(edPrivateKey) {
  const jwk = crypto.createPublicKey(edPrivateKey).export({ format: 'jwk' });
  const edPublicRaw = new Uint8Array(Buffer.from(jwk.x, 'base64url'));
  const sid = base32(new Uint8Array(crypto.createHash('sha256').update(edPublicRaw).digest())).slice(0, SID_LEN);
  return { sid, edPublicRaw };
}

function makeEd25519(dir) {
  const { privateKey } = crypto.generateKeyPairSync('ed25519');
  writeAtomic(path.join(dir, 'ed25519.pem'), privateKey.export({ type: 'pkcs8', format: 'pem' }));
  return privateKey;
}

/** dir is the tunnel dir (<subway_dir>/tunnel). Loads existing key material; missing pieces are undefined. */
export function loadIdentity(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch { /* best effort */ }

  let edPrivateKey;
  const pem = readIf(path.join(dir, 'ed25519.pem'));
  if (pem) {
    try { edPrivateKey = crypto.createPrivateKey(pem); } catch { edPrivateKey = undefined; }
  }

  let noiseStatic;
  const xk = readIf(path.join(dir, 'x25519.key'));
  if (xk && xk.length === 32) noiseStatic = keypairFromSecret(new Uint8Array(xk));

  let secret;
  const sraw = readIf(path.join(dir, 'connector.secret'));
  const s = sraw ? sraw.toString('utf8').trim() : '';
  if (/^[0-9a-f]{64}$/.test(s)) secret = s;

  let sid, edPublicRaw;
  if (edPrivateKey) ({ sid, edPublicRaw } = edPublicInfo(edPrivateKey));
  return { sid, edPrivateKey, edPublicRaw, noiseStatic, secret };
}

/** dir is the tunnel dir (<subway_dir>/tunnel). */
export function loadOrCreateIdentity(dir) {
  const existing = loadIdentity(dir);

  let edPrivateKey = existing.edPrivateKey;
  if (!edPrivateKey) edPrivateKey = makeEd25519(dir);
  const { sid, edPublicRaw } = edPublicInfo(edPrivateKey);

  let noiseStatic = existing.noiseStatic;
  if (!noiseStatic) {
    noiseStatic = generateKeypair();
    writeAtomic(path.join(dir, 'x25519.key'), Buffer.from(noiseStatic.secretKey));
  }

  let secret = existing.secret;
  if (!secret) {
    secret = crypto.randomBytes(32).toString('hex');
    writeAtomic(path.join(dir, 'connector.secret'), secret + '\n');
  }
  return { sid, edPrivateKey, edPublicRaw, noiseStatic, secret };
}

/** Rotates the x25519 and ed25519 keys, backing up the old ones to <name>.bak-<unix ts> (mode 0600). Keeps connector.secret. */
export function rotateIdentity(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch { /* best effort */ }

  const ts = Math.floor(Date.now() / 1000);
  for (const name of ['x25519.key', 'ed25519.pem']) {
    const src = path.join(dir, name);
    try {
      const bak = `${src}.bak-${ts}`;
      fs.copyFileSync(src, bak);
      fs.chmodSync(bak, 0o600);
    } catch { /* missing or unreadable: nothing to back up */ }
  }

  makeEd25519(dir);
  const noiseStatic = generateKeypair();
  writeAtomic(path.join(dir, 'x25519.key'), Buffer.from(noiseStatic.secretKey));

  return { x25519_pub: Buffer.from(noiseStatic.publicKey).toString('base64') };
}

/** Writes station.json (no secrets), atomically. */
export function writeStationInfo(dir, identity, url) {
  const info = {
    sid: identity.sid,
    x25519_pub: Buffer.from(identity.noiseStatic.publicKey).toString('base64'),
    url: url || null,
    updated_at: new Date().toISOString(),
  };
  writeAtomic(path.join(dir, 'station.json'), JSON.stringify(info, null, 2) + '\n');
  return info;
}

export function signChallenge(identity, nonceB64) {
  const nonce = Buffer.from(nonceB64, 'base64');
  const msg = Buffer.concat([nonce, Buffer.from(identity.sid, 'utf8')]);
  const sig = crypto.sign(null, msg, identity.edPrivateKey);
  return { t: 'auth', pub: Buffer.from(identity.edPublicRaw).toString('base64'), sig: sig.toString('base64') };
}

export function pathAllowed(p) {
  return typeof p === 'string' && p.startsWith('/api/') && !p.startsWith('/api/mcp/proxy/') && !p.includes('..');
}

export function createStation({ secret, port, noiseStatic, fetchImpl = fetch, send }) {
  const channels = new Map();

  function sendFrame(ch, payload) {
    const out = new Uint8Array(payload.length + 4);
    new DataView(out.buffer).setUint32(0, ch >>> 0);
    out.set(payload, 4);
    send(out);
  }
  function closeChannel(ch, notify) {
    const st = channels.get(ch);
    if (!st) return;
    channels.delete(ch);
    for (const c of st.inflight.values()) c.abort();
    st.inflight.clear();
    if (notify) send(JSON.stringify({ t: 'close', ch }));
  }
  function sealed(st, ch, obj) {
    if (st.dead || channels.get(ch) !== st) return;
    sendFrame(ch, seal(st.hs.send, te.encode(JSON.stringify(obj))));
  }
  const b64 = (u8) => Buffer.from(u8).toString('base64');

  async function forward(ch, st, msg) {
    const id = msg.id;
    const reply = (o) => sealed(st, ch, { ...o, id });
    const jsonRes = (status, body) => {
      reply({ t: 'res', status, headers: { 'content-type': 'application/json' } });
      reply({ t: 'chunk', data: b64(te.encode(JSON.stringify(body))) });
      reply({ t: 'end' });
    };
    if (typeof msg.method !== 'string' || !pathAllowed(msg.path)) return jsonRes(403, { error: 'path not allowed over tunnel' });
    if (st.inflight.size >= MAX_INFLIGHT) return jsonRes(429, { error: 'too many requests' });

    const headers = {};
    for (const [k, v] of Object.entries(msg.headers || {})) {
      const lk = k.toLowerCase();
      if (STRIP.has(lk) || lk.startsWith('x-subway-tunnel') || lk === 'accept-encoding') continue;
      headers[k] = String(v);
    }
    headers['X-Subway-Tunnel'] = secret;
    headers['X-Subway-Tunnel-Device'] = st.deviceId;

    const ac = new AbortController();
    st.inflight.set(id, ac);
    let timer = setTimeout(() => ac.abort(), REQUEST_TIMEOUT_MS);
    timer.unref?.();
    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };
    let started = false;
    try {
      const hasBody = msg.body != null && msg.body !== '';
      const res = await fetchImpl(`http://127.0.0.1:${port}${msg.path}`, {
        method: msg.method,
        headers,
        body: hasBody ? Buffer.from(msg.body, 'base64') : undefined,
        signal: ac.signal,
      });
      const ct = res.headers.get('content-type') || '';
      if (ct.includes('text/event-stream')) clear();
      const rh = {};
      for (const k of KEEP_RES) { const v = res.headers.get(k); if (v != null) rh[k] = v; }
      reply({ t: 'res', status: res.status, headers: rh });
      started = true;
      if (res.body) {
        const reader = res.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (let o = 0; o < value.length; o += CHUNK_BYTES) {
            reply({ t: 'chunk', data: b64(value.subarray(o, o + CHUNK_BYTES)) });
          }
        }
      }
      reply({ t: 'end' });
    } catch {
      if (ac.signal.aborted && (st.dead || channels.get(ch) !== st || !st.inflight.has(id))) {
        // cancelled by the device or channel gone: nothing to say
      } else if (started) reply({ t: 'end', error: 'upstream failed' });
      else jsonRes(502, { error: 'station unreachable' });
    } finally {
      clear();
      if (st.inflight.get(id) === ac) st.inflight.delete(id);
    }
  }

  function onText(str) {
    if (typeof str !== 'string' || str === 'pong') return;
    let m;
    try { m = JSON.parse(str); } catch { return; }
    if (!m || typeof m.ch !== 'number') return;
    if (m.t === 'open') {
      closeChannel(m.ch, false);
      channels.set(m.ch, {
        hs: new Handshake({ initiator: false, s: noiseStatic, prologue: PROLOGUE }),
        deviceId: null, inflight: new Map(), dead: false,
      });
    } else if (m.t === 'close') {
      const st = channels.get(m.ch);
      if (st) st.dead = true;
      closeChannel(m.ch, false);
    }
  }

  function onBinary(data) {
    if (data.byteLength < 4) return;
    const ch = new DataView(data.buffer, data.byteOffset, data.byteLength).getUint32(0);
    const payload = data.subarray(4);
    const st = channels.get(ch);
    if (!st) { send(JSON.stringify({ t: 'close', ch })); return; }
    try {
      if (!st.hs.complete) {
        st.hs.readMessage(payload);
        st.deviceId = crypto.createHash('sha256').update(st.hs.remoteStatic).digest('hex').slice(0, 32);
        sendFrame(ch, st.hs.writeMessage());
        return;
      }
      const msg = JSON.parse(td.decode(open(st.hs.recv, payload)));
      if (msg.t === 'req' && Number.isInteger(msg.id)) {
        if (st.inflight.has(msg.id)) return;
        void forward(ch, st, msg);
      } else if (msg.t === 'cancel') {
        const ac = st.inflight.get(msg.id);
        if (ac) { st.inflight.delete(msg.id); ac.abort(); }
      }
    } catch {
      st.dead = true;
      closeChannel(ch, true);
    }
  }

  function reset() {
    for (const ch of [...channels.keys()]) {
      const st = channels.get(ch);
      st.dead = true;
      closeChannel(ch, false);
    }
  }

  return { onText, onBinary, reset, channels };
}

function isAllowedIPv4(ip) {
  const parts = ip.split('.').map(Number);
  if (parts.length !== 4 || parts.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return false;
  const [a, b] = parts;
  if (a === 127) return true; // loopback 127.0.0.0/8
  if (a === 10) return true; // RFC1918 10.0.0.0/8
  if (a === 172 && b >= 16 && b <= 31) return true; // RFC1918 172.16.0.0/12
  if (a === 192 && b === 168) return true; // RFC1918 192.168.0.0/16
  return false;
}

function parseIPv6Groups(ip) {
  let s = ip.toLowerCase();
  const lastColon = s.lastIndexOf(':');
  let v4Tail = null;
  if (lastColon !== -1 && s.slice(lastColon + 1).includes('.')) {
    const v4 = s.slice(lastColon + 1);
    if (!net.isIPv4(v4)) return null;
    const p = v4.split('.').map(Number);
    v4Tail = [(p[0] << 8) | p[1], (p[2] << 8) | p[3]];
    s = s[lastColon - 1] === ':' ? s.slice(0, lastColon + 1) : s.slice(0, lastColon);
  }
  const targetLen = v4Tail ? 6 : 8;
  const halves = s.split('::');
  if (halves.length > 2) return null;
  const parseSide = (part) => (part === '' ? [] : part.split(':').map((g) => parseInt(g, 16)));
  const left = parseSide(halves[0]);
  const right = halves.length === 2 ? parseSide(halves[1]) : [];
  if (left.some((n) => !Number.isInteger(n) || n < 0 || n > 0xffff)) return null;
  if (right.some((n) => !Number.isInteger(n) || n < 0 || n > 0xffff)) return null;
  const missing = targetLen - (left.length + right.length);
  if (halves.length === 1 ? missing !== 0 : missing < 1) return null;
  return [...left, ...Array(missing).fill(0), ...right, ...(v4Tail || [])];
}

export function isAllowedLanHost(host) {
  if (typeof host !== 'string' || !host) return false;
  const h = host.trim().toLowerCase();
  if (h === 'localhost') return true;
  if (net.isIPv4(h)) return isAllowedIPv4(h);
  if (net.isIPv6(h)) {
    if (h.includes('%')) return false;
    const g = parseIPv6Groups(h);
    if (!g) return false;
    if (g[0] === 0 && g[1] === 0 && g[2] === 0 && g[3] === 0 && g[4] === 0 && g[5] === 0 && g[6] === 0 && g[7] === 1) {
      return true;
    }
    if (g[0] === 0 && g[1] === 0 && g[2] === 0 && g[3] === 0 && g[4] === 0 && g[5] === 0xffff) {
      const v4 = `${g[6] >> 8}.${g[6] & 0xff}.${g[7] >> 8}.${g[7] & 0xff}`;
      return isAllowedIPv4(v4);
    }
    return false;
  }
  return false;
}

export function parseListenAddr(addr) {
  if (typeof addr !== 'string' || !addr.trim()) {
    throw new Error('LAN listen address must be host:port');
  }
  const s = addr.trim();
  let host, portStr;
  if (s.startsWith('[')) {
    const m = /^\[([^\]]+)\]:(\d+)$/.exec(s);
    if (!m) throw new Error(`invalid LAN listen address: ${addr}`);
    host = m[1];
    portStr = m[2];
  } else {
    const idx = s.indexOf(':');
    if (idx <= 0 || idx !== s.lastIndexOf(':')) {
      throw new Error(`invalid LAN listen address: ${addr}`);
    }
    host = s.slice(0, idx);
    portStr = s.slice(idx + 1);
  }
  if (!/^\d+$/.test(portStr)) throw new Error(`invalid LAN listen port: ${portStr}`);
  const port = Number(portStr);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error(`invalid LAN listen port: ${portStr}`);
  }
  if (!isAllowedLanHost(host)) {
    throw new Error(`LAN listen host must be loopback or RFC1918: ${host}`);
  }
  return { host: host.toLowerCase(), port };
}

export function encodeNoiseFrame(payload) {
  const out = Buffer.allocUnsafe(4 + payload.length);
  out.writeUInt32BE(payload.length, 0);
  out.set(payload, 4);
  return out;
}

/** Noise-framed TCP listener for direct LAN calls (loopback/RFC1918 only; no HTTP). */
export function createLanServer({ listen, secret, port, noiseStatic, fetchImpl = fetch, onError }) {
  const target = parseListenAddr(listen);
  const sockets = new Set();

  const server = net.createServer((socket) => {
    sockets.add(socket);
    socket.setNoDelay(true);

    const st = createStation({
      secret,
      port,
      noiseStatic,
      fetchImpl,
      send: (data) => {
        if (typeof data === 'string') {
          socket.destroy();
          return;
        }
        if (!socket.destroyed && socket.writable) {
          socket.write(encodeNoiseFrame(data.subarray(4)));
        }
      },
    });
    st.onText(JSON.stringify({ t: 'open', ch: 1 }));

    let buf = Buffer.alloc(0);
    socket.on('data', (chunk) => {
      if (socket.destroyed) return;
      buf = buf.length === 0 ? chunk : Buffer.concat([buf, chunk]);
      while (buf.length >= 4) {
        const len = buf.readUInt32BE(0);
        if (len === 0 || len > MAX_MESSAGE) {
          socket.destroy();
          return;
        }
        if (buf.length < 4 + len) break;
        const payload = new Uint8Array(buf.subarray(4, 4 + len));
        buf = buf.length === 4 + len ? Buffer.alloc(0) : buf.subarray(4 + len);
        const framed = new Uint8Array(4 + payload.length);
        new DataView(framed.buffer).setUint32(0, 1);
        framed.set(payload, 4);
        st.onBinary(framed);
        if (socket.destroyed) return;
      }
    });

    const cleanup = () => {
      sockets.delete(socket);
      st.reset();
    };
    socket.on('close', cleanup);
    socket.on('error', () => {
      cleanup();
      socket.destroy();
    });
  });

  if (typeof onError === 'function') server.on('error', onError);

  const ready = new Promise((resolve, reject) => {
    const onListenErr = (err) => reject(err);
    server.once('error', onListenErr);
    server.listen(target.port, target.host, () => {
      server.removeListener('error', onListenErr);
      const addr = server.address();
      resolve({
        host: typeof addr === 'object' && addr ? addr.address : target.host,
        port: typeof addr === 'object' && addr ? addr.port : target.port,
      });
    });
  });

  async function close() {
    for (const s of [...sockets]) s.destroy();
    sockets.clear();
    await new Promise((resolve) => {
      try { server.close(() => resolve()); } catch { resolve(); }
    });
  }

  return { server, ready, close };
}

if (process.argv.includes('--rotate-keys')) {
  const dir = path.join(subwayDir(), 'tunnel');
  const info = rotateIdentity(dir);
  console.log(JSON.stringify(info));
  process.exit(0);
}
