"""
Associate Lex bots with the destination Connect instance.

Amazon Connect only uses Lex bots from its own account, and Lex bots do
not move between accounts. This function therefore never copies a bot. For
each Lex V2 bot associated with the source instance it:

1. Looks for a bot with the same bot name and alias name that is already
   associated with the target instance (matched with lexv2-models
   DescribeBot and DescribeBotAlias). That bot is what flows are mapped to.
2. Otherwise, when the alias is already in the target account (same
   account migration), associates it with the target instance.
3. Otherwise lists the bot in manualFollowUp: rebuild or import it in the
   target account and Region, associate it, then fix the flow placeholder.

Lex V1 (Lex Classic) bots are not supported: they are neither listed nor
associated.
"""
import boto3
from botocore.exceptions import ClientError

from arn_mapping import ARN_PATTERN, build_lex_map
from region_utils import (
    BOTO_CONFIG,
    create_connect_client_from_arn,
    create_destination_client,
    create_destination_session,
    extract_account_id_from_arn,
    extract_region_from_arn,
    reraise_if_fatal,
)
from region_utils import redact
from region_utils import describe_error, error_code_of
from region_utils import feature_skipped_result, feature_unavailable_follow_up
from region_utils import resolve_external_id
from region_utils import step_handler


@step_handler
def lambda_handler(event, context):
    source_instance_arn = event['sourceInstanceId']
    destination_instance_arn = event['destinationInstanceId']
    lex_v2_bots = event.get('lexV2Bots', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    dest_session = create_destination_session(destination_instance_arn, cross_account_role_arn, external_id)
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, session=dest_session
    )
    source_client, source_instance_id, _ = create_connect_client_from_arn(source_instance_arn)

    source_account = extract_account_id_from_arn(source_instance_arn)
    destination_account = extract_account_id_from_arn(destination_instance_arn)
    is_cross_account = source_account != destination_account

    print(f"Processing {len(lex_v2_bots)} Lex V2 bots")
    print(f"Cross-account migration: {is_cross_account}")

    associated_v2, matched_v2, skipped, failed, follow_up = [], [], [], [], []
    lex_unavailable = {}  # {Region: error code} where Lex V2 is not offered

    # Lex V2 -------------------------------------------------------------
    if lex_v2_bots:
        lex_map, lex_names = build_lex_map(
            source_client, connect_client, source_instance_id, destination_instance_id,
            source_lex_factory=lambda region: boto3.client('lexv2-models', region_name=region, config=BOTO_CONFIG),
            dest_lex_factory=lambda region: dest_session.client('lexv2-models', region_name=region, config=BOTO_CONFIG),
            unavailable=lex_unavailable,
        )
    else:
        lex_map, lex_names = {}, {}

    # The target Region does not offer Lex V2: nothing can be matched, associated or rebuilt there.
    if destination_region in lex_unavailable:
        return feature_skipped_result(
            'lexBots', destination_region, 'target', lex_unavailable[destination_region], count=len(lex_v2_bots),
            associatedV2=[], matchedV2=[], skipped=[], failed=[],
            summary={'totalV2': len(lex_v2_bots), 'associatedV2': 0, 'matchedV2': 0, 'manualFollowUp': 1,
                     'skipped': 0, 'failed': 0})
    # A source alias Region without Lex V2: its aliases cannot be matched by name (listed below as usual).
    for region, code in sorted(lex_unavailable.items()):
        follow_up.append(feature_unavailable_follow_up('lexBots', region, 'source', code))

    for bot in lex_v2_bots:
        alias_arn = bot.get('aliasArn')
        if not alias_arn:
            continue
        names = lex_names.get(alias_arn)
        label = f"{names[0]}/{names[1]}" if names else alias_arn  # logs only, through redact()

        # V2 entries carry alias ARNs only: bot and alias names stay out of the step output.
        if alias_arn in lex_map:
            matched_v2.append({'aliasArn': alias_arn, 'targetAliasArn': lex_map[alias_arn]})
            print(f"Lex V2 {redact(label)}: matched by name to {redact(lex_map[alias_arn])}")
            continue

        parsed = ARN_PATTERN.fullmatch(alias_arn)
        if parsed and parsed.group('account') == destination_account:
            try:
                connect_client.associate_bot(InstanceId=destination_instance_id,
                                             LexV2Bot={'AliasArn': alias_arn})
                associated_v2.append({'aliasArn': alias_arn, 'version': 'V2'})
            except ClientError as e:
                reraise_if_fatal(e, resource=alias_arn)
                if e.response['Error']['Code'] in ('ResourceConflictException', 'DuplicateResourceException'):
                    skipped.append({'aliasArn': alias_arn, 'version': 'V2', 'reason': 'Already associated'})
                else:
                    failed.append({'aliasArn': alias_arn, 'version': 'V2', 'error': describe_error(e),
                                   'errorCode': error_code_of(e)})
            continue

        follow_up.append({
            'type': 'lexV2', 'source': alias_arn,
            'action': (f"Create a Lex V2 bot and alias matching source alias {alias_arn} in account "
                       f"{destination_account} Region {extract_region_from_arn(destination_instance_arn)} "
                       "(export and import from the source works), associate it with the target instance, "
                       "then re-run contact flows or replace the placeholder alias in the affected flows."),
        })

    print(f"Associated V2: {len(associated_v2)}, "
          f"matched by name: {len(matched_v2)}, manual follow up: {len(follow_up)}, failed: {len(failed)}")

    return {
        'statusCode': 200,
        'associatedV2': associated_v2,
        'matchedV2': matched_v2,
        'skipped': skipped,
        'failed': failed,
        'manualFollowUp': follow_up,
        'summary': {
            'totalV2': len(lex_v2_bots),
            'associatedV2': len(associated_v2),
            'matchedV2': len(matched_v2),
            'manualFollowUp': len(follow_up),
            'skipped': len(skipped),
            'failed': len(failed),
        },
    }
