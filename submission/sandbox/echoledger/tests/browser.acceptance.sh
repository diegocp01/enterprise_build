#!/usr/bin/env bash
set -euo pipefail
rm -f artifacts/browser-acceptance/receipt.json tests/.browser-acceptance-success.json
node --test tests/domain.test.mjs
npm run build
if ! acceptance_output="$(node tests/browser.acceptance.mjs 2>&1)"; then
  printf '%s\n' "$acceptance_output" >&2
  printf '%s\n' '### Error: browser acceptance failed' >&2
  exit 1
fi
printf '%s\n' "$acceptance_output"
if ! printf '%s\n' "$acceptance_output" | grep -Fqx 'ZEROHANDOFF_BROWSER_ACCEPTANCE_OK'; then
  printf '%s\n' '### Error: browser acceptance success marker missing' >&2
  exit 1
fi
