# AI security and threat model

Short answer to "can an external, or any, AI do anything on our system?": **no.**
AI in HA Finance Intelligence is off by default, opt-in, advisory-only, and
tightly sandboxed. This page explains what protects you and names the residual
risks honestly.

## The one thing that matters most: AI output cannot act on the system

Model output is treated as **data, never as code or a command**.

- The AI gateway never writes a category itself. A classification is a
  *suggestion* returned to the UI; nothing is applied until you explicitly
  accept it (`POST /ai/apply`, treated as a manual decision), and applying
  refuses to overwrite a category you set by hand.
- Model output is validated and bounded before any use: a suggested category
  must resolve to one of the candidates you sent, a country must be a clean
  ISO-2 code, the vendor string is length-capped, confidence is clamped to 0-1.
- There is **no** `eval`, `exec`, `os.system`, or `subprocess` anywhere in the
  backend. The only dynamic SQL is the full-text search MATCH, whose table name
  is a hardcoded constant and whose search term is a bound parameter, not string
  interpolation. No AI text ever reaches SQL, a shell, or a file path.

So even a fully compromised or malicious model can, at worst, *suggest a wrong
category*, which you then reject. It cannot run code, exfiltrate the database, or
change data on its own.

## Off by default, opt-in, minimal egress

- The shipped defaults are `ai_enabled=false` and `privacy_mode=strict_local`;
  the default provider does nothing. Cloud AI is strictly opt-in.
- Privacy modes:
  - `strict_local` / `no_ai`: AI is refused entirely.
  - `local_llm`: calls your own local endpoint; the payload stays on your
    network.
  - `cloud_manual`: every call needs per-request approval; the payload is
    redacted first.
  - `cloud_auto`: sends automatically, but only after redaction and a
    sensitivity gate.
- What actually leaves the machine for a text classification is a minimal
  payload (`description`, `amount`, `currency`, `candidate_categories`) POSTed to
  the configured endpoint. For cloud modes the host must resolve to a public IP
  (an SSRF guard forbids loopback/LAN targets) and redirects are disabled so the
  API key cannot be bounced to a different host.

## One redaction choke-point for anything cloud-bound

- A single `redact_for_cloud()` function allow-lists only the four fields above
  and recursively masks PII in every string (card/PAN, IBAN, sort code, phone,
  long account numbers, postcode, email) before anything is sent or staged.
- Every cloud caller goes through it; no text cloud path bypasses redaction. A
  separate gate always blocks "never-cloud" categories and, under `cloud_auto`,
  refuses text that still looks sensitive after redaction.

## Authenticated, throttled, and audited

- Every `/api/*` route (except the read-only health probe) resolves an
  authenticated user through a global middleware gate; read-only roles cannot
  issue mutating requests, and no unauthenticated path reaches the AI gateway.
- New non-owner users land as **pending** with no data access until an owner
  approves them.
- The AI gateway is guarded (knobs, each disabled with `0`): per-user rate limit
  (`HAFI_AI_RATE_LIMIT_PER_MINUTE`, default 30), payload cap
  (`HAFI_AI_MAX_PAYLOAD_BYTES`, default ~100 KB), and a daily request budget
  (`HAFI_AI_DAILY_REQUEST_CAP`, default 500).
- The AI API key resolves env-first (`HAFI_AI_API_KEY`) then a UI-stored key that
  is encrypted at rest; it is write-only over the API, stripped from every read
  and export surface, and never logged.

## Residual risks (named honestly)

These are the ordinary risks of an opt-in outbound LLM integration, not remote
compromise:

1. **Images cannot be redacted.** Cloud vision extraction sends the raw image.
   This is mitigated, not eliminated: `cloud_auto` refuses a raw image unless
   explicitly approved (the import/receipt callers never auto-approve), and the
   UI warns per send. Audit rows store only the byte size, never the image.
2. **Vision upload routes** enforce the daily budget and a 15 MB size cap but
   not the per-minute rate limit or the 100 KB payload knob (those apply to the
   text routes). A small follow-up could extend a rate-limit-only guard to the
   two vision routes.
3. **A settings-manager can point the cloud base URL at any public endpoint.**
   This is by design (only a settings-manager can change it); the SSRF guard
   forbids private/loopback hosts but does not pin against DNS rebinding.
4. **Plaintext key fallback** exists when `HAFI_DB_KEY` is not set, mirroring how
   the two-factor secret is handled. Set `HAFI_DB_KEY` (at-rest encryption) to
   encrypt stored secrets.

## If you want maximum isolation

Run with `privacy_mode=strict_local` (the default) for no AI at all, or
`local_llm` pointed at an on-device model for AI with zero cloud egress. Set
`HAFI_DB_KEY` to encrypt everything at rest. Behind a directly exposed port, set
`HAFI_TRUST_PROXY_HEADERS=false` so identity headers cannot be forged.
