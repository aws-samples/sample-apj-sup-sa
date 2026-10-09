# Performance Hotspot Detection - Detailed Steps

## Purpose
Identify code-level performance issues: algorithmic, I/O, memory, and frontend-specific.

---

## Step 1: Algorithmic Hotspots

### O(n²) or Worse Detection
Look for nested iterations over the same or related collections:

```
FOR each function:
  IF contains nested loops over collections:
    AND inner loop size correlates with outer loop size:
      FLAG as potential O(n²)
      SEVERITY = based on typical collection size
```

**Examples:**
```typescript
// O(n²) - nested find inside loop
users.forEach(user => {
  const match = allOrders.find(o => o.userId === user.id); // O(n) per iteration
});
// Fix: Build a Map first → O(n) total

// O(n²) - includes in loop
for (const item of largeList) {
  if (processedIds.includes(item.id)) continue; // Array.includes is O(n)
}
// Fix: Use Set instead → O(1) lookup
```

### Redundant Computation
```
FOR each loop body:
  IF contains function call with same arguments each iteration:
    AND function has no side effects:
      FLAG as "hoist computation out of loop"
```

---

## Step 2: I/O Bound Hotspots

### Sequential Awaits (Should Be Parallel)
```typescript
// BAD: Sequential (total time = sum of all)
const user = await getUser(id);
const orders = await getOrders(id);
const preferences = await getPreferences(id);

// GOOD: Parallel (total time = max of all)
const [user, orders, preferences] = await Promise.all([
  getUser(id),
  getOrders(id),
  getPreferences(id),
]);
```

**Detection:** Multiple independent `await` statements in same scope where results
don't depend on each other.

### Synchronous I/O in Async Context
```bash
# Node.js: sync file operations
grep -rn "readFileSync\|writeFileSync\|existsSync" --include="*.{ts,js}" .

# Python: blocking I/O in async functions
grep -rn "def async.*:" -A20 --include="*.py" . | grep "open(\|requests\.\|time.sleep"
```

---

## Step 3: Memory Hotspots

### Unbounded Growth
```
# Detect arrays/lists that grow without bound
FOR each array/list variable:
  IF push/append is called:
    AND no corresponding pop/shift/slice/clear:
      AND no size check or limit:
        FLAG as "unbounded growth risk"
```

### Streaming Opportunities
```
# Detect large file/data loading into memory
FOR each file read operation:
  IF reads entire file (readFile, not createReadStream):
    AND file could be large (user upload, log, data file):
      FLAG as "streaming opportunity"
```

### Closure Memory Retention
```
# Detect closures holding references to large objects
FOR each closure/callback:
  IF captures large object from outer scope:
    AND closure is long-lived (event handler, timeout, interval):
      FLAG as "potential memory leak"
```

---

## Step 4: Frontend-Specific Performance

### React/Vue Re-Render Issues
| Issue | Detection | Impact |
|-------|-----------|--------|
| New object/array in props | `prop={{ key: value }}` in JSX | Re-render every time |
| Inline function in JSX | `onClick={() => ...}` | New function reference each render |
| Missing memoization | Expensive computation in render without useMemo | CPU waste |
| Large list without virtualization | `.map()` rendering >100 items | DOM bloat |
| Unoptimized context | Context value changes trigger all consumers | Cascade re-renders |

### Bundle Performance
| Issue | Detection | Impact |
|-------|-----------|--------|
| No code splitting | Single entry point, no dynamic import() | Large initial load |
| No lazy loading | All routes loaded upfront | Slow first paint |
| Unoptimized images | Large images in bundle (base64, uncompressed) | Bundle bloat |
| Missing compression | No gzip/brotli in build config | Larger network transfer |

---

## Step 5: Caching Opportunities

| Pattern | Detection | Recommendation |
|---------|-----------|---------------|
| Repeated expensive computation | Same function called with same args multiple times | Add memoization |
| Repeated API calls | Same endpoint called multiple times per page | Add response cache |
| Repeated DB queries | Same query in different handlers | Add query cache layer |
| Static data fetched repeatedly | Config/reference data fetched per request | Cache at startup |

---

## Step 6: Generate Report

```markdown
## ⚡ Performance Hotspot Report

### Summary
| Category | Findings | Estimated Impact |
|----------|----------|-----------------|
| Algorithmic (O(n²)+) | {n} | {latency_reduction}ms per call |
| Sequential I/O | {n} | {latency_reduction}ms per request |
| Memory issues | {n} | {memory_savings}MB potential |
| Frontend re-renders | {n} | {time_savings}ms per interaction |
| Missing caching | {n} | {calls_saved} DB/API calls saved |

### Top Optimization Opportunities (Sorted by Impact)
| # | File | Issue | Est. Improvement | Effort |
|---|------|-------|-----------------|--------|
| 1 | {file}:{line} | O(n²) loop on {n} items | -{x}ms latency | Low |
| 2 | {file}:{line} | Sequential 3 awaits | -{x}ms latency | Low |
| 3 | {file}:{line} | Full file read (streaming) | -{x}MB memory | Medium |

### Detailed Fixes
For each finding, provide before/after code examples.
```

---

## Completion
Output to `dep-analysis-docs/analysis/performance-hotspots.md`
