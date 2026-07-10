---
title: "Step 1: Test a Basic Agent (Exercise)"
weight: 15
---

## Learning Objectives

By the end of this step, you will:
- Understand the three components of a Strands agent: Model, Tools, and Prompt
- Build a minimal agent that connects directly to Aurora PostgreSQL
- See how the LLM selects the right tool based on the user's question

::alert[**This is a learning exercise.** The agent you build here connects directly to the database — it won't be used in the actual workshop deliverable. Starting from Step 2, you'll build the actual agent that uses AgentCore Gateway instead.]{type="warning"}

## Why Build a Local Agent First?

Your tenants need answers. Lyra Starwhisper at Mythical Unicorns wants to know her top customers by revenue — but she'd have to email the data team and wait half a day. As the Timely-Unicorn SaaS platform team, let's build something better for her and all of the future tenants with the same need.

Before deploying to the cloud, it's important to understand how an AI agent works at its core. :link[Strands Agents]{href="https://strandsagents.com/latest/" external=true} is an open-source SDK from AWS that takes a :link[model-driven approach]{href="https://aws.amazon.com/blogs/opensource/strands-agents-and-the-model-driven-approach/" external=true} — instead of writing complex routing logic, you define tools and a prompt, and the LLM decides which tools to call and in what order.

An agent has three components:

| Component | What It Does | Example |
|-----------|-------------|---------|
| **Model** | The LLM that reasons about user queries | Claude on Amazon Bedrock |
| **Tools** | Python functions the agent can call | Database queries, API calls |
| **Prompt** | Instructions that guide the agent's behavior | "You are an analytics assistant" |

## Lab Procedures

### Step 1.1: Open the Local Agent File

```bash
cd /workshop/agentic-analytics
```

Open :code[exercises/basic_agent.py]{showCopyAction=true} in the Code Editor. This is a simplified agent that connects directly to Aurora PostgreSQL.

### Step 1.2: Configure the Bedrock Model (TODO 1.2)

The agent needs a foundation model. Find `TODO 1.2` in `exercises/basic_agent.py` — replace `None` with a :link[BedrockModel]{href="https://strandsagents.com/latest/user-guide/concepts/model-providers/amazon-bedrock/" external=true} using the model ID and region already shown in the hint.

::::expand{header="💡 Need help with TODO 1.2? Click to see the solution"}
:::code{language=python showCopyAction=true}
bedrock_model = BedrockModel(
    model_id="global.anthropic.claude-sonnet-4-6",
    streaming=True
)
:::
::::

### Step 1.3: Create a Database Tool (TODO 1.3)

Strands uses the `@tool` decorator to turn any Python function into an agent-callable tool. The agent reads the function's **docstring** to understand when to use it — this is how the LLM knows "if the user asks about top customers, call this function." You need to add the `@tool` decorator to the `get_top_customers` function.

Find `TODO 1.3` in `exercises/basic_agent.py` and add the decorator above the function definition:

::::expand{header="💡 Need help with TODO 1.3? Click to see the solution"}
:::code{language=python showCopyAction=true}
@tool
:::

One line. The docstring `"""Get the top customers by revenue..."""` already tells the LLM exactly when this tool is relevant.
::::

### Step 1.4: Create the Agent (TODO 1.4)

Wire the three components together. Find `TODO 1.4` — replace `None` with an :link[Agent]{href="https://strandsagents.com/latest/user-guide/concepts/agents/agent-loop/" external=true} using `bedrock_model`, a system prompt string, and `tools=[get_top_customers]`.

::::expand{header="💡 Need help with TODO 1.4? Click to see the solution"}
:::code{language=python showCopyAction=true}
agent = Agent(
    model=bedrock_model,
    system_prompt="You are a unicorn rental analytics assistant. Use the available tools to answer questions about the business.",
    tools=[get_top_customers]
)
:::
::::

### Step 1.5: Run the Agent

Save your changes in basic_agent.py. Back to the terminal, run the below commands. (If you opened a fresh terminal, activate the virtual environment first — it lives at `/workshop/.venv`.)

```bash
source /workshop/.venv/bin/activate
cd /workshop/agentic-analytics
python3 exercises/basic_agent.py
```

Expected output:

```
Agent: Based on the data, here are the top customers by revenue:

| Rank | Customer | Total Revenue |
|------|----------|--------------|
| 1    | Rvthelia Fxstorm | $3,165,561.20 |
| 2    | Example Fantasy Ecosystems | $2,731,004.59 |
...
```

### What Just Happened?

```
User: "Show me top customers by revenue"
  → Strands sends prompt + tool descriptions to Claude
    → Claude decides: "I should call get_top_customers"
      → Strands executes the @tool function
        → Function queries Aurora PostgreSQL
          → Results returned to Claude
            → Claude formats a human-readable response
```

You didn't write any if/else routing. The LLM chose the right tool based on the question and the docstring. This is the **model-driven** approach — the model is the orchestrator.

## Verification

- The agent runs without errors
- The response contains a formatted list with customer names and revenue figures
- The data comes from Aurora PostgreSQL — you can verify by checking the database directly

## Troubleshooting

**`ModuleNotFoundError: No module named 'boto3'` (or `strands`, `psycopg2`)**
- You're running with the system Python instead of the workshop virtual environment. Activate it first: `source /workshop/.venv/bin/activate`, then re-run.

**`botocore.exceptions.NoCredentialsError: Unable to locate credentials`**
- The EC2 instance role should provide credentials automatically. Verify with:
  ```bash
  aws sts get-caller-identity
  ```

**`psycopg2.OperationalError: could not connect to server`**
- Check that `AURORA_ENDPOINT` in `config.env` is correct.
- The EC2 security group must have ingress to the Aurora security group on port 5432 (this is configured by CloudFormation).

**Agent responds but with no data / empty table**
- Verify the database has data: check the CloudFormation stack events for the `DatabaseInitStack` — it should show `CREATE_COMPLETE`.

## The Limitation

This agent runs locally in this development machine with no built-in session isolation and high-availability. In the actual agent deployment in this workshop, you'd deploy the agent into AgentCore Runtime, support 20+ tools, need authentication, and want centralized management.

That's what **AgentCore** solves — next step.

## Summary

You built a minimal Strands agent with three components: a Bedrock model, a `@tool` function, and a system prompt. The LLM autonomously decided which tool to call based on the user's question. This model-driven approach is the foundation for everything that follows.

Next, you'll move from a single machine deployment to a highly available deployment → [Step 2: Deploy Agent Infrastructure](../02-deploy-agent-infra/)

## Reference Materials

- [Strands Agents SDK Documentation](https://strandsagents.com/latest/)
- [Strands Agents — Tools](https://strandsagents.com/latest/user-guide/concepts/tools/tools-overview/)
- [Amazon Bedrock Model Access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
- [BedrockModel Configuration](https://strandsagents.com/latest/user-guide/concepts/model-providers/amazon-bedrock/)
