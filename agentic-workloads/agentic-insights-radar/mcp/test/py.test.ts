// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { describe, expect, it } from 'vitest';

import { bool, int, strings } from '../src/args.js';
import { cpLen, cpSlice, pyJson, pyRepr, pyStr, pyStrip, pyTruthy, quote } from '../src/py.js';

describe('Python-compatible helpers', () => {
  it('str() and truthiness', () => {
    expect([null, undefined, true, false, 'x', 1.5].map(pyStr)).toEqual(['None', 'None', 'True', 'False', 'x', '1.5']);
    expect(pyStr({ a: [1, 'b'] })).toBe('{"a": [1, "b"]}');
    expect([[], {}, '', 0, null].map(pyTruthy)).toEqual([false, false, false, false, false]);
    expect([[0], { a: 0 }, 'x', 1].map(pyTruthy)).toEqual([true, true, true, true]);
  });

  it('json.dumps with ensure_ascii=False', () => {
    expect(pyJson({ a: 1, b: [true, null, 'é\n"'], c: {}, d: [], e: undefined })).toBe(
      '{"a": 1, "b": [true, null, "é\\n\\""], "c": {}, "d": []}',
    );
    expect(pyJson(undefined)).toBe('null');
  });

  it('repr() of a string', () => {
    expect(pyRepr('abc')).toBe("'abc'");
    expect(pyRepr("it's")).toBe('"it\'s"');
    expect(pyRepr('both \' and "')).toBe("'both \\' and \"'");
    expect(pyRepr('a\\b\n\r\t')).toBe("'a\\\\b\\n\\r\\t'");
    expect(pyRepr('\x00\u200b\u{e0001} é ')).toBe("'\\x00\\u200b\\U000e0001 é '");
  });

  it('lengths, slices, strip and quote', () => {
    expect(cpLen('a\u{1f600}b')).toBe(3);
    expect(cpLen('\udc00\ud800')).toBe(2); // lone surrogates count once each
    expect(cpSlice('a\u{1f600}bc', 2)).toBe('a\u{1f600}');
    expect(cpSlice('abc', 5)).toBe('abc');
    expect(pyStrip('\x1c\x85 x 　')).toBe('x');
    expect(quote("a b!'()*~._-/é")).toBe('a%20b%21%27%28%29%2A~._-%2F%C3%A9');
  });

  it('argument types accept what pydantic accepted', () => {
    expect(int().parse('5')).toBe(5);
    expect(int().parse(' -3 ')).toBe(-3);
    expect(int().parse(4)).toBe(4);
    expect(int().parse('+5')).toBe(5); // not JSON, but an integer to pydantic
    expect(int().parse('007')).toBe(7);
    expect(int().safeParse('"5"').success).toBe(false); // JSON text of a string stays a string
    expect(int().safeParse('many').success).toBe(false);
    expect(int().safeParse(true).success).toBe(false);
    expect(bool().parse('Yes')).toBe(true);
    expect(bool().parse('true')).toBe(true);
    expect(bool().parse(0)).toBe(false);
    expect(bool().safeParse('maybe').success).toBe(false);
    expect(bool().safeParse(null).success).toBe(false);
    expect(strings().parse('["a"]')).toEqual(['a']);
    expect(strings().parse(null)).toBeNull();
    expect(strings().safeParse('a').success).toBe(false);
  });
});
