# AnyCompanyPay — Aurora PostgreSQL module

A self-contained AWS CDK module that spins up a **private Amazon Aurora PostgreSQL** cluster and seeds
it with **200 multi-tenant transaction records**. It is independent of the main `infra/` stacks (its
own VPC and lifecycle) and deploys to whatever region your AWS session targets (`AWS_REGION` /
`CDK_DEFAULT_REGION`; the reference build used `REGION`).

## What it creates

| Resource | Detail |
|---|---|
| **VPC** | Dedicated, **isolated private subnets only** — no Internet Gateway, no NAT |
| **Aurora PostgreSQL** | Latest engine (**18.4**), one writer instance on **`db.t4g.large`**, storage encrypted |
| **Credentials** | Generated into **Secrets Manager** (no password in the template) |
| **Secrets Manager VPC endpoint** | So the in-VPC seed Lambda can read the secret without any internet route |
| **`transactions` table** | `transaction_id` **PRIMARY KEY**, plus `merchant_id`, `merchant_name`, `customer_name`, `amount`, `currency`, `status`, `payment_method`, `created_at` |
| **Seed Lambda (Trigger)** | Runs **inside the VPC** on deploy, creates the table and inserts 200 rows |

### Why it is not publicly reachable

The cluster is placed in **`PRIVATE_ISOLATED`** subnets of a VPC that has **no Internet Gateway and no
NAT gateway**, the writer is `publiclyAccessible: false`, and its security group only admits port 5432
from the seed Lambda's security group. There is no public path to the database.

### How seeding works without exposing the DB

Seeding a private database is done from **inside** the VPC: a Lambda (subnet-attached) connects to
Aurora over 5432, reads the DB password from Secrets Manager through an **interface VPC endpoint**,
creates the table, and inserts the rows. CDK's `Trigger` invokes it after the cluster is available.
The seed is **idempotent** — if the table already has rows it does nothing.

The 200 records are spread evenly across the five AnyCompanyPay tenants (`mch_luxe`, `mch_nova`,
`mch_pixel`, `mch_terra`, `mch_volt`); every row is an identified transaction keyed by
`transaction_id`.

## Deploy

```bash
# from repo root
aws login                     # if the session expired
bash database/deploy.sh
```

or manually:

```bash
cd database
npm install
npx cdk deploy AnyCompanyPayAuroraStack --require-approval never --outputs-file cdk-outputs.json
```

Outputs include the cluster endpoint, port, database name, and the Secrets Manager secret ARN.

## Verify

The database is private, so verify from CloudWatch (the seed Lambda logs
`seeded 200 transactions; per tenant: …`) or connect from inside the VPC. To query it yourself, run
`psql` from a host/Lambda in the same VPC using the credentials in Secrets Manager, e.g.:

```sql
SELECT merchant_id, count(*) FROM transactions GROUP BY merchant_id ORDER BY merchant_id;
SELECT * FROM transactions ORDER BY created_at DESC LIMIT 5;
```

## Tear down

```bash
cd database
npx cdk destroy AnyCompanyPayAuroraStack
```

`removalPolicy` is `DESTROY` and deletion protection is off (demo settings) — change both for anything
real.

## Notes

- **Cost:** a `db.t4g.large` Aurora instance plus storage bills while running — destroy it when done.
- **Latest version:** `18.4` was the newest Aurora PostgreSQL in the build region at build time
  (`aws rds describe-db-engine-versions --engine aurora-postgresql`). Bump the version in
  `lib/aurora-stack.ts` if a newer one is available.
