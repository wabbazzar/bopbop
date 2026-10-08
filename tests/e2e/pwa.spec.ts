import { test, expect, type Page } from '@playwright/test';
async function open(page: Page) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'What’s on your mind?' })).toBeVisible();
  await expect(page.locator('.connection')).toContainText('gpt-oss:20b');
}
async function token(page: Page, value = 'fixture-access') {
  await page.getByLabel('Settings', { exact: true }).click();
  await page.getByLabel(/Access token/).fill(value);
  await page.getByRole('button', { name: 'Save token' }).click();
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
  await page.getByLabel(/Access token/).fill('incorrect');
  await page.getByRole('button', { name: 'Save token' }).click();
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
