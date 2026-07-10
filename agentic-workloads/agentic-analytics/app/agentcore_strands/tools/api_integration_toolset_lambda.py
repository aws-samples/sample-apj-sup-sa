"""
Create Booking Lambda - Write API for unicorn bookings
"""

import json
import boto3
import psycopg2
from psycopg2.extras import RealDictCursor
from decimal import Decimal
from datetime import datetime
import os
import uuid

import jwt  # PyJWT (with cryptography) — provided by the pyjwt Lambda layer
from jwt import PyJWKClient

import time

# Aurora PostgreSQL configuration
AURORA_ENDPOINT = os.environ.get('AURORA_ENDPOINT')
AURORA_DATABASE = os.environ.get('AURORA_DATABASE', 'timely_unicorn')
# Two secrets wired in; the RLS feature flag in SSM (read at runtime, cached)
# decides which to use — flipping RLS never changes this Lambda's config/version.
AURORA_SECRET_ARN = os.environ.get('AURORA_SECRET_ARN')            # postgres owner (RLS off)
APP_AURORA_SECRET_ARN = os.environ.get('APP_AURORA_SECRET_ARN')    # app_user (RLS on)
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

secrets_client = boto3.client('secretsmanager', region_name=AWS_REGION)
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

def success_response(data):
    return {'statusCode': 200, 'body': json.dumps(data, default=json_serializer)}

def error_response(message):
    return {'statusCode': 400, 'body': json.dumps({'success': False, 'error': message})}

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    # Extract RLS context from JWT claims
    rls_context = _extract_rls_context_from_jwt(context)
    
    try:
        # Extract tool name
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
        
        arguments = event.get('arguments', {}) if 'arguments' in event else event
        print(f"Tool: {tool_name}, Args: {arguments}")
        
        if tool_name == 'create_booking_tool':
            return create_booking(arguments, rls_context)
        
        return error_response(f'Unknown tool: {tool_name}')
    except Exception as e:
        return error_response(str(e))

def create_booking(args, rls_context):
    """Create a new booking for a unicorn rental"""
    
    # Required parameters (account_id removed — RLS handles tenant isolation)
    customer_id = args.get('customer_id')
    unicorn_id = args.get('unicorn_id')
    start_datetime = args.get('start_datetime')
    end_datetime = args.get('end_datetime')
    
    if not all([customer_id, unicorn_id, start_datetime, end_datetime]):
        return error_response('Missing required fields: customer_id, unicorn_id, start_datetime, end_datetime')
    
    # Optional parameters
    special_requests = args.get('special_requests')
    pickup_location = args.get('pickup_location')
    dropoff_location = args.get('dropoff_location')
    
    conn = get_db_connection(rls_context)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            
            # Check unicorn exists and get hourly rate (RLS filters by tenant)
            cur.execute("""
                SELECT unicorn_id, name, hourly_rate, is_available 
                FROM unicorns WHERE unicorn_id = %s
            """, [unicorn_id])
            unicorn = cur.fetchone()
            if not unicorn:
                return error_response(f'Unicorn {unicorn_id} not found')
            if not unicorn['is_available']:
                return error_response(f'Unicorn {unicorn["name"]} is not available')
            
            # Check customer exists (RLS filters by tenant)
            cur.execute("SELECT customer_id FROM customers WHERE customer_id = %s", 
                       [customer_id])
            if not cur.fetchone():
                return error_response(f'Customer {customer_id} not found')
            
            # Get a user_id from the account (RLS filters by tenant)
            cur.execute("SELECT user_id FROM users LIMIT 1")
            user = cur.fetchone()
            if not user:
                return error_response(f'No users found for this account')
            user_id = user['user_id']
            
            # Parse datetimes and calculate cost
            start_dt = datetime.fromisoformat(start_datetime.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_datetime.replace('Z', '+00:00'))
            duration_hours = (end_dt - start_dt).total_seconds() / 3600
            if duration_hours <= 0:
                return error_response('end_datetime must be after start_datetime')
            
            hourly_rate = float(unicorn['hourly_rate'])
            total_cost = round(hourly_rate * duration_hours, 2)
            
            # Generate booking reference
            booking_id = str(uuid.uuid4())
            booking_reference = f"BK-{datetime.now().strftime('%Y%m%d')}-{booking_id[:8].upper()}"
            
            # Get account_id from RLS context (JWT) for the INSERT
            rls_account_id = rls_context.get('account_id')
            if not rls_account_id:
                return error_response('No account context available — please log in')
            
            # Insert booking
            cur.execute("""
                INSERT INTO bookings (booking_id, customer_id, unicorn_id, user_id, account_id, 
                    booking_reference, start_datetime, end_datetime, base_hourly_rate, total_cost,
                    special_requests, pickup_location, dropoff_location)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING booking_id, booking_reference, total_cost
            """, [booking_id, customer_id, unicorn_id, user_id, rls_account_id, booking_reference,
                  start_dt, end_dt, hourly_rate, total_cost, special_requests, 
                  pickup_location, dropoff_location])
            
            result = cur.fetchone()
            conn.commit()
            
            return success_response({
                'success': True,
                'booking': {
                    'booking_id': result['booking_id'],
                    'booking_reference': result['booking_reference'],
                    'unicorn_name': unicorn['name'],
                    'start_datetime': start_datetime,
                    'end_datetime': end_datetime,
                    'duration_hours': round(duration_hours, 2),
                    'hourly_rate': hourly_rate,
                    'total_cost': float(result['total_cost'])
                }
            })
    except Exception as e:
        conn.rollback()
        return error_response(f'Failed to create booking: {str(e)}')
    finally:
        conn.close()
