# Circular Dependency Detection - Detailed Steps

## Purpose
Detect circular references in internal packages and module imports.

---

## Step 1: Check Internal Package References

### Node.js Monorepo
```bash
# For each workspace package, check if it depends on a package that depends back on it
# Parse all package.json files and build internal dependency graph

# Using madge (if available):
npx madge --circular --extensions ts,js src/ 2>/dev/null

# Or manually: check workspace cross-references
```

### Python
```bash
# Check for circular imports
# Using pydeps or importlab (if available):
python -m importlab --find-cycles {source_dir} 2>/dev/null
```

### Java
```bash
# Maven: check inter-module circular deps
mvn dependency:analyze -DignoreNonCompile 2>/dev/null
# Look for cycles in module dependency graph
```

---

## Step 2: Classify Circular Dependencies

| Type | Severity | Description |
|------|----------|-------------|
| Package-level cycle | 🟠 High | Package A depends on B, B depends on A |
| Module-level cycle | 🟡 Medium | File A imports B, B imports A |
| Type-only cycle | 🔵 Low | Only type imports are circular (no runtime impact) |
| Lazy/dynamic cycle | 🟡 Medium | Circular via dynamic imports |

---

## Step 3: Generate Report

```markdown
## 🔄 Circular Dependency Report

### Summary
- Package-level cycles: {n}
- Module-level cycles: {n}
- Type-only cycles: {n}

### Cycles Found
| # | Cycle Path | Type | Severity |
|---|-----------|------|----------|
| 1 | A → B → A | Package | 🟠 |
| 2 | src/a.ts → src/b.ts → src/a.ts | Module | 🟡 |

### Recommended Resolutions
| Cycle | Resolution Strategy |
|-------|-------------------|
| A → B → A | Extract shared code to package C |
| a.ts → b.ts | Use dependency injection or interface extraction |
```
