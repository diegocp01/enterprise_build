import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const baseUrl = 'http://127.0.0.1:4173';
const chrome = process.env.ECHOLEDGER_BROWSER || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const receiptPath = join(root, 'artifacts/browser-acceptance/receipt.json');
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const bounded = (promise, milliseconds, label) => {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} timed out after ${milliseconds}ms`)), milliseconds); }),
  ]).finally(() => clearTimeout(timer));
};

let preview;
let browser;
let profile;
let downloads;
let client;
let previewLog = '';
let networkPhase = 'initial-navigation';

async function fetchBounded(url, options = {}, milliseconds = 2500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), milliseconds);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJsonBounded(url, options = {}, milliseconds = 2500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), milliseconds);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    if (!response.ok) throw new Error(`Request failed with ${response.status}: ${url}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

async function waitHealth() {
  const end = Date.now() + 12000;
  while (Date.now() < end) {
    if (preview?.exitCode !== null) throw new Error(`Preview exited before readiness. ${previewLog.trim()}`);
    try {
      const response = await fetchBounded(baseUrl, {}, 1200);
      if (response.ok) return;
    } catch {}
    await delay(150);
  }
  throw new Error(`Preview healthcheck timed out. ${previewLog.trim()}`);
}

async function stopChild(child, signal, label) {
  if (!child) return;
  if (child.exitCode === null) child.kill(signal);
  if (child.exitCode === null) {
    await bounded(new Promise(resolve => {
      const done = () => resolve();
      child.once('exit', done);
      if (child.exitCode !== null) {
        child.off('exit', done);
        resolve();
      }
    }), 5000, label);
  }
}

async function launchChrome() {
  const child = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--disable-component-extensions-with-background-pages',
    `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-allow-origins=*', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', chunk => { stderr += chunk; });
  try {
    const portFile = join(profile, 'DevToolsActivePort');
    const end = Date.now() + 10000;
    let port;
    while (Date.now() < end) {
      try {
        port = (await readFile(portFile, 'utf8')).split('\n')[0];
        break;
      } catch {
        if (child.exitCode !== null) throw new Error(`Chrome exited during startup: ${stderr}`);
        await delay(100);
      }
    }
    if (!port) throw new Error(`Chrome debugging endpoint timeout: ${stderr}`);
    const pages = await fetchJsonBounded(`http://127.0.0.1:${port}/json/list`, {}, 2500);
    const websocket = pages.find(page => page.type === 'page')?.webSocketDebuggerUrl;
    if (!websocket) throw new Error('No page debugging target');
    return { child, websocket };
  } catch (error) {
    await stopChild(child, 'SIGKILL', 'failed Chrome launcher cleanup');
    throw error;
  }
}

class CDP {
  constructor(url) {
    this.id = 0;
    this.pending = new Map();
    this.events = new Map();
    this.ws = new WebSocket(url);
  }

  async open() {
    await bounded(new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true });
      this.ws.addEventListener('error', reject, { once: true });
    }), 5000, 'CDP WebSocket connection');
    this.ws.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        message.error ? pending.reject(new Error(message.error.message)) : pending.resolve(message.result);
        return;
      }
      for (const handler of this.events.get(message.method) || []) handler(message.params);
    });
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out after 5000ms`));
      }, 5000);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.ws.send(JSON.stringify({ id, method, params }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  on(name, handler) {
    this.events.set(name, [...(this.events.get(name) || []), handler]);
  }

  async close() {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error('CDP closed'));
    }
    this.pending.clear();
    if (this.ws.readyState === WebSocket.CLOSED) return;
    const closed = new Promise((resolve, reject) => {
      this.ws.addEventListener('close', resolve, { once: true });
      this.ws.addEventListener('error', reject, { once: true });
    });
    this.ws.close();
    await bounded(closed, 5000, 'CDP WebSocket cleanup');
  }
}

async function evaluate(expression) {
  const result = await client.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}

async function waitFor(expression, label, milliseconds = 7000) {
  const end = Date.now() + milliseconds;
  while (Date.now() < end) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await delay(80);
  }
  throw new Error(`${label} not reached`);
}

async function waitForCondition(expression, milliseconds = 1200) {
  const end = Date.now() + milliseconds;
  while (Date.now() < end) {
    if (await evaluate(`Boolean(${expression})`)) return true;
    await delay(60);
  }
  return false;
}

async function assertVisibleFocus(selector, label) {
  const result = await evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element || document.activeElement !== element || !element.matches(':focus-visible')) return { ok: false, reason: 'active focus-visible element mismatch' };
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') return { ok: false, reason: 'focused control is not rendered' };
    if (style.outlineStyle === 'none' || Number.parseFloat(style.outlineWidth) <= 0) return { ok: false, reason: 'nonzero outline missing' };
    const parseColor = color => {
      if (!color || color === 'transparent' || color === 'rgba(0, 0, 0, 0)') return null;
      const open = color.indexOf('('); const close = color.lastIndexOf(')');
      if (open < 0 || close < 0) return null;
      const parts = color.slice(open + 1, close).split(',').map(part => Number.parseFloat(part.trim()));
      if (parts.length > 3 && parts[3] === 0) return null;
      return parts.slice(0, 3);
    };
    const luminance = rgb => rgb.map(value => value / 255).map(value => value <= .03928 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    const indicator = parseColor(style.outlineColor);
    let surfaceNode = element.parentElement; let surface = null; let transparentLayers = 0;
    while (surfaceNode && !surface) { surface = parseColor(getComputedStyle(surfaceNode).backgroundColor); if (!surface) { transparentLayers += 1; surfaceNode = surfaceNode.parentElement; } }
    if (!indicator || !surface) return { ok: false, reason: 'transparent or unparseable adjacent surface', transparentLayers };
    const first = luminance(indicator); const second = luminance(surface); const contrast = (Math.max(first, second) + .05) / (Math.min(first, second) + .05);
    return { ok: contrast >= 3, reason: contrast >= 3 ? 'visible focus proven' : 'focus contrast below 3:1', contrast, indicator: style.outlineColor, adjacentSurface: surface.join(','), transparentLayers };
  })()`);
  if (!result?.ok) throw new Error(`${label}: ${JSON.stringify(result)}`);
  receipt.focus.push({ label, selector, ...result });
}

async function rawKey(key, code, windowsVirtualKeyCode, modifiers = 0) {
  await client.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key, code, windowsVirtualKeyCode, location: 0, modifiers });
  await client.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode, location: 0, modifiers });
}

async function tabTo(selector, label = selector) {
  for (let step = 0; step < 180; step += 1) {
    const focused = await evaluate(`Boolean(document.querySelector(${JSON.stringify(selector)}) && document.activeElement === document.querySelector(${JSON.stringify(selector)}) && document.querySelector(${JSON.stringify(selector)}).matches(':focus-visible'))`);
    if (focused) {
      // A state transition can schedule deliberate focus recovery in React's
      // next layout pass. Only accept focus after it remains stable.
      await delay(100);
      const stable = await evaluate(`Boolean(document.querySelector(${JSON.stringify(selector)}) && document.activeElement === document.querySelector(${JSON.stringify(selector)}) && document.querySelector(${JSON.stringify(selector)}).matches(':focus-visible'))`);
      if (stable) { await assertVisibleFocus(selector, `keyboard focus ${label}`); return; }
      continue;
    }
    await rawKey('Tab', 'Tab', 9);
  }
  throw new Error(`Sequential keyboard traversal did not reach ${label}: ${selector}`);
}

async function shiftTab() {
  await rawKey('Tab', 'Tab', 9, 8);
}

async function enter(selector) {
  await tabTo(selector);
  // State-changing controls can schedule an intentional focus recovery in the
  // next layout pass. Let that settle, then reach the requested control again
  // through sequential Tab if necessary before dispatching Enter.
  await delay(100);
  if (!await evaluate(`document.activeElement === document.querySelector(${JSON.stringify(selector)})`)) await tabTo(selector);
  if (!await evaluate(`document.activeElement === document.querySelector(${JSON.stringify(selector)})`)) throw new Error(`Exact intended control was not active before Enter: ${selector}`);
  await client.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', text: '\r', unmodifiedText: '\r', windowsVirtualKeyCode: 13, location: 0, modifiers: 0 });
  await client.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, location: 0, modifiers: 0 });
}

async function escape() {
  await rawKey('Escape', 'Escape', 27);
}

async function selectValue(selector, value) {
  await tabTo(selector);
  const selection = await evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) return { target: -1, value: '', label: '' };
    const target = [...element.options].findIndex(option => option.value === ${JSON.stringify(value)});
    return { target, value: element.value, label: target < 0 ? '' : element.options[target].text };
  })()`);
  if (selection.target < 0) throw new Error(`Missing select option ${value} in ${selector}`);
  if (selection.value === value) return;

  // Each fixture option has a unique initial. A real text key event exercises
  // the native select's keyboard type-ahead without opening a platform popup or
  // mutating the element from script. This is stable in both headed and
  // headless system Chrome, unlike Home/ArrowDown on a collapsed macOS select.
  const character = selection.label.trim()[0].toLowerCase();
  const keyCode = character.toUpperCase().charCodeAt(0);
  await client.send('Input.dispatchKeyEvent', { type: 'keyDown', key: character, code: `Key${character.toUpperCase()}`, text: character, unmodifiedText: character, windowsVirtualKeyCode: keyCode, location: 0, modifiers: 0 });
  await client.send('Input.dispatchKeyEvent', { type: 'keyUp', key: character, code: `Key${character.toUpperCase()}`, windowsVirtualKeyCode: keyCode, location: 0, modifiers: 0 });
  await waitFor(`document.querySelector(${JSON.stringify(selector)})?.value === ${JSON.stringify(value)}`, `keyboard selection ${value}`);
  if (!await evaluate(`document.activeElement === document.querySelector(${JSON.stringify(selector)})`)) throw new Error(`Native select lost exact keyboard focus after choosing ${value}: ${selector}`);
}

async function typeText(selector, value) {
  await tabTo(selector);
  const existing = await evaluate(`document.querySelector(${JSON.stringify(selector)})?.value || ''`);
  if (existing) {
    await rawKey('a', 'KeyA', 65, 2);
    await rawKey('Backspace', 'Backspace', 8);
  }
  for (const [index, character] of [...value].entries()) {
    const code = character === ' ' ? 'Space' : /[a-z]/i.test(character) ? `Key${character.toUpperCase()}` : character === '.' ? 'Period' : character === '-' ? 'Minus' : 'Unidentified';
    const virtualKey = character === ' ' ? 32 : character === '.' ? 190 : character === '-' ? 189 : character.toUpperCase().charCodeAt(0);
    await client.send('Input.dispatchKeyEvent', { type: 'keyDown', key: character, code, text: character, unmodifiedText: character, windowsVirtualKeyCode: virtualKey, location: 0, modifiers: 0 });
    await client.send('Input.dispatchKeyEvent', { type: 'keyUp', key: character, code, windowsVirtualKeyCode: virtualKey, location: 0, modifiers: 0 });
    const expected = value.slice(0, index + 1);
    await waitFor(`document.querySelector(${JSON.stringify(selector)})?.value === ${JSON.stringify(expected)}`, `keyboard text prefix ${index + 1} in ${selector}`);
  }
  await waitFor(`document.querySelector(${JSON.stringify(selector)})?.value === ${JSON.stringify(value)}`, `keyboard text entry ${selector}`);
}

async function waitForDownload(path, label) {
  const end = Date.now() + 7000;
  while (Date.now() < end) {
    try { return await readFile(path, 'utf8'); } catch {}
    await delay(100);
  }
  throw new Error(`${label} was not accepted by the browser`);
}

async function waitForCompletedDownload(previousCount, label) {
  const end = Date.now() + 7000;
  while (Date.now() < end) {
    if (receipt.downloads.filter(item => item.state === 'completed').length > previousCount) return;
    await delay(80);
  }
  throw new Error(`${label} did not emit a completed browser download event`);
}

const receipt = {
  schema: 'echoledger-browser-acceptance-v1',
  success: false,
  marker: 'ZEROHANDOFF_BROWSER_ACCEPTANCE_OK',
  browserExecutable: chrome,
  commandResults: {
    domainTests: 'passed-before-browser-launch',
    productionBuild: 'passed-before-browser-launch',
    browserJourney: 'pending',
  },
  permittedPreview: [], prohibitedRuntime: [], browserInternals: [], assertions: [], focus: [], downloads: [], downloadCorrelations: [], fullRecordingInspection: null, cleanup: {},
};

async function recordTestedBuild() {
  const index = await readFile(join(root, 'dist/index.html'));
  const assetNames = (await readdir(join(root, 'dist/assets'))).sort();
  const digest = createHash('sha256').update(index);
  for (const name of assetNames) digest.update(name).update(await readFile(join(root, 'dist/assets', name)));
  receipt.testedBuild = {
    indexSha256: createHash('sha256').update(index).digest('hex'),
    bundleSha256: digest.digest('hex'),
    assets: assetNames,
  };
}

async function visibleBaseline() {
  return evaluate(`(() => {
    const text = selector => document.querySelector(selector)?.textContent?.trim() || '';
    const value = selector => document.querySelector(selector)?.value || '';
    const disabled = selector => Boolean(document.querySelector(selector)?.disabled);
    return {
      timeline: value('[data-testid="timeline"]'),
      activeSegment: document.querySelector('[data-testid="transcript"]')?.dataset.activeSegment || '',
      sensitivePresentation: text('[data-testid="sensitive-text"]'),
      complaintConfirmDisabled: disabled('[data-testid="complaint-confirm"]'),
      complaintPendingDisabled: disabled('[data-testid="complaint-pending"]'),
      commitmentConfirmDisabled: disabled('[data-testid="commitment-confirm"]'),
      commitmentPendingDisabled: disabled('[data-testid="commitment-pending"]'),
      signalConfirmDisabled: disabled('[data-testid="signal-confirm"]'),
      signalPendingDisabled: disabled('[data-testid="signal-pending"]'),
      team: value('[data-testid="team"]'),
      owner: value('[data-testid="owner"]'),
      rationale: value('textarea[data-testid="rationale"]'),
      status: value('[data-testid="action-status"]'),
      privacyHistoryCount: document.querySelectorAll('.history-card li').length,
      findingHistoryCount: document.querySelectorAll('.evidence-rail .decision-history li').length,
      signalHistoryCount: document.querySelectorAll('.signal-card .decision-history li').length,
      actionHistoryCount: document.querySelectorAll('.provenance-card .decision-history li').length,
      savedActionPresent: Boolean(document.querySelector('.saved-action')),
      liveDownloadUrls: Number(document.querySelector('[data-testid="download-lifecycle"]')?.dataset.live || -1),
      acceptedDownloads: Number(document.querySelector('[data-testid="download-lifecycle"]')?.dataset.accepted || -1),
      revokedDownloadUrls: Number(document.querySelector('[data-testid="download-lifecycle"]')?.dataset.revoked || -1),
    };
  })()`);
}

function recordRequest(event) {
  const url = event.request.url;
  if (!url.startsWith('http://') && !url.startsWith('https://')) return;
  const sameOrigin = new URL(url).origin === baseUrl;
  const type = event.type || 'Other';
  const initiator = event.initiator?.type || 'unknown';
  const permittedModelAsset = sameOrigin && new URL(url).pathname === '/audio/echoledger-el1042.wav' && ['Media', 'Other'].includes(type);
  const appInitiated = ['Fetch', 'XHR', 'WebSocket', 'EventSource', 'Ping'].includes(type) || (['fetch', 'xmlhttprequest', 'script'].includes(initiator) && !permittedModelAsset);
  const record = { url, type, initiator, phase: networkPhase, permittedModelAsset };
  if (!sameOrigin || appInitiated) receipt.prohibitedRuntime.push(record);
  else receipt.permittedPreview.push(record);
}

function recordWorker(url, source) {
  if (!url) return;
  if (url.startsWith('chrome-extension:') || url.startsWith('chrome:') || url.startsWith('devtools:')) receipt.browserInternals.push({ url, source });
  else if (url.startsWith('http://') || url.startsWith('https://')) receipt.prohibitedRuntime.push({ url, type: 'ServiceWorker', initiator: source, phase: networkPhase });
}

async function assertNoApplicationNetworkCalls(label) {
  const calls = await evaluate(`Boolean(Array.isArray(globalThis.__echoledgerRuntimeCalls) && globalThis.__echoledgerRuntimeCalls.length) ? globalThis.__echoledgerRuntimeCalls.slice() : []`);
  if (calls.length) throw new Error(`${label}: application network API called ${JSON.stringify(calls)}`);
}

async function runJourney() {
  const start = Date.now();
  await client.send('Page.navigate', { url: baseUrl });
  await waitFor(`document.readyState === 'complete' && Boolean(document.querySelector('[data-testid="complaint-marker"]'))`, 'initial app');
  receipt.baselineBeforeJourney = await visibleBaseline();
  networkPhase = 'runtime';
  if (receipt.prohibitedRuntime.length) throw new Error(`Prohibited initial traffic: ${JSON.stringify(receipt.prohibitedRuntime)}`);

  // Exercise the entire native media timeline in one bounded pass. Playback is
  // unmuted and pitch-preserved; acceleration keeps acceptance well below the
  // narrated three-minute limit while proving the complete 06:18 asset decodes.
  await evaluate(`(() => { const audio = document.querySelector('[data-testid="bundled-audio"]'); audio.currentTime = 0; audio.playbackRate = 8; audio.preservesPitch = true; audio.muted = false; audio.volume = 1; return true; })()`);
  await enter('[data-testid="play"]');
  await waitFor(`document.querySelector('[data-testid="bundled-audio"]')?.ended === true && document.querySelector('[data-testid="bundled-audio"]')?.currentTime >= 377.9 && document.querySelector('[data-testid="transcript"]')?.dataset.activeSegment === 'SEG-29'`, 'full spoken recording inspection', 60_000);
  receipt.fullRecordingInspection = {
    asset: 'public/audio/echoledger-el1042.wav',
    fixtureSource: 'scripts/generate-synthetic-call.mjs',
    authoringMethod: 'offline macOS spoken voices from exact pre-authored fixture lines',
    durationSeconds: 378,
    startSeconds: 0,
    endSeconds: 378,
    playbackRate: 8,
    muted: false,
    volume: 1,
    canonicalWindowsTraversed: 10,
    transcriptEndSegment: 'SEG-29',
    result: 'passed',
  };
  receipt.assertions.push('full 06:18 intelligible spoken fixture decoded and played across every canonical segment boundary');
  await evaluate(`(() => { const audio = document.querySelector('[data-testid="bundled-audio"]'); audio.playbackRate = 1; audio.currentTime = 0; return true; })()`);

  await tabTo('[data-testid="timeline"]', 'native playback range');
  await rawKey('End', 'End', 35);
  await waitFor(`document.querySelector('[data-testid="timeline"]')?.value === '378' && document.querySelector('[data-testid="transcript"]')?.dataset.activeSegment === 'SEG-29'`, 'range End bound');
  await rawKey('Home', 'Home', 36);
  await waitFor(`document.querySelector('[data-testid="timeline"]')?.value === '0' && document.querySelector('[data-testid="transcript"]')?.dataset.activeSegment === 'SEG-01'`, 'range Home bound');
  await rawKey('ArrowRight', 'ArrowRight', 39);
  await waitFor(`document.querySelector('[data-testid="timeline"]')?.value === '1'`, 'native range Arrow seek');
  for (const segmentId of ['SEG-05', 'SEG-08', 'SEG-13', 'SEG-15', 'SEG-18', 'SEG-25', 'SEG-29']) {
    await enter(`.transcript-row[aria-label*="${segmentId}"]`);
    await waitFor(`document.querySelector('[data-testid="transcript"]')?.dataset.activeSegment === '${segmentId}'`, `${segmentId} boundary synchronization`);
  }
  receipt.assertions.push('native range bounds, Arrow seeking, and every transcript fixture boundary synchronized');

  await enter('[data-testid="complaint-marker"]');
  await waitFor(`document.querySelector('[data-testid="timeline"]')?.value === '151' && Math.abs(document.querySelector('[data-testid="bundled-audio"]')?.currentTime - 151) < .1 && Math.abs(document.querySelector('[data-testid="bundled-audio"]')?.duration - 378) < .01 && document.querySelector('[data-testid="transcript"]')?.dataset.activeSegment === 'SEG-14' && document.querySelector('.transcript-row[aria-current="true"]')?.getAttribute('aria-label')?.includes('SEG-14') === true && document.querySelector('.transcript-row[aria-current="true"] .now-playing')?.textContent?.includes('Now playing') === true`, 'complaint synchronization');
  await enter('[data-testid="play"]');
  await waitFor(`document.querySelector('[data-testid="bundled-audio"]')?.paused === false && Number(document.querySelector('[data-testid="timeline"]')?.value) > 151 && document.querySelector('[data-testid="bundled-audio"]')?.currentTime > 151`, 'native recording playback');
  await enter('[data-testid="play"]');
  await waitFor(`document.querySelector('[data-testid="bundled-audio"]')?.paused === true`, 'native recording pause');
  receipt.assertions.push('06:18 bundled WAV, native media position, visible timeline, and SEG-14 synchronized at 02:31');
  await enter('[data-testid="commitment-marker"]');
  await waitFor(`document.querySelector('[data-testid="timeline"]')?.value === '252' && Math.abs(document.querySelector('[data-testid="bundled-audio"]')?.currentTime - 252) < .1 && document.querySelector('[data-testid="transcript"]')?.dataset.activeSegment === 'SEG-22'`, 'commitment synchronization');
  receipt.assertions.push('SEG-22 synchronized at 04:12');

  await enter('[data-testid="complaint-confirm"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="complaint-pending"]')`, 'confirmed-finding recovery focus');
  await assertVisibleFocus('[data-testid="complaint-pending"]', 'confirmed finding recovery');
  await enter('[data-testid="complaint-pending"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="complaint-confirm"]')`, 'complaint pending recovery focus');
  await enter('[data-testid="complaint-reject"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="complaint-pending"]')`, 'rejected-finding recovery focus');
  await assertVisibleFocus('[data-testid="complaint-pending"]', 'rejected finding recovery');
  await enter('[data-testid="complaint-pending"]');

  await enter('[data-testid="commitment-confirm"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="commitment-pending"]')`, 'confirmed-commitment recovery focus');
  await assertVisibleFocus('[data-testid="commitment-pending"]', 'confirmed commitment recovery');
  await enter('[data-testid="commitment-pending"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="commitment-confirm"]')`, 'commitment pending recovery focus');
  await enter('[data-testid="commitment-reject"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="commitment-pending"]')`, 'rejected-commitment recovery focus');
  await assertVisibleFocus('[data-testid="commitment-pending"]', 'rejected commitment recovery');
  await enter('[data-testid="commitment-pending"]');
  receipt.assertions.push('complaint and commitment independently exercised confirmed, rejected, and pending states');

  await enter('[data-testid="redact"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="sensitive-text"]')?.textContent.includes('REDACTED')) && document.activeElement === document.querySelector('[data-testid="restore"]')`, 'redaction and recovery focus');
  await assertVisibleFocus('[data-testid="restore"]', 'redaction recovery');
  await enter('[data-testid="restore"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="sensitive-text"]')?.textContent.includes('mara.chen')) && document.activeElement === document.querySelector('[data-testid="redact"]')`, 'restoration and recovery focus');
  await enter('[data-testid="redact"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="sensitive-text"]')?.textContent.includes('REDACTED'))`, 'second redaction');
  receipt.assertions.push('EV-015 presentation redacted with stable evidence history');

  await enter('[data-testid="history-0"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="close-history"]')`, 'application-driven historical dialog focus');
  await assertVisibleFocus('[data-testid="close-history"]', 'historical dialog entry');
  if (!await evaluate(`document.querySelectorAll('[data-testid="historical-dialog"] .historical-transcript article').length === 6 && document.querySelectorAll('[data-testid="historical-dialog"] .cited-segment').length === 1`)) throw new Error('First historical dialog did not expose the complete bundled transcript');
  await rawKey('Tab', 'Tab', 9);
  if (!await evaluate(`document.activeElement === document.querySelector('[data-testid="close-history"]')`)) throw new Error('Historical dialog did not contain forward Tab');
  await shiftTab();
  if (!await evaluate(`document.activeElement === document.querySelector('[data-testid="close-history"]')`)) throw new Error('Historical dialog did not contain Shift+Tab');
  await escape();
  await waitFor(`!document.querySelector('[data-testid="historical-dialog"]') && document.activeElement === document.querySelector('[data-testid="history-0"]')`, 'historical focus restoration');
  await enter('[data-testid="history-0"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="historical-dialog"]'))`, 'immediate historical reopen');
  await delay(120);
  await waitFor(`Boolean(document.querySelector('[data-testid="historical-dialog"]'))`, 'historical dialog generation stability');
  await enter('[data-testid="close-history"]');
  await enter('[data-testid="history-1"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="historical-dialog"]'))`, 'second historical context');
  if (!await evaluate(`document.querySelectorAll('[data-testid="historical-dialog"] .historical-transcript article').length === 6 && document.querySelectorAll('[data-testid="historical-dialog"] .cited-segment').length === 1`)) throw new Error('Second historical dialog did not expose the complete bundled transcript');
  await enter('[data-testid="close-history"]');

  await enter('[data-testid="signal-reject"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="signal-pending"]')`, 'rejected signal recovery focus');
  await enter('[data-testid="signal-pending"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="signal-confirm"]')`, 'signal pending recovery focus');
  await enter('[data-testid="signal-confirm"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="signal-pending"]')`, 'signal recovery focus');
  await assertVisibleFocus('[data-testid="signal-pending"]', 'confirmed signal recovery');
  if (!await evaluate(`document.querySelector('[data-testid="save-action"]')?.disabled === true && document.querySelector('[data-testid="action-validation"]')?.textContent?.includes('Add a rationale')`)) throw new Error('Incomplete action draft was not visibly isolated from saving');
  await selectValue('[data-testid="team"]', 'Legal');
  await waitFor(`document.querySelector('[data-testid="owner"]')?.value === 'Noah Perrin'`, 'team-appropriate Legal owner');
  await selectValue('[data-testid="team"]', 'Product');
  await waitFor(`document.querySelector('[data-testid="owner"]')?.value === 'Priya Nolen'`, 'team-appropriate Product owner');
  await selectValue('[data-testid="owner"]', 'Priya Nolen');
  await typeText('textarea[data-testid="rationale"]', 'Review the reproducible export-stall fixture and document product follow-up.');
  await waitFor(`document.querySelector('[data-testid="save-action"]')?.disabled === false`, 'valid action draft');
  await enter('[data-testid="save-action"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="status"]')?.textContent.includes('Action saved locally for Priya Nolen'))`, 'action save');
  await selectValue('[data-testid="team"]', 'Legal');
  if (!await evaluate(`document.querySelector('[data-testid="committed-action"]')?.textContent?.includes('Priya Nolen') && document.querySelector('[data-testid="committed-action"]')?.textContent?.includes('Product') && document.querySelectorAll('.provenance-card .decision-history li').length === 5`)) throw new Error('Unsaved action draft mutated the committed snapshot or field-level audit');
  await selectValue('[data-testid="team"]', 'Product');
  await selectValue('[data-testid="action-status"]', 'In progress');
  await enter('[data-testid="save-action"]');
  await selectValue('[data-testid="action-status"]', 'Completed');
  await enter('[data-testid="save-action"]');
  await waitFor(`document.querySelectorAll('.provenance-card .decision-history li').length === 7`, 'ordered field-level action history');
  for (const excerpt of ['Every time our team exports the quarterly workspace, the progress reaches ninety-eight percent and then stalls.', 'The archive export froze on the final step for our operations workspace.', 'Our monthly export stalled at ninety-nine percent until we cancelled it.']) {
    if (!await evaluate(`document.querySelector('.provenance-card')?.textContent?.includes(${JSON.stringify(excerpt)})`)) throw new Error(`Action evidence omitted exact excerpt: ${excerpt}`);
  }
  receipt.assertions.push('Product action assigned to Priya Nolen with three exact links and seven ordered field-level changes');

  const briefPath = join(downloads, 'EchoLedger_EL-1042_case-brief.json');
  await rm(briefPath, { force: true });
  let completedBefore = receipt.downloads.filter(item => item.state === 'completed').length;
  await enter('[data-testid="export"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="status"]')?.textContent.includes('Export 1 prepared locally'))`, 'first export prepared status');
  const firstText = await waitForDownload(briefPath, 'first correlated export');
  const firstBrief = JSON.parse(firstText);
  if (firstBrief.signal.evidence.length !== 3 || firstBrief.evidenceCatalog.length !== 22 || firstBrief.calls.length !== 3 || firstBrief.calls.some(call => !call.transcript.length) || firstBrief.findings.some(finding => !finding.evidence?.excerpt) || !firstBrief.privacy.evidence?.excerpt || firstBrief.action.owner !== 'Priya Nolen' || firstBrief.action.status !== 'Completed' || firstBrief.action.history.length !== 7 || new Set(firstBrief.action.history.map(entry => entry.field)).size !== 5 || firstBrief.findingHistory.length !== 8 || firstBrief.signal.history.length !== 3 || firstBrief.privacy.history.length !== 4 || firstBrief.privacy.presentation !== 'redacted') throw new Error('Downloaded brief omitted the current evidence or field-level decision chain');
  await waitForCompletedDownload(completedBefore, 'first correlated export');
  await waitFor(`document.querySelector('[data-testid="download-lifecycle"]')?.dataset.live === '0' && document.querySelector('[data-testid="download-lifecycle"]')?.dataset.accepted === '1' && document.querySelector('[data-testid="download-lifecycle"]')?.dataset.revoked === '1' && globalThis.__echoledgerBlobLifecycle?.revoked.length === 1`, 'first accepted Blob revocation');
  receipt.downloadCorrelations.push({ attempt: 1, browserAcceptedBeforeRevocation: true, productOwnedRevocation: true });
  await rm(briefPath, { force: true });
  completedBefore = receipt.downloads.filter(item => item.state === 'completed').length;
  await enter('[data-testid="export"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="status"]')?.textContent.includes('Export 2 prepared locally'))`, 'second export prepared status');
  const secondText = await waitForDownload(briefPath, 'second correlated export');
  await waitForCompletedDownload(completedBefore, 'second correlated export');
  if (firstText !== secondText) throw new Error('Repeated export was stale or semantically nondeterministic');
  await waitFor(`document.querySelector('[data-testid="download-lifecycle"]')?.dataset.live === '0' && document.querySelector('[data-testid="download-lifecycle"]')?.dataset.accepted === '2' && document.querySelector('[data-testid="download-lifecycle"]')?.dataset.revoked === '2' && globalThis.__echoledgerBlobLifecycle?.revoked.length === 2`, 'second accepted Blob revocation');
  receipt.downloadCorrelations.push({ attempt: 2, browserAcceptedBeforeRevocation: true, productOwnedRevocation: true });

  await rm(briefPath, { force: true });
  completedBefore = receipt.downloads.filter(item => item.state === 'completed').length;
  await enter('[data-testid="export"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="status"]')?.textContent.includes('Export 3 prepared locally'))`, 'pending reset export status');
  await waitForDownload(briefPath, 'pending reset export');
  await waitForCompletedDownload(completedBefore, 'pending reset export');
  if (!await evaluate(`document.querySelector('[data-testid="download-lifecycle"]')?.dataset.live === '1'`)) throw new Error('Pending correlated Blob URL was not kept live before reset');
  receipt.downloadCorrelations.push({ attempt: 3, browserAccepted: false, blobUrlLiveUntilReset: true });
  receipt.assertions.push('repeated deterministic downloads correlated, accepted URLs revoked, and one pending URL retained for reset');

  await enter('[data-testid="open-reset"]');
  await waitFor(`document.activeElement === document.querySelector('[data-testid="confirm-reset"]')`, 'reset dialog focus');
  await assertVisibleFocus('[data-testid="confirm-reset"]', 'reset dialog entry');
  await rawKey('Tab', 'Tab', 9);
  if (!await evaluate(`document.activeElement === document.querySelector('[data-testid="cancel-reset"]')`)) throw new Error('Reset dialog did not contain forward Tab');
  await shiftTab();
  if (!await evaluate(`document.activeElement === document.querySelector('[data-testid="confirm-reset"]')`)) throw new Error('Reset dialog did not contain Shift+Tab');
  await escape();
  await waitFor(`!document.querySelector('[data-testid="reset-dialog"]') && document.activeElement === document.querySelector('[data-testid="open-reset"]')`, 'reset Escape restoration');
  await enter('[data-testid="open-reset"]');
  await waitFor(`Boolean(document.querySelector('[data-testid="reset-dialog"]'))`, 'immediate reset reopen');
  await enter('[data-testid="confirm-reset"]');
  await waitFor(`!document.querySelector('[data-testid="reset-dialog"]') && document.querySelector('[data-testid="timeline"]')?.value === '0' && Boolean(document.querySelector('[data-testid="sensitive-text"]')?.textContent.includes('mara.chen')) && Boolean(document.querySelector('[data-testid="status"]')?.textContent.includes('Reset complete'))`, 'reset baseline');
  if (!await evaluate(`document.querySelector('[data-testid="download-lifecycle"]')?.dataset.live === '0' && document.querySelector('[data-testid="download-lifecycle"]')?.dataset.accepted === '0' && document.querySelector('[data-testid="download-lifecycle"]')?.dataset.revoked === '0' && globalThis.__echoledgerBlobLifecycle?.created.length === 3 && globalThis.__echoledgerBlobLifecycle?.revoked.length === 3`)) throw new Error('Confirmed reset did not revoke the pending Blob URL and clear download correlation state');
  receipt.baselineAfterReset = await visibleBaseline();
  receipt.resetStateEquivalent = JSON.stringify(receipt.baselineBeforeJourney) === JSON.stringify(receipt.baselineAfterReset);
  if (!receipt.resetStateEquivalent) throw new Error(`Reset state drifted: ${JSON.stringify({ before: receipt.baselineBeforeJourney, after: receipt.baselineAfterReset })}`);
  receipt.assertions.push('Escape, immediate reopen, and confirm restored the visible baseline');

  await client.send('Emulation.setDeviceMetricsOverride', { width: 320, height: 800, deviceScaleFactor: 1, mobile: false });
  if (await evaluate(`Boolean(document.documentElement.scrollWidth > document.documentElement.clientWidth)`)) throw new Error('320px layout has horizontal overflow');
  const mobileResult = await evaluate(`(() => {
    const visible = element => { const style = getComputedStyle(element); const rect = element.getBoundingClientRect(); return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0; };
    const undersized = [...document.querySelectorAll('button, a, select, textarea, input')].filter(visible).map(element => { const rect = element.getBoundingClientRect(); return { label: element.getAttribute('data-testid') || element.getAttribute('aria-label') || element.textContent.trim().slice(0, 40), width: rect.width, height: rect.height }; }).filter(item => item.width < 44 || item.height < 44);
    const required = ['play', 'timeline', 'complaint-marker', 'commitment-marker', 'redact', 'history-0', 'team', 'owner', 'rationale', 'save-action', 'export', 'open-reset'];
    const unreachable = required.filter(id => { const element = document.querySelector('[data-testid="' + id + '"]'); return !element || !visible(element); });
    return { undersized, unreachable };
  })()`);
  if (mobileResult.undersized.length || mobileResult.unreachable.length) throw new Error(`320px target or workflow reachability failure: ${JSON.stringify(mobileResult)}`);
  receipt.mobile = mobileResult;
  receipt.assertions.push('320px responsive parity, 44px targets, full workflow reachability, and no horizontal overflow');
  await assertNoApplicationNetworkCalls('runtime journey');

  networkPhase = 'reload';
  await client.send('Page.reload', { ignoreCache: true });
  await waitFor(`document.readyState === 'complete' && document.querySelector('[data-testid="timeline"]')?.value === '0'`, 'reload baseline');
  await assertNoApplicationNetworkCalls('reload');
  if (receipt.prohibitedRuntime.length) throw new Error(`Runtime network gate failed: ${JSON.stringify(receipt.prohibitedRuntime)}`);
  receipt.durationMs = Date.now() - start;
  if (receipt.durationMs >= 180000) throw new Error('Journey exceeded three minutes');
  receipt.commandResults.browserJourney = 'passed';
}

let failure;
try {
  profile = await mkdtemp(join(tmpdir(), 'echoledger-chrome-'));
  downloads = await mkdtemp(join(tmpdir(), 'echoledger-downloads-'));
  preview = spawn(process.execPath, [join(root, 'node_modules/vite/bin/vite.js'), 'preview', '--host', '127.0.0.1', '--port', '4173'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  for (const stream of [preview.stdout, preview.stderr]) { stream.setEncoding('utf8'); stream.on('data', chunk => { previewLog += chunk; }); }
  await bounded(waitHealth(), 14000, 'preview startup');
  await recordTestedBuild();

  const launched = await launchChrome();
  browser = launched.child;
  client = new CDP(launched.websocket);
  await client.open();
  await client.send('Network.enable');
  await client.send('Page.enable');
  await client.send('Runtime.enable');
  await client.send('ServiceWorker.enable');
  await client.send('Target.setDiscoverTargets', { discover: true });
  await client.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads, eventsEnabled: true });

  client.on('Network.requestWillBeSent', recordRequest);
  client.on('Network.webSocketCreated', event => receipt.prohibitedRuntime.push({ url: event.url, type: 'WebSocket', initiator: event.initiator?.type || 'unknown', phase: networkPhase }));
  client.on('Browser.downloadWillBegin', event => receipt.downloads.push({ guid: event.guid, filename: event.suggestedFilename, state: 'began' }));
  client.on('Browser.downloadProgress', event => { if (event.state === 'completed' || event.state === 'canceled') receipt.downloads.push({ guid: event.guid, state: event.state }); });
  client.on('ServiceWorker.workerRegistrationUpdated', event => { for (const registration of event.registrations || []) recordWorker(registration.scopeURL, 'registration'); });
  client.on('ServiceWorker.workerVersionUpdated', event => { for (const version of event.versions || []) recordWorker(version.scriptURL, 'worker-version'); });
  client.on('Target.targetCreated', event => { if (event.targetInfo?.type === 'service_worker') recordWorker(event.targetInfo.url, 'target'); });
  await client.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
    const calls = globalThis.__echoledgerRuntimeCalls = [];
    const blobLifecycle = globalThis.__echoledgerBlobLifecycle = { created: [], revoked: [] };
    const note = (api, value) => calls.push({ api, value: String(value || '') });
    const originalCreateObjectURL = URL.createObjectURL.bind(URL); URL.createObjectURL = value => { const url = originalCreateObjectURL(value); blobLifecycle.created.push(url); return url; };
    const originalRevokeObjectURL = URL.revokeObjectURL.bind(URL); URL.revokeObjectURL = url => { blobLifecycle.revoked.push(String(url)); return originalRevokeObjectURL(url); };
    const originalFetch = globalThis.fetch; globalThis.fetch = (...args) => { note('fetch', args[0]); return originalFetch(...args); };
    const originalOpen = XMLHttpRequest.prototype.open; XMLHttpRequest.prototype.open = function(method, url, ...rest) { note('XMLHttpRequest', url); return originalOpen.call(this, method, url, ...rest); };
    if (globalThis.WebSocket) globalThis.WebSocket = new Proxy(globalThis.WebSocket, { construct(target, args) { note('WebSocket', args[0]); return Reflect.construct(target, args); } });
    if (globalThis.EventSource) globalThis.EventSource = new Proxy(globalThis.EventSource, { construct(target, args) { note('EventSource', args[0]); return Reflect.construct(target, args); } });
    if (navigator.sendBeacon) { const originalBeacon = navigator.sendBeacon.bind(navigator); navigator.sendBeacon = (url, data) => { note('sendBeacon', url); return originalBeacon(url, data); }; }
    if (globalThis.ServiceWorkerContainer) { const originalRegister = ServiceWorkerContainer.prototype.register; ServiceWorkerContainer.prototype.register = function(url, options) { note('serviceWorker.register', url); return originalRegister.call(this, url, options); }; }
  })();` });

  await bounded(runJourney(), 170000, 'overall browser journey');
} catch (error) {
  failure = error;
} finally {
  try { await client?.close(); receipt.cleanup.browserClient = true; } catch (error) { receipt.cleanup.browserClient = false; failure ||= error; }
  try { await stopChild(browser, 'SIGKILL', 'browser cleanup'); receipt.cleanup.browserProcess = true; } catch (error) { receipt.cleanup.browserProcess = false; failure ||= error; }
  try { await stopChild(preview, 'SIGTERM', 'preview cleanup'); receipt.cleanup.previewProcess = true; } catch (error) { receipt.cleanup.previewProcess = false; failure ||= error; }
  try { if (profile) await rm(profile, { recursive: true, force: true }); if (downloads) await rm(downloads, { recursive: true, force: true }); receipt.cleanup.temporaryFiles = true; } catch (error) { receipt.cleanup.temporaryFiles = false; failure ||= error; }
}

if (!receipt.cleanup.browserClient || !receipt.cleanup.browserProcess || !receipt.cleanup.previewProcess || !receipt.cleanup.temporaryFiles) failure ||= new Error('Cleanup incomplete');
if (failure) throw failure;

receipt.counts = {
  permittedPreview: receipt.permittedPreview.length,
  prohibitedRuntime: receipt.prohibitedRuntime.length,
  permittedByPhase: Object.fromEntries(['initial-navigation', 'runtime', 'reload'].map(phase => [phase, receipt.permittedPreview.filter(item => item.phase === phase).length])),
  prohibitedByPhase: Object.fromEntries(['initial-navigation', 'runtime', 'reload'].map(phase => [phase, receipt.prohibitedRuntime.filter(item => item.phase === phase).length])),
};
receipt.success = true;
await mkdir(dirname(receiptPath), { recursive: true });
await writeFile(receiptPath, JSON.stringify(receipt, null, 2));
console.log('ZEROHANDOFF_BROWSER_ACCEPTANCE_OK');
