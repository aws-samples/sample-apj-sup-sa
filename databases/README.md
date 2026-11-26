# Databases

Sample projects for relational, non-relational, Timestream, and vector
databases on AWS.

| Sample | Description |
| --- | --- |
| [aurora-mysql-upgrade-agents](aurora-mysql-upgrade-agents/) | AWS Bedrock AgentCore multi-agent package that analyzes Aurora MySQL 3.04 → 3.10 minor-upgrade readiness on a Blue-Green deployment (variable diff, error logs, InnoDB status, query risk, and Blue/Green EXPLAIN plan diff) and produces downloadable Korean/English reports. |
| [rds-mysql-upgrade-agents](rds-mysql-upgrade-agents/) | AWS Bedrock AgentCore multi-agent package that analyzes RDS/Aurora MySQL 8.0 → 8.4 upgrade readiness on a Blue-Green deployment (variable diff, error logs, InnoDB status, and query optimizer risk) and produces downloadable Korean/English reports. |
| [pg-decider](pg-decider/) | PostgreSQL extension (PL/Python) that answers plain-language yes/no, pick-one and rating questions about table rows, e.g. `WHERE decide(tickets, 'the customer is angry')`, using a Strands Decider model server. |
