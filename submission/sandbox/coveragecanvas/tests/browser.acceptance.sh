#!/usr/bin/env bash
set -euo pipefail
rm -f tests/.browser-acceptance-success.json artifacts/browser/browser-acceptance-receipt.json
APP_ROOT="$(pwd -P)"
test -f "$APP_ROOT/package.json"
npx --no-install vitest run
npm run build
set +e
ACCEPTANCE_OUTPUT="$(node tests/browser.acceptance.mjs 2>&1)"
ACCEPTANCE_STATUS=$?
set -e
printf '%s\n' "$ACCEPTANCE_OUTPUT"
if (( ACCEPTANCE_STATUS != 0 )); then
  rm -f tests/.browser-acceptance-success.json artifacts/browser/browser-acceptance-receipt.json
  exit "$ACCEPTANCE_STATUS"
fi
if ! grep -Fxq 'ZEROHANDOFF_BROWSER_ACCEPTANCE_OK' <<<"$ACCEPTANCE_OUTPUT"; then
  rm -f tests/.browser-acceptance-success.json artifacts/browser/browser-acceptance-receipt.json
  printf '%s\n' '### Error' 'Browser acceptance exited without ZEROHANDOFF_BROWSER_ACCEPTANCE_OK.' >&2
  exit 1
fi
test -f tests/.browser-acceptance-success.json
test -f artifacts/browser/browser-acceptance-receipt.json
