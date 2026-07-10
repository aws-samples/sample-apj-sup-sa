#!/usr/bin/env python3
"""
Local Strands Agent — connects directly to Aurora PostgreSQL with @tool functions.
Use this for local testing before deploying to AgentCore.
"""

import os
import json
import boto3
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / "app" / "agentcore_strands" / ".env")
if not os.getenv("AURORA_ENDPOINT"):
    load_dotenv(Path(__file__).resolve().parent.parent / "app" / "agentcore_strands" / "config.env")

from strands import Agent, tool
from strands.models import BedrockModel

bedrock_model = BedrockModel(
    model_id="global.anthropic.claude-sonnet-4-6",
    streaming=True
)

def get_db_connection():
    secrets = boto3.client('secretsmanager', region_name=os.getenv('AWS_REGION', 'us-east-1'))
    secret = secrets.get_secret_value(SecretId=os.getenv('AURORA_SECRET_ARN'))
    creds = json.loads(secret['SecretString'])
    return psycopg2.connect(
        host=os.getenv('AURORA_ENDPOINT'), port=5432,
        database=os.getenv('DATABASE_NAME', 'timely_unicorn'),
        user=creds['username'], password=creds['password']
    )

@tool
def get_top_customers(limit: int = 5) -> str:
    """Get the top customers by revenue. Use this when the user asks about best customers, highest revenue customers, or top spenders."""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM top_revenue_customers LIMIT %s", (limit,))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
    conn.close()
    return json.dumps([dict(zip(columns, row)) for row in rows], default=str)

agent = Agent(
    model=bedrock_model,
    system_prompt="You are a unicorn rental analytics assistant. Use the available tools to answer questions about the business.",
    tools=[get_top_customers]
)

if __name__ == "__main__":
    print("Testing local Strands Agent...")
    result = agent("Show me the top 5 customers by revenue")
    print(result)
