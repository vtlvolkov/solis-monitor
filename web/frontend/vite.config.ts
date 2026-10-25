import { defineConfig } from 'vitest/config';
import preact from '@preact/preset-vite';

// `npm run dev` proxies the API to a backend on :8088 (override with API_URL).
export default defineConfig({
  plugins: [preact()],
  server: { proxy: { '/api': process.env.API_URL ?? 'http://localhost:8088' } },
  build: { outDir: 'dist', sourcemap: false },
  // 30 s: the 7776-combination flow test takes ~6 s on a Raspberry Pi (the Docker build runs the tests there).
  test: { include: ['src/**/*.test.ts'], environment: 'node', testTimeout: 30_000 },
});
