# CoverageCanvas

CoverageCanvas is a deterministic, local-only shift coverage planner for a fictional field-service team. Dispatchers can inspect eight roles across three shifts and three zones, see why a technician is blocked, make one safe assignment, undo it, and restore the exact baseline.

The demo begins at **7 filled / 1 uncovered**. Open **Morning · North · Safety inspection** (OSHA-30), attempt Theo Brooks to see both his certification mismatch and Morning double-booking, then assign eligible Amara Cole to reach **8 / 0**. Undo and Reset return the original **7 / 1** plan.

## Local development

Prerequisites:

- Node.js 22 or newer and npm.
- Google Chrome or Chromium 138 or newer for production-browser acceptance.
- The harness discovers standard Chrome/Chromium installations on macOS, Windows, and Linux. Set `BROWSER_EXECUTABLE` to an absolute executable path to override discovery; no machine-specific path is embedded.

```bash
npm install
npm run typecheck
npm test
npm run build
npm run dev -- --host 127.0.0.1
```

`npm test` is the complete deterministic gate. Its wrapper deletes any prior browser receipt, runs Vitest, builds the current production bundle, and drives a fresh second production build in a real Chrome preview. The browser journey proves that opening the assignment desk moves focus to its first technician radio, then covers keyboard cancellation and immediate reopen, invalid and valid assignment paths, reset directly from rejected and committed states, visible focus with 3:1 indicator contrast, undo, reset after undo, repeated reset determinism, reload equivalence, 320 CSS-pixel layout, empty browser storage, and runtime network isolation.

The browser harness starts `vite preview`, waits for a bounded HTTP health check, installs its network gate before the first application navigation, and writes matching machine-readable success receipts to `tests/.browser-acceptance-success.json` and `artifacts/browser/browser-acceptance-receipt.json` only after browser and preview cleanup succeeds. To inspect the production build manually in a second terminal:

```bash
npm run build
npm exec --no -- vite preview --host 127.0.0.1 --port 4173 --strictPort
curl --fail --silent --show-error http://127.0.0.1:4173/ >/dev/null
```

Override the browser executable when necessary:

```bash
BROWSER_EXECUTABLE=/absolute/path/to/chrome-or-chromium npm test
```

Audit commands used by delivery orchestration:

```bash
npm audit --omit=dev --audit-level=high
npm audit --audit-level=critical
```

## Technical boundary

All names, schedules, certifications, roles, and assignments are fictional typed fixtures bundled with the application. State exists only in React memory. CoverageCanvas has no backend, authentication, persistence, analytics, service worker, database, external API, or application-initiated network request.
