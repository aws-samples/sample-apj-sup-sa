import json
import os

from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn


@tool
def score_quality(text: str, criteria: str) -> str:
    """Score the quality of the given text against the specified criteria."""
    # Demo stub
    return json.dumps({"score": 0.75, "criteria": criteria, "rationale": "Meets most criteria"})


@tool
def detect_gaps(analysis: str) -> str:
    """Detect gaps and missing elements in the analysis result."""
    # Demo stub
    return json.dumps({"gaps": ["Missing recent data", "No competitor comparison"], "severity": "medium"})


_model = BedrockModel(
    model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

agent = Agent(
    model=_model,
    system_prompt=(
        "You are an expert quality evaluator. Evaluate the given analysis result "
        "and provide a quality score along with actionable feedback for improvement. "
        "Output JSON with a 'score' key (0-1) and a 'feedback' key listing improvements."
    ),
    tools=[score_quality, detect_gaps],
    name="Evaluate Agent",
    description="Agent that evaluates analysis quality and provides feedback",
)

_port = os.environ.get("PORT", "9000")
runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", f"http://127.0.0.1:{_port}/")
a2a_server = A2AServer(agent=agent, http_url=runtime_url, serve_at_root=True)

app = FastAPI()


@app.get("/ping")
def ping():
    return {"status": "healthy"}


app.mount("/", a2a_server.to_fastapi_app())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
