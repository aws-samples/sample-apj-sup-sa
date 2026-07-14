# Internal Business Agent Tools

社内の非公開データ（顧客DB、社内ドキュメント）に対して、自然言語で安全にアクセスできる AI エージェント基盤。

## 概要

ビジネスユーザーが Claude（Amazon Quick / LibreChat）を UI として利用し、AgentCore Gateway 経由で社内リソースに問い合わせる構成のサンプルです

## デモ

<img width="800" height="440" alt="demo" src="https://github.com/user-attachments/assets/628fdc21-e272-4f9d-a1d1-c821925f8585" />

## アーキテクチャ

```mermaid
graph TD
    subgraph Clients
        Quick[Amazon Quick]
        LibreChat[LibreChat]
    end

    subgraph Auth["Cognito User Pool"]
        ResourceServer["Resource Server: mcp/access"]
        TokenEndpoint["Token Endpoint<br/>/oauth2/token"]
    end

    subgraph AgentCore["Bedrock AgentCore"]
        Gateway["AgentCore Gateway<br/>CUSTOM_JWT Authorizer<br/>MCP Streamable HTTP"]
        MCPRuntime["MCP Runtime (PUBLIC)<br/>Python / FastMCP"]
        A2ARuntime["A2A Runtime<br/>Data Analyst Agent"]
    end

    subgraph DataLayer["VPC (Private Isolated)"]
        Aurora["Aurora PostgreSQL<br/>Serverless v2<br/>Data API"]
        S3["S3 Bucket<br/>社内ドキュメント"]
    end

    Quick -->|"OAuth2<br/>client_credentials"| TokenEndpoint
    LibreChat -->|"OAuth2<br/>authorization_code+PKCE"| TokenEndpoint

    TokenEndpoint -->|"JWT"| Quick
    TokenEndpoint -->|"JWT"| LibreChat

    Quick -->|"MCP + Bearer JWT"| Gateway
    LibreChat -->|"MCP + Bearer JWT"| Gateway

    Gateway -->|"IAM SigV4"| MCPRuntime
    MCPRuntime -->|"A2A JSON-RPC<br/>SigV4"| A2ARuntime
    MCPRuntime -->|"RDS Data API"| Aurora
    MCPRuntime -->|"S3 API"| S3
    A2ARuntime -->|"Bedrock Converse"| Bedrock["Bedrock<br/>Claude Sonnet"]
```

## サンプルのユースケース

| シナリオ | ユーザー入力例 | 利用ツール |
|---------|-------------|-----------|
| 顧客情報の検索 | 「契約金額1000万円以上のアクティブな顧客は？」 | `query_database` |
| プロジェクト状況の確認 | 「進行中のプロジェクトの予算一覧を見せて」 | `query_database` |
| 社内ポリシーの参照 | 「リモートワークのルールを教えて」 | `list_documents` → `get_document` |
| データ分析 | 「この顧客データの傾向を分析して」 | `query_database` → `ask_analyst` |

## サンプルのMCP ツール一覧

| ツール名 | 機能 | バックエンド |
|---------|------|------------|
| `query_database` | SQL (SELECT のみ) を実行 | Aurora PostgreSQL (Data API) |
| `list_documents` | ドキュメント一覧を取得 | S3 |
| `get_document` | ドキュメント内容を取得 | S3 |
| `ask_analyst` | A2A エージェントにデータ分析を依頼 | AgentCore Runtime 上のエージェント |


## CDK スタック構成

| Stack | リソース |
|-------|----------|
| `InternalAgent-Data` | VPC, Aurora Serverless v2, S3, Cognito UserPool, SSM Parameters, DB Seed |
| `InternalAgent-AgentCore` | ECR (import), CfnRuntime x2 (MCP + A2A), CfnGateway, CfnGatewayTarget |

## 認証フロー

```mermaid
sequenceDiagram
    participant Client as MCP Client
    participant Cognito as Cognito<br/>Token Endpoint
    participant GW as AgentCore Gateway
    participant RT as MCP Runtime

    Client->>Cognito: POST /oauth2/token<br/>grant_type=client_credentials<br/>scope=mcp/access
    Cognito-->>Client: access_token (JWT)
    Client->>GW: POST /mcp<br/>Authorization: Bearer JWT
    GW->>GW: JWT 検証<br/>allowedClients / allowedScopes
    GW->>RT: IAM SigV4 署名
    RT-->>GW: MCP Response
    GW-->>Client: MCP Response
```

## セットアップ

### 前提条件

- AWS アカウント (ap-northeast-1)
- Node.js 18+, Python 3.12+, uv, Docker
- AWS CDK v2

### デプロイ

```bash
cd cdk
npm install

# AWS profile / account を設定 (どちらか必須)
export AWS_PROFILE=<profile>            # 推奨: profile経由でCDK_DEFAULT_ACCOUNTを自動解決
# あるいは
export CDK_DEFAULT_ACCOUNT=<12-digit>
export AWS_ACCOUNT=$CDK_DEFAULT_ACCOUNT  # build-push スクリプトが参照
export AWS_REGION=ap-northeast-1

# コンテナイメージをビルド & プッシュ
./scripts/build-push.sh
./scripts/build-push-a2a.sh

# CDK デプロイ
npx cdk deploy --all
```

### 動作確認

`cdk deploy --all` 後に、チャット UI なしで CLI からスタック全体を検証できる。
Cognito Client Secret と CDK output を取得したうえで:

```bash
export ACCOUNT_ID=<12-digit-account-id>
export CLIENT_ID=<InternalAgent-Data output AuthClientId>
export CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id <UserPoolId> --client-id ${CLIENT_ID} \
  --query 'UserPoolClient.ClientSecret' --output text)
export TOKEN_ENDPOINT="https://internal-agent-mcp-${ACCOUNT_ID}.auth.ap-northeast-1.amazoncognito.com/oauth2/token"
export GATEWAY_URL=<InternalAgent-AgentCore output GatewayUrl>

# アクセストークン取得
export TOKEN=$(curl -s -X POST "${TOKEN_ENDPOINT}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=${CLIENT_ID}&client_secret=${CLIENT_SECRET}&scope=mcp/access" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 4つの MCP ツールを一覧
curl -s -X POST "${GATEWAY_URL}" \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### クライアント接続

| クライアント | 接続方式 | 認証フロー |
|------------|---------|-----------|
| Amazon Quick | Connectors → MCP | Service authentication (client_credentials) |
| LibreChat | `librechat.yaml` OAuth 設定 | authorization_code + PKCE |

## セキュリティ

| レイヤー | 保護方式 |
|---------|---------|
| Client → Gateway | Cognito JWT (OAuth 2.0) |
| Gateway → Runtime | IAM SigV4 |
| Runtime → Aurora | RDS Data API (IAM 認証) |
| Runtime → S3 | IAM ロール (デプロイされたバケットにスコープ限定) |
| DB アクセス制御 | SELECT 文のみ許可 + 危険キーワードブロック |
| ネットワーク | Private Isolated Subnet + VPC Endpoints |

### 本番利用に向けたセキュリティ上の考慮事項

本サンプルはエージェントからデータへのアクセス実装例であり、**そのまま本番利用することを想定していません**。本番環境への適用時には以下を検討してください。

**データベースアクセス**

- `query_database` ツールは文字列ベースの拒否リストで危険な SQL キーワードをブロックしていますが、エンコードやネストクエリ等で回避される可能性があります。本番ではデータベースレベルのアクセス制御を実装してください。具体的には、PostgreSQL の READ-ONLY ユーザーを作成し、`GRANT SELECT` を特定テーブルのみに限定し、管理系テーブルを除外します。
- エンドユーザーごとのアクセスレベルの区別はありません。すべてのリクエストが同一の DB 認証情報を共有します。
- LLM エージェントと DB の間にセマンティックレイヤー (クエリテンプレート、パラメータ化クエリ等) を導入し、実行可能なクエリを制約することを検討してください。
- LLM ガードレールやロジックベースのガードレールによる、生成 SQL の検証・制限を検討してください。

**S3 アクセス**

- IAM 権限は本スタックでデプロイされた特定バケットに限定されていますが、バケット内の任意のオブジェクトキーにアクセス可能です。本番では IAM 条件や S3 アクセスポイントでキープレフィックスをさらに制限することを検討してください。

**全般的な推奨事項**

- ユーザーレベルのデータアクセス分離 (行レベルセキュリティ、ユーザー別 IAM ポリシー、テナント別バケット等) を実装する。
- すべてのツール呼び出しの監査ログを記録する。
- Amazon Bedrock Guardrails でエージェントの入出力をフィルタリング・検証する。
- 認証情報を定期的にレビュー・ローテーションする。

## 注意事項

- Runtime は **PUBLIC mode 必須** — Gateway → Runtime の接続は AgentCore コントロールプレーン経由のため
- Cognito `client_credentials` トークンには `aud` claim が含まれない — Gateway の `allowedAudience` は設定しない
- LibreChat は Dynamic Client Registration 非対応 — `librechat.yaml` で OAuth 情報を事前設定
