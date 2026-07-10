import json
import boto3
import psycopg2
from psycopg2.extras import RealDictCursor
from decimal import Decimal
from datetime import datetime
import os

import jwt  # PyJWT (with cryptography) — provided by the pyjwt Lambda layer
from jwt import PyJWKClient

import time

# Aurora PostgreSQL configuration from environment
AURORA_ENDPOINT = os.environ.get('AURORA_ENDPOINT')
AURORA_DATABASE = os.environ.get('AURORA_DATABASE', 'timely_unicorn')
AURORA_USERNAME = os.environ.get('AURORA_USERNAME', 'postgres')
# Two secrets are wired to this Lambda: the 'postgres' table-owner (bypasses RLS)
# and the 'app_user' non-owner (RLS enforced). Which one we use is decided at
# RUNTIME by the RLS feature flag in SSM Parameter Store (see _rls_enabled),
# NOT by a redeploy — so flipping RLS never changes this Lambda's config/version.
AURORA_SECRET_ARN = os.environ.get('AURORA_SECRET_ARN')            # postgres owner (RLS off)
APP_AURORA_SECRET_ARN = os.environ.get('APP_AURORA_SECRET_ARN')    # app_user (RLS on)
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

# Bedrock configuration for embeddings
EMBEDDING_MODEL = 'amazon.titan-embed-text-v2:0'
EMBEDDING_DIMENSION = 1024

secrets_client = boto3.client('secretsmanager', region_name=AWS_REGION)
bedrock_client = boto3.client('bedrock-runtime', region_name=AWS_REGION)
ssm_client = boto3.client('ssm', region_name=AWS_REGION)

# ---------------------------------------------------------------------------
# RLS feature flag (runtime toggle via SSM Parameter Store)
#
# RLS_PARAM_NAME holds a value of 'enabled' or 'disabled'. When 'enabled', the
# Lambda connects as the non-owner 'app_user' so PostgreSQL enforces Row-Level
# Security; otherwise it connects as the 'postgres' owner (RLS bypassed). The
# participant flips this in the SSM console — it takes effect within the cache
# TTL with no redeploy. The flag is cached at module scope so a warm Lambda hits
# SSM at most once per TTL, not on every invocation.
#
# Fail-closed: if the flag can't be read AND an app_user secret exists, default
# to ENFORCED (safer to over-restrict than to leak another tenant's rows).
# ---------------------------------------------------------------------------
RLS_PARAM_NAME = os.environ.get('RLS_PARAM_NAME', '')
_RLS_CACHE_TTL = 60  # seconds
_rls_cache = {'enabled': None, 'ts': 0.0}


def _rls_enabled():
    """Return True if RLS enforcement is on, reading the SSM flag (cached ~60s)."""
    if not RLS_PARAM_NAME or not APP_AURORA_SECRET_ARN:
        return False  # no app_user secret wired -> can only use postgres
    now = time.time()
    if _rls_cache['enabled'] is None or (now - _rls_cache['ts']) > _RLS_CACHE_TTL:
        try:
            val = ssm_client.get_parameter(Name=RLS_PARAM_NAME)['Parameter']['Value']
            _rls_cache['enabled'] = (str(val).strip().lower() == 'enabled')
        except Exception as e:
            # Fail closed: if we have an app_user secret but can't read the flag,
            # enforce RLS rather than silently fall back to the owner role.
            print(f"RLS flag read failed ({type(e).__name__}: {e}) — defaulting to ENFORCED")
            _rls_cache['enabled'] = True
        _rls_cache['ts'] = now
    return _rls_cache['enabled']


def _current_secret_arn():
    """Pick the DB secret based on the runtime RLS flag."""
    return APP_AURORA_SECRET_ARN if _rls_enabled() else AURORA_SECRET_ARN

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
# Module-scoped, lazily initialized; PyJWKClient keeps its own key cache.
_jwks_client = PyJWKClient(_JWKS_URL) if _JWKS_URL else None


def _verify_jwt(token):
    """Verify a Cognito access token and return its claims, or None if invalid."""
    if not _jwks_client or not COGNITO_CLIENT_ID:
        # Misconfiguration (env vars not set) — fail closed rather than trust blindly.
        print("JWT verification not configured (missing COGNITO_USER_POOL_ID / "
              "COGNITO_USER_LOGIN_CLIENT_ID) — denying RLS context")
        return None
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        # Cognito ACCESS tokens carry client_id (not aud); pin RS256 to avoid
        # algorithm-confusion, verify signature + iss + exp here, then check the
        # Cognito-specific claims explicitly below.
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

def get_db_connection(rls_context=None):
    secret = secrets_client.get_secret_value(SecretId=_current_secret_arn())
    creds = json.loads(secret['SecretString'])
    conn = psycopg2.connect(host=AURORA_ENDPOINT, port=5432, database=AURORA_DATABASE,
                            user=creds['username'], password=creds['password'])
    # Set RLS context from JWT claims
    if rls_context and (rls_context.get('account_id') or rls_context.get('role')):
        with conn.cursor() as cur:
            if rls_context.get('account_id'):
                cur.execute("SET app.current_account_id = %s", [rls_context['account_id']])
            if rls_context.get('role'):
                cur.execute("SET app.current_user_role = %s", [rls_context['role']])
    return conn

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    # Extract RLS context from JWT claims
    rls_context = _extract_rls_context_from_jwt(context)
    print(f"DEBUG RLS context: {rls_context}")
    
    try:
        delimiter = "___"
        tool_name = ""
        if context and hasattr(context, 'client_context') and context.client_context:
            custom = getattr(context.client_context, 'custom', None)
            if custom and 'bedrockAgentCoreToolName' in custom:
                original_tool_name = custom['bedrockAgentCoreToolName']
                tool_name = original_tool_name.split(delimiter)[-1] if delimiter in original_tool_name else original_tool_name
        if not tool_name:
            tool_name = event.get('name', '')
            tool_name = tool_name.split(delimiter)[-1] if delimiter in tool_name else tool_name
        
        arguments = event.get('arguments', {}) if 'arguments' in event else event
        print(f"Tool: {tool_name}, Args: {arguments}")
        
        handlers = {
            'list_tables_tool': list_tables,
            'get_accounts_tool': get_accounts,
            'get_unicorns_tool': get_unicorns,
            'get_customers_tool': get_customers,
            'get_bookings_tool': get_bookings,
            'get_transactions_tool': get_transactions,
            'get_unicorn_availability_tool': get_unicorn_availability,
            'get_users_tool': get_users,
            'get_subscription_plans_tool': get_subscription_plans,
            'search_unicorns_tool': search_unicorns,
            'search_customers_tool': search_customers,
            'get_booking_summary_tool': get_booking_summary,
            'get_revenue_summary_tool': get_revenue_summary,
            'check_db_status_tool': check_db_status,
            # View-based tools
            'get_daily_bookings_summary_tool': get_daily_bookings_summary,
            'get_monthly_revenue_summary_tool': get_monthly_revenue_summary,
            'get_current_unicorn_availability_tool': get_current_unicorn_availability,
            'get_calendar_bookings_tool': get_calendar_bookings,
            'get_customer_retention_metrics_tool': get_customer_retention_metrics,
            'get_top_revenue_breeds_tool': get_top_revenue_breeds,
            'get_top_revenue_customers_tool': get_top_revenue_customers,
            'get_revenue_by_time_and_day_tool': get_revenue_by_time_and_day,
            'get_unicorns_due_maintenance_tool': get_unicorns_due_maintenance,
            'get_account_subscription_status_tool': get_account_subscription_status,
            'get_customer_lifetime_value_tool': get_customer_lifetime_value,
            'get_seasonal_trends_tool': get_seasonal_trends,
            'get_customer_segmentation_tool': get_customer_segmentation,
            # Semantic search tool
            'semantic_search_tool': semantic_search,
            # Account info tool (no args — uses JWT/RLS)
            'get_my_account_info_tool': get_my_account_info,
        }
        
        if tool_name in handlers:
            return handlers[tool_name](arguments, rls_context)
        return error_response(f'Unknown tool: {tool_name}')
    except Exception as e:
        return error_response(str(e))

def success_response(data):
    return {'statusCode': 200, 'body': json.dumps(data, default=json_serializer)}

def error_response(message):
    return {'statusCode': 500, 'body': json.dumps({'success': False, 'error': message})}

def json_serializer(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def list_tables(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        tables = [row[0] for row in cur.fetchall()]
    conn.close()
    return success_response({'success': True, 'tables': tables})

def get_my_account_info(args, rls_context):
    """Return the current tenant's account info. No args needed — uses JWT/RLS."""
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT a.account_name, a.status, sp.plan_name, sp.monthly_price,
                   a.current_user_count, sp.user_limit, a.current_storage_usage_gb, sp.storage_limit_gb,
                   a.billing_cycle, a.industry, a.website
            FROM accounts a
            JOIN subscription_plans sp ON a.plan_id = sp.plan_id
        """)
        row = cur.fetchone()
    conn.close()
    if not row:
        return error_response('No account found for current user')
    return success_response({'success': True, 'account': row})

def get_accounts(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT a.*, sp.plan_name FROM accounts a JOIN subscription_plans sp ON a.plan_id = sp.plan_id")
        accounts = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(accounts), 'accounts': accounts})

def get_unicorns(args, rls_context):
    available_only = args.get('available_only', False)
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM unicorns WHERE 1=1"
        params = []
        if available_only:
            sql += " AND is_available = true"
        cur.execute(sql, params)
        unicorns = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(unicorns), 'unicorns': unicorns})

def get_customers(args, rls_context):
    customer_type = args.get('customer_type')
    limit = args.get('limit', 100)
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM customers WHERE 1=1"
        params = []
        if customer_type:
            sql += " AND customer_type = %s"
            params.append(customer_type)
        sql += " LIMIT %s"
        params.append(limit)
        cur.execute(sql, params)
        customers = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(customers), 'customers': customers})

def get_bookings(args, rls_context):
    start_date = args.get('start_date')
    end_date = args.get('end_date')
    limit = args.get('limit', 100)
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = """SELECT b.*, u.name as unicorn_name, c.first_name, c.last_name 
                 FROM bookings b 
                 JOIN unicorns u ON b.unicorn_id = u.unicorn_id 
                 JOIN customers c ON b.customer_id = c.customer_id WHERE 1=1"""
        params = []
        if start_date:
            sql += " AND b.start_datetime >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND b.end_datetime <= %s"
            params.append(end_date)
        sql += " ORDER BY b.start_datetime DESC LIMIT %s"
        params.append(limit)
        cur.execute(sql, params)
        bookings = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(bookings), 'bookings': bookings})

def get_transactions(args, rls_context):
    transaction_type = args.get('transaction_type')
    limit = args.get('limit', 100)
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM transactions WHERE 1=1"
        params = []
        if transaction_type:
            sql += " AND transaction_type = %s"
            params.append(transaction_type)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        cur.execute(sql, params)
        transactions = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(transactions), 'transactions': transactions})

def get_unicorn_availability(args, rls_context):
    unicorn_id = args.get('unicorn_id')
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM unicorn_availability WHERE unicorn_id = %s ORDER BY created_at DESC LIMIT 10"
        cur.execute(sql, [unicorn_id])
        availability = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'availability': availability})

def get_users(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT user_id, account_id, username, email, first_name, last_name, role, is_active FROM users"
        params = []
        cur.execute(sql, params)
        users = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(users), 'users': users})

def get_subscription_plans(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM subscription_plans WHERE is_active = true")
        plans = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'plans': plans})

def search_unicorns(args, rls_context):
    query = args.get('query', '')
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM unicorns WHERE (LOWER(name) LIKE %s OR LOWER(breed) LIKE %s OR LOWER(color) LIKE %s)"
        params = [f'%{query.lower()}%'] * 3
        cur.execute(sql, params)
        unicorns = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(unicorns), 'unicorns': unicorns})

def search_customers(args, rls_context):
    query = args.get('query', '')
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM customers WHERE (LOWER(first_name) LIKE %s OR LOWER(last_name) LIKE %s OR LOWER(email) LIKE %s OR LOWER(first_name || ' ' || last_name) LIKE %s)"
        params = [f'%{query.lower()}%'] * 4
        sql += " LIMIT 50"
        cur.execute(sql, params)
        customers = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(customers), 'customers': customers})

def get_booking_summary(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = """SELECT COUNT(*) as total_bookings, 
                        SUM(total_cost) as total_revenue,
                        AVG(total_cost) as avg_booking_value,
                        COUNT(DISTINCT customer_id) as unique_customers,
                        COUNT(DISTINCT unicorn_id) as unicorns_used
                 FROM bookings WHERE 1=1"""
        params = []
        cur.execute(sql, params)
        summary = cur.fetchone()
    conn.close()
    return success_response({'success': True, 'summary': summary})

def get_revenue_summary(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM monthly_revenue_summary ORDER BY year_month")
        revenue = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'monthly_revenue': revenue})

def check_db_status(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        tables = [row[0] for row in cur.fetchall()]
    conn.close()
    required = ['subscription_plans', 'accounts', 'users', 'customers', 'unicorns', 'unicorn_availability', 'bookings', 'transactions']
    missing = [t for t in required if t not in tables]
    return success_response({'db_accessible': True, 'tables': tables, 'missing': missing, 'all_tables_exist': len(missing) == 0})


# View-based tools
def get_daily_bookings_summary(args, rls_context):
    limit = args.get('limit', 100)
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM daily_bookings_summary ORDER BY start_datetime DESC LIMIT %s", [limit])
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(data), 'bookings': data})

def get_monthly_revenue_summary(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM monthly_revenue_summary ORDER BY year_month DESC")
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'monthly_revenue': data})

def get_current_unicorn_availability(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM current_unicorn_availability"
        params = []
        cur.execute(sql, params)
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(data), 'unicorns': data})

def get_calendar_bookings(args, rls_context):
    start_date = args.get('start_date')
    end_date = args.get('end_date')
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM calendar_bookings WHERE 1=1"
        params = []
        if start_date:
            sql += " AND start_date >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND start_date <= %s"
            params.append(end_date)
        sql += " ORDER BY start_date, start_time"
        cur.execute(sql, params)
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(data), 'bookings': data})

def get_customer_retention_metrics(args, rls_context):
    segment = args.get('segment')
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM customer_retention_metrics"
        params = []
        if segment:
            sql += " WHERE retention_segment = %s"
            params.append(segment)
        cur.execute(sql, params)
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(data), 'customers': data})

def get_top_revenue_breeds(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM top_revenue_unicorn_breeds")
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'breeds': data})

def get_top_revenue_customers(args, rls_context):
    limit = args.get('limit', 20)
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM top_revenue_customers LIMIT %s", [limit])
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'customers': data})

def get_revenue_by_time_and_day(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM revenue_by_time_and_day")
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'revenue_patterns': data})

def get_unicorns_due_maintenance(args, rls_context):
    urgency = args.get('urgency')
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM unicorns_due_for_maintenance"
        params = []
        if urgency:
            sql += " WHERE maintenance_urgency = %s"
            params.append(urgency)
        cur.execute(sql, params)
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'count': len(data), 'unicorns': data})

def get_account_subscription_status(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM account_subscription_status")
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'accounts': data})

def get_customer_lifetime_value(args, rls_context):
    limit = args.get('limit', 50)
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM customer_lifetime_value"
        params = []
        sql += " ORDER BY total_revenue DESC LIMIT %s"
        params.append(limit)
        cur.execute(sql, params)
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'customers': data})

def get_seasonal_trends(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM seasonal_trends"
        params = []
        cur.execute(sql, params)
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'trends': data})

def get_customer_segmentation(args, rls_context):
    conn = get_db_connection(rls_context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        sql = "SELECT * FROM customer_segmentation_by_revenue"
        params = []
        cur.execute(sql, params)
        data = cur.fetchall()
    conn.close()
    return success_response({'success': True, 'segments': data})



# Semantic search tool - uses Bedrock Knowledge Base Retrieve API
KNOWLEDGE_BASE_ID = os.environ.get('KNOWLEDGE_BASE_ID', '')

bedrock_agent_runtime = boto3.client('bedrock-agent-runtime', region_name=AWS_REGION)


def semantic_search(args, rls_context):
    """
    Search business context using Bedrock Knowledge Base.
    Finds relevant information based on natural language query.
    
    Args:
        query: Natural language query describing what data the user is looking for
        top_k: Number of results to return (default: 10)
    
    Returns:
        List of relevant context with similarity scores
    """
    query = args.get('query', '')
    top_k = args.get('top_k', 10)
    
    if not query:
        return error_response('Query parameter is required')
    
    if not KNOWLEDGE_BASE_ID:
        return error_response('Knowledge Base ID not configured')
    
    try:
        # Use Bedrock KB Retrieve API
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={'text': query},
            retrievalConfiguration={
                'vectorSearchConfiguration': {
                    'numberOfResults': top_k
                }
            }
        )
        
        results = []
        for result in response.get('retrievalResults', []):
            content = result.get('content', {}).get('text', '')
            score = result.get('score', 0.0)
            metadata = result.get('metadata', {})
            location = result.get('location', {})
            
            results.append({
                'content': content,
                'score': score,
                'source': metadata.get('source', 'business-context.md'),
                'type': metadata.get('type', 'documentation'),
                'location': location
            })
        
        # Extract SQL examples if present
        sql_examples = []
        table_references = []
        
        for r in results:
            content = r['content']
            # Check for SQL patterns
            if 'SELECT' in content.upper() or 'FROM' in content.upper():
                sql_examples.append(content)
            # Check for table references
            for table in ['accounts', 'customers', 'unicorns', 'bookings', 'transactions', 
                         'users', 'subscription_plans', 'unicorn_availability']:
                if table in content.lower():
                    if table not in table_references:
                        table_references.append(table)
        
        return success_response({
            'success': True,
            'query': query,
            'results': results,
            'sql_examples': sql_examples[:3],  # Top 3 relevant SQL examples
            'relevant_tables': table_references,
            'total_results': len(results)
        })
        
    except Exception as e:
        return error_response(f'Semantic search failed: {str(e)}')
