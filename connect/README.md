# Amazon Connect

Sample projects for voice, chat, call centres, and back-office automation on
Amazon Connect.

| Sample | Description |
| --- | --- |
| [amazon-connect-instance-cutover](amazon-connect-instance-cutover/) | Serverless tool for a one time cutover of Amazon Connect Customer configuration to another instance, across AWS accounts and Regions. It copies queues, users, flows, AI agents and more than 15 other resource types in dependency order, rewrites ARNs inside flows, writes only through a target role with an ExternalId, and reports every manual follow up after a read only verification. |
| [multi-tenant-agentic-contact-center](multi-tenant-agentic-contact-center/) | Multi-tenant SaaS support experience on Amazon Connect — chat, Cases, and Customer Profiles — with agentic self-service through Q in Connect and an Amazon Bedrock AgentCore Gateway. Tenant isolation is enforced server-side by a Gateway interceptor that stamps the caller's `merchant_id` from their Cognito JWT, backed by Aurora PostgreSQL zero-ETL into a VPC-only OpenSearch Serverless collection. |
| [well-architected-ai-contact-center-readiness-lens](well-architected-ai-contact-center-readiness-lens/) | Two custom AWS Well-Architected lenses that assess whether knowledge content, knowledge bases, AI guardrails, security, and operations are ready for AI agents in AWS Connect Customer: a 21-question Startup Essentials lens for a first launch and a 60-question lens for larger or regulated deployments. Import either into the AWS Well-Architected Tool to get a risk rating and improvement plan for each question. |
