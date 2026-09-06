# Secrets & Credentials Scan - Detailed Steps

## Purpose
Detect exposed secrets, hardcoded credentials, and poor secrets management practices.

CRITICAL: NEVER output actual secret values in reports. Reference by key name only.

---

## Step 1: Pattern-Based Secret Detection

Scan for known secret patterns:

### AWS Credentials
```bash
grep -rn "AKIA[0-9A-Z]{16}" . --include="*.{ts,js,py,java,go,yml,yaml,json,env,cfg,conf,properties}"
grep -rn "aws_secret_access_key\|aws_access_key_id" . --include="*"
```

### API Keys & Tokens
```bash
# Generic patterns
grep -rn "api[_-]key\|apikey\|api_secret\|secret_key\|private_key\|auth_token\|access_token" \
  --include="*.{ts,js,py,java,go,yml,yaml,json,env,cfg,properties}" -i .

# Specific services
grep -rn "sk_live_\|pk_live_\|sk_test_" .  # Stripe
grep -rn "ghp_\|gho_\|github_pat_" .  # GitHub
grep -rn "xoxb-\|xoxp-\|xoxa-" .  # Slack
grep -rn "SG\.[a-zA-Z0-9._-]" .  # SendGrid
grep -rn "AIza[0-9A-Za-z_-]{35}" .  # Google API
```

### Database Credentials
```bash
# Connection strings with passwords
grep -rn "postgres://\|mysql://\|mongodb://\|redis://" . | grep -v "localhost\|127.0.0.1\|example"
grep -rn "password\s*[:=]\s*[\"'][^\"']*[\"']" --include="*.{ts,js,py,java,yml,yaml,json,cfg}" .
```

### Private Keys
```bash
find . -name "*.pem" -o -name "*.key" -o -name "*.p12" -o -name "id_rsa" -o -name "*.pfx" 2>/dev/null
grep -rn "BEGIN.*PRIVATE KEY" .
grep -rn "BEGIN RSA PRIVATE KEY\|BEGIN EC PRIVATE KEY\|BEGIN OPENSSH PRIVATE KEY" .
```

### JWT Secrets
```bash
grep -rn "jwt_secret\|JWT_SECRET\|jwtSecret\|token_secret" . --include="*"
```

---

## Step 2: Check .gitignore Coverage

```bash
# Verify sensitive patterns are in .gitignore
cat .gitignore 2>/dev/null | grep -E "\.env|\.pem|\.key|\.p12|credentials|secrets"

# Check if .env files are committed
git ls-files | grep -E "\.env$|\.env\." | grep -v "\.example\|\.sample\|\.template"
```

---

## Step 3: Check Git History

```bash
# Check if secrets were EVER committed (even if removed)
git log --all --full-history -p -- "*.env" "*.pem" "*.key" 2>/dev/null | head -50
git log --all --diff-filter=D -- "*.env" 2>/dev/null  # Deleted .env files (still in history)
```

---

## Step 4: Secrets Management Assessment

| Practice | Check | Status |
|----------|-------|--------|
| .gitignore covers .env | Pattern present | {✅/❌} |
| .env.example exists | Template for required env vars | {✅/❌} |
| No secrets in source | Zero hardcoded credentials found | {✅/❌} |
| Secrets manager integration | AWS SM, Vault, etc. | {✅/❌/N/A} |
| Rotation policy | Evidence of key rotation | {✅/❌/Unknown} |
| Least privilege | Scoped tokens, not admin keys | {Check manually} |

---

## Step 5: Generate Report

CRITICAL: Mask all actual values. Report structure/location only.

```markdown
## 🔐 Secrets & Credentials Report

### Summary
| Severity | Count | Status |
|----------|-------|--------|
| 🔴 Exposed production secrets | {n} | IMMEDIATE ACTION |
| 🟠 Hardcoded credentials (non-prod) | {n} | Migrate to env vars |
| 🟡 Missing .gitignore patterns | {n} | Add patterns |
| 🔵 Best practice suggestions | {n} | Improve posture |

### 🔴 Critical: Exposed Secrets
| # | Type | File | Line | Risk | Action |
|---|------|------|------|------|--------|
| 1 | AWS Access Key | {file} | {line} | Account compromise | Rotate + move to env |
| 2 | DB Password | {file} | {line} | Data breach | Rotate + use Secrets Manager |

⚠️ If secrets are in git history, they MUST be rotated even if removed from HEAD.

### Remediation Steps
1. IMMEDIATELY rotate all exposed credentials
2. Add patterns to .gitignore
3. Remove from git history: `git filter-branch` or BFG Repo-Cleaner
4. Migrate to secrets management solution
5. Set up pre-commit hooks (e.g., detect-secrets, gitleaks)

### Recommended Tools
| Tool | Purpose | Integration |
|------|---------|-------------|
| gitleaks | Pre-commit secret detection | Git hooks, CI |
| detect-secrets | Yelp's secret scanner | Pre-commit hook |
| AWS Secrets Manager | Runtime secret storage | SDK integration |
| HashiCorp Vault | Enterprise secret management | API/SDK |
| git-crypt | Encrypt files in repo | Git filter |
```

---

## Step 6: Approval Gate

MANDATORY: This is a security approval gate.

Present findings (without revealing actual values) and wait for user acknowledgment
before proceeding. If critical secrets are found, recommend immediate rotation.
