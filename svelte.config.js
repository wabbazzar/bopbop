import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      fallback: 'index.html',
      strict: true
    }),
    paths: {
      base: ''
    },
    // Unique per build, including builds made within the same minute.
    version: { name: process.env.BOPBOP_WEB_VERSION || 'development' }
  }
};

export default config;
