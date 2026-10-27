import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    watch: {
      // pnpm-workspace.yaml keeps the package store inside apps/web. Watching its
      // tens of thousands of files makes the dev server crawl, above all on Windows/OneDrive.
      ignored: ['**/.pnpm-store/**', '**/.pnpm-cache/**', '**/dist/**'],
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    env: { TZ: 'UTC' },
    // antd modals are slow to mount in jsdom when files run in parallel.
    testTimeout: 20000,
    exclude: ['**/node_modules/**', '**/.pnpm-store/**', '**/dist/**'],
  },
});
