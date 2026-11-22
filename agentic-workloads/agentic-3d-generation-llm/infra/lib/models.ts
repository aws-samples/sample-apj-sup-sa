// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as fs from 'fs';
import * as path from 'path';

export interface ModelConfig {
  key: string;
  name: string;
  provider: string;
  api: 'converse' | 'mantle';
  modelId: string;
  baseModelId: string;
  pricing: { inputPerMTok: number; outputPerMTok: number; cacheReadPerMTok?: number; cacheWritePerMTok?: number };
  /** Models a user may run in this slot instead (the Generate page shows a dropdown). */
  options?: ModelConfig[];
}

/** us.* / global.* IDs are cross-Region inference profiles; anything else is an in-Region foundation model. */
export const usesInferenceProfile = (m: Pick<ModelConfig, 'modelId'>) => /^(us|eu|apac|global)\./.test(m.modelId);

export const MODELS_FILE = path.join(__dirname, '..', '..', 'config', 'models.json');

function validate(m: ModelConfig, keys: Set<string>) {
  if (!/^[a-z0-9-]{1,40}$/.test(m.key)) throw new Error(`Bad model key: ${m.key}`);
  if (keys.has(m.key)) throw new Error(`Duplicate model key: ${m.key}`);
  keys.add(m.key);
  if (m.api !== 'converse' && m.api !== 'mantle') throw new Error(`Model ${m.key}: api must be converse or mantle`);
  if (!(m.pricing?.inputPerMTok >= 0 && m.pricing?.outputPerMTok >= 0)) throw new Error(`Model ${m.key}: pricing missing`);
}

/** The model slots shown side by side; each slot's `options` are validated too. */
export function loadModels(file = MODELS_FILE): ModelConfig[] {
  const models = JSON.parse(fs.readFileSync(file, 'utf-8')).models as ModelConfig[];
  if (!Array.isArray(models) || models.length === 0) throw new Error(`${file} has no models`);
  const keys = new Set<string>();
  for (const m of models) {
    validate(m, keys);
    for (const o of m.options ?? []) {
      if (o.options) throw new Error(`Model ${o.key}: options cannot be nested`);
      validate(o, keys);
    }
  }
  return models;
}

/** Every model that can run: each slot's default plus its options (for IAM and the worker). */
export function runnableModels(models: ModelConfig[]): ModelConfig[] {
  return models.flatMap((m) => [m, ...(m.options ?? [])]);
}

/** Models no longer run, so past generations can still show their name and price. Only key, name and pricing are used. */
export function loadRetiredModels(file = MODELS_FILE): Pick<ModelConfig, 'key' | 'name' | 'provider' | 'modelId' | 'pricing'>[] {
  const active = new Set(runnableModels(loadModels(file)).map((m) => m.key));
  const retired = (JSON.parse(fs.readFileSync(file, 'utf-8')).retired ?? []) as ModelConfig[];
  return retired
    .filter((m) => !active.has(m.key))
    .map(({ key, name, provider, modelId, pricing }) => ({ key, name, provider, modelId, pricing }));
}
