# AnyCompanyPay — Aurora → OpenSearch zero-ETL module

Replicates the `transactions` table from the private Aurora PostgreSQL cluster (the `database/`
module) into a **private Amazon OpenSearch Serverless** collection, using **zero-ETL** — i.e. an
**Amazon OpenSearch Ingestion (OSIS)** pipeline with the `rds` source (initial snapshot to S3 +
change-data-capture). Self-contained CDK module (region from your AWS session), in a separate folder.

## What it creates

| Resource | Detail |
|---|---|
| **OpenSearch Serverless collection** | `anycompany-pay-tx` (SEARCH), **private** — VPC-only network policy (`AllowFromPublic: false`), reachable only via its VPC endpoint |
| **OpenSearch Serverless VPC endpoint** | In the Aurora VPC's isolated subnets — the only path to the collection |
| **OSIS pipeline** | `anycompany-pay-zeroetl`, **MinUnits=1, MaxUnits=2** (your "min 1 / max 2"), attached to the Aurora VPC |
| **S3 bucket + KMS key** | For the initial snapshot export from Aurora |
| **IAM roles** | Export role (assumed by `export.rds.amazonaws.com`) + pipeline role (assumed by `osis-pipelines.amazonaws.com`) |

### How the zero-ETL works

AWS delivers Aurora → OpenSearch "zero-ETL" through an **OpenSearch Ingestion pipeline**:

1. **Initial snapshot** — the pipeline triggers an Aurora snapshot export to the S3 bucket (encrypted
   with the KMS key), then ingests it into the collection index.
2. **Change data capture (CDC)** — it then streams inserts/updates/deletes via Aurora's **logical
   replication**, keeping the OpenSearch index in near-real-time sync. `transaction_id` is used as the
   OpenSearch document ID, so updates/deletes reconcile correctly.

### Prerequisites (handled by the `database/` module)

- Aurora PostgreSQL **>= 16.4** (we run 18.4) with **logical replication** enabled via a cluster
  parameter group (`rds.logical_replication=1`, `aurora.enhanced_logical_replication=1`, …) and the
  writer rebooted. The `database/` module sets this up.
- Secrets Manager authentication on the cluster (the Aurora module's generated secret).

### Private / no public access

- The collection's **network policy** sets `AllowFromPublic: false` and only allows its **VPC
  endpoint** — there is no public path to OpenSearch.
- The OSIS pipeline runs **inside the Aurora VPC** (isolated subnets). Its ENIs reach the private
  Aurora on 5432 and the collection through the VPC endpoint. OpenSearch Ingestion is a managed
  service, so it reaches the AWS control-plane APIs (RDS, S3, Secrets Manager) over its own managed
  network — no NAT/Internet Gateway is added to the isolated VPC.

## Deploy

```bash
# from repo root — deploy the database module first, then:
aws login                       # if the session expired
bash opensearch-zeroetl/deploy.sh
```

The script auto-discovers the Aurora stack's VPC, subnets, security group, cluster id, and secret and
passes them as CDK context. Deploying the OSIS pipeline takes several minutes; the initial data sync
runs after the pipeline reaches `ACTIVE`.

## Verify

```bash
R="${AWS_REGION:-<your-region>}"
# pipeline status
aws osis get-pipeline --region $R --pipeline-name anycompany-pay-zeroetl \
  --query "Pipeline.Status" --output text
# collection status (should be ACTIVE, and private)
aws opensearchserverless batch-get-collection --region $R --names anycompany-pay-tx \
  --query "collectionDetails[0].status" --output text
```

The collection is private, so query it from inside the VPC (e.g. a bastion/Lambda in the Aurora VPC)
against the collection endpoint, index `transactions`.

## Cost & teardown

> **Cost:** OpenSearch Serverless keeps baseline OCUs running and the OSIS pipeline bills per OCU —
> this is materially more expensive than the other modules. Tear it down when done:

```bash
cd opensearch-zeroetl
npx cdk destroy AnyCompanyPayZeroEtlStack
```

## Notes / limitations (from AWS docs)

- Aurora → OpenSearch zero-ETL requires the cluster and pipeline to be in the **same account and
  Region**.
- One Aurora database per pipeline; DDL changes (renaming PKs, dropping/truncating tables, changing
  column types) are not tracked for consistency.
- `MinUnits`/`MaxUnits` are OSIS Ingestion OCUs. `1`/`2` keeps this demo small; raise `MaxUnits` for
  higher throughput.
