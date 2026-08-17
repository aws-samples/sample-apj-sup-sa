## AWS APJ Startup Samples

Welcome. This repository has been prepared by Startup Solution Architects in AWS APJ to help startups easily discover additional resources, including sample code, workshops, and demos.

We hope this repository helps you navigate the available options and maximize the value of running on AWS. As always, if you have any questions, please don't hesitate to contact your local AWS startup team. If you're not already connected with them, you can reach out here:
https://aws.amazon.com/startups/contact-us


## Cloning

One sample is mounted as a git submodule, so clone with `--recurse-submodules` to get
its content:

```bash
git clone --recurse-submodules https://github.com/aws-samples/sample-apj-sup-sa.git

# already cloned without it?
git submodule update --init --recursive
```

Without this the submodule directory is present but empty. Everything else in the
repository is checked out normally.

## What's Inside

The repository is organized by domain. Below is a summary of the areas covered:

| Domain | Areas Covered |
|--------|---------------|
| [Agentic Workloads](./agentic-workloads) | Agentic Patterns, Model Selection & Agent Platform |
| [Agentic Coding](./ai-coding-assistants) | Kiro, Claude Code, AI-DLC Practices, Kiro Autonomous & Frontier Agents |
| [Analytics](./analytics) | OpenSearch, Ingestion, Streaming, Processing, Cloud Data Warehouse, Data Lake, S3 Tables, QuickSight |
| [Databases](./databases) | Relational, Non-relational, Timestream, Vector Databases |
| [AI Infra](./ai-infra) | SageMaker, Fine-tuning, Inference, Training |
| [Modern Applications](./modern-applications) | AI on EKS, ML Orchestration, Containers, Serverless & Event-Driven Architecture |
| [Security & Compliance](./security) | Identity, IDP, Compliance, Cloud and Data Sovereignty |
| [Amazon Connect](./connect) | Voice, Call Centers & Back Offices |

## Sample catalogue

Every sample in this repository, with a short summary of the problem it solves, so you can
find what you need without opening each folder.

> **Adding a new sample?** Please add a row here in the same pull request, so it stays
> discoverable. The CoP STO for the domain can help if you are unsure where it fits.

### [Agentic Workloads](./agentic-workloads)

| Sample | What it solves |
|--------|----------------|
| [agentic-analytics](./agentic-workloads/agentic-analytics) | Natural-language and voice self-service analytics for multi-tenant SaaS, so business users get answers without going through the data team. Tenant isolation is enforced in the data path rather than the prompt: Cognito JWT claims drive Aurora PostgreSQL row-level security, with Cedar policies gating tool access at the AgentCore Gateway. |
| [agentic-app-starter-kit](./agentic-workloads/agentic-app-starter-kit) | Boilerplate for an agentic application with long-term memory that runs locally first and deploys to Amazon ECS. Built deliberately on open interfaces — OpenAI-compatible model calls, MCP for tools, Mem0 for memory, OpenTelemetry for tracing — so any layer can be swapped without rewriting the agent. |
| [agentic-bedrock-benchmarking](./agentic-workloads/agentic-bedrock-benchmarking) | Web app for comparing Amazon Bedrock foundation models **against each other** on the same prompt, when latency, quality, and cost all matter to the choice. Runs models in parallel and reports time-to-first-token, end-to-end latency, token counts, and USD cost, then ranks the answers with a configurable LLM-as-judge. |
| [agentic-payments-mpp-workshop](./agentic-workloads/agentic-payments-mpp-workshop) | Workshop on machine-to-machine paid agents, for when one agent must pay another per call. Two agents transact over Stripe's Machine Payments Protocol using Shared Payment Tokens on Amazon Bedrock AgentCore, with a mock mode so the flow runs before you have Stripe keys. |
| [ambient-voice-qa](./agentic-workloads/ambient-voice-qa) | Hands-free voice agent that walks factory and warehouse workers through quality-inspection checklists, for jobs where both hands stay on the equipment. It reads each step aloud, captures the spoken measurement, validates it against the engineering threshold in real time, and flags anomalies as they occur. |
| [bedrock-agentcore-temporal-dag](./agentic-workloads/bedrock-agentcore-temporal-dag) | Runs multi-agent workflows as a dependency-ordered DAG across independently deployed AgentCore Runtimes. The orchestrator uses **no LLM** — it is a deterministic runner, with Temporal Cloud providing retries, timeouts, and workflow visibility, so failures are debuggable rather than probabilistic. |
| [bedrock-image-url-bridge](./agentic-workloads/bedrock-image-url-bridge) | Makes plain `http(s)` image URLs work with Amazon Bedrock, which accepts only `s3://` or inline `data:` URIs on the OpenAI-compatible `bedrock-mantle` endpoint and raw bytes on Converse. Rewrites URLs to inline data URIs behind an SSRF guard, with an optional client-side resize that cuts vision-token cost on large photos. |
| [bedrock-mantle-samples](./agentic-workloads/bedrock-mantle-samples) | Superseded and frozen — use [sample-per-model-bedrock](https://github.com/aws-samples/sample-per-model-bedrock) instead, which covers these model families plus the `bedrock-runtime` endpoint. Kept unchanged so previously shared links keep resolving. |
| [bedrock-ramp-testing](./agentic-workloads/bedrock-ramp-testing) | Finds the throughput your Amazon Bedrock account can actually sustain, by following AWS's documented TPM/RPM ramp-up procedure instead of discovering the ceiling as 503 errors in production. Includes a dry-run mode and a hard request cap, because the test spends real inference money. |
| [bedrock-service-tier-benchmark](./agentic-workloads/bedrock-service-tier-benchmark) | Measures what Amazon Bedrock's **service tiers** cost or save in latency for a given model, comparing `default`, `flex`, and `priority` head to head. Built on AWS Labs LLMeter with a request-paced scheduler that stays inside account limits, and outputs a self-contained HTML report. |
| [claude-code-on-agentcore](./agentic-workloads/claude-code-on-agentcore) | Hosts Claude Code on Amazon Bedrock AgentCore Runtime instead of your laptop, so closing the lid doesn't kill the run and the workspace survives between sessions. Each session gets an isolated microVM with a reconnectable WebSocket terminal, and GitHub credentials stay in Secrets Manager behind an IAM-authenticated MCP Gateway so the agent never handles raw tokens. |
| [deepgram-meeting-assistant](./agentic-workloads/deepgram-meeting-assistant) | Desktop app that records meetings, transcribes them in real time with Amazon Transcribe, and uses Amazon Bedrock to correct sentences and generate summaries with action items and decisions. Transcripts and credentials stay on the local machine in encrypted SQLite storage, which matters where meeting content cannot leave the device. |
| [genai-product-canvas](./agentic-workloads/genai-product-canvas) | Facilitator's guide for a half-day GenAI product workshop aimed at product managers, for customers stuck at "let's add a chatbot." No code is written: the team fills a single-page canvas that forces evaluation, cost, and pricing onto the page from the start, which is what separates a shippable feature from a demo. |
| [internal-business-agent-tools](./agentic-workloads/internal-business-agent-tools) | Lets business users query internal databases in natural language through a server-side agent, avoiding the desktop-MCP pattern where every user installs a client and tool calls leave the region. The MCP server runs on AgentCore behind a Cognito-authenticated Gateway, reachable from LibreChat or Amazon Quick, so users need only a browser and SSO while data stays in AWS. |
| [multi-agent-text2sql](./agentic-workloads/multi-agent-text2sql) | Multi-agent natural-language-to-SQL over Amazon Athena and the AWS Glue Data Catalog, for questions single-prompt text-to-SQL gets wrong because it doesn't know the schema. Runs as a deterministic Strands Agents graph — route, retrieve schema, explore catalogue, generate SQL, summarise — with live tool and reasoning views so you can see why a query was written. |
| [nemoclaw-on-aws](./agentic-workloads/nemoclaw-on-aws) | Terraform module deploying NVIDIA NemoClaw on Amazon EC2 with Amazon Bedrock as the inference backend, bridged by a LiteLLM proxy because NemoClaw has no native Bedrock support. Access is over SSM Session Manager, with no inbound SSH. |
| [neural-audio-codec-sagemaker](./agentic-workloads/neural-audio-codec-sagemaker) | Deploys and compares neural audio codecs on Amazon SageMaker AI — the tokenisation layer that speech language models are built on. Mimi and EnCodec sit behind a **single** endpoint so the codec is the only variable, and the sample benchmarks frame rate, bitrate, and latency, with reconstructed audio at increasing codebook counts to hear the trade-off. |
| [restaurant-order-agent](./agentic-workloads/restaurant-order-agent) | Voice-first food ordering, where customers browse menus, order, and track delivery by speaking instead of tapping through an app. Low-latency voice-to-voice with preferences remembered across sessions, and it ships the whole system — customer app, FastAPI backend, and a real-time kitchen dashboard — not just the agent. |
| [sample-per-model-bedrock](https://github.com/aws-samples/sample-per-model-bedrock) | **Git submodule**, so the files live in [its own repository](https://github.com/aws-samples/sample-per-model-bedrock) rather than here. Runnable notebooks for Amazon Bedrock with **one folder per model family**, because model behaviour is not uniform and the differences cost time: Gemma rejects `top_p`, Grok wants `max_completion_tokens`, and most Claude models must be called through a cross-Region inference profile. Each folder names the endpoint, API, and parameters for that family across both `bedrock-runtime` and `bedrock-mantle`. |
| [semantic-prompt-routing](./agentic-workloads/semantic-prompt-routing) | Routes each prompt to the cheapest Amazon Bedrock model capable of answering it, instead of paying premium prices for simple queries. Classifies complexity, task type, and language across 15 models in 4 cost tiers, and the classifier itself can run locally on Ollama so routing does not add inference cost. |
| [unicorn-rental-dataset](./agentic-workloads/unicorn-rental-dataset) | **Dataset (CC0-1.0), not a runnable sample.** Synthetic multi-tenant SaaS data — roughly 14,000 bookings and 30,000 availability records across three tenant accounts — with `account_id` on most tables so it can back row-level-security and tenant-isolation demos. |
| [virtual-interview-coach-using-deepgram](./agentic-workloads/virtual-interview-coach-using-deepgram) | Voice-first AI mock interviews for students preparing to enter the workforce, held as a spoken conversation with questions grounded in the candidate's own resume and target job description. Produces a scored report where every competency score is anchored to a verbatim quote from the interview, plus coaching notes that track recurring strengths and weaknesses across sessions. |
| [voice-ai-shopping-assistant](./agentic-workloads/voice-ai-shopping-assistant) | Voice-first shopping assistant for the two moments shopping is hardest — planning at home and standing in front of a shelf of near-identical products. Unlike most shopping demos it **completes the purchase**, using AgentCore Payments and Browser to pay each merchant the way that merchant actually accepts payment, with dietary and brand preferences remembered via AgentCore Memory. |

### [Agentic Coding](./ai-coding-assistants)

| Sample | What it solves |
|--------|----------------|
| [bedrock-bridge](./ai-coding-assistants/bedrock-bridge) | Runs Claude Code, or any Anthropic-API client, against **non-Claude** models on Amazon Bedrock — Kimi, Llama, DeepSeek, Qwen, GLM, MiniMax, Mistral. A local proxy translates the Anthropic Messages API to Bedrock Converse, with separate main, light, and vision model slots so a text-only model can still handle images. |
| [claude-apps-gateway-on-aws](./ai-coding-assistants/claude-apps-gateway-on-aws) | AWS CDK deployment of Anthropic's self-hosted Claude Apps Gateway, so a team gets Claude Code access without anyone holding AWS or Anthropic credentials. Developers sign in through Cognito OIDC while Bedrock is called via the ECS task role, over a private-only network — which Claude Code's `/login` requires, since it only connects to a gateway resolving to private IPs. |
| [claude-code-proxy-on-aws](./ai-coding-assistants/claude-code-proxy-on-aws) | Anthropic-compatible proxy that puts SSO and spend control in front of Amazon Bedrock, for organisations that need to govern who uses which model and what each team spends. Issues virtual API keys to IAM Identity Center users and evaluates every request through an eight-stage policy chain covering user, team, model, and budget. |
| [isolated-agentic-coder](./ai-coding-assistants/isolated-agentic-coder) | Learning example of a six-agent Strands Agents Swarm on Amazon Bedrock that designs, builds, tests, reviews, and signs off an implementation. The agents run on the host but every tool call lands inside a hardened non-root container, so nothing touches your workstation, and the run is bounded by both an iteration cap and a cost budget. |
| [kiro-powers](./ai-coding-assistants/kiro-powers) | Two Kiro Powers that turn repeatable AWS advisory work into generated deliverables. The Disaster Recovery Advisor scans an account and produces a gap analysis, DR plan, CloudFormation templates, and an operational checklist; the Security Analyzer assesses posture across all AWS regions and scores the account against the Well-Architected Security Pillar. |
| [skills](./ai-coding-assistants/skills) | Reusable Agent Skills that work across Claude Code, Codex, and Kiro, distributed through this repository's Claude Code plugin marketplace. `aws-bento-deck` turns a storyline into a finished AWS-branded presentation as one self-contained HTML file rather than a PowerPoint. |
| [voice-pair-debugger](./ai-coding-assistants/voice-pair-debugger) | Voice agent you talk to while debugging a live AWS application, instead of context-switching between CloudWatch, X-Ray, the Lambda console, and your editor. It inspects logs, traces, and local source, narrates the likely root cause, and prints a suggested fix — and is **read-only by design**, never changing your files or your AWS resources. |

### [AI Infra](./ai-infra)

| Sample | What it solves |
|--------|----------------|
| [finetuning-on-eks](./ai-infra/finetuning-on-eks) | Infrastructure scaffold for distributed LLM fine-tuning on Amazon EKS, covering models from TinyLlama-1.1B to Llama-70B with QLoRA, LoRA, DDP, and FSDP. KubeRay handles the training jobs while Karpenter scales GPU nodes to zero when idle and prefers Spot, so the cluster is not billing you between runs. |
| [llm-inference](./ai-infra/llm-inference) | Two end-to-end paths for serving open-source LLMs with vLLM on AWS: bulk JSONL inference on AWS Batch with EC2 Spot, and reproducible single-instance benchmarks across g5, g6, g6e, g7e, p4d, and p6-b200. Both produce throughput and **cost per million tokens**, which is the number that decides the instance family. |
| [robotics-foundation-models-on-eks](./ai-infra/robotics-foundation-models-on-eks) | Deploys NVIDIA OSMO on Amazon EKS and validates robotics foundation model workflows — GR00T fine-tuning, OpenPI LoRA, Cosmos, and Isaac Lab — on a private-subnet cluster with EFA for distributed training. OSMO stays a pinned external dependency rather than being vendored, with version compatibility documented. |
| [whisper-large-v3-turbo-sagemaker](./ai-infra/whisper-large-v3-turbo-sagemaker) | Deploys `openai/whisper-large-v3-turbo` as a real-time speech-to-text endpoint on Amazon SageMaker, with a Transformers pipeline option and a vLLM option benchmarked head to head (vLLM roughly three times the throughput on the same instance). Both bake the model weights into the deployment artefact, so autoscaled instances start serving without downloading weights first. |

### [Databases](./databases)

| Sample | What it solves |
|--------|----------------|
| [aurora-mysql-upgrade-agents](./databases/aurora-mysql-upgrade-agents) | Multi-agent readiness analysis for an **Aurora MySQL minor upgrade (3.04 → 3.10)**, replacing the manual work of diffing parameters, reading error logs, and guessing which queries will regress. Six agents on Bedrock AgentCore cover Blue/Green variable comparison, CloudWatch error-log analysis, a weighted query risk score from `performance_schema`, and a measured Blue/Green `EXPLAIN` plan diff. |
| [rds-mysql-upgrade-agents](./databases/rds-mysql-upgrade-agents) | Multi-agent readiness analysis for a **MySQL 8.0 → 8.4 upgrade** on Amazon RDS for MySQL or Aurora MySQL-compatible, where optimizer and parameter changes are what regress queries. Four agents on Bedrock AgentCore compare Blue/Green variables, analyse CloudWatch error logs, and score InnoDB and query-optimizer risk, deployable into an account with CDK. |

### [Security & Compliance](./security)

| Sample | What it solves |
|--------|----------------|
| [bedrock-api-key-push-protection-guide](./security/bedrock-api-key-push-protection-guide) | Stops Amazon Bedrock long-term API keys (`ABSK…`) reaching a GitHub repository — which matters because GitHub's built-in secret scanning does not cover that pattern, so a hardcoded key can be pushed undetected. Gives the detection regex and an implementation path per GitHub plan: `git-secrets` pre-commit on Free, custom pattern plus push protection on Team, and organisation-level patterns on Enterprise Cloud. |
| [cloudhsm-to-kms-keys-migration](./security/cloudhsm-to-kms-keys-migration) | Bulk migration of asymmetric RSA and ECC keys from AWS CloudHSM to AWS KMS, where the AWS guidance covers one key at a time and offers no process for many. Provides discovery and listing with pattern filters, key-type analysis for planning, and batch splitting into fixed-size JSON files to limit blast radius and allow parallel runs. |

[Analytics](./analytics), [Modern Applications](./modern-applications) and
[Amazon Connect](./connect) do not have samples yet. Contributions are welcome.

## How to use this repository

This repository is large and contains many projects, making it a monorepo.  
To save time and disk space, we recommend cloning only the directories you need rather than downloading the entire repository.

You can do this using `git sparse-checkout`.

For example, if you want to clone the `agentic-workloads/sample-1` project, run:

   ```
   git clone https://github.com/aws-samples/sample-apj-sup-sa.git
   cd sample-apj-sup-sa
   git sparse-checkout init --cone
   git sparse-checkout set agentic-workloads/sample-1
   ```

For more details on `git sparse-checkout`, see:
https://github.blog/2020-01-17-bring-your-monorepo-down-to-size-with-sparse-checkout/


## Additional Resources

### Startup Build Solutions

In addition to the resources in this repository, [Startup Build Solutions](https://aws.amazon.com/startups/build) offers sample code and step-by-step guides on many of the topics covered here, tailored to help startups adopt them more effectively. We encourage you to explore it alongside this repository.

You may also find the following resources helpful as you build on AWS:

- [AWS Architecture Center](https://aws.amazon.com/architecture/) – Reference architectures and best practices
- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/) – Design principles for cloud workloads
- [AWS Solutions Library](https://aws.amazon.com/solutions/) – Vetted technical solutions and patterns
- [AWS Sample Code](https://github.com/aws-samples) – Official AWS sample code repositories


## Important Notes

- All samples in this repository are provided for reference purposes only.
- Please ensure a thorough security review before applying any sample to a production environment.
- Usage of AWS services may incur costs. Be sure to review the relevant pricing pages before deploying.


## License

This library is licensed under the MIT-0 License. See the LICENSE file for details. The `agentic-workloads/unicorn-rental-dataset/` folder is licensed under CC0-1.0 — see its own [LICENSE](agentic-workloads/unicorn-rental-dataset/LICENSE) file.
