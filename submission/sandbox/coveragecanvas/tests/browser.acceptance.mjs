import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

const ROOT = process.cwd();
const RECEIPT = join(ROOT, 'tests', '.browser-acceptance-success.json');
const ARTIFACT_RECEIPT = join(ROOT, 'artifacts', 'browser', 'browser-acceptance-receipt.json');
const OVERALL_TIMEOUT_MS = 150_000;

function resolveBrowserExecutable() {
  if (process.env.BROWSER_EXECUTABLE) return process.env.BROWSER_EXECUTABLE;
  const executableNames = process.platform === 'win32'
    ? ['chrome.exe', 'chromium.exe']
    : ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'];
  const pathCandidates = (process.env.PATH ?? '')
    .split(process.platform === 'win32' ? ';' : ':')
    .flatMap((directory) => executableNames.map((name) => join(directory, name)));
  const platformCandidates = process.platform === 'darwin'
    ? [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
      ]
    : process.platform === 'win32'
      ? [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)']]
          .filter(Boolean)
          .map((directory) => join(directory, 'Google', 'Chrome', 'Application', 'chrome.exe'))
      : [];
  return [...pathCandidates, ...platformCandidates].find((candidate) => existsSync(candidate));
}

const CHROME = resolveBrowserExecutable();

function deadline(promise, milliseconds, label) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} timed out after ${milliseconds}ms`)), milliseconds); }),
  ]).finally(() => clearTimeout(timer));
}

function runCommand(command, args, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: ROOT, stdio: 'inherit' });
    let timedOut = false;
    let forceTimer;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGTERM');
      forceTimer = setTimeout(() => child.kill('SIGKILL'), 2_000);
    }, timeoutMs);
    child.once('error', (error) => { clearTimeout(timer); clearTimeout(forceTimer); reject(error); });
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      clearTimeout(forceTimer);
      code === 0 && !timedOut ? resolve() : reject(new Error(timedOut ? `${command} ${args.join(' ')} timed out and was stopped` : `${command} exited with ${code ?? signal}`));
    });
  });
}

async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => server.once('error', reject).listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitForHttp(url, timeoutMs) {
  const end = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < end) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
      if (response.ok) return;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) { lastError = error; }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Preview was not ready: ${lastError?.message ?? 'unknown error'}`);
}

async function stopChild(child, label) {
  if (!child || child.exitCode !== null || child.signalCode) return;
  child.kill('SIGTERM');
  try {
    await deadline(new Promise((resolve) => child.once('exit', resolve)), 5_000, `${label} shutdown`);
  } catch {
    child.kill('SIGKILL');
    if (child.exitCode !== null || child.signalCode) return;
    await deadline(new Promise((resolve) => child.once('exit', resolve)), 3_000, `${label} forced shutdown`);
  }
}

class CdpClient {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }
  async connect() {
    await deadline(new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', () => reject(new Error('CDP WebSocket connection failed')), { once: true });
    }), 8_000, 'CDP WebSocket connection');
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        message.error ? pending.reject(new Error(message.error.message)) : pending.resolve(message.result);
        return;
      }
      for (const listener of this.listeners.get(message.method) ?? []) listener(message.params ?? {});
    });
    this.socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) pending.reject(new Error('CDP connection closed'));
      this.pending.clear();
    });
  }
  on(method, listener) {
    const listeners = this.listeners.get(method) ?? [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }
  send(method, params = {}, timeoutMs = 8_000) {
    const id = this.nextId++;
    const request = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
    return deadline(request, timeoutMs, `CDP ${method}`).catch((error) => {
      this.pending.delete(id);
      throw error;
    });
  }
  async close() {
    for (const pending of this.pending.values()) pending.reject(new Error('CDP client cleanup rejected the pending request'));
    this.pending.clear();
    if (this.socket.readyState === WebSocket.CLOSED) return;
    const closed = new Promise((resolve) => this.socket.addEventListener('close', resolve, { once: true }));
    if (this.socket.readyState < WebSocket.CLOSING) this.socket.close();
    await deadline(closed, 3_000, 'CDP client cleanup');
  }
}

async function launchChrome(profileDir, downloadDir) {
  assert.ok(CHROME, 'Chrome or Chromium 138+ was not found. Set BROWSER_EXECUTABLE to its absolute executable path.');
  assert.ok(existsSync(CHROME), `Chrome executable not found at ${CHROME}. Set BROWSER_EXECUTABLE.`);
  const child = spawn(CHROME, [
    '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`,
    `--download-default-directory=${downloadDir}`, '--disable-extensions',
    '--disable-component-extensions-with-background-pages', '--disable-background-networking',
    '--no-first-run', '--no-default-browser-check', '--disable-sync', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  try {
    const endpoint = await deadline(new Promise((resolve, reject) => {
      child.stderr.setEncoding('utf8');
      child.stderr.on('data', (chunk) => {
        stderr += chunk;
        const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) resolve(match[1]);
      });
      child.once('error', reject);
      child.once('exit', (code) => reject(new Error(`Chrome exited before CDP startup (${code}): ${stderr.slice(-800)}`)));
    }), 15_000, 'Chrome startup');
    return { child, browserEndpoint: endpoint };
  } catch (error) {
    await stopChild(child, 'Chrome startup failure');
    throw error;
  }
}

async function fetchJson(url, label) {
  const response = await fetch(url, { signal: AbortSignal.timeout(2_000) });
  assert.ok(response.ok, `${label} returned HTTP ${response.status}`);
  return response.json();
}

function parseRgb(value) {
  const parts = value.match(/[\d.]+/g)?.slice(0, 3).map(Number);
  assert.equal(parts?.length, 3, `Expected RGB color, got ${value}`);
  return parts;
}

function luminance([r, g, b]) {
  const convert = (channel) => {
    const value = channel / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  };
  return .2126 * convert(r) + .7152 * convert(g) + .0722 * convert(b);
}

function contrast(a, b) {
  const [lighter, darker] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (lighter + .05) / (darker + .05);
}

async function journey(client, appUrl, network) {
  const startedAt = Date.now();
  const focusObservations = [];
  const evaluate = async (expression) => {
    let result;
    try {
      // Serialize inside the page so a native object or accidental DOM value
      // can never cross the CDP boundary as a remote object graph.
      const serializedExpression = `(async () => JSON.stringify((await (${expression})) ?? null))()`;
      result = await client.send('Runtime.evaluate', { expression: serializedExpression, returnByValue: true, awaitPromise: true });
    } catch (error) {
      throw new Error(`Browser evaluation transport failed for ${expression.slice(0, 180)}: ${error.message}`);
    }
    if (result.exceptionDetails) throw new Error(`Browser evaluation failed: ${result.exceptionDetails.text}`);
    assert.equal(typeof result.result.value, 'string', 'Browser evaluation did not return serialized JSON');
    return JSON.parse(result.result.value);
  };
  const waitFor = async (expression, label, timeoutMs = 8_000) => {
    const end = Date.now() + timeoutMs;
    while (Date.now() < end) {
      // CDP cannot return DOM nodes by value. Normalize every readiness probe
      // to a primitive boolean before it crosses the protocol boundary.
      if (await evaluate(`Boolean(${expression})`)) return;
      await new Promise((resolve) => setTimeout(resolve, 75));
    }
    throw new Error(`Timed out waiting for ${label}`);
  };
  const pressEnter = async () => {
    await client.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', text: '\r', unmodifiedText: '\r', windowsVirtualKeyCode: 13, location: 0, modifiers: 0 });
    await client.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, location: 0, modifiers: 0 });
  };
  const pressRaw = async (key, code, windowsVirtualKeyCode) => {
    await client.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key, code, windowsVirtualKeyCode, location: 0, modifiers: 0 });
    await client.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode, location: 0, modifiers: 0 });
  };
  const focus = async (selector) => {
    // Establish keyboard modality with a real, sequential key event before
    // targeting the exact control. Chrome then applies :focus-visible to the
    // programmatic focus used to make every activation deterministic.
    await pressRaw('Tab', 'Tab', 9);
    const focused = await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return false; el.focus({ focusVisible: true }); return document.activeElement === el; })()`);
    assert.equal(focused, true, `Could not focus exact control ${selector}`);
  };
  const pressSpace = async () => {
    await client.send('Input.dispatchKeyEvent', { type: 'keyDown', key: ' ', code: 'Space', text: ' ', unmodifiedText: ' ', windowsVirtualKeyCode: 32, location: 0, modifiers: 0 });
    await client.send('Input.dispatchKeyEvent', { type: 'keyUp', key: ' ', code: 'Space', windowsVirtualKeyCode: 32, location: 0, modifiers: 0 });
  };
  const assertVisibleFocus = async (selector) => {
    const proof = await evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      const style = getComputedStyle(el);
      const isTransparent = (color) => color === 'transparent' || color === 'rgba(0, 0, 0, 0)';
      let parent = el.parentElement;
      let parentBackground = 'rgba(0, 0, 0, 0)';
      while (parent && isTransparent(parentBackground)) {
        parentBackground = getComputedStyle(parent).backgroundColor;
        parent = parent.parentElement;
      }
      const outlineOffset = parseFloat(style.outlineOffset);
      // A non-negative outline offset places the indicator outside the
      // control, so the surrounding surface is adjacent on both edges. The
      // control fill is only adjacent when an inset/negative outline is used.
      const surfaces = [parentBackground, ...(outlineOffset < 0 ? [style.backgroundColor] : [])]
        .filter((color, index, values) => !isTransparent(color) && values.indexOf(color) === index);
      const rect = el.getBoundingClientRect();
      return { active: document.activeElement === el, focusVisible: el.matches(':focus-visible'), rendered: rect.width > 0 && rect.height > 0, outlineWidth: parseFloat(style.outlineWidth), outlineOffset, outlineColor: style.outlineColor, boxShadow: style.boxShadow, adjacentSurfaces: surfaces };
    })()`);
    assert.ok(proof?.active && proof.focusVisible && proof.rendered, `${selector} lacks exact rendered :focus-visible focus: ${JSON.stringify(proof)}`);
    assert.ok(proof.outlineWidth > 0 || proof.boxShadow !== 'none', `${selector} has no nonzero focus indicator`);
    assert.ok(proof.adjacentSurfaces.length > 0, `${selector} has no measurable adjacent focus surface`);
    const surfaceContrasts = proof.adjacentSurfaces.map((surface) => ({
      surface,
      contrastRatio: Number(contrast(parseRgb(proof.outlineColor), parseRgb(surface)).toFixed(2)),
    }));
    for (const observation of surfaceContrasts) {
      assert.ok(observation.contrastRatio >= 3, `${selector} focus contrast ${observation.contrastRatio.toFixed(2)} against ${observation.surface} is below 3:1`);
    }
    focusObservations.push({
      selector,
      indicatorColor: proof.outlineColor,
      indicatorWidthPx: proof.outlineWidth,
      indicatorOffsetPx: proof.outlineOffset,
      boxShadow: proof.boxShadow,
      adjacentSurfaces: surfaceContrasts,
    });
  };

  await client.send('Runtime.addBinding', { name: '__coverageCanvasNetworkAttempt' });
  client.on('Runtime.bindingCalled', ({ name, payload }) => {
    if (name !== '__coverageCanvasNetworkAttempt') return;
    const attempt = JSON.parse(payload);
    network.prohibited.push({
      url: attempt.detail,
      type: attempt.kind,
      initiator: 'application-api',
      phase: network.phase,
      documentPhase: attempt.documentPhase,
    });
  });

  await client.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
    window.__appNetworkAttempts = [];
    const record = (kind, detail) => {
      const attempt = { kind, detail: String(detail), documentPhase: document.readyState };
      window.__appNetworkAttempts.push(attempt);
      window.__coverageCanvasNetworkAttempt(JSON.stringify(attempt));
    };
    window.fetch = function(input) { const url = typeof input === 'string' ? input : input.url; record('fetch', url); return Promise.reject(new Error('Application fetch blocked by acceptance gate')); };
    const OriginalXHR = window.XMLHttpRequest; window.XMLHttpRequest = class extends OriginalXHR { open(_method, url) { record('xhr', url); throw new Error('Application XMLHttpRequest blocked by acceptance gate'); } };
    const OriginalWS = window.WebSocket; window.WebSocket = new Proxy(OriginalWS, { construct(_Target, args) { record('websocket', args[0]); throw new Error('Application WebSocket blocked by acceptance gate'); } });
    const OriginalES = window.EventSource; window.EventSource = new Proxy(OriginalES, { construct(_Target, args) { record('eventsource', args[0]); throw new Error('Application EventSource blocked by acceptance gate'); } });
    navigator.sendBeacon = function(url) { record('beacon', url); return false; };
    if (navigator.serviceWorker) { navigator.serviceWorker.register = function(url) { record('serviceworker-register', url); return Promise.reject(new Error('Service worker registration prohibited by acceptance gate')); }; }
  })();` });

  network.phase = 'navigation';
  await client.send('Page.navigate', { url: appUrl });
  await waitFor(`document.querySelector('#page-title')?.textContent.includes('Put every shift')`, 'application heading');
  await waitFor(`document.readyState === 'complete'`, 'initial load');
  network.phase = 'runtime';

  const initial = await evaluate(`({ filled: document.querySelector('[data-testid="filled-total"] strong')?.textContent, uncovered: document.querySelector('[data-testid="uncovered-total"] strong')?.textContent, slots: document.querySelectorAll('[data-slot-id]').length, cards: [...document.querySelectorAll('[data-slot-id]')].map((slot) => slot.textContent), open: document.querySelector('[data-slot-id="m-n-safety"]')?.textContent })`);
  assert.deepEqual(initial.filled, '7');
  assert.deepEqual(initial.uncovered, '1');
  assert.equal(initial.slots, 8);
  assert.match(initial.open, /Morning|Safety inspection|OSHA-30|Uncovered/);

  const selectButton = '[data-focus-target="slot-m-n-safety"]';
  await focus(selectButton);
  await assertVisibleFocus(selectButton);
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="assignment-panel"]')`, 'assignment panel');
  assert.match(await evaluate(`document.querySelector('[data-testid="assignment-panel"]').textContent`), /Morning[\s\S]*North[\s\S]*OSHA-30/);

  const firstTechnician = '[data-technician-id="priya"]';
  assert.equal(await evaluate(`document.activeElement?.matches(${JSON.stringify(firstTechnician)})`), true, 'Opening the assignment panel must move focus to its first technician radio');
  await assertVisibleFocus(firstTechnician);
  await pressRaw('Escape', 'Escape', 27);
  await waitFor(`!document.querySelector('[data-testid="assignment-panel"]')`, 'Escape cancellation');
  await assertVisibleFocus(selectButton);
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="assignment-panel"]')`, 'immediate panel reopen');
  assert.equal(await evaluate(`document.activeElement?.matches(${JSON.stringify(firstTechnician)})`), true, 'Immediate reopen must focus the first technician without a delayed close override');
  await assertVisibleFocus(firstTechnician);

  await focus('[data-technician-id="theo"]');
  await assertVisibleFocus('[data-technician-id="theo"]');
  await pressSpace();
  assert.equal(await evaluate(`document.querySelector('[data-technician-id="theo"]').checked`), true);
  await focus('.commit-button');
  await assertVisibleFocus('.commit-button');
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="feedback"]')?.textContent.includes('missing OSHA-30')`, 'Theo rejection');
  const rejected = await evaluate(`({ message: document.querySelector('[data-testid="feedback"]').textContent, filled: document.querySelector('[data-testid="filled-total"] strong').textContent, uncovered: document.querySelector('[data-testid="uncovered-total"] strong').textContent, undoDisabled: document.querySelector('[data-focus-target="undo"]').disabled })`);
  assert.match(rejected.message, /already assigned to HVAC call in Central during Morning/);
  assert.deepEqual({ filled: rejected.filled, uncovered: rejected.uncovered, undoDisabled: rejected.undoDisabled }, { filled: '7', uncovered: '1', undoDisabled: true });
  await assertVisibleFocus('.commit-button');

  // Reset must recover directly from a rejected attempt, not only from a
  // successful or already-undone assignment.
  await focus('button.button.ghost');
  await assertVisibleFocus('button.button.ghost');
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="feedback"]') === null`, 'rejected-state reset feedback clearing');
  const resetAfterRejection = await evaluate(`({ filled: document.querySelector('[data-testid="filled-total"] strong').textContent, uncovered: document.querySelector('[data-testid="uncovered-total"] strong').textContent, cards: [...document.querySelectorAll('[data-slot-id]')].map((slot) => slot.textContent), panelClosed: !document.querySelector('[data-testid="assignment-panel"]'), undoDisabled: document.querySelector('[data-focus-target="undo"]').disabled })`);
  assert.deepEqual(resetAfterRejection, { filled: '7', uncovered: '1', cards: initial.cards, panelClosed: true, undoDisabled: true });
  await assertVisibleFocus(selectButton);
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="assignment-panel"]')`, 'assignment panel after rejected-state reset');

  await focus('[data-technician-id="amara"]');
  await assertVisibleFocus('[data-technician-id="amara"]');
  await pressSpace();
  await focus('.commit-button');
  await assertVisibleFocus('.commit-button');
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="filled-total"] strong')?.textContent === '8'`, 'valid Amara assignment');
  assert.match(await evaluate(`document.querySelector('[data-testid="feedback"]').textContent`), /Amara Cole[\s\S]*Safety inspection[\s\S]*North[\s\S]*Morning/);
  assert.match(await evaluate(`document.querySelector('[data-slot-id="m-n-safety"]').textContent`), /Filled[\s\S]*Amara Cole/);
  await assertVisibleFocus('[data-focus-target="undo"]');

  // Prove reset directly from the committed 8/0 state in the real app.
  await focus('button.button.ghost');
  await assertVisibleFocus('button.button.ghost');
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="uncovered-total"] strong')?.textContent === '1'`, 'committed-state reset totals');
  assert.deepEqual(await evaluate(`({ filled: document.querySelector('[data-testid="filled-total"] strong').textContent, uncovered: document.querySelector('[data-testid="uncovered-total"] strong').textContent, cards: [...document.querySelectorAll('[data-slot-id]')].map((slot) => slot.textContent), feedback: !!document.querySelector('[data-testid="feedback"]'), undoDisabled: document.querySelector('[data-focus-target="undo"]').disabled })`), { filled: '7', uncovered: '1', cards: initial.cards, feedback: false, undoDisabled: true });
  await assertVisibleFocus(selectButton);

  // Reapply one valid assignment so Undo is proven independently after the
  // committed-state reset path.
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="assignment-panel"]')`, 'assignment panel after committed-state reset');
  await assertVisibleFocus(firstTechnician);
  await focus('[data-technician-id="amara"]');
  await pressSpace();
  await focus('.commit-button');
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="filled-total"] strong')?.textContent === '8'`, 'reapplied Amara assignment');
  await assertVisibleFocus('[data-focus-target="undo"]');

  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="uncovered-total"] strong')?.textContent === '1'`, 'undo totals');
  await assertVisibleFocus(selectButton);
  assert.equal(await evaluate(`document.querySelector('[data-focus-target="undo"]').disabled`), true);

  await focus('button.button.ghost');
  await assertVisibleFocus('button.button.ghost');
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="feedback"]') === null`, 'reset feedback clearing');
  await assertVisibleFocus(selectButton);
  assert.deepEqual(await evaluate(`({ filled: document.querySelector('[data-testid="filled-total"] strong').textContent, uncovered: document.querySelector('[data-testid="uncovered-total"] strong').textContent })`), { filled: '7', uncovered: '1' });

  // A second consecutive Reset must be observably idempotent.
  await focus('button.button.ghost');
  await pressEnter();
  await waitFor(`document.querySelector('[data-focus-target="slot-m-n-safety"]') === document.activeElement`, 'repeated reset focus recovery');
  assert.deepEqual(await evaluate(`({ filled: document.querySelector('[data-testid="filled-total"] strong').textContent, uncovered: document.querySelector('[data-testid="uncovered-total"] strong').textContent, cards: [...document.querySelectorAll('[data-slot-id]')].map((slot) => slot.textContent), feedback: !!document.querySelector('[data-testid="feedback"]'), undoDisabled: document.querySelector('[data-focus-target="undo"]').disabled })`), { filled: '7', uncovered: '1', cards: initial.cards, feedback: false, undoDisabled: true });
  await assertVisibleFocus(selectButton);

  await client.send('Emulation.setDeviceMetricsOverride', { width: 320, height: 800, deviceScaleFactor: 1, mobile: false });
  await new Promise((resolve) => setTimeout(resolve, 150));
  await pressEnter();
  await waitFor(`document.querySelector('[data-testid="assignment-panel"]')`, 'narrow assignment panel');
  const narrow = await evaluate(`(() => { const controls = [...document.querySelectorAll('button:not([hidden]), input[type="radio"]')]; const cardText = [...document.querySelectorAll('[data-slot-id]')].map((slot) => slot.textContent); return { width: innerWidth, scrollWidth: document.documentElement.scrollWidth, slots: cardText.length, allCardsKeepContext: cardText.every((text) => /(Morning|Midday|Evening)/.test(text) && /(North|Central|South) zone/.test(text) && /Required/.test(text) && /(Filled|Uncovered)/.test(text)), technicianOptions: document.querySelectorAll('input[name="technician"]').length, selectedContext: document.querySelector('.selected-context')?.textContent, smallTarget: controls.map(el => el.matches('input') ? el.closest('label').getBoundingClientRect() : el.getBoundingClientRect()).filter(r => r.width > 0 && r.height > 0).some(r => r.height < 44) }; })()`);
  assert.equal(narrow.width, 320);
  assert.ok(narrow.scrollWidth <= 320, `Page clips horizontally at 320px (${narrow.scrollWidth}px)`);
  assert.equal(narrow.slots, 8);
  assert.equal(narrow.allCardsKeepContext, true, 'Every narrow role card must preserve shift, zone, requirement, and status text');
  assert.equal(narrow.technicianOptions, 6);
  assert.match(narrow.selectedContext, /Morning[\s\S]*North[\s\S]*OSHA-30/);
  assert.equal(narrow.smallTarget, false, 'Every interactive target must be at least 44px high');
  await focus('.text-button');
  await assertVisibleFocus('.text-button');
  await pressRaw('Escape', 'Escape', 27);
  await waitFor(`!document.querySelector('[data-testid="assignment-panel"]')`, 'narrow Escape cancellation');
  await assertVisibleFocus(selectButton);

  network.phase = 'reload';
  await client.send('Page.reload', { ignoreCache: true });
  await waitFor(`document.querySelector('[data-testid="filled-total"] strong')?.textContent === '7'`, 'reload baseline');
  network.phase = 'runtime';
  assert.deepEqual(await evaluate(`({ filled: document.querySelector('[data-testid="filled-total"] strong').textContent, uncovered: document.querySelector('[data-testid="uncovered-total"] strong').textContent, feedback: !!document.querySelector('[data-testid="feedback"]') })`), { filled: '7', uncovered: '1', feedback: false });
  const localOnly = await evaluate(`(async () => ({ attempts: window.__appNetworkAttempts, localStorage: localStorage.length, sessionStorage: sessionStorage.length, indexedDb: indexedDB.databases ? (await indexedDB.databases()).length : 0, caches: 'caches' in window ? (await caches.keys()).length : 0, serviceWorkerControlled: !!navigator.serviceWorker?.controller, registrations: navigator.serviceWorker ? (await navigator.serviceWorker.getRegistrations()).length : 0 }))()`);
  assert.deepEqual(localOnly, { attempts: [], localStorage: 0, sessionStorage: 0, indexedDb: 0, caches: 0, serviceWorkerControlled: false, registrations: 0 });
  const elapsedMs = Date.now() - startedAt;
  assert.ok(elapsedMs < 180_000, `Keyboard demo exceeded three minutes (${elapsedMs}ms)`);
  return { appUrl, elapsedMs, focusObservations };
}

async function main() {
  let profileDir;
  let downloadDir;
  let preview;
  let chrome;
  let client;
  let journeyEvidence;
  let overallTimedOut = false;
  const network = { phase: 'setup', permitted: [], prohibited: [], browserInternals: [] };
  const overallTimer = setTimeout(() => {
    overallTimedOut = true;
    if (client?.socket.readyState < WebSocket.CLOSING) client.socket.close();
    if (chrome?.child.exitCode === null) chrome.child.kill('SIGTERM');
    if (preview?.exitCode === null) preview.kill('SIGTERM');
  }, OVERALL_TIMEOUT_MS);

  try {
    profileDir = await mkdtemp(join(tmpdir(), 'coveragecanvas-profile-'));
    downloadDir = await mkdtemp(join(tmpdir(), 'coveragecanvas-downloads-'));
    await runCommand('npm', ['run', 'build'], 90_000);
    const port = await freePort();
    const appUrl = `http://127.0.0.1:${port}/`;
    preview = spawn('npm', ['exec', '--no', '--', 'vite', 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
    await waitForHttp(appUrl, 15_000);

    chrome = await launchChrome(profileDir, downloadDir);
    const browserBase = chrome.browserEndpoint.replace(/^ws:/, 'http:').replace(/\/devtools\/browser\/.*$/, '');
    const targets = await fetchJson(`${browserBase}/json/list`, 'Chrome target list');
    const pageTarget = targets.find((target) => target.type === 'page');
    assert.ok(pageTarget?.webSocketDebuggerUrl, 'Chrome page target unavailable');
    client = new CdpClient(pageTarget.webSocketDebuggerUrl);
    await client.connect();
    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await client.send('Network.enable');
    await client.send('ServiceWorker.enable');

    const allowedOrigin = new URL(appUrl).origin;
    client.on('Network.requestWillBeSent', ({ request, type, initiator }) => {
      const url = request.url;
      if (!/^https?:/.test(url)) return;
      const sameOrigin = new URL(url).origin === allowedOrigin;
      const runtimeType = ['Fetch', 'XHR', 'WebSocket', 'EventSource', 'Ping'].includes(type);
      // Resource type describes the response destination, not who initiated
      // the request. A script-created <script>, stylesheet, font, image, or
      // navigation is still application-driven HTTP and must fail the gate.
      const scriptDrivenHttp = initiator?.type === 'script';
      const record = { url, type, initiator: initiator?.type ?? 'unknown', phase: network.phase };
      if (!sameOrigin || runtimeType || scriptDrivenHttp) network.prohibited.push(record);
      else network.permitted.push(record);
    });
    client.on('Network.webSocketCreated', ({ url }) => network.prohibited.push({ url, type: 'WebSocket', initiator: 'script', phase: network.phase }));
    client.on('ServiceWorker.workerRegistrationUpdated', ({ registrations }) => {
      for (const registration of registrations ?? []) {
        const scope = registration.scopeURL ?? '';
        (/^https?:/.test(scope) ? network.prohibited : network.browserInternals).push({ url: scope, type: 'ServiceWorker scope', phase: network.phase });
      }
    });
    client.on('ServiceWorker.workerVersionUpdated', ({ versions }) => {
      for (const version of versions ?? []) {
        const script = version.scriptURL ?? '';
        (/^https?:/.test(script) ? network.prohibited : network.browserInternals).push({ url: script, type: 'ServiceWorker script', phase: network.phase });
      }
    });

    journeyEvidence = await deadline(journey(client, appUrl, network), 120_000, 'real-browser keyboard journey');
    assert.equal(network.prohibited.length, 0, `Prohibited runtime network detected: ${JSON.stringify(network.prohibited, null, 2)}`);
  } finally {
    clearTimeout(overallTimer);
    const cleanupErrors = [];
    const attemptCleanup = async (label, action) => {
      try { await action(); } catch (error) { cleanupErrors.push(new Error(`${label}: ${error.message}`, { cause: error })); }
    };
    await attemptCleanup('CDP cleanup', async () => client?.close());
    await attemptCleanup('Chrome cleanup', async () => stopChild(chrome?.child, 'Chrome'));
    await attemptCleanup('preview cleanup', async () => stopChild(preview, 'preview'));
    if (profileDir) await attemptCleanup('profile cleanup', async () => rm(profileDir, { recursive: true, force: true }));
    if (downloadDir) await attemptCleanup('download cleanup', async () => rm(downloadDir, { recursive: true, force: true }));
    if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Browser acceptance cleanup failed');
  }
  if (overallTimedOut) throw new Error(`Overall browser acceptance timed out after ${OVERALL_TIMEOUT_MS}ms`);

  const phases = (records) => records.reduce(
    (counts, record) => ({ ...counts, [record.phase]: (counts[record.phase] ?? 0) + 1 }),
    { navigation: 0, runtime: 0, reload: 0 },
  );
  const receipt = {
    ok: true,
    browser: CHROME,
    browserConfiguration: { executableOverride: 'BROWSER_EXECUTABLE', freshTemporaryProfile: true, extensionsDisabled: true },
    viewport: { desktop: 'browser default', narrow: { width: 320, height: 800, deviceScaleFactor: 1 } },
    productionBuild: { command: 'npm run build', server: 'vite preview', testedUrl: journeyEvidence.appUrl },
    elapsedDemoMs: journeyEvidence.elapsedMs,
    permittedPreview: { count: network.permitted.length, phases: phases(network.permitted), classifications: [...new Set(network.permitted.map(({ type, initiator }) => `${type}:${initiator}`))].sort() },
    prohibitedRuntime: { count: network.prohibited.length, phases: phases(network.prohibited), classifications: [...new Set(network.prohibited.map(({ type, initiator }) => `${type}:${initiator ?? 'unknown'}`))].sort() },
    browserInternalServiceWorkers: { count: network.browserInternals.length },
    focusObservations: journeyEvidence.focusObservations,
    artifacts: { receipt: 'tests/.browser-acceptance-success.json', productionBundle: 'dist/' },
    assertions: ['application-owned focus into first technician', 'keyboard cancellation and immediate reopen', 'two-cause rejection', 'reset directly from rejection', 'valid assignment', 'reset directly from committed state', 'latest-valid undo', 'reset after undo', 'repeated reset determinism', '320px layout', 'reload equivalence', 'focus visibility and contrast', 'storage and network isolation'],
  };
  const serializedReceipt = `${JSON.stringify(receipt, null, 2)}\n`;
  await mkdir(dirname(ARTIFACT_RECEIPT), { recursive: true });
  await writeFile(ARTIFACT_RECEIPT, serializedReceipt, 'utf8');
  await writeFile(RECEIPT, serializedReceipt, 'utf8');
  process.stdout.write('ZEROHANDOFF_BROWSER_ACCEPTANCE_OK\n');
}

await main().catch(async (error) => {
  await Promise.allSettled([
    rm(RECEIPT, { force: true }),
    rm(ARTIFACT_RECEIPT, { force: true }),
  ]);
  process.stderr.write(`### Error\n${error.stack ?? error}\n`);
  process.exitCode = 1;
});
