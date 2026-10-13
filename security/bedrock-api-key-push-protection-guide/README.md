# Amazon Bedrock Long-term API Key Push Protection Guide

> **Purpose**: Describes how to prevent Amazon Bedrock Long-term API Keys (starting with `ABSK...`) from being accidentally pushed to GitHub repositories, organized by GitHub subscription plan.
>
> **Audience**: AWS customers (development teams using GitHub)  
> **Created**: 2026-06-08
>
> ⚠️ **Important**: Before applying the settings in this guide to production environments, thoroughly validate them in a test environment first. In particular, it is recommended to verify false positives through Dry Run before enabling Custom Patterns and Push Protection.

---

## 1. Background

### 1.1 What is an Amazon Bedrock API Key?

In July 2025, AWS launched Amazon Bedrock API Keys. Instead of traditional IAM Access Key/Secret Key pairs, these are dedicated keys that allow calling Bedrock via a **Bearer Token** approach.

| Type | Prefix | Validity | Purpose |
|------|--------|----------|---------|
| **Long-term** | `ABSK...` | Until configured expiration (up to 100 years) | Exploration/Development |

### 1.2 The Problem

As of June 2026, GitHub's [Supported Secret Scanning Patterns](https://docs.github.com/en/code-security/reference/secret-security/supported-secret-scanning-patterns) **do not include the Bedrock API Key pattern**.

Therefore, even if a Bedrock API Key is hardcoded and pushed to a repository, GitHub cannot automatically detect it.

### 1.3 Regex Pattern for Detection

```regex
# Long-term Bedrock API Key
ABSK[A-Za-z0-9+/=]+
```

---

## 2. Implementation Summary by Plan

| GitHub Plan | Recommended Method | Push Block Location | Cost |
|-------------|-------------------|--------------------| -----|
| **Free** | Pre-commit Hook (`git-secrets`) | Developer local | Free |
| **Team** | GitHub Secret Protection + Custom Pattern + Push Protection | GitHub server | Active committer-based billing |
| **Enterprise Cloud** | Org/Enterprise-level Custom Pattern | GitHub server | Active committer-based billing (no additional cost if GHAS included) |

---

## 3. GitHub Free Plan — Pre-commit Hook Method

> **Key Point**: Server-side blocking not available → Block at commit time on developer's local machine

### 3.1 Install AWS git-secrets

#### macOS

```bash
brew install git-secrets
```

#### Linux (Ubuntu/Debian)

```bash
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets
sudo make install
```

#### Windows

```powershell
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets
./install.ps1
```

#### Verify Installation

```bash
git secrets --version
# git-secrets 1.3.0
```

---

### 3.2 Apply git-secrets to a Repository

#### Step 1: Navigate to Repository and Initialize

```bash
cd /path/to/your-repo
git secrets --install
```

**Output:**
```
✓ Installed commit-msg hook to .git/hooks/commit-msg
✓ Installed pre-commit hook to .git/hooks/pre-commit
✓ Installed prepare-commit-msg hook to .git/hooks/prepare-commit-msg
```

#### Step 2: Register Bedrock API Key Pattern

```bash
# Long-term Bedrock API Key pattern
git secrets --add 'ABSK[A-Za-z0-9+/=]+'
```

**Verify registration:**
```bash
git secrets --list
```

**Example output:**
```
secrets.patterns ABSK[A-Za-z0-9+/=]+
```

---

#### Step 3: Test

```bash
# Create test file (fake key)
echo 'API_KEY="ABSKQmVkcm9ja0FQSUtleS1hYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODk="' > test_secret.txt

# Stage
git add test_secret.txt

# Attempt commit
git commit -m "test commit"
```

**Blocked result:**
```
test_secret.txt:1:API_KEY="ABSKQmVkcm9ja0FQSUtleS1hYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODk="

[ERROR] Matched one or more prohibited patterns

Possible mitigations:
- Mark false positives as allowed using: git config --add secrets.allowed ...
- Mark the commit as allowed using: git config --add secrets.allowed ...
- List your configured patterns: git secrets --list
- Undo the last commit: git reset HEAD~1

ERROR: commit is not allowed
```

> ✅ Commit was blocked!

---

#### Step 4: Clean Up Test File

```bash
git reset HEAD test_secret.txt
rm test_secret.txt
```

---

### 3.3 Global Configuration (Auto-apply to All Repos)

Configure git-secrets to be automatically applied whenever a developer clones a new repo.

```bash
# 1. Register global pattern
git secrets --add --global 'ABSK[A-Za-z0-9+/=]+'

# 2. Install hooks to Git template directory
git secrets --install ~/.git-templates/git-secrets

# 3. Set global template
git config --global init.templateDir ~/.git-templates/git-secrets
```

> 📌 Now all repos created via `git clone` or `git init` will automatically have git-secrets hooks installed.

---

### 3.4 Scan Existing Repos

To find Bedrock API Keys in existing code:

```bash
# Scan current code
git secrets --scan

# Scan entire Git history
git secrets --scan-history
```

---

### 3.5 GitHub Actions Secondary Alert (Optional)

Set up a GitHub Actions workflow as a secondary check in case pre-commit hooks are bypassed.

#### Create `.github/workflows/bedrock-key-scan.yml`

```yaml
name: Bedrock API Key Scan

on:
  push:
    branches: ['**']
  pull_request:
    branches: ['**']

jobs:
  scan-secrets:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Scan for Bedrock API Keys
        run: |
          echo "Scanning for Amazon Bedrock API Keys..."
          
          FOUND=0
          
          # Long-term key pattern (ABSK...)
          if grep -rP 'ABSK[A-Za-z0-9+/=]+' \
            --include='*.py' --include='*.js' --include='*.ts' \
            --include='*.java' --include='*.yml' --include='*.yaml' \
            --include='*.json' --include='*.env' --include='*.tf' \
            --include='*.sh' --include='*.go' --include='*.rb' \
            . 2>/dev/null; then
            echo "::error::Amazon Bedrock Long-term API Key detected!"
            FOUND=1
          fi
          
          if [ $FOUND -eq 1 ]; then
            echo ""
            echo "Bedrock API Key found in code."
            echo "Immediately deactivate the key and issue a new one."
            echo "Reference: https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html"
            exit 1
          fi
          
          echo "No Bedrock API Keys found."
```

> ⚠️ **Note**: GitHub Actions runs *after* the push. Since the key is already recorded in Git history, you must immediately **deactivate** the key upon detection.

---

## 4. GitHub Team Plan — GitHub Secret Protection + Push Protection

> **Key Point**: Block push at the GitHub server (no local developer configuration required)

### 4.1 Prerequisites

- GitHub Team plan or higher
- **GitHub Secret Protection** license activated

### 4.2 Licensing and Billing Structure

GitHub Advanced Security consists of 2 independent SKUs:

| SKU | Included Features | Purpose |
|-----|------------------|---------|
| **GitHub Secret Protection** | Secret scanning, Push protection, Custom patterns | Secret leak detection/prevention |
| **GitHub Code Security** | Code scanning, Dependabot premium, Dependency review | Vulnerability detection/remediation |

Only **Secret Protection is required** to block Bedrock API Key pushes (Code Security is separate).

**Billing model:**
- **Active committer-based**: Measured by unique users who committed to repos with Secret Protection enabled within the last 90 days
- A single user committing to multiple repos consumes only **1 license**
- License is automatically released after 90 days of no commits
- Secret scanning and Push protection are available **for free** on Public repos (paid license required only for Private repos)
- For exact pricing, see [GitHub Advanced Security pricing](https://github.com/enterprise/advanced-security#pricing)

---

### 4.3 Step 1: Enable GitHub Secret Protection

On GitHub Team plan, activate Secret Protection through the Organization's **Security configurations**.

**Path:** Organization → Settings → (Security section) Advanced Security → Configurations

**Steps:**

1. Navigate to the Organization page on GitHub
2. Click the **Settings** tab
3. Left sidebar → Security section → Click **Advanced Security** dropdown
4. Select **Configurations**
5. Edit an existing Configuration or create a **New configuration**
6. Set "Secret Protection" to **Enabled**
7. Select target repositories and apply the Configuration

---

### 4.4 Step 2: Register Custom Pattern

**Path:** Organization → Settings → (Security section) Advanced Security → Global settings → Custom patterns → New pattern

**Steps:**

1. Left sidebar → Security section → Click **Advanced Security** dropdown
2. Select **Global settings**
3. Under "Custom patterns", click **New pattern**
4. Enter the following values:

| Field | Value |
|-------|-------|
| Pattern name | `Amazon Bedrock Long-term API Key` |
| Secret format | `ABSK[A-Za-z0-9+/=]+` |
| Test string | `ABSKQmVkcm9ja0FQSUtleS1hYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODk=` |

5. Verify "1 match found" is displayed after entering the test string
6. Click **Save and dry run** (recommended: verify false positives via Dry Run)
   - Dry Run can be performed across the entire Organization or on selected repositories (up to 10)
7. After reviewing Dry Run results → Click **Publish pattern**

---

### 4.5 Step 3: Enable Push Protection

**Path:** Organization → Settings → Advanced Security → Global settings → Custom patterns → [Select pattern] → Pattern Configurations → Custom tab

**Steps:**

1. Click the Custom Pattern created in 4.4 (`Amazon Bedrock Long-term API Key`)
2. Click the **Pattern Configurations** link at the bottom
3. Click the **Custom** tab
4. Select **Enabled** in the Organization setting column

> **Note**: The push protection option is only visible after the pattern is published. Additionally, Secret scanning as push protection must be enabled on the target repositories.

---

### 4.6 Step 4: Verify Push Blocking

When a developer pushes code containing an ABSK key:

```bash
$ git push origin main

remote: error: GH013: Push cannot contain secrets
remote: 
remote: ─── Amazon Bedrock Long-term API Key ──────────────
remote:  locations:
remote:    - commit: a1b2c3d
remote:      path: config/settings.py:15
remote: 
remote: (?) To push, remove the secret from your commits.
remote:     https://docs.github.com/code-security/secret-scanning
remote: 
To https://github.com/your-org/your-repo.git
 ! [remote rejected] main -> main (push rule violation)
error: failed to push some refs
```

> Push is blocked at the server!

---

## 5. GitHub Enterprise Cloud — Org/Enterprise-wide Application

> **Key Point**: Single configuration applies to all repositories across all Organizations

### 5.1 Enterprise-level Configuration

**Path:** Enterprise → Settings → Advanced Security

**Steps:**

1. Navigate to `https://github.com/enterprises/YOUR-ENTERPRISE/settings/security_analysis`
2. Click **Advanced Security** in the left menu
3. In the **Configurations** section, create or edit a Security Configuration
   - Set Secret Protection to **Enabled**
   - Click **Update configuration**
   - Optionally set to **Enforced** to prevent Organizations from disabling it
4. Click **Apply to** to apply to target repositories

---

### 5.2 Custom Pattern Registration and Push Protection Activation

Follow the same process as the Team plan. Refer to Sections 4.4 and 4.5.

> Patterns configured at the Enterprise level are automatically applied to all repositories across all Organizations.

> **Note**: Starting July 2026, Enterprise Cloud customers can use [Public Monitoring](https://github.blog/changelog/2026-07-01-secret-scanning-public-monitoring-for-enterprises/) to detect secret leaks in public areas outside their owned repositories (personal forks, external OSS projects, public issues/PRs, etc.). However, whether custom patterns are covered by Public Monitoring requires separate verification.

---

## 6. Dry Run and Bypass Management (Team/Enterprise Common)

### 6.1 Dry Run (False Positive Verification)

Before applying a pattern, use **Dry Run** to check how many matches exist in existing repositories.

- Dry Run scans existing repos and displays match counts
- Verify whether false positives exist
- Any existing exposed keys must be immediately **deactivated** and new keys issued

### 6.2 Bypass Management

When push protection needs to be bypassed in certain situations, configure Bypass privileges in the Security Configuration:

- **Bypass allowed for**: Specific actors (specific roles/teams)
- **Exempt option**: Fully exempt trusted automation from push protection (use with caution)
- Available reasons: "It's used in tests" / "I'll fix it later" / "It's a false positive"

> All bypasses are recorded in the **Audit Log** and can be tracked by security teams.

---

## 7. Supporting Tool: .gitignore Configuration

Basic protection settings that should be applied across all plans.

### `.gitignore` Example

```gitignore
# ===== Environment & Secret Files =====
.env
.env.*
!.env.example

# AWS credentials
.aws/credentials
**/credentials

# Bedrock config files
bedrock-config.yaml
bedrock-config.json

# ===== IDE/Editor =====
.idea/
.vscode/settings.json
*.swp

# ===== OS =====
.DS_Store
Thumbs.db
```

### `.env.example` Template

```bash
# Amazon Bedrock Configuration
# ⚠️ DO NOT commit actual keys! Use environment variables.
AWS_BEARER_TOKEN_BEDROCK=your-bedrock-api-key-here
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6
```

---

## 8. Key Exposure Response Procedure

If a Bedrock API Key is exposed:

### 8.1 Immediate Actions (Within 5 Minutes)

1. **Deactivate the key**
   - AWS Console → IAM → Security credentials → API keys for Amazon Bedrock → **Deactivate**
   - Or via CLI:
   ```bash
   aws iam update-service-specific-credential \
     --service-specific-credential-id <ID> \
     --status Inactive
   ```

2. **Check CloudTrail**
   - Filter with `"additionalEventData.callWithBearerToken": true` to identify unauthorized calls

3. **Issue and deploy a new key**

4. **Clean Git history**
   ```bash
   # Use BFG Repo-Cleaner (recommended)
   bfg --replace-text passwords.txt your-repo.git
   
   # passwords.txt contents:
   # ABSKQmVkcm9ja0FQSUtleS1... ==> [REMOVED]
   ```

---

## 9. Recommended Architecture (Defense in Depth)

The safest configuration is **multi-layered defense**:

| Layer | Location | Tool | Role |
|-------|----------|------|------|
| **1** | Developer local | git-secrets pre-commit hook | Block at commit stage |
| **2** | GitHub server | Custom Pattern + Push Protection | Server-side block at push stage (Team/Enterprise) |
| **3** | CI/CD | GitHub Actions Secret Scan Workflow | Detection and alerting after push |
| **4** | AWS monitoring | CloudTrail + GuardDuty | Real-time detection of abnormal API Key usage |

---

## 10. Team Deployment Checklist

### Free Plan Teams

- [ ] Share git-secrets installation guide on team wiki
- [ ] Distribute global Git template setup script
- [ ] Add GitHub Actions workflow file to all repos
- [ ] Apply standard .gitignore template
- [ ] Perform quarterly `git secrets --scan-history`

### Team/Enterprise Plan Teams

- [ ] Enable GitHub Secret Protection
- [ ] Register Custom Pattern (Long-term)
- [ ] Verify existing exposed keys via Dry Run
- [ ] Enable Push Protection
- [ ] Establish Bypass policy (who can bypass, with what reason?)
- [ ] Deactivate existing exposed keys and issue new ones
- [ ] Recommend git-secrets as an additional layer

---

## 11. References

| Resource | Link |
|----------|------|
| GitHub Custom Pattern Definition | [docs.github.com](https://docs.github.com/en/code-security/secret-scanning/using-advanced-secret-scanning-and-push-protection-features/custom-patterns/defining-custom-patterns-for-secret-scanning) |
| GitHub Secret Protection Pricing | [resources.github.com](https://resources.github.com/evolving-github-advanced-security/) |
| AWS git-secrets | [github.com/awslabs/git-secrets](https://github.com/awslabs/git-secrets) |
| Amazon Bedrock API Keys Documentation | [docs.aws.amazon.com](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) |
| Bedrock API Key Security Best Practices | [aws.amazon.com/blogs](https://aws.amazon.com/blogs/security/securing-amazon-bedrock-api-keys-best-practices-for-implementation-and-management/) |
| Bedrock API Key Security Analysis (3rd party) | [medium.com/@adan.alvarez](https://medium.com/@adan.alvarez/api-keys-for-bedrock-a-brief-security-overview-2133ed9a2b3f) |
| GitHub Supported Patterns | [docs.github.com](https://docs.github.com/en/code-security/reference/secret-security/supported-secret-scanning-patterns) |

---

*Created: 2026-06-08*  
*Last updated: 2026-06-19*  
*Audience: AWS customers (development teams using GitHub)*  
*Version: 2.0 (GitHub UI path updates, billing structure updates, regex pattern revision)*
