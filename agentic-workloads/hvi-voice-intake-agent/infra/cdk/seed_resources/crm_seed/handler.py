"""CRM seed custom-resource handler (Data stack).

Loads ``crm-customers.json`` (vendored beside this handler) into the
``hvi-crm-cache`` table, one item per customer keyed by ``customerId``. The
seed file is a local projection of Salesforce; Salesforce stays the system of
record. Idempotent (``PutItem`` by key) and fail-loud (empty seed raises).

Invoked by the CDK ``Provider`` framework; the event carries ``RequestType``
and ``ResourceProperties`` (``TableName``, ``SeedFile``, ``SeedHash``).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

import boto3

_HERE = os.path.dirname(os.path.abspath(__file__))
_ddb = boto3.resource("dynamodb")


def _physical_id(event: dict) -> str:
    return event.get("PhysicalResourceId") or "hvi-crm-cache-seed"


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})

    if request_type == "Delete":
        return {"PhysicalResourceId": _physical_id(event)}

    table_name = props["TableName"]
    seed_file = props["SeedFile"]
    table = _ddb.Table(table_name)

    path = os.path.join(_HERE, seed_file)
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle, parse_float=Decimal)

    customers = doc.get("customers", [])
    count = 0
    with table.batch_writer() as writer:
        for customer in customers:
            if "customerId" not in customer:
                continue
            writer.put_item(Item=customer)
            count += 1

    if count == 0:
        raise RuntimeError(f"CRM seed produced no customers from {seed_file}")

    return {
        "PhysicalResourceId": _physical_id(event),
        "Data": {"ItemCount": str(count)},
    }
