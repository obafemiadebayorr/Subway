#!/usr/bin/env node
// Subway Tunnel connector CLI: one outbound WebSocket to the relay, forwarding to the local HUD.
import path from 'node:path';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { httpFetch } from '../tunnel/connector/httpFetch.mjs';
import { createLanServer, createStation, loadOrCreateIdentity, signChallenge, subwayDir, writeStationInfo } from '../tunnel/connector/station.mjs';

const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const url = (opt('--url') || process.env.SUBWAY_TUNNEL_URL || '').replace(/\/+$/, '');
const listenRaw = opt('--listen');
if (args.includes('--listen') && (!listenRaw || listenRaw.startsWith('--'))) {
  console.error('usage: subway-tunnel [--url wss://...] [--listen host:port] [--port 7890] (or SUBWAY_TUNNEL_URL / SUBWAY_LAN_LISTEN)');
  process.exit(2);
}
const listen = (listenRaw || process.env.SUBWAY_LAN_LISTEN || '').trim();
const port = Number(opt('--port') || process.env.SUBWAY_HUD_PORT || 7890);
const dir = path.join(subwayDir(), 'tunnel');
const log = (...a) => console.log(new Date().toISOString(), ...a);

const identity = loadOrCreateIdentity(dir);
const info = writeStationInfo(dir, identity, url || null);

if (args.includes('--identity')) {
  console.log(JSON.stringify({ sid: info.sid, x25519_pub: info.x25519_pub, url: info.url }));
  process.exit(0);
}
if (!url && !listen) {
  console.error('usage: subway-tunnel --url wss://... [--listen host:port] [--port 7890] (or SUBWAY_TUNNEL_URL / SUBWAY_LAN_LISTEN)');
  process.exit(2);
}
if (process.argv.includes("--exit-with-parent")) {
  const parent0 = process.ppid;
  setInterval(() => { if (process.ppid !== parent0 || process.ppid === 1) process.exit(0); }, 5000).unref();
}

let ws = null;
let delay = 1000;
let pingTimer = null;
let probeStartTimer = null;
let probeInterval = null;
let probeRunning = false;
let firstProbeAfterReady = false;
let consecutiveOk = 0;
const PROBE_FAST_MS = 2 * 60 * 1000;
const PROBE_SLOW_MS = 5 * 60 * 1000;
let probeEveryMs = PROBE_FAST_MS;
const peerScript = path.join(path.dirname(fileURLToPath(import.meta.url)), 'subway-peer.mjs');

function clearProbeTimers() {
  clearTimeout(probeStartTimer);
  clearInterval(probeInterval);
  probeStartTimer = null;
  probeInterval = null;
}

function runRouteProbe(sock) {
  if (probeRunning) return;
  if (!sock || sock !== ws || sock.readyState !== 1) return;
  probeRunning = true;
  const args = [peerScript, 'call', '--sid', info.sid, '--pk', info.x25519_pub, '--relay', info.url,
    '--method', 'GET', '--path', '/api/peer/ping', '--timeout-ms', '10000'];
  execFile(process.execPath, args, { timeout: 15000 }, (err, stdout) => {
    probeRunning = false;
    let routed = false;
    if (!err && stdout) {
      const line = stdout.split('\n').find((l) => l.trim());
      try { const j = JSON.parse(line); routed = !!(j && typeof j.status === 'number'); } catch { routed = false; }
    }
    if (routed) {
      if (firstProbeAfterReady) log('route ok');
      consecutiveOk += 1;
      if (consecutiveOk === 5 && probeEveryMs !== PROBE_SLOW_MS) {
        probeEveryMs = PROBE_SLOW_MS;
        clearInterval(probeInterval);
        probeInterval = setInterval(() => runRouteProbe(sock), probeEveryMs);
      }
    } else {
      consecutiveOk = 0;
      probeEveryMs = PROBE_FAST_MS;
      log('route check failed; reconnecting');
      if (sock === ws) { if (typeof sock.terminate === 'function') sock.terminate(); else sock.close(); }
    }
    firstProbeAfterReady = false;
  });
}

function scheduleRouteProbe(sock) {
  clearProbeTimers();
  firstProbeAfterReady = true;
  consecutiveOk = 0;
  probeEveryMs = PROBE_FAST_MS;
  probeStartTimer = setTimeout(() => {
    runRouteProbe(sock);
    probeInterval = setInterval(() => runRouteProbe(sock), probeEveryMs);
  }, 20000);
}

const station = createStation({
  secret: identity.secret, port, noiseStatic: identity.noiseStatic, fetchImpl: httpFetch,
  send: (d) => { if (ws && ws.readyState === 1) ws.send(d); },
});

if (listen) {
  try {
    const lan = createLanServer({
      listen,
      secret: identity.secret,
      port,
      noiseStatic: identity.noiseStatic,
      fetchImpl: httpFetch,
      onError: (err) => log('lan error', err.message || String(err)),
    });
    lan.ready.then(
      (addr) => log('lan listen', `${addr.host}:${addr.port}`),
      (err) => {
        console.error('lan listen failed:', err.message || String(err));
        if (!url) process.exit(1);
      },
    );
  } catch (err) {
    console.error('invalid --listen / SUBWAY_LAN_LISTEN:', err.message || String(err));
    process.exit(2);
  }
}

function connect() {
  log('connect');
  const sock = new WebSocket(`${url}/v1/station/${identity.sid}`);
  ws = sock;
  sock.binaryType = 'arraybuffer';
  sock.onmessage = (ev) => {
    if (typeof ev.data !== 'string') { station.onBinary(new Uint8Array(ev.data)); return; }
    let m = null;
    try { m = JSON.parse(ev.data); } catch { /* not json */ }
    if (m && m.t === 'challenge') { sock.send(JSON.stringify(signChallenge(identity, m.n))); return; }
    if (m && m.t === 'ready') { delay = 1000; log('ready'); scheduleRouteProbe(sock); return; }
    if (m && m.t === 'open') log('channel open', m.ch);
    if (m && m.t === 'close') log('channel close', m.ch);
    station.onText(ev.data);
  };
  let done = false;
  const gone = (code) => {
    if (done) return;
    done = true;
    clearInterval(pingTimer);
    clearProbeTimers();
    station.reset();
    log('disconnect', code ?? '');
    if (code === 4000) {
      // Replaced: another connector for this station took over. Don't knock it off in a loop.
      log('replaced by another connector for this station; retrying in 60s');
      setTimeout(connect, 60000);
      return;
    }
    const wait = delay * (0.8 + Math.random() * 0.4);
    delay = Math.min(delay * 2, 30000);
    setTimeout(connect, wait);
  };
  sock.onopen = () => {
    pingTimer = setInterval(() => { try { sock.send('ping'); } catch { /* closing */ } }, 25000);
  };
  sock.onclose = (ev) => gone(ev.code);
  sock.onerror = () => gone('error');
}
if (url) connect();
