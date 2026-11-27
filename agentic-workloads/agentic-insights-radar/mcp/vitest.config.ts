// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['test/**/*.test.ts'],
    // The sign-in tests bind the fixed redirect port 127.0.0.1:8765: one test file at a time.
    fileParallelism: false,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.ts'],
      reportsDirectory: './coverage',
      thresholds: { lines: 100, branches: 100, functions: 100, statements: 100 },
    },
  },
});
