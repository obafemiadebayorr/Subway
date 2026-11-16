#!/usr/bin/env node
/**
 * One-shot station-to-station tunnel call. Prints exactly one JSON line to stdout.
 * Usage: node bin/subway-peer.mjs call --sid S --pk K --relay URL --method GET --path /api/status [--token-env NAME] [--body-file PATH]
 */
import fs from 'node:fs';
import { TunnelClient } from '../tunnel/initiator/client.ts';

const te = new TextEncoder();
const td = new TextDecoder();

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
  const relay = String(args.relay || '');
  const method = String(args.method || 'GET').toUpperCase();
  const path = String(args.path || '/api/status');
  const tokenEnv = args['token-env'] ? String(args['token-env']) : '';
  const token = tokenEnv ? String(process.env[tokenEnv] || '') : '';
  if (!sid || !pk || !relay) return done({ error: 'missing sid, pk or relay' }, 1);

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
    client = new TunnelClient({ url: relay, sid, stationKey: pk, timeoutMs: Math.max(1000, hardMs - 5000) });
    const res = await client.request(path, { method, headers, body: body ?? null });
    const text = td.decode(res.body || new Uint8Array());
    clearTimeout(hard);
    try { client.close(); } catch {}
    return done({ status: res.status, body: text }, 0);
  } catch (e) {
    clearTimeout(hard);
    try { client && client.close(); } catch {}
    const msg = e && e.message ? e.message : String(e);
    return done({ error: msg }, 1);
  }
}

main().catch((e) => done({ error: e && e.message ? e.message : String(e) }, 1));
