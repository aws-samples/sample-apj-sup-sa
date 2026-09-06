# Infrastructure Detection - Detailed Steps

## Purpose
Identify all infrastructure-as-code, container, and CI/CD configuration in the project.

---

## Step 1: Detect IaC Frameworks

```bash
# Terraform
find . -name "*.tf" | head -10
test -d .terraform && echo "Terraform: initialized"

# AWS CDK
test -f cdk.json && echo "AWS CDK detected"
grep -l "aws-cdk-lib\|@aws-cdk" package.json tsconfig.json 2>/dev/null

# CloudFormation / SAM
find . -name "template.yaml" -o -name "template.json" | head -5
grep -l "AWSTemplateFormatVersion\|Transform.*Serverless" template.yaml 2>/dev/null

# Pulumi
test -f Pulumi.yaml && echo "Pulumi detected"

# Serverless Framework
test -f serverless.yml && echo "Serverless Framework detected"
test -f serverless.ts && echo "Serverless Framework (TS) detected"
```

---

## Step 2: Detect Container Configuration

```bash
# Dockerfile
find . -name "Dockerfile*" | head -10
find . -name "docker-compose*.yml" -o -name "docker-compose*.yaml" | head -5
find . -name ".dockerignore" | head -3

# Kubernetes
find . -path "*/k8s/*" -o -path "*/kubernetes/*" -o -name "*.k8s.yml" | head -10
find . -name "Chart.yaml" | head -5  # Helm
find . -name "kustomization.yaml" | head -5  # Kustomize
```

---

## Step 3: Detect CI/CD

```bash
# GitHub Actions
test -d .github/workflows && echo "GitHub Actions" && ls .github/workflows/

# GitLab CI
test -f .gitlab-ci.yml && echo "GitLab CI"

# Jenkins
test -f Jenkinsfile && echo "Jenkins"

# CircleCI
test -d .circleci && echo "CircleCI"

# AWS CodeBuild/CodePipeline
find . -name "buildspec.yml" -o -name "buildspec.yaml" | head -3

# Azure Pipelines
test -f azure-pipelines.yml && echo "Azure Pipelines"
```

---

## Step 4: Detect Configuration Files

```bash
# Environment files
find . -name ".env*" -not -path "*/node_modules/*" | head -10
find . -name "*.properties" -o -name "appsettings*.json" -o -name "application*.yml" | \
  grep -v node_modules | head -10
```

---

## Step 5: Classification

```markdown
## Infrastructure Detection Results

| Category | Tool | Files Found | Confidence |
|----------|------|-------------|-----------|
| IaC | {Terraform/CDK/CF/Pulumi/None} | {n} files | {HIGH/MED} |
| Containers | {Docker/Docker Compose/None} | {n} files | {HIGH/MED} |
| Orchestration | {K8s/Helm/ECS/None} | {n} files | {HIGH/MED} |
| CI/CD | {GH Actions/GitLab/Jenkins/None} | {n} workflows | {HIGH/MED} |
| Config | {.env + n others} | {n} files | {HIGH} |
```

---

## Step 6: Decision

If any infrastructure detected → Phase 6 (Infrastructure & Configuration) will execute.
If nothing detected → Phase 6 will be skipped (except Secrets Scan which ALWAYS runs).
