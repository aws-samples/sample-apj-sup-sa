import json
import os

from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn


@tool
def classify_topic(text: str) -> str:
    """Classify the topics in the given text."""
    # Demo stub
    return json.dumps({"topics": ["technology", "architecture"], "confidence": 0.92})


@tool
def extract_key_points(text: str) -> str:
    """Extract key points from the given text."""
    # Demo stub
    return json.dumps({"key_points": [f"Point extracted from: {text[:50]}..."]})


_model = BedrockModel(
    model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

agent = Agent(
    model=_model,
    system_prompt=(
        "You are an expert analyst. Analyze the given information, "
        "performing topic classification, key point extraction, and pattern identification. "
        "Output JSON with an 'analysis' key for results and a 'confidence' key for score (0-1)."
    ),
    tools=[classify_topic, extract_key_points],
    name="Analyze Agent",
    description="Agent that analyzes information and extracts patterns and key points",
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
