// RFC 6238 TOTP, computed in-test with Node's crypto so the MFA spec can submit a
// real 6-digit code (there is no TOTP dependency in this suite, by design). This
// mirrors the backend's own stdlib implementation (SHA-1, 6 digits, 30s period):
// backend/app/services/totp.py. The base32 secret comes straight off the /setup
// response, so decoding + HMAC here reproduces exactly what the server expects.

import { createHmac } from "node:crypto";

const PERIOD = 30;
const DIGITS = 6;

// Decode an unpadded RFC 4648 base32 secret (upper-case A-Z2-7) to bytes.
function base32Decode(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const clean = secret.toUpperCase().replace(/=+$/, "").replace(/\s+/g, "");
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const ch of clean) {
    const idx = alphabet.indexOf(ch);
    if (idx === -1) continue; // ignore anything outside the alphabet
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      out.push((value >>> bits) & 0xff);
    }
  }
  return Buffer.from(out);
}

function hotp(secret: string, counter: number): string {
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64BE(BigInt(counter)); // 64-bit big-endian counter
  const digest = createHmac("sha1", base32Decode(secret)).update(buf).digest();
  const offset = digest[digest.length - 1] & 0x0f; // dynamic truncation
  const bin =
    ((digest[offset] & 0x7f) << 24) |
    ((digest[offset + 1] & 0xff) << 16) |
    ((digest[offset + 2] & 0xff) << 8) |
    (digest[offset + 3] & 0xff);
  return (bin % 10 ** DIGITS).toString().padStart(DIGITS, "0");
}

// The 6-digit TOTP valid at `now` (ms since epoch; defaults to the current time).
export function totpCode(secret: string, now: number = Date.now()): string {
  return hotp(secret, Math.floor(now / 1000 / PERIOD));
}

// How far (seconds) we are into the current 30s window. Lets a caller avoid the
// period boundary so a single code stays valid across a multi-request flow.
export function secondsIntoPeriod(now: number = Date.now()): number {
  return Math.floor(now / 1000) % PERIOD;
}

export { PERIOD };
