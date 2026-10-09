# Consolidation Opportunities - Detailed Steps

## Purpose
Identify duplicate functionality across dependencies and recommend standardization.

---

## Step 1: Detect Functional Overlaps

Scan the dependency list for known overlapping categories:

### HTTP Clients
| Package | Ecosystem | Bundle Size | Recommendation |
|---------|-----------|-------------|---------------|
| axios | Node.js | 14KB | Replace with native fetch (Node 18+) |
| node-fetch | Node.js | 2KB | Replace with native fetch (Node 18+) |
| got | Node.js | 25KB | Keep only if advanced features needed |
| superagent | Node.js | 18KB | Legacy, replace |
| request | Node.js | DEPRECATED | Remove immediately |

### Utility Libraries
| Package | Ecosystem | Notes |
|---------|-----------|-------|
| lodash | Node.js | Full import = 72KB, use lodash-es or individual imports |
| underscore | Node.js | Legacy, lodash superset |
| ramda | Node.js | Different paradigm (FP), fine alongside lodash |

### Date/Time Libraries
| Package | Bundle Size | Status | Recommendation |
|---------|-------------|--------|---------------|
| moment | 72KB | Deprecated | Replace with dayjs (2KB) or date-fns |
| dayjs | 2KB | Active | Recommended (moment-compatible API) |
| date-fns | tree-shakeable | Active | Recommended (modular) |
| luxon | 21KB | Active | Good for complex timezone work |

### Schema Validation
| Package | Notes |
|---------|-------|
| joi | Heavier, server-focused |
| yup | Lighter, form-focused |
| zod | TypeScript-first, recommended |
| ajv | JSON Schema standard |

### State Management (React)
| Package | Notes |
|---------|-------|
| redux + react-redux + redux-toolkit | Full suite if needed |
| zustand | Lighter alternative |
| jotai | Atomic state |
| Multiple state libs | Likely over-engineered |

---

## Step 2: Version Inconsistency Detection (Monorepos)

For monorepo projects, find version inconsistencies:

```bash
# Find all versions of a package across workspace package.jsons
grep -r '"lodash"' packages/*/package.json
# Look for different version specifiers of the same package
```

Flag:
- Same package at different major versions across workspaces
- Same package with conflicting pin strategies (exact vs range)
- Dev dependency in one package, production in another

---

## Step 3: Generate Consolidation Report

```markdown
## 🔄 Consolidation Opportunities

### Duplicate Functionality
| Category | Current Packages | Recommended Standard | Savings |
|----------|-----------------|---------------------|---------|
| HTTP | axios, node-fetch | native fetch | -16KB |
| Dates | moment, dayjs | dayjs only | -72KB |
| Utils | lodash, underscore | lodash-es | -72KB |

### Version Inconsistencies (Monorepo)
| Package | Versions Found | Recommended |
|---------|---------------|-------------|
| {pkg} | 2.1.0, 2.3.0, 3.0.0 | 3.0.0 (all packages) |

### Recommended Actions
1. Standardize on {package} for {category}
2. Remove {deprecated_packages}
3. Align versions in monorepo packages
```

---

## Step 4: Generate Migration Snippets

For each consolidation recommendation, provide code-level guidance:

```markdown
### Replacing moment with dayjs

**Before:**
\```javascript
import moment from 'moment';
const formatted = moment(date).format('YYYY-MM-DD');
const diff = moment(a).diff(moment(b), 'days');
\```

**After:**
\```javascript
import dayjs from 'dayjs';
const formatted = dayjs(date).format('YYYY-MM-DD');
const diff = dayjs(a).diff(dayjs(b), 'day');
\```

**Plugins needed for full moment compatibility:**
\```javascript
import relativeTime from 'dayjs/plugin/relativeTime';
import utc from 'dayjs/plugin/utc';
dayjs.extend(relativeTime);
dayjs.extend(utc);
\```
```
