# Implementation Plan: Agentic Analytics

## Overview

This implementation plan focuses on adding the missing infrastructure automation using CloudFormation, Glue catalog integration, vector embeddings, and test coverage to the existing Agentic Analytics system. The core agent, Lambda tools, UI, and database schema are already implemented.

## Tasks

- [x] 1. Create Aurora PostgreSQL CloudFormation Template
  - [x] 1.1 Create `infrastructure/aurora-stack.yaml` with VPC resources
    - Define VPC with CIDR block 10.0.0.0/16
    - Create 2 private subnets in different AZs
    - Create 2 public subnets for NAT gateway
    - Create Internet Gateway and NAT Gateway
    - Create route tables for private and public subnets
    - _Requirements: 11.1, 11.2_

  - [x] 1.2 Add security group resources to CloudFormation
    - Create security group for Aurora (allow 5432 from Lambda SG)
    - Create security group for Lambda (allow outbound to Aurora and Secrets Manager)
    - Create VPC endpoints for Secrets Manager and RDS Data API
    - _Requirements: 11.3_

  - [x] 1.3 Add Secrets Manager secret resource
    - Create secret with GenerateSecretString for password
    - Define username, database name in secret
    - _Requirements: 11.4_

  - [x] 1.4 Add Aurora Serverless v2 cluster resources
    - Create DB subnet group
    - Create Aurora PostgreSQL cluster (engine 15.4+)
    - Configure ServerlessV2ScalingConfiguration (min 0.5, max 4 ACU)
    - Enable Data API (EnableHttpEndpoint)
    - Create DB instance with db.serverless instance class
    - _Requirements: 11.1, 13.1_

  - [x] 1.5 Add CloudFormation outputs
    - Output VPC ID, subnet IDs, security group IDs
    - Output Aurora cluster endpoint and ARN
    - Output Secrets Manager secret ARN
    - Output Resource ARN for RDS Data API
    - _Requirements: 11.9_

- [x] 2. Create Post-Deployment Initialization Script
  - [x] 2.1 Create `scripts/init_database.py` for pgvector and schema
    - Read CloudFormation outputs
    - Enable pgvector extension via RDS Data API
    - Load schema.sql via RDS Data API
    - _Requirements: 11.5, 11.6, 13.1_

  - [x] 2.2 Add CSV data loading function
    - Load CSVs in foreign-key order: subscription_plans → accounts → users → customers → unicorns → unicorn_availability → bookings → transactions → subscription_tracker
    - Use existing type casting logic from load_via_rds_api.py
    - Add progress reporting
    - _Requirements: 11.7_

  - [x] 2.3 Add views creation function
    - Create all 22 analytics views
    - Use existing view definitions from create_views.py
    - _Requirements: 11.8_

  - [x] 2.4 Generate configuration files for agent deployment
    - Generate .env file for agentcore_strands with Aurora endpoint
    - Update staffcast_gateway_config.json template
    - _Requirements: 11.9_

- [x] 3. Checkpoint - Verify Aurora deployment works end-to-end
  - Deploy CloudFormation stack
  - Run init_database.py
  - Verify data loading completes successfully
  - Verify views are created and queryable
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Create AWS Glue Data Catalog Resources
  - [x] 4.1 Create `infrastructure/glue-stack.yaml` with Glue database
    - Create Glue database named 'timely_unicorn'
    - Add database description and metadata
    - Create JDBC connection to Aurora PostgreSQL
    - Create IAM role for Glue with necessary permissions
    - _Requirements: 12.1_

  - [x] 4.2 Create `scripts/register_glue_tables.py` for manual table registration
    - Define table schemas based on schema.sql (9 tables)
    - Register tables directly in Glue Data Catalog via boto3
    - Include column names, types, and descriptions
    - Avoid crawler connectivity issues with manual registration
    - _Requirements: 12.2, 12.3_

  - [x] 4.3 Add metadata export function to registration script
    - Query Glue catalog for all table definitions after registration
    - Export table schemas, column types, descriptions to JSON
    - Save to `infrastructure/glue-catalog-metadata.json`
    - _Requirements: 12.4, 12.5, 12.6_

- [x] 5. Create Vector Embeddings System
  - [x] 5.1 Create `scripts/generate_embeddings.py` with embeddings table creation
    - Create metadata_embeddings table with vector(1536) column
    - Create IVFFlat index for cosine similarity
    - _Requirements: 13.2_

  - [x] 5.2 Add Bedrock Titan Embeddings integration
    - Initialize Bedrock client for Titan Embeddings model (amazon.titan-embed-text-v2:0)
    - Create function to generate embeddings for text
    - Handle rate limiting and retries
    - _Requirements: 13.3_

  - [x] 5.3 Add metadata vectorization logic
    - Generate embeddings for table names with descriptions
    - Generate embeddings for column names with types and descriptions
    - Generate embeddings for sample values
    - Store all embeddings in metadata_embeddings table
    - _Requirements: 13.4, 13.5_

  - [x] 5.4 Add semantic search function
    - Create function to search metadata by vector similarity
    - Return top-k most relevant tables/columns
    - Include relevance scores
    - _Requirements: 13.6_

- [x] 6. Checkpoint - Verify Glue and embeddings work
  - Deploy Glue CloudFormation stack
  - Run run_glue_crawler.py and verify catalog creation
  - Run generate_embeddings.py and verify embeddings stored
  - Test semantic search with sample queries
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Update Agent to Use Semantic Search
  - [x] 7.1 Add semantic search tool to Lambda
    - Create semantic_search_tool in datafoundation_lambda.py
    - Accept query string, return relevant tables/columns
    - _Requirements: 13.6, 13.7_

  - [x] 7.2 Update agent system prompt to use semantic search
    - Add semantic_search_tool to available tools
    - Update response guidelines to use semantic search for ambiguous queries
    - _Requirements: 13.7_

- [x] 8. Add Unit Tests for Scripts
  - [x]* 8.1 Write unit tests for init_database.py
    - Test schema loading logic
    - Test data loading with type casting
    - Test views creation
    - Mock RDS Data API client
    - _Requirements: 11.5-11.8_

  - [x]* 8.2 ~~Write unit tests for run_glue_crawler.py~~ (Script removed - tables registered manually via register_glue_tables.py)
    - ~~Test crawler start logic~~
    - ~~Test polling and completion detection~~
    - ~~Test metadata export format~~
    - _Requirements: 12.1-12.6_

  - [x]* 8.3 Write unit tests for generate_embeddings.py
    - Test embedding generation
    - Test vector storage
    - Test semantic search
    - _Requirements: 13.1-13.7_

- [x] 9. Add Property Tests for Lambda Tools
  - [x]* 9.1 Write property test for account ID data isolation
    - **Property 3: Account ID Data Isolation**
    - For any tool response with account_id, all records must have matching account_id
    - **Validates: Requirements 2.2**

  - [x]* 9.2 Write property test for revenue ordering
    - **Property 5: Top Customers Revenue Ordering**
    - For any result from get_top_revenue_customers_tool, customers must be sorted by revenue descending
    - **Validates: Requirements 4.2**

  - [x]* 9.3 Write property test for valid retention segments
    - **Property 7: Valid Retention Segments**
    - For any customer from get_customer_retention_metrics_tool, segment must be one of: churned, at_risk, active, new
    - **Validates: Requirements 4.4**

  - [x]* 9.4 Write property test for valid maintenance urgency
    - **Property 8: Valid Maintenance Urgency Levels**
    - For any unicorn from get_unicorns_due_maintenance_tool, urgency must be one of: overdue, due_this_week, due_this_month
    - **Validates: Requirements 5.2**

  - [x]* 9.5 Write property test for valid customer tiers
    - **Property 10: Valid Customer Tier Assignments**
    - For any customer from get_customer_segmentation_tool, tier must be one of: VIP, Premium, Standard, Basic
    - **Validates: Requirements 6.2**

- [x] 10. Add Integration Tests
  - [x]* 10.1 Write integration test for full deployment flow
    - Test CloudFormation stack creates working cluster
    - Test init_database.py completes
    - Test views are queryable
    - _Requirements: 11.1-11.10_

  - [x]* 10.2 Write integration test for Glue catalog flow
    - Test crawler discovers all tables
    - Test metadata export contains expected fields
    - _Requirements: 12.1-12.6_

  - [x]* 10.3 Write integration test for semantic search
    - Test embeddings are generated for all tables
    - Test semantic search returns relevant results
    - _Requirements: 13.1-13.7_

- [x] 11. Final Checkpoint - Full system verification
  - Run all unit tests
  - Run all property tests
  - Run all integration tests
  - Verify end-to-end deployment works
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Create Master Deployment Script
  - [x] 12.1 Create `deploy_all.sh` orchestration script
    - **Check AWS credentials** - verify AWS_PROFILE or AWS_ACCESS_KEY_ID is set
    - **Prompt for AWS region** if not set via AWS_REGION environment variable
    - **Display target account** - show account ID and region before proceeding
    - **Confirm deployment** - ask user to confirm before creating resources
    - Deploy aurora-stack.yaml via `aws cloudformation deploy --stack-name agentic-analytics-aurora`
    - **Wait for Aurora stack completion** via `aws cloudformation wait stack-create-complete`
    - Verify Aurora cluster is in 'available' state before proceeding
    - Run init_database.py to load schema, data, and views
    - Deploy glue-stack.yaml via `aws cloudformation deploy --stack-name agentic-analytics-glue`
    - **Wait for Glue stack completion** via `aws cloudformation wait stack-create-complete`
    - Run run_glue_crawler.py to execute crawler and export metadata
    - Run generate_embeddings.py to create vector embeddings
    - Run deploy_agentcore_gateway.py to deploy Lambda and Gateway
    - Provide progress updates and error handling at each step
    - Exit with clear error message if any step fails
    - _Requirements: 11.1-11.10, 12.1-12.6, 13.1-13.7_

  - [x] 12.2 Add configuration file for deployment settings
    - Create `deploy-config.yaml` with customizable settings:
      - AWS region (default: us-west-2 / Sydney)
      - Stack name prefix (default: agentic-analytics)
      - Aurora min/max ACU capacity
      - VPC CIDR block
    - Script reads config and applies to CloudFormation parameters
    - _Requirements: 11.1_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- CloudFormation templates go in `infrastructure/` directory
- Python scripts go in `scripts/` directory
- The existing Lambda tools, agent, and UI code do not need modification except for adding the semantic search tool
