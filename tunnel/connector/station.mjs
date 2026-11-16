// Subway Tunnel station connector logic. No network I/O: the CLI wires it to a WebSocket.
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Handshake, generateKeypair, keypairFromSecret, seal, open } from '../noise/noise.js';

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

/** dir is the tunnel dir (<subway_dir>/tunnel). */
export function loadOrCreateIdentity(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch { /* best effort */ }

  let edPrivateKey;
  const pem = readIf(path.join(dir, 'ed25519.pem'));
  if (pem) {
    try { edPrivateKey = crypto.createPrivateKey(pem); } catch { edPrivateKey = undefined; }
  }
  if (!edPrivateKey) {
    const { privateKey } = crypto.generateKeyPairSync('ed25519');
    writeAtomic(path.join(dir, 'ed25519.pem'), privateKey.export({ type: 'pkcs8', format: 'pem' }));
    edPrivateKey = privateKey;
  }
  const jwk = crypto.createPublicKey(edPrivateKey).export({ format: 'jwk' });
  const edPublicRaw = new Uint8Array(Buffer.from(jwk.x, 'base64url'));
  const sid = base32(new Uint8Array(crypto.createHash('sha256').update(edPublicRaw).digest())).slice(0, SID_LEN);

  let noiseStatic;
  const xk = readIf(path.join(dir, 'x25519.key'));
  if (xk && xk.length === 32) noiseStatic = keypairFromSecret(new Uint8Array(xk));
  else {
    noiseStatic = generateKeypair();
    writeAtomic(path.join(dir, 'x25519.key'), Buffer.from(noiseStatic.secretKey));
  }

  let secret;
  const sraw = readIf(path.join(dir, 'connector.secret'));
  const s = sraw ? sraw.toString('utf8').trim() : '';
  if (/^[0-9a-f]{64}$/.test(s)) secret = s;
  else {
    secret = crypto.randomBytes(32).toString('hex');
    writeAtomic(path.join(dir, 'connector.secret'), secret + '\n');
  }
  return { sid, edPrivateKey, edPublicRaw, noiseStatic, secret };
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
