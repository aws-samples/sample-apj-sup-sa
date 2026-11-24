# API Surface Detection - Detailed Steps

## Purpose
Identify all API endpoints, protocols, and interface definitions in the project.

---

## Step 1: Detect API Protocol

### REST API Detection
```bash
# Route/controller patterns
grep -rn "app\.\(get\|post\|put\|delete\|patch\)\|router\.\(get\|post\|put\|delete\)" \
  --include="*.{ts,js}" . | head -30
grep -rn "@GetMapping\|@PostMapping\|@RequestMapping\|@RestController" \
  --include="*.java" . | head -30
grep -rn "@app\.\(get\|post\|put\|delete\)\|@router\." --include="*.py" . | head -30
grep -rn "path(\|re_path(" --include="urls.py" . | head -30

# OpenAPI/Swagger spec
find . -name "openapi*" -o -name "swagger*" | grep -v node_modules
```

### GraphQL Detection
```bash
# Schema files
find . -name "*.graphql" -o -name "*.gql" | grep -v node_modules
# Schema in code
grep -rn "typeDefs\|gql\`\|buildSchema\|@ObjectType\|@Query\|@Mutation" \
  --include="*.{ts,js,java}" . | head -20
# GraphQL packages
grep -rn "graphql\|apollo\|@nestjs/graphql\|strawberry\|graphene" package.json pyproject.toml pom.xml 2>/dev/null
```

### gRPC Detection
```bash
find . -name "*.proto" | grep -v node_modules | grep -v vendor
grep -rn "^service \|^rpc " --include="*.proto" . | head -20
```

### WebSocket Detection
```bash
grep -rn "WebSocket\|socket\.io\|ws\(\|WSS\)" --include="*.{ts,js,py,java}" . | head -10
```

---

## Step 2: Count API Surface

```markdown
## API Surface Detection Results

| Protocol | Endpoints/Operations | Spec File | Confidence |
|----------|---------------------|-----------|-----------|
| REST | {n} routes | {openapi.yaml or "none"} | {HIGH/MED} |
| GraphQL | {n} queries + {n} mutations | {schema.graphql or "inline"} | {HIGH/MED} |
| gRPC | {n} services, {n} RPCs | {n} proto files | {HIGH/MED} |
| WebSocket | {n} channels/events | none | {MED/LOW} |
```

---

## Step 3: Decision

If API surface detected → Phase 5 (API & Contracts) will execute.
If no API surface → Phase 5 will be skipped.
Mark finding in state file.
