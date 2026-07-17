#!/usr/bin/env node

import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]?.replace(/^--/, '');
    const value = argv[index + 1];
    if (!key || value === undefined) throw new Error(`Invalid argument near ${argv[index]}`);
    args[key] = value;
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
for (const required of ['url', 'output', 'screenshot', 'plan', 'report', 'node-modules']) {
  if (!args[required]) throw new Error(`Missing --${required}`);
}

const require = createRequire(path.join(args['node-modules'], 'package.json'));
const { chromium } = require('playwright');
const viewport = { width: 1280, height: 720 };
const videoDir = path.join(path.dirname(args.output), '.browser-video');
await fs.mkdir(videoDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: args.chrome || undefined,
});
const context = await browser.newContext({
  viewport,
  deviceScaleFactor: 1,
  recordVideo: { dir: videoDir, size: viewport },
});
const page = await context.newPage();
const video = page.video();
const report = {
  schema_version: '1.0',
  url: args.url,
  started_at: new Date().toISOString(),
  completed: false,
  plan_steps: 0,
  planned_actions: 0,
  mutating_actions_planned: 0,
  mutating_actions_completed: 0,
  unique_state_count: 0,
  actions: [],
  state_digests: [],
  error: null,
};
const mutatingTypes = new Set(['click', 'select', 'fill']);
const pacingScale = Number(args['pacing-scale'] || '1');
if (!Number.isFinite(pacingScale) || pacingScale < 0.45 || pacingScale > 1.8) {
  throw new Error(`Invalid --pacing-scale ${args['pacing-scale'] || ''}`);
}
report.pacing_scale = pacingScale;
report.showcased_waits = 0;
let flowStartedAt = 0;

async function pause(milliseconds) {
  await page.waitForTimeout(milliseconds);
}

function paced(milliseconds, minimum = 20) {
  return Math.max(minimum, Math.round(milliseconds * pacingScale));
}

async function installDemoLayer() {
  await page.addStyleTag({
    content: `
      html { scroll-behavior: smooth !important; }
      #zh-demo-caption {
        position: fixed; left: 24px; bottom: 22px; z-index: 2147483645;
        width: min(370px, calc(100vw - 48px)); padding: 11px 14px 12px;
        color: #f8fafc; background: rgba(10, 20, 30, .94);
        border: 1px solid rgba(255,255,255,.16); border-radius: 14px;
        box-shadow: 0 18px 55px rgba(0,0,0,.28);
        font-family: Inter, ui-sans-serif, system-ui, sans-serif;
        opacity: 0; transform: translateY(14px); transition: .32s ease;
        pointer-events: none;
      }
      #zh-demo-caption.zh-visible { opacity: 1; transform: translateY(0); }
      #zh-demo-caption small {
        display: block; margin-bottom: 4px; color: #64e2c4;
        font-size: 9px; font-weight: 850; letter-spacing: .14em; text-transform: uppercase;
      }
      #zh-demo-caption strong { display: block; font-size: 17px; line-height: 1.15; }
      #zh-demo-caption span { display: block; margin-top: 4px; color: #cbd5e1; font-size: 11px; line-height: 1.3; }
      #zh-demo-cursor {
        position: fixed; left: 0; top: 0; z-index: 2147483647; width: 20px; height: 20px;
        margin: -10px 0 0 -10px; border: 3px solid #ff725e; border-radius: 50%;
        background: rgba(255,255,255,.94); box-shadow: 0 2px 12px rgba(0,0,0,.35);
        pointer-events: none; transform: translate(-40px,-40px); transition: width .12s, height .12s, margin .12s;
      }
      #zh-demo-cursor.zh-click { width: 34px; height: 34px; margin: -17px 0 0 -17px; background: rgba(255,114,94,.28); }
      .zh-demo-target { outline: 4px solid rgba(255,114,94,.72) !important; outline-offset: 5px !important; }
    `,
  });
  await page.evaluate(() => {
    const caption = document.createElement('div');
    caption.id = 'zh-demo-caption';
    caption.innerHTML = '<small></small><strong></strong><span></span>';
    document.body.append(caption);
    const cursor = document.createElement('div');
    cursor.id = 'zh-demo-cursor';
    document.body.append(cursor);
    window.addEventListener('mousemove', (event) => {
      cursor.style.transform = `translate(${event.clientX}px, ${event.clientY}px)`;
    }, true);
    window.addEventListener('mousedown', () => cursor.classList.add('zh-click'), true);
    window.addEventListener('mouseup', () => {
      setTimeout(() => cursor.classList.remove('zh-click'), 220);
    }, true);
  });
}

async function caption(kicker, title, detail, milliseconds = 1800) {
  await page.evaluate(({ kicker, title, detail }) => {
    const root = document.querySelector('#zh-demo-caption');
    root.querySelector('small').textContent = kicker;
    root.querySelector('strong').textContent = title;
    root.querySelector('span').textContent = detail;
    root.classList.add('zh-visible');
  }, { kicker, title, detail });
  await pause(paced(milliseconds));
}

async function hideCaption() {
  await page.evaluate(() => document.querySelector('#zh-demo-caption')?.classList.remove('zh-visible'));
  await pause(paced(180));
}

async function focusTarget(locator) {
  await locator.scrollIntoViewIfNeeded();
  await pause(paced(300));
  const box = await locator.boundingBox();
  if (!box) return;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 18 });
  await locator.evaluate((element) => element.classList.add('zh-demo-target'));
  await pause(paced(220));
}

async function releaseTarget() {
  await page.locator('.zh-demo-target').evaluateAll((elements) => {
    for (const element of elements) element.classList.remove('zh-demo-target');
  }).catch(() => {});
}

function escapePattern(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function flexibleDecimalTextPattern(value) {
  let pattern = '';
  let cursor = 0;
  for (const match of value.matchAll(/-?\d+\.\d+/g)) {
    pattern += escapePattern(value.slice(cursor, match.index));
    const token = match[0];
    const dot = token.indexOf('.');
    const whole = token.slice(0, dot);
    const fraction = token.slice(dot + 1);
    const significant = fraction.replace(/0+$/, '');
    pattern += significant.length === fraction.length
      ? escapePattern(token)
      : `${escapePattern(whole)}\\.${escapePattern(significant)}0+`;
    cursor = match.index + token.length;
  }
  pattern += escapePattern(value.slice(cursor));
  return new RegExp(pattern, 'i');
}

function plannedLocator(action) {
  if (action.selector) return page.locator(action.selector).first();
  if (action.role) {
    return page.getByRole(action.role, {
      name: action.name ? new RegExp(escapePattern(action.name), 'i') : undefined,
    }).first();
  }
  return page.getByLabel(action.name, { exact: false }).first();
}

async function plannedActionLocator(action) {
  if (!action.selector && action.role) {
    const openDialog = page.locator('dialog[open]').last();
    if (await openDialog.count()) {
      const dialogTarget = openDialog.getByRole(action.role, {
        name: action.name ? new RegExp(escapePattern(action.name), 'i') : undefined,
      });
      if (await dialogTarget.count()) return dialogTarget.first();
    }
  }
  return plannedLocator(action);
}

async function stateSnapshot() {
  const state = await page.evaluate(() => ({
    title: document.title,
    url: window.location.href,
    text: (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 12_000),
  }));
  return {
    digest: createHash('sha256').update(JSON.stringify(state)).digest('hex'),
    title: state.title,
    url: state.url,
    text_excerpt: state.text.slice(0, 500),
  };
}

async function rememberState(label) {
  const snapshot = await stateSnapshot();
  report.state_digests.push({ label, ...snapshot });
  report.unique_state_count = new Set(report.state_digests.map((item) => item.digest)).size;
  return snapshot;
}

async function observedText(locator) {
  if (!(await locator.count())) return '';
  return String(await locator.innerText().catch(() => '')).replace(/\s+/g, ' ').trim().slice(0, 500);
}

async function runPlannedFlow(plan) {
  const title = (await page.title()) || 'Generated application';
  flowStartedAt = Date.now();
  await caption('End-to-end product demo', title, 'A real browser session performs the verified primary workflow.', 1800);
  await hideCaption();
  for (const step of plan) {
    const actions = step.actions || step.browser_actions || [];
    const firstInteractive = actions.find((action) => mutatingTypes.has(action.type) || action.type === 'scroll');
    const showcasedWaitIndex = actions.findIndex((action) => action.type === 'wait');
    const stepTitle = String(step.title || firstInteractive?.name || 'Verify the visible outcome');
    await caption(
      `Step ${step.step ?? ''}`,
      stepTitle,
      String(step.expected || 'Perform the next verified application action.').slice(0, 190),
      1100,
    );
    await hideCaption();
    for (let actionIndex = 0; actionIndex < actions.length; actionIndex += 1) {
      const action = actions[actionIndex];
      const actionReport = {
        step: step.step ?? null,
        index: actionIndex + 1,
        type: action.type,
        role: action.role || '',
        name: action.name || '',
        value: action.value || '',
        status: 'running',
        started_ms: Date.now() - flowStartedAt,
      };
      report.actions.push(actionReport);
      if (action.type === 'wait') {
        const showcase = actionIndex === showcasedWaitIndex;
        actionReport.showcased = showcase;
        const numericDelay = Number(action.value);
        if (String(action.value ?? '').trim() && !Number.isFinite(numericDelay)) {
          throw new Error('Demo wait value must be numeric milliseconds; observable text belongs in name or selector');
        }
        if (action.role || action.name || action.selector) {
          let locator = plannedLocator(action);
          if (!(await locator.count()) && action.name) {
            // Observation steps may describe a landmark using a stricter ARIA
            // role than the generated markup exposes. Falling back to visible
            // text is safe for waits; mutating actions remain role-strict.
            locator = page.getByText(action.name, { exact: false }).first();
          }
          if (!(await locator.count()) && action.name) {
            // Compact metric cards often split their visible phrase across
            // nested label/value/suffix elements. A stable accessible name is
            // valid observation evidence and avoids whitespace-coupled demos.
            locator = page.getByLabel(action.name, { exact: false }).first();
            actionReport.match_mode = 'accessible-name-fallback';
          }
          if (!(await locator.count()) && action.name) {
            const withoutTrailingPunctuation = action.name.replace(/[.!?]+$/, '');
            if (withoutTrailingPunctuation !== action.name) {
              locator = page.getByText(withoutTrailingPunctuation, { exact: false }).first();
              actionReport.match_mode = 'trailing-punctuation-relaxed';
            }
          }
          if (!(await locator.count()) && action.name && /-?\d+\.\d+/.test(action.name)) {
            // Outcome artifacts often use the displayed two-decimal value while
            // the UI exposes exact four-decimal arithmetic before that value.
            // Decimal-flexible observation matching preserves the actual numbers
            // and surrounding formula while tolerating trailing precision zeros.
            locator = page.getByText(flexibleDecimalTextPattern(action.name)).first();
            actionReport.match_mode = actionReport.match_mode
              ? `${actionReport.match_mode}+flexible-decimal-text`
              : 'flexible-decimal-text';
          }
          if (!(await locator.count())) {
            throw new Error(`Demo observation not found: ${action.role || ''} ${action.name || action.selector || ''}`);
          }
          await locator.waitFor({ state: 'visible', timeout: 10_000 });
          if (showcase) {
            await focusTarget(locator);
            report.showcased_waits += 1;
          }
          actionReport.observed_text = await observedText(locator);
          if (showcase) await releaseTarget();
          await pause(
            showcase
              ? paced(Math.min(Number.isFinite(numericDelay) ? numericDelay : 350, 350))
              : paced(40),
          );
        } else {
          await pause(
            showcase
              ? paced(Math.min(Number.isFinite(numericDelay) ? numericDelay : 350, 350))
              : paced(40),
          );
        }
        actionReport.status = 'completed';
        actionReport.completed_ms = Date.now() - flowStartedAt;
        actionReport.state = await rememberState(`step-${step.step ?? ''}-wait-${actionIndex + 1}`);
        continue;
      }
      const locator = await plannedActionLocator(action);
      if (!(await locator.count())) throw new Error(`Demo target not found: ${action.role || ''} ${action.name || action.selector || ''}`);
      await focusTarget(locator);
      let verifiedDownload = null;
      if (action.type === 'select') await locator.selectOption({ label: action.value });
      else if (action.type === 'fill') await locator.fill(action.value || '');
      else if (action.type === 'click' && /download|export/i.test(`${action.name || ''} ${step.expected || ''}`)) {
        const downloadPromise = page.waitForEvent('download', { timeout: 5_000 }).catch(() => null);
        await locator.click();
        verifiedDownload = await downloadPromise;
      }
      else if (action.type === 'click') await locator.click();
      else if (action.type === 'scroll') await locator.scrollIntoViewIfNeeded();
      else throw new Error(`Unsupported demo action: ${action.type}`);
      await releaseTarget();
      if (verifiedDownload) {
        const filename = verifiedDownload.suggestedFilename();
        actionReport.download = { suggested_filename: filename };
        await caption('Verified local export', filename, 'The browser accepted the generated JSON snapshot.', 700);
        await hideCaption();
      }
      await pause(paced(850));
      actionReport.status = 'completed';
      actionReport.completed_ms = Date.now() - flowStartedAt;
      actionReport.observed_text = await observedText(locator);
      actionReport.state = await rememberState(`step-${step.step ?? ''}-${action.type}-${actionIndex + 1}`);
      if (mutatingTypes.has(action.type)) report.mutating_actions_completed += 1;
    }
  }
  await caption('Verified output', 'The primary workflow completed', 'Every visible action ran against the delivered application in a real browser.', 1800);
}

try {
  await Promise.all([
    fs.rm(args.output, { force: true }),
    fs.rm(args.screenshot, { force: true }),
    fs.rm(args.report, { force: true }),
  ]);
  await page.goto(args.url, { waitUntil: 'networkidle', timeout: 30_000 });
  await installDemoLayer();
  await page.mouse.move(86, 88, { steps: 8 });

  const payload = JSON.parse(await fs.readFile(args.plan, 'utf8'));
  const plan = Array.isArray(payload) ? payload : (payload.steps || []);
  const allActions = plan.flatMap((step) => step.actions || step.browser_actions || []);
  report.plan_steps = plan.length;
  report.planned_actions = allActions.length;
  report.mutating_actions_planned = allActions.filter((action) => mutatingTypes.has(action.type)).length;
  if (!plan.length || !allActions.length) throw new Error('Demo plan is empty');
  if (report.mutating_actions_planned < 2) throw new Error('Demo plan needs at least two mutating browser actions');
  await rememberState('initial');
  await runPlannedFlow(plan);
  await rememberState('final');
  if (report.mutating_actions_completed !== report.mutating_actions_planned) {
    throw new Error('Not every planned mutating browser action completed');
  }
  if (report.unique_state_count < 2) throw new Error('Browser actions did not produce a visible state change');

  await hideCaption();
  await pause(paced(300));
  await page.screenshot({ path: args.screenshot, type: 'png' });
  await context.close();
  await video.saveAs(args.output);
  await browser.close();
  report.completed = true;
  report.completed_at = new Date().toISOString();
  await fs.writeFile(args.report, `${JSON.stringify(report, null, 2)}\n`);
} catch (error) {
  report.error = error instanceof Error ? error.message : String(error);
  report.completed_at = new Date().toISOString();
  const running = report.actions.findLast((item) => item.status === 'running');
  if (running) running.status = 'failed';
  await fs.writeFile(args.report, `${JSON.stringify(report, null, 2)}\n`).catch(() => {});
  await context.close().catch(() => {});
  await browser.close().catch(() => {});
  throw error;
}
