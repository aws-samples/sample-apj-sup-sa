# Workshop Studio Deployment

Deploy the Agentic Analytics infrastructure to AWS Workshop Studio for event delivery.

## Prerequisites

- [git-remote-workshopstudio plugin](https://catalog.workshops.aws/docs/en-US/create-a-workshop/authoring-a-workshop/connecting-to-the-repository) installed:
  ```bash
  pipx install --index-url https://plugin.us-east-1.prod.workshops.aws git-remote-workshopstudio==0.2.0 --pip-args="--extra-index-url https://pypi.org/simple"
  ```
- AWS CLI configured
- Workshop created in [Workshop Studio](https://catalog.workshops.aws)

## Setup (one-time)

1. Clone the Workshop Studio repo outside this project:
   ```bash
   cd ..
   git clone workshopstudio://ws-content-<your-workshop-id>/<your-repo-name> ws
   ```

2. Get credentials from Workshop Studio (workshop details → Repository credentials)

3. Export the credentials in your terminal

## Deploy

From this project root:

```bash
cd infrastructure/scripts
./deploy_to_workshop.sh
```

The script will:
1. Prompt for WS repo path and S3 assets location (saved for future runs)
2. Package all artifacts (templates, Lambdas, data)
3. Copy `contentspec.yaml` and `static/main-stack.yaml` to the WS repo
4. Sync assets to S3
5. Commit and push the WS repo (triggers a Workshop Studio build)

Use `--skip-push` to package and sync without pushing.

## What gets deployed

Workshop Studio deploys `main-stack.yaml` in **workshop mode**, which creates:
- VPC + Aurora PostgreSQL Serverless v2
- Database schema, sample data, and views
- Cognito User Pool with test users
- AWS Glue Data Catalog
- Bedrock Knowledge Base
- EC2 VS Code instance (via CloudFront)

Attendees then manually deploy AgentCore components from the VS Code terminal.

## File layout

```
workshop/
├── contentspec.yaml    # Workshop Studio config (committed)
├── static/             # Root CFN template (committed)
│   └── main-stack.yaml
├── assets/             # S3 assets (gitignored, synced via AWS CLI)
│   ├── templates/      # Child CFN templates
│   ├── lambdas/        # Lambda deployment packages
│   ├── lambda/         # DataFoundation Lambda
│   ├── schema/         # SQL schema
│   ├── data/           # CSV data files
│   ├── docs/           # Business context
│   ├── drivers/        # JDBC driver
│   ├── sops/           # Agent SOP files
│   └── repo/           # Project repo ZIP
└── .deploy-config      # Local deploy settings (gitignored)
```
