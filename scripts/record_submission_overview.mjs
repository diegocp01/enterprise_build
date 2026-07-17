#!/usr/bin/env node

import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

function args(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    result[argv[index].replace(/^--/, '')] = argv[index + 1];
  }
  return result;
}

const options = args(process.argv.slice(2));
for (const required of ['url', 'output', 'node-modules']) {
  if (!options[required]) throw new Error(`Missing --${required}`);
}

const require = createRequire(path.join(options['node-modules'], 'package.json'));
const { chromium } = require('playwright');
const output = path.resolve(options.output);
const videoDir = path.join(path.dirname(output), '.overview-video');
await fs.mkdir(videoDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: options.chrome || undefined,
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  deviceScaleFactor: 1,
  recordVideo: { dir: videoDir, size: { width: 1280, height: 720 } },
});
const page = await context.newPage();
const video = page.video();
await page.goto(options.url, { waitUntil: 'load' });

await page.addStyleTag({
  content: `
    #submission-title {
      position: fixed; inset: 0; z-index: 2147483647; display: grid; place-items: center;
      color: #fffdf7; background: rgba(16, 24, 32, .96);
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
      transition: opacity .45s ease;
    }
    #submission-title > div { text-align: center; }
    #submission-title small { display: block; color: #65d9bd; font-size: 15px; font-weight: 900; letter-spacing: .18em; }
    #submission-title strong { display: block; margin-top: 15px; font-size: 62px; letter-spacing: -.055em; }
    #submission-title span { display: block; margin-top: 12px; color: #d7dee7; font-size: 20px; }
  `,
});
await page.evaluate(() => {
  const title = document.createElement('div');
  title.id = 'submission-title';
  title.innerHTML = '<div><small>OPENAI BUILD WEEK · DEVELOPER TOOLS</small><strong>ZeroHandoff</strong><span>GPT-5.6 Sol × Codex · autonomous software delivery</span></div>';
  document.body.append(title);
});
await page.waitForTimeout(3500);
await page.evaluate(() => { document.querySelector('#submission-title').style.opacity = '0'; });
await page.waitForTimeout(700);
await page.evaluate(() => document.querySelector('#submission-title')?.remove());
await page.waitForTimeout(5200);
await page.locator('.scroll-cue').click();
await page.waitForTimeout(9000);

await page.close();
await video.saveAs(output);
await context.close();
await browser.close();
console.log(`Recorded ${output}`);
