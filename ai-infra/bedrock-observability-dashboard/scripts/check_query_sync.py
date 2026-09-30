#!/usr/bin/env python3
"""Verify each queries/logs-insights/<NN>-name.cwli file's body matches the
QueryString of the AWS::Logs::QueryDefinition whose Name ends with the same
<NN>-name. Pairs by NN-name suffix and requires EXACT normalized equality."""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _normalize(text, tenant_key="team"):
    # The template parameterizes the tenant key via ${TenantMetadataKey}; the
    # standalone .cwli files use the parameter's DEFAULT value literally.
    # Normalize so the two compare equal. Whitespace runs collapse to a single space.
    text = text.replace("${TenantMetadataKey}", tenant_key)
    return re.sub(r"\s+", " ", text).strip()


def _query_string(props):
    qs = props["QueryString"]
    # May be a plain block scalar (str) or an Fn::Sub (dict).
    if isinstance(qs, dict):
        sub = qs.get("Fn::Sub")
        return sub[0] if isinstance(sub, list) else sub
    return qs


def _name_suffix(props):
    name = props["Name"]
    name_str = name.get("Fn::Sub") if isinstance(name, dict) else name
    if isinstance(name_str, list):
        name_str = name_str[0]
    return name_str.split("/", 1)[1] if "/" in name_str else name_str


def find_mismatches():
    sys.path.insert(0, str(ROOT))
    from scripts.render_dashboard import CfnLoader
    with open(ROOT / "cloudformation" / "dashboard.yaml") as fh:
        template = yaml.load(fh, Loader=CfnLoader)

    tenant_default = (template.get("Parameters", {})
                      .get("TenantMetadataKey", {})
                      .get("Default", "team"))

    qdefs = {}
    for _name, res in template["Resources"].items():
        if res.get("Type") == "AWS::Logs::QueryDefinition":
            props = res["Properties"]
            qdefs[_name_suffix(props)] = _normalize(_query_string(props), tenant_default)

    mismatches = []
    cwli_dir = ROOT / "queries" / "logs-insights"
    for cwli in sorted(cwli_dir.glob("*.cwli")):
        stem = cwli.stem
        body = _normalize(cwli.read_text(), tenant_default)
        if stem not in qdefs:
            mismatches.append(f"{cwli.name} (no matching QueryDefinition '{stem}')")
        elif qdefs[stem] != body:
            mismatches.append(cwli.name)
    return mismatches


if __name__ == "__main__":
    bad = find_mismatches()
    if bad:
        print("OUT OF SYNC:", ", ".join(bad))
        raise SystemExit(1)
    print("all .cwli files in sync")
