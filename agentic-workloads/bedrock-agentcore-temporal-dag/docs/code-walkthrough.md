# コード解説

## 全体構成

```
workflow/              Temporal Worker（DAG実行エンジン）
  main.py             Worker起動
  demo.py             デモCLI（進捗表示付き）
  flows/              Workflow定義（DAGのフロー）
  activities.py       Activity定義（Agent呼び出しの単位）
  a2a_client.py       A2Aプロトコルの通信層

agents/               各Agent（AgentCore Runtime上で独立稼働）
  gather/main.py      情報収集Agent
  analyze/main.py     分析Agent
  evaluate/main.py    評価Agent
  synthesize/main.py  統合Agent

cdk/                  CDKインフラ定義
```

## Orchestrator

### main.py — Worker起動

```python
worker = Worker(
    client,
    task_queue="daf-orchestrator",
    workflows=[ResearchPipelineWorkflow],
    activities=[invoke_agent],
)
await worker.run()
```

Temporal Worker は poll型です。Temporal Cloud に接続し、`daf-orchestrator` キューからタスクを取得して実行します。インバウンドポートは不要で、ECS Fargate上ではアウトバウンド通信のみで動作します。

### flows/research_pipeline.py — DAGフロー定義

このファイルがシステムの中核です。Python コードで DAG を直接表現します。

```
gather → [analyze, evaluate] → (条件: score < 0.7 なら re_analyze) → synthesize
```

**設計ポイント:**

- `@workflow.defn` + `@workflow.run`: Temporal Workflow として登録。Temporal がこのコードの実行状態を永続化します
- `workflow.execute_activity("invoke_agent", args=[...])`: Agent呼び出しを Activity として実行。失敗時は Temporal がリトライをスケジュールします
- `asyncio.gather(...)`: fan-out を表現。analyze と evaluate を並列に実行します
- `if score < 0.7`: 条件分岐。evaluate の結果スコアが低い場合のみ再分析を実行します
- `@workflow.query def get_status()`: 実行中のワークフローに対してステータスを問い合わせる API を提供します

**RetryPolicy:**

各 Activity にリトライポリシーを設定しています。`maximum_attempts=3, backoff_coefficient=2.0` は「2秒 → 4秒 → 8秒」の指数バックオフで3回まで再試行することを意味します。

### activities.py — Activity定義

```python
@activity.defn
async def invoke_agent(agent_name: str, input_data: dict) -> dict:
    return await _invoke_agent(agent_name, input_data)
```

薄いラッパーです。Temporal の Activity として登録するために `@activity.defn` を付けています。実際の通信は `a2a_client.py` に委譲します。

### a2a_client.py — A2A通信層

Agent との通信を担当します。ローカルモードと AWS モードの 2つの実行パスがあります。

**ローカルモード** (`AGENT_ENDPOINTS` 環境変数設定時):

1. 環境変数から Agent URL を取得（例: `{"gather":"http://localhost:9001",...}`）
2. a2a-sdk の `A2ACardResolver` → `ClientFactory` で Agent Card を解決
3. `SendMessageRequest` で A2A プロトコルメッセージを送信
4. レスポンスの task artifacts からテキストを抽出

**AWS モード** (デフォルト):

1. **サービスディスカバリ**: SSM Parameter Store から Agent の ARN を取得
2. **URL構築**: ARN からAgentCore Runtime の呼び出し URL を生成
   - `https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{URL_ENCODED_ARN}/invocations`
3. **SigV4署名**: AWS認証情報でリクエストを署名 (`bedrock-agentcore` サービス)
4. **JSON-RPC送信**: A2Aプロトコル（JSON-RPC 2.0）のペイロードを直接 POST
5. **レスポンスパース**: artifacts または status message からテキストを抽出してJSONとして返却

AWS モードでは a2a-sdk の `A2ACardResolver` / `ClientFactory` を使わず、直接 JSON-RPC POST を送信します。AgentCore Runtime は単一の invoke エンドポイントしか持たず、`/.well-known/agent-card.json` への GET をサポートしないためです。

**SigV4HTTPXAuth クラス:**

httpxのカスタムAuth実装です。各リクエストに対してAWS SigV4署名を自動付与します。`bedrock-agentcore` サービスとして署名し、セッション ID ヘッダも付与します。

**キャッシュ:**

`_arn_cache` で SSM呼び出し結果をプロセス内キャッシュしています。Worker は長時間稼働するため、同じ Agent を何度も呼び出す際のレイテンシを削減します。

### demo.py — デモCLI

Temporal Cloud にワークフローを発行し、Query API でステータスをポーリングしてリアルタイム表示します。

```bash
python demo.py "AWS Bedrock AgentCoreの概要を調査してください"
```

- Workflow ID を自動生成して `start_workflow` を発行
- 2秒間隔で `handle.query("get_status")` を呼び出し、ターミナルに進捗を描画
- 完了後に `handle.result()` で最終結果を表示

## Agents

### 各Agent の構造 (例: gather/main.py)

```python
_model = BedrockModel(
    model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
)

agent = Agent(
    model=_model,
    system_prompt="...",
    tools=[web_search, extract_content],
)

a2a_server = A2AServer(agent=agent, http_url=runtime_url, serve_at_root=True)
app = FastAPI()
app.get("/ping")(ping)
app.mount("/", a2a_server.to_fastapi_app())
```

**設計ポイント:**

- `@tool` デコレータ: Strands SDK で LLM に使わせるツールを定義。現在はスタブ実装で、実運用時は SerpAPI 等に差し替えます
- `A2AServer`: Strands SDK の A2A サーバー。Agent を A2A プロトコル対応のエンドポイントとして公開します
- `/ping`: AgentCore Runtime のヘルスチェック用エンドポイント（必須contract）
- `PORT` 環境変数: ローカル実行時に各 Agent を別ポートで起動可能（デフォルト 9000）
- `MODEL_ID` 環境変数: デプロイ先でモデルを切り替え可能
- `Dockerfile` の `ARG AGENT_NAME`: 同一の Dockerfile で4つの Agent イメージをビルドするための build argument

## CDK Infrastructure

### network_stack.py

VPC を作成します。2 AZ、NAT Gateway 1台、Public/Private Subnet。Orchestrator（ECS）は Private Subnet に配置され、NAT Gateway 経由で外部通信します。

### agents_stack.py

- ECR Repository x4: 各 Agent のコンテナイメージ格納先
- SSM Parameter x4: AgentCore Runtime ARN のサービスディスカバリ用（初期値はプレースホルダ）
- IAM Role (AgentCore Execution Role):
  - Bedrock モデル呼び出し（foundation-model + inference-profile）
  - ECR イメージ取得（GetAuthorizationToken, BatchGetImage, GetDownloadUrlForLayer）

### orchestrator_stack.py

- ECS Cluster + Fargate Service: Temporal Worker の実行環境
- Task Definition: 256 CPU / 512 MB RAM, ARM64。環境変数で Temporal 接続先を注入、Secrets Manager から API Key を取得
- Security Group: アウトバウンドのみ許可（Worker は poll 型のため受信ポート不要）
- Capacity Provider: Fargate Spot を優先（`weight=1`）、最低1タスクは On-Demand（`base=1`）。Spot 中断時は Temporal が自動リスケジュールするため安全です
