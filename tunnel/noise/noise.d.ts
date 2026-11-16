export const PROTOCOL: 'Noise_IK_25519_ChaChaPoly_SHA256';
export const MAX_MESSAGE: number;
export type Keypair = { secretKey: Uint8Array; publicKey: Uint8Array };
export function generateKeypair(): Keypair;
export function keypairFromSecret(secretKey: Uint8Array): Keypair;
export class CipherState {
  constructor(k?: Uint8Array | null);
  hasKey(): boolean;
  encrypt(ad: Uint8Array, plaintext: Uint8Array): Uint8Array;
  decrypt(ad: Uint8Array, ciphertext: Uint8Array): Uint8Array;
  rekey(): void;
}
export type HandshakeOptions = {
  initiator: boolean;
  s: Keypair;
  rs?: Uint8Array;
  prologue?: Uint8Array;
  /** Tests only: a fixed ephemeral key. */
  e?: Keypair;
};
export class Handshake {
  constructor(opts: HandshakeOptions);
  readonly complete: boolean;
  readonly send: CipherState | null;
  readonly recv: CipherState | null;
  readonly remoteStatic: Uint8Array | null;
  readonly handshakeHash: Uint8Array;
  writeMessage(payload?: Uint8Array): Uint8Array;
  readMessage(message: Uint8Array): Uint8Array;
}
export function seal(cs: CipherState, plaintext: Uint8Array): Uint8Array;
export function open(cs: CipherState, ciphertext: Uint8Array): Uint8Array;
