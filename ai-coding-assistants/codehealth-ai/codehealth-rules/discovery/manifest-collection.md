# Dependency Manifest Collection - Detailed Steps

## Purpose
Collect all dependency declaration files based on the confirmed tech stack.

---

## Step 1: Map Tech Stack to Manifest Files

Based on confirmed tech stack, determine which files to collect:

| Tech Stack | Primary Manifest | Lock File | Supplementary |
|-----------|-----------------|-----------|---------------|
| Node.js (npm) | `package.json` | `package-lock.json` | `.npmrc`, `.nvmrc` |
| Node.js (yarn) | `package.json` | `yarn.lock` | `.yarnrc.yml` |
| Node.js (pnpm) | `package.json` | `pnpm-lock.yaml` | `.npmrc`, `pnpm-workspace.yaml` |
| Python (pip) | `requirements.txt` | — | `constraints.txt`, `requirements-dev.txt` |
| Python (pipenv) | `Pipfile` | `Pipfile.lock` | — |
| Python (poetry) | `pyproject.toml` | `poetry.lock` | — |
| Python (uv) | `pyproject.toml` | `uv.lock` | — |
| Java (Maven) | `pom.xml` | — | `settings.xml`, `.mvn/` |
| Java (Gradle) | `build.gradle[.kts]` | `gradle.lockfile` | `gradle.properties`, `buildSrc/` |
| Go | `go.mod` | `go.sum` | — |
| Rust | `Cargo.toml` | `Cargo.lock` | `rust-toolchain.toml` |
| .NET | `*.csproj` | `packages.lock.json` | `nuget.config`, `Directory.Packages.props` |
| Ruby | `Gemfile` | `Gemfile.lock` | `*.gemspec` |
| PHP | `composer.json` | `composer.lock` | — |
| Swift | `Package.swift` | `Package.resolved` | — |
| Dart/Flutter | `pubspec.yaml` | `pubspec.lock` | — |
| Elixir | `mix.exs` | `mix.lock` | — |

---

## Step 2: Recursive Collection (Monorepos)

For monorepo projects, collect manifests from ALL workspace packages:

### Node.js Workspaces
```bash
# Get workspace package locations
# npm/yarn: from root package.json "workspaces" field
# pnpm: from pnpm-workspace.yaml

# Find all package.json files (exclude node_modules)
find . -name "package.json" -not -path "*/node_modules/*" -not -path "*/.git/*"
```

### Maven Multi-Module
```bash
# Find all pom.xml files
find . -name "pom.xml" -not -path "*/target/*"
```

### Gradle Multi-Project
```bash
# Find all build.gradle files
find . -name "build.gradle" -o -name "build.gradle.kts" | grep -v "buildSrc"
```

---

## Step 3: Parse Dependency Counts

For each manifest file found, extract basic counts:

### Node.js
```bash
# Count dependencies from package.json
node -e "
const pkg = require('./{PATH}/package.json');
console.log('dependencies:', Object.keys(pkg.dependencies || {}).length);
console.log('devDependencies:', Object.keys(pkg.devDependencies || {}).length);
console.log('peerDependencies:', Object.keys(pkg.peerDependencies || {}).length);
console.log('optionalDependencies:', Object.keys(pkg.optionalDependencies || {}).length);
"
```

### Python
```bash
# Count from requirements.txt (non-empty, non-comment lines)
grep -v '^\s*#' requirements.txt | grep -v '^\s*$' | wc -l
```

### Go
```bash
# Count from go.mod require block
grep -c '^\t' go.mod  # rough count of direct deps
```

---

## Step 4: Detect Version Pinning Strategy

Classify how the project pins its dependencies:

| Strategy | Pattern | Risk Level |
|----------|---------|-----------|
| Exact pin | `"lodash": "4.17.21"` | Low (predictable, may miss patches) |
| Caret range | `"lodash": "^4.17.0"` | Medium (allows minor+patch) |
| Tilde range | `"lodash": "~4.17.0"` | Low-Medium (allows patch only) |
| Wildcard | `"lodash": "*"` | High (any version) |
| Range | `"lodash": ">=4.0.0 <5.0.0"` | Medium |
| Latest | `"lodash": "latest"` | High (unpredictable) |

Report the dominant pinning strategy for the project.

---

## Step 5: Generate Manifest Inventory

```markdown
## 📦 Manifest Inventory

### Summary
- **Total Manifest Files**: {count}
- **Total Direct Dependencies**: {sum across all manifests}
- **Total Dev Dependencies**: {sum}
- **Lock File Present**: {yes/no}
- **Pinning Strategy**: {dominant strategy}

### Manifests Found
| # | File | Path | Direct | Dev | Peer | Optional |
|---|------|------|--------|-----|------|----------|
| 1 | package.json | / | 25 | 48 | 3 | 0 |
| 2 | package.json | /packages/core | 12 | 8 | 0 | 0 |
| ... | | | | | | |

### Lock File Status
| Manifest | Lock File | In Sync | Last Modified |
|----------|-----------|---------|---------------|
| package.json | package-lock.json | ✅ | 2024-03-15 |
```

---

## Step 6: Lock File Sync Check

Verify lock files are in sync with manifests:

```bash
# Node.js: Check if lock file is newer than manifest
# (Heuristic - proper check requires installing)
stat -f "%m" package.json
stat -f "%m" package-lock.json 2>/dev/null || stat -f "%m" yarn.lock 2>/dev/null

# Python: Check if lock exists alongside Pipfile
test -f Pipfile && test -f Pipfile.lock && echo "In sync (check required)"
```

Flag if:
- Lock file is missing when manifest exists
- Lock file is significantly older than manifest
- Multiple lock files exist (npm + yarn conflict)

---

## Completion

After collection, proceed automatically to Workspace State Initialization.
No user approval needed at this stage (informational only).
