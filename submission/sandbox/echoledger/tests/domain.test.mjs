import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const domain = readFileSync(new URL('../src/domain.ts', import.meta.url), 'utf8');
const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const packageJson = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
const acceptanceShell = readFileSync(new URL('./browser.acceptance.sh', import.meta.url), 'utf8');
const acceptanceDriver = readFileSync(new URL('./browser.acceptance.mjs', import.meta.url), 'utf8');
const recordingGenerator = readFileSync(new URL('../scripts/generate-synthetic-call.mjs', import.meta.url), 'utf8');
const recording = readFileSync(new URL('../public/audio/echoledger-el1042.wav', import.meta.url));

test('fixtures are immutable and expose exact canonical evidence', () => {
  assert.match(domain, /Object\.freeze\(\[/);
  for (const value of ['EL-1042', 'EL-1017', 'EL-0998', 'SEG-14', 'SEG-15', 'SEG-22', 'EV-015', 'EXPORT_STALL_V1']) assert.match(domain, new RegExp(value));
});

test('the bundled recording is a complete six minute eighteen second WAV fixture', () => {
  assert.equal(recording.subarray(0, 4).toString('ascii'), 'RIFF');
  assert.equal(recording.subarray(8, 12).toString('ascii'), 'WAVE');
  const dataOffset = recording.indexOf(Buffer.from('data'));
  assert.ok(dataOffset > 0, 'WAV data chunk is present');
  const byteRate = recording.readUInt32LE(28);
  const dataBytes = recording.readUInt32LE(dataOffset + 4);
  assert.equal(dataBytes / byteRate, 378);
  const audioFormat = recording.readUInt16LE(20);
  const channels = recording.readUInt16LE(22);
  const sampleRate = recording.readUInt32LE(24);
  const bitsPerSample = recording.readUInt16LE(34);
  assert.equal(audioFormat, 1, 'recording uses uncompressed PCM');
  assert.equal(channels, 1, 'recording is a mono call fixture');
  assert.equal(bitsPerSample, 16, 'recording preserves speech-shaped sample detail');
  const samples = new Int16Array(recording.buffer, recording.byteOffset + dataOffset + 8, dataBytes / 2);
  const distinct = new Set(samples.subarray(0, Math.min(samples.length, sampleRate * 180)));
  assert.ok(distinct.size > 512, 'recording contains varied audible waveform data, not digital silence');
  const rms = (start, end) => {
    let energy = 0;
    const from = Math.floor(start * sampleRate);
    const to = Math.floor(end * sampleRate);
    for (let index = from; index < to; index += 1) energy += samples[index] ** 2;
    return Math.sqrt(energy / (to - from));
  };
  const fixtureWindows = [[0, 32], [32, 76], [76, 136], [136, 151], [151, 168], [168, 202], [202, 252], [252, 281], [281, 333], [333, 378]];
  for (const [start, end] of fixtureWindows) assert.ok(rms(start, end) > 250, `canonical ${start}-${end} second window contains audible synthetic speech`);
  assert.match(recordingGenerator, /execFileSync\('\/usr\/bin\/say'/, 'the offline authoring source uses an intelligible system speech voice');
  assert.match(recordingGenerator, /segment\.text/, 'the exact pre-authored fixture lines are passed to speech synthesis');
  assert.doesNotMatch(recordingGenerator, /renderPhoneticFallback|vowelFormants|noiseSeed/, 'tone or noise fallback cannot replace spoken output');
});

test('the deterministic baseline and serializer cover every audit chain', () => {
  for (const value of ['createInitialState', 'subjectId', 'redactionHistory', 'findingHistory', 'signalHistory', 'appendActionAudit', 'previousValue', 'resultingValue', 'semanticCaseBrief']) assert.match(domain, new RegExp(value));
  assert.doesNotMatch(domain, /Date\.now|Math\.random|new Date/);
  for (const value of ['evidenceCatalog', "resolveEvidence('EV-015')", "resolveEvidence('EV-022')", 'previousSnapshot', 'resultingSnapshot']) assert.match(domain, new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
});

test('the app exposes the complete keyboard-operable workflow', () => {
  for (const value of ['complaint-marker', 'commitment-marker', 'redact', 'team', 'owner', 'rationale', 'export', 'open-reset', 'confirm-reset']) assert.match(app, new RegExp(`data-testid="${value}"`));
  assert.match(app, /data-testid=\{`history-\$\{index\}`\}/);
  assert.match(app, /DecisionHistory/);
  assert.match(app, /role="status"/);
  assert.match(app, /Escape/);
  assert.match(app, /action\.committed/);
  assert.match(app, /conservative handoff window/);
  assert.match(app, /revokeAllDownloads/);
});

test('the allowlisted command surface and fresh browser receipt gate remain intact', () => {
  assert.equal(packageJson.scripts.test, 'bash tests/browser.acceptance.sh');
  assert.equal(packageJson.scripts.typecheck, 'tsc -b --pretty false');
  assert.equal(packageJson.scripts.build, 'tsc -b && vite build');
  assert.equal(acceptanceShell.split('\n')[1], 'set -euo pipefail');
  assert.equal(acceptanceShell.split('\n')[2], 'rm -f artifacts/browser-acceptance/receipt.json tests/.browser-acceptance-success.json');
  for (const value of ['artifacts/browser-acceptance/receipt.json', 'fetchJsonBounded', 'recordTestedBuild', 'fullRecordingInspection', 'confirmed finding recovery', 'rejected finding recovery', 'resetStateEquivalent', 'permittedByPhase', 'prohibitedByPhase', 'ZEROHANDOFF_BROWSER_ACCEPTANCE_OK']) assert.match(acceptanceDriver, new RegExp(value.replaceAll('/', '\\/')));
});
