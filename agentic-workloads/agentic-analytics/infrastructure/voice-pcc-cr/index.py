"""CloudFormation custom resource that manages a Pipecat Cloud agent.

Pipecat Cloud is Daily's SaaS (not an AWS service), so there's no native CFN
resource. This Lambda drives PCC's OFFICIAL, DOCUMENTED REST API
(https://docs.pipecat.ai/api-reference/pipecat-cloud/rest-reference) so the
agent's lifecycle is bound to a CFN stack:

  Create -> cloud-build the bot image from the bundled context, then
            POST /v1/agents with minAgents/maxAgents from stack params.
  Update -> rebuild, then POST /v1/agents/{name}.
  Delete -> DELETE /v1/agents/{name} (clean teardown, no orphaned billing).

  CFN OWNS the agent: an intentional `delete-stack` deletes the agent for real.
  The only Delete that does NOT delete is the rollback of a FAILED create — that
  rollback carries the SENTINEL_PID, which the Delete handler no-ops on. (A
  failed create and an intentional delete are otherwise identical Delete events;
  the physical-id is the only signal that distinguishes them.) Because CFN
  creates and owns the agent here, point the stack at a CFN-OWNED agent name —
  do NOT aim it at a pre-existing agent you want to keep, since delete-stack
  will remove it.

Endpoints (base https://api.pipecat.daily.co/v1, Bearer = PCC PRIVATE API KEY):
  POST /builds/upload-url {region}     -> {uploadId, uploadUrl, uploadFields}
  (S3 presigned POST of context.tar.gz)
  POST /builds {uploadId, region, dockerfilePath} -> {build:{id}, cached}
  GET  /builds/{id}                    -> {build:{status}}  (success|failed|timeout|building|pending)
  POST /agents {serviceName, buildId, secretSet, autoScaling, nodeType:"arm", ...}
  POST /agents/{name} {buildId, autoScaling, ...}   (UPDATE — POST, not PUT)
  DELETE /agents/{name}

Pure stdlib (urllib + tarfile) — no pip deps, nothing to bundle. The bot build
context (bot.py, analytics_processor.py, auth.py, Dockerfile, pyproject.toml,
uv.lock) is packaged INTO this Lambda's zip under ./bot_context/.

The PCC PRIVATE API KEY comes from Secrets Manager (never in the template).
Scaling/secretSet/etc. come from the resource Properties. Region defaults to
us-west (PCC's region label, not an AWS region).
"""
import gzip
import io
import json
import os
import tarfile
import time
import urllib.request
import urllib.error
import uuid

import boto3

API = "https://api.pipecat.daily.co/v1"
CONTEXT_DIR = os.path.join(os.path.dirname(__file__), "bot_context")
DEFAULT_REGION = os.environ.get("PCC_REGION", "us-west")  # PCC region label, not AWS


def _https_urlopen(req_or_url, timeout):
    """urlopen that REFUSES any scheme other than https.

    Every URL here is an https Pipecat Cloud API endpoint, a CFN-provided https
    ResponseURL, or an https presigned-S3 upload URL — pin the scheme so a
    malformed value can't read file:// or a custom scheme (closes B310).
    """
    full_url = req_or_url.full_url if isinstance(req_or_url, urllib.request.Request) else req_or_url
    if not full_url.lower().startswith("https://"):
        raise ValueError(f"refusing non-https URL: {full_url[:60]}")
    return urllib.request.urlopen(req_or_url, timeout=timeout)  # nosec B310 - scheme pinned to https above


# ── tiny REST helper ─────────────────────────────────────────────────────────
def _req(method, url, token, body=None, raw=None, headers=None):
    h = {"Authorization": f"Bearer {token}"}
    data = None
    if raw is not None:
        data = raw
        h.update(headers or {})
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with _https_urlopen(req, timeout=60) as r:
            txt = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


# ── build context tarball (deterministic gzip tar, like the CLI) ─────────────
def _make_tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for root, _dirs, files in os.walk(CONTEXT_DIR):
            for fn in sorted(files):
                fp = os.path.join(root, fn)
                arc = os.path.relpath(fp, CONTEXT_DIR)
                ti = tar.gettarinfo(fp, arcname=arc)
                ti.mtime = 0  # deterministic
                with open(fp, "rb") as fh:
                    tar.addfile(ti, fh)
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as g:
        g.write(buf.getvalue())
    return gz.getvalue()


def _multipart(fields, file_bytes):
    """Build a presigned-POST multipart body (fields first, file last)."""
    boundary = "----pcccr" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    if "Content-Type" not in fields:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"Content-Type\"\r\n\r\napplication/gzip\r\n".encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"context.tar.gz\"\r\n"
        f"Content-Type: application/gzip\r\n\r\n".encode()
        + file_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _cloud_build(token):
    """Upload the bundled context and run a PCC cloud build → returns build id.

    Documented flow: POST /builds/upload-url → S3 presigned POST → POST /builds
    → poll GET /builds/{id} until status in {success, failed, timeout}.
    """
    st, up = _req("POST", f"{API}/builds/upload-url", token, body={"region": DEFAULT_REGION})
    if st not in (200, 201) or not isinstance(up, dict):
        raise RuntimeError(f"upload-url failed: {st} {up}")
    tarball = _make_tarball()
    body, ctype = _multipart(up.get("uploadFields", {}), tarball)
    # presigned S3 POST — unauthenticated (no bearer), just the form
    req = urllib.request.Request(up["uploadUrl"], data=body, method="POST",
                                 headers={"Content-Type": ctype})
    with _https_urlopen(req, timeout=120) as r:
        if r.status not in (200, 201, 204):
            raise RuntimeError(f"context upload failed: {r.status}")
    st, bc = _req("POST", f"{API}/builds", token,
                  body={"uploadId": up["uploadId"], "region": DEFAULT_REGION,
                        "dockerfilePath": "Dockerfile"})
    if st not in (200, 201) or not isinstance(bc, dict):
        raise RuntimeError(f"build create failed: {st} {bc}")
    build = bc.get("build") or {}
    build_id = build.get("id")
    context_hash = bc.get("contextHash") or build.get("contextHash")
    # 200 = cached build (already success); 201 = new build, must poll.
    if bc.get("cached") or build.get("status") == "success":
        return build_id
    for _ in range(70):
        st, bs = _req("GET", f"{API}/builds/{build_id}", token)
        b = (bs or {}).get("build") or {}
        status = b.get("status") if isinstance(bs, dict) else None
        context_hash = context_hash or b.get("contextHash")
        if status == "success":
            return build_id
        if status in ("failed", "timeout"):
            # PCC tags images by context hash with an IMMUTABLE tag. If this exact
            # context was built before, the push collides ("tag already exists")
            # and the build is marked failed — but a SUCCESS build for the same
            # contextHash already exists. Reuse it instead of failing the stack.
            reused = _find_success_build(token, context_hash) if context_hash else None
            if reused:
                return reused
            raise RuntimeError(f"cloud build {build_id} {status}: {b.get('errorMessage','')}")
        time.sleep(10)
    raise RuntimeError(f"cloud build {build_id} timed out (polling)")


def _find_success_build(token, context_hash):
    """Find an existing successful build for a given context hash (to reuse when a
    rebuild hits the immutable-tag push collision)."""
    st, body = _req("GET", f"{API}/builds?limit=50", token)
    if st != 200 or not isinstance(body, dict):
        return None
    for b in body.get("builds", []):
        if b.get("contextHash") == context_hash and b.get("status") == "success":
            return b.get("id")
    return None


def _deploy(token, props, update):
    """Create (POST /agents) or update (POST /agents/{name}) the agent.

    Note: update is also POST (not PUT), and the update body has NO serviceName
    (the name is in the path). nodeType must be 'arm'.
    """
    name = props["AgentName"]
    scaling = {"minAgents": int(props.get("MinAgents", 0)),
               "maxAgents": int(props.get("MaxAgents", 5))}
    common = {
        "buildId": props["_buildId"],
        "secretSet": props["SecretSet"],
        "autoScaling": scaling,
        "nodeType": "arm",
        "agentProfile": props.get("AgentProfile", "agent-1x"),
        "krispViva": {"audioFilter": props.get("KrispAudioFilter", "tel")},
        "forceRedeploy": True,
    }
    if update:
        st, body = _req("POST", f"{API}/agents/{name}", token, body=common)
    else:
        st, body = _req("POST", f"{API}/agents", token, body={"serviceName": name, **common})
    if st not in (200, 201):
        # Create on an existing agent → fall back to update.
        if not update and st in (409, 422):
            return _deploy(token, props, update=True)
        raise RuntimeError(f"deploy ({'update' if update else 'create'}) failed: {st} {body}")
    return body


# ── CFN response ─────────────────────────────────────────────────────────────
def _send(event, context, status, data=None, reason=None, pid=None):
    body = json.dumps({
        "Status": status,
        "Reason": reason or f"See CloudWatch: {context.log_stream_name}",
        "PhysicalResourceId": pid or event.get("PhysicalResourceId") or event["LogicalResourceId"],
        "StackId": event["StackId"], "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"], "Data": data or {},
    }).encode()
    req = urllib.request.Request(event["ResponseURL"], data=body, method="PUT",
                                 headers={"Content-Type": ""})
    _https_urlopen(req, timeout=30)


# Sentinel physical-id returned ONLY when a Create fails before it produced a
# usable agent. CloudFormation's rollback then sends a Delete carrying THIS id,
# and the Delete handler no-ops on it — so a failed create never tries to delete
# an agent it didn't successfully create. A successful deploy returns the REAL
# id (pcc-agent/{name}); a later intentional `delete-stack` carries that real id
# and DELETES the agent for real. This is the standard custom-resource pattern
# that distinguishes "rollback of a failed create" from "intentional teardown" —
# they are otherwise identical Delete events.
SENTINEL_PID = "pcc-agent/none"


def handler(event, context):
    rtype = event["RequestType"]
    props = event.get("ResourceProperties", {})
    agent = props.get("AgentName", "voice-analytics-agent")
    real_pid = f"pcc-agent/{agent}"

    try:
        sm = boto3.client("secretsmanager")
        token = sm.get_secret_value(SecretId=props["PatSecretArn"])["SecretString"].strip()

        if rtype == "Delete":
            # CFN owns the agent's lifecycle. Delete the agent UNLESS this is the
            # rollback of a never-created resource (sentinel id) — then no-op.
            if event.get("PhysicalResourceId") == SENTINEL_PID:
                return _send(event, context, "SUCCESS", pid=SENTINEL_PID)
            _req("DELETE", f"{API}/agents/{agent}", token)  # 404 is harmless
            return _send(event, context, "SUCCESS", pid=real_pid)

        # Create / Update: build, then deploy. Create is idempotent — if the agent
        # already exists (re-run, drift), update it instead of erroring.
        props["_buildId"] = _cloud_build(token)
        st, _ = _req("GET", f"{API}/agents/{agent}", token)
        _deploy(token, props, update=(st == 200))
        return _send(event, context, "SUCCESS", data={"AgentName": agent}, pid=real_pid)
    except Exception as e:  # noqa: BLE001
        if rtype == "Delete":
            # Best-effort: never wedge a stack delete on a PCC API hiccup.
            return _send(event, context, "SUCCESS", reason=f"delete best-effort: {e}",
                         pid=event.get("PhysicalResourceId") or real_pid)
        if rtype == "Create":
            # Failed create produced no managed agent → sentinel, so the rollback
            # Delete no-ops instead of deleting (a possibly-half-made) agent.
            return _send(event, context, "FAILED", reason=str(e), pid=SENTINEL_PID)
        # Failed UPDATE: keep the real id (the agent from the prior successful
        # create still exists and stays CFN-managed).
        return _send(event, context, "FAILED", reason=str(e), pid=real_pid)
