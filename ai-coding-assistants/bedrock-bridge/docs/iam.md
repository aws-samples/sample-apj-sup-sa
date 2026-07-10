# IAM permissions for bedrock-bridge

> **Disclaimer.** The policy below is an example to help you get started. It is not a security review or a recommendation tailored to your environment. You are responsible for reviewing, adjusting, and maintaining the IAM policies you apply. The bedrock-bridge authors and contributors provide this material as-is, with no warranties, and are not liable for any damages, costs, or security incidents arising from its use.

The principal (IAM user, role, or SSO permission set) running `bedrock-bridge` needs to:

1. Identify itself (preflight sanity check)
2. Read foundation-model and inference-profile metadata (preflight per-model verification)
3. Invoke Bedrock Converse / ConverseStream against the configured models

Below is a minimal templated policy. List your `<MAIN_MODEL_ID>` and `<LIGHT_MODEL_ID>` in the `Resource` array; if you don't configure a light model, drop that ARN pair. If you use `--vision-model`, add its ID the same way (`<VISION_MODEL_ID>`); it is invoked via Converse like any other slot and needs no extra actions.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Identity",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    },
    {
      "Sid": "BedrockMetadata",
      "Effect": "Allow",
      "Action": [
        "bedrock:GetFoundationModel",
        "bedrock:GetInferenceProfile"
      ],
      "Resource": "*"
    },
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:Converse",
        "bedrock:ConverseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/<MAIN_MODEL_ID>",
        "arn:aws:bedrock:*:*:inference-profile/<MAIN_MODEL_ID>",
        "arn:aws:bedrock:*::foundation-model/<LIGHT_MODEL_ID>",
        "arn:aws:bedrock:*:*:inference-profile/<LIGHT_MODEL_ID>",
        "arn:aws:bedrock:*::foundation-model/<VISION_MODEL_ID>",
        "arn:aws:bedrock:*:*:inference-profile/<VISION_MODEL_ID>"
      ]
    }
  ]
}
```

## Notes

- **Foundation model vs inference profile.** Pure Bedrock IDs like `moonshotai.kimi-k2.5` resolve to `foundation-model/<id>`. Inference-profile IDs (cross-region or regional) resolve to `inference-profile/<id>`; in practice you will see prefixes like `global.`, `us.`, `eu.`, `apac.`, `jp.`, etc. Including both ARN forms above covers either case without having to know which one applies.
- **Cross-region inference profiles fan out.** A `global.*` profile may invoke the underlying foundation model in any region. If you scope by region, also allow the foundation model in those regions, or keep `*` as in the template.
- **Wider catalog.** If you want to swap target Bedrock models without editing the policy each time, broaden the `Resource` list to `arn:aws:bedrock:*::foundation-model/*` and `arn:aws:bedrock:*:*:inference-profile/*`. The trade-off: any Bedrock model the principal would otherwise be able to invoke becomes reachable through the bridge.
- **Log data handling.** AWS error strings (which can quote parts of a request) surface at every log tier, including `default`. The `debug` tier additionally writes request and response content (prompt text, full bodies) to `/tmp/bedrock-bridge-<port>.log`; image bytes are redacted but text is verbatim. Treat that file as sensitive when running at `debug`. See [logging.md](./logging.md).
