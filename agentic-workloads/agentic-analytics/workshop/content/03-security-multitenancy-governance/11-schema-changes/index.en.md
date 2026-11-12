---
title: "Step 11: Keep the Agent Current When the Schema Changes"
weight: 70
---

## Learning Objectives

By the end of this step, you will:
- Deploy a pipeline that notices schema changes and updates what the agent knows
- See which changes publish on their own, which wait for a human, and which are refused
- Watch the agent use a renamed column without anyone editing a prompt

## The problem

The agent learns your schema from two places: the Glue Data Catalog (column names and types for text-to-SQL) and the knowledge base (what tables and columns mean). Both were filled once, when the workshop started.

Databases do not stay still. Someone adds a column, renames another, or creates a new table. If nothing updates the catalog and the knowledge base, the agent keeps writing SQL against the old schema, and the queries fail or, worse, return the wrong thing.

## How the pipeline works

A Step Functions state machine runs after every Glue crawler run, or when you start it yourself:

1. **Snapshot** reads the Postgres catalog: tables, views, columns, comments and row-level security (RLS) policies.
2. **Classify** compares the snapshot with the last published one and sorts the changes:
   - *additive* (new column, new view): safe, publishes on its own
   - *breaking* (rename, type change, dropped column): could break saved queries, waits for approval
   - *blocked*: a new table with an `account_id` column but no RLS policy. Publishing it would let the agent read every tenant's rows, so the run stops.
3. **Describe** writes a plain-English description for anything new or changed, using the comment on the column if there is one and the agent's model if not.
4. **WaitForApproval** pauses breaking changes until someone approves or rejects them.
5. **Publish** updates the Glue table descriptions, writes one knowledge-base card per changed table or view (with a note such as "renamed from promo_code"), and starts a knowledge-base sync.

## Lab Procedures

### Step 11.1: Deploy the pipeline

Open :code[/workshop/agentic-analytics/app/agentcore_strands/agentcore-topup-stack.yaml]{showCopyAction=true}, find the **Step 11** fence, uncomment the lines between the markers, and deploy:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

Run the pipeline once to record today's schema as the starting point:

```bash
make schema-sync
```

Open the **Step Functions** console, choose the `agentic-analytics-schema-sync` state machine and open the newest run. The first run has nothing to compare with, so it describes every table and view and publishes them. Wait until it shows **Succeeded** (about a minute).

### Step 11.2: Make a safe change

Set up a small helper in the Code Editor terminal. It runs SQL through the RDS Data API with the database admin secret:

```bash
export CLUSTER_ARN=$(aws cloudformation list-exports --query "Exports[?Name=='agentic-analytics-AuroraClusterArn'].Value" --output text)
export SECRET_ARN=$(aws cloudformation list-exports --query "Exports[?Name=='agentic-analytics-DatabaseSecretArn'].Value" --output text)
sql() { aws rds-data execute-statement --resource-arn "$CLUSTER_ARN" --secret-arn "$SECRET_ARN" \
          --database timely_unicorn --sql "$1" --query numberOfRecordsUpdated --output text; }
```

Add a column with a comment. Each command prints `0`, because DDL changes no rows:

```bash
sql "ALTER TABLE bookings ADD COLUMN promo_code varchar(20)"
sql "COMMENT ON COLUMN bookings.promo_code IS 'Marketing promo code applied at checkout, NULL when none'"
```

Now let the normal trigger run the pipeline. In the **AWS Glue** console, open **Crawlers**, select `agentic-analytics-aurora-crawler` and choose **Run**. The crawler takes about three minutes. When it finishes, a new run of the state machine starts on its own. Open it in the Step Functions console: the graph goes Snapshot, Classify, Describe, past the **NeedsApproval** check and straight to Publish, because a new column is additive. The Classify output lists the change as `column_added`.

### Step 11.3: Make a breaking change and approve it

Rename the column:

```bash
sql "ALTER TABLE bookings RENAME COLUMN promo_code TO promotion_code"
make schema-sync
```

This time the run stops at **WaitForApproval**. Choose that step in the graph and look at the input: the summary says which column was renamed and to what. Approve it from the terminal:

```bash
make approve
```

The run continues to Publish. (`make reject` would end the run instead and leave the published schema as it was.)

### Step 11.4: Ask the agent

Wait a minute for the knowledge-base sync, then start a new chat as `lyra.starwhisper@example-mythicalunicorns.com` and ask:

> "Using custom SQL, how many bookings have a promotion code set?"

Choose **Approve & Run**. The answer is a count (0 is expected: nobody has used a promotion code yet), not a "column does not exist" error. You have not run the crawler since the rename, so where did the agent get the new name? Publish rewrote the Glue column list for `bookings` from its own snapshot of the database, and it rewrote the knowledge-base card for `bookings`, which notes that `promo_code` became `promotion_code`. The agent reads both, so it never sees the old name.

### Step 11.5: Try a tenant table without RLS

```bash
sql "CREATE TABLE promotions (promotion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), account_id uuid NOT NULL, discount_pct numeric(5,2))"
make schema-sync
```

The run goes from Classify straight to **BlockedNoRowLevelSecurity** and fails with `TenantTableWithoutRowLevelSecurity`. Nothing is published, so the agent cannot see the table. Add a policy and run it again:

```bash
sql "ALTER TABLE promotions ENABLE ROW LEVEL SECURITY"
sql "CREATE POLICY tenant_promotions ON promotions FOR ALL USING (account_id = get_current_account_id())"
make schema-sync
```

Now the change is additive and publishes.

## Verification

- The first `make schema-sync` run succeeds and publishes every table and view.
- Adding a column publishes without approval; renaming it waits until `make approve`.
- After the rename, the agent's custom SQL count runs without a "column does not exist" error.
- The `promotions` table without RLS stops the run, and publishes once RLS is on.

## Troubleshooting

**`make approve` says no run is waiting**
- The run may not have reached WaitForApproval yet. Check the graph, then try again.

**Publish retries for a few minutes**
- The knowledge base runs one sync at a time. If the previous run's sync is still going, Publish waits and tries again.

**The agent still uses the old column name**
- Start a new chat, and check in the Bedrock console that the knowledge-base sync has finished.

## Summary

You connected schema changes to the agent's knowledge: safe changes flow through, risky ones wait for a person, and a table that would leak tenant data is refused. Next → [Summary & Next Steps](../../summary/)

## Reference Materials

- :link[AWS Step Functions: wait for a callback with a task token]{href="https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html#connect-wait-token" external=true}
- :link[AWS Glue crawler events in EventBridge]{href="https://docs.aws.amazon.com/glue/latest/dg/automating-awsglue-with-cloudwatch-events.html" external=true}
- :link[PostgreSQL Row-Level Security]{href="https://www.postgresql.org/docs/current/ddl-rowsecurity.html" external=true}
