/**
 * Noise_IK_25519_ChaChaPoly_SHA256, for Subway Tunnel.
 *
 * Follows the Noise Protocol Framework, revision 34 (noiseprotocol.org/noise.html), using
 * audited primitives from the noble libraries. Plain JS so it loads in Node (inside
 * node_modules, where type stripping is off) and in React Native.
 *
 * IK: the initiator (phone) already knows the responder's (station's) static key, from the
 * pairing QR. It sends its own static key, encrypted, in the first message.
 *   <- s
 *   ...
 *   -> e, es, s, ss
 *   <- e, ee, se
 */
import { x25519 } from '@noble/curves/ed25519.js';
import { chacha20poly1305 } from '@noble/ciphers/chacha.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { hmac } from '@noble/hashes/hmac.js';

export const PROTOCOL = 'Noise_IK_25519_ChaChaPoly_SHA256';
const DHLEN = 32;
const HASHLEN = 32;
const TAGLEN = 16;
export const MAX_MESSAGE = 65535;
const MAX_NONCE = 2n ** 64n - 1n;

const enc = new TextEncoder();
const EMPTY = new Uint8Array(0);

function concat(...parts) {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

function nonceBytes(n) {
  // 32 bits of zeros, then the 64-bit counter little-endian.
  const b = new Uint8Array(12);
  const v = new DataView(b.buffer);
  v.setUint32(4, Number(n & 0xffffffffn), true);
  v.setUint32(8, Number(n >> 32n), true);
  return b;
}

function hkdf2(ck, ikm) {
  const temp = hmac(sha256, ck, ikm);
  const out1 = hmac(sha256, temp, Uint8Array.of(1));
  const out2 = hmac(sha256, temp, concat(out1, Uint8Array.of(2)));
  return [out1, out2];
}

export function generateKeypair() {
  const secretKey = x25519.utils.randomSecretKey();
  return { secretKey, publicKey: x25519.getPublicKey(secretKey) };
}

export function keypairFromSecret(secretKey) {
  return { secretKey, publicKey: x25519.getPublicKey(secretKey) };
}

function dh(kp, pub) {
  return x25519.getSharedSecret(kp.secretKey, pub);
}

/** One direction of transport encryption. */
export class CipherState {
  constructor(k) {
    this.k = k || null;
    this.n = 0n;
  }
  hasKey() { return this.k !== null; }
  encrypt(ad, plaintext) {
    if (!this.k) return plaintext;
    if (this.n >= MAX_NONCE) throw new Error('noise: nonce exhausted');
    const out = chacha20poly1305(this.k, nonceBytes(this.n), ad).encrypt(plaintext);
    this.n += 1n;
    return out;
  }
  decrypt(ad, ciphertext) {
    if (!this.k) return ciphertext;
    if (this.n >= MAX_NONCE) throw new Error('noise: nonce exhausted');
    const out = chacha20poly1305(this.k, nonceBytes(this.n), ad).decrypt(ciphertext);
    this.n += 1n; // only after a successful decrypt
    return out;
  }
  /** Rekey(): REKEY(k) = ENCRYPT(k, maxnonce, zerolen, zeros)[0:32]. */
  rekey() {
    if (!this.k) return;
    const nonce = nonceBytes(MAX_NONCE);
    this.k = chacha20poly1305(this.k, nonce, EMPTY).encrypt(new Uint8Array(32)).subarray(0, 32);
  }
}

class SymmetricState {
  constructor() {
    const name = enc.encode(PROTOCOL);
    this.h = name.length <= HASHLEN ? concat(name, new Uint8Array(HASHLEN - name.length)) : sha256(name);
    this.ck = this.h;
    this.cs = new CipherState(null);
  }
  mixKey(ikm) {
    const [ck, k] = hkdf2(this.ck, ikm);
    this.ck = ck;
    this.cs = new CipherState(k);
  }
  mixHash(data) { this.h = sha256(concat(this.h, data)); }
  encryptAndHash(pt) {
    const ct = this.cs.encrypt(this.h, pt);
    this.mixHash(ct);
    return ct;
  }
  decryptAndHash(ct) {
    const pt = this.cs.decrypt(this.h, ct);
    this.mixHash(ct);
    return pt;
  }
  split() {
    const [k1, k2] = hkdf2(this.ck, EMPTY);
    return [new CipherState(k1), new CipherState(k2)];
  }
}

/**
 * Handshake for one side.
 * opts: { initiator: bool, s: keypair, rs?: Uint8Array (initiator: the station key),
 *         prologue?: Uint8Array, e?: keypair (tests only) }
 * After both messages, `complete` is true and `send`/`recv` are the transport ciphers.
 */
export class Handshake {
  constructor(opts) {
    this.initiator = !!opts.initiator;
    this.s = opts.s;
    this.rs = opts.rs || null;
    this.e = opts.e || null;
    this.re = null;
    this.ss = new SymmetricState();
    this.ss.mixHash(opts.prologue || EMPTY);
    if (this.initiator) {
      if (!this.rs) throw new Error('noise: IK initiator needs the responder static key');
      this.ss.mixHash(this.rs);
    } else {
      this.ss.mixHash(this.s.publicKey);
    }
    this.step = 0;
    this.complete = false;
    this.send = null;
    this.recv = null;
  }

  /** The remote static key: the device key, on the station side, after message 1. */
  get remoteStatic() { return this.rs; }
  get handshakeHash() { return this.ss.h; }

  writeMessage(payload = EMPTY) {
    if (this.initiator && this.step === 0) {
      if (!this.e) this.e = generateKeypair();
      this.ss.mixHash(this.e.publicKey);
      this.ss.mixKey(dh(this.e, this.rs));               // es
      const sCt = this.ss.encryptAndHash(this.s.publicKey); // s
      this.ss.mixKey(dh(this.s, this.rs));               // ss
      const pCt = this.ss.encryptAndHash(payload);
      this.step = 1;
      return this.checkLen(concat(this.e.publicKey, sCt, pCt));
    }
    if (!this.initiator && this.step === 1) {
      if (!this.e) this.e = generateKeypair();
      this.ss.mixHash(this.e.publicKey);
      this.ss.mixKey(dh(this.e, this.re));               // ee
      this.ss.mixKey(dh(this.e, this.rs));               // se
      const pCt = this.ss.encryptAndHash(payload);
      this.finish();
      return this.checkLen(concat(this.e.publicKey, pCt));
    }
    throw new Error('noise: writeMessage out of order');
  }

  readMessage(message) {
    if (message.length > MAX_MESSAGE) throw new Error('noise: message too long');
    if (!this.initiator && this.step === 0) {
      if (message.length < DHLEN + DHLEN + TAGLEN + TAGLEN) throw new Error('noise: message 1 too short');
      this.re = message.subarray(0, DHLEN);
      this.ss.mixHash(this.re);
      this.ss.mixKey(dh(this.s, this.re));               // es
      this.rs = this.ss.decryptAndHash(message.subarray(DHLEN, DHLEN + DHLEN + TAGLEN)); // s
      this.ss.mixKey(dh(this.s, this.rs));               // ss
      const payload = this.ss.decryptAndHash(message.subarray(DHLEN + DHLEN + TAGLEN));
      this.step = 1;
      return payload;
    }
    if (this.initiator && this.step === 1) {
      if (message.length < DHLEN + TAGLEN) throw new Error('noise: message 2 too short');
      this.re = message.subarray(0, DHLEN);
      this.ss.mixHash(this.re);
      this.ss.mixKey(dh(this.e, this.re));               // ee
      this.ss.mixKey(dh(this.s, this.re));               // se
      const payload = this.ss.decryptAndHash(message.subarray(DHLEN));
      this.finish();
      return payload;
    }
    throw new Error('noise: readMessage out of order');
  }

  finish() {
    const [c1, c2] = this.ss.split();
    this.send = this.initiator ? c1 : c2;
    this.recv = this.initiator ? c2 : c1;
    this.complete = true;
    this.step = 2;
    // Ephemeral secrets are no longer needed.
    if (this.e) this.e.secretKey.fill(0);
  }

  checkLen(m) {
    if (m.length > MAX_MESSAGE) throw new Error('noise: message too long');
    return m;
  }
}

/** Transport helpers after the handshake. Associated data is empty, as the spec requires. */
export function seal(cs, plaintext) {
  if (plaintext.length + TAGLEN > MAX_MESSAGE) throw new Error('noise: message too long');
  return cs.encrypt(EMPTY, plaintext);
}

export function open(cs, ciphertext) {
  return cs.decrypt(EMPTY, ciphertext);
}
