---
title: "Step 6: Custom SQL for Ad-Hoc Queries"
weight: 23
---

## Learning Objectives

By the end of this step, you will:
- Deploy the Custom SQL toolset that generates SQL dynamically using Glue schema + Bedrock KB RAG
- Explore the `business-context.md` document and understand how it reduces SQL hallucination
- Test semantic search in the Bedrock Knowledge Base console
- Experience the human-in-the-loop approval workflow

## The Problem

Your agent has 29 prebaked tools, but tenants always ask questions that don't map to any existing tool: "What's the average booking duration by unicorn breed?" Building a tool for every possible question isn't scalable.

## The Solution: Custom SQL with RAG

The Custom SQL toolset lets the agent **generate SQL dynamically** — but with multiple hallucination mitigation layers:

1. **Full schema from :link[AWS Glue Data Catalog]{href="https://docs.aws.amazon.com/glue/latest/dg/catalog-and-crawler.html"}** — every table, column, and type
2. **Business context from :link[Bedrock Knowledge Base]{href="https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html"} (RAG)** — descriptions, rules, and sample SQL
3. **Human approval** — the user reviews a query plan before execution
4. **SELECT only** — dangerous patterns blocked at the Lambda level

## Lab Procedures

### Step 6.1: Explore the Business Context Document

Before deploying, understand what powers the RAG. Open :code[dataset/docs/business-context.md]{showCopyAction=true} in the Code Editor.

This document contains:
- **Schema descriptions** — not just column names, but what they mean (e.g., "a full-day booking = 8 hours")
- **Business rules** — "revenue = SUM(total_cost)", "active unicorns = status != 'retired'"
- **Sample SQL** — correct JOIN patterns for complex multi-table queries

::alert[**Why this matters:** Schema alone tells the LLM that `bookings.start_datetime` exists, but not that "booking duration" means `end_datetime - start_datetime` in hours. The business context fills this gap. The sample SQL is especially important — it gives the LLM correct join patterns to follow, significantly reducing hallucination on complex queries.]{type="info"}

### Step 6.2: Explore the Knowledge Base in the Console

If you do not have the AWS UI console open, you can open it from the AWS workshop studio dashboard (if the lab is done on sandbox account in AWS event) by clicking "Open AWS console" link on the left pane.

1. Open **AWS Console** → **Amazon Bedrock** → **Knowledge bases** or navigate straight to the :link[Bedrock Knowledge Bases UI console]{href="https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/knowledge-bases" external=true}
2. Find the knowledge base (named `agentic-analytics-kb`)
3. Click on it
4. Look at the **Data source** section and click `business-context-source` data source

You'll see:
- **Data source type**: Amazon S3
- **S3 bucket**: Contains `business-context.md`
- **Chunking strategy**: Hierarchical (parent: 1000 tokens, child: 300 tokens)

::alert[**Hierarchical chunking** splits the document into large parent chunks (1000 tokens) and smaller child chunks (300 tokens). When the agent searches, it matches on child chunks (precise) but retrieves the parent chunk (more context). This gives the LLM both precision and surrounding context — critical for understanding SQL patterns that span multiple paragraphs.]{type="info"}

### Step 6.3: Test Semantic Search in the Console

1. In the Knowledge Base console, click `agentic-analytics-kb` breadcrumbs menu on top to navigate up
2. Click **Test knowledge base** (right panel)
3. Select any available **Claude** model under the `Anthropic` category (a Sonnet or Opus model is ideal), and click `Apply`
4. Try these queries:
   - "How to calculate booking duration?" — should return the section about `end_datetime - start_datetime`
   - "What is the relationship between bookings and unicorns?" — should return JOIN patterns
   - "How is revenue calculated?" — should return the revenue formula

This is the same RAG retrieval the agent uses when generating SQL.

::alert[**Updating the Knowledge Base:** To add new business context, upload files to the S3 data source bucket and click **Sync** in the data source section (or call the :link[StartIngestionJob API]{href="https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_StartIngestionJob.html"}). The new content will be chunked, vectorized, and stored in Aurora PostgreSQL's pgvector extension.]{type="info"}

### Step 6.4: Understand the SOP — Text-to-SQL Workflow

Open :code[unicorn_rental_analytics.sop.md]{showCopyAction=true} and review:

**Lines 83-122 — Text-to-SQL Workflow (Human-in-the-Loop):** This is the most complex workflow in the SOP. Key constraints:
- *"You MUST call text_to_sql_tool to get schema context first"* — the agent retrieves Glue schema + RAG context before generating SQL
- *"You MUST present a business-level query plan for approval"* — the user sees a tree-format plan in business language, not raw SQL
- *"You MUST STOP and wait after presenting the query plan"* — human approval required before execution
- *"You MUST NOT execute SQL that modifies data"* — SELECT only

**Lines 96-112 — Approval Card Format:** The SOP defines the exact JSON format for the approval card, including the tree-format query plan with `└─` connectors. The UI parses this format to render the approval buttons.

**Line 44 — Tool Priority:** *"You MUST NOT use text-to-sql when a specific tool exists."* This prevents the agent from generating SQL for questions that already have prebaked tools — reducing cost, latency, and hallucination risk.

::alert[**The SOP creates a layered defense against SQL hallucination:** (1) Prebaked tools handle known queries — minimum hallucination risk. (2) For unknown queries, Glue schema + RAG context ground the SQL generation. (3) Human approval catches any remaining errors. (4) Lambda-level validation blocks dangerous patterns. Each layer reduces risk independently.]{type="info"}

### Step 6.5: Uncomment the Custom SQL toolset and deploy

Open :code[/workshop/agentic-analytics/app/agentcore_strands/agentcore-topup-stack.yaml]{showCopyAction=true} and find the **Step 6** fence:

```
# ===== UNCOMMENT FROM HERE (Step 6: Custom SQL toolset ...) =====
...
# ===== UNCOMMENT TO HERE (Step 6) =====
```

Uncomment everything between the markers (this brings up `CustomSqlLambda`, its role + permission, and the `CustomSqlTarget` with its three tools: `text_to_sql_tool`, `get_schema_context_tool`, `execute_sql_tool`). The Lambda already imports the Glue database name and the Bedrock Knowledge Base id from the base stack's exports — no values to wire by hand.

::alert[**Tip — uncomment the whole block at once.** Don't delete each `#` by hand. The Code Editor is VS Code: click the first line *inside* the fence, then **Shift+click** the last line inside it to select the whole block, and press **Cmd + /** (macOS) or **Ctrl + /** (Windows/Linux) to toggle the comments off for every selected line in one go. Select only the lines **between** the two `UNCOMMENT` markers — not the marker lines themselves.]{type="info"}

Then deploy:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

When it finishes, the Gateway has a third target, `CustomSQL`.

### Step 6.6: Test Custom SQL Queries

If you're not logged in to the chat UI, log in as:

| Field | Value |
|-------|-------|
| Username | `orion.moonshadow@example-mythicalunicorns.com` |
| Password | `Unicorn123!` |

::alert[**Use the right user:** In case you intend to run the below queries after deploying the AgentCore Policy and RLS in step 7, you MUST use users with "analyst" or "rental_admin" or "saas_admin" type to be able to use the custom SQL. The user above (Orion Moonshadow) is an analyst role. If you run this step before deploying the components in step 7, the user does not matter.]{type="info"}

::alert[**Start fresh:** It is best to clear the chatbot conversation from the previous step by clicking the small bin icon next to the chat input field or by refreshing the application demo browser tab.]{type="info"}

Try these ad-hoc analytics questions — none are covered by the 29 prebaked tools, so the agent will use `CustomSQL___text_to_sql_tool` and show an **approval card** for each:

**Query 1: Booking duration by breed**
- Ask: "What's the average booking duration by unicorn breed?"
- Tests: `bookings × unicorns` JOIN, EXTRACT duration calculation

**Query 2: Customer breed diversity**
- Ask: "Which customers have booked the most different unicorn breeds?"
- Tests: `customers × bookings × unicorns` three-table JOIN, COUNT DISTINCT

**Query 3: Revenue by unicorn color**
- Ask: "Which unicorn colors generate the most revenue?"
- Tests: `unicorns × bookings × transactions` three-table JOIN, color aggregation

**Query 4: Individual vs organization customers**
- Ask: "Compare the average booking cost between individual and organization customers"
- Tests: `customers × bookings` JOIN, customer_type grouping, conditional aggregation

### Step 6.7: The Human-in-the-Loop Flow

As you have experienced in the tests, for each query, an approval card appears:

1. **Review the query plan** — it describes what the query will do in business terms (not raw SQL)
2. Click **"Approve & Run"** to execute
3. The agent calls `CustomSQL___execute_sql_tool` and returns formatted results with insights

You can also **Cancel** if the query doesn't make sense.

### Security Guardrails in Custom SQL

The `execute_sql_tool` enforces:
- Only `SELECT` queries allowed
- `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER` patterns blocked
- Results limited to 100 rows
- RLS policies filter data per tenant (after Step 7)

## Verification

- `business-context.md` contains schema descriptions, business rules, and sample SQL
- Semantic search in the KB console returns relevant chunks
- After uncommenting Step 6 and `make deploy`, the Gateway has a `CustomSQL` target with 3 tools
- Ad-hoc questions show the approval card with a business-level query plan
- Approved queries return formatted results with insights
- Multiple query types work: JOINs, aggregations, time patterns, conditional grouping

## Troubleshooting

**Agent uses Custom SQL for questions that have prebaked tools**
- The SOP already handles this — it constrains the agent to use specific tools before falling back to Custom SQL (see SOP line 44).

**Approval card doesn't appear**
- Ensure the UI is running and connected to the agent.
- The approval card only appears in the React UI, not in CLI invocations.

**SQL execution returns empty results**
- The generated SQL may have incorrect column names. Check the query plan and cancel if it looks wrong.

## Summary

You deployed the Custom SQL toolset — the third integration pattern. The agent combines full schema from Glue with business context from RAG to generate SQL, then asks for human approval before executing. The business-context.md document is the key to reducing hallucination — it provides the "why" behind the schema that the LLM needs to generate correct queries.

Next, you'll add role-based access control → [Step 7: Agent Access Policy](../../03-security-multitenancy-governance/07-agent-access-policy/)

## Reference Materials

- :link[Amazon Bedrock Knowledge Bases]{href="https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html"}
- :link[Bedrock KB — Chunking Strategies]{href="https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking-parsing.html"}
- :link[Bedrock KB — StartIngestionJob API]{href="https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_StartIngestionJob.html"}
- :link[AWS Glue Data Catalog]{href="https://docs.aws.amazon.com/glue/latest/dg/catalog-and-crawler.html"}
