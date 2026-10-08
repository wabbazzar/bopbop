import { test, expect, type Page } from '@playwright/test';

test.beforeEach(async ({ page, request }) => {
  expect((await request.post('/api/fixture/reset')).ok()).toBe(true);
  await page.setViewportSize({ width: 393, height: 852 });
  // Emulate iOS changing only the visible area, including focus-driven panning.
  // Resizing the browser window alone cannot exercise this behavior.
  await page.addInitScript(() => {
    const visible = Object.assign(new EventTarget(), { height: innerHeight, width: innerWidth, offsetTop: 0, offsetLeft: 0, scale: 1 });
    Object.defineProperty(window, 'visualViewport', { value: visible, configurable: true });
    (window as any).setTestViewport = (values: object) => {
      Object.assign(visible, values);
      visible.dispatchEvent(new Event('resize'));
      visible.dispatchEvent(new Event('scroll'));
    };
  });
  await page.goto('/');
  await visibleArea(page, 852);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('Access token', { exact: true }).fill('fixture-access');
  await page.getByRole('button', { name: 'Save token' }).click();
  await expect(page.getByLabel('Harness', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
});

async function visibleArea(page: Page, height: number, top = 0, scale = 1) {
  await page.evaluate(values => (window as any).setTestViewport(values), { height, offsetTop: top, scale });
  await expect.poll(async () => Math.round((await page.locator('.viewport').boundingBox())!.height)).toBe(scale === 1 ? height : await page.evaluate(() => innerHeight));
}
async function composerFits(page: Page, bottom: number) {
  await expect.poll(async () => {
    const box = (await page.locator('.input-row').boundingBox())!;
    return bottom - box.y - box.height;
  }).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => {
    const box = (await page.locator('.input-row').boundingBox())!;
    return bottom - box.y - box.height;
  }).toBeLessThanOrEqual(8);
}
async function send(page: Page, message: string) {
  const count = await page.getByTestId('answer').count();
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(message);
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByTestId('answer')).toHaveCount(count + 1);
  await expect(page.locator('.working')).toHaveCount(0);
}

test('iPhone keyboard panning keeps the composer flush and zoom does not trigger typing mode', async ({ page }) => {
  const input = page.getByRole('textbox', { name: 'Message', exact: true });
  const draft = 'A multiline draft\n'.repeat(10);
  await input.fill(draft);
  await visibleArea(page, 400, 52);
  expect(await page.evaluate(() => innerHeight)).toBe(852);
  await expect(page.getByRole('button', { name: 'Hide keyboard' })).toBeVisible();
  await expect(page.locator('.stage')).toBeHidden();
  await expect(page.locator('.composer-note')).toBeHidden();
  await expect(page.locator('.starters')).toBeHidden();
  await composerFits(page, 452);
  expect((await input.boundingBox())!.height).toBeLessThanOrEqual(88);
  expect(await input.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
  await visibleArea(page, 340, 91);
  await composerFits(page, 431);
  await page.getByRole('button', { name: 'Hide keyboard' }).click();
  await expect(input).not.toBeFocused();
  await visibleArea(page, 852, 91); // restored height arrives before the pan resets
  expect((await page.locator('.viewport').boundingBox())!.y).toBe(0);
  await expect(page.locator('.stage')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hide keyboard' })).toHaveCount(0);
  await expect(input).toHaveValue(draft);
  expect((await input.boundingBox())!.height).toBe(128);
  await input.focus();
  await visibleArea(page, 426, 80, 2);
  await expect(page.locator('.stage')).toBeVisible();
  expect((await page.locator('.viewport').boundingBox())!.y).toBe(0);
});

test('keyboard transitions preserve the latest reply or scrollback and sending keeps focus', async ({ page }) => {
  for (let i = 0; i < 4; i++) await send(page, `project health ${i}\n${'A longer question\n'.repeat(8)}`);
  const input = page.getByRole('textbox', { name: 'Message', exact: true });
  const timeline = page.locator('.transcript');
  const bottomGap = () => timeline.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
  await input.focus();
  await visibleArea(page, 400, 50);
  await expect.poll(bottomGap).toBeLessThanOrEqual(2);
  await send(page, 'project health');
  await expect(input).toBeFocused();
  await expect.poll(bottomGap).toBeLessThanOrEqual(2);
  await timeline.evaluate(el => { el.scrollTop = 200; });
  await expect(page.getByRole('button', { name: 'Latest reply' })).toBeVisible();
  await visibleArea(page, 340, 90);
  await expect.poll(() => timeline.evaluate(el => el.scrollTop)).toBe(200);
  await visibleArea(page, 852);
  await expect.poll(() => timeline.evaluate(el => el.scrollTop)).toBe(200);
  await input.fill('A longer draft\n'.repeat(10));
  await page.setViewportSize({ width: 852, height: 393 });
  await visibleArea(page, 200);
  await composerFits(page, 200);
  expect((await input.boundingBox())!.height).toBeLessThanOrEqual(44);
});

test('settings follows keyboard panning and reveals the focused model or token field', async ({ page }) => {
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('Harness', { exact: true }).selectOption('codex');
  const model = page.getByLabel('Model', { exact: true });
  await model.focus();
  await visibleArea(page, 400, 76);
  const dialog = page.getByRole('dialog', { name: 'Settings' });
  const fieldFits = async (field: typeof model) => {
    await expect.poll(async () => {
      const box = (await field.boundingBox())!, body = (await page.locator('.sheet-body').boundingBox())!;
      return box.y >= body.y && box.y + box.height <= body.y + body.height;
    }).toBe(true);
  };
  await fieldFits(model);
  expect((await dialog.boundingBox())!.y).toBeGreaterThanOrEqual(76);
  const access = page.getByLabel('Access token', { exact: true });
  await access.evaluate(el => el.focus({ preventScroll: true }));
  await visibleArea(page, 340, 125);
  await fieldFits(access);
  const box = (await dialog.boundingBox())!;
  expect(box.y + box.height).toBeLessThanOrEqual(465);
  await page.getByRole('button', { name: 'Save token' }).click();
  await expect(page.getByLabel('Harness', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
  await visibleArea(page, 852);
  expect(await page.evaluate(() => scrollY)).toBe(0);
});

test('visual: iPhone conversation above an open keyboard', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' });
  await send(page, 'project health');
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Tell me more about activity.');
  await visibleArea(page, 400, 76);
  await expect(page.getByRole('button', { name: 'Hide keyboard' })).toBeVisible();
  await composerFits(page, 476);
  await expect(page.locator('.viewport')).toHaveScreenshot('keyboard-chat.png', {
    animations: 'disabled', mask: [page.locator('.trace summary')], maxDiffPixelRatio: .003
  });
});
