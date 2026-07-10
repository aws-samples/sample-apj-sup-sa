---
title: "Summary & Next Steps"
weight: 80
---

## What You Built

Congratulations! You've built a self-service analytics system for Timely-Unicorn and learned what it takes to get an agentic system into production. Your tenants can now ask questions in plain English and get instant, accurate, secure answers — no SQL required.

| Step | What You Built | Business Value |
|------|---------------|---------------|
| 1 | Test basic agent (exercise) | Understand how LLM selects tools |
| 2 | Agent infrastructure | Gateway + Runtime — production foundation |
| 3 | React chat UI | Business users can interact naturally |
| 4 | Prebaked SQL toolset | Analytics tools backed by database Views |
| 5 | API integration toolset | Connect agent to existing business APIs |
| 6 | Custom SQL toolset | Ad-hoc queries with Glue + Bedrock KB RAG |
| 7 | Multi-Tenant Isolation & User Access | Cedar policies + JWT → PostgreSQL RLS |
| 8 | Guardrails | Off-topic blocked, PII filtered |
| 9 | Observability | Trace agent behavior end-to-end |
| 10 | Evaluation | Measure quality with LLM-as-a-Judge |
| Optional | Semantic Layer (Cube Core) | Structured ad-hoc analytics — flexible yet reliable |
| Optional | Voice | Spoken interface to the same secured agent |

## The Security Layers

| Layer | What It Controls | How It Works |
|-------|-----------------|-------------|
| **Authentication, Authorization, JWT propagation** | access control | Restrict access to authenticated user and role-based access control with propagated claim |
| **Cedar Policy** | Which tools each role can access | Gateway hides tools from unauthorized users |
| **Row-Level Security** | Which data rows each tenant sees | PostgreSQL filters rows via JWT claims |
| **Bedrock Guardrails** | Content safety and topic boundaries | Blocks off-topic, PII, and schema leakage |
| **SOPs** | Agent behavior and response format | RFC 2119 constraints guide LLM decisions |
| **AgentCore Isolation** | Session isolation | AgentCore Runtime lets you isolate each user session and safely reuse context across multiple invocations in a user session. |

## Key Takeaways

1. **Start simple, add capabilities progressively** — local agent → Gateway → Runtime → features, one uncommented CloudFormation section at a time
2. **The whole agent layer is infrastructure-as-code** — Gateway, Runtime, Memory, toolsets, Cedar policies, and guardrail all live in one CloudFormation template you reviewed, versioned, and redeployed with `make deploy` (no imperative SDK scripts, no `agentcore` CLI for the runtime lifecycle)
3. **Model-driven approach** — define tools and prompts, let the LLM orchestrate (don't write routing logic)
4. **SOPs make agents reliable** — structured behavior beats ad-hoc prompt engineering
5. **Deterministic security at the infrastructure level** — JWT claim propagation, Cedar policies, and RLS to implement security with role-based access control and multi-tenant isolation
6. **Defense in depth** — multiple security controls to protect against diverse attack vectors
7. **Human-in-the-loop for risky operations** — query plan review and approval before execution, in natural language
8. **Observability is not optional** — you can't improve what you can't measure

## Applying This to Your SaaS

The pattern in this workshop maps to a real SaaS challenge:

| Workshop Pattern | Your SaaS Application |
|-----------------|----------------------|
| **Pool model** (shared DB + agent) | Scale to hundreds of tenants without per-tenant infrastructure |
| **RLS via JWT** | Enforce data isolation at the database level |
| **Cedar policies** | Let tenants configure their own RBAC without custom code per tenant |
| **Guardrails** | Protect your platform from misuse — off-topic, PII |
| **Human-in-the-loop** | Let power users run custom queries while maintaining control |
| **Observability** | Debug tenant-reported issues with full trace visibility |

The :link[SaaS Lens]{href="https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/saas-lens.html"} of the AWS Well-Architected Framework provides additional guidance on building secure, scalable multi-tenant applications.

## Explore More

::alert[**Take the code with you.** The complete source for everything you built — agent, Lambda toolsets, CloudFormation, UI, and this workshop content — is available at :link[aws-samples/sample-apj-sup-sa]{href="https://github.com/aws-samples/sample-apj-sup-sa/tree/main/agentic-workloads/agentic-analytics" external=true} (the `agentic-workloads/agentic-analytics` directory). Clone it to keep after your event account expires, and adapt the patterns to your own SaaS.]{type="success"}

### Extend This Workshop
- Improve agent SOP to handle more scenarios
- Add more tools for more capabilities

### Related AWS Services and Features
- :link[AgentCore Memory]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html"} — Short-term and long-term memory for personalized experiences
- :link[AgentCore Identity]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html"} — OAuth2 credential management for external service access
- :link[AgentCore Browser]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html"} — Managed browser sessions for web automation
- :link[AgentCore Code Interpreter]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html"} — Secure sandbox for code execution
- :link[AgentCore Evaluations]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/built-in-evaluators-overview.html"} — Automated agent quality assessment

### Learn More
- :link[Amazon Bedrock AgentCore Documentation]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/"}
- :link[Strands Agents SDK]{href="https://strandsagents.com/latest/" external=true}
- :link[AI Agents in Enterprises: Best Practices]{href="https://aws.amazon.com/blogs/machine-learning/ai-agents-in-enterprises-best-practices-with-amazon-bedrock-agentcore/" external=true}
- :link[Dynamic Text-to-SQL for Enterprise Workloads]{href="https://aws.amazon.com/blogs/machine-learning/dynamic-text-to-sql-for-enterprise-workloads-with-amazon-bedrock-agents/" external=true}
- :link[Cedar Policy Language]{href="https://www.cedarpolicy.com/" external=true}
- :link[How BGL uses Claude Agent SDK on AgentCore for analytics]{href="https://aws.amazon.com/blogs/machine-learning/democratizing-business-intelligence-bgls-journey-with-claude-agent-sdk-and-amazon-bedrock-agentcore/" external=true}
- :link[Text-to-SQL at Parcel Perform]{href="https://aws.amazon.com/blogs/machine-learning/democratize-data-for-timely-decisions-with-text-to-sql-at-parcel-perform/" external=true}

## Clean Up

If running in Workshop Studio, all resources — including the AWS account itself — are automatically cleaned up when the event ends. No action needed.

For manual cleanup (own account), delete the two CloudFormation stacks in this order:

1. **The AgentCore top-up stack** (deletes the Gateway, Runtime, Memory, toolsets, Cedar policies, guardrail, and the container build chain — everything you built):
```bash
aws cloudformation delete-stack --stack-name agentic-analytics-agentcore --region us-east-1
aws cloudformation wait stack-delete-complete --stack-name agentic-analytics-agentcore --region us-east-1
```

2. **The base stack** (deletes Aurora, Glue, Cognito, EC2, Bedrock KB). In a Workshop Studio event this stack is named `main-stack`; if you deployed to your own account, use the stack name you chose in your `create-stack` command:
```bash
aws cloudformation delete-stack --stack-name main-stack --region us-east-1
```

::alert[**Delete the top-up stack first.** It imports values (secrets, KB id) from the base stack via `Fn::ImportValue` — CloudFormation won't let you delete the base stack while the top-up still imports from it. Once the top-up is gone, the base stack deletes cleanly. Because the whole agent layer is now CloudFormation, there are **no** leftover AgentCore resources to hunt down in the console.]{type="warning"}

::alert[If you also ran the optional **Cube** lab, delete its parallel `semantic-layer` stack before the base stack too (it was created by `deploy_semantic_layer_stack.py`).]{type="info"}

---

Thank you for completing this workshop! We hope Mythical Unicorns and Mythic Unicorns are happy with their new analytics assistant — and you're ready to onboard more tenants.
