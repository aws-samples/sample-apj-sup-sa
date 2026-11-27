// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * A whole-number form field's text. An empty or partial entry is invalid rather than 0: a budget
 * of 0 is a real setting (it stops the work), so a cleared field must never save as one.
 */
export class WholeNumber {
  constructor(
    readonly text: string,
    private readonly min = 0,
  ) {}

  static of(value: number, min = 0): WholeNumber {
    return new WholeNumber(String(value), min);
  }

  get value(): number | null {
    const t = this.text.trim();
    if (!/^\d+$/.test(t)) return null;
    const n = Number(t);
    return Number.isSafeInteger(n) && n >= this.min ? n : null;
  }

  get error(): string | undefined {
    return this.value === null ? `Enter a whole number of at least ${this.min}.` : undefined;
  }

  with(text: string): WholeNumber {
    return new WholeNumber(text, this.min);
  }
}
