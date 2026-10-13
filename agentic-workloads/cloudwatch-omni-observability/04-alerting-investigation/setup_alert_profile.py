"""Create the access profile that Omni alerts run their queries as. Run it once per space.

An alert evaluates its query as an access profile. A profile is an empty container
until two grants exist:
  1. a permission grant: principal ACCESS_PROFILE gets READ (that includes
     StartTelemetryQuery and GetTelemetryQueryResults; without them an alert sits at NODATA)
  2. a trust grant: principal ALERT / ALL gets CUSTOM cloudwatch:AssumeAccessProfile,
     scoped to this profile's ARN. ALL is required: an alert's ID doesn't exist until
     it's created, so CreateAlert checks the wildcard alert ARN.

Dry run by default. It's idempotent: existing pieces are reused.

    python setup_alert_profile.py            # show what would be created
    python setup_alert_profile.py --apply
"""

import argparse
import os
import pathlib
import sys

import boto3

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))
from omni_query import load_env  # noqa: E402

PROFILE_NAME = "omni-samples-alerts"


def resolve_space(client, region):
    space_id = os.environ.get("OMNI_SPACE_ID")
    spaces = [s for s in client.list_spaces()["items"] if s["region"] == region and s["status"] == "ACTIVE"]
    if space_id:
        spaces = [s for s in spaces if s["spaceId"] == space_id]
    if len(spaces) != 1:
        sys.exit(f"Expected one ACTIVE space in {region} (OMNI_SPACE_ID={space_id!r}); found {len(spaces)}")
    return spaces[0]


def domain_id_for(client, space):
    for d in client.list_domains()["items"]:
        if d["domainArn"] == space.get("domainArn"):
            return d["domainId"]
    sys.exit(f"Could not find the domain {space.get('domainArn')} of space {space['spaceId']}")


def grants(client, space_id, **filters):
    out = []
    for page in client.get_paginator("list_access_grants").paginate(spaceId=space_id, **filters):
        out += page["items"]
    return out


def main():
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    region = os.environ["AWS_REGION"]
    client = boto3.client("cloudwatchomni", region_name=region)
    space = resolve_space(client, region)
    space_id, domain_id = space["spaceId"], domain_id_for(client, space)
    print(f"Space {space['name']} ({space_id}), domain {domain_id}")

    profile = next((p for p in client.list_access_profiles(spaceId=space_id)["items"]
                    if p["name"] == PROFILE_NAME), None)
    if profile:
        print(f"= profile exists: {profile['profileId']}")
    elif args.apply:
        profile = client.create_access_profile(
            spaceId=space_id, name=PROFILE_NAME,
            description="Read-only boundary for the omni-samples alerts: run their SQL/PromQL queries, nothing else.",
        )["accessProfile"]
        print(f"+ created profile {profile['profileId']}")
    else:
        print(f"+ would create profile {PROFILE_NAME!r}")
        print("\nDRY RUN. Re-run with --apply.")
        return

    profile_id, profile_arn = profile["profileId"], profile["arn"]  # use the ARN as returned, never hand-built

    if grants(client, space_id, principalType="ACCESS_PROFILE", principalId=profile_id):
        print("= permission grant exists (ACCESS_PROFILE)")
    elif args.apply:
        client.create_access_grant(
            domainId=domain_id, spaceId=space_id, name="omni-samples-alerts-read",
            principal={"principalType": "ACCESS_PROFILE", "principalId": profile_id},
            permission="READ",
        )
        print("+ granted READ to the profile")
    else:
        print("+ would grant READ to the profile")

    trust = [g for g in grants(client, space_id, principalType="ALERT", principalId="ALL")
             if g["permission"] == "CUSTOM"]
    trust = [g for g in trust
             if any(profile_arn in r.get("resourceArns", [])
                    for sa in client.get_access_grant(grantId=g["grantId"])["accessGrant"].get("scopedActions", [])
                    for r in sa.get("resources", []))]
    if trust:
        print("= trust grant exists (ALERT/ALL -> AssumeAccessProfile)")
    elif args.apply:
        client.create_access_grant(
            domainId=domain_id, spaceId=space_id, name="omni-samples-alerts-assume",
            principal={"principalType": "ALERT", "principalId": "ALL"},
            permission="CUSTOM",
            scopedActions=[{"actions": ["cloudwatch:AssumeAccessProfile"],
                            "resources": [{"resourceType": "AccessProfile", "resourceArns": [profile_arn]}]}],
        )
        print("+ granted ALERT/ALL the right to assume the profile")
    else:
        print("+ would grant ALERT/ALL the right to assume the profile")

    print(f"\nProfile ID for create_alerts.py: {profile_id}" if args.apply else "\nDRY RUN. Re-run with --apply.")


if __name__ == "__main__":
    main()
