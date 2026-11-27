# Costs

Model tokens are almost all of the bill. The gateway, the Lambdas, the ledger and the logs are small next to them.
Prices are us-east-1 list prices as of November 2026, from the AWS pricing page linked in each row. Check every price
below against those pages for your region before you rely on it.

## Model tokens

These are the default prices in `terraform/variables.tf` (`openweight_models`), in USD per million tokens, us-east-1
on-demand. `usage_sync` uses the same numbers to price usage, so if your prices differ (for example because of a
private rate), change them there.

| Model | Input | Output |
|---|---|---|
| Kimi K3 (`ow/kimi-k3`) | 3.30 | 16.50 |
| GLM 5.3 (`ow/glm-5.3`) | 1.848 | 5.808 |
| DeepSeek V3.2 (`ow/deepseek-v3.2`) | 0.62 | 1.85 |
| GLM 5 (`ow/glm-5`) | 1.00 | 3.20 |

Mantle does not report cached input separately, so the sample prices all input at the full input rate, or at a
model's optional `cache_write` price (`openweight_models` in `terraform/variables.tf`) when that is higher. The
reported spend is higher than your bill when prompt caching is in use only for models whose cache writes cost no more
than that rate. Kimi K3 and GLM 5.3 have cache-write usage types whose rates the sample has not checked; set
`cache_write` for them from the Price List, or their reported spend can be below the bill.

### Worked example

Take 20 developers, each sending 2 million input tokens and 0.2 million output tokens a day, for 21 working days a
month. Coding agents resend the conversation on every turn, so input is usually about ten times output.

| All traffic on | Input (840 M tokens) | Output (84 M tokens) | Month |
|---|---|---|---|
| Kimi K3 | $2,772 | $1,386 | $4,158 |
| GLM 5.3 | $1,552 | $488 | $2,040 |
| DeepSeek V3.2 | $521 | $155 | $676 |

The default $50 monthly budget per user would stop these developers early in the month on Kimi K3, which works out at
about $208 per developer. Set `default_budget_usd` and the group budgets from a figure like this one.

## Everything else

| Component | What drives the cost | Notes |
|---|---|---|
| AgentCore Gateway | Requests through the inference and web search gateways | One gateway request per model call or search. See [Amazon Bedrock AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) |
| AgentCore Web Search | Searches | Charged to the caller's budget at $0.007 per search (interceptor `WEBSEARCH_USD`; see [Amazon Bedrock AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)) |
| Lambda | Interceptor runs once per request; `usage_sync` every 5 minutes (about 8,700 runs a month); dashboard on use | Usually inside the free tier for a small team |
| DynamoDB (on-demand) | A few writes per request plus the `usage_sync` rollups | Cents to a few dollars a month for a small team |
| CloudWatch Logs | Interceptor and gateway logs | Set `log_retention_days` to keep this bounded |
| AWS KMS | One customer managed key | $1 a month plus requests ([AWS KMS pricing](https://aws.amazon.com/kms/pricing/)) |
| ALB and AWS WAF (optional dashboard) | ALB hours and LCUs; one web ACL with 3 rules, its requests and its logs | Roughly $16 a month for the ALB before LCUs ([Elastic Load Balancing pricing](https://aws.amazon.com/elasticloadbalancing/pricing/): $0.0225 an hour), plus about $8 a month for the web ACL and its 3 rules ([AWS WAF pricing](https://aws.amazon.com/waf/pricing/): $5 a web ACL and $1 a rule each month) |

## Keeping it down

- Point cheaper models at day-to-day work: set opencode's `small_model` to `ow/glm-5.3` or `ow/deepseek-v3.2` (the
  MDM template does this).
- Give each group only the models it needs (`group_models`).
- Use hourly and daily caps as well as the monthly one, so one runaway agent loop stops within the hour.
- Follow the README's Clean up (deletion protection off, then `terraform destroy`) when you finish testing. Idle, the
  ALB, the AWS WAF web ACL and the KMS key still cost money.
