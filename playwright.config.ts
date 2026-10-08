import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';
const python = process.env.BOPBOP_TEST_PYTHON || (existsSync('server/.venv/bin/python') ? 'server/.venv/bin/python' : 'python3');
export default defineConfig({
  testDir: './tests/e2e', fullyParallel: false, forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0, workers: 1,
  reporter: [['list']], timeout: 20000,
  use: { baseURL: 'http://127.0.0.1:8191', trace: 'retain-on-failure' },
  webServer: { command: `${python} tests/fixtures/pwa_server.py`, url: 'http://127.0.0.1:8191/api/client', reuseExistingServer: false, timeout: 15000 },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1100, height: 850 } } },
    { name: 'mobile-chromium', use: { ...devices['iPhone 15'], defaultBrowserType: 'chromium', viewport: { width: 393, height: 852 } } },
    { name: 'mobile-webkit', use: { ...devices['iPhone 15'] } }
  ]
});
