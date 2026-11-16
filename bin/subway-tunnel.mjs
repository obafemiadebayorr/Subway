#!/usr/bin/env node
// Subway Tunnel connector CLI: one outbound WebSocket to the relay, forwarding to the local HUD.
import path from 'node:path';
import { httpFetch } from '../tunnel/connector/httpFetch.mjs';
import { createStation, loadOrCreateIdentity, signChallenge, subwayDir, writeStationInfo } from '../tunnel/connector/station.mjs';

const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const url = (opt('--url') || process.env.SUBWAY_TUNNEL_URL || '').replace(/\/+$/, '');
const port = Number(opt('--port') || process.env.SUBWAY_HUD_PORT || 7890);
const dir = path.join(subwayDir(), 'tunnel');
const log = (...a) => console.log(new Date().toISOString(), ...a);

const identity = loadOrCreateIdentity(dir);
const info = writeStationInfo(dir, identity, url || null);

if (args.includes('--identity')) {
  console.log(JSON.stringify({ sid: info.sid, x25519_pub: info.x25519_pub, url: info.url }));
  process.exit(0);
}
if (!url) { console.error('usage: subway-tunnel --url wss://... [--port 7890] (or SUBWAY_TUNNEL_URL)'); process.exit(2); }
if (process.argv.includes("--exit-with-parent")) {
  const parent0 = process.ppid;
  setInterval(() => { if (process.ppid !== parent0 || process.ppid === 1) process.exit(0); }, 5000).unref();
}

let ws = null;
let delay = 1000;
let pingTimer = null;

const station = createStation({
  secret: identity.secret, port, noiseStatic: identity.noiseStatic, fetchImpl: httpFetch,
  send: (d) => { if (ws && ws.readyState === 1) ws.send(d); },
});

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
    if (m && m.t === 'ready') { delay = 1000; log('ready'); return; }
    if (m && m.t === 'open') log('channel open', m.ch);
    if (m && m.t === 'close') log('channel close', m.ch);
    station.onText(ev.data);
  };
  let done = false;
  const gone = (code) => {
    if (done) return;
    done = true;
    clearInterval(pingTimer);
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
connect();
