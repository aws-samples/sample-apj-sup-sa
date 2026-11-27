# Background: opencode with open-weight models on Amazon Bedrock

opencode ships an `amazon-bedrock` provider that calls the Bedrock Converse API with the developer's AWS credentials.
AWS has published how to use it with open-weight models
([Use open weight models as your AI coding agent with Amazon Bedrock](https://aws.amazon.com/blogs/machine-learning/use-open-weight-models-as-your-ai-coding-agent-with-amazon-bedrock/),
[Introducing Kimi K3 on Amazon Bedrock](https://aws.amazon.com/blogs/machine-learning/introducing-kimi-k3-on-amazon-bedrock/)).
For a team, these gaps remain. Each one links to the public opencode issue that reports it, as of November 2026.

| Gap | Public reports | How this sample addresses it |
|---|---|---|
| Every developer needs AWS credentials on the laptop | opencode docs: the provider uses the AWS credential chain | Developers sign in with the IdP; only the gateway holds AWS permissions |
| Custom-provider API keys are read once at start; an IdP token expires mid-session | [#43281](https://github.com/anomalyco/opencode/issues/43281), [#31649](https://github.com/anomalyco/opencode/issues/31649) | `ocgw proxy` adds a fresh token to every request |
| Bedrock model-ID rewriting and region-prefix errors | [#18812](https://github.com/anomalyco/opencode/issues/18812), [#25428](https://github.com/anomalyco/opencode/issues/25428), [#12824](https://github.com/anomalyco/opencode/issues/12824) | opencode uses its OpenAI-compatible provider with stable `ow/<alias>` names; the gateway maps them to Bedrock model IDs |
| Tool calls through Converse break some open-weight agent loops | [#13807](https://github.com/anomalyco/opencode/issues/13807), [#14221](https://github.com/anomalyco/opencode/issues/14221) | Models are served through Bedrock Mantle's OpenAI-compatible API |
| Bedrock provider regressions (hangs, empty output, hidden models) | [#30858](https://github.com/anomalyco/opencode/issues/30858), [#30948](https://github.com/anomalyco/opencode/issues/30948), [#40663](https://github.com/anomalyco/opencode/issues/40663) | The Bedrock provider code path is not used |
| Kimi rejects assistant turns with empty content | [#39451](https://github.com/anomalyco/opencode/issues/39451), [#46577](https://github.com/anomalyco/opencode/issues/46577) | The interceptor normalises such turns |
| No per-user rate limits, budgets, fallback or usage view | opencode [Enterprise](https://opencode.ai/docs/enterprise/) expects you to bring a gateway | AgentCore Gateway rate limits and fallback, plus the interceptor, ledger and dashboard |

## Related samples

- [sample-opencode-with-bedrock](https://github.com/aws-samples/sample-opencode-with-bedrock): an ECS-hosted router
  with Cognito sign-in for opencode. It predates AgentCore Gateway inference targets (July 2026); this sample uses the
  managed gateway instead of a self-run router and adds budgets, rate limits, fallback and attribution.
- [sample-opencode-on-amazon-bedrock-agentcore](https://github.com/aws-samples/sample-opencode-on-amazon-bedrock-agentcore):
  runs opencode itself inside AgentCore Runtime as a cloud coding worker. Complementary to this sample.
