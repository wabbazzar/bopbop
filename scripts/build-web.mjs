import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
// SvelteKit loads config in multiple build processes. All must agree on the
// version, which also namespaces the client bootstrap and offline cache.
const result = spawnSync(process.execPath, ['node_modules/vite/bin/vite.js', 'build', ...process.argv.slice(2)], {
  cwd: root, stdio: 'inherit', env: { ...process.env, BOPBOP_WEB_VERSION: Date.now().toString() }
});
if (result.error) { console.error(result.error.message); process.exit(1); }
process.exit(result.status ?? 1);
