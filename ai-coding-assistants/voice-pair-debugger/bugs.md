# Voice pair debugger for AWS — Planted bugs

The demo stack in `sample-app/` ships with deliberate bugs for `Voice` to diagnose. The source is intentionally free of "this is the bug" comments so the bugs are realistic to find. Each one surfaces through a different tool and has its root cause in a different place, so the set exercises the full diagnostic loop rather than a single signal.

> This document is the answer key.
> If you are running the demo to practise, stop reading here.

In the examples below, `$API` is the `api_url` output from `terraform apply`.

## Summary

| Endpoint | Function | Class of bug | Root cause lives in | Difficulty |
|----------|----------|--------------|---------------------|------------|
| `GET /users` | `get_users.mjs` | Wrong attribute name | Lambda code | Easy |
| `GET /users/{id}` | `get_user.mjs` | Event-shape mismatch | Code vs route | Medium |
| `GET /stats` | `get_stats.mjs` | Environment variable mismatch | Code vs Lambda config | Medium |
| `DELETE /users/{id}` | `delete_user.mjs` | Missing IAM permission | IAM policy, not the code | Hard |

`POST /users` (`create_user.mjs`) is the working control. It has no planted bug and is useful for confirming the stack itself is healthy.

## 1. GET /users: wrong attribute name

- File: `sample-app/lambda/get_users.mjs`
- Difficulty: easy
- Surfaced by: CloudWatch logs, then reading the Lambda source

### Symptom

`GET /users` returns HTTP 500.

```bash
curl -s "$API/users"
# {"error":"Missing required attribute 'userId' on user item"}
```

### Root cause

The handler scans the table and maps each item's `userId` property, but the table's partition key attribute is `user_id`. Every item therefore fails the guard and the handler throws.

### Fix

In `get_users.mjs`, read `item.user_id` instead of `item.userId`:

```js
return {
  id: item.user_id,
  name: item.name,
  email: item.email
};
```

(and remove the now-redundant `userId` guard).

### Why it is a good teaching case

It is the warm-up. The error message names the offending attribute, so it is a clean log-to-code correlation with no cross-file hunting.

## 2. GET /users/{id}: event-shape mismatch

- File: `sample-app/lambda/get_user.mjs`
- Route: defined in `sample-app/apigateway.tf`
- Difficulty: medium
- Surfaced by: CloudWatch logs, then correlating the route with the code

### Symptom

`GET /users/{id}` returns HTTP 500 regardless of the id supplied.

```bash
curl -s "$API/users/u-001"
# {"error":"Missing path parameter: userId"}
```

### Root cause

The API Gateway route is declared as `GET /users/{id}`, so API Gateway delivers the path value as `event.pathParameters.id`. The handler reads `event.pathParameters.userId`, which is always `undefined`.

The code looks correct on its own, and the route looks correct on its own. The bug only appears when you read both together.

### Fix

Either read the correct key in `get_user.mjs`:

```js
const userId = event.pathParameters?.id;
```

or rename the route parameter in `apigateway.tf` to `{userId}` and keep the code. Pick one and keep the two consistent.

## 3. GET /stats: environment variable mismatch

- File: `sample-app/lambda/get_stats.mjs`
- Config: `sample-app/lambda.tf`
- Difficulty: medium
- Surfaced by: `describe_lambda_function` (the env vars), confirmed in logs

### Symptom

`GET /stats` returns HTTP 500.

```bash
curl -s "$API/stats"
# {"error":"... TableName ... null ..."}  (a DynamoDB ValidationException)
```

### Root cause

The handler reads `process.env.DYNAMO_TABLE`, but the Terraform definition sets the environment variable as `TABLE_NAME`. `DYNAMO_TABLE` is therefore `undefined`, the scan is sent with a null `TableName`, and DynamoDB rejects it.

This is the case where reading the Lambda configuration beats reading logs: the env var list shows `TABLE_NAME`, while the code expects `DYNAMO_TABLE`.

### Fix

In `get_stats.mjs`, read the variable the function is actually given:

```js
const TABLE_NAME = process.env.TABLE_NAME;
```

(or set `DYNAMO_TABLE` in `lambda.tf` instead; keep code and config aligned).

## 4. DELETE /users/{id}: missing IAM permission

- File: `sample-app/lambda/delete_user.mjs` (the code is correct)
- Root cause: `sample-app/iam.tf`
- Difficulty: hard
- Surfaced by: CloudWatch logs, then correlating with the IAM policy

### Symptom

`DELETE /users/{id}` returns HTTP 500.

```bash
curl -s -X DELETE "$API/users/u-002"
# {"error":"... is not authorized to perform: dynamodb:DeleteItem ..."}
```

### Root cause

The handler is correct: it reads the path parameter and issues a `DeleteCommand`. The Lambda execution role, however, only grants `dynamodb:GetItem`, `dynamodb:PutItem`, and `dynamodb:Scan`. The delete is denied with an `AccessDeniedException`.

This is the hardest of the four because the instinct is to fix the function, and the function has nothing wrong with it. The fix is in the policy.

### Fix

Add `dynamodb:DeleteItem` to the inline policy in `iam.tf`:

```hcl
Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan", "dynamodb:DeleteItem"]
```

### Why it is a good teaching case

It forces the habit of reading the error rather than assuming. The `AccessDeniedException` message names the exact action and principal, which points straight at IAM rather than the code.
