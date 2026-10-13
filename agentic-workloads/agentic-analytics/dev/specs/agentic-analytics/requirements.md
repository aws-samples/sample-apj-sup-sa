# Requirements Document

## Introduction

This document defines the requirements for the Agentic Analytics system - a deployable demo showcasing how business users can access data self-service with natural language. The system is powered by Amazon Bedrock, Strands Agents, and Bedrock AgentCore, demonstrating AI-driven business intelligence for the Timely-Unicorn multi-tenant SaaS platform serving unicorn rental businesses.

## Glossary

- **Analytics_Assistant**: The AI-powered conversational agent that processes natural language queries and returns business insights
- **AgentCore_Runtime**: The AWS Bedrock AgentCore service that hosts and executes the Strands agent
- **AgentCore_Gateway**: The MCP gateway that connects the agent to Lambda tools via authenticated endpoints
- **Lambda_Tools**: AWS Lambda functions that execute database queries and return structured data
- **Account**: A unicorn rental business that subscribes to the Timely-Unicorn SaaS platform (tenant)
- **Customer**: An end user (individual or organization) who rents unicorns from a rental business
- **Unicorn**: A rental asset with attributes like breed, color, magical abilities, and hourly rate
- **Booking**: A confirmed rental agreement between a customer and a unicorn for a specific time period
- **Transaction**: A financial record associated with bookings, subscriptions, or other charges
- **Multi_Tenant_Model**: Architecture where each rental business operates in isolation with their own data

## Requirements

### Requirement 1: Natural Language Query Processing

**User Story:** As a business user, I want to ask questions about my business data in natural language, so that I can get insights without writing SQL queries.

#### Acceptance Criteria

1. WHEN a user submits a natural language query, THE Analytics_Assistant SHALL interpret the intent and select appropriate tools to answer the query
2. WHEN a query references business metrics (revenue, bookings, customers), THE Analytics_Assistant SHALL use the corresponding analytics tools to retrieve data
3. WHEN a query is ambiguous, THE Analytics_Assistant SHALL always request clarification from the user before proceeding
4. THE Analytics_Assistant SHALL respond in clear, professional language without emojis
5. WHEN presenting tabular data, THE Analytics_Assistant SHALL format results using markdown tables for readability

### Requirement 2: Multi-Tenant Data Isolation

**User Story:** As a rental business owner, I want my data to be isolated from other businesses, so that my business information remains private and secure.

#### Acceptance Criteria

1. THE Lambda_Tools SHALL filter all queries by account_id when provided
2. WHEN a tool receives an account_id parameter, THE Lambda_Tools SHALL return only data belonging to that account
3. THE Database_Schema SHALL include account_id as a foreign key in all tenant-specific tables (customers, unicorns, bookings, transactions)
4. WHEN no account_id is provided, THE Lambda_Tools SHALL return aggregated data across all accounts (for platform-level analytics)

### Requirement 3: Core Data Access Tools

**User Story:** As a business analyst, I want to access core business entities, so that I can understand the current state of my rental business.

#### Acceptance Criteria

1. THE Lambda_Tools SHALL provide a get_accounts_tool that returns rental business accounts with subscription details
2. THE Lambda_Tools SHALL provide a get_unicorns_tool that returns unicorn inventory with optional filters for account_id and availability
3. THE Lambda_Tools SHALL provide a get_customers_tool that returns customer records with optional filters for account_id, customer_type, and limit
4. THE Lambda_Tools SHALL provide a get_bookings_tool that returns booking records with optional date range filters
5. THE Lambda_Tools SHALL provide a get_transactions_tool that returns financial records with optional filters for account_id and transaction_type
6. THE Lambda_Tools SHALL provide search tools (search_unicorns_tool, search_customers_tool) for text-based queries

### Requirement 4: Business Intelligence Analytics

**User Story:** As a rental business manager, I want to view aggregated analytics and trends, so that I can make data-driven decisions.

#### Acceptance Criteria

1. THE Lambda_Tools SHALL provide a get_monthly_revenue_summary_tool that returns revenue metrics aggregated by month
2. THE Lambda_Tools SHALL provide a get_top_revenue_customers_tool that returns customers ranked by total revenue contribution
3. THE Lambda_Tools SHALL provide a get_top_revenue_breeds_tool that returns unicorn breeds ranked by revenue generation
4. THE Lambda_Tools SHALL provide a get_customer_retention_metrics_tool that segments customers by retention status (churned, at_risk, active, new)
5. THE Lambda_Tools SHALL provide a get_seasonal_trends_tool that returns monthly booking and revenue patterns
6. THE Lambda_Tools SHALL provide a get_revenue_by_time_and_day_tool that returns revenue patterns by hour and day of week

### Requirement 5: Operational Monitoring Tools

**User Story:** As rental staff, I want to monitor operational status, so that I can manage day-to-day business activities effectively.

#### Acceptance Criteria

1. THE Lambda_Tools SHALL provide a get_current_unicorn_availability_tool that returns real-time availability status of all unicorns
2. THE Lambda_Tools SHALL provide a get_unicorns_due_maintenance_tool that returns unicorns requiring maintenance with urgency levels
3. THE Lambda_Tools SHALL provide a get_calendar_bookings_tool that returns bookings formatted for calendar visualization
4. THE Lambda_Tools SHALL provide a get_daily_bookings_summary_tool that returns daily booking details with customer and unicorn information
5. THE Lambda_Tools SHALL provide a check_db_status_tool that verifies database connectivity and table existence

### Requirement 6: Customer Analytics

**User Story:** As a business owner, I want to understand customer behavior and value, so that I can optimize customer relationships and marketing.

#### Acceptance Criteria

1. THE Lambda_Tools SHALL provide a get_customer_lifetime_value_tool that calculates customer value metrics over their relationship
2. THE Lambda_Tools SHALL provide a get_customer_segmentation_tool that groups customers into value tiers (VIP, Premium, Standard, Basic)
3. WHEN analyzing customer retention, THE Analytics_Assistant SHALL identify at-risk customers based on booking patterns
4. THE Lambda_Tools SHALL support filtering customer analytics by account_id for tenant-specific insights

### Requirement 7: AgentCore Gateway Authentication

**User Story:** As a system administrator, I want secure authentication for the agent gateway, so that only authorized requests can access business data.

#### Acceptance Criteria

1. THE AgentCore_Gateway SHALL require OAuth2 bearer tokens for all requests
2. THE Analytics_Assistant SHALL fetch fresh access tokens from Cognito before each request
3. IF token fetch fails, THEN THE Analytics_Assistant SHALL fall back to cached tokens with appropriate warnings
4. THE AgentCore_Gateway SHALL use client credentials grant flow for machine-to-machine authentication
5. WHEN authentication fails, THE Analytics_Assistant SHALL return a user-friendly error message without exposing internal details

### Requirement 8: React UI Chat Interface

**User Story:** As a business user, I want a web-based chat interface, so that I can interact with the analytics assistant through my browser.

#### Acceptance Criteria

1. THE React_UI SHALL provide a chat panel for submitting natural language queries
2. THE React_UI SHALL display assistant responses with proper markdown formatting
3. THE React_UI SHALL provide navigation panels for different data views (Overview, Bookings, Customers, Unicorns, Revenue)
4. THE React_UI SHALL maintain conversation context within a session
5. WHEN the assistant is processing a query, THE React_UI SHALL display a loading indicator

### Requirement 9: Database Schema and Views

**User Story:** As a data engineer, I want pre-computed database views, so that complex analytics queries perform efficiently.

#### Acceptance Criteria

1. THE Database_Schema SHALL include views for operational data (daily_bookings_summary, calendar_bookings, current_unicorn_availability)
2. THE Database_Schema SHALL include views for financial analytics (monthly_revenue_summary, top_revenue_unicorns_by_period, revenue_by_time_and_day)
3. THE Database_Schema SHALL include views for customer analytics (customer_retention_metrics, customer_lifetime_value, customer_segmentation_by_revenue)
4. THE Database_Schema SHALL include a trigger that automatically updates unicorns.is_available when unicorn_availability records are inserted
5. THE Database_Schema SHALL use PostgreSQL ENUM types for constrained values (customer_type, transaction_type, status fields)

### Requirement 10: Streaming Response Support

**User Story:** As a user, I want to see responses as they are generated, so that I don't have to wait for the complete response before seeing results.

#### Acceptance Criteria

1. THE Analytics_Assistant SHALL support streaming responses via async iteration
2. WHEN streaming is enabled, THE AgentCore_Runtime SHALL yield response events incrementally
3. THE React_UI SHALL render streamed content progressively as it arrives
4. IF streaming fails mid-response, THEN THE Analytics_Assistant SHALL provide a graceful error message

### Requirement 11: Automated Infrastructure Deployment

**User Story:** As a developer, I want to deploy the entire infrastructure with a single command, so that I can quickly set up the demo environment without manual AWS console steps.

#### Acceptance Criteria

1. THE Deployment_Script SHALL create an Aurora PostgreSQL Serverless v2 cluster with appropriate capacity settings
2. THE Deployment_Script SHALL create a VPC with private subnets for database isolation
3. THE Deployment_Script SHALL create a security group allowing Lambda function access to the database
4. THE Deployment_Script SHALL create a Secrets Manager secret to store database credentials securely
5. THE Deployment_Script SHALL wait for the Aurora cluster to reach 'available' state before proceeding
6. WHEN the cluster is available, THE Deployment_Script SHALL initialize the database schema from schema.sql
7. WHEN the schema is created, THE Deployment_Script SHALL load all CSV data files in foreign-key order
8. WHEN data is loaded, THE Deployment_Script SHALL create all analytics views
9. THE Deployment_Script SHALL output the Aurora endpoint and secret ARN for use by subsequent deployment steps
10. IF any deployment step fails, THEN THE Deployment_Script SHALL provide clear error messages and cleanup instructions

### Requirement 12: AWS Glue Data Catalog Integration

**User Story:** As a data engineer, I want the database tables registered in AWS Glue Data Catalog, so that I can discover and manage metadata centrally and enable other AWS analytics services.

#### Acceptance Criteria

1. THE Deployment_Script SHALL create an AWS Glue database to represent the Timely-Unicorn schema
2. THE Deployment_Script SHALL create a Glue Crawler configured to scan the Aurora PostgreSQL database
3. WHEN the crawler runs, THE Glue_Crawler SHALL discover all tables and create corresponding Glue table definitions
4. THE Glue_Catalog SHALL store metadata including column names, data types, and table descriptions
5. THE Deployment_Script SHALL export the Glue Catalog metadata to a structured format (JSON or Parquet)
6. THE exported metadata SHALL include table schemas, column descriptions, relationships, and sample values

### Requirement 13: Vector Embeddings for Semantic Search

**User Story:** As a business user, I want the system to understand the meaning of my queries, so that I can find relevant data even when I don't use exact column or table names.

#### Acceptance Criteria

1. THE Deployment_Script SHALL enable the pgvector extension in the Aurora PostgreSQL database
2. THE Deployment_Script SHALL create a metadata_embeddings table to store vectorized catalog metadata
3. THE Vectorization_Script SHALL use Amazon Bedrock Titan Embeddings model to generate vector embeddings
4. THE Vectorization_Script SHALL generate embeddings for table names, column names, column descriptions, and sample values
5. THE metadata_embeddings table SHALL store the original text, embedding vector, and metadata type (table/column/description)
6. THE Analytics_Assistant SHALL use vector similarity search to find relevant tables and columns for ambiguous queries
7. WHEN a user query doesn't match exact column names, THE Analytics_Assistant SHALL use semantic search to suggest relevant data sources
