"""
Custom SQL Toolset Lambda — generates SQL dynamically using Glue schema and Bedrock KB RAG.
Workflow: Get schema from Glue -> RAG retrieve from Bedrock KB -> Return context for SQL generation
Includes execute_sql_tool for human-in-the-loop approval workflow.
"""

import json
import os
import boto3
import psycopg2
from psycopg2.extras import RealDictCursor
from decimal import Decimal

import jwt  # PyJWT (with cryptography) — provided by the pyjwt Lambda layer
from jwt import PyJWKClient

GLUE_DATABASE = os.environ.get('GLUE_DATABASE', 'timely_unicorn')
KB_ID = os.environ.get('BEDROCK_KB_ID', '')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

import time

# Aurora config for SQL execution
AURORA_ENDPOINT = os.environ.get('AURORA_ENDPOINT')
AURORA_DATABASE = os.environ.get('AURORA_DATABASE', 'timely_unicorn')
# Two secrets wired in; the RLS feature flag in SSM (read at runtime, cached)
# decides which to use — flipping RLS never changes this Lambda's config/version.
AURORA_SECRET_ARN = os.environ.get('AURORA_SECRET_ARN')            # postgres owner (RLS off)
APP_AURORA_SECRET_ARN = os.environ.get('APP_AURORA_SECRET_ARN')    # app_user (RLS on)

secrets_client = boto3.client('secretsmanager', region_name=AWS_REGION)
glue_client = boto3.client('glue', region_name=AWS_REGION)
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime', region_name=AWS_REGION)
ssm_client = boto3.client('ssm', region_name=AWS_REGION)

# RLS feature flag (runtime toggle via SSM Parameter Store). 'enabled' -> connect
# as app_user (RLS enforced); else as postgres owner (RLS bypassed). Cached at
# module scope so a warm Lambda reads SSM at most once per TTL. Fail-closed to
# ENFORCED if the flag can't be read but an app_user secret is wired.
RLS_PARAM_NAME = os.environ.get('RLS_PARAM_NAME', '')
_RLS_CACHE_TTL = 60  # seconds
_rls_cache = {'enabled': None, 'ts': 0.0}


def _rls_enabled():
    if not RLS_PARAM_NAME or not APP_AURORA_SECRET_ARN:
        return False
    now = time.time()
    if _rls_cache['enabled'] is None or (now - _rls_cache['ts']) > _RLS_CACHE_TTL:
        try:
            val = ssm_client.get_parameter(Name=RLS_PARAM_NAME)['Parameter']['Value']
            _rls_cache['enabled'] = (str(val).strip().lower() == 'enabled')
        except Exception as e:
            print(f"RLS flag read failed ({type(e).__name__}: {e}) — defaulting to ENFORCED")
            _rls_cache['enabled'] = True
        _rls_cache['ts'] = now
    return _rls_cache['enabled']


def _current_secret_arn():
    return APP_AURORA_SECRET_ARN if _rls_enabled() else AURORA_SECRET_ARN

def get_db_connection(rls_context=None):
    secret = secrets_client.get_secret_value(SecretId=_current_secret_arn())
    creds = json.loads(secret['SecretString'])
    conn = psycopg2.connect(host=AURORA_ENDPOINT, port=5432, database=AURORA_DATABASE,
                            user=creds['username'], password=creds['password'])
    if rls_context and (rls_context.get('account_id') or rls_context.get('role')):
        with conn.cursor() as cur:
            if rls_context.get('account_id'):
                cur.execute("SET app.current_account_id = %s", [rls_context['account_id']])
            if rls_context.get('role'):
                cur.execute("SET app.current_user_role = %s", [rls_context['role']])
    return conn

def json_serializer(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

# ---------------------------------------------------------------------------
# JWT verification (defense-in-depth at the service level)
#
# The Runtime and the Gateway each validate this Cognito access token at their
# edge before we ever run, but per zero-trust / OWASP microservices guidance the
# tool Lambda also validates it: signature (RS256 via Cognito JWKS) + issuer +
# token_use + client_id + expiry. Only then do we trust custom:account_id /
# custom:role to drive Row-Level Security. Fail closed — any failure yields no
# RLS context, and RLS then returns zero rows rather than leaking another tenant.
#
# PyJWKClient is created once at module scope and caches the signing keys across
# warm invocations (and provisioned-concurrency instances), so the JWKS endpoint
# is hit at most once per microVM, never on the hot path.
# ---------------------------------------------------------------------------
COGNITO_USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
COGNITO_CLIENT_ID = os.environ.get('COGNITO_USER_LOGIN_CLIENT_ID', '')
_COGNITO_ISSUER = (
    f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
    if COGNITO_USER_POOL_ID else ''
)
_JWKS_URL = f"{_COGNITO_ISSUER}/.well-known/jwks.json" if _COGNITO_ISSUER else ''
_jwks_client = PyJWKClient(_JWKS_URL) if _JWKS_URL else None


def _verify_jwt(token):
    """Verify a Cognito access token and return its claims, or None if invalid."""
    if not _jwks_client or not COGNITO_CLIENT_ID:
        print("JWT verification not configured (missing COGNITO_USER_POOL_ID / "
              "COGNITO_USER_LOGIN_CLIENT_ID) — denying RLS context")
        return None
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=_COGNITO_ISSUER,
            options={"verify_aud": False, "require": ["exp", "iss"]},
        )
        if claims.get("token_use") != "access":
            print(f"JWT rejected: token_use={claims.get('token_use')} (expected access)")
            return None
        if claims.get("client_id") != COGNITO_CLIENT_ID:
            print("JWT rejected: client_id mismatch")
            return None
        return claims
    except Exception as e:
        print(f"JWT verification failed: {type(e).__name__}: {e}")
        return None


def _extract_rls_context_from_jwt(context):
    """Verify the propagated JWT and extract account_id + role for RLS.

    Fail-closed: returns {} on any missing/invalid token, which leaves the RLS
    session vars unset so the database returns no tenant rows.
    """
    if context and hasattr(context, 'client_context') and context.client_context:
        custom = getattr(context.client_context, 'custom', {}) or {}
        propagated_headers = custom.get('bedrockAgentCorePropagatedHeaders', {})
        auth_header = propagated_headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1].strip()
            claims = _verify_jwt(token)
            if claims:
                return {
                    'account_id': claims.get('custom:account_id'),
                    'role': claims.get('custom:role')
                }
    return {}

def lambda_handler(event, context):
    
    # Get propagated headers from Lambda client context (AgentCore Gateway pattern)
    auth_header = None
    user_info = {}
    
    if context and hasattr(context, 'client_context') and context.client_context:
        custom = getattr(context.client_context, 'custom', {}) or {}
        propagated_headers = custom.get('bedrockAgentCorePropagatedHeaders', {})
        auth_header = propagated_headers.get('Authorization')
        
        if auth_header and auth_header.startswith('Bearer '):
            # Decode JWT to get user claims
            import base64
            token = auth_header.split(' ')[1]
            payload = token.split('.')[1]
            # Add padding for base64 decode
            payload += '=' * (4 - len(payload) % 4)
            claims = json.loads(base64.b64decode(payload))
            user_info = {
                'username': claims.get('username'),
                'role': claims.get('custom:role'),
                'sub': claims.get('sub')
            }
            print(f"User: {user_info['username']}, Role: {user_info['role']}")
    
    if not auth_header:
        print("No Authorization header in request")
    
    # Extract RLS context from JWT claims
    rls_context = _extract_rls_context_from_jwt(context)
    
    delimiter = "___"
    tool_name = ""
    if context and hasattr(context, 'client_context') and context.client_context:
        custom = getattr(context.client_context, 'custom', None)
        if custom and 'bedrockAgentCoreToolName' in custom:
            original = custom['bedrockAgentCoreToolName']
            tool_name = original.split(delimiter)[-1] if delimiter in original else original
    if not tool_name:
        tool_name = event.get('name', '')
        tool_name = tool_name.split(delimiter)[-1] if delimiter in tool_name else tool_name
    
    args = event.get('arguments', {}) if 'arguments' in event else event
    
    handlers = {
        'get_schema_context_tool': get_schema_context,
        'text_to_sql_tool': text_to_sql_with_rag,
        'execute_sql_tool': execute_sql,
    }
    
    if tool_name in handlers:
        return handlers[tool_name](args, rls_context)
    return {'statusCode': 500, 'body': json.dumps({'error': f'Unknown tool: {tool_name}'})}


def get_schema_context(args, rls_context):
    """Get table schemas from Glue Data Catalog"""
    tables = args.get('tables', [])
    # Glue Crawler prefixes table names with {database}_{schema}_ (e.g. timely_unicorn_public_)
    # Strip this prefix so the LLM sees actual PostgreSQL table names
    glue_prefix = f"{GLUE_DATABASE}_public_"

    try:
        response = glue_client.get_tables(DatabaseName=GLUE_DATABASE)
        all_tables = response.get('TableList', [])

        result = {}
        for table in all_tables:
            glue_name = table['Name']
            # Strip Glue prefix to get the real PostgreSQL table name
            pg_name = glue_name[len(glue_prefix):] if glue_name.startswith(glue_prefix) else glue_name
            if tables and pg_name not in tables:
                continue
            columns = [{'name': c['Name'], 'type': c['Type'], 'description': c.get('Comment', '')}
                       for c in table.get('StorageDescriptor', {}).get('Columns', [])]
            result[pg_name] = {
                'columns': columns,
                'description': table.get('Description', table.get('Parameters', {}).get('comment', ''))
            }

        return {'statusCode': 200, 'body': json.dumps({'success': True, 'schemas': result})}
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'success': False, 'error': str(e)})}


def rag_retrieve(query):
    """Retrieve relevant business context from Bedrock Knowledge Base"""
    if not KB_ID:
        return [{'content': 'Knowledge Base not configured. Use appropriate JOINs and filter by account_id.', 'score': 0.5}]

    try:
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={'text': query},
            retrievalConfiguration={'vectorSearchConfiguration': {'numberOfResults': 5}}
        )
        results = []
        for r in response.get('retrievalResults', []):
            results.append({
                'content': r.get('content', {}).get('text', ''),
                'score': r.get('score', 0.0)
            })
        return results if results else [{'content': 'No relevant context found.', 'score': 0.0}]
    except Exception as e:
        print(f"RAG retrieve error: {e}")
        return [{'content': f'RAG retrieval failed: {str(e)}', 'score': 0.0}]


def text_to_sql_with_rag(args, rls_context):
    """Main tool: Get schema + RAG context for text-to-SQL generation"""
    question = args.get('question', '')
    
    if not question:
        return {'statusCode': 400, 'body': json.dumps({'error': 'question is required'})}
    
    # Step 1: Get full schema from Glue (all tables)
    schema_result = get_schema_context({'tables': []}, rls_context)
    schemas = json.loads(schema_result['body'])['schemas']
    
    # Step 2: RAG retrieve business context
    rag_results = rag_retrieve(question)
    
    # Step 3: Assemble context for LLM
    schema_text = []
    for table, info in schemas.items():
        cols = ', '.join([f"{c['name']} ({c['type']})" for c in info['columns']])
        schema_text.append(f"Table {table}: {info['description']}\nColumns: {cols}")
    
    rag_text = '\n'.join([f"- {r['content']}" for r in rag_results])
    
    context = f"""DATABASE SCHEMA:
{chr(10).join(schema_text)}

BUSINESS CONTEXT FROM KNOWLEDGE BASE:
{rag_text}

QUERY GUIDELINES:
- Row-Level Security (RLS) handles tenant isolation automatically — do NOT add WHERE account_id clauses
- Use appropriate JOINs based on foreign keys
- Return meaningful column aliases
- Limit results to 100 rows unless aggregating"""

    return {
        'statusCode': 200,
        'body': json.dumps({
            'success': True,
            'question': question,
            'context': context,
            'tables_count': len(schemas),
            'rag_sources': len(rag_results),
        })
    }


def execute_sql(args, rls_context):
    """Execute approved SQL query against the database"""
    sql = args.get('sql', '')
    
    if not sql:
        return {'statusCode': 400, 'body': json.dumps({'error': 'sql is required'})}
    
    # Security: Only allow SELECT statements
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith('SELECT'):
        return {'statusCode': 400, 'body': json.dumps({'error': 'Only SELECT queries are allowed'})}
    
    # Security: Block dangerous patterns
    dangerous = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'TRUNCATE', 'GRANT', 'REVOKE', ';--']
    for pattern in dangerous:
        if pattern in sql_upper:
            return {'statusCode': 400, 'body': json.dumps({'error': f'Forbidden SQL pattern: {pattern}'})}
    
    try:
        conn = get_db_connection(rls_context)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description] if cur.description else []
        conn.close()
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'columns': columns,
                'row_count': len(rows),
                'data': rows[:100]  # Limit to 100 rows
            }, default=json_serializer)
        }
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
