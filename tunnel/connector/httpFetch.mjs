/**
 * A small fetch for the connector's calls to the local HUD, built on node:http.
 *
 * Node's global fetch (undici) can crash the whole process with `assert(!this.paused)` when the
 * server ends a socket while a streamed body is paused. One such crash takes every device offline,
 * so the connector uses plain node:http: errors reach the body reader instead of killing the process.
 * Covers what station.mjs needs: status, headers.get(), body.getReader(), and abort by signal.
 */
import http from 'node:http';
import { Readable } from 'node:stream';

const agent = new http.Agent({ keepAlive: false });

export function httpFetch(url, { method = 'GET', headers = {}, body, signal } = {}) {
  // A fixed Content-Length, never chunked: the HUD reads exactly Content-Length bytes, so a chunked
  // body arrived empty (every POST through the tunnel lost its payload).
  const buf = body == null ? null : Buffer.from(typeof body === 'string' ? body : body);
  const hdrs = {};
  for (const [k, v] of Object.entries(headers || {})) {
    const lk = k.toLowerCase();
    if (lk === 'content-length' || lk === 'transfer-encoding') continue;
    hdrs[k] = v;
  }
  if (buf) hdrs['Content-Length'] = String(buf.length);
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method, headers: hdrs, agent, signal }, (res) => {
      const h = new Map();
      for (const [k, v] of Object.entries(res.headers)) h.set(k, Array.isArray(v) ? v.join(', ') : String(v));
      resolve({
        status: res.statusCode || 0,
        headers: { get: (k) => h.get(String(k).toLowerCase()) ?? null },
        body: Readable.toWeb(res),
      });
    });
    req.on('error', reject);
    req.end(buf ?? undefined);
  });
}
