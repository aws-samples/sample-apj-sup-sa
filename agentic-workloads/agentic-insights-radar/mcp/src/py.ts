// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Small Python-compatible helpers. This server was ported from Python, and its messages, JSON and
 * length limits are part of its behaviour: they are reproduced here rather than approximated.
 */

/** Python's `str()` for a JSON value. */
export function pyStr(value: unknown): string {
  if (value === null || value === undefined) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return pyJson(value);
}

/** Python's truthiness for a JSON value. */
export function pyTruthy(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (isObject(value)) return Object.keys(value).length > 0;
  return Boolean(value);
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * `json.dumps(value, ensure_ascii=False)`: Python's default separators (", " and ": "). Strings are
 * escaped the same way by both languages once non-ASCII is left as is.
 */
export function pyJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(pyJson).join(', ')}]`;
  if (isObject(value)) {
    const parts = Object.entries(value)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => `${JSON.stringify(k)}: ${pyJson(v)}`);
    return `{${parts.join(', ')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/** Python's `repr()` of a string. */
export function pyRepr(text: string): string {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  let out = '';
  for (const ch of text) {
    if (ch === '\\') out += '\\\\';
    else if (ch === quote) out += `\\${quote}`;
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (ch !== ' ' && NOT_PRINTABLE.test(ch)) {
      const code = ch.codePointAt(0) as number;
      out +=
        code < 0x100
          ? `\\x${code.toString(16).padStart(2, '0')}`
          : code < 0x10000
            ? `\\u${code.toString(16).padStart(4, '0')}`
            : `\\U${code.toString(16).padStart(8, '0')}`;
    } else out += ch;
  }
  return `${quote}${out}${quote}`;
}

const NOT_PRINTABLE = /^[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]$/u;

/** Python's `len()`: code points, not UTF-16 units. */
export function cpLen(text: string): number {
  let n = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    const lowAfterHigh = c >= 0xdc00 && c <= 0xdfff && i > 0 && isHigh(text.charCodeAt(i - 1));
    if (!lowAfterHigh) n++;
  }
  return n;
}

function isHigh(c: number): boolean {
  return c >= 0xd800 && c <= 0xdbff;
}

/** Python's `text[:n]`, in code points. */
export function cpSlice(text: string, n: number): string {
  if (text.length <= n) return text;
  return Array.from(text).slice(0, n).join('');
}

/** Python's `str.strip()`: Python's whitespace also includes U+001C..U+001F and U+0085. */
export function pyStrip(text: string): string {
  // eslint-disable-next-line no-control-regex -- U+001C..U+001F are whitespace to Python
  return text.replace(/^[\s\x1c-\x1f\x85]+|[\s\x1c-\x1f\x85]+$/gu, '');
}

/** `urllib.parse.quote(value, safe="")`: everything but `A-Za-z0-9_.-~` is percent-encoded. */
export function quote(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}
