"""
Semantic Layer Toolset Lambda — Cube Core integration.

Exposes two tools via AgentCore Gateway:
  - cube_meta_tool: GET /cubejs-api/v1/meta (discover cubes, dimensions, measures)
  - cube_query_tool: POST /cubejs-api/v1/load (execute Cube JSON queries)

Multi-tenancy: account_id is extracted from the user's JWT (propagated via
Gateway Interceptor) and injected as a filter into every /load query.
The same RLS extraction pattern as prebaked_sql_toolset_lambda.py is used.
"""

import json
import os
import time
import base64
import hmac
import hashlib
import traceback
import urllib.request
import urllib.error

CUBE_API_URL = os.environ.get('CUBE_API_URL', 'http://localhost:4000')
# Set by the stack from the Cube secret. No default: an unset secret must fail, not fall back
# to a value anyone can read in this repo.
CUBE_API_SECRET = os.environ.get('CUBE_API_SECRET', '')

# Global RLS context — set per invocation from JWT claims


# ---------------------------------------------------------------------------
# JWT / RLS helpers (same verification as prebaked_sql_toolset_lambda.py)
# ---------------------------------------------------------------------------

AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
COGNITO_USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
COGNITO_CLIENT_ID = os.environ.get('COGNITO_USER_LOGIN_CLIENT_ID', '')
_COGNITO_ISSUER = (f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
                   if COGNITO_USER_POOL_ID else '')
_jwks_client = None


def _verify_jwt(token):
    """Verify a Cognito access token (signature, issuer, token_use, client_id).

    Returns the claims, or None. Fails closed when PyJWT or the Cognito settings
    are missing, so an unverified token never yields a tenant.
    """
    global _jwks_client  # caches signing keys only, never request data
    if not _COGNITO_ISSUER or not COGNITO_CLIENT_ID:
        print("JWT verification not configured (COGNITO_USER_POOL_ID / COGNITO_USER_LOGIN_CLIENT_ID)")
        return None
    try:
        import jwt  # PyJWT, from the pyjwt Lambda layer
        if _jwks_client is None:
            _jwks_client = jwt.PyJWKClient(f"{_COGNITO_ISSUER}/.well-known/jwks.json")
        key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=_COGNITO_ISSUER,
                            options={"verify_aud": False, "require": ["exp", "iss"]})
        if claims.get("token_use") != "access" or claims.get("client_id") != COGNITO_CLIENT_ID:
            print("JWT rejected: wrong token_use or client_id")
            return None
        return claims
    except Exception as e:
        print(f"JWT verification failed: {type(e).__name__}")
        return None


def _extract_rls_context_from_jwt(context):
    """Tenant and role from the VERIFIED propagated token; {} when missing or invalid."""
    if context and hasattr(context, 'client_context') and context.client_context:
        custom = getattr(context.client_context, 'custom', {}) or {}
        auth_header = custom.get('bedrockAgentCorePropagatedHeaders', {}).get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            claims = _verify_jwt(auth_header.split(' ', 1)[1].strip())
            if claims:
                return {'account_id': claims.get('custom:account_id'), 'role': claims.get('custom:role')}
    return {}


# ---------------------------------------------------------------------------
# Cube API token helper
# ---------------------------------------------------------------------------

def _create_cube_api_token():
    """Create a minimal JWT for Cube API authentication.

    Cube validates the token signature against CUBEJS_API_SECRET using HS256.
    We only need iat/exp claims — tenant filtering is done at the query level,
    not via Cube's security context.
    """
    if not CUBE_API_SECRET:
        raise RuntimeError("CUBE_API_SECRET is not set")
    # Build JWT manually to avoid PyJWT dependency.
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b'=').decode()

    now = int(time.time())
    payload = base64.urlsafe_b64encode(
        json.dumps({"iat": now, "exp": now + 300}).encode()
    ).rstrip(b'=').decode()

    signature = base64.urlsafe_b64encode(
        hmac.new(
            CUBE_API_SECRET.encode(),
            f"{header}.{payload}".encode(),
            hashlib.sha256
        ).digest()
    ).rstrip(b'=').decode()

    return f"{header}.{payload}.{signature}"


# ---------------------------------------------------------------------------
# Cube API caller
# ---------------------------------------------------------------------------

def _call_cube_api(path, method='GET', body=None):
    """Call Cube Core REST API.

    GET  /cubejs-api/v1/meta  — no body
    POST /cubejs-api/v1/load  — body = {"query": <cube_query_object>}
    """
    token = _create_cube_api_token()
    url = f"{CUBE_API_URL}/cubejs-api{path}"
    headers = {
        'Authorization': token,
        'Content-Type': 'application/json',
    }

    if method == 'POST' and body is not None:
        data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    else:
        req = urllib.request.Request(url, headers=headers, method='GET')

    # Cube runs on a private EC2 host reachable over http (or https); reject any
    # other scheme so a malformed CUBE_API_URL can't read file:// or a custom
    # scheme (closes the B310 risk while still allowing the http Cube endpoint).
    if not url.lower().startswith(('http://', 'https://')):
        raise ValueError(f"unexpected Cube URL scheme: {url[:40]}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - scheme constrained to http/https above
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        print(f"Cube API error {e.code}: {error_body}")
        raise Exception(f"Cube API {e.code}: {error_body}")


# ---------------------------------------------------------------------------
# Response helpers (same format as other toolset Lambdas)
# ---------------------------------------------------------------------------

def _json_serializer(obj):
    """Fallback serializer for types json.dumps cannot handle."""
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)


def success_response(data):
    return {'statusCode': 200, 'body': json.dumps(data, default=_json_serializer)}


def error_response(message):
    return {'statusCode': 500, 'body': json.dumps({'error': message})}


# ---------------------------------------------------------------------------
# Tool: cube_meta_tool — GET /cubejs-api/v1/meta
# ---------------------------------------------------------------------------


def cube_meta(args):
    """Return metadata about available cubes, their dimensions, measures, and segments.

    The response is simplified for the agent:
      - The 'accounts' cube is excluded (internal tenant table).
      - Dimensions ending in '.account_id' are excluded so the agent
        doesn't try to filter by them manually (handled automatically).
      - Segments are included so the agent can prefer them over ad-hoc filters.
    """
    result = _call_cube_api('/v1/meta')

    cubes_summary = []
    for cube in result.get('cubes', []):
        if cube['name'] == 'accounts':
            continue

        measures = [
            {'name': m['name'], 'type': m.get('type', ''), 'title': m.get('title', '')}
            for m in cube.get('measures', [])
        ]
        dimensions = [
            {'name': d['name'], 'type': d.get('type', ''), 'title': d.get('title', '')}
            for d in cube.get('dimensions', [])
            if not d['name'].endswith('.account_id')
        ]
        segments = [
            {'name': s['name'], 'title': s.get('title', ''), 'description': s.get('description', '')}
            for s in cube.get('segments', [])
        ]

        cubes_summary.append({
            'name': cube['name'],
            'type': cube.get('type', 'cube'),
            'measures': measures,
            'dimensions': dimensions,
            'segments': segments,
        })

    return success_response({'cubes': cubes_summary})



# ---------------------------------------------------------------------------
# Tool: cube_query_tool — POST /cubejs-api/v1/load
# ---------------------------------------------------------------------------

def _inject_account_id_filter(query, account_id):
    """Inject account_id filter into the Cube query for multi-tenant isolation.

    Security approach:
      1. Remove any existing account_id filters the agent may have included
         (prevents prompt injection from overriding tenant isolation).
      2. Add an account_id filter for EVERY referenced cube (not just one),
         so cross-cube queries can't leak data from unfiltered cubes.
      3. Block queries that reference only the 'accounts' cube (internal).
    """
    if not account_id:
        # Fail closed: without a tenant from the caller's token, run nothing.
        raise PermissionError("No tenant in the caller's token; refusing to query")

    if 'filters' not in query:
        query['filters'] = []

    # Strip any existing account_id filters — the Lambda is the sole authority.
    query['filters'] = [
        f for f in query['filters']
        if not (isinstance(f, dict) and f.get('member', '').endswith('.account_id'))
    ]

    # Collect all member references to figure out which cubes are in play.
    member_names = list(query.get('measures') or [])
    member_names += list(query.get('dimensions') or [])
    for td in (query.get('timeDimensions') or []):
        dim = td.get('dimension')
        if dim:
            member_names.append(dim)

    cube_names = list(set(m.split('.')[0] for m in member_names if '.' in m))

    # Filter out the 'accounts' cube — it's internal.
    data_cubes = [c for c in cube_names if c != 'accounts']

    if not data_cubes:
        raise PermissionError("Query must reference at least one tenant data cube")

    # Add account_id filter for every referenced data cube.
    for cube_name in data_cubes:
        query['filters'].append({
            'member': f'{cube_name}.account_id',
            'operator': 'equals',
            'values': [account_id],
        })

    return query


def cube_query(args, rls_context):
    """Execute a Cube JSON query via POST /cubejs-api/v1/load.

    Expects ``args['query']`` to be a Cube query object (dict or JSON string)
    with at least a ``measures`` key.
    """
    query = args.get('query', {})
    if isinstance(query, str):
        query = json.loads(query)

    # Inject tenant filter
    account_id = rls_context.get('account_id')
    query = _inject_account_id_filter(query, account_id)

    # Cube /v1/load expects {"query": <query_object>} as the POST body.
    result = _call_cube_api('/v1/load', method='POST', body={'query': query})

    return success_response({
        'data': result.get('data', []),
        'annotation': result.get('annotation', {}),
        'query': result.get('query', {}),
    })


# ---------------------------------------------------------------------------
# Lambda entrypoint
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """Route to the appropriate tool based on the tool name.

    Tool name resolution follows the same pattern as the other toolset
    Lambdas: first check ``context.client_context`` (set by Gateway),
    then fall back to ``event['name']``.  The ``TargetName___`` prefix
    is stripped automatically.
    """
    # Request-scoped: never keep tenant identity in a module global (Lambda reuses containers).
    rls_context = _extract_rls_context_from_jwt(context)

    try:
        delimiter = "___"
        tool_name = ""

        # Prefer tool name from Gateway client context
        if context and hasattr(context, 'client_context') and context.client_context:
            custom = getattr(context.client_context, 'custom', None)
            if custom and 'bedrockAgentCoreToolName' in custom:
                original = custom['bedrockAgentCoreToolName']
                tool_name = original.split(delimiter)[-1] if delimiter in original else original

        # Fallback to event payload
        if not tool_name:
            tool_name = event.get('name', '')
            tool_name = tool_name.split(delimiter)[-1] if delimiter in tool_name else tool_name

        arguments = event.get('arguments', {}) if 'arguments' in event else event
        print(f"Tool: {tool_name}, arg keys: {sorted(arguments)}")

        if tool_name == 'cube_meta_tool':
            return cube_meta(arguments)
        if tool_name == 'cube_query_tool':
            return cube_query(arguments, rls_context)

        return error_response(f'Unknown tool: {tool_name}')

    except Exception as e:
        traceback.print_exc()
        return error_response(str(e))
