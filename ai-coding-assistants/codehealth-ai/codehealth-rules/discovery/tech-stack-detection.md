# Tech Stack Detection - Detailed Steps

## Purpose
Identify all technologies used in the repository before running dependency analysis.
This ensures the correct analysis tools and patterns are applied.

---

## Step 1: Scan Root Directory

List the root directory and identify primary indicators:

```bash
# List all files at root (no recursion)
ls -la {REPO_ROOT}

# Check for key manifest files
for f in package.json tsconfig.json pom.xml build.gradle build.gradle.kts \
         requirements.txt Pipfile pyproject.toml setup.py go.mod Cargo.toml \
         composer.json Gemfile pubspec.yaml mix.exs *.csproj *.sln \
         Package.swift Podfile stack.yaml; do
  test -f "$f" && echo "FOUND: $f"
done
```

---

## Step 2: Scan Subdirectories for Secondary Indicators

```bash
# Infrastructure and tooling
find . -maxdepth 2 -name "Dockerfile" -o -name "docker-compose*.yml" \
  -o -name "*.tf" -o -name "cdk.json" -o -name "serverless.yml" \
  -o -name "template.yaml" -o -name "Pulumi.yaml" 2>/dev/null

# CI/CD
find . -maxdepth 2 -path "./.github/workflows" -o -name ".gitlab-ci.yml" \
  -o -name "Jenkinsfile" -o -name "buildspec.yml" 2>/dev/null

# Monorepo packages (for workspace detection)
find . -maxdepth 3 -name "package.json" -not -path "*/node_modules/*" 2>/dev/null
find . -maxdepth 3 -name "pom.xml" 2>/dev/null
```

---

## Step 3: Content-Based Framework Detection

For each detected language, parse manifest contents:

### Node.js (package.json)
```bash
# Extract dependency names for framework detection
cat package.json | grep -E '"(react|next|vue|nuxt|angular|svelte|express|fastify|@nestjs|hono|remix)"'

# Check for TypeScript
test -f tsconfig.json && echo "TypeScript: YES"

# Detect bundler
for f in webpack.config.* vite.config.* rollup.config.* esbuild.* turbopack.*; do
  test -f "$f" && echo "Bundler: $f"
done
```

### Python (requirements.txt / pyproject.toml)
```bash
# Check for framework imports
grep -iE "^(django|flask|fastapi|starlette|tornado|pyramid|bottle)" requirements.txt 2>/dev/null
grep -iE "^(django|flask|fastapi|starlette|tornado|pyramid|bottle)" */requirements.txt 2>/dev/null

# Check pyproject.toml for project metadata
grep -A5 "\[tool.poetry.dependencies\]" pyproject.toml 2>/dev/null
grep -A5 "\[project.dependencies\]" pyproject.toml 2>/dev/null
```

### Java (pom.xml / build.gradle)
```bash
# Spring Boot detection
grep -l "spring-boot" pom.xml build.gradle build.gradle.kts 2>/dev/null

# Quarkus detection
grep -l "quarkus" pom.xml build.gradle build.gradle.kts 2>/dev/null

# Java version
grep -E "<java.version>|sourceCompatibility" pom.xml build.gradle 2>/dev/null
```

---

## Step 4: Calculate Confidence Scores

Apply the scoring algorithm from `common/tech-stack-signatures.md`:

For each detected technology:
- Primary signal found: +30 confidence
- Secondary signal found: +15 confidence
- Tertiary signal found: +5 confidence

Threshold for automatic progression: >= 60 (HIGH confidence)

---

## Step 5: Generate Tech Stack Report

```markdown
## 🔍 Tech Stack Detection Report

### Primary Languages
| # | Language | Confidence | Evidence |
|---|----------|-----------|----------|
| 1 | {lang} | {HIGH/MED/LOW} | {files found} |

### Frameworks & Libraries
| # | Framework | Category | Evidence |
|---|-----------|----------|----------|
| 1 | {framework} | {frontend/backend/fullstack} | {manifest entry} |

### Package Managers
| # | Manager | Lock File Present | Manifest |
|---|---------|------------------|----------|
| 1 | {npm/yarn/pnpm/pip/maven/etc} | {yes/no} | {file} |

### Infrastructure & Tooling
| # | Tool | Category | Evidence |
|---|------|----------|----------|
| 1 | {tool} | {container/iac/ci-cd/orchestration} | {file} |

### Project Structure
- **Type**: {single-package | monorepo | multi-module}
- **Monorepo Tool**: {if applicable}
- **Workspace Count**: {if monorepo}
```

---

## Step 6: Present for User Confirmation

MANDATORY: Present the detection results and ask for confirmation.

```
📋 Tech Stack Detection Complete

Detected:
  Language:     {language} ({confidence})
  Framework:    {framework}
  Pkg Manager:  {package_manager}
  Infrastructure: {infra_tools}
  Project Type: {single/monorepo}

Is this correct? [Y/n]
If corrections needed, please specify what should be different.
```

Wait for user response before proceeding.

---

## Step 7: Handle Corrections

If user provides corrections:
1. Log the correction in audit.md
2. Update the tech stack report
3. Re-confirm with user
4. Proceed only when confirmed

---

## Edge Cases

| Scenario | Handling |
|----------|----------|
| No manifest files found | Ask user to specify tech stack manually |
| Multiple conflicting signals | Present all options, ask user to disambiguate |
| Unknown/exotic tech stack | Report what was found, ask for guidance |
| Polyglot project | Report ALL detected stacks, analyze each independently |
| Generated code (no manifest) | Check for Makefile, CMakeLists.txt, or custom build scripts |
