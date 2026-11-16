import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Handshake, generateKeypair, keypairFromSecret, open, seal } from './noise.js';

const hex = (s) => Uint8Array.from(Buffer.from(s, 'hex'));
const toHex = (b) => Buffer.from(b).toString('hex');

// Noise_IK_25519_ChaChaPoly_SHA256 from the cacophony vector set (haskell-cryptography/cacophony).
const VECTOR = JSON.parse(readFileSync(new URL('./ik-vector.json', import.meta.url), 'utf8'));

test('matches the cacophony IK vector byte for byte', () => {
  const v = VECTOR;
  const init = new Handshake({
    initiator: true,
    prologue: hex(v.init_prologue),
    s: keypairFromSecret(hex(v.init_static)),
    e: keypairFromSecret(hex(v.init_ephemeral)),
    rs: hex(v.init_remote_static),
  });
  const resp = new Handshake({
    initiator: false,
    prologue: hex(v.resp_prologue),
    s: keypairFromSecret(hex(v.resp_static)),
    e: keypairFromSecret(hex(v.resp_ephemeral)),
  });

  const [m1, m2, ...rest] = v.messages;
  const c1 = init.writeMessage(hex(m1.payload));
  assert.equal(toHex(c1), m1.ciphertext, 'message 1');
  assert.equal(toHex(resp.readMessage(c1)), m1.payload);
  assert.equal(toHex(resp.remoteStatic), toHex(keypairFromSecret(hex(v.init_static)).publicKey));

  const c2 = resp.writeMessage(hex(m2.payload));
  assert.equal(toHex(c2), m2.ciphertext, 'message 2');
  assert.equal(toHex(init.readMessage(c2)), m2.payload);

  assert.equal(toHex(init.handshakeHash), v.handshake_hash);
  assert.equal(toHex(resp.handshakeHash), v.handshake_hash);

  // Transport messages alternate, initiator first.
  rest.forEach((m, i) => {
    const [from, to] = i % 2 === 0 ? [init, resp] : [resp, init];
    const ct = seal(from.send, hex(m.payload));
    assert.equal(toHex(ct), m.ciphertext, `transport ${i}`);
    assert.equal(toHex(open(to.recv, ct)), m.payload);
  });
});

function pair() {
  const station = generateKeypair();
  const device = generateKeypair();
  const init = new Handshake({ initiator: true, s: device, rs: station.publicKey, prologue: new TextEncoder().encode('subway-tunnel/1') });
  const resp = new Handshake({ initiator: false, s: station, prologue: new TextEncoder().encode('subway-tunnel/1') });
  return { station, device, init, resp };
}

test('round trip with fresh keys; the station learns the device key', () => {
  const { device, init, resp } = pair();
  resp.readMessage(init.writeMessage(new TextEncoder().encode('hello')));
  assert.deepEqual(resp.remoteStatic, device.publicKey);
  init.readMessage(resp.writeMessage());
  const msg = new TextEncoder().encode('{"t":"req"}');
  assert.deepEqual(open(resp.recv, seal(init.send, msg)), msg);
  assert.deepEqual(open(init.recv, seal(resp.send, msg)), msg);
});

test('a wrong station key fails the handshake (a MITM relay cannot answer)', () => {
  const station = generateKeypair();
  const impostor = generateKeypair();
  const device = generateKeypair();
  const init = new Handshake({ initiator: true, s: device, rs: station.publicKey });
  const resp = new Handshake({ initiator: false, s: impostor });
  assert.throws(() => resp.readMessage(init.writeMessage()));
});

test('tampered or replayed transport messages are rejected', () => {
  const { init, resp } = pair();
  resp.readMessage(init.writeMessage());
  init.readMessage(resp.writeMessage());
  const ct = seal(init.send, new Uint8Array([1, 2, 3]));
  const bad = ct.slice();
  bad[0] ^= 1;
  assert.throws(() => open(resp.recv, bad));
  open(resp.recv, ct); // the failed decrypt did not advance the nonce
  assert.throws(() => open(resp.recv, ct), 'replay fails: the nonce moved on');
});

test('messages out of order throw', () => {
  const { init, resp } = pair();
  assert.throws(() => resp.writeMessage());
  assert.throws(() => init.readMessage(new Uint8Array(48)));
});
