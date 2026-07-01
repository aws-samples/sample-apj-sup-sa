import json
import os

from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn


@tool
def web_search(query: str) -> str:
    """指定したクエリでWeb検索を実行し、結果を返します"""
    # デモ用スタブ — 実際のプロジェクトではSerpAPI等に置き換え
    return json.dumps({
        "results": [
            {"title": f"Result for: {query}", "snippet": f"Sample search result about {query}"},
        ]
    })


@tool
def extract_content(url: str) -> str:
    """指定したURLからコンテンツを抽出します"""
    # デモ用スタブ
    return json.dumps({"content": f"Extracted content from {url}", "length": 1500})


_model = BedrockModel(
    model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

agent = Agent(
    model=_model,
    system_prompt=(
        "あなたは情報収集の専門家です。与えられたクエリに対して、"
        "Web検索やコンテンツ抽出を行い、構造化された情報を返してください。"
        "出力はJSON形式で、'findings'キーにリスト形式で結果を含めてください。"
    ),
    tools=[web_search, extract_content],
    name="Gather Agent",
    description="情報を収集して構造化するエージェント",
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
