# Amazon Connect

Sample projects for voice, chat, call centres, and back-office automation on
Amazon Connect.

| Sample | Description |
| --- | --- |
| [multi-tenant-agentic-contact-center](multi-tenant-agentic-contact-center/) | Multi-tenant SaaS support experience on Amazon Connect — chat, Cases, and Customer Profiles — with agentic self-service through Q in Connect and an Amazon Bedrock AgentCore Gateway. Tenant isolation is enforced server-side by a Gateway interceptor that stamps the caller's `merchant_id` from their Cognito JWT, backed by Aurora PostgreSQL zero-ETL into a VPC-only OpenSearch Serverless collection. |
