# Dependency Graph Construction - Detailed Steps

## Purpose
Build a complete dependency tree showing direct, transitive, dev, and peer dependencies.

---

## Step 1: Resolve Full Dependency Tree

Execute the appropriate command for the detected package manager:

### Node.js (npm)
```bash
npm ls --all --json 2>/dev/null | head -500
# Or for a readable tree:
npm ls --all --depth=10 2>&1
```

### Node.js (yarn)
```bash
yarn list --all --json 2>/dev/null
# Or readable:
yarn why {package_name}
```

### Node.js (pnpm)
```bash
pnpm list --depth=Infinity --json 2>/dev/null
```

### Python (pip)
```bash
pip show {package} 2>/dev/null  # per-package
pipdeptree --json 2>/dev/null   # if pipdeptree available
pipdeptree --warn silence 2>/dev/null  # tree format
```

### Python (poetry)
```bash
poetry show --tree 2>/dev/null
```

### Java (Maven)
```bash
mvn dependency:tree -DoutputType=text 2>/dev/null
mvn dependency:tree -DoutputType=dot 2>/dev/null  # for graph viz
```

### Java (Gradle)
```bash
./gradlew dependencies --configuration runtimeClasspath 2>/dev/null
./gradlew dependencies --configuration compileClasspath 2>/dev/null
```

### Go
```bash
go mod graph 2>/dev/null
go list -m all 2>/dev/null
```

### Rust
```bash
cargo tree 2>/dev/null
cargo tree --duplicates 2>/dev/null  # show duplicate versions
```

### .NET
```bash
dotnet list package --include-transitive 2>/dev/null
```

### Ruby
```bash
bundle list 2>/dev/null
bundle viz 2>/dev/null  # if graphviz available
```

---

## Step 2: Parse and Classify Dependencies

Classify each dependency into categories:

| Category | Definition | Analysis Scope |
|----------|-----------|---------------|
| Direct Production | Declared in main dependencies | Full analysis |
| Direct Dev | Declared in devDependencies | Security + freshness |
| Transitive | Pulled by direct deps | Security only |
| Peer | Declared as peer requirements | Compatibility check |
| Optional | Declared as optional | Note only |

---

## Step 3: Calculate Graph Metrics

```markdown
## Dependency Graph Metrics

| Metric | Value | Assessment |
|--------|-------|-----------|
| Direct Production Deps | {n} | {ok/high/very high} |
| Direct Dev Deps | {n} | {ok/high} |
| Transitive Deps | {n} | {ok/high/very high} |
| Total Unique Packages | {n} | — |
| Max Tree Depth | {n} | {shallow/deep/very deep} |
| Duplicate Packages | {n} | {none/few/many} |

### Assessment Thresholds
- Direct deps > 50: HIGH - consider consolidation
- Direct deps > 100: VERY HIGH - likely over-engineered
- Transitive > 500: HIGH - supply chain risk
- Max depth > 10: DEEP - resolution conflicts likely
- Duplicates > 20: MANY - bundle bloat, version conflicts
```

---

## Step 4: Identify Key Dependencies

Find the most critical dependencies (highest fan-in):

```markdown
### Most Depended-Upon Packages
| # | Package | Dependents Count | Category |
|---|---------|-----------------|----------|
| 1 | {pkg} | {n} packages depend on this | Core |
| 2 | {pkg} | {n} packages depend on this | Utility |
```

These are the "keystone" packages - if they have issues, the blast radius is large.

---

## Step 5: Detect Conflicts and Issues

Check for:
- **Version conflicts**: Same package required at different versions
- **Peer dependency warnings**: Unmet peer requirements
- **Deprecated packages**: Packages marked as deprecated
- **Duplicate installations**: Same package installed multiple times

```bash
# npm: Check for peer dep issues
npm ls 2>&1 | grep -i "peer dep" | head -20

# npm: Check for deduplication opportunities  
npm dedupe --dry-run 2>&1

# yarn: Check for duplicates
yarn dedupe --check 2>&1
```

---

## Step 6: Generate Visualization

Produce a text-based dependency tree (truncated to reasonable depth):

```
project-root@1.0.0
├── express@4.18.2
│   ├── body-parser@1.20.2
│   │   ├── bytes@3.1.2
│   │   └── content-type@1.0.5
│   ├── cookie@0.5.0
│   └── ...
├── @prisma/client@5.10.0
│   └── @prisma/engines@5.10.0
└── zod@3.22.4 (no transitive deps)
```

Limit visualization to depth 3 for readability.
Full tree available in generated report file.

---

## Step 7: Write Report

Output to `dep-analysis-docs/analysis/dependency-graph.md`

Proceed automatically to Version Freshness Analysis.
