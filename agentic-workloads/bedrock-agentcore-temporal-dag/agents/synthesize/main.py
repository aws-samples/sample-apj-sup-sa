import json
import os

from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn


@tool
def merge_findings(sources: str) -> str:
    """複数ソースの結果を統合します"""
    # デモ用スタブ
    return json.dumps({"merged": "Consolidated findings from all sources", "source_count": 3})


@tool
def generate_summary(content: str, max_length: int) -> str:
    """コンテンツを指定文字数以内に要約します"""
    # デモ用スタブ
    return json.dumps({"summary": content[:max_length], "original_length": len(content)})


_model = BedrockModel(
    model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

agent = Agent(
    model=_model,
    system_prompt=(
        "あなたは統合・要約の専門家です。複数の分析結果と評価を統合し、"
        "最終的なレポートを生成してください。"
        "出力はJSON形式で、'summary'キーに統合結果を、'recommendations'キーに推奨事項を含めてください。"
    ),
    tools=[merge_findings, generate_summary],
    name="Synthesize Agent",
    description="複数の分析結果を統合し、最終レポートを生成するエージェント",
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
