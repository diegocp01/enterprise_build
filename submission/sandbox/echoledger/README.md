# EchoLedger

EchoLedger is a local React, TypeScript, and Vite prototype for investigating one fictional customer-support call. It links a bundled 06:18 recording made from locally synthesized fictional voices and a pre-authored transcript to reversible presentation masking, contestable findings, a recurring-signal evidence chain, accountable action, local JSON export, and a reproducible reset.

The recording is an intelligible synthetic spoken fixture generated offline from pre-authored lines by `scripts/generate-synthetic-call.mjs`; it distinguishes the two fictional speakers and places every utterance at the matching transcript boundary. The checked-in WAV needs no voice tool at runtime. It is not real customer audio or a speech-to-text/model output.

All names, organizations, calls, excerpts, confidence values, classifications, owners, and actions are fictional fixed fixtures. “Transcription,” sensitive candidates, recurrence analysis, and proposed actions are deterministic prototype behavior requiring human review. There is no production AI, backend, authentication, database, telemetry, upload, persistence, external integration, or runtime application network call.

## Commands

```bash
npm install
npm run typecheck
npm test
npm run build
npm run dev -- --host 127.0.0.1
```

Healthcheck: `http://127.0.0.1:5173/`

Dependency gates used by delivery orchestration:

```bash
npm audit --omit=dev --audit-level=high
npm audit --audit-level=critical
```

`npm test` runs `tests/browser.acceptance.sh`. Its first filesystem action removes stale browser receipts under fail-fast shell handling, then it runs focused domain checks, builds the current production bundle, and drives that bundle in a real Chromium browser. The harness monitors navigation and runtime traffic from before initial navigation through reload, verifies full-range native playback, keyboard focus, synchronized evidence, all review recovery transitions, reversible masking, historical context recovery, constrained action assignment with field-level audit history, correlated downloads, product-owned post-download Blob revocation, reset cancellation/reopen/complete-state equivalence, 320px overflow, and bounded cleanup. Confirmed reset also revokes any still-live local Blob URL. Only a fully cleaned-up pass publishes `artifacts/browser-acceptance/receipt.json`.

## Browser prerequisite

The acceptance harness defaults to Google Chrome on macOS at `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`. On another clean machine, install a current Chromium-family browser and set its executable explicitly:

```bash
ECHOLEDGER_BROWSER=/absolute/path/to/chrome npm test
```

The browser starts headlessly with a fresh temporary profile, extensions disabled, and bounded startup, CDP, journey, request, and cleanup operations. A success receipt is written only after browser, preview server, download directory, and temporary profile cleanup succeed.
