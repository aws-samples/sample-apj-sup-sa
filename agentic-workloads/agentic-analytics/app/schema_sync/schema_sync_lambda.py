"""Schema change pipeline steps, called by the <env>-schema-sync Step Functions state machine.

Actions (event["action"]):
  snapshot  - read the live Postgres schema, diff it against the last published snapshot,
              classify the change set (schema_diff.classify) and store the new snapshot
  describe  - write a one-line business description for every object that needs one
              (Claude Haiku; DDL and names only, never row data); human COMMENT ON text wins
  hold      - store the change report and Step Functions task token for a human decision
  publish   - push descriptions to the Glue catalog, write one schema card per object to
              the business-context KB bucket, start KB ingestion, mark the snapshot current
See notes in the repo README ("Schema changes") for the flow.
"""
import datetime
import json
import os
import re

import boto3

from schema_diff import classify

S3 = boto3.client("s3")
GLUE = boto3.client("glue")
BEDROCK = boto3.client("bedrock-runtime")
BEDROCK_AGENT = boto3.client("bedrock-agent")
EVENTS = boto3.client("events")
SECRETS = boto3.client("secretsmanager")

ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "")
KB_DOCS_BUCKET = os.environ.get("KB_DOCS_BUCKET", "")
KB_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
KB_DATA_SOURCE_ID = os.environ.get("KB_DATA_SOURCE_ID", "")
GLUE_DATABASE = os.environ.get("GLUE_DATABASE", "")
AURORA_DATABASE = os.environ.get("AURORA_DATABASE", "timely_unicorn")
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")
DB_HOST = os.environ.get("AURORA_ENDPOINT", "")
DESCRIBE_MODEL = os.environ.get("DESCRIBE_MODEL_ID", "global.anthropic.claude-sonnet-5-5")
EVENT_BUS = os.environ.get("EVENT_BUS", "default")
PREFIX = os.environ.get("SCHEMA_PREFIX", "schema/")  # must match the IAM grant (ArtifactsPrefix + schema/)
CARD_PREFIX = "docs/schema/"

SNAPSHOT_SQL = """
SELECT c.relname AS name,
       CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'view' ELSE 'table' END AS kind,
       COALESCE(obj_description(c.oid, 'pg_class'), '') AS comment,
       c.relrowsecurity AS rls,
       EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS has_policy,
       COALESCE(pg_get_viewdef(c.oid, true), '') AS sql
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
ORDER BY c.relname
"""
COLUMNS_SQL = """
SELECT c.relname AS obj, a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
       COALESCE(col_description(c.oid, a.attnum), '') AS comment
FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""


def _connect():
    import psycopg2  # from the psycopg2 layer
    secret = json.loads(SECRETS.get_secret_value(SecretId=DB_SECRET_ARN)["SecretString"])
    return psycopg2.connect(host=DB_HOST or secret.get("host"), port=int(secret.get("port", 5432)),
                            dbname=AURORA_DATABASE, user=secret["username"], password=secret["password"],
                            connect_timeout=10, sslmode="require")


def read_schema():
    """Snapshot of the public schema in the shape schema_diff expects."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(SNAPSHOT_SQL)
            objects = {r[0]: {"kind": r[1], "comment": r[2], "rls": r[3], "has_policy": r[4],
                              "sql": r[5], "columns": []} for r in cur.fetchall()}
            cur.execute(COLUMNS_SQL)
            for obj, name, typ, comment in cur.fetchall():
                if obj in objects:
                    objects[obj]["columns"].append({"name": name, "type": typ, "comment": comment})
        conn.rollback()
    finally:
        conn.close()
    return {"taken_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "objects": objects}


def _get_json(key, default=None):
    try:
        return json.loads(S3.get_object(Bucket=ARTIFACTS_BUCKET, Key=key)["Body"].read())
    except S3.exceptions.NoSuchKey:
        return default


def _put_json(bucket, key, data):
    S3.put_object(Bucket=bucket, Key=key, Body=json.dumps(data, indent=1, default=str).encode(),
                  ContentType="application/json")


def action_snapshot(event):
    new = read_schema()
    # {"rebuild": true} as the execution input republishes every card and description,
    # e.g. after cards were edited or deleted by hand.
    rebuild = bool((event.get("input") or {}).get("rebuild"))
    current = None if rebuild else _get_json(PREFIX + "current.json")
    baseline = current is None
    result = classify(current or {"objects": {}}, new)
    run_id = event.get("run_id") or new["taken_at"].replace(":", "").replace("-", "")[:15]
    key = f"{PREFIX}snapshots/{run_id}.json"
    _put_json(ARTIFACTS_BUCKET, key, new)
    # The first run has nothing to compare with: everything is "added", so it publishes
    # (descriptions and cards for the whole schema) unless a tenant table lacks RLS.
    return {"run_id": run_id, "snapshot_key": key, "baseline": baseline, **result,
            "summary": summarize(result)}


def summarize(result):
    lines = [f"Severity: {result['severity']}"]
    for c in result["changes"]:
        what = c["type"].replace("_", " ")
        target = c["object"] + (f".{c['column']}" if c.get("column") else "")
        extra = f" ({c['from']} -> {c['to']})" if c.get("from") else ""
        lines.append(f"- {what}: {target}{extra}")
    if result["rls_gaps"]:
        lines.append("Blocked: tenant tables without row-level security or a policy: " + ", ".join(result["rls_gaps"]))
    return "\n".join(lines)


def _needs_description(obj):
    return not (obj.get("comment") or "").strip()


def describe_object(name, obj):
    cols = ", ".join(f"{c['name']} {c['type']}" for c in obj["columns"])
    body = f"{obj['kind']} {name}({cols})"
    if obj["kind"] == "view" and obj.get("sql"):
        body += "\nDefinition:\n" + obj["sql"][:4000]
    prompt = ("Write ONE plain sentence (under 30 words) describing what business question this database "
              "object answers, for an analyst choosing which table to query. No preamble.\n\n" + body)
    r = BEDROCK.converse(modelId=DESCRIBE_MODEL, messages=[{"role": "user", "content": [{"text": prompt}]}],
                         inferenceConfig={"maxTokens": 120})
    text = "".join(c.get("text", "") for c in r["output"]["message"]["content"]).strip()
    return re.sub(r"\s+", " ", text)[:300]


OBJECT_DROPS = ("table_dropped", "view_dropped")
OBJECT_RENAMES = ("table_renamed", "view_renamed")


def touched_objects(changes):
    """Objects whose card must be (re)written. A dropped column still leaves its table in place."""
    return {c["object"] for c in changes if c["type"] not in OBJECT_DROPS}


def removed_objects(changes):
    """Objects whose card must be deleted: dropped tables/views and the old name of a renamed one."""
    return ([c["object"] for c in changes if c["type"] in OBJECT_DROPS]
            + [c["from"] for c in changes if c["type"] in OBJECT_RENAMES])


def action_describe(event):
    snap = _get_json(event["snapshot_key"])
    touched = touched_objects(event["changes"])
    if event.get("baseline"):
        touched = set(snap["objects"])
    descriptions = {}
    for name in sorted(touched):
        obj = snap["objects"].get(name)
        if not obj:
            continue
        descriptions[name] = obj["comment"].strip() if not _needs_description(obj) else describe_object(name, obj)
    return {**event, "descriptions": descriptions}


def action_hold(event):
    key = f"{PREFIX}pending/{event['run_id']}.json"
    _put_json(ARTIFACTS_BUCKET, key, {"task_token": event["task_token"], "summary": event["summary"],
                                      "changes": event["changes"], "run_id": event["run_id"]})
    print(f"Approval needed for schema run {event['run_id']}:\n{event['summary']}")
    return {"pending_key": key}


def schema_card(name, obj, description, changes):
    lines = [f"# {obj['kind'].title()} `{name}`", "", description or "", ""]
    for c in changes:
        if c.get("to") == name or (c["object"] == name and c.get("from")):
            lines.append(f"Note: {c['type'].replace('_', ' ')} on {datetime.date.today()}: "
                         f"{c.get('from')} -> {c.get('to')}. Older questions may use the old name.")
    lines += ["", "| Column | Type | Meaning |", "|---|---|---|"]
    lines += [f"| {c['name']} | {c['type']} | {c.get('comment', '')} |" for c in obj["columns"]]
    return "\n".join(lines) + "\n"


_PG_TO_GLUE = (("character varying", "string"), ("character", "string"), ("text", "string"),
               ("uuid", "string"), ("json", "string"), ("bigint", "bigint"), ("integer", "int"),
               ("smallint", "smallint"), ("boolean", "boolean"), ("double precision", "double"),
               ("real", "float"), ("timestamp", "timestamp"), ("date", "date"))


def _glue_type(pg_type):
    if pg_type.startswith("numeric"):
        return "decimal" + pg_type[len("numeric"):]
    return next((g for p, g in _PG_TO_GLUE if pg_type.startswith(p)), "string")


def glue_columns(name, obj, existing, changes):
    """The Glue column list for a table, rebuilt from the Postgres snapshot.

    The text-to-SQL tool reads columns from Glue, and the crawler only refreshes them on its next
    run, so Publish rewrites them: renamed and added columns are visible to the agent at once.
    Columns Glue already has keep the crawler's type; a renamed column keeps its old one."""
    renamed = {c["to"]: c["from"] for c in changes
               if c["type"] == "column_renamed" and c["object"] == name}
    by_name = {c["Name"]: c for c in existing}
    cols = []
    for c in obj["columns"]:
        old = by_name.get(c["name"]) or by_name.get(renamed.get(c["name"], ""))
        col = {"Name": c["name"], "Type": old["Type"] if old else _glue_type(c["type"])}
        if c.get("comment"):
            col["Comment"] = c["comment"][:255]
        cols.append(col)
    return cols


_GLUE_TABLE_INPUT_KEYS = ("Name", "Description", "Owner", "Retention", "StorageDescriptor", "PartitionKeys",
                          "TableType", "Parameters", "ViewOriginalText", "ViewExpandedText", "TargetTable")


def _glue_name(name):
    return f"{AURORA_DATABASE}_public_{name}"


def _get_glue_table(name):
    try:
        return GLUE.get_table(DatabaseName=GLUE_DATABASE, Name=_glue_name(name))["Table"]
    except GLUE.exceptions.EntityNotFoundException:
        return None


def _update_glue_table(name, obj, description, changes):
    table = _get_glue_table(name)
    renamed_from = next((c["from"] for c in changes
                         if c["type"] in OBJECT_RENAMES and c["object"] == name), None)
    create = False
    if table is None and renamed_from:
        # A renamed table: carry the crawled entry over to the new name, so the
        # text-to-SQL tool stops offering the old name before the next crawl.
        table = _get_glue_table(renamed_from)
        create = table is not None
    if table is None:
        return False  # not crawled yet (views are not crawled); the KB card still carries it
    table_input = {k: v for k, v in table.items() if k in _GLUE_TABLE_INPUT_KEYS}
    table_input["Name"] = _glue_name(name)
    table_input["Description"] = description
    sd = table_input.get("StorageDescriptor")
    if sd is not None and obj["kind"] == "table":
        sd["Columns"] = glue_columns(name, obj, sd.get("Columns", []), changes)
    if create:
        GLUE.create_table(DatabaseName=GLUE_DATABASE, TableInput=table_input)
    else:
        GLUE.update_table(DatabaseName=GLUE_DATABASE, TableInput=table_input)
    return True


def _delete_glue_table(name):
    try:
        GLUE.delete_table(DatabaseName=GLUE_DATABASE, Name=_glue_name(name))
    except GLUE.exceptions.EntityNotFoundException:
        pass


def is_stale(snap, current):
    """True if the published snapshot was taken after this one (ISO UTC timestamps sort)."""
    return bool(current and snap and (current.get("taken_at") or "") > (snap.get("taken_at") or ""))


def action_publish(event):
    snap = _get_json(event["snapshot_key"])
    current = _get_json(PREFIX + "current.json")
    if is_stale(snap, current):
        # An older run approved after a newer one: publishing it would roll the schema back.
        return {"run_id": event["run_id"], "skipped": "a newer snapshot is already published"}
    descriptions = event.get("descriptions", {})
    glue_updated, cards = 0, 0
    for name, desc in descriptions.items():
        obj = snap["objects"][name]
        if GLUE_DATABASE and _update_glue_table(name, obj, desc, event["changes"]):
            glue_updated += 1
        if KB_DOCS_BUCKET:
            S3.put_object(Bucket=KB_DOCS_BUCKET, Key=f"{CARD_PREFIX}{name}.md",
                          Body=schema_card(name, obj, desc, event["changes"]).encode(), ContentType="text/markdown")
            cards += 1
    dropped = removed_objects(event["changes"])
    for name in dropped:
        if GLUE_DATABASE:
            _delete_glue_table(name)
        if KB_DOCS_BUCKET:
            S3.delete_object(Bucket=KB_DOCS_BUCKET, Key=f"{CARD_PREFIX}{name}.md")
    ingestion = None
    if KB_ID and KB_DATA_SOURCE_ID and (cards or dropped):
        ingestion = BEDROCK_AGENT.start_ingestion_job(knowledgeBaseId=KB_ID, dataSourceId=KB_DATA_SOURCE_ID)[
            "ingestionJob"]["ingestionJobId"]
    _put_json(ARTIFACTS_BUCKET, PREFIX + "current.json", snap)
    EVENTS.put_events(Entries=[{"Source": "agentic-analytics.schema", "DetailType": "Schema Published",
                                "EventBusName": EVENT_BUS,
                                "Detail": json.dumps({"run_id": event["run_id"], "severity": event["severity"],
                                                      "changes": event["changes"]})}])
    return {"run_id": event["run_id"], "glue_descriptions": glue_updated, "kb_cards": cards,
            "kb_cards_removed": len(dropped), "ingestion_job": ingestion}


ACTIONS = {"snapshot": action_snapshot, "describe": action_describe, "hold": action_hold,
           "publish": action_publish}


def lambda_handler(event, context):
    action = event.get("action")
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    return ACTIONS[action](event)
