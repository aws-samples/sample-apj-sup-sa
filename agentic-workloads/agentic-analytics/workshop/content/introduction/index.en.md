---
title: "Introduction: The Timely-Unicorn Story"
weight: 5
---

## The Business Problem

**Timely-Unicorn** is a multi-tenant SaaS platform for unicorn rental businesses. Two rental companies operate on the platform:

| Business | Staff | Unicorns | Customers |
|----------|-------|----------|-----------|
| **Mythical Unicorns** | Lyra Starwhisper (admin), Stella Moonbeam (staff), Luna Starlight (staff), Orion Moonshadow (analyst) | ~50 unicorns | ~85 customers |
| **Mythic Unicorns** | Aria Skybloom (admin), Sarah Williams (staff), Michael Thompson (staff), David  Brown (staff), Elena Rodriguez (analyst) | ~50 unicorns | ~65 customers |

Each business manages their own fleet of unicorns, customers, bookings, and transactions on the platform. Their staff handle day-to-day operations (bookings, customer service) and their analysts need data insights (revenue trends, customer segmentation, unicorn utilization). But there's a problem:

::alert[The analysts have no bandwidth to serve every query from executives, partners, staff, and end customers with hand-written SQL. The staff need a faster way to check unicorn availability and create bookings to serve customers on time. And the executives want quick, constant business health checks. Everyone wants answers *now*.]{type="warning"}

## What You'll Build

::alert[**Source code:** This workshop is built from a public reference implementation. Browse the full agent, infrastructure, and UI code at :link[aws-samples/sample-apj-sup-sa]{href="https://github.com/aws-samples/sample-apj-sup-sa/tree/main/agentic-workloads/agentic-analytics" external=true} — useful for reference during the labs and for adapting the patterns to your own use case afterward.]{type="info"}

An AI-powered analytics assistant that understands natural language and translates it into the right database query. Here's what a typical interaction looks like:

> **Analyst:** "Who are my top 5 customers by revenue?"
>
> **Agent:** *Calls `get_top_customers_tool` → queries Aurora PostgreSQL → formats results*
>
> | Rank | Customer | Total Revenue |
> |------|----------|--------------|
> | 1 | Mfaranwe Quoralis | $45,750 |
> | 2 | Cttharion Dlstormrider | $38,200 |
> | ... | ... | ... |

But self-service analytics for a multi-tenant SaaS platform isn't just about answering questions. You also need:

- **Role-based access** — analysts can read data; only staff or admins can create bookings
- **Tenant isolation** — Mythical Unicorns can't see Mythic Unicorns' data
- **Content safety** — the agent shouldn't answer questions about medical or legal advice
- **Custom queries** — when the prebaked SQL tools aren't enough, generate SQL with human approval
- **Observability** — monitor quality, debug wrong answers, measure improvement

## Four Ways to Connect Your Agent to Data

This workshop demonstrates four integration patterns that cover most SaaS data access needs. Some patterns demonstrate trade-off between **reliability** and **flexibility**:

### 1. API Integration (APIInteg toolset) — Connect to Existing Services

Most SaaS platforms already have APIs for transactional operations — booking, billing, notifications. Rather than rebuilding these, you connect the agent to your existing APIs.

1. Each API endpoint maps to a **tool** (e.g., `create_booking_tool`)
2. The LLM **extracts API inputs** from the conversation (customer name, date, unicorn)
3. If inputs are incomplete, the agent asks for the missing information
4. The tool calls the API and returns the result

This method provides most reliability since it reuses your well-tested API functionalities already functioning before. However, this restricts the flexibility in accessing data because of its dependency with API implementation layer in terms of data access capabilities.

AgentCore Gateway supports calling Lambda, API Gateway, or any OpenAPI endpoint. In this workshop, we use Lambda directly due to current limitation in AgentCore Gateway - private API Gateway support for passing OAuth tokens for RLS purpose that may add up complexity with work around (e.g. adding proxy Lambda)

### 2. Prebaked SQL (PrebakedSQL toolset) — Reliable, Minimum Hallucination Risk

For the 80% of questions your users ask daily — "top customers", "revenue trends", "unicorn utilization" — you don't need the LLM to generate SQL at all. Instead:

1. Your database team creates **Views** for commonly requested queries
2. Each view maps to a **tool** with a clear name and description (e.g., `get_top_revenue_customers_tool`)
3. The LLM's only job is to **pick the right tool** and **extract arguments** from the user's question (e.g., `limit=5`)
4. If arguments are missing, the agent asks the user for clarification

This minimizes SQL hallucination — the LLM never sees or generates SQL. The complex joins, window functions, and business logic are handled by your database team, not the LLM.

This method can complement the above existing API integration by adding more data access capability beyond the what the current API layer can perform, while still being able to minimize the risk of hallucination.

### 3. Custom SQL (CustomSQL toolset) — Most Flexible, with Guardrails

For the 20% of questions that don't map to any prebaked tool — "average booking duration by unicorn breed" — the agent generates SQL dynamically. This uses text-to-SQL technique that takes in schema and context to convert natural language query into SQL statement. This is powerful but risky, so we add multiple hallucination mitigation layers:

1. **Full schema from :link[AWS Glue Data Catalog]{href="https://docs.aws.amazon.com/glue/latest/dg/catalog-and-crawler.html"}** — the agent knows every table and column
2. **Business context from :link[Bedrock Knowledge Base]{href="https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html"} (RAG)** — not just schema, but *what the data means*. Our `business-context.md` includes column descriptions ("a full-day booking = 8 hours"), business rules, and sample SQL for complex joins. RAG retrieves the most relevant chunks for each question.
3. **Human approval** — the agent shows a business-level query plan before executing. The user reviews and approves, edits, or cancels.
4. **SOP driven flow** - the agent is instructed with clear SOP (standard operating procedure) to handle less-ideal flows, such as when the request comes with imcomplete data. The agent can ask user back for the missing information instead of making up the information (hallucination).
5. **SELECT only** — dangerous patterns (DROP, DELETE, UPDATE) are blocked at the Lambda level

The combination of schema + business context + sample SQL significantly reduces hallucination compared to schema-only approaches, because the LLM has context to help interpret field names and link back to the business context in addition to the examples of correct joins to follow.

::alert[**Context Window Limitation:** LLMs have context window limitation, which may not allow the full database schema to be loaded into a single LLM call. As a solution, you can pick LLM with larger context window to fit the full schema into a single LLM call, reducing risk of LLM generating wrong SQL statement due to full context it has. Alternatively, you may consider RAG approach for the schema. However, multiple supporting components are needed for the latter to reduce the risk of wrong SQL generation due to limited context, for example by having business/semantic context and SQL samples. Yet another alternative is to not let LLM generate SQL at all, and delegate that to the semantic layer (see below). ]{type="warning"}

### 4. Custom SQL with Semantic Layer — Modest Flexibility, with Reduced Hallucination Risk

When text-to-SQL pattern does not work, you can consider using the LLM to generate a higher level definition of the query, while letting the actual SQL statements be generated from semantic layer. The idea is to let LLM generate high level query that does not involve joins, as tables are abstracted away and the LLM will see entities, their fields, and available measures instead of database tables. The complex joins are hidden from LLM and are prebaked in the semantic layer.

A semantic layer has visibility of the database actual schema, either by manual input or by fetching it from the database. It can be configured to expose certain measures, that are backed by predefined SQL queries. LLM will generate higher level queries that will be translated by the semantic layer into actual SQL queries.

## The User Roles

| Role | Can Do | Can't Do |
|------|--------|----------|
| **SaaS Admin** (SaaS Admin) | All data access across all tenants | - |
| **Rental Admin** (e.g., Lyra Starwhisper) | All data read + create bookings + custom SQL analytics | See other tenants' data |
| **Staff** (e.g., Stella Moonbeam) | All data read + create bookings | Use text-to-SQL (too risky) |
| **Analyst** (e.g., Orion Moonshadow) | All data read + custom SQL analytics | Create bookings (read-only) |

## The Data

Your Aurora PostgreSQL database contains sample data across nine core tables:

| Table | Records | Description |
|-------|---------|-------------|
| `accounts` | 2 | Unicorn rental businesses (tenants) |
| `unicorns` | 100 | Fleet with breeds, hourly rates, maintenance status |
| `customers` | 500 | Business customers across all tenants |
| `bookings` | ~14,000 | Rental bookings with dates, costs, special requests |
| `transactions` | ~14,000 | Financial transactions linked to bookings |
| `unicorn_availability` | ~31,000 | Availability status history (insert-only audit trail) |
| `users` | 10 | Staff and admin users across tenants |
| `subscription_plans` | 5 | SaaS subscription tiers |
| `subscription_tracker` | ~17,500 | Monthly subscription usage tracking |

Plus **20+ pre-built database views** for common analytics queries (revenue trends, customer segmentation, unicorn utilization, maintenance schedules, and more).

## Workshop Flow

You'll build progressively — each step adds one capability:

```
Step 0: Environment setup
  ↓
Step 1: Test basic agent (exercise, direct DB)
  ↓ deploy production infrastructure
Step 2: Gateway + Runtime (empty, no toolsets)
  ↓ add user interface
Step 3: Chat UI (React, streaming)
  ↓ add first toolset
Step 4: Prebaked SQL (20+ tools, database Views)
  ↓ add API integration
Step 5: Integrate existing APIs (booking creation)
  ↓ add custom queries
Step 6: Custom SQL (Glue + Bedrock KB RAG + human approval)
  ↓ add access control + data isolation
Step 7: Multi-Tenant Isolation & User Access (Cedar + JWT → RLS)
  ↓ add content safety
Step 8: Guardrails (topic + PII + schema protection)
  ↓ add monitoring
Step 9: Observability (traces, spans, logs)
  ↓ add quality measurement
Step 10: Evaluation (on-demand + continuous)

Optional labs (after the core path):
  • Semantic Layer — Cube Core (a more structured ad-hoc pattern)
  • Voice — talk to your data
```

Ready? Let's set up your environment → [Step 0: Getting Started](../01-agent-and-infrastructure/00-getting-started/)

::alert[**Your role in this workshop:** You're the Timely-Unicorn SaaS platform engineering team. In Steps 0-3, you build the core agent infrastructure. In Steps 4-6, you add toolsets and behavior. From Step 7 onward, you harden for production with security, observability, and evaluation.]{type="info"}

::alert[**Workshop vs Production:** This workshop deploys single-AZ infrastructure to minimize cost and deployment time. In production, you would use multi-AZ Aurora, redundant NAT gateways, and CloudFront distribution for the UI. The security patterns (RLS, Cedar policies, Guardrails) are meant for path-to-production education, but are not intended to be used in production as is without thorough review and adjustments to your own use case and requirements.]{type="warning"}

::alert[**SaaS Architecture:** This workshop follows the :link[pool model]{href="https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/pool-model.html"} — all tenants share the same database, agent, and Gateway. Isolation is enforced at the data layer (RLS) and policy layer (Cedar), not by provisioning separate infrastructure per tenant. This is the most cost-efficient model for SaaS startups scaling from 2 to 200 tenants. However, the security measure has to be implemented correctly and carefully to avoid unintended data leak at multipler layers.]{type="info"}
