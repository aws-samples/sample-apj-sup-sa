"""A2A Data Analyst Agent — powered by Amazon Bedrock (Claude).

Implements the Agent-to-Agent (A2A) protocol using the official ``a2a-sdk``
via ``bedrock_agentcore.runtime.a2a.serve_a2a``. Runs on the AgentCore
A2A service-contract port (9000).

See:
- https://a2a-protocol.org/ — A2A protocol specification
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html
"""

import asyncio
import logging
import os

import boto3
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from bedrock_agentcore.runtime.a2a import serve_a2a

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
MODEL_ID = os.environ.get("MODEL_ID", "jp.anthropic.claude-sonnet-4-6")

SYSTEM_PROMPT = """あなたはデータ分析の専門家です。

## スキル
- SQLクエリ結果やドキュメントの内容を受け取り、ビジネスインサイトを提供する
- データの傾向、異常値、パターンを特定する
- 経営判断に役立つ推奨アクションを提示する

## 出力形式
分析結果は以下の構造で返してください:

1. **サマリー**: 1-2文で要点
2. **分析結果**: 主要な発見事項（箇条書き）
3. **推奨アクション**: 次に取るべきアクション（あれば）

## 制約
- 提供されたデータのみに基づいて分析する。推測で数値を作らない
- 日本語で回答する
"""


class DataAnalystAgent:
    """Bedrock Claude を Converse API で呼び出す分析エージェント。"""

    def __init__(self) -> None:
        self._client = boto3.client("bedrock-runtime", region_name=REGION)

    async def invoke(self, user_message: str) -> str:
        """User の入力を受け取り、Bedrock からの分析結果テキストを返す。

        boto3 は同期 SDK なので、``asyncio.to_thread`` でイベントループを
        ブロックしないようにする。
        """
        return await asyncio.to_thread(self._invoke_sync, user_message)

    def _invoke_sync(self, user_message: str) -> str:
        response = self._client.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            system=[{"text": SYSTEM_PROMPT}],
        )
        return response["output"]["message"]["content"][0]["text"]


class DataAnalystExecutor(AgentExecutor):
    """A2A ``AgentExecutor`` — DataAnalystAgent を A2A プロトコルで公開する。"""

    def __init__(self) -> None:
        self.agent = DataAnalystAgent()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = context.get_user_input()
        logger.info("[A2A] execute() called, input length=%d", len(user_text))

        if not user_text.strip():
            await event_queue.enqueue_event(
                new_agent_text_message("エラー: 分析対象のテキストが空です。")
            )
            return

        try:
            result = await self.agent.invoke(user_text)
            logger.info("[A2A] Bedrock returned %d chars", len(result))
            await event_queue.enqueue_event(new_agent_text_message(result))
        except Exception as exc:  # noqa: BLE001 — surface any error to caller
            logger.exception("[A2A] Bedrock invocation failed")
            await event_queue.enqueue_event(
                new_agent_text_message(f"分析エージェント内部エラー: {exc}")
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancellation not supported")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9000))
    logger.info("Starting data-analyst A2A agent on port %d", port)
    serve_a2a(DataAnalystExecutor(), port=port)
