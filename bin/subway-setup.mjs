#!/usr/bin/env node
/**
 * `subway setup --headless`: one command (after `npm i -g`) to bring a headless
 * station online. Implements plan/headless-station.md §2, steps 1-5.
 *
 * Everything except key/directory creation is a dry run unless --apply is passed.
 * OS service definitions and commands come from service_install.py, which never
 * executes anything itself without --apply. No secret is ever printed.
 */
import { spawn, spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SERVICE_PY = path.join(ROOT, 'service_install.py');
const B32 = 'abcdefghijklmnopqrstuvwxyz234567';
const args = process.argv.slice(2);
const has = (flag) => args.includes(flag);
const opt = (flag, fallback = undefined) => {
  const i = args.indexOf(flag);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
const log = (...parts) => console.log('[SUBWAY]', ...parts);
const fail = (msg) => { console.error('[SUBWAY] ' + msg); process.exit(1); };

function base32(bytes) {
  let bits = 0, value = 0, out = '';
  for (const byte of bytes) {
    value = (value << 8) | byte; bits += 8;
    while (bits >= 5) { out += B32[(value >>> (bits - 5)) & 31]; bits -= 5; }
  }
  if (bits > 0) out += B32[(value << (5 - bits)) & 31];
  return out;
}

function atomic(file, data, mode = 0o600) {
  const tmp = `${file}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
  fs.writeFileSync(tmp, data, { mode });
  fs.renameSync(tmp, file);
  try { fs.chmodSync(file, mode); } catch { /* best effort */ }
}

function subwayDir() {
  if (process.env.SUBWAY_DIR) return process.env.SUBWAY_DIR;
  const home = os.homedir();
  const old = path.join(home, '.subway-context');
  try {
    const st = fs.lstatSync(old);
    if (st.isDirectory() && !st.isSymbolicLink()) return old;
  } catch { /* missing */ }
  return path.join(home, '.subway');
}

function resolvePython() {
  if (process.env.PYTHON_BIN) return { bin: process.env.PYTHON_BIN, prefix: [] };
  const candidates = process.platform === 'win32'
    ? [{ bin: 'python3', prefix: [] }, { bin: 'py', prefix: ['-3'] }, { bin: 'python', prefix: [] }]
    : [{ bin: 'python3', prefix: [] }, { bin: 'python', prefix: [] }];
  for (const c of candidates) {
    try {
      if (spawnSync(c.bin, [...c.prefix, '--version'], { stdio: 'ignore' }).status === 0) return c;
    } catch { /* try next */ }
  }
  return candidates[0];
}

/** Same file layout as tunnel/connector/station.mjs: ed25519.pem, x25519.key, connector.secret. */
function ensureIdentity(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch { /* best effort */ }
  const edFile = path.join(dir, 'ed25519.pem');
  const xSecretFile = path.join(dir, 'x25519.key');
  const xPublicFile = path.join(dir, 'x25519.pub');
  const secretFile = path.join(dir, 'connector.secret');

  let ed;
  try { ed = crypto.createPrivateKey(fs.readFileSync(edFile)); } catch { ed = null; }
  if (!ed) {
    ed = crypto.generateKeyPairSync('ed25519').privateKey;
    atomic(edFile, ed.export({ type: 'pkcs8', format: 'pem' }));
  }
  const jwk = crypto.createPublicKey(ed).export({ format: 'jwk' });
  const edRaw = new Uint8Array(Buffer.from(jwk.x, 'base64url'));
  const sid = base32(new Uint8Array(crypto.createHash('sha256').update(edRaw).digest())).slice(0, 26);

  let xSecret = null, xPublic = null;
  try {
    const s = fs.readFileSync(xSecretFile);
    const p = fs.readFileSync(xPublicFile);
    if (s.length === 32 && p.length === 32) { xSecret = new Uint8Array(s); xPublic = new Uint8Array(p); }
  } catch { /* missing or corrupt: rotate */ }
  if (!xSecret) {
    const pair = crypto.generateKeyPairSync('x25519');
    const pub = pair.publicKey.export({ format: 'jwk' });
    const priv = pair.privateKey.export({ format: 'jwk' });
    xPublic = new Uint8Array(Buffer.from(pub.x, 'base64url'));
    xSecret = new Uint8Array(Buffer.from(priv.d, 'base64url'));
    atomic(xSecretFile, Buffer.from(xSecret));
    atomic(xPublicFile, Buffer.from(xPublic));
  }

  let secret = '';
  try { secret = fs.readFileSync(secretFile, 'utf8').trim(); } catch { /* missing */ }
  if (!/^[0-9a-f]{64}$/.test(secret)) {
    secret = crypto.randomBytes(32).toString('hex');
    atomic(secretFile, secret + '\n');
  }
  // Short, QR-less code derived from (never exposing) the connector secret: stable across re-runs.
  const shortCode = base32(new Uint8Array(
    crypto.createHmac('sha256', secret).update('subway-headless-link').digest())).slice(0, 8);
  const info = {
    sid,
    x25519_pub: Buffer.from(xPublic).toString('base64'),
    url: process.env.SUBWAY_TUNNEL_URL || null,
    updated_at: new Date().toISOString(),
  };
  atomic(path.join(dir, 'station.json'), JSON.stringify(info, null, 2) + '\n', 0o600);
  return { sid, shortCode, info };
}

function checkRuntime() {
  log('node', process.version);
  const py = resolvePython();
  const version = spawnSync(py.bin, [...py.prefix, '--version'], { encoding: 'utf8' });
  if (version.status !== 0) fail('Python 3 not found. Install python3 and re-run `subway setup --headless`.');
  log('python', (version.stdout || version.stderr || '').trim() || py.bin);
  return py;
}

function serviceArgs(py, dir) {
  const forwarded = [
    SERVICE_PY,
    '--platform', process.platform,
    '--home', os.homedir(),
    '--subway-dir', dir,
    '--command', 'subway', 'tunnel',
  ];
  if (has('--apply')) forwarded.push('--apply');
  if (has('--uninstall')) forwarded.push('--uninstall');
  if (process.env.SUBWAY_SERVICE_UID) forwarded.push('--uid', process.env.SUBWAY_SERVICE_UID);
  if (process.env.SUBWAY_SERVICE_USER) forwarded.push('--user', process.env.SUBWAY_SERVICE_USER);
  return [...py.prefix, ...forwarded];
}

function reboot() {
  const plat = process.platform;
  let cmd;
  if (plat === 'darwin') cmd = ['sudo', 'fdesetup', 'authrestart'];
  else if (plat === 'win32') cmd = ['powershell', '-Command', "Suspend-BitLocker -MountPoint 'C:' -RebootCount 1; Restart-Computer -Force"];
  else cmd = ['sudo', 'systemctl', 'reboot'];
  if (has('--dry-run')) {
    log('dry run:', cmd.join(' '));
    return;
  }
  const r = spawnSync(cmd[0], cmd.slice(1), { stdio: 'inherit' });
  if (r.error) fail('reboot failed: ' + r.error.message);
  process.exit(r.status || 0);
}

function main() {
  if (args[0] === 'reboot') { reboot(); return; }
  if (has('--help') || has('-h')) {
    log('usage: subway setup --headless [--apply] [--uninstall]');
    log('  --apply      write the service definition and register start-on-login');
    log('  --uninstall  unregister and delete the service definition (needs --apply)');
    return;
  }
  const py = checkRuntime();

  // Steps 1-2: SUBWAY_DIR + identity.
  const dir = subwayDir();
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const identity = ensureIdentity(path.join(dir, 'tunnel'));
  log('SUBWAY_DIR', dir);

  if (has('--uninstall')) {
    log('step 4: uninstall start-on-login');
  } else {
    // Step 2 (continued): print the link identity and short code.
    log('station id (link code)', identity.sid);
    log('8-char link code', identity.shortCode);
    log('link a peer with this code in the HUD; the link is marked "own" on approval');

    // Step 3: optional hardware grants. Terminal is off by default and own-link-only.
    log('step 3: vdisplay -> if no display is detected, use the virtual-display helper or an HDMI dummy plug');
    log('step 3: terminal grant is OFF by default; enable "Terminal" on the own link in HUD settings');
  }

  // Step 4: start-on-login (dry run unless --apply).
  log('step 4: ' + (has('--apply') ? 'installing' : 'previewing') + ' start-on-login');
  const result = spawnSync(py.bin, serviceArgs(py, dir), { stdio: 'inherit', env: process.env });
  if (result.error) fail('could not run service_install.py: ' + result.error.message);
  if (result.status !== 0) process.exit(result.status || 1);

  // Step 5: logs + removal instructions.
  log('step 5: logs', path.join(dir, 'logs', 'station.out.log'),
      '/', path.join(dir, 'logs', 'station.err.log'));
  log('remove: subway setup --headless --apply --uninstall');
  if (!has('--apply')) log('dry run only; re-run with --apply to install the service');
}

main();
