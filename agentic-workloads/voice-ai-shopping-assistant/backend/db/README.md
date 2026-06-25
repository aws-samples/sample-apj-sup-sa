# Silo 1 — Database & Seed (`DataStack`)

**Owns (writes only here):** `db/**`, `infra/lib/data-stack.ts`.
**Deploys:** `DataStack`.

## Build
- Aurora Serverless v2 PostgreSQL: `MinCapacity 0` (auto-pause, `SecondsUntilAutoPause: 300`),
  `MaxCapacity 2`, **Data API enabled**, credentials in Secrets Manager, private subnets.
- `db/schema.sql` — tables per SPEC §3.6 (snake_case, plural). Columns must match the
  `Product` / `Recipe` / `Cart` / `Order` shapes in `agent/contracts.py` exactly.
- `db/seed/products.json`, `db/seed/recipes.json` — ~150–300 grocery items, snake_case
  fields mirroring `Product`; ≥5 brand variants for staples (pasta, milk) with realistic
  `allergens` / `dietary_tags` / `quality_tier`. This is the "live inventory."
- Seed loader runs `rds-data:BatchExecuteStatement`.

## Exports (SSM)
`/aisle/db/cluster_arn`, `/aisle/db/secret_arn`, `/aisle/db/name`

## Verify
`aws rds-data execute-statement ... "select count(*) from products"` → ~200.
