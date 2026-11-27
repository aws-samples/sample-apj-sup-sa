// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Tool argument types that accept what the Python server (pydantic, lax mode) accepted: a number or a
 * yes/no sent as a string, and a list sent as JSON text. The JSON Schema a client sees is the plain type.
 */

import { z } from 'zod';

function fromJsonText(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  try {
    const parsed: unknown = JSON.parse(value);
    return typeof parsed === 'string' ? value : parsed;
  } catch {
    return value;
  }
}

const TRUE = new Set(['1', 'on', 't', 'true', 'y', 'yes']);
const FALSE = new Set(['0', 'off', 'f', 'false', 'n', 'no']);

// The default goes on the inner type: zod 4 leaves a default set on the preprocess pipe out of the JSON Schema.
export function int(fallback?: number): z.ZodType<number> {
  const inner = z.number().int();
  return z.preprocess(
    (value) => {
      const v = fromJsonText(value);
      if (typeof v === 'string' && /^\s*[+-]?\d+\s*$/.test(v)) return parseInt(v, 10);
      return v;
    },
    fallback === undefined ? inner : inner.default(fallback),
  ) as unknown as z.ZodType<number>;
}

export function bool(fallback?: boolean): z.ZodType<boolean> {
  const inner = z.boolean();
  return z.preprocess(
    (value) => {
      const v = fromJsonText(value);
      if (v === 0 || v === 1) return v === 1;
      if (typeof v === 'string') {
        const word = v.trim().toLowerCase();
        if (TRUE.has(word)) return true;
        if (FALSE.has(word)) return false;
      }
      return v;
    },
    fallback === undefined ? inner : inner.default(fallback),
  ) as unknown as z.ZodType<boolean>;
}

export function strings(fallback?: null): z.ZodType<string[] | null> {
  const inner = z.array(z.string()).nullable();
  return z.preprocess(fromJsonText, fallback === undefined ? inner : inner.default(fallback)) as unknown as z.ZodType<
    string[] | null
  >;
}
