---
name: workshop-deployment
description: Package code and assets, sync to S3, and push content to AWS Workshop Studio. Use when deploying workshop changes or when asked to update the workshop.
metadata:
  author: agentic-analytics-team
  version: "1.0"
---

# Workshop Deployment

## Prerequisites

### Install git-remote-workshopstudio plugin (one-time)
```bash
pipx install --index-url https://plugin.us-east-1.prod.workshops.aws git-remote-workshopstudio==0.2.0 --pip-args="--extra-index-url https://pypi.org/simple"
```

### Clone WS repo (one-time, one level up from GitLab repo)
```bash
cd /Users/diponego/Projects/agentic-analytics
git clone workshopstudio://ws-content-173b87a9-7267-4478-b7ec-0cd16a7cb520/run-llama-on-aws ws2
```

## Steps

### 1. Get Workshop Studio Credentials
Go to Workshop Studio → your workshop → **Repository credentials** → **Assets access instructions**. Save to `/tmp/ws-creds.sh`:
```bash
cat > /tmp/ws-creds.sh << 'EOF'
export WS_REPO_SOURCE="s3"
export AWS_DEFAULT_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."
EOF
```
Credentials expire — refresh if you get auth errors.

### 2. Package
```bash
cd infrastructure/scripts
bash package_for_workshop.sh
```
Creates `workshop/assets/` (Lambda ZIPs, templates, data, repo ZIP with overlay) and `workshop/static/main-stack.yaml`.

### 3. Sync assets to S3
```bash
source /tmp/ws-creds.sh
cd ../../workshop
aws s3 sync ./assets s3://ws-assets-us-east-1/173b87a9-7267-4478-b7ec-0cd16a7cb520 --delete
```

### 4. Check for others' pushes
```bash
source /tmp/ws-creds.sh
cd ../../ws2
git fetch origin
git log --oneline origin/mainline -5
```
If someone pushed after your last sync, pull first or merge carefully.

### 5. Push content to WS repo
```bash
source /tmp/ws-creds.sh
cd ../../ws2
cp ../gitlab/agentic-analytics/workshop/contentspec.yaml .
cp ../gitlab/agentic-analytics/workshop/static/main-stack.yaml static/
rm -rf content && cp -r ../gitlab/agentic-analytics/workshop/content .
cp -r ../gitlab/agentic-analytics/workshop/static/images static/ 2>/dev/null
git add -A
git commit -m "Update workshop"
git push
```
Use `--force` only if you're sure your version supersedes the remote.

## Important Notes

- S3 assets only affect NEW deployments. Existing sandboxes keep old code until participants `git pull` from GitLab.
- The repo ZIP includes the workshop overlay (`workshop/code/` applied on top).
- `contentspec.yaml` defines CFN deployment with `DeployMode=workshop` and magic variables for S3 bucket/prefix.
- `main-stack.yaml` goes to `workshop/static/` (WS repo). Child templates go to S3 `templates/`.
