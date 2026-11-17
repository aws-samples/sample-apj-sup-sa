# Databases

Sample projects for relational, non-relational, Timestream, and vector
databases on AWS.

| Sample | Description |
| --- | --- |
| [aurora-mysql-upgrade-agents](aurora-mysql-upgrade-agents/) | AWS Bedrock AgentCore multi-agent package that analyzes Aurora MySQL 3.04 → 3.10 minor-upgrade readiness on a Blue-Green deployment (variable diff, error logs, InnoDB status, query risk, and Blue/Green EXPLAIN plan diff) and produces downloadable Korean/English reports. |
| [postgres-migration-safety](postgres-migration-safety/) | Agent skill (Claude Code / Kiro) that reviews PostgreSQL schema migrations (Prisma, Drizzle, Rails, Django, Alembic, raw SQL, and more) for table locks, rewrites, and breaking changes before they ship, and rewrites risky ones into zero-downtime steps. Built for startups on RDS/Aurora PostgreSQL without a dedicated DBA. Lock claims are verified against PostgreSQL 14, 17, and 18. |
| [rds-mysql-upgrade-agents](rds-mysql-upgrade-agents/) | AWS Bedrock AgentCore multi-agent package that analyzes RDS/Aurora MySQL 8.0 → 8.4 upgrade readiness on a Blue-Green deployment (variable diff, error logs, InnoDB status, and query optimizer risk) and produces downloadable Korean/English reports. |
