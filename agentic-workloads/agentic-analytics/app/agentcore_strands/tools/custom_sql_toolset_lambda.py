"""
Custom SQL Toolset Lambda — generates SQL dynamically using Glue schema and Bedrock KB RAG.
Workflow: Get schema from Glue -> RAG retrieve from Bedrock KB -> Return context for SQL generation
Includes execute_sql_tool for human-in-the-loop approval workflow.
"""

import json
import re
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
            print(f"Role: {user_info['role']}")
    
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


# Above this size text_to_sql_tool returns a table index instead of the full schema.
SCHEMA_FULL_MAX_CHARS = int(os.environ.get('SCHEMA_FULL_MAX_CHARS', '32000'))


def get_schema_context(args, rls_context):
    """Get table schemas from Glue Data Catalog"""
    tables = args.get('tables', [])
    # Glue Crawler prefixes table names with {database}_{schema}_ (e.g. timely_unicorn_public_)
    # Strip this prefix so the LLM sees actual PostgreSQL table names
    # The crawler names tables <postgres db>_<schema>_<table>; the catalog database
    # itself may have a different (per-environment) name.
    glue_prefix = f"{AURORA_DATABASE}_public_"

    try:
        all_tables = []
        for page in glue_client.get_paginator('get_tables').paginate(DatabaseName=GLUE_DATABASE):
            all_tables.extend(page.get('TableList', []))

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
        # No retrievalConfiguration: a managed knowledge base rejects
        # vectorSearchConfiguration, and the Lambda runtime's boto3 does not know
        # managedSearchConfiguration yet. The default returns 5 results.
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={'text': query},
        )
        results = []
        for r in response.get('retrievalResults', [])[:5]:
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
    
    # Step 1: schema from Glue. Adaptive (see notes/12): the whole schema when it is small;
    # above SCHEMA_FULL_MAX_CHARS only an index of table names and descriptions, and the
    # model calls this tool again with `tables` set to the ones it picked. Benchmarked on a
    # 181-table catalogue: 0.90 execution accuracy at 11.6k tokens vs 0.85 at 50.9k for the
    # full schema.
    requested = args.get('tables') or []
    schema_result = get_schema_context({'tables': requested}, rls_context)
    schemas = json.loads(schema_result['body'])['schemas']

    # Step 2: RAG retrieve business context
    rag_results = rag_retrieve(question)

    # Step 3: Assemble context for LLM
    schema_text = []
    for table, info in schemas.items():
        cols = ', '.join([f"{c['name']} ({c['type']})" for c in info['columns']])
        schema_text.append(f"Table {table}: {info['description']}\nColumns: {cols}")
    schema_mode = 'full' if not requested else 'selected'
    if not requested and len('\n'.join(schema_text)) > SCHEMA_FULL_MAX_CHARS:
        schema_mode = 'index'
        schema_text = [f"- {t}: {info['description'] or 'no description'} ({len(info['columns'])} columns)"
                       for t, info in schemas.items()]
        schema_text.insert(0, "The schema is too large to send whole. This is the table index only. "
                              "Pick the tables and views you need, then call text_to_sql_tool again "
                              "with the same question and `tables` set to them to get their columns.")

    rag_text = '\n'.join([f"- {r['content']}" for r in rag_results])

    context = f"""DATABASE SCHEMA ({schema_mode}):
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
            'schema_mode': schema_mode,
            'rag_sources': len(rag_results),
        })
    }


# ---------------------------------------------------------------------------
# Read-only SQL guard
#
# The database is the real enforcement point: every query runs inside a
# READ ONLY transaction with a statement timeout, so INSERT/UPDATE/DELETE/DDL
# (including data-modifying CTEs) fail in PostgreSQL itself. The checks below
# close the gaps a read-only transaction leaves open:
#   - exactly one statement, so "SELECT 1; COMMIT; DROP ..." cannot end the
#     read-only transaction and run something after it;
#   - it starts with SELECT or WITH;
#   - no call to functions that change session state or touch the server,
#     above all set_config(), which could rewrite the RLS session variables
#     (app.current_account_id) from inside a SELECT and read another tenant.
# String literals, quoted identifiers and comments are blanked before the
# keyword checks, so a column like "updated_at" or a literal 'DROP' is fine.
# ---------------------------------------------------------------------------
SQL_STATEMENT_TIMEOUT_MS = int(os.environ.get('SQL_STATEMENT_TIMEOUT_MS', '15000'))
MAX_RESULT_ROWS = 100
_BLOCKED_FUNCTIONS = (
    'set_config', 'pg_read_file', 'pg_read_binary_file', 'pg_ls_dir', 'pg_stat_file',
    'lo_import', 'lo_export', 'lo_get', 'lo_put', 'dblink', 'dblink_exec',
    'pg_terminate_backend', 'pg_cancel_backend', 'pg_reload_conf', 'pg_sleep',
    'pg_advisory_lock', 'pg_advisory_xact_lock', 'query_to_xml', 'query_to_json',
)
_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_$]*')


def _ident_char(ch):
    return ch.isalnum() or ch in '_$'


def _mask_sql(sql):
    """Return (masked_sql, quoted_identifiers): literals and comments become spaces."""
    out, idents, i, n = [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == '-' and sql.startswith('--', i):
            j = sql.find('\n', i)
            j = n if j == -1 else j
            out.append(' ' * (j - i)); i = j
        elif c == '/' and sql.startswith('/*', i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith('/*', j):
                    depth += 1; j += 2
                elif sql.startswith('*/', j):
                    depth -= 1; j += 2
                else:
                    j += 1
            if depth:
                raise ValueError('Unterminated comment')
            out.append(' ' * (j - i)); i = j
        elif c in ("'", '"'):
            # E'...' takes backslash escapes, so a \' would end the literal here but
            # not in PostgreSQL. Refuse escape strings instead of modelling them.
            if c == "'" and i and sql[i - 1] in 'eE' and (i < 2 or not _ident_char(sql[i - 2])):
                raise ValueError("Escape string literals (E'...') are not allowed")
            j = i + 1
            while True:
                k = sql.find(c, j)
                if k == -1:
                    raise ValueError('Unterminated quoted string or identifier')
                if k + 1 < n and sql[k + 1] == c:  # doubled quote = escaped quote
                    j = k + 2
                    continue
                break
            if c == '"':
                idents.append(sql[i + 1:k].replace('""', '"'))
            out.append(' ' * (k + 1 - i)); i = k + 1
        elif c == '$' and not (i and _ident_char(sql[i - 1])):  # a$b$ is one identifier
            m = re.match(r'\$([A-Za-z_][A-Za-z0-9_]*)?\$', sql[i:])
            if m:
                tag = m.group(0)
                k = sql.find(tag, i + len(tag))
                if k == -1:
                    raise ValueError('Unterminated dollar-quoted string')
                out.append(' ' * (k + len(tag) - i)); i = k + len(tag)
            else:
                out.append(c); i += 1
        else:
            out.append(c); i += 1
    return ''.join(out), idents


def validate_readonly_sql(sql):
    """Return an error message if the SQL is not a single read-only query, else None."""
    if not sql or not sql.strip():
        return 'sql is required'
    try:
        masked, quoted = _mask_sql(sql)
    except ValueError as e:
        return str(e)
    body = masked.strip()
    if body.endswith(';'):
        body = body[:-1].rstrip()
    if ';' in body:
        return 'Only one SQL statement is allowed'
    words = [w.lower() for w in _IDENT_RE.findall(body)]
    if not words or words[0] not in ('select', 'with'):
        return 'Only SELECT queries (optionally starting with WITH) are allowed'
    # Unicode-escaped names (U&"set\005fconfig") would slip past the blocked-name check
    # below while PostgreSQL still resolves them, so they are refused outright.
    if re.search(r'(?i)(?<![A-Za-z0-9_$])u&', body) or 'uescape' in words \
            or any('\\' in q for q in quoted):
        return 'Unicode-escaped identifiers and strings are not allowed'
    names = set(words) | {q.lower() for q in quoted}
    for fn in _BLOCKED_FUNCTIONS:
        if fn in names:
            return f'Function not allowed in queries: {fn}'
    return None


def _session_changed(rls_context, after):
    """True if the RLS variables after the query differ from what get_db_connection set."""
    ctx = rls_context or {}
    after = after or {}
    return ((after.get('a') or '') != (ctx.get('account_id') or '')
            or (after.get('r') or '') != (ctx.get('role') or ''))


def execute_sql(args, rls_context):
    """Execute an approved read-only SQL query against the database."""
    sql = args.get('sql', '')
    error = validate_readonly_sql(sql)
    if error:
        return {'statusCode': 400, 'body': json.dumps({'success': False, 'error': error})}

    conn = None
    try:
        conn = get_db_connection(rls_context)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Scope both settings to this transaction only.
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = %s", [SQL_STATEMENT_TIMEOUT_MS])
            # The masker assumes plain '...' strings take no backslash escapes.
            cur.execute("SET LOCAL standard_conforming_strings = on")
            cur.execute(sql)
            rows = cur.fetchmany(MAX_RESULT_ROWS + 1) if cur.description else []
            columns = [desc[0] for desc in cur.description] if cur.description else []
            # Backstop for the static check: if the query changed the RLS session
            # variables, its rows may belong to another tenant, so drop them.
            cur.execute("SELECT current_setting('app.current_account_id', true) AS a, "
                        "current_setting('app.current_user_role', true) AS r")
            after = cur.fetchone()
        conn.rollback()
        if _session_changed(rls_context, after):
            print("execute_sql refused: query changed the RLS session variables")
            return {'statusCode': 400, 'body': json.dumps(
                {'success': False, 'error': 'Query changed session settings and was refused'})}
        truncated = len(rows) > MAX_RESULT_ROWS
        rows = rows[:MAX_RESULT_ROWS]
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'columns': columns,
                'row_count': len(rows),
                'truncated': truncated,
                'data': rows,
            }, default=json_serializer)
        }
    except Exception as e:
        print(f"execute_sql failed: {type(e).__name__}: {e}")
        return {'statusCode': 500, 'body': json.dumps({'success': False, 'error': str(e)})}
    finally:
        if conn is not None:
            conn.close()
