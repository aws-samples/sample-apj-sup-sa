import json
import os

from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn


@tool
def score_quality(text: str, criteria: str) -> str:
    """指定した基準でテキストの品質をスコアリングします"""
    # デモ用スタブ
    return json.dumps({"score": 0.75, "criteria": criteria, "rationale": "Meets most criteria"})


@tool
def detect_gaps(analysis: str) -> str:
    """分析結果の不足点を検出します"""
    # デモ用スタブ
    return json.dumps({"gaps": ["Missing recent data", "No competitor comparison"], "severity": "medium"})


_model = BedrockModel(
    model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

agent = Agent(
    model=_model,
    system_prompt=(
        "あなたは品質評価の専門家です。与えられた分析結果を評価し、"
        "品質スコアと改善のためのフィードバックを提供してください。"
        "出力はJSON形式で、'score'キーに0-1のスコアを、'feedback'キーに改善点を含めてください。"
    ),
    tools=[score_quality, detect_gaps],
    name="Evaluate Agent",
    description="分析結果の品質を評価し、フィードバックを提供するエージェント",
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
