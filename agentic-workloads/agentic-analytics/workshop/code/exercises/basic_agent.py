#!/usr/bin/env python3
"""
Local Strands Agent — Step 1: Build Your First Agent
Connects directly to Aurora PostgreSQL with a simple @tool function.
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

# ============================================================================
# TODO 1.2: Configure the Bedrock Model
# This connects your agent to Claude on Amazon Bedrock.
# Hint: BedrockModel(model_id="global.anthropic.claude-sonnet-4-6", streaming=True)
# ============================================================================
bedrock_model = None  # TODO 1.2: Replace with BedrockModel(...)

# ============================================================================
# Database connection helper
# ============================================================================
def get_db_connection():
    """Connect to Aurora PostgreSQL using credentials from Secrets Manager"""
    secrets = boto3.client('secretsmanager', region_name=os.getenv('AWS_REGION', 'us-east-1'))
    secret = secrets.get_secret_value(SecretId=os.getenv('AURORA_SECRET_ARN'))
    creds = json.loads(secret['SecretString'])
    return psycopg2.connect(
        host=os.getenv('AURORA_ENDPOINT'),
        port=5432,
        database=os.getenv('DATABASE_NAME', 'timely_unicorn'),
        user=creds['username'],
        password=creds['password']
    )

# ============================================================================
# TODO 1.3: Add the @tool decorator
# The @tool decorator turns this function into a tool the agent can call.
# The agent reads the docstring to know WHEN to use this tool.
# Hint: Just add @tool on the line above the function definition
# ============================================================================
# TODO 1.3: Add @tool decorator here
def get_top_customers(limit: int = 5) -> str:
    """Get the top customers by revenue. Use this when the user asks about best customers, highest revenue customers, or top spenders."""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM top_revenue_customers LIMIT %s", (limit,))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
    conn.close()
    results = [dict(zip(columns, row)) for row in rows]
    return json.dumps(results, default=str)

# ============================================================================
# TODO 1.4: Create the Agent
# Wire together the model, system prompt, and tools.
# Hint: Agent(model=bedrock_model, system_prompt="You are a unicorn rental analytics assistant...", tools=[get_top_customers])
# ============================================================================
agent = None  # TODO 1.4: Replace with Agent(...)

# ============================================================================
# Test the agent
# ============================================================================
if __name__ == "__main__":
    if agent is None:
        print("Please complete TODOs 1-3 first!")
    else:
        print("Testing your Strands Agent...")
        print("=" * 50)
        result = agent("Show me the top 5 customers by revenue")
        print("\nAgent response:")
        print(result)
