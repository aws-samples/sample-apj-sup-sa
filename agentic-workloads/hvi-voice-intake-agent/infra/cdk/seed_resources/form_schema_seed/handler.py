"""Form-schema seed custom-resource handler (Data stack).

Loads the two form-definition JSON files vendored beside this handler into the
``hvi-form-schema`` table, keyed ``(formId, itemId)``. Idempotent: every item
is a ``PutItem`` (overwrite by key), so a re-run converges. Fail-loud: an empty
or malformed seed raises, failing the custom resource and rolling the stack
back.

Invoked by the CDK ``Provider`` framework; the event carries ``RequestType``
and ``ResourceProperties`` (``TableName``, ``SeedFiles`` — a JSON list of the
vendored filenames — and ``SeedHash`` for change detection).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

import boto3

_HERE = os.path.dirname(os.path.abspath(__file__))
_ddb = boto3.resource("dynamodb")


def _physical_id(event: dict) -> str:
    return event.get("PhysicalResourceId") or "hvi-form-schema-seed"


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})

    if request_type == "Delete":
        # Seed data is retained with its table (RETAIN); nothing to undo.
        return {"PhysicalResourceId": _physical_id(event)}

    table_name = props["TableName"]
    seed_files = json.loads(props["SeedFiles"])
    table = _ddb.Table(table_name)

    count = 0
    with table.batch_writer() as writer:
        for filename in seed_files:
            path = os.path.join(_HERE, filename)
            with open(path, encoding="utf-8") as handle:
                doc = json.load(handle, parse_float=Decimal)
            form_id = doc["formId"]
            for item in doc.get("items", []):
                record = dict(item)
                record["formId"] = form_id
                record["itemId"] = item["itemId"]
                writer.put_item(Item=record)
                count += 1

    if count == 0:
        raise RuntimeError(
            f"form-schema seed produced no items from {seed_files}"
        )

    return {
        "PhysicalResourceId": _physical_id(event),
        "Data": {"ItemCount": str(count)},
    }
