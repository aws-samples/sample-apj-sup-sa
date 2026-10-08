-- bedrock-tier-bench schema (idempotent). Applied by the schema custom resource through the RDS Data API.
CREATE TABLE IF NOT EXISTS models (
  key          text PRIMARY KEY,
  family       text NOT NULL,
  display_name text NOT NULL,
  reasoning    boolean NOT NULL DEFAULT false,
  body_style   text NOT NULL DEFAULT 'openai',
  cache        jsonb NOT NULL DEFAULT '{}'::jsonb,
  sources      jsonb NOT NULL DEFAULT '[]'::jsonb,
  verified_at  date,
  notes        text NOT NULL DEFAULT '',
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS offerings (
  model_key text NOT NULL REFERENCES models(key) ON DELETE CASCADE,
  endpoint  text NOT NULL,
  api       text NOT NULL,
  scope     text NOT NULL,
  model_id  text NOT NULL,
  regions   text[] NOT NULL,
  tiers     text[] NOT NULL,
  base_path text,
  PRIMARY KEY (model_key, endpoint, api, scope, model_id)
);
CREATE TABLE IF NOT EXISTS runs (
  run_id   text PRIMARY KEY,
  started  timestamptz,
  finished timestamptz,
  version  text,
  config   jsonb NOT NULL DEFAULT '{}'::jsonb,
  meta     jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS cells (
  run_id         text NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  label          text NOT NULL,
  model          text NOT NULL,
  display_name   text,
  family         text,
  model_id       text NOT NULL,
  endpoint       text NOT NULL,
  api            text NOT NULL,
  scope          text NOT NULL,
  region         text NOT NULL,
  prompt_size    text NOT NULL,
  cache          text NOT NULL,
  tier           text NOT NULL,
  summary        jsonb NOT NULL,
  PRIMARY KEY (run_id, label)
);
CREATE INDEX IF NOT EXISTS cells_dims ON cells (model, endpoint, api, scope, region, prompt_size, cache, tier);
CREATE TABLE IF NOT EXISTS comparisons (
  run_id       text NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  model        text NOT NULL,
  display_name text,
  endpoint     text NOT NULL,
  api          text NOT NULL,
  scope        text NOT NULL,
  region       text NOT NULL,
  prompt_size  text NOT NULL,
  cache        text NOT NULL,
  tier         text NOT NULL,
  deltas       jsonb NOT NULL,
  PRIMARY KEY (run_id, model, endpoint, api, scope, region, prompt_size, cache, tier)
);
CREATE TABLE IF NOT EXISTS discovery_events (
  id       bigserial PRIMARY KEY,
  at       timestamptz NOT NULL DEFAULT now(),
  kind     text NOT NULL,
  model_key text,
  detail   jsonb NOT NULL DEFAULT '{}'::jsonb
);
