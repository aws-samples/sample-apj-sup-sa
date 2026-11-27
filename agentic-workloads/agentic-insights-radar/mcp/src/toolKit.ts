// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** What every tool shares: annotations, the error result, path segments and the call/define shapes. */

import type { RegisteredTool } from '@modelcontextprotocol/sdk/server/mcp.js';
import type { ToolAnnotations } from '@modelcontextprotocol/sdk/types.js';
import type { z, ZodRawShape } from 'zod';

import { ApiError, type Query } from './client.js';
import { Fence } from './fence.js';
import { pyJson, quote } from './py.js';

export const MAX_CHARS = 60_000;

// Hints for the client's approval policy: reads change nothing; the writes change state (one also spends
// model tokens). They are marked destructive, so a client that auto-approves non-destructive tools still
// asks the user before text inside a result can trigger them.
export const READ: ToolAnnotations = { readOnlyHint: true, openWorldHint: false };
export const WRITE: ToolAnnotations = {
  readOnlyHint: false,
  destructiveHint: true,
  idempotentHint: false,
  openWorldHint: false,
};

/** A failure the client receives as an error result (`isError`), its message already fenced. */
export class ToolFailure extends Error {
  override name = 'ToolFailure';
}

/** An error result for the client (`isError`), its message fenced like any result. */
export function failure(tool: string, message: string): ToolFailure {
  return new ToolFailure(Fence.wrap(tool, pyJson({ error: message }), MAX_CHARS, { jsonPayload: true }));
}

/** One path segment. quote() leaves dots alone, so '.' and '..' would walk the API path. */
export function seg(value: string): string {
  if (value === '.' || value === '..') throw new ApiError(400, 'not a valid name');
  return quote(value);
}

export function quoteKind(kind: string): string {
  return seg(kind).replaceAll('%3A', ':');
}

export interface CallOptions {
  query?: Query | null;
  text?: boolean;
  transform?: (result: unknown) => unknown;
}

export type Call = (
  tool: string,
  method: string,
  path: string | (() => string),
  body?: unknown,
  options?: CallOptions,
) => Promise<string>;

export type Define = <S extends ZodRawShape>(
  name: string,
  config: { description: string; annotations: ToolAnnotations; inputSchema: S },
  handler: (args: z.infer<z.ZodObject<S>>) => Promise<string>,
) => RegisteredTool;
