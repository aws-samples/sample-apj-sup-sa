// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it } from 'vitest';

import { WholeNumber } from './wholeNumber';

describe('WholeNumber', () => {
  it('reads whole numbers at or above the minimum', () => {
    expect(WholeNumber.of(0).value).toBe(0);
    expect(new WholeNumber(' 42 ').value).toBe(42);
    expect(WholeNumber.of(3, 1).with('1').value).toBe(1);
  });

  it('treats empty, partial, negative, fractional, too-small and unsafe entries as invalid', () => {
    for (const text of ['', '  ', '-1', '1.5', '1e3', 'abc', '9007199254740993']) {
      expect(new WholeNumber(text).value).toBeNull();
    }
    const n = new WholeNumber('0', 1);
    expect(n.value).toBeNull();
    expect(n.error).toBe('Enter a whole number of at least 1.');
    expect(WholeNumber.of(5).error).toBeUndefined();
  });
});
