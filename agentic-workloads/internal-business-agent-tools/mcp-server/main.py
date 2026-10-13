import json
import logging
import os

import boto3
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

mcp = FastMCP("internal_mcp_server", host="0.0.0.0", stateless_http=True)

REGION = os.environ.get("AWS_REGION", "ap-northeast-1")

SSM_PREFIX = "/internalagent/"


def _load_config() -> dict:
    ssm = boto3.client("ssm", region_name=REGION)
    response = ssm.get_parameters(
        Names=[
            f"{SSM_PREFIX}CLUSTER_ARN",
            f"{SSM_PREFIX}SECRET_ARN",
            f"{SSM_PREFIX}DATABASE",
            f"{SSM_PREFIX}S3_BUCKET",
            f"{SSM_PREFIX}A2A_AGENT_ARN",
        ]
    )
    params = {p["Name"].split("/")[-1]: p["Value"] for p in response["Parameters"]}
    return params


_config = _load_config()
CLUSTER_ARN = _config.get("CLUSTER_ARN")
SECRET_ARN = _config.get("SECRET_ARN")
DATABASE = _config.get("DATABASE", "internal_db")
S3_BUCKET = _config.get("S3_BUCKET")


@mcp.tool()
def query_database(sql: str) -> str:
    """社内データベースにSQLクエリを実行する (SELECT文のみ)。

    利用可能なテーブル:
    - customers: 顧客情報 (company_name, department, contact_person, contract_start, contract_end, contract_value, status)
    - internal_projects: 社内プロジェクト (project_name, owner, department, status, budget, start_date, end_date, description)

    Args:
        sql: 実行するSELECTクエリ
    """
    sql = sql.strip()

    if not sql.upper().startswith("SELECT"):
        return "エラー: SELECT文のみ実行可能です。"

    dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE", "EXEC"]
    sql_upper = sql.upper()
    for keyword in dangerous:
        if keyword in sql_upper:
            return f"エラー: {keyword}を含むクエリは実行できません。"

    try:
        rds_data = boto3.client("rds-data", region_name=REGION)

        # Switch to the read-only role before executing the user query.
        # This ensures DB-level enforcement of SELECT-only access on
        # application tables, regardless of the generated SQL.
        rds_data.execute_statement(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            database=DATABASE,
            sql="SET ROLE readonly_user",
        )

        response = rds_data.execute_statement(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            database=DATABASE,
            sql=sql,
            includeResultMetadata=True,
        )

        columns = [col["name"] for col in response.get("columnMetadata", [])]
        rows = []
        for record in response.get("records", []):
            row = {}
            for i, field in enumerate(record):
                value = None
                if "stringValue" in field:
                    value = field["stringValue"]
                elif "longValue" in field:
                    value = field["longValue"]
                elif "doubleValue" in field:
                    value = field["doubleValue"]
                elif "booleanValue" in field:
                    value = field["booleanValue"]
                elif "isNull" in field and field["isNull"]:
                    value = None
                row[columns[i]] = value
            rows.append(row)

        if not rows:
            return "該当するデータが見つかりませんでした。"

        return json.dumps(rows, ensure_ascii=False, indent=2)

    except Exception as e:
        return f"クエリ実行エラー: {str(e)}"


@mcp.tool()
def list_documents(prefix: str = "") -> str:
    """社内ドキュメントの一覧を取得する。

    Args:
        prefix: フォルダパス (例: "policies/", "guides/")。空で全件取得。
    """
    try:
        s3 = boto3.client("s3", region_name=REGION)
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix, MaxKeys=50)
        objects = response.get("Contents", [])
        if not objects:
            return "ドキュメントが見つかりませんでした。"

        docs = []
        for obj in objects:
            if obj["Key"].endswith("/"):
                continue
            docs.append({
                "path": obj["Key"],
                "size_bytes": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })
        return json.dumps(docs, ensure_ascii=False, indent=2)

    except Exception as e:
        return f"一覧取得エラー: {str(e)}"


@mcp.tool()
def get_document(key: str) -> str:
    """社内ドキュメントの内容を取得する。

    Args:
        key: ドキュメントのパス (例: "policies/company-policy.txt")
    """
    try:
        s3 = boto3.client("s3", region_name=REGION)
        response = s3.get_object(Bucket=S3_BUCKET, Key=key)
        content = response["Body"].read().decode("utf-8")

        max_chars = 10000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n... (以下省略)"

        return content

    except Exception as e:
        return f"取得エラー: {str(e)}"


@mcp.tool()
def ask_analyst(data: str, question: str) -> str:
    """データ分析の専門Agentに分析を依頼する。

    データ（SQLの結果やドキュメントの内容）と質問を渡すと、
    傾向分析・インサイト・推奨アクションを返す。

    Args:
        data: 分析対象のデータ（JSON、テキスト等）
        question: 分析の観点や質問（例: "売上の傾向は？", "リスクのある顧客は？"）
    """
    import httpx
    from urllib.parse import quote
    from uuid import uuid4
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    a2a_runtime_arn = _config.get("A2A_AGENT_ARN")
    if not a2a_runtime_arn:
        return "エラー: A2A Agent が設定されていません。"

    logger.info(f"[MCP] ask_analyst called. A2A ARN: {a2a_runtime_arn}")
    logger.info(f"[MCP] Data length: {len(data)}, Question: {question}")

    encoded_arn = quote(a2a_runtime_arn, safe="")
    endpoint = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": str(uuid4()),
                "parts": [{"kind": "text", "text": f"以下のデータを分析してください。\n\n## データ\n{data}\n\n## 質問\n{question}"}],
            }
        },
    }

    try:
        import json as json_mod
        body = json_mod.dumps(payload)

        session = boto3.Session()
        credentials = session.get_credentials().get_frozen_credentials()
        aws_request = AWSRequest(method="POST", url=endpoint, data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(aws_request)

        with httpx.Client(timeout=55.0) as client:
            response = client.post(
                endpoint,
                content=body,
                headers=dict(aws_request.headers),
            )
            logger.info(f"[MCP] A2A response status: {response.status_code}")
            response.raise_for_status()
            result = response.json()

        parts = result.get("result", {}).get("message", {}).get("parts", [])
        text_parts = [p["text"] for p in parts if p.get("kind") == "text"]
        logger.info(f"[MCP] A2A response received. Parts: {len(text_parts)}")
        return "\n".join(text_parts) if text_parts else json_mod.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[MCP] A2A call error: {str(e)}")
        return f"分析Agentへの問い合わせエラー: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
