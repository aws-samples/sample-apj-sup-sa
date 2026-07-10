# Phase 0: DR Assessment Intake

This steering file guides the agent through generating `dr-intake.json` — a fillable JSON form the user completes before the scan begins. The form captures business context, known RTO/RPO targets, team contacts, and the service inventory upfront, so later phases can skip redundant questions and produce more accurate output.

**This phase is optional.** If the user says "skip intake" or "go straight to scan", proceed directly to Phase 1 and note that intake was skipped in the state file.

---

## Generate the Intake Form

Write `dr-intake.json` to the user's working directory with the following content exactly. All values are pre-filled with `null` or empty defaults — the user replaces them with their actual values.

Also create the `dr-context/` directory and a `dr-context/README.md` explaining what to put there.

Use chunked write if the content exceeds 50 lines. After writing, read back the first 10 lines to verify the file was created correctly.

### Create `dr-context/` folder

Create the directory `dr-context/` in the working directory and write `dr-context/README.md`:

```markdown
# DR Context Folder

Drop any of the following files here before running the DR assessment.
The agent will read them during Phase 0 and use the information to enrich
the gap analysis, DR plan, and recommendations.

## Supported file types

| File type | What to include | How it's used |
|-----------|----------------|---------------|
| Architecture diagrams | `.png`, `.jpg`, `.svg`, `.drawio`, `.pdf` | Agent reads image/diagram to identify services, data flows, and dependencies not visible from the AWS API scan |
| IaC files | `.yaml`, `.json`, `.tf`, `.cdk.ts`, `.cdk.py` | CloudFormation, Terraform, CDK — agent identifies resources and existing DR configurations |
| Existing runbooks | `.md`, `.docx`, `.pdf` | Existing DR or operational runbooks — agent identifies gaps vs current best practices |
| Network diagrams | `.png`, `.jpg`, `.svg`, `.drawio` | VPC topology, on-premises connectivity, hybrid architecture |
| Application architecture docs | `.md`, `.pdf`, `.docx` | Service dependencies, data flows, SLA requirements |
| Existing DR plan | `.md`, `.pdf`, `.docx` | Current DR plan — agent identifies what's missing or outdated |
| Cost reports | `.csv`, `.pdf` | AWS Cost Explorer exports — used to estimate DR cost impact |
| Compliance requirements | `.md`, `.pdf`, `.docx` | SOC 2, HIPAA, PCI-DSS, ISO 27001 requirements affecting DR |

## Notes

- Files are read-only — the agent will never modify files in this folder
- Sensitive files (credentials, private keys) should NOT be placed here
- Large files (> 10MB) may be skipped — compress or summarize if needed
- The agent will list all files it found and what it extracted from each
```

### Create `dr-intake.json`

```json
{
  "_instructions": "Fill in this form before running the DR assessment. Replace null values with your actual data. Leave fields as null if unknown — the agent will gather missing information interactively. When done, tell the agent: 'intake complete' or 'ready to scan'.",
  "_version": "aws-dr-power v1.0.0",

  "organization": {
    "name": null,
    "aws_account_ids": [],
    "primary_region": null,
    "recovery_region": null,
    "additional_scan_regions": []
  },

  "scope": {
    "full_account_scan": true,
    "workloads_in_scope": [
      {
        "name": null,
        "description": null,
        "primary_services": []
      }
    ]
  },

  "business_requirements": {
    "workload_targets": [
      {
        "workload_name": null,
        "max_downtime_rto": null,
        "max_data_loss_rpo": null,
        "criticality": null,
        "notes": null
      }
    ],
    "compliance_requirements": [],
    "existing_dr_strategy": null,
    "existing_dr_notes": null
  },

  "contacts": {
    "dr_coordinator":       { "name": null, "email": null, "phone": null },
    "infrastructure_lead":  { "name": null, "email": null, "phone": null },
    "database_lead":        { "name": null, "email": null, "phone": null },
    "application_lead":     { "name": null, "email": null, "phone": null },
    "security_lead":        { "name": null, "email": null, "phone": null },
    "communications_lead":  { "name": null, "email": null, "phone": null },
    "executive_sponsor":    { "name": null, "email": null, "phone": null },
    "primary_channel":      null,
    "backup_channel":       null,
    "status_page_url":      null
  },

  "service_inventory": {
    "compute":        { "ec2": false, "auto_scaling": false, "ecs": false, "eks": false, "lambda": false, "elastic_beanstalk": false, "batch": false, "lightsail": false, "app_runner": false },
    "databases":      { "rds": false, "aurora": false, "dynamodb": false, "elasticache": false, "redshift": false, "documentdb": false, "neptune": false, "timestream": false, "keyspaces": false, "memorydb": false },
    "storage":        { "s3": false, "efs": false, "fsx": false, "s3_glacier": false, "aws_backup": false },
    "networking":     { "vpc": false, "elb": false, "route53": false, "cloudfront": false, "api_gateway": false, "global_accelerator": false, "transit_gateway": false, "direct_connect": false, "vpn": false, "privatelink": false },
    "messaging":      { "sqs": false, "sns": false, "kinesis_streams": false, "kinesis_firehose": false, "msk": false, "amazon_mq": false, "eventbridge": false, "step_functions": false },
    "security":       { "iam": false, "kms": false, "secrets_manager": false, "acm": false, "waf": false, "shield": false, "guardduty": false, "security_hub": false, "directory_service": false },
    "operations":     { "cloudformation": false, "cloudwatch": false, "systems_manager": false, "config": false, "cloudtrail": false, "organizations": false, "service_catalog": false },
    "analytics":      { "glue": false, "athena": false, "emr": false, "opensearch": false, "quicksight": false, "lake_formation": false, "kinesis_analytics": false },
    "ai_ml":          { "sagemaker": false, "bedrock": false, "comprehend": false, "rekognition": false, "textract": false, "translate": false, "polly_transcribe": false, "forecast": false, "personalize": false },
    "app_integration":{ "appsync": false, "amplify": false, "cognito": false, "ses": false, "pinpoint": false, "connect": false, "chime": false },
    "iot_edge":       { "iot_core": false, "iot_greengrass": false, "iot_sitewise": false, "wavelength_outposts": false },
    "custom_services": [
      {
        "service_name": null,
        "resource_types": [],
        "notes": null
      }
    ]
  },

  "known_gaps": [
    {
      "description": null,
      "affected_resources": [],
      "priority": null
    }
  ],

  "preferences": {
    "budget_constraint": null,
    "preferred_dr_strategy": null,
    "template_scope": "all"
  },

  "context": {
    "architecture_description": null,
    "recent_incidents": null,
    "upcoming_changes": null,
    "additional_notes": null
  }
}
```

After writing the file, display:

> **Intake form created: `dr-intake.json`**
> **Context folder created: `dr-context/`**
>
> **Step 1 — Fill in the intake form:**
> Open `dr-intake.json` and replace `null` values with your actual data, then tell me **"intake complete"** when ready.
>
> Key fields to fill:
> - `organization.primary_region` and `organization.recovery_region`
> - `business_requirements.workload_targets` — your RTO/RPO targets per workload
> - `contacts` — DR team members
> - `service_inventory` — set `true` for each service you use
> - `service_inventory.custom_services` — add any services not in the list
>
> **Step 2 — Drop context files (optional but recommended):**
> Place any of the following in the `dr-context/` folder before saying "intake complete":
> - Architecture diagrams (PNG, SVG, DrawIO, PDF)
> - IaC files (CloudFormation YAML, Terraform `.tf`, CDK)
> - Existing runbooks or DR plans (Markdown, PDF, DOCX)
> - Network diagrams
> - Compliance requirement documents
>
> See `dr-context/README.md` for the full list of supported file types.
>
> All fields in `dr-intake.json` are optional — leave as `null` to answer interactively during the assessment.

---

## Reading the Completed Intake Form

When the user indicates the intake form is complete (e.g., "intake complete", "ready to scan", "I've filled in the form"):

1. Read `dr-intake.json` from the working directory.
2. Validate it is valid JSON. If parsing fails, display the error and ask the user to fix it:
   > "Could not parse `dr-intake.json`: `[error message]`. Please fix the JSON and try again."
3. Parse each section:

   **`organization`:**
   - `primary_region` → store in `metadata.primary_region`
   - `recovery_region` → store in `metadata.recovery_region`
   - `additional_scan_regions` → store in `metadata.additional_regions`
   - `aws_account_ids` → store in `metadata.account_ids`

   **`scope`:**
   - `full_account_scan: false` + `workloads_in_scope` → restrict scan to listed workloads only

   **`business_requirements.workload_targets`:**
   - Store in `intake.workload_targets` — Phase 2 uses these to skip Q1/Q2 for pre-set workloads

   **`contacts`:**
   - Store in `intake.contacts` — Phase 3 uses these to populate Roles & Responsibilities and Communication Plan

   **`service_inventory`:**
   - Collect all keys set to `true` across all categories → confirm they are in the built-in scan catalog
   - `custom_services` array (entries where `service_name` is non-null) → add to `metadata.custom_services`

   **`known_gaps`:**
   - Non-null entries → store in `intake.known_gaps`; Phase 2 includes these with `"source": "user_provided"`

   **`preferences`:**
   - `budget_constraint` → inform Phase 2 strategy recommendations
   - `template_scope` → inform Phase 4 scope

   **`context`:**
   - `architecture_description` → use in DR plan Executive Summary

4. Display a parsed summary:
   ```
   Intake Form Parsed
   ==================
   Organization:          Acme Corp
   Primary region:        us-east-1
   Recovery region:       us-west-2
   Additional regions:    eu-west-1
   Workloads with RTO/RPO: 3
   Contacts provided:     5 roles
   Services enabled:      12 built-in
   Custom services:       2 (Amazon Connect, AWS IoT Core)
   Known gaps:            1
   Budget constraint:     moderate
   ```

5. **Scan the `dr-context/` folder:**

   Check if `dr-context/` exists and contains any files (excluding `README.md`).

   If files are present, list them and read each one:
   ```
   Context Folder Scan
   ===================
   Found 4 files in dr-context/:
   - architecture-diagram.png
   - main.tf
   - existing-runbook.md
   - compliance-requirements.pdf
   ```

   For each file, extract relevant information based on file type:

   **Images (`.png`, `.jpg`, `.svg`, `.drawio`):**
   - Read the image and identify: AWS services visible, data flow directions, on-premises components, network boundaries, annotations
   - Note any services or dependencies not captured by the AWS API scan
   - Flag architecture patterns with known DR implications (e.g., single-region databases, no load balancer, direct EC2-to-EC2 connections)

   **IaC files (`.yaml`, `.json`, `.tf`, `.cdk.ts`, `.cdk.py`):**
   - Parse to identify: resource types, regions, existing DR configurations (Multi-AZ, backup policies, replication rules)
   - Cross-reference with `service_inventory` — add any services found in IaC but not checked in the intake form
   - Note resources defined in IaC that may not yet be deployed (gap between IaC and actual state)
   - Flag any DR anti-patterns: `backup_retention_period = 0`, missing replication blocks, single-AZ deployments

   **Markdown/text documents (`.md`, `.txt`):**
   - Extract: service names, RTO/RPO targets mentioned, existing DR procedures, known issues, compliance requirements
   - If it's an existing DR plan: note what's covered and what's missing vs current best practices

   **PDF/DOCX documents:**
   - Extract text and apply the same rules as markdown documents

   **CSV files:**
   - If it's a cost report: extract total monthly spend and top services by cost (informs DR cost estimates)
   - If it's a resource inventory: extract service names and resource counts

   After scanning all files, write extracted context to `dr-state/context.json`:
   ```json
   {
     "files_scanned": [
       {
         "filename": "architecture-diagram.png",
         "type": "image",
         "services_identified": ["EC2", "RDS", "ALB", "S3"],
         "notes": "Three-tier architecture. RDS appears single-AZ. No DR region shown.",
         "flags": ["RDS single-AZ visible in diagram"]
       },
       {
         "filename": "main.tf",
         "type": "iac_terraform",
         "services_identified": ["aws_instance", "aws_db_instance", "aws_s3_bucket"],
         "notes": "RDS backup_retention_period=0. S3 bucket has no replication block.",
         "flags": ["RDS backup retention is 0", "S3 bucket missing replication"]
       }
     ],
     "additional_services_from_context": [],
     "context_flags": [
       "RDS single-AZ visible in architecture diagram",
       "RDS backup retention is 0 in Terraform",
       "S3 bucket missing replication in Terraform"
     ],
     "architecture_summary": "Three-tier web application with EC2, RDS MySQL, and S3. No DR region configured in IaC."
   }
   ```

   Display a context summary:
   ```
   Context Folder Summary
   ======================
   Files read:     4
   Services found: EC2, RDS, ALB, S3, Lambda (from IaC)
   Context flags:  3 (potential gaps identified from documents)
   Architecture:   Three-tier web app, single-region, no DR config in IaC
   ```

   > Context flags from `dr-context/` are pre-populated into the gap analysis in Phase 2, marked with `"source": "context_document"` so users know they came from uploaded files rather than the live AWS scan.

   If `dr-context/` is empty or does not exist, skip silently and write `{ "files_scanned": [], "context_flags": [] }` to `dr-state/context.json`.

6. If `organization.primary_region` is null, ask before proceeding:
   > "Your intake form doesn't specify a primary region. Which AWS region should I use as the primary?"

7. Write parsed intake data to `dr-state/intake.json`:
   ```json
   {
     "status": "completed",
     "source_file": "dr-intake.json",
     "workload_targets": {},
     "contacts": {},
     "known_gaps": [],
     "preferences": {},
     "architecture_notes": null,
     "custom_services_from_bom": []
   }
   ```

8. Update `dr-state/core.json`:
   - Set `phases.intake.status = "completed"`
   - Append `"intake"` to `metadata.completed_phases`
   - Write `core.json` **after** both `intake.json` and `context.json` are fully written.

> **Next step:** Run Phase 1 (Scan) to discover AWS resources.

---

## Skipped Intake

If the user skips intake:
1. Create `dr-state/` directory if it does not exist.
2. Create `dr-context/` directory if it does not exist, and write `dr-context/README.md` (same content as above).
3. Write `dr-state/core.json` with `phases.intake.status = "skipped"` and append `"intake"` to `metadata.completed_phases`.
4. Write `dr-state/context.json` with `{ "files_scanned": [], "context_flags": [] }` as a placeholder.
5. Proceed directly to Phase 1.

> **Note:** Even when intake is skipped, the user can still add files to `dr-context/` before Phase 2 (Analyze). The agent will read them at the start of Phase 2 if `context.json` is empty or missing.
