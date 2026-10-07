# Kiro First Call Deck

An interactive, 57-slide presentation for a first technical conversation about Kiro. It explains spec-driven development, shows how agents use project knowledge and tools, and covers the controls and costs involved in team adoption.

Use the deck to discuss a customer's development challenges, identify a useful starting workflow, and choose a follow-up demo or pilot. Korean, English, and Japanese versions are available in the same deck.

**[Open the deck in English](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/#lang=en&slide=1)** · [한국어](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/#lang=ko&slide=1) · [日本語](https://aws-samples.github.io/sample-apj-sup-sa/ai-coding-assistants/kiro-fcd/#lang=ja&slide=1)

[Download the standalone HTML](https://github.com/aws-samples/sample-apj-sup-sa/raw/refs/heads/main/ai-coding-assistants/kiro-fcd/index.html)

## What the deck covers

The presentation follows eight topics. Use the agenda to jump to the sections that match the customer's questions.

| Slides | Topic | What the discussion covers |
| --- | --- | --- |
| 3–5 | **The changing role of AI development tools** | The move from code suggestions to agents that perform several tasks. Three common challenges: unclear intent, lost context, and review workload. |
| 6–12 | **Spec-driven development with Kiro** | Requirements, design, and tasks; four ways to create specs; and property-based testing. How people review intent and verify the implementation. |
| 13–27 | **Core Kiro capabilities** | IDE, CLI, Web, and Mobile; Agent Focus; Steering, MCP, Skills, and Hooks; Cloud Sessions; SubAgent; Custom Agents; and Workflows. |
| 28–30 | **IDE and CLI workflows** | Powers and Checkpoints in the IDE. CLI automation, code intelligence, ACP, Knowledge, and Rewind. |
| 31–38 | **Kiro Crew** | An agent workspace on local or remote hardware. Gateway and backend roles, memory, scheduling, artifacts, knowledge, apps, issue investigation, and PR review. |
| 39–46 | **Enterprise operation and visibility** | Model and tool policies, execution permissions, data protection boundaries, cost comparisons, logging, activity reports, OpenTelemetry, and the Kiro Analytics Dashboard. |
| 47–48 | **Plans and costs** | Plan credits and additional usage. Questions to consider when assessing team usage and budget. |
| 49–54 | **Context engineering in practice** | How Steering, Skills, and Custom Agents divide responsibilities. Examples of shared Global rules and project-specific guidance for a payment service. |

The deck also includes Q&A, references, and a change history.

## Who this deck is for

This deck is a useful starting point for the following conversations. Select a route based on the customer's current problem.

| Customer situation | Why this content is relevant | Useful sections |
| --- | --- | --- |
| **An engineering team already generates code with AI but spends too much time correcting intent or reviewing changes.** | Specs make requirements and acceptance criteria explicit. The testing and review examples show how to check results against those criteria. | Spec-driven development, Correctness, Checkpoints, and Workflows |
| **A platform or developer experience team wants consistent practices across projects.** | Steering, Skills, Hooks, and Custom Agents show where to put team rules, reusable procedures, tool permissions, and role-specific settings. | Core capabilities and context engineering |
| **A development or SRE team needs agents in terminals, remote environments, or CI/CD.** | The CLI and Crew sections explain automation, editor connections, persistent work, scheduled tasks, and the roles of local and remote hosts. | IDE/CLI workflows and Kiro Crew |
| **A team needs several agents to implement, review, and verify a feature.** | The Workflows examples explain separate sessions, explicit handoffs, parallel reviews, loops, and waits for external activity. | Custom Agents and Workflows, especially slides 21–27 |
| **An organization is assessing a wider rollout and needs security, governance, or budget discussions.** | The enterprise sections distinguish model and tool controls, data handling, logging, usage metrics, and credits. They also explain differences in control coverage. | Enterprise operation, analytics, and plans |

Engineering leaders, developers, platform teams, and security stakeholders can use the same material to discuss their different concerns.

## Examples to explore together

The deck includes concrete examples that help turn a feature overview into a technical discussion:

- **Spec creation:** follow a request through requirements, design, implementation tasks, and verification.
- **Cloud Sessions:** inspect where code runs, what settings are carried into the cloud, and how work continues across devices.
- **Custom Agents:** compare an implementation agent with a reviewer that has read-only tools and a checklist.
- **Workflows:** follow an API rate-limiting request through planning, implementation, independent quality and security reviews, verification, PR creation, and a wait for new PR activity.
- **Kiro Crew:** examine the Gateway/backend architecture, memory lifecycle, and examples of issue investigation and PR review.
- **Project context:** use the payment-service example to discuss how organizational rules, project standards, review procedures, and agent permissions fit together.

Illustrations and detail buttons reveal further explanations. The Workflows demo shows communication between the main conversation and the execution steps. Product screenshots, videos, and expandable media support the discussion.

## Using it in a customer meeting

1. **Start with the workflow.** Ask where the team loses time: defining requirements, repeating project context, reviewing code, or coordinating several steps.
2. **Choose the relevant sections.** Use the examples to discuss who performs each task, what context and tools they need, and what evidence confirms completion.
3. **Agree on a next step.** Select one representative feature, review task, or automation for a demo or pilot. Define acceptance criteria, permissions, and a way to evaluate the result.

For an initial introduction, start with the development challenges and spec workflow. For an adoption discussion, focus on enterprise controls, usage visibility, costs, and shared project guidance.

## Viewing and sharing

Open the online deck or download `index.html` and open it in a browser. The file includes the images, videos, fonts, and interactive Workflows demo, so it can be shared as one HTML file.

Use **한국어 · English · 日本語** at the bottom left to select a language. Switching languages keeps the current slide, and the browser remembers the selection. Speaker notes remain as you entered them. Screenshots and videos keep their original language.

Features, pricing, and support conditions can change. The slides link to official documentation for checking current details; those links require an internet connection.

## Licenses

See the repository license and the licenses of embedded materials. The Noto font licenses are included in [Noto Sans KR](LICENSES/NotoSansKR-OFL.txt) and [Noto Sans CJK JP](LICENSES/NotoSansJP-OFL.txt).
