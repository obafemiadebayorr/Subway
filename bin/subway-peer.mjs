#!/usr/bin/env node
/**
 * One-shot station-to-station tunnel call. Prints exactly one JSON line to stdout.
 * Usage: node bin/subway-peer.mjs call --sid S --pk K (--relay URL | --direct HOST:PORT) --method GET --path /api/status [--token-env NAME] [--body-file PATH] [--timeout-ms MS]
 */
import fs from 'node:fs';
import net from 'node:net';
import { TunnelClient } from '../tunnel/initiator/client.ts';

const te = new TextEncoder();
const td = new TextDecoder();
const MAX_FRAME_BYTES = 1024 * 1024;

function parseDirectAddr(addr) {
  const s = String(addr || '').trim();
  if (!s) throw new Error('direct address must be host:port');
  let host;
  let portStr;
  if (s.startsWith('[')) {
    const m = /^\[([^\]]+)\]:(\d+)$/.exec(s);
    if (!m) throw new Error(`invalid direct address: ${addr}`);
    host = m[1];
    portStr = m[2];
  } else {
    const idx = s.lastIndexOf(':');
    if (idx <= 0 || s.indexOf(':') !== idx) throw new Error(`invalid direct address: ${addr}`);
    host = s.slice(0, idx);
    portStr = s.slice(idx + 1);
  }
  const port = Number(portStr);
  if (!/^\d+$/.test(portStr) || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`invalid direct port: ${portStr}`);
  }
  return { host, port };
}

function encodeNoiseFrame(payload) {
  const frame = Buffer.allocUnsafe(4 + payload.length);
  frame.writeUInt32BE(payload.length, 0);
  frame.set(payload, 4);
  return frame;
}

function createDirectSocket(host, port) {
  const sock = net.connect({ host, port });
  sock.setNoDelay(true);
  let buf = Buffer.alloc(0);
  let closed = false;

  const wrapper = {
    binaryType: 'arraybuffer',
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    send(data) {
      if (closed || sock.destroyed) throw new Error('direct socket closed');
      const bytes = typeof data === 'string'
        ? te.encode(data)
        : (data instanceof Uint8Array ? data : new Uint8Array(data));
      sock.write(encodeNoiseFrame(bytes));
    },
    close() {
      if (closed) return;
      closed = true;
      sock.destroy();
    },
  };

  sock.on('connect', () => {
    if (!closed) wrapper.onopen?.();
  });

  sock.on('data', (chunk) => {
    if (closed) return;
    buf = buf.length === 0 ? chunk : Buffer.concat([buf, chunk]);
    while (buf.length >= 4) {
      const len = buf.readUInt32BE(0);
      if (len === 0 || len > MAX_FRAME_BYTES) {
        closed = true;
        sock.destroy();
        wrapper.onerror?.(new Error('invalid direct frame length'));
        return;
      }
      if (buf.length < 4 + len) break;
      const payload = new Uint8Array(buf.subarray(4, 4 + len));
      buf = buf.length === 4 + len ? Buffer.alloc(0) : buf.subarray(4 + len);
      wrapper.onmessage?.({ data: payload });
      if (closed) return;
    }
  });

  sock.on('error', (err) => {
    if (closed) return;
    closed = true;
    wrapper.onerror?.(err);
    sock.destroy();
  });

  sock.on('close', () => {
    if (closed) return;
    closed = true;
    wrapper.onclose?.();
  });

  return wrapper;
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next == null || next.startsWith('--')) out[key] = true;
      else { out[key] = next; i++; }
    } else out._.push(a);
  }
  return out;
}

let finished = false;
function done(obj, code) {
  if (finished) return;
  finished = true;
  // Exit only once stdout is flushed: a large reply (e.g. /api/status) is cut short on a pipe otherwise.
  try { process.stdout.write(JSON.stringify(obj) + '\n', () => process.exit(code)); } catch { process.exit(code); }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args._[0] !== 'call') return done({ error: 'usage: call' }, 1);
  const sid = String(args.sid || '');
  const pk = String(args.pk || '');
  if (args.direct === true) return done({ error: 'direct address must be host:port' }, 1);
  const direct = typeof args.direct === 'string' ? args.direct.trim() : '';
  const relay = typeof args.relay === 'string' ? args.relay.trim() : '';
  const method = String(args.method || 'GET').toUpperCase();
  const path = String(args.path || '/api/status');
  const tokenEnv = args['token-env'] ? String(args['token-env']) : '';
  const token = tokenEnv ? String(process.env[tokenEnv] || '') : '';
  if (!sid || !pk || (!relay && !direct)) return done({ error: 'missing sid, pk or relay' }, 1);

  let directTarget = null;
  if (direct) {
    try {
      directTarget = parseDirectAddr(direct);
    } catch (e) {
      return done({ error: e && e.message ? e.message : String(e) }, 1);
    }
  }

  let body;
  const headers = {};
  if (args['body-file']) {
    try {
      body = fs.readFileSync(String(args['body-file']), 'utf8');
      headers['Content-Type'] = 'application/json';
    } catch (e) {
      return done({ error: `body read failed: ${e && e.message || e}` }, 1);
    }
  }
  if (token) headers['X-Subway-Token'] = token;

  let client;
  const hardMs = Number(args['timeout-ms']) || 25000;
  const hard = setTimeout(() => {
    try { client && client.close(); } catch {}
    done({ error: 'timeout' }, 1);
  }, hardMs);
  if (typeof hard.unref === 'function') hard.unref();

  try {
    const timeoutMs = Math.max(1000, hardMs - 5000);
    client = directTarget
      ? new TunnelClient({
          url: `tcp://${directTarget.host}:${directTarget.port}`,
          sid,
          stationKey: pk,
          timeoutMs,
          socketFactory: () => createDirectSocket(directTarget.host, directTarget.port),
        })
      : new TunnelClient({ url: relay, sid, stationKey: pk, timeoutMs });
    const res = await client.request(path, { method, headers, body: body ?? null });
    const text = td.decode(res.body || new Uint8Array());
    clearTimeout(hard);
    try { client.close(); } catch {}
    return done({ status: res.status, body: text }, 0);
  } catch (e) {
    clearTimeout(hard);
    try { client && client.close(); } catch {}
    const msg = e && e.reason === 'timeout' ? 'timeout' : (e && e.message ? e.message : String(e));
    return done({ error: msg }, 1);
  }
}

main().catch((e) => done({ error: e && e.message ? e.message : String(e) }, 1));
