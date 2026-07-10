**English** | [한국어](./README.ko.md)

# MySQL Upgrade Readiness Checker — Streamlit UI (optional)

A simple Streamlit app for invoking the deployed orchestrator through a GUI.
It **runs locally** and calls AgentCore using your PC's AWS credentials.
(The app itself does not need to be deployed — it's independent of the CDK stack.)

## Prerequisites

- The CDK stack (`infra/`) is already deployed and you know its Outputs
- Python 3.10+
- AWS credentials configured via `aws configure`, with the `bedrock-agentcore:InvokeAgentRuntime` permission

## Run

```bash
cd ui/streamlit

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Enter only the CDK Outputs (4 ARNs) in .env

streamlit run app.py
```

The browser opens `http://localhost:8501` automatically.

## How to Fill In `.env`

The UI reads **two `.env` files** in order:

1. `../../infra/.env` — VPC / DB / host / region / bucket and other values already filled in at CDK deploy time
2. `./.env` — values specific to this UI (mainly the 4 ARNs produced by CDK)

So **you don't need to copy the values you already set in `infra/.env`.** The UI
picks them up automatically.

When `cdk deploy` finishes, the CloudFormation Outputs are printed in the terminal:

```
Outputs:
RdsMysqlUpgradeAgentStack.OrchestratorArn = arn:aws:bedrock-agentcore:...
RdsMysqlUpgradeAgentStack.VariablesCompareArn = arn:aws:...
RdsMysqlUpgradeAgentStack.ErrorLogAnalyzerArn = arn:aws:...
RdsMysqlUpgradeAgentStack.UpgradeReadinessArn = arn:aws:...
```

Just copy these 4 into `./.env` and you're done.

### If You Want to Override Values

The UI's `.env` **overrides** `infra/.env`. For example, to test in a different
region, just add `CDK_DEFAULT_REGION=ap-northeast-2` to `./.env` and only the UI
will call that region.

### Display Language

The UI display language and the report language both follow the
`REPORT_LANGUAGE` value in `infra/.env` (`ko` = Korean, `en` = English, default
`ko`). Add `REPORT_LANGUAGE=en` to `./.env` to switch both the UI and the
reports to English.

## Screen Layout

- **Top badge** — live check of whether all `.env` values are filled in
- **Progress** — each step shown as a checklist (running ⏳ / done ✅)
- **Detailed log** (collapsed) — streaming terminal output
- **Sidebar reports** — download the 3 `.md` reports after analysis completes

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `AccessDeniedException` | Check that your AWS credentials have the `bedrock-agentcore:InvokeAgentRuntime` permission |
| `.env` badge stays ❌ | Run `cd ui/streamlit && cp .env.example .env`, then check the values are entered |
| Report download fails | The presigned URL may have expired. Run the analysis again |
| Stream cuts off midway | Check the AgentCore timeout (default 15 minutes). Re-run |
