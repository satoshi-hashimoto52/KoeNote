import { defineConfig } from 'vitest/config';

// 純関数のユニットテスト用の最小構成。
// DOM は使わないので environment は既定の 'node' のまま。
// アプリのビルド（vite.config.ts）とは分離し、build / typecheck / 起動へ影響させない。
export default defineConfig({
  test: {
    include: ['electron/**/*.test.ts', 'frontend/src/**/*.test.ts'],
    environment: 'node'
  }
});
