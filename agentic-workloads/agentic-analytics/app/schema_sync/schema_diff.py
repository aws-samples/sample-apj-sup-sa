"""Pure schema snapshot diff and classification for the schema change pipeline.

A snapshot is a dict: {"objects": {name: {"kind": "table"|"view", "comment": str,
"columns": [{"name", "type", "comment"}], "rls": bool, "has_policy": bool, "sql": str}}}.
No I/O here, so the rules can be unit-tested and benchmarked offline.

Severity, highest wins:
  blocked  - a tenant table (it has an account_id column) without RLS enabled and a policy
  breaking - a drop, rename or column type change (needs approval before publish)
  additive - new tables, views or columns (publishes automatically)
  none     - only comments changed, or nothing changed
"""

TENANT_COLUMN = "account_id"
SEVERITY_ORDER = ["none", "additive", "breaking", "blocked"]


def _cols(obj):
    return {c["name"]: c["type"] for c in obj.get("columns", [])}


def _is_tenant_table(obj):
    return obj.get("kind") == "table" and TENANT_COLUMN in _cols(obj)


def _signature(obj):
    """Order-independent column signature used to recognise a renamed table."""
    return (obj.get("kind"), tuple(sorted(_cols(obj).items())))


def diff(old, new):
    """Return a list of change dicts: {type, object, column?, from?, to?, detail}."""
    o, n = old.get("objects", {}), new.get("objects", {})
    changes = []
    dropped = sorted(set(o) - set(n))
    added = sorted(set(n) - set(o))

    # Table/view renames: a dropped and an added object with the same kind and identical columns.
    for d in list(dropped):
        match = [a for a in added if _signature(o[d]) == _signature(n[a])]
        if len(match) == 1:
            a = match[0]
            changes.append({"type": f"{o[d]['kind']}_renamed", "object": a, "from": d, "to": a,
                            "detail": "probable rename: same kind and identical columns"})
            dropped.remove(d)
            added.remove(a)

    for d in dropped:
        changes.append({"type": f"{o[d]['kind']}_dropped", "object": d})
    for a in added:
        changes.append({"type": f"{n[a]['kind']}_added", "object": a})

    for name in sorted(set(o) & set(n)):
        oc, nc = _cols(o[name]), _cols(n[name])
        gone = [c for c in oc if c not in nc]
        new_cols = [c for c in nc if c not in oc]
        # Column renames: one dropped and one added column of the same type.
        for g in list(gone):
            same_type = [c for c in new_cols if nc[c] == oc[g]]
            if len(same_type) == 1 and len(gone) == len(new_cols):
                changes.append({"type": "column_renamed", "object": name, "from": g, "to": same_type[0],
                                "detail": "probable rename: same type, one column out and one in"})
                gone.remove(g)
                new_cols.remove(same_type[0])
        for g in gone:
            changes.append({"type": "column_dropped", "object": name, "column": g})
        for c in new_cols:
            changes.append({"type": "column_added", "object": name, "column": c})
        for c in sorted(set(oc) & set(nc)):
            if oc[c] != nc[c]:
                changes.append({"type": "column_retyped", "object": name, "column": c,
                                "from": oc[c], "to": nc[c]})
        if o[name].get("sql") != n[name].get("sql") and n[name].get("kind") == "view":
            changes.append({"type": "view_definition_changed", "object": name})
        if (o[name].get("comment") or "") != (n[name].get("comment") or ""):
            changes.append({"type": "comment_changed", "object": name})
        # Turning off RLS or dropping the policy changes no column, but rls_gaps must see it.
        if (bool(o[name].get("rls")), bool(o[name].get("has_policy"))) != \
                (bool(n[name].get("rls")), bool(n[name].get("has_policy"))):
            changes.append({"type": "rls_changed", "object": name,
                            "from": {"rls": bool(o[name].get("rls")), "has_policy": bool(o[name].get("has_policy"))},
                            "to": {"rls": bool(n[name].get("rls")), "has_policy": bool(n[name].get("has_policy"))}})
    return changes


def rls_gaps(new, changes):
    """Tenant tables touched by this change set that lack RLS or a policy."""
    touched = {c["object"] for c in changes if c["type"] in
               ("table_added", "table_renamed", "column_added", "column_renamed", "column_retyped",
                "rls_changed")}
    objs = new.get("objects", {})
    return sorted(t for t in touched if t in objs and _is_tenant_table(objs[t])
                  and not (objs[t].get("rls") and objs[t].get("has_policy")))


def classify(old, new):
    """Return {severity, changes, rls_gaps} for a pair of snapshots."""
    changes = diff(old, new)
    severity = "none"
    for c in changes:
        t = c["type"]
        if t.endswith("_added"):
            level = "additive"
        elif t in ("comment_changed", "rls_changed"):  # a lost policy is caught by rls_gaps
            level = "none"
        else:
            level = "breaking"  # drops, renames, retypes, changed view definitions
        if SEVERITY_ORDER.index(level) > SEVERITY_ORDER.index(severity):
            severity = level
    gaps = rls_gaps(new, changes)
    if gaps:
        severity = "blocked"
    return {"severity": severity, "changes": changes, "rls_gaps": gaps}
