# NemoClaw on AWS with Bedrock

[NVIDIA NemoClaw](https://github.com/NVIDIA/NemoClaw)をAWS EC2上にデプロイし、[Amazon Bedrock](https://aws.amazon.com/bedrock/)経由でClaude等のLLMを推論バックエンドとして利用するためのTerraformモジュール。

[LiteLLM](https://docs.litellm.ai/)をOpenAI互換プロキシとして挟むことで、BedrockのAPIをNemoClawから利用可能にしている。

## アーキテクチャ

```
OpenClaw TUI (sandbox)
  → https://inference.local/v1 (OpenShell Gateway)
    → http://172.17.0.1:4000/v1 (LiteLLM proxy on host)
      → Bedrock API (ap-northeast-1)
```

## 前提条件

- Terraform >= 1.5
- AWS CLIが認証済み
- [Session Manager Plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)がインストール済み（SSM接続に必要）
- 使用するBedrockモデルへのアクセスが有効化済み

## デプロイ

```bash
terraform init
terraform apply
```

apply完了後、User Data（Docker + LiteLLM + NemoClawインストール）の完了まで3〜5分待つ。

## セットアップ

### 1. SSMで接続

```bash
# terraform outputに接続コマンドが表示される
aws ssm start-session --region ap-northeast-1 --target <instance-id>
```

### 2. NemoClawのonboardを実行

```bash
sudo su - ec2-user
nemoclaw onboard
```

onboardの対話型ウィザードで以下を選択:

| ステップ | 選択肢 |
|---|---|
| Inference options | `3) Other OpenAI-compatible endpoint` |
| Base URL | `http://172.17.0.1:4000/v1` |
| API key | `dummy`（LiteLLMにmaster_keyを設定していないため） |
| Model | `claude-opus`（`variables.tf`の`litellm_model_name`に対応） |

### 3. 動作確認

onboard完了後:

```bash
nemoclaw my-assistant connect
openclaw tui
```

TUIが起動し、ステータスバーに `inference/claude-opus` と表示されれば成功。

## ダッシュボード

SSMポートフォワードでローカルからNemoClawダッシュボードにアクセスできる:

```bash
# terraform outputに表示されるコマンドを使用
aws ssm start-session --region ap-northeast-1 --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
```

ブラウザで `http://127.0.0.1:18789` を開く（onboard完了時に表示されるトークン付きURLを使用）。

## 変数

| 変数 | デフォルト | 説明 |
|---|---|---|
| `instance_type` | `m5.xlarge` | EC2インスタンスタイプ（4 vCPU / 16GB RAM） |
| `volume_size` | `40` | ルートEBSボリュームサイズ（GB） |
| `bedrock_model_id` | `global.anthropic.claude-opus-4-6-v1` | BedrockモデルID（[推論プロファイル](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)推奨） |
| `bedrock_region` | `ap-northeast-1` | Bedrock APIのリージョン |
| `litellm_model_name` | `claude-opus` | NemoClawのonboardで指定するモデルエイリアス |
| `name_prefix` | `nemoclaw` | リソース名のプレフィックス |

## クリーンアップ

```bash
terraform destroy
```

