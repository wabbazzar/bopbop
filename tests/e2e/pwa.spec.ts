import { test, expect, type Page } from '@playwright/test';
import { EMOTES } from '../../src/lib/emote';
test.beforeEach(async ({ request }) => { expect((await request.post('/api/fixture/reset')).ok()).toBe(true); });
async function open(page: Page) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'What’s on your mind?' })).toBeVisible();
  await expect(page.locator('.connection')).toContainText('gpt-oss:20b');
}
async function token(page: Page, value = 'fixture-access') {
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel(/Access token/).fill(value);
  await page.getByRole('button', { name: 'Save token' }).click();
  await expect(page.getByLabel('Harness', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
}
async function send(page: Page, message: string) {
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(message);
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.locator('.working')).toHaveCount(0);
}

test('hydrates, sends through the real API, renders Markdown and telemetry, resumes after reload', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await open(page); await token(page);
  await send(page, 'project health');
  await expect(page.getByTestId('answer')).toContainText('Public demo');
  await expect(page.getByTestId('answer').locator('table')).toBeVisible();
  await expect(page.getByTestId('answer')).not.toContainText('<emote>');
  await page.getByText('Agent trace', { exact: true }).click();
  await expect(page.getByTestId('trace')).toContainText('ice_query');
  await expect(page.getByTestId('trace')).toContainText('partial');
  await expect(page.getByTestId('trace')).toContainText('1,240 in / 81 out');
  await expect(page.getByTestId('trace')).toContainText('First visible text');
  await expect(page.getByTestId('trace')).toContainText('Thinking characters');
  await expect(page.getByTestId('trace')).not.toContainText('$0.0000');
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('saved draft');
  await page.reload();
  await expect(page.getByTestId('answer')).toContainText('Public demo');
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('saved draft');
  await send(page, 'resume check');
  await expect(page.getByTestId('answer').last()).toHaveText('Session resumed.');
  const saved = await page.evaluate(() => localStorage.getItem('bopbop_history_v1'));
  expect(saved).not.toContain('fixture-access'); expect(saved).not.toContain('fixture-agent-session');
  await page.getByRole('button', { name: 'New chat' }).click();
  await expect(page.getByTestId('answer')).toHaveCount(0);
  await send(page, 'resume check');
  await expect(page.getByTestId('answer')).toHaveText('Fresh session.');
  expect(errors).toEqual([]);
});

test('token gate opens settings and wrong credentials produce a recoverable error', async ({ page }) => {
  await open(page);
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('hello');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByLabel(/Access token/)).toBeVisible();
  await page.getByLabel('Access token', { exact: true }).fill('incorrect');
  await page.getByRole('button', { name: 'Save token' }).click();
  await expect(page.getByRole('alert')).toContainText('access token');
  await page.getByRole('button', { name: 'Close settings' }).click();
  await send(page, 'hello');
  await expect(page.getByRole('alert')).toContainText('Access token');
  await token(page); await send(page, 'resume check');
  await expect(page.getByTestId('answer')).toHaveText('Fresh session.');
});

test('tool failure and an unfinished stream are visible; input remains usable', async ({ page }) => {
  await open(page); await token(page);
  await send(page, 'tool failure');
  await expect(page.getByRole('alert')).toContainText('dashboard is unavailable');
  await page.getByText('Agent trace', { exact: true }).click();
  await expect(page.getByTestId('trace')).toContainText('failed');
  await send(page, 'interrupt stream');
  await expect(page.getByRole('alert').last()).toContainText('before the reply finished');
  await expect(page.getByTestId('answer')).toHaveText('Partial reply');
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('continue');
  await expect(page.getByRole('button', { name: 'Send message' })).toBeEnabled();
});

test('quiet work shows server heartbeats without claiming visible model text', async ({ page }) => {
  await open(page); await token(page); await send(page, 'quiet work');
  await expect(page.getByTestId('answer')).toHaveText('Quiet work finished.');
  await page.getByText('Agent trace', { exact: true }).click();
  await expect(page.getByTestId('trace')).toContainText('Last server heartbeat');
  await expect(page.getByTestId('trace')).toContainText('10.00s');
  await page.reload();
  await expect(page.getByTestId('trace')).toContainText('Last server heartbeat');
});

test('model Markdown cannot execute scripts, load images, or link javascript', async ({ page }) => {
  await open(page); await token(page); await send(page, 'unsafe markdown');
  const answer = page.getByTestId('answer');
  await expect(answer.locator('strong')).toHaveText('Safe');
  await expect(answer.locator('script,svg,img,iframe')).toHaveCount(0);
  await expect(answer.locator('a[href^="javascript:"]')).toHaveCount(0);
  await expect(answer.locator('a[href="https://example.com"]')).toHaveText('good');
  expect(await page.evaluate(() => (window as any).pwned)).toBeUndefined();
});

test('layout fits narrow and keyboard-sized viewports; reduced motion stops character animation', async ({ page }) => {
  await open(page); await token(page); await send(page, 'project health');
  for (const size of [{ width: 320, height: 568 }, { width: 393, height: 360 }, { width: 852, height: 393 }]) {
    await page.setViewportSize(size);
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible();
    // visualViewport.resize is asynchronous in Chromium, like a real keyboard
    // transition. Check the settled layout rather than the pre-event frame.
    await expect.poll(async () => {
      const box = await page.getByRole('button', { name: 'Send message' }).boundingBox();
      return box!.y + box!.height;
    }).toBeLessThanOrEqual(size.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.locator('summary').filter({ hasText: 'Agent trace' }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await page.locator('.stage-blob').evaluate(el => getComputedStyle(el).animationName)).toBe('none');
});

test('visual: original character, empty state and a populated trace in light and dark', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' });
  await open(page);
  await expect(page).toHaveScreenshot('empty-light.png', { animations: 'disabled', maxDiffPixelRatio: .003 });
  await token(page); await send(page, 'project health');
  await page.getByText('Agent trace', { exact: true }).click();
  // Live wall-clock values are deliberately masked; content/layout remain checked.
  await expect(page).toHaveScreenshot('chat-light.png', { animations: 'disabled', mask: [page.locator('.stage-caption'), page.locator('.totals'), page.locator('.trace summary')], maxDiffPixelRatio: .003 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page).toHaveScreenshot('chat-dark.png', { animations: 'disabled', mask: [page.locator('.stage-caption'), page.locator('.totals'), page.locator('.trace summary')], maxDiffPixelRatio: .003 });
});

// SW must run normally; Playwright routing can bypass interception, so this
// scenario uses the fixture server directly and checks the actual Cache API.
test('installable shell reloads offline, retains history, and never caches API data', async ({ page, context }) => {
  await open(page); await token(page); await send(page, 'project health');
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  const manifest = await page.evaluate(async () => (await fetch('/manifest.webmanifest')).json());
  expect(manifest.name).toBe('BopBop'); expect(manifest.display).toBe('standalone');
  for (const icon of manifest.icons) expect((await page.request.get(icon.src)).ok()).toBe(true);
  const keys = await page.evaluate(async () => {
    const urls: string[] = [];
    for (const name of await caches.keys()) {
      if (name.startsWith('bopbop-shell-')) for (const req of await (await caches.open(name)).keys()) urls.push(new URL(req.url).pathname);
    }
    return urls;
  });
  expect(keys).toContain('/index.html'); expect(keys.some(k => k.startsWith('/api/'))).toBe(false);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByTestId('answer')).toContainText('Public demo');
  await expect(page.getByRole('status').filter({ hasText: 'You’re offline' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('offline draft');
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled();
  await context.setOffline(false);
  await expect(page.locator('.connection')).toContainText('gpt-oss:20b');
  await send(page, 'resume check');
  await expect(page.getByTestId('answer').last()).toHaveText('Session resumed.');
});

test('server settings select an installed model and pack, survive reload, and reach the next turn', async ({ page }) => {
  await open(page); await token(page);
  await send(page, 'resume check');
  await expect(page.getByTestId('answer')).toHaveText('Fresh session.');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Model', { exact: true })).toBeVisible();
  await page.getByLabel('Model', { exact: true }).selectOption('fixture-small:latest');
  await page.getByLabel('Context pack', { exact: true }).selectOption('ice');
  await page.getByLabel('Context size', { exact: true }).selectOption('8192');
  await page.getByRole('button', { name: 'Save agent settings' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved. New turns' })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
  await expect(page.locator('.connection')).toContainText('fixture-small:latest');
  await page.reload();
  await expect(page.locator('.connection')).toContainText('fixture-small:latest');
  await send(page, 'resume check');
  await expect(page.getByTestId('answer').last()).toHaveText('Fresh session.');
  await send(page, 'configuration check');
  await expect(page.getByTestId('answer').last()).toHaveText('ollama / fixture-small:latest / ice / 8192');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('Harness', { exact: true }).selectOption('codex');
  await page.getByLabel('Model', { exact: true }).fill('fixture-codex');
  await page.getByRole('button', { name: 'Save agent settings' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved. New turns' })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
  await send(page, 'configuration check');
  await expect(page.getByTestId('answer').last()).toHaveText('codex / fixture-codex / ice / 8192');
  await page.getByText('Agent trace', { exact: true }).last().click();
  await expect(page.getByTestId('trace').last()).toContainText('fixture-codex');
});

test('changing settings while a reply runs leaves that reply on its original model', async ({ page }) => {
  await open(page); await token(page);
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('slow reply configuration check');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.locator('.working')).toBeVisible();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('Model', { exact: true }).selectOption('fixture-small:latest');
  await page.getByRole('button', { name: 'Save agent settings' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved. New turns' })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
  await expect(page.getByTestId('answer')).toHaveText('ollama / gpt-oss:20b / general / 16384');
  await send(page, 'configuration check');
  await expect(page.getByTestId('answer').last()).toHaveText('ollama / fixture-small:latest / general / 16384');
});

test('iPhone SE, 13, 15 and Pro Max dimensions keep composer, touch targets, and sheet usable', async ({ page }) => {
  await open(page); await token(page);
  for (const viewport of [{ width: 375, height: 667 }, { width: 390, height: 844 }, { width: 393, height: 852 }, { width: 430, height: 932 }, { width: 393, height: 340 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(async () => {
      const box = await page.getByRole('button', { name: 'Send message' }).boundingBox();
      return box!.y + box!.height;
    }).toBeLessThanOrEqual(viewport.height);
    for (const name of ['Settings', 'Send message']) {
      const box = await page.getByRole('button', { name, exact: true }).boundingBox();
      expect(box!.width).toBeGreaterThanOrEqual(44); expect(box!.height).toBeGreaterThanOrEqual(44);
    }
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page.getByLabel('Harness', { exact: true })).toBeVisible();
    const modal = page.getByRole('dialog', { name: 'Settings' });
    await expect.poll(async () => (await modal.boundingBox())!.width).toBeLessThanOrEqual(viewport.width);
    await page.getByLabel('Access token', { exact: true }).scrollIntoViewIfNeeded();
    await page.getByRole('button', { name: 'Save token' }).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button', { name: 'Save token' })).toBeInViewport();
    await page.getByRole('button', { name: 'Save token' }).click();
    await expect(page.getByLabel('Harness', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Close settings' }).click();
  }
  await page.setViewportSize({ width: 393, height: 852 });
  await page.getByRole('button', { name: /What can you do/ }).click();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('What can you help me with?');
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeFocused();
});

test('settings cancel discards edits and a stale server edit is reported', async ({ page, request }) => {
  await open(page); await token(page);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('Model', { exact: true }).selectOption('fixture-small:latest');
  await page.getByRole('button', { name: 'Close settings' }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('gpt-oss:20b');
  const current = (await request.get('/api/settings', { headers: { Authorization: 'Bearer fixture-access' } })).json();
  const data = await current;
  await request.put('/api/settings', { headers: { Authorization: 'Bearer fixture-access', 'X-BopBop-Settings': '1' }, data: { harness: data.harness, model: 'fixture-small:latest', profile: data.profile, ollama_context: data.ollama_context, revision: data.revision } });
  await page.getByRole('button', { name: 'Save agent settings' }).click();
  await expect(page.getByRole('alert')).toContainText('changed elsewhere');
  await page.getByRole('button', { name: 'Reload settings' }).click();
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('fixture-small:latest');
});

test('visual: server settings sheet at iPhone dimensions', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' });
  await open(page); await token(page);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Harness', { exact: true })).toBeVisible();
  await expect(page).toHaveScreenshot('settings-light.png', { animations: 'disabled', maxDiffPixelRatio: .003 });
});

test('visual: all expressions use the CLI character body and load their face overlays', async ({ page }) => {
  const missing: string[] = [];
  const poses: { name: string; svg: string }[] = [];
  page.on('response', response => { if (response.url().includes('/character/') && !response.ok()) missing.push(response.url()); });
  await open(page); await token(page);
  for (const emote of EMOTES) {
    await send(page, `expression ${emote}`);
    const portrait = page.getByRole('img', { name: `BopBop ${emote}`, exact: true });
    await expect(portrait).toBeVisible();
    await expect(portrait.locator('image').first()).toHaveAttribute('href', '/character/body.jpg');
    if (emote !== 'happy' && emote !== 'idling') {
      await expect(portrait.locator('image').last()).toHaveAttribute('href', `/character/expressions/${emote}.png`);
    }
    poses.push({ name: emote, svg: await portrait.evaluate(el => el.outerHTML) });
  }
  expect(missing).toEqual([]);
  // Review the actual rendered character markup at a readable size. Unique
  // mask IDs prevent one expression's masks from affecting another portrait.
  const review = await page.context().newPage();
  await review.setViewportSize({ width: 960, height: 1020 });
  await review.setContent(`<base href="${page.url()}"><style>
    * { box-sizing: border-box; } body { margin: 0; color: #181c2a; background: #edf3fc; font: 16px system-ui; }
    main { display: grid; grid-template-columns: repeat(5,1fr); gap: 20px; padding: 25px; }
    section { text-align: center; padding: 10px; background: #ffffff80; border-radius: 16px; }
    svg { height: 145px; width: 155px; overflow: visible; }
  </style><main>${poses.map(({ name, svg }, i) => `<section>${svg.replaceAll('pearl-', `pearl-${i}-`)}<p>${name}</p></section>`).join('')}</main>`);
  await expect(review.locator('main')).toHaveScreenshot('expressions.png', { maxDiffPixelRatio: .003 });
  await review.close();
});
