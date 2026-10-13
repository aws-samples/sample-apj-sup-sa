#!/usr/bin/env python3
"""
Deploy a parallel stack for the Semantic Layer Agent.

Creates a complete separate deployment (Gateway, Runtime, Amplify UI) so
participants can compare the semantic layer agent side-by-side with the
prebaked SQL agent in two browser tabs.

Shared infrastructure (Aurora PostgreSQL, Cognito User Pool) is reused.
The existing semantic-layer-toolset-lambda is reused — only a new Gateway
target is registered pointing to the same Lambda ARN.

Steps:
  1. Create a separate AgentCore Gateway with Cognito authorizer
  2. Register the SemanticLayer target on the new Gateway
  3. Deploy a separate AgentCore Runtime (unicorn_rental_semantic_agent.py)
  4. Deploy a separate Amplify UI pointing to the new Gateway/Runtime
  5. Save config to semantic_config.env
"""

import boto3
from botocore.exceptions import ClientError
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = ROOT_DIR.parent.parent
load_dotenv(ROOT_DIR / 'config.env')

REGION = os.getenv('AWS_REGION', 'us-east-1')
ENV_NAME = os.getenv('ENV_NAME', 'agentic-analytics')
# The base/top-up stack already deploys this interceptor Lambda; it forwards the
# caller's Authorization (JWT) header to Gateway targets so the semantic-layer
# Lambda can read custom:account_id and scope every Cube query to the tenant.
INTERCEPTOR_LAMBDA_NAME = f"{ENV_NAME}-gateway-interceptor"
GATEWAY_NAME = f"SemanticLayerGateway-{int(time.time())}"
LAMBDA_NAME = "semantic-layer-toolset-lambda"
SEMANTIC_CONFIG_FILE = ROOT_DIR / 'semantic_config.env'
AMPLIFY_APP_NAME = "agentic-analytics-semantic-ui"
RUNTIME_AGENT_NAME = "unicorn_rental_semantic_agent"
RUNTIME_ENTRYPOINT = "agent/unicorn_rental_semantic_agent.py"

try:
    from bedrock_agentcore_starter_toolkit.operations.gateway.client import (
        GatewayClient, create_gateway_execution_role
    )
except ImportError:
    print("Installing bedrock-agentcore-starter-toolkit...")
    # Use subprocess with an argument list (no shell) so there is no command
    # injection surface — the args are fixed literals, not an interpolated string.
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "bedrock-agentcore-starter-toolkit", "-q",
    ])
    from bedrock_agentcore_starter_toolkit.operations.gateway.client import (
        GatewayClient, create_gateway_execution_role
    )

# Add infrastructure/common to path for Amplify utilities
sys.path.insert(0, str(PROJECT_ROOT))
from common.amplify_utils import (
    create_amplify_app,
    create_branch,
    deploy_from_zip,
    wait_for_deployment,
)
from common.build_utils import build_react_app, create_deployment_zip


# ── Tool schemas (same as deploy_semantic_layer_toolset.py) ──────────────

TOOL_SCHEMA = [
    {
        "name": "cube_meta_tool",
        "description": (
            "Get metadata about available semantic layer cubes, including their "
            "dimensions (attributes you can group by or filter on), measures "
            "(aggregations you can calculate), and segments (pre-defined named filters "
            "that encode business logic like 'completed bookings' or 'late returns'). "
            "MUST be called before cube_query_tool to discover available dimensions, "
            "measures, and segments for constructing queries. "
            "ALWAYS check segments first — prefer segments over ad-hoc filters when "
            "a segment matches the filtering intent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "cube_query_tool",
        "description": (
            "Execute a query against the semantic layer using a Cube JSON query object. "
            "You MUST call cube_meta_tool first to discover available dimensions, measures, and segments. "
            "ALWAYS prefer segments over filters when a matching segment exists (e.g., use "
            "\"segments\": [\"bookings.completed_bookings\"] instead of filtering on is_completed). "
            "The query object supports: measures (required), dimensions, segments, "
            "filters (for dynamic user-provided values only), "
            "order (object mapping member names to \"asc\" or \"desc\"), "
            "limit (integer), and timeDimensions (array for time-based grouping). "
            "Example: {\"measures\": [\"bookings.total_revenue\"], \"dimensions\": [\"unicorns.breed\"], "
            "\"segments\": [\"bookings.completed_bookings\"], "
            "\"order\": {\"bookings.total_revenue\": \"desc\"}, \"limit\": 5}. "
            "Tenant data isolation is handled automatically — do NOT add account_id filters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "object",
                    "description": "Cube JSON query object with measures, dimensions, filters, order, limit, and/or timeDimensions",
                    "properties": {
                        "measures": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of measure names (e.g., [\"bookings.total_revenue\", \"bookings.count\"])"
                        },
                        "dimensions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of dimension names to group by (e.g., [\"unicorns.breed\", \"bookings.status\"])"
                        },
                        "segments": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of pre-defined segment names to apply (e.g., [\"bookings.completed_bookings\", \"bookings.late_returns\"]). Prefer segments over filters when a segment matches the intent."
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "member": {"type": "string"},
                                    "operator": {
                                        "type": "string",
                                        "description": "Filter operator. Valid values: equals, notEquals, contains, notContains, gt, gte, lt, lte, set, notSet, inDateRange, notInDateRange, beforeDate, afterDate"
                                    },
                                    "values": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    }
                                }
                            },
                            "description": "Array of filter objects"
                        },
                        "order": {
                            "type": "object",
                            "description": "Object mapping member names to sort direction (\"asc\" or \"desc\")"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of rows to return"
                        },
                        "timeDimensions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "dimension": {"type": "string"},
                                    "granularity": {
                                        "type": "string",
                                        "description": "Time granularity. Valid values: day, week, month, quarter, year"
                                    },
                                    "dateRange": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    }
                                }
                            },
                            "description": "Array of time dimension objects for time-based grouping"
                        }
                    },
                    "required": ["measures"]
                }
            },
            "required": ["query"]
        }
    }
]


# ── Helper: save to semantic_config.env ──────────────────────────────────

def save_semantic_config(**kwargs):
    """Append or update key=value pairs in semantic_config.env."""
    existing = {}
    if SEMANTIC_CONFIG_FILE.exists():
        for line in SEMANTIC_CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                existing[k.strip()] = v.strip()

    existing.update({k: str(v).strip() for k, v in kwargs.items()})
    lines = [f"{k}={v}" for k, v in existing.items()]
    SEMANTIC_CONFIG_FILE.write_text("\n".join(lines) + "\n")


# ── Step 1: Create a separate Gateway ────────────────────────────────────

def create_gateway():
    """Create a new AgentCore Gateway with the same Cognito authorizer config."""
    print("\n" + "=" * 60)
    print("Step 1: Create Semantic Layer Gateway")
    print("=" * 60)

    pool_id = os.getenv('COGNITO_USER_POOL_ID')
    user_login_client_id = os.getenv('COGNITO_USER_LOGIN_CLIENT_ID')

    if not pool_id or not user_login_client_id:
        print("Error: COGNITO_USER_POOL_ID and COGNITO_USER_LOGIN_CLIENT_ID required in config.env")
        sys.exit(1)

    # Same authorizer config as deploy_gateway.py
    discovery_url = (
        f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}"
        f"/.well-known/openid-configuration"
    )
    authorizer_config = {
        'customJWTAuthorizer': {
            'discoveryUrl': discovery_url,
            'allowedClients': [user_login_client_id],
        }
    }
    print(f"[OK] Authorizer: Cognito pool {pool_id}, client {user_login_client_id}")

    # Create execution role
    role_arn = create_gateway_execution_role(
        boto3.Session(region_name=REGION), logging.getLogger()
    )
    print(f"[OK] Execution role: {role_arn}")

    # Resolve the gateway interceptor Lambda (deployed by the base/top-up stack).
    # Without an interceptor the Gateway does NOT forward the Authorization header
    # to the Lambda target, so the semantic Lambda can't read custom:account_id and
    # every tenant would see global data. Multi-tenancy on the Cube path is enforced
    # by the Lambda injecting an account_id filter into each Cube query (Cube itself
    # connects to Aurora as the table owner and bypasses Postgres RLS — see the
    # semantic-layer toolset Lambda), so propagating the JWT here is what makes
    # tenant isolation work. Mirrors the main analytics Gateway's interceptor.
    interceptor_configs = []
    try:
        lam = boto3.client('lambda', region_name=REGION)
        interceptor_arn = lam.get_function(
            FunctionName=INTERCEPTOR_LAMBDA_NAME
        )['Configuration']['FunctionArn']
        interceptor_configs = [{
            'interceptor': {'lambda': {'arn': interceptor_arn}},
            'interceptionPoints': ['REQUEST'],
            'inputConfiguration': {'passRequestHeaders': True},
        }]
        print(f"[OK] Interceptor: {interceptor_arn} (propagates Authorization → target)")
    except Exception as e:
        print(f"⚠️  Could not resolve interceptor Lambda '{INTERCEPTOR_LAMBDA_NAME}': {e}")
        print("    The semantic agent would NOT enforce tenant isolation without it.")

    # Create gateway
    print("Creating Gateway...")
    agentcore = boto3.client('bedrock-agentcore-control', region_name=REGION)

    create_kwargs = dict(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType='MCP',
        authorizerType='CUSTOM_JWT',
        authorizerConfiguration=authorizer_config,
        description='AgentCore Gateway for Semantic Layer Agent (parallel stack)',
    )
    if interceptor_configs:
        create_kwargs['interceptorConfigurations'] = interceptor_configs
    gateway = agentcore.create_gateway(**create_kwargs)
    gateway_id = gateway['gatewayId']
    gateway_url = gateway['gatewayUrl']
    print(f"[OK] Created Gateway: {gateway_id}")

    # Wait for READY
    for _ in range(60):
        status = agentcore.get_gateway(gatewayIdentifier=gateway_id).get('status')
        if status == 'READY':
            break
        time.sleep(5)
    print(f"Gateway status: {status}")

    if status != 'READY':
        print("❌ Gateway did not reach READY status")
        sys.exit(1)

    save_semantic_config(
        SEMANTIC_GATEWAY_URL=gateway_url,
        SEMANTIC_GATEWAY_ID=gateway_id,
    )
    print(f"[OK] Gateway URL: {gateway_url}")
    print(f"[OK] Gateway ID: {gateway_id}")

    return gateway_id, gateway_url


# ── Step 2: Register SemanticLayer target on the new Gateway ─────────────

def register_target(gateway_id):
    """Register the SemanticLayer target reusing the existing Lambda ARN."""
    print("\n" + "=" * 60)
    print("Step 2: Register SemanticLayer Target")
    print("=" * 60)

    # Resolve existing Lambda ARN
    lambda_client = boto3.client('lambda', region_name=REGION)
    try:
        resp = lambda_client.get_function(FunctionName=LAMBDA_NAME)
        lambda_arn = resp['Configuration']['FunctionArn']
        print(f"[OK] Reusing Lambda: {lambda_arn}")
    except lambda_client.exceptions.ResourceNotFoundException:
        print(f"❌ Lambda '{LAMBDA_NAME}' not found. Run deploy_semantic_layer_toolset.py first.")
        sys.exit(1)

    agentcore = boto3.client('bedrock-agentcore-control', region_name=REGION)

    # Delete existing target if present (idempotent)
    try:
        targets = agentcore.list_gateway_targets(gatewayIdentifier=gateway_id).get('items', [])
        for t in targets:
            if t.get('name') == 'SemanticLayer':
                target_id_to_delete = t['targetId']
                agentcore.delete_gateway_target(
                    gatewayIdentifier=gateway_id, targetId=target_id_to_delete
                )
                print(f"[OK] Deleted old target: {target_id_to_delete}")
                # Wait for deletion to propagate before creating new target
                for i in range(15):
                    time.sleep(2)
                    remaining = agentcore.list_gateway_targets(gatewayIdentifier=gateway_id).get('items', [])
                    if not any(rt.get('name') == 'SemanticLayer' for rt in remaining):
                        print(f"[OK] Deletion confirmed")
                        break
                    print(f"  Waiting for deletion to propagate... ({i+1}/15)")
                else:
                    print("  Warning: target may still be deleting, proceeding anyway")
    except Exception as e:
        print(f"Note: {e}")

    # The Gateway execution role was just created; its trust policy can take a few
    # seconds to propagate. Until it does, CreateGatewayTarget fails with a
    # ValidationException ("Gateway service is not authorized to perform AssumeRole
    # on Gateway role"). Retry with backoff instead of making the participant re-run.
    create_target_kwargs = dict(
        gatewayIdentifier=gateway_id,
        name="SemanticLayer",
        targetConfiguration={
            'mcp': {
                'lambda': {
                    'lambdaArn': lambda_arn,
                    'toolSchema': {'inlinePayload': TOOL_SCHEMA}
                }
            }
        },
        credentialProviderConfigurations=[
            {'credentialProviderType': 'GATEWAY_IAM_ROLE'}
        ]
    )
    response = None
    for attempt in range(1, 13):  # ~12 * 5s = up to 60s for IAM to propagate
        try:
            response = agentcore.create_gateway_target(**create_target_kwargs)
            break
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', '')
            msg = e.response.get('Error', {}).get('Message', str(e))
            if code == 'ValidationException' and 'AssumeRole' in msg and attempt < 12:
                print(f"  Gateway role trust not propagated yet — retrying ({attempt}/12)...")
                time.sleep(5)
                continue
            raise
    if response is None:
        print("❌ Target registration failed after retries (Gateway role AssumeRole trust).")
        sys.exit(1)

    target_id = response['targetId']
    print(f"[OK] Created SemanticLayer target: {target_id}")
    print(f"   Tools: cube_meta_tool, cube_query_tool")
    return target_id


# ── Step 3: Deploy a separate AgentCore Runtime ─────────────────────────

def deploy_runtime(gateway_url):
    """Deploy a separate AgentCore Runtime using the semantic agent entrypoint.

    Uses the agentcore CLI (configure + deploy) with GATEWAY_URL pointing
    to the new semantic layer Gateway.
    """
    print("\n" + "=" * 60)
    print("Step 3: Deploy Semantic Layer Runtime")
    print("=" * 60)

    # Write GATEWAY_URL into config.env so the agent picks it up at runtime.
    # The semantic agent reads GATEWAY_URL from config.env (bundled in the
    # deployment package). We write to a temporary semantic config.env that
    # the agent entrypoint will load.
    #
    # Strategy: the agent code loads config.env from its project dir.
    # We temporarily set GATEWAY_URL in config.env, deploy, then restore.
    # A cleaner approach: set it as an env override via agentcore CLI.

    # The runtime build uses `uv` (the agentcore CLI resolves deps + builds the
    # image context with it). If it's missing, `agentcore configure` silently
    # produces an incomplete build config and the failure only surfaces minutes
    # later as a cryptic CodeBuild "no Dockerfile" error. Fail fast with the fix.
    import shutil as _shutil
    if _shutil.which("uv") is None:
        print("❌ `uv` is not installed, but the runtime build needs it.")
        print("   Install it, then re-run this script:")
        print("     curl -LsSf https://astral.sh/uv/install.sh | sh")
        print('     export PATH="$HOME/.local/bin:$PATH"')
        sys.exit(1)

    # Clean up any previous agentcore config (may have stale container settings).
    # Two places hold state: the per-agent dir AND the CLI's global
    # .bedrock_agentcore.yaml. A failed/partial earlier run can leave the agent
    # registered there as deployment_type=container, which blocks a retry with
    # "Cannot change deployment type from 'container' to 'direct_code_deploy'".
    # Remove BOTH so a re-run is always clean.
    import shutil
    agentcore_config_dir = ROOT_DIR / '.bedrock_agentcore' / RUNTIME_AGENT_NAME
    if agentcore_config_dir.exists():
        shutil.rmtree(agentcore_config_dir)
        print(f"[OK] Cleaned up old config: {agentcore_config_dir}")

    agentcore_yaml = ROOT_DIR / '.bedrock_agentcore.yaml'
    if agentcore_yaml.exists():
        try:
            import yaml as _yaml
            state = _yaml.safe_load(agentcore_yaml.read_text()) or {}
            agents = state.get('agents', {})
            if RUNTIME_AGENT_NAME in agents:
                agents.pop(RUNTIME_AGENT_NAME, None)
                if state.get('default_agent') == RUNTIME_AGENT_NAME:
                    state['default_agent'] = next(iter(agents), None)
                if agents:
                    agentcore_yaml.write_text(_yaml.safe_dump(state, sort_keys=False))
                else:
                    agentcore_yaml.unlink()
                print(f"[OK] Removed stale '{RUNTIME_AGENT_NAME}' entry from .bedrock_agentcore.yaml")
        except Exception as e:
            # If we can't parse it, removing the file is safe — configure regenerates it.
            print(f"Note: resetting .bedrock_agentcore.yaml ({type(e).__name__}: {e})")
            agentcore_yaml.unlink()

    # Configure the runtime
    print("Configuring AgentCore Runtime...")
    configure_cmd = [
        "agentcore", "configure",
        "--entrypoint", RUNTIME_ENTRYPOINT,
        "--name", RUNTIME_AGENT_NAME,
        "--disable-memory",
        "--non-interactive",
    ]
    result = subprocess.run(
        configure_cmd, cwd=str(ROOT_DIR),
        capture_output=True, text=True,
        timeout=60
    )
    if result.returncode != 0:
        print(f"❌ agentcore configure failed:\n{result.stderr}\n{result.stdout}")
        sys.exit(1)
    print(f"[OK] Configured runtime: {RUNTIME_AGENT_NAME}")
    if result.stdout:
        print(f"   configure output: {result.stdout.strip()[:300]}")

    # Force direct_code_deploy: remove any Dockerfile that configure may have created
    dockerfile_path = ROOT_DIR / '.bedrock_agentcore' / RUNTIME_AGENT_NAME / 'Dockerfile'
    if dockerfile_path.exists():
        dockerfile_path.unlink()
        print(f"[OK] Removed Dockerfile to force direct_code_deploy")

    # Inject GATEWAY_URL into config.env for the deployment package.
    # We write to BOTH the project root config.env AND a copy inside agent/
    # so the agent finds it regardless of how __file__ resolves at runtime.
    config_env_path = ROOT_DIR / 'config.env'
    agent_config_env_path = ROOT_DIR / 'agent' / 'config.env'
    original_config = config_env_path.read_text()
    agent_config_existed = agent_config_env_path.exists()
    agent_config_original = agent_config_env_path.read_text() if agent_config_existed else None

    # Build config with GATEWAY_URL pointing to the new semantic layer Gateway
    config_lines = original_config.splitlines()
    new_lines = [l for l in config_lines if not l.startswith('GATEWAY_URL=')]
    new_lines.append(f"GATEWAY_URL={gateway_url}")
    config_with_gw = "\n".join(new_lines) + "\n"

    # Write to both locations
    config_env_path.write_text(config_with_gw)
    agent_config_env_path.write_text(config_with_gw)
    print(f"[OK] Injected GATEWAY_URL into config.env and agent/config.env")
    print(f"   GATEWAY_URL={gateway_url}")

    # Deploy
    print("Deploying to AgentCore Runtime (this takes ~2-3 minutes)...")
    deploy_cmd = ["agentcore", "deploy"]
    result = subprocess.run(
        deploy_cmd, cwd=str(ROOT_DIR),
        capture_output=True, text=True, timeout=600
    )

    # Restore original config.env and clean up agent/config.env
    config_env_path.write_text(original_config)
    if agent_config_existed:
        agent_config_env_path.write_text(agent_config_original)
    else:
        agent_config_env_path.unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"❌ agentcore deploy failed:\n{result.stderr}")
        print("Note: Gateway was created successfully (manual cleanup may be needed)")
        sys.exit(1)

    print(f"[OK] Runtime deployed: {RUNTIME_AGENT_NAME}")

    # Try to extract runtime ARN from deploy stdout first.
    # The agentcore CLI output contains box-drawing characters (│, ╭, ╰, etc.)
    # and wraps long lines INSIDE the box border — so an ARN printed in a box can
    # be split across visual lines, leaving the regex with only the pre-wrap
    # fragment (e.g. ".../runtime/unicorn_rental_sema"). Accept the stdout match
    # only if it ends with the full "<RUNTIME_AGENT_NAME>-<suffix>" id; otherwise
    # fall through to the authoritative API lookup in _get_runtime_arn().
    import re
    runtime_arn = None
    if result.stdout:
        for line in result.stdout.splitlines():
            print(f"   {line}")
            match = re.search(r'(arn:aws:bedrock-agentcore:[a-zA-Z0-9\-]+:\d+:runtime/[a-zA-Z0-9_\-]+)', line)
            if match:
                candidate = match.group(1)
                # Guard against box-wrapped truncation: the runtime id must be
                # the agent name PLUS the AgentCore-assigned "-XXXXXXXXXX" suffix.
                rid = candidate.rsplit('/', 1)[-1]
                if re.match(rf'^{re.escape(RUNTIME_AGENT_NAME)}-[A-Za-z0-9]+$', rid):
                    runtime_arn = candidate

    # Fallback (also used when stdout only had a truncated/wrapped ARN): the
    # authoritative source is the control-plane list, never the box-drawn stdout.
    if not runtime_arn:
        runtime_arn = _get_runtime_arn()

    if runtime_arn:
        save_semantic_config(SEMANTIC_RUNTIME_ARN=runtime_arn)
        print(f"[OK] Runtime ARN: {runtime_arn}")
        # `agentcore configure/deploy` creates the runtime with the DEFAULT
        # (IAM/SigV4) inbound auth — it has no flag for a JWT authorizer. The UI
        # calls the runtime with a Cognito Bearer token, so without this the
        # invoke fails 403 "Authorization method mismatch". Attach the same
        # CustomJWTAuthorizer the analytics runtime uses, and allowlist the
        # Authorization header so the agent receives the JWT for RBAC/RLS.
        _attach_jwt_authorizer(runtime_arn.rsplit('/', 1)[-1])
    else:
        print("⚠️  Could not determine runtime ARN — UI may not connect to agent")

    return runtime_arn


def _attach_jwt_authorizer(runtime_id):
    """Switch the voice/semantic runtime from default IAM auth to CustomJWT.

    update-agent-runtime REPLACES the whole config, so we re-send the runtime's
    existing artifact/network/protocol/role unchanged and add the authorizer +
    request-header allowlist (the same replace-trap the analytics `make build`
    handles). Idempotent and safe to re-run.
    """
    pool_id = os.getenv('COGNITO_USER_POOL_ID')
    client_id = os.getenv('COGNITO_USER_LOGIN_CLIENT_ID')
    if not pool_id or not client_id:
        print("⚠️  COGNITO_USER_POOL_ID / COGNITO_USER_LOGIN_CLIENT_ID missing — "
              "skipping JWT authorizer attach (UI invoke would 403)")
        return
    discovery_url = (
        f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}"
        f"/.well-known/openid-configuration"
    )
    try:
        agentcore = boto3.client('bedrock-agentcore-control', region_name=REGION)
        rt = agentcore.get_agent_runtime(agentRuntimeId=runtime_id)
        if rt.get('authorizerConfiguration', {}).get('customJWTAuthorizer'):
            print("[OK] JWT authorizer already attached to runtime")
            return
        print(f"[voice/semantic] attaching CustomJWTAuthorizer to runtime {runtime_id}")
        agentcore.update_agent_runtime(
            agentRuntimeId=runtime_id,
            roleArn=rt['roleArn'],
            networkConfiguration=rt['networkConfiguration'],
            protocolConfiguration=rt.get('protocolConfiguration', {'serverProtocol': 'HTTP'}),
            agentRuntimeArtifact=rt['agentRuntimeArtifact'],
            authorizerConfiguration={
                'customJWTAuthorizer': {
                    'discoveryUrl': discovery_url,
                    'allowedClients': [client_id],
                }
            },
            requestHeaderConfiguration={'requestHeaderAllowlist': ['Authorization']},
        )
        print("[OK] JWT authorizer + Authorization header allowlist attached")
    except Exception as e:
        print(f"⚠️  Could not attach JWT authorizer ({e}) — the UI invoke may 403; "
              f"re-run this step or attach via update-agent-runtime")


def _get_runtime_arn():
    """Get the runtime ARN for the semantic agent from the AgentCore control plane.

    Uses the boto3 control-plane API (list_agent_runtimes) rather than the
    `agentcore` CLI: the CLI has no stable `list` subcommand across toolkit
    versions (newer builds raise "No such command 'list'"), which silently
    returned None and left the UI with an empty REACT_APP_AGENT_RUNTIME_ARN.
    The control-plane list is authoritative and version-independent.
    """
    try:
        agentcore = boto3.client('bedrock-agentcore-control', region_name=REGION)
        runtimes = []
        paginator = None
        try:
            paginator = agentcore.get_paginator('list_agent_runtimes')
        except Exception:
            paginator = None
        if paginator is not None:
            for page in paginator.paginate():
                runtimes.extend(page.get('agentRuntimes', []))
        else:
            resp = agentcore.list_agent_runtimes()
            runtimes.extend(resp.get('agentRuntimes', []))
            token = resp.get('nextToken')
            while token:
                resp = agentcore.list_agent_runtimes(nextToken=token)
                runtimes.extend(resp.get('agentRuntimes', []))
                token = resp.get('nextToken')
        # Prefer the exact-name match; fall back to any runtime whose name
        # starts with the agent name (it carries the AgentCore "-XXXX" suffix).
        for rt in runtimes:
            if rt.get('agentRuntimeName') == RUNTIME_AGENT_NAME:
                return rt.get('agentRuntimeArn', '') or None
        for rt in runtimes:
            if str(rt.get('agentRuntimeName', '')).startswith(RUNTIME_AGENT_NAME):
                return rt.get('agentRuntimeArn', '') or None
    except Exception as e:
        print(f"Note: Could not retrieve runtime ARN: {e}")
    return None


# ── Step 4: Deploy a separate Amplify UI ─────────────────────────────────

def deploy_ui(gateway_url, runtime_arn):
    """Deploy a separate Amplify-hosted UI pointing to the new Gateway/Runtime."""
    print("\n" + "=" * 60)
    print("Step 4: Deploy Semantic Layer UI")
    print("=" * 60)

    # Resolve Cognito config from config.env / CloudFormation
    pool_id = os.getenv('COGNITO_USER_POOL_ID', '')
    user_login_client_id = os.getenv('COGNITO_USER_LOGIN_CLIENT_ID', '')
    cognito_domain = os.getenv('COGNITO_DOMAIN', '')

    # Resolve Identity Pool ID from CloudFormation
    identity_pool_id = ''
    try:
        cfn = boto3.client('cloudformation', region_name=REGION)
        outputs = cfn.describe_stacks(StackName='main-stack')['Stacks'][0].get('Outputs', [])
        for o in outputs:
            if o['OutputKey'] == 'IdentityPoolId':
                identity_pool_id = o['OutputValue']
                break
    except Exception as e:
        print(f"Note: Could not resolve IdentityPoolId: {e}")

    # Build env vars for React app
    env_vars = {
        'REACT_APP_AWS_REGION': REGION,
        'REACT_APP_AGENT_RUNTIME_ARN': runtime_arn or '',
        'REACT_APP_COGNITO_USER_POOL_ID': pool_id,
        'REACT_APP_COGNITO_IDENTITY_POOL_ID': identity_pool_id,
        'REACT_APP_COGNITO_USER_CLIENT_ID': user_login_client_id,
        'REACT_APP_COGNITO_DOMAIN': cognito_domain,
    }

    print(f"[OK] React env vars:")
    for k, v in env_vars.items():
        display = v[:40] + '...' if len(v) > 40 else v
        print(f"   {k}={display}")

    # Build and deploy using shared utilities
    ui_dir = PROJECT_ROOT / 'app' / 'ui'
    print(f"\nBuilding React app from {ui_dir}...")
    build_dir = build_react_app(ui_dir, env_vars)
    zip_path = create_deployment_zip(build_dir)

    try:
        app_id, default_domain = create_amplify_app(AMPLIFY_APP_NAME, REGION)
        branch_name = "main"
        create_branch(app_id, branch_name, REGION)

        job_id = deploy_from_zip(app_id, branch_name, zip_path, REGION)
        wait_for_deployment(app_id, branch_name, job_id, region=REGION)

        app_url = f"https://{branch_name}.{default_domain}"

        # Update Cognito callback URLs to include the new Amplify URL
        _update_cognito_callbacks(app_url)

        save_semantic_config(
            SEMANTIC_AMPLIFY_APP_ID=app_id,
            SEMANTIC_AMPLIFY_URL=app_url,
        )

        print(f"\n[OK] Amplify app deployed:")
        print(f"   App ID:  {app_id}")
        print(f"   URL:     {app_url}")

        return app_id, app_url

    finally:
        os.unlink(zip_path)


def _update_cognito_callbacks(amplify_url):
    """Add the new Amplify URL to Cognito user login client callback/logout URLs."""
    # Try gateway_config.json first (same pattern as deploy_amplify_hosting.py)
    gw_config_path = ROOT_DIR / 'gateway_config.json'
    if gw_config_path.exists():
        with open(gw_config_path) as f:
            config = json.load(f)
        client_id = config.get('user_login_client_id') or config.get('cognito_user_login_client_id')
        pool_id = config.get('cognito_user_pool_id')
    else:
        client_id = os.getenv('COGNITO_USER_LOGIN_CLIENT_ID')
        pool_id = os.getenv('COGNITO_USER_POOL_ID')

    if not client_id or not pool_id:
        print("  No Cognito client config — skipping callback update")
        return

    try:
        cognito = boto3.client('cognito-idp', region_name=REGION)
        desc = cognito.describe_user_pool_client(
            UserPoolId=pool_id, ClientId=client_id
        )['UserPoolClient']

        callbacks = desc.get('CallbackURLs', [])
        logouts = desc.get('LogoutURLs', [])

        # Add both with and without trailing slash (Cognito requires exact match)
        url_no_slash = amplify_url.rstrip('/')
        url_with_slash = url_no_slash + '/'
        changed = False
        for url in [url_no_slash, url_with_slash]:
            if url not in callbacks:
                callbacks.append(url)
                changed = True
            if url not in logouts:
                logouts.append(url)
                changed = True

        if changed:
            cognito.update_user_pool_client(
                UserPoolId=pool_id, ClientId=client_id,
                ClientName=desc.get('ClientName', 'user-login'),
                CallbackURLs=callbacks, LogoutURLs=logouts,
                AllowedOAuthFlows=desc.get('AllowedOAuthFlows', ['code']),
                AllowedOAuthScopes=desc.get('AllowedOAuthScopes', ['openid', 'profile', 'email']),
                AllowedOAuthFlowsUserPoolClient=True,
                SupportedIdentityProviders=desc.get('SupportedIdentityProviders', ['COGNITO']),
                ExplicitAuthFlows=desc.get('ExplicitAuthFlows', []),
                ReadAttributes=desc.get('ReadAttributes', []),
                WriteAttributes=desc.get('WriteAttributes', []),
            )
            print(f"  [OK] Added {url_no_slash} (with and without trailing slash) to Cognito callbacks")
        else:
            print(f"  [OK] Amplify URL already in Cognito callbacks")
    except Exception as e:
        print(f"  ⚠️  Failed to update Cognito callbacks: {e}")


# ── Main ─────────────────────────────────────────────────────────────────

def _load_semantic_config():
    """Load saved values from semantic_config.env."""
    saved = {}
    if SEMANTIC_CONFIG_FILE.exists():
        for line in SEMANTIC_CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                saved[k.strip()] = v.strip()
    return saved


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Deploy Semantic Layer Parallel Stack')
    parser.add_argument('--step', type=int, choices=[1, 2, 3, 4],
                        help='Run only this step (requires semantic_config.env for steps 2-4)')
    args = parser.parse_args()

    step = args.step  # None means run all

    # Load saved config for partial runs
    saved = _load_semantic_config()

    print("Deploying Semantic Layer Parallel Stack")
    print("=" * 60)
    print(f"Region:       {REGION}")
    print(f"Gateway name: {GATEWAY_NAME}")
    print(f"Amplify app:  {AMPLIFY_APP_NAME}")
    print(f"Runtime:      {RUNTIME_AGENT_NAME}")
    print(f"Config file:  {SEMANTIC_CONFIG_FILE}")
    if step:
        print(f"Running:      Step {step} only")
    print()

    gateway_id = saved.get('SEMANTIC_GATEWAY_ID')
    gateway_url = saved.get('SEMANTIC_GATEWAY_URL')
    runtime_arn = saved.get('SEMANTIC_RUNTIME_ARN')
    target_id = None
    app_id = None
    app_url = saved.get('SEMANTIC_AMPLIFY_URL')

    # Step 1: Create Gateway
    if step is None or step == 1:
        try:
            gateway_id, gateway_url = create_gateway()
        except Exception as e:
            print(f"\n❌ Gateway creation failed: {e}")
            sys.exit(1)

    # Step 2: Register target
    if step is None or step == 2:
        if not gateway_id:
            print("❌ No gateway_id — run step 1 first or check semantic_config.env")
            sys.exit(1)
        try:
            target_id = register_target(gateway_id)
        except Exception as e:
            print(f"\n❌ Target registration failed: {e}")
            sys.exit(1)

    # Step 3: Deploy Runtime
    if step is None or step == 3:
        if not gateway_url:
            print("❌ No gateway_url — run step 1 first or check semantic_config.env")
            sys.exit(1)
        try:
            runtime_arn = deploy_runtime(gateway_url)
        except Exception as e:
            print(f"\n❌ Runtime deployment failed: {e}")
            sys.exit(1)

    # Step 4: Deploy UI
    if step is None or step == 4:
        if not gateway_url:
            print("❌ No gateway_url — run step 1 first or check semantic_config.env")
            sys.exit(1)
        try:
            app_id, app_url = deploy_ui(gateway_url, runtime_arn)
        except Exception as e:
            print(f"\n❌ Amplify deployment failed: {e}")
            sys.exit(1)

    # Summary
    print("\n" + "=" * 60)
    if step:
        print(f"[OK] Step {step} complete.")
    else:
        print("[OK] Parallel Stack Deployment Complete!")
    print("=" * 60)
    print(f"   Gateway ID:   {gateway_id}")
    print(f"   Gateway URL:  {gateway_url}")
    print(f"   Runtime:      {runtime_arn or RUNTIME_AGENT_NAME}")
    if app_url:
        print(f"   Amplify URL:  {app_url}")
    print(f"   Config saved: {SEMANTIC_CONFIG_FILE}")


if __name__ == '__main__':
    main()
