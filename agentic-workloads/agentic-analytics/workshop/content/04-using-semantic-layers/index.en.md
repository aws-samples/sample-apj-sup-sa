---
title: "[Optional] Integrating your agent with Cube Core Semantic Layer"
weight: 77
---

## Learning Objectives

By the end of this step, you will:
- Understand why a semantic layer sits between raw SQL and prebaked views — and when to use each
- Define Cube data models (cubes, measures, dimensions) that map to your database schema
- Deploy a Lambda function that proxies Cube's REST API with automatic tenant isolation
- Register the Lambda as a Gateway target and test the agent's ability to construct Cube JSON queries

## The Problem

You've now seen three data access patterns:

| Pattern | Pros | Cons |
|---------|------|------|
| **Prebaked SQL** (Step 3) | Minimum hallucination — LLM never generates SQL | Rigid — every new question needs a new View |
| **Custom SQL** (Step 6) | Flexible — handles any ad-hoc question | Hallucination risk — LLM generates raw SQL |
| **API Integration** (Step 5) | Connects to external services | Only for write operations and external APIs |

There's a gap between Prebaked SQL (safe but rigid) and Custom SQL (flexible but risky). What if you could give the agent a **structured vocabulary** of dimensions and measures, and let it construct queries using that vocabulary — without ever writing raw SQL?

## The Solution: Semantic Layer with Cube Core

A :link[semantic layer]{href="https://cube.dev/docs/product/introduction" external=true} defines the **business meaning** of your data — what can be measured, what can be grouped by, and how tables relate. The agent constructs structured JSON queries using this vocabulary, and Cube translates them to SQL.

```
User: "Top 5 customers by revenue"
  → Agent calls cube_meta_tool (discovers: bookings.total_revenue, customers.name)
  → Agent constructs JSON query:
      {"measures": ["bookings.total_revenue"],
       "dimensions": ["customers.name"],
       "order": {"bookings.total_revenue": "desc"},
       "limit": 5}
  → Lambda injects account_id filter (multi-tenancy)
  → Cube translates to SQL → Aurora PostgreSQL → Results
```

The agent never writes SQL. It picks from a defined set of measures and dimensions — more flexible than Prebaked SQL, more reliable than Custom SQL.

::alert[**Key insight:** The semantic layer defines the "vocabulary" the agent can use. If a measure or dimension doesn't exist in the Cube model, the agent can't query it. This is a controlled flexibility — you expand what the agent can do by adding to the data model, not by hoping the LLM generates correct SQL.]{type="info"}

## Architecture

```
AgentCore Runtime (Strands Agent)
  → AgentCore Gateway (MCP Server)
    → SemanticLayer target (Lambda, 2 tools)
      → cube_meta_tool  → GET  /cubejs-api/v1/meta  (discover cubes)
      → cube_query_tool → POST /cubejs-api/v1/load  (execute queries)
        → Cube Core (Docker on EC2, same VPC)
          → Aurora PostgreSQL
```

The Lambda handles multi-tenancy the same way as the other toolsets: it extracts `account_id` from the JWT (propagated via the Gateway Interceptor) and injects it as a filter into every Cube query before sending it to the API.

::alert[**This optional lab uses scripts, on purpose.** The rest of the workshop builds the agent by editing one CloudFormation top-up and running `make deploy`. This lab is different: it stands up a **parallel, second** agent (its own Gateway, Runtime, and UI) so you can compare the semantic-layer agent side-by-side with the one you built. It also pushes Cube data models to an EC2 container over S3 + SSM. Those steps don't map cleanly to the single top-up stack, so this lab keeps its purpose-built `deploy_cube_models.py` and `deploy_semantic_layer_stack.py` scripts — a deliberate exception, like the evaluation CLI in Step 10.]{type="info"}

## Lab Procedures

### Step 1: Access the Cube Core Playground UI

Cube Core was deployed as part of the CloudFormation stack — it's running as a Docker container on an EC2 instance in a **private subnet** of your VPC (no public IP). Cube ships with a browser-based **Playground UI** that lets you explore data models, build queries visually, and test them — all without writing code.

::alert[**The Cube EC2 is private and not reachable from the internet** — by design. Its security group accepts traffic on port 4000 from only two places: the semantic-layer **Lambda** (the agent's tool path) and the **Code Editor** (your workspace). You reach the Playground *through* the Code Editor, which forwards the port to your browser. This is the secure pattern: the database-facing Cube instance is never exposed publicly.]{type="info"}

**1a. Open the Playground via the Code Editor's Ports panel:**

Your Code Editor already forwards the private Cube instance to **`localhost:4000`** on the Code Editor box (a small tunnel set up at deploy time). To open it in your browser, use the Code Editor's built-in **Ports** feature:

1. In the Code Editor, open the **Ports** panel (the **Ports** tab next to the Terminal, or **View → Open View… → Ports**).
2. You should see port **4000** listed (labelled the Cube tunnel). If it isn't there yet, click **Forward a Port** and enter `4000`.
3. Click the **🌐 globe / "Open in Browser"** icon next to port 4000.

A new tab opens the Cube Playground at its root path. It will show a mostly empty interface because no data models have been defined yet — that's expected.

::alert[**Why the Ports panel and not a URL path?** Cube's Playground is a single-page app that loads its assets from the site root, so it can't be served under a sub-path like `/cube/`. The Ports panel forwards it at the root of a generated URL, which is why the Playground (and its live-reload) work correctly. The forward targets `localhost:4000` on the Code Editor, which tunnels to the private Cube instance.]{type="info"}

::alert[**If port 4000 isn't listed or the page doesn't load:** the Code Editor discovers the Cube instance shortly after both boot and retries every ~2 minutes — wait a minute and re-open the Ports panel. You can also confirm Cube is running via **SSM Session Manager** on the Cube EC2 instance: `docker ps`.]{type="warning"}

**1b. Set up database connectivity:**

In the Playground, open the **`#/connection`** path (append `#/connection` to the forwarded URL) to configure the PostgreSQL connection. Enter your Aurora PostgreSQL credentials (see the info box below). Once the connection is saved, Cube will use these credentials to query Aurora when you add data models in the next step.

::alert[**Need the database credentials?** Retrieve the Aurora credentials from **AWS Console** → **Secrets Manager** → the secret named `agentic-analytics/aurora/credentials`. Click **Retrieve secret value** to see the host, port, username, password, and database name. **Note:** Cube connects to Aurora as the database **owner** (`postgres`), which is why tenant isolation for the semantic layer is enforced in the Lambda rather than by PostgreSQL RLS — see the Step 9 callout.]{type="info"}

### Step 2: Explore the Cube Data Models

Before deploying anything, take a moment to explore the Cube data model files in the Code Editor. Understanding the model structure is key to understanding how the semantic layer works.

**2a. Open the initial (baseline) models:**

In the Code Editor file explorer, navigate to :code[dataset/cube_models/initial_model/]{showCopyAction=true}. Open a few of the YAML files — `bookings.yml` and `customers.yml` are good starting points.

Notice the structure of each file. A Cube data model is built from three core concepts:

- **Cubes** — A cube maps to a database table (or SQL query). Each YAML file defines one cube. The `sql_table` property tells Cube which table to query. Think of a cube as a logical entity the agent can ask questions about.
- **Dimensions** — These are the columns you can group by or filter on. In the baseline models, each dimension maps directly to a raw database column (e.g., `pickup_location`, `breed`, `first_name`). Dimensions have a `type` — `string`, `number`, `time`, or `boolean`.
- **Measures** — These are the aggregations you can compute. In the baseline models, each cube has only a single `count` measure. Measures define *what* gets calculated — count, sum, average, etc.

The baseline models are intentionally minimal: raw column dimensions, a primary key, and a single `count` measure per cube. No joins between cubes, no derived dimensions, no business logic. This is the simplest possible semantic layer — just the tables exposed as-is.

**2b. Compare with the final (production) models:**

Now open the same files under :code[dataset/cube_models/final_model/]{showCopyAction=true}. Compare `bookings.yml` side by side with the initial version.

You'll immediately see the difference. The final models add:
- **Joins** — Relationships between cubes (e.g., bookings → customers, bookings → unicorns) that let the agent combine data across tables in a single query
- **Derived dimensions** — Computed values like `status` (derived from `is_completed` and `cancellation_reason`), `duration_hours`, and `start_day_name` that encode business logic the agent would otherwise have to guess
- **Richer measures** — `total_revenue`, `avg_booking_value`, `cancellation_rate`, `unique_customers` — aggregations that match how the business actually thinks about the data
- **Segments** — Named filters like `completed_bookings` and `late_returns` that the agent can apply by name instead of constructing filter logic

The key insight: every enrichment you add to the model expands what the agent can do reliably — without changing any application code. The semantic layer is the control surface.

**2c. Deploy the baseline models:**

Cube needs these YAML model files in `/cube/conf/model/` on the EC2 instance. Deploy the baseline models using the provided script:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
python3 infra/deploy_cube_models.py --model-set initial
```

The script uploads all five YAML model files (`accounts.yml`, `bookings.yml`, `customers.yml`, `transactions.yml`, `unicorns.yml`) to S3, then triggers an SSM Run Command on the Cube EC2 instance to sync them into `/cube/conf/model/`. Finally, it verifies that Cube loaded the expected number of cubes.

Expected output:

```
Deploying Cube models: initial
============================================================
[OK] Uploaded 5 YAML files to s3://<bucket>/models/initial/
[OK] SSM command completed — models synced to /cube/conf/model/
[OK] Cube verification: 5 cubes loaded
============================================================
```

::alert[**Why start with a baseline?** Starting with minimal models lets you see what the agent can and cannot do with just raw columns. As you enrich the models in the next steps, you'll see the agent's capabilities expand — without changing any application code. The semantic layer is the control surface.]{type="info"}

### Step 3: Verify the Data Models in the Playground UI

After deploying the baseline model files, Cube automatically picks them up in dev mode. Go back to the Cube Playground UI in your browser and refresh the page.

You should now see the data models available in the Playground:

1. Click the **Build** tab
2. Click the dropdown on the **Bookings** cube — you'll see the measures and dimensions available for that cube
3. Select the **Bookings Count** measure and the **Bookings Pickup Location** dimension
4. Click **Run** — you should see booking counts grouped by pickup location

This confirms Cube can reach Aurora and the data models are correctly defined.

::alert[**Cube dev mode** watches the `/cube/conf/model/` directory for changes. If you edit a YAML file on the EC2 instance, Cube reloads automatically — no restart needed. Refresh the Playground UI to see updated models.]{type="info"}

::alert[**Notice the limitation:** With baseline models, the agent can't ask "top customers by revenue" because there's no `total_revenue` measure and no joins between cubes. It can only count rows and group by columns within a single cube. This is why the progression to the full models matters.]{type="info"}

You can click the **JSON Query** tab in the Playground to see the Cube JSON query object:

```json
{
  "measures": ["bookings.count"],
  "dimensions": ["bookings.pickup_location"]
}
```

This is the format the agent will construct and send via the Lambda.

### Step 4: Upgrade to the Full Production Models

Now that you've verified the baseline works in the Playground, upgrade to the full models to unlock the agent's full analytical capabilities. You explored the differences between the initial and final models in Step 2 — now you'll deploy the enriched versions.

The full production models are located in `dataset/cube_models/final_model/`. Deploy them using the same script with the `--model-set final` flag:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
python3 infra/deploy_cube_models.py --model-set final
```

This uploads the enriched model files to S3 and deploys them to the Cube EC2 instance via SSM Run Command — the same process as the initial deployment, but with the full production models that include joins, derived dimensions, subquery dimensions, and segments.

Expected output:

```
Deploying Cube models: final
============================================================
[OK] Uploaded 5 YAML files to s3://<bucket>/models/final/
[OK] SSM command completed — models synced to /cube/conf/model/
[OK] Cube verification: 5 cubes loaded
============================================================
```

Cube reloads automatically. Refresh the Cube Playground UI to see the new dimensions, measures, and segments. You'll notice derived dimensions like `bookings.status`, subquery dimensions like `customers.lifetime_revenue`, and segments like `completed_bookings` and `vip_customers`.

::alert[**Key takeaway:** Each layer you add to the Cube model expands what the agent can do reliably — without changing the Lambda, the agent code, or the SOP. The semantic layer is the single place where you encode business logic, and the agent automatically benefits from every enrichment.]{type="success"}

### Step 5: Review the Lambda Function

Open :code[tools/semantic_layer_toolset_lambda.py]{showCopyAction=true} in the Code Editor.

This Lambda exposes two tools:

| Tool | Cube Endpoint | Purpose |
|------|--------------|---------|
| `cube_meta_tool` | `GET /v1/meta` | Discover available cubes, dimensions, and measures |
| `cube_query_tool` | `POST /v1/load` | Execute a Cube JSON query |

Key design decisions:

1. **No PyJWT dependency** — The Lambda creates Cube API tokens using only standard library modules (`hmac`, `hashlib`, `base64`). No Lambda layer needed. Note: In dev mode (`CUBEJS_DEV_MODE=true`), Cube does not enforce API token authentication — requests without a token are accepted. The Lambda still generates and sends a token for production readiness, but it is not strictly required in this workshop environment.

2. **Multi-tenancy at the Lambda layer** — `_inject_account_id_filter()` extracts `account_id` from the JWT and adds it as a filter to every query. The agent never sees `account_id` dimensions (they're stripped from the `/meta` response), so it can't accidentally bypass tenant isolation.

3. **Same JWT extraction pattern** — Uses `context.client_context.custom['bedrockAgentCorePropagatedHeaders']` to read the JWT, identical to the Prebaked SQL and Custom SQL Lambdas.

::alert[**Compare with Prebaked SQL:** In Step 3, the Lambda maps each tool to a database View — the LLM picks a tool. Here, the Lambda proxies Cube's API — the LLM constructs a JSON query from discovered dimensions and measures. The tenant-isolation *boundary*, however, differs: Prebaked/Custom SQL rely on PostgreSQL Row-Level Security, while the semantic layer enforces isolation in **this Lambda** by injecting the `account_id` filter (Cube connects to Aurora as the table owner and bypasses RLS). Step 9 covers this in detail. The flexibility is much higher than Prebaked SQL.]{type="info"}

### Step 6: Deploy the Semantic Layer Toolset

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
python3 infra/deploy_semantic_layer_toolset.py
```

This script:
1. Resolves the Cube EC2 private IP from CloudFormation
2. Creates the Lambda in VPC private subnets (with egress to Cube on port 4000)
3. Runs a smoke test (calls `cube_meta_tool` and verifies cubes are returned)

::alert[This script only deploys the Lambda — it does **not** register it to any Gateway. You will deploy a dedicated Gateway for the semantic layer agent in Step 7 (`deploy_semantic_layer_stack.py`), which handles Gateway creation and target registration.]{type="info"}

Expected output:

```
Deploying Semantic Layer (Cube Core) Lambda
============================================================
[OK] Cube private IP: 10.0.1.xxx
[OK] Created ZIP: semantic_layer_toolset_lambda.zip
[OK] Created role: semantic-layer-toolset-lambda-role
[OK] Created Lambda: semantic-layer-toolset-lambda

Testing Lambda...
[OK] Test passed! Cubes found: ['unicorns', 'customers', 'bookings', 'transactions']

============================================================
[OK] Lambda deployment complete!
   Lambda: arn:aws:lambda:...
   Tools: cube_meta_tool, cube_query_tool
```

::alert[Notice the smoke test output shows 4 cubes, not 5 — the `accounts` cube is intentionally hidden from the agent by the Lambda's `cube_meta` function. This prevents the agent from querying the tenant table directly.]{type="info"}

### Step 7: Deploy the Parallel Semantic Layer Stack

Now you'll deploy a completely separate stack — Gateway, Runtime, and Amplify UI — dedicated to the semantic layer agent. This lets you compare the semantic layer agent side-by-side with the prebaked SQL agent in two browser tabs.

The parallel stack shares the same Aurora PostgreSQL database and Cognito User Pool as the existing stack. Only the Gateway, Runtime, and UI are separate — so both agents query the same data with the same user credentials, but use different tools.

::alert[**Before running this script, install `uv`** (the script's runtime build step uses it; it isn't pre-installed). Run the one-liner below, then continue. See the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/) for details.]{type="warning"}

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
```

::alert[**This script may need to be run twice — that's expected.** It creates a fresh IAM role for the Gateway, and AWS IAM can take a moment to propagate; the first run can fail at "Register SemanticLayer Target" with *"Gateway service is not authorized to perform AssumeRole on Gateway role"*. Simply **run the script again** — every step is idempotent, and the second run succeeds once the role has propagated.]{type="info"}

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
python3 infra/deploy_semantic_layer_stack.py
```

The script performs four steps:

1. **Creates a separate AgentCore Gateway** with the same Cognito authorizer configuration but a distinct Gateway name
2. **Registers the SemanticLayer target** (`cube_meta_tool` + `cube_query_tool`) on the new Gateway
3. **Deploys a separate AgentCore Runtime** using the semantic layer agent entrypoint (`unicorn_rental_semantic_agent.py`)
4. **Deploys a separate Amplify UI** (`agentic-analytics-semantic-ui`) pointing to the new Gateway and Runtime
5. **Saves configuration** — the new Gateway URL and Amplify URL are written to `semantic_config.env`

Expected output:

```
Deploying Semantic Layer Parallel Stack
============================================================
[1/4] Creating separate Gateway...
[OK] Gateway created: SemanticLayerGateway-xxxxxxxxxx
[2/4] Registering SemanticLayer target...
[OK] Target registered with 2 tools
[3/4] Deploying AgentCore Runtime...
[OK] Runtime deployed: unicorn_rental_semantic_agent
[4/4] Deploying Amplify UI...
[OK] Amplify app deployed: agentic-analytics-semantic-ui
============================================================
Semantic Layer UI: https://main.xxxxxxxxxx.amplifyapp.com
Config saved to: semantic_config.env
```

**Open two browser tabs for side-by-side comparison:**

- **Tab 1:** Open the existing Amplify UI URL (from `config.env`) — this is the **prebaked SQL agent** with the original toolset
- **Tab 2:** Open the new semantic layer UI URL (from `semantic_config.env`) — this is the **semantic layer agent** with Cube tools

::alert[**Same credentials, different tools.** Both UIs connect to the same Aurora PostgreSQL database and Cognito User Pool, so the same login credentials work in both. The difference is which tools the agent has access to — the prebaked SQL agent uses pre-defined database Views, while the semantic layer agent constructs Cube JSON queries from discovered measures and dimensions.]{type="info"}

### Step 8: Test Semantic Layer Queries

Try these queries in the semantic layer UI (Tab 2) — prefix with "Using the semantic layer" to guide the agent toward the SemanticLayer tools. These queries leverage the full production models you deployed in Step 4.

For each query, the agent follows the same pattern: call `cube_meta_tool` first to discover available measures and dimensions, construct a Cube JSON query, then call `cube_query_tool` to execute it.

**Query 1: Top 5 customers by revenue**

Ask: :code[Using the semantic layer, show me top 5 customers by revenue]{showCopyAction=true}

The agent should:
1. Call `SemanticLayer___cube_meta_tool` to discover available measures and dimensions
2. Identify `bookings.total_revenue` as the revenue measure and `customers.display_name` as the customer dimension
3. Construct a Cube JSON query and call `SemanticLayer___cube_query_tool`
4. Return formatted results

Expected Cube JSON query:

```json
{
  "measures": ["bookings.total_revenue"],
  "dimensions": ["customers.display_name"],
  "order": {"bookings.total_revenue": "desc"},
  "limit": 5
}
```

**Query 2: Monthly revenue trend**

Ask: :code[Using the semantic layer, what's the monthly revenue trend?]{showCopyAction=true}

The agent should:
1. Call `SemanticLayer___cube_meta_tool` to discover available measures and time dimensions
2. Identify `bookings.total_revenue` as the measure and `bookings.start_datetime` as the time dimension
3. Use `timeDimensions` with `granularity: "month"` for a time-series query

Expected Cube JSON query:

```json
{
  "measures": ["bookings.total_revenue"],
  "timeDimensions": [{
    "dimension": "bookings.start_datetime",
    "granularity": "month"
  }],
  "order": {"bookings.start_datetime": "asc"}
}
```

**Query 3: Revenue by unicorn breed**

Ask: :code[Using the semantic layer, which unicorn breeds generate the most revenue?]{showCopyAction=true}

The agent should:
1. Call `SemanticLayer___cube_meta_tool` to discover measures and dimensions across cubes
2. Identify `bookings.total_revenue` as the measure and `unicorns.breed` as the dimension (cross-cube query using joins)
3. Construct a query that groups by breed and orders by revenue

Expected Cube JSON query:

```json
{
  "measures": ["bookings.total_revenue"],
  "dimensions": ["unicorns.breed"],
  "order": {"bookings.total_revenue": "desc"}
}
```

**Query 4: Customer segmentation (requires final models)**

Ask: :code[Using the semantic layer, how many customers are in each revenue segment?]{showCopyAction=true}

The agent should:
1. Call `SemanticLayer___cube_meta_tool` to discover the `revenue_segment` dimension (only available in the final model set)
2. Identify `customers.count` as the measure and `customers.revenue_segment` as the dimension
3. Construct a segmentation query

Expected Cube JSON query:

```json
{
  "measures": ["customers.count"],
  "dimensions": ["customers.revenue_segment"]
}
```

::alert[**This query only works with the full production models** (final model set deployed in Step 4). The `revenue_segment` dimension is a derived CASE expression over subquery dimensions (`lifetime_revenue`) that don't exist in the baseline initial models. If you see an error about `revenue_segment` not being found, verify you ran `deploy_cube_models.py --model-set final` in Step 4.]{type="warning"}

### Step 9: Verify Multi-Tenancy

Multi-tenancy ensures that each tenant only sees their own data, even though all tenants share the same Aurora PostgreSQL database. The Lambda's `_inject_account_id_filter` function automatically scopes every Cube query to the authenticated tenant by extracting the `account_id` from the JWT and injecting it as a filter — before the query reaches Cube.

Follow these steps to verify tenant isolation:

**9a. Log in as the first user:**

1. In the semantic layer UI (Tab 2), log in as :code[lyra.starwhisper@example-mythicalunicorns.com]{showCopyAction=true} / :code[Unicorn123!]{showCopyAction=true}
2. Ask: :code[Using the semantic layer, show me total revenue]{showCopyAction=true}
3. Note the revenue figure returned

**9b. Log in as the second user:**

4. Log out of the semantic layer UI
5. Log in as :code[aria.skybloom@example-mythicunicorns.com]{showCopyAction=true} / :code[Unicorn123!]{showCopyAction=true}
6. Ask the same question: :code[Using the semantic layer, show me total revenue]{showCopyAction=true}
7. The revenue figure should be **different** — the Lambda injected a different `account_id` filter for this user

**9c. Confirm isolation:**

The two users belong to different accounts, so the `_inject_account_id_filter` function adds a different `account_id` value to the Cube query's `filters` array for each user. This means the same Cube JSON query produces different results depending on who is logged in — without the agent or the Cube model needing to know anything about multi-tenancy.

This confirms that tenant isolation works at the Cube query level — the same security *goal* as the Prebaked SQL and Custom SQL toolsets, but enforced by a **different mechanism**.

::alert[**Important — tenant isolation here is enforced differently than in Step 7.** The Prebaked SQL and Custom SQL toolsets connect to Aurora as a **non-owner role** (`app_user`) and rely on **PostgreSQL Row-Level Security** to filter rows at the database engine. The semantic layer does **not** use RLS: Cube Core connects to Aurora as the table **owner** (the `postgres` user you entered in Step 1c), and table owners **bypass RLS**. Instead, tenant isolation is enforced **one layer up, in the Lambda** — `_inject_account_id_filter` adds an `account_id` filter (read from the JWT) to every Cube query, for every referenced cube, before it reaches Cube. So the isolation boundary for the semantic layer is the **Lambda + the propagated JWT**, not the database. This is why the Gateway must propagate the `Authorization` header to the Lambda (via the Gateway interceptor `deploy_semantic_layer_stack.py` configures) — without the JWT, the Lambda can't determine the tenant and the filter can't be applied. When you adapt this pattern, treat the Lambda's filter injection as the security-critical control and review it accordingly.]{type="warning"}

## Comparing the Three Data Access Patterns

Now that you've built all three, here's when to use each:

| Pattern | Use When | Agent Constructs | Hallucination Risk |
|---------|----------|-----------------|-------------------|
| **Prebaked SQL** | Common, well-defined queries | Nothing — picks a tool | None |
| **Semantic Layer** | Ad-hoc analytics within a defined vocabulary | Cube JSON query | Low — constrained to defined measures/dimensions |
| **Custom SQL** | Truly novel questions outside any model | Raw SQL | Higher — mitigated by RAG + human approval |

::alert[**The sweet spot:** In production, you'd use Prebaked SQL for the top 20 questions (fast, minimum risk), the Semantic Layer for the next 80% of ad-hoc analytics (flexible, low risk), and Custom SQL as a last resort for truly novel questions (maximum flexibility, human approval required). The SOP (Step 7) can encode this preference hierarchy.]{type="success"}

## Verification

✅ Cube Core returns 5 cubes from `/v1/meta` on the EC2 instance
✅ A direct Cube query returns booking data from Aurora
✅ `deploy_cube_models.py --model-set initial` deploys baseline models via S3 + SSM
✅ `deploy_cube_models.py --model-set final` deploys full production models via S3 + SSM
✅ `deploy_semantic_layer_toolset.py` deploys the Lambda with 2 tools (cube_meta_tool, cube_query_tool)
✅ `deploy_semantic_layer_stack.py` deploys the parallel Gateway, Runtime, and Amplify UI
✅ The semantic layer UI (`agentic-analytics-semantic-ui`) is accessible and functional
✅ The agent calls `cube_meta_tool` first, then constructs a JSON query for `cube_query_tool`
✅ "Top customers by revenue" returns correct results
✅ Different tenants see different data (if RLS is configured)

## Troubleshooting

**Deploy script fails with "Could not resolve Cube private IP"**
- The CubeStack must be deployed as part of the CloudFormation stack. Check that `main-stack` has a `CubeStack` resource and that it completed successfully.

**`deploy_cube_models.py` fails with S3 or SSM errors**
- Verify that the `main-stack` CloudFormation outputs include `CubeConfigBucketName`, `CubeInstanceId`, and `CubePrivateIp`.
- Check that the EC2 instance is running and has the SSM agent installed (it should be configured by the CloudFormation stack).

**Smoke test fails — "cubes found: []"**
- The Cube data model YAML files haven't been deployed yet (Step 2). Run `deploy_cube_models.py --model-set initial` first.
- Check Cube container logs: `!docker logs $(docker ps -q --filter ancestor=cubejs/cube)`

**Agent doesn't use SemanticLayer tools**
- The agent may prefer Prebaked SQL tools for questions that match existing tool descriptions. Prefix your question with "Using the semantic layer" to guide tool selection.
- Verify the SemanticLayer target is `ACTIVE` in the Gateway console.

**Lambda timeout or connection error**
- The Lambda must be in VPC private subnets with the `LambdaSecurityGroup`, which has egress to port 4000 on the `CubeSecurityGroup`. Verify security group rules in the EC2 console.

**Cube returns "Error: <cube_name> not found"**
- The YAML model file may have a syntax error. Check Cube logs on the EC2 instance. YAML is indentation-sensitive — ensure consistent 2-space indentation.

**Parallel stack deployment fails**
- Verify that the existing stack (`config.env`) is fully deployed and functional before running `deploy_semantic_layer_stack.py`.
- Check that the Cognito User Pool and Aurora database are accessible from the new Gateway and Runtime.

## Summary

You deployed a semantic layer using Cube Core — the fourth data access pattern in the workshop. The agent discovers available measures and dimensions via `/meta`, constructs structured JSON queries, and Cube translates them to SQL. Multi-tenancy is enforced at the Lambda layer, consistent with all other toolsets. A parallel stack lets you compare the semantic layer agent side-by-side with the prebaked SQL agent. This gives you controlled flexibility: more ad-hoc than Prebaked SQL, more reliable than Custom SQL.

Congratulations — you've completed all the steps! → [Summary & Next Steps](../summary/)

## Reference Materials

- :link[Cube — Introduction]{href="https://cube.dev/docs/product/introduction" external=true}
- :link[Cube — Data Modeling with YAML]{href="https://cube.dev/docs/product/data-modeling/reference/cube" external=true}
- :link[Cube — REST API Reference]{href="https://cube.dev/docs/product/apis-integrations/rest-api/reference" external=true}
- :link[AgentCore Gateway — Adding Targets]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-building-adding-targets.html"}
- :link[AgentCore Gateway — Interceptors]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html"}
