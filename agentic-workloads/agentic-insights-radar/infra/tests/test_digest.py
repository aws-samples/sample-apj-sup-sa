# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest delivery (Slack channel or DM, email through SES): nothing when it is off, and in each mode
only what that mode needs, held by only the functions that use it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aws_cdk.assertions import Match

from insights_radar_infra import config, digest, frontend, nag
from insights_radar_infra.config import DeployConfig, DigestDelivery

from .conftest import Synth, build, synth
from .test_hardening import _actions, _statements, _waits_for, key_order_violations

ROOT = Path(__file__).resolve().parents[2]

SENDER = "no-reply@digest.example.com"
FULL = {
    "slackMode": "bot",
    "dmResolver": "directory",
    "directoryRoleArn": "arn:aws:iam::444455556666:role/DirectoryLookup",
    "directoryEndpoint": "https://directory.example.com/lookup",
    "emailMode": "ses",
    "emailSender": SENDER,
    "sesDomain": "digest.example.com",
    "sesHostedZoneId": "Z0123456789ABCDEFGHIJ",
    "sesDmarc": True,
}
IDENTITY = "arn:aws:ses:us-east-1:111122223333:identity/example.com"
DIGEST_TYPES = (
    "AWS::SES::ConfigurationSet",
    "AWS::SES::ConfigurationSetEventDestination",
    "AWS::SES::EmailIdentity",
    "AWS::SecretsManager::Secret",
    "AWS::Route53::RecordSet",
)
ENV_KEYS = {
    "DIGEST_SLACK_MODE",
    "DIGEST_DM_RESOLVER",
    "DIGEST_EMAIL_MODE",
    "DIGEST_EMAIL_SENDER",
    "DIGEST_SES_CONFIG_SET",
    "DIGEST_SLACK_SECRET_ARN",
    "DIGEST_UNSUB_SECRET_ARN",
    "DIGEST_QUEUE_URL",
    "DIGEST_DIRECTORY_ENDPOINT",
    "DIGEST_DIRECTORY_ROLE_ARN",
    "APP_URL",
}


def _synth(settings: dict[str, Any], **context: str) -> Synth:
    return synth(digestDelivery=json.dumps(settings), **context)


@pytest.fixture(scope="module")
def full(bundles: None) -> Synth:
    return _synth(FULL)


@pytest.fixture(scope="module")
def mail_only(bundles: None) -> Synth:
    """Email from an SES identity that already exists; no Slack."""
    return _synth(
        {
            "emailMode": "ses",
            "emailSender": "digest@example.com",
            "sesIdentityArn": IDENTITY,
        }
    )


@pytest.fixture(scope="module")
def webhook_mail(bundles: None) -> Synth:
    """A Slack webhook, and an SES domain with no zone given (its DNS is the operator's)."""
    return _synth(
        {
            "slackMode": "webhook",
            "emailMode": "ses",
            "emailSender": "news@mail.example.org",
            "sesDomain": "mail.example.org",
        }
    )


@pytest.fixture(scope="module")
def slack_only(bundles: None) -> Synth:
    return _synth({"slackMode": "bot", "dmResolver": "email"})


def _functions(s: Synth, prefix: str) -> list[dict[str, Any]]:
    return [
        v["Properties"]
        for k, v in s.resources("AWS::Lambda::Function").items()
        if k.startswith(prefix)
    ]


def _fn(s: Synth, prefix: str) -> dict[str, Any]:
    (fn,) = _functions(s, prefix)
    return fn


def _routes(s: Synth) -> dict[str, str | None]:
    return {
        p["RouteKey"]: p.get("AuthorizationType")
        for p in s.props("AWS::ApiGatewayV2::Route")
    }


def _ops_indexes(s: Synth) -> list[str]:
    (ops,) = [
        v["Properties"]
        for k, v in s.resources("AWS::DynamoDB::GlobalTable").items()
        if k.startswith("DataOpsTable")
    ]
    return [g["IndexName"] for g in ops["GlobalSecondaryIndexes"]]


def _data_key(s: Synth) -> dict[str, Any]:
    (key,) = [
        dict(v["Properties"])
        for k, v in s.resources("AWS::KMS::Key").items()
        if k.startswith("DataDataKey")
    ]
    return key


def _resource(st: dict[str, Any]) -> list[Any]:
    r = st["Resource"]
    return r if isinstance(r, list) else [r]


# ---- Off: nothing ------------------------------------------------------------------------------------


def test_all_off_adds_nothing(default: Synth, bundles: None) -> None:
    off = _synth({"slackMode": "off", "dmResolver": "none", "emailMode": "off"})
    assert set(off.template.to_json()["Resources"]) == set(
        default.template.to_json()["Resources"]
    )
    for type_ in DIGEST_TYPES:
        assert default.resources(type_) == {}, type_
    assert not _functions(default, "BackendDigest")
    assert _ops_indexes(default) == ["gsi1"]
    assert not ENV_KEYS & set(
        _fn(default, "BackendApiHandler")["Environment"]["Variables"]
    )
    assert "/api/digest/unsubscribe" not in json.dumps(_routes(default))
    options = json.loads(
        default.template.to_json()["Outputs"]["DeployOptions"]["Value"]
    )
    assert "digestDelivery" not in options
    outputs = default.template.to_json()["Outputs"]
    assert not {
        "DigestSlackSecretArn",
        "DigestSenderRoleArn",
        "DigestApiRoleArn",
    } & set(outputs)


# ---- The pipeline ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raised(bundles: None) -> Synth:
    """Both secrets at a raised generation: the recovery deploy must be as nag-clean as any (scan cycle 62)."""
    return _synth(FULL, slackSecretGeneration="2", unsubscribeKeyGeneration="1")


@pytest.mark.parametrize(
    "which", ["full", "mail_only", "webhook_mail", "slack_only", "raised"]
)
def test_every_mode_is_cdk_nag_clean_and_keeps_its_ordering_guarantees(
    which: str, request: pytest.FixtureRequest
) -> None:
    s: Synth = request.getfixturevalue(which)
    errors = s.annotations.find_error("*", Match.string_like_regexp("AwsSolutions-.*"))
    warnings = s.annotations.find_warning(
        "*", Match.string_like_regexp("AwsSolutions-.*")
    )
    assert [e.entry.data for e in errors] == []
    assert [w.entry.data for w in warnings] == []
    template = s.template.to_json()
    assert key_order_violations(template) == []
    resources = template["Resources"]
    ((seed, _),) = s.resources("Custom::Trigger").items()
    assert set(resources) - _waits_for(resources, seed) == {seed}


def test_scheduler_runs_every_15_minutes_and_keeps_failed_events(full: Synth) -> None:
    (rule,) = [r for r in full.props("AWS::Events::Rule") if r["Name"] == "air-digest"]
    assert rule["ScheduleExpression"] == "rate(15 minutes)"
    assert rule["Targets"][0]["DeadLetterConfig"]["Arn"]
    assert rule["Targets"][0]["RetryPolicy"]["MaximumRetryAttempts"] == 0
    schedule = _fn(full, "BackendDigestSchedule")
    assert schedule["Handler"] == "insights_radar.api.workers.digest_schedule"
    assert schedule["DeadLetterConfig"]["TargetArn"]
    # EventBridge may use the data key for this rule's dead letters too.
    key = _data_key(full)
    stmt = next(
        s
        for s in key["KeyPolicy"]["Statement"]
        if s.get("Sid") == "AllowSweepRuleDeadLetters"
    )
    assert "rule/air-digest" in json.dumps(stmt["Condition"])


def test_the_sender_takes_one_message_at_a_time_from_an_encrypted_queue(
    full: Synth,
) -> None:
    send = _fn(full, "BackendDigestSend")
    assert send["Handler"] == "insights_radar.api.workers.digest_send"
    (mapping,) = [
        m
        for m in full.props("AWS::Lambda::EventSourceMapping")
        if "BackendDigestSend" in json.dumps(m["FunctionName"])
    ]
    assert mapping["BatchSize"] == 1
    assert mapping["ScalingConfig"] == {"MaximumConcurrency": 2}
    assert mapping["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    queues = {
        k: v["Properties"]
        for k, v in full.resources("AWS::SQS::Queue").items()
        if k.startswith("BackendDigest")
    }
    (queue,) = [q for k, q in queues.items() if k.startswith("BackendDigestQueue")]
    (dlq_id,) = [k for k in queues if k.startswith("BackendDigestDlq")]
    assert queue["VisibilityTimeout"] > send["Timeout"]
    assert queue["RedrivePolicy"] == {
        "deadLetterTargetArn": {"Fn::GetAtt": [dlq_id, "Arn"]},
        "maxReceiveCount": 3,
    }
    assert all(q["KmsMasterKeyId"] for q in queues.values())


def test_reserved_concurrency_caps_the_digest_workers_when_asked(bundles: None) -> None:
    s = _synth({"slackMode": "bot"}, reservedConcurrency="true")
    assert _fn(s, "BackendDigestSend")["ReservedConcurrentExecutions"] == 2
    assert _fn(s, "BackendDigestSchedule")["ReservedConcurrentExecutions"] == 1


def test_feedback_reads_ses_bounces_and_complaints_from_an_encrypted_topic(
    full: Synth,
) -> None:
    feedback = _fn(full, "BackendDigestFeedback")
    assert feedback["Handler"] == "insights_radar.api.workers.digest_feedback"
    assert feedback["DeadLetterConfig"]["TargetArn"]
    (subscription,) = [
        p for p in full.props("AWS::SNS::Subscription") if p["Protocol"] == "lambda"
    ]
    topics = {
        k: v["Properties"]
        for k, v in full.resources("AWS::SNS::Topic").items()
        if k.startswith("BackendDigest")
    }
    ((topic_id, topic),) = topics.items()
    assert topic["KmsMasterKeyId"]
    assert subscription["TopicArn"] == {"Ref": topic_id}
    (destination,) = full.props("AWS::SES::ConfigurationSetEventDestination")
    assert destination["EventDestination"]["MatchingEventTypes"] == [
        "bounce",
        "complaint",
    ]
    assert destination["EventDestination"]["SnsDestination"]["TopicARN"] == {
        "Ref": topic_id
    }
    (config_set,) = full.props("AWS::SES::ConfigurationSet")
    assert config_set["Name"] == "air-digest"
    assert config_set["DeliveryOptions"]["TlsPolicy"] == "REQUIRE"
    # SES may publish, and use the data key, for this configuration set only.
    (policy,) = [
        p
        for p in full.props("AWS::SNS::TopicPolicy")
        if p["Topics"] == [{"Ref": topic_id}]
    ]
    (allow,) = [
        s for s in policy["PolicyDocument"]["Statement"] if s["Effect"] == "Allow"
    ]
    assert allow["Principal"] == {"Service": "ses.amazonaws.com"}
    condition = json.dumps(allow["Condition"])
    assert (
        "configuration-set/" in condition
        and "BackendDigestConfigurationSet" in condition
    )
    key = _data_key(full)
    (ses_key,) = [
        s
        for s in key["KeyPolicy"]["Statement"]
        if s.get("Sid") == "AllowSesDigestFeedback"
    ]
    assert ses_key["Condition"]["StringEquals"] == {
        "aws:SourceAccount": {"Ref": "AWS::AccountId"}
    }
    assert "configuration-set/air-digest" in json.dumps(
        ses_key["Condition"]["ArnEquals"]
    )


def test_the_due_index_is_added_to_the_ops_table(full: Synth) -> None:
    (ops,) = [
        v["Properties"]
        for k, v in full.resources("AWS::DynamoDB::GlobalTable").items()
        if k.startswith("DataOpsTable")
    ]
    (due,) = [g for g in ops["GlobalSecondaryIndexes"] if g["IndexName"] == "due"]
    assert due["KeySchema"] == [
        {"AttributeName": "due_pk", "KeyType": "HASH"},
        {"AttributeName": "due_sk", "KeyType": "RANGE"},
    ]


@pytest.mark.parametrize(
    "prefix",
    [
        "BackendApiHandler",
        "BackendDigestSchedule",
        "BackendDigestSend",
        "BackendDigestFeedback",
    ],
)
def test_the_contract_environment_is_set(full: Synth, prefix: str) -> None:
    env = _fn(full, prefix)["Environment"]["Variables"]
    assert ENV_KEYS <= set(env)
    assert env["DIGEST_SLACK_MODE"] == "bot"
    assert env["DIGEST_DM_RESOLVER"] == "directory"
    assert env["DIGEST_EMAIL_MODE"] == "ses"
    assert env["DIGEST_EMAIL_SENDER"] == SENDER
    assert env["DIGEST_SES_CONFIG_SET"] == "air-digest"
    assert env["DIGEST_DIRECTORY_ENDPOINT"] == FULL["directoryEndpoint"]
    assert env["DIGEST_DIRECTORY_ROLE_ARN"] == FULL["directoryRoleArn"]
    assert "BackendDigestSlackSecret" in json.dumps(env["DIGEST_SLACK_SECRET_ARN"])
    assert "BackendDigestUnsubscribeKey" in json.dumps(env["DIGEST_UNSUB_SECRET_ARN"])
    assert "BackendDigestQueue" in json.dumps(env["DIGEST_QUEUE_URL"])
    assert "CloudFront" in json.dumps(env["APP_URL"]) or "Cdn" in json.dumps(
        env["APP_URL"]
    )


def test_options_off_are_empty_in_the_environment(slack_only: Synth) -> None:
    env = _fn(slack_only, "BackendDigestSend")["Environment"]["Variables"]
    assert env["DIGEST_EMAIL_MODE"] == "off"
    assert env["DIGEST_EMAIL_SENDER"] == env["DIGEST_SES_CONFIG_SET"] == ""
    assert env["DIGEST_UNSUB_SECRET_ARN"] == env["DIGEST_DIRECTORY_ROLE_ARN"] == ""


def test_alarms_on_the_dead_letter_queue_and_the_first_sender_error(
    full: Synth,
) -> None:
    alarms = {a["AlarmDescription"]: a for a in full.props("AWS::CloudWatch::Alarm")}
    dlq = alarms[
        "A digest delivery failed three times and was set aside in the digest dead-letter queue. After fixing "
        "the cause, a redriven message for a time a later send already covered sends nothing: its items went "
        "out in that later digest. If every send of one channel lands here, its secret may be unreadable or"
        " deleted: the digestDelivery row of docs/implementation-guide.md says how to bring it back"
    ]
    assert dlq["MetricName"] == "ApproximateNumberOfMessagesVisible"
    assert alarms["DigestSend function errors"]["Threshold"] == 1
    assert alarms["DigestFeedback function errors"]["Threshold"] == 1
    assert alarms["DigestSchedule function errors"]["Threshold"] == 3


def test_outputs_name_the_slack_secret_and_the_sender_role(full: Synth) -> None:
    outputs = full.template.to_json()["Outputs"]
    assert "BackendDigestSlackSecret" in json.dumps(outputs["DigestSlackSecretArn"])
    assert "BackendDigestSendRole" in json.dumps(outputs["DigestSenderRoleArn"])
    # Both roles that assume the directory role are named, for its trust policy (scan cycle 2).
    assert "BackendApiHandlerRole" in json.dumps(outputs["DigestApiRoleArn"])
    options = json.loads(outputs["DeployOptions"]["Value"])
    assert json.loads(options["digestDelivery"]) == FULL


# ---- Secrets -----------------------------------------------------------------------------------


def _secret_kinds(synth: Synth) -> list[str]:
    """Which delivery secrets a template has, by construct ID (they carry no fixed name)."""
    kinds = ("SlackSecret", "UnsubscribeKey")
    return [
        k
        for lid in synth.resources("AWS::SecretsManager::Secret")
        for k in kinds
        if k in lid
    ]


def test_c58_outputs_name_the_current_secrets(full: Synth) -> None:
    outputs = full.template.to_json()["Outputs"]
    assert {"DigestSlackSecretArn", "DigestUnsubscribeKeyArn"} <= set(outputs)
    assert "UnsubscribeKey" in json.dumps(outputs["DigestUnsubscribeKeyArn"]["Value"])


def test_c59_outputs_follow_the_channels(
    slack_only: Synth, webhook_mail: Synth, full: Synth
) -> None:
    only = slack_only.template.to_json()["Outputs"]
    assert "DigestUnsubscribeKeyArn" not in only and "SlackSecret" in json.dumps(
        only["DigestSlackSecretArn"]
    )
    hook = webhook_mail.template.to_json()["Outputs"]
    assert (
        "SlackSecret" in json.dumps(hook["DigestSlackSecretArn"])
        and "DigestUnsubscribeKeyArn" in hook
    )
    faults = [
        a["AlarmDescription"]
        for a in full.props("AWS::CloudWatch::Alarm")
        if "because of a deployment setting" in a["AlarmDescription"]
    ]
    assert (
        faults
        and "DigestUnsubscribeKeyArn" in faults[0]
        and "<prefix>-digest-unsubscribe-key" not in faults[0]
    )


def test_secrets_hold_generated_placeholders_on_the_data_key(full: Synth) -> None:
    secrets = full.props("AWS::SecretsManager::Secret")
    assert sorted(_secret_kinds(full)) == ["SlackSecret", "UnsubscribeKey"]
    for s in secrets:
        # No fixed name: a secret made again never collides with a kept one's name (scan cycles 58 to 61).
        assert "Name" not in s and s["Description"].startswith("air: ")
        # Generated on create only: no token in the template, and an update never writes over the
        # value the operator put.
        assert "SecretString" not in s and s["GenerateSecretString"]
        assert "DataDataKey" in json.dumps(s["KmsKeyId"])
    assert "xoxb-" not in json.dumps(full.template.to_json()).replace("(xoxb-...)", "")


def test_webhook_mode_has_a_webhook_secret_and_no_bot_token(
    webhook_mail: Synth,
) -> None:
    assert sorted(_secret_kinds(webhook_mail)) == ["SlackSecret", "UnsubscribeKey"]


def test_slack_only_has_no_mail_pieces(slack_only: Synth) -> None:
    # c60: the unsubscribe key exists while digest delivery is on, so email off and on keeps every link valid.
    assert sorted(_secret_kinds(slack_only)) == ["SlackSecret", "UnsubscribeKey"]
    assert slack_only.resources("AWS::SES::ConfigurationSet") == {}
    assert slack_only.resources("AWS::SES::EmailIdentity") == {}
    assert not [
        p
        for p in slack_only.props("AWS::SNS::Subscription")
        if p["Protocol"] == "lambda"
    ]
    assert not _functions(slack_only, "BackendDigestFeedback")
    assert "/api/digest/unsubscribe" not in json.dumps(_routes(slack_only))
    assert not any(
        a.startswith("ses:")
        for a in _actions(_statements(slack_only, "BackendDigestSendRole"))
    )


# ---- Least privilege -----------------------------------------------------------------------------


def _grants(s: Synth, role: str, action: str) -> list[dict[str, Any]]:
    return [
        st
        for st in _statements(s, role)
        if action
        in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])
        and st["Effect"] == "Allow"
    ]


@pytest.mark.parametrize("role", ["BackendDigestSendRole", "BackendApiHandlerRole"])
def test_only_the_sender_and_the_api_may_read_the_secrets_and_send(
    full: Synth, role: str
) -> None:
    (secrets,) = _grants(full, role, "secretsmanager:GetSecretValue")
    assert secrets["Action"] == "secretsmanager:GetSecretValue"
    assert len(_resource(secrets)) == 2
    (send,) = _grants(full, role, "ses:SendEmail")
    assert send["Action"] == "ses:SendEmail"
    assert send["Condition"] == {"StringEquals": {"ses:FromAddress": SENDER}}
    targets = json.dumps(_resource(send))
    assert "configuration-set/air-digest" in targets
    assert "identity/digest.example.com" in targets
    assert len(_resource(send)) == 2
    (assume,) = _grants(full, role, "sts:AssumeRole")
    assert _resource(assume) == [FULL["directoryRoleArn"]]


def test_only_the_scheduler_and_the_sender_may_write_to_the_digest_queue(
    full: Synth,
) -> None:
    # The API sends "Send test now" itself and never queues (c2 security review).
    assert _grants(full, "BackendApiHandlerRole", "sqs:SendMessage") == []
    for role in ("BackendDigestScheduleRole", "BackendDigestSendRole"):
        assert _grants(full, role, "sqs:SendMessage")


@pytest.mark.parametrize(
    "role", ["BackendDigestScheduleRole", "BackendDigestFeedbackRole"]
)
def test_the_scheduler_and_feedback_hold_no_secret_mail_or_directory_access(
    full: Synth, role: str
) -> None:
    actions = _actions(_statements(full, role))
    assert not any(a.startswith(("secretsmanager:", "ses:", "sts:")) for a in actions)
    assert _grants(full, role, "dynamodb:Scan") == []


def test_no_other_role_may_read_the_secrets_send_mail_or_assume_the_directory_role(
    full: Synth,
) -> None:
    for policy in full.props("AWS::IAM::Policy"):
        roles = json.dumps(policy["Roles"])
        if "BackendDigestSendRole" in roles or "BackendApiHandlerRole" in roles:
            continue
        for st in policy["PolicyDocument"]["Statement"]:
            actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            assert not any(
                a
                in ("secretsmanager:GetSecretValue", "ses:SendEmail", "sts:AssumeRole")
                for a in actions
            ), (roles, st)


@pytest.mark.parametrize(
    ("role", "writes", "reads"),
    [
        ("BackendDigestScheduleRole", ["USER#*"], ["DELIVERY_DUE"]),
        ("BackendDigestSendRole", ["USER#*"], ["PROFILE", "MODULES", "DIGEST"]),
        ("BackendDigestFeedbackRole", ["USER#*"], None),
    ],
)
def test_the_workers_touch_only_subscription_partitions(
    full: Synth, role: str, writes: list[str], reads: list[str] | None
) -> None:
    ops = [
        st
        for st in _statements(full, role)
        if st["Effect"] == "Allow" and "DataOpsTable" in json.dumps(st["Resource"])
    ]
    keys = {
        "dynamodb:PutItem" in st["Action"]: st["Condition"]["ForAllValues:StringLike"][
            "dynamodb:LeadingKeys"
        ]
        for st in ops
    }
    assert keys.get(True) == writes
    assert keys.get(False) == reads
    assert len(ops) == (2 if reads else 1)


def test_the_sender_reads_users_of_this_pool_only(full: Synth) -> None:
    (users,) = [
        st
        for st in _statements(full, "BackendDigestSendRole")
        if "cognito-idp:AdminGetUser" in st["Action"]
    ]
    assert sorted(users["Action"]) == sorted(digest.SENDER_USER_READS)
    assert "userpool/" in json.dumps(users["Resource"])
    assert "AuthUsers" in json.dumps(users["Resource"])
    for role in ("BackendDigestScheduleRole", "BackendDigestFeedbackRole"):
        assert not any(
            a.startswith("cognito-idp:") for a in _actions(_statements(full, role))
        )


def test_the_scheduler_cannot_read_the_graph(full: Synth) -> None:
    assert "DataGraphTable" not in json.dumps(
        _statements(full, "BackendDigestScheduleRole")
    )


# ---- SES identity and DNS ------------------------------------------------------------------------


def test_a_custom_domain_gets_an_identity_with_easy_dkim_and_dmarc_reject(full: Synth) -> None:
    (identity,) = full.props("AWS::SES::EmailIdentity")
    assert identity["EmailIdentity"] == "digest.example.com"
    assert "DkimSigningAttributes" not in identity  # Easy DKIM, the default
    records = full.props("AWS::Route53::RecordSet")
    digest_records = [
        r for r in records if r.get("HostedZoneId") == FULL["sesHostedZoneId"]
    ]
    cnames = [r for r in digest_records if r["Type"] == "CNAME"]
    assert len(cnames) == 3
    assert all("DkimDNSTokenName" in json.dumps(r["Name"]) for r in cnames)
    (dmarc,) = [r for r in digest_records if r["Type"] == "TXT"]
    assert dmarc["Name"] == "_dmarc.digest.example.com"
    assert dmarc["ResourceRecords"] == ['"v=DMARC1; p=reject;"']


def test_dmarc_is_written_only_when_asked(bundles: None) -> None:
    # A shared or apex domain may have its own DMARC record and other senders (scan cycle 3).
    s = _synth({**FULL, "sesDmarc": False})
    records = s.props("AWS::Route53::RecordSet")
    assert [
        r["Type"] for r in records if r.get("HostedZoneId") == FULL["sesHostedZoneId"]
    ] == ["CNAME"] * 3
    assert "sesDmarc" not in _parse({**FULL, "sesDmarc": False}).options()
    assert _parse(FULL).options()["sesDmarc"] is True


def test_directory_without_role_and_endpoint_waits_for_onboarding() -> None:
    pending = _parse({"slackMode": "bot", "dmResolver": "directory"})
    assert pending.dm_resolver == "directory"
    assert pending.directory_role_arn is None and pending.directory_endpoint is None


def test_an_existing_identity_is_used_as_it_is(mail_only: Synth) -> None:
    assert mail_only.resources("AWS::SES::EmailIdentity") == {}
    assert mail_only.resources("AWS::Route53::RecordSet") == {}
    (send,) = _grants(mail_only, "BackendDigestSendRole", "ses:SendEmail")
    assert IDENTITY in _resource(send)
    assert send["Condition"] == {
        "StringEquals": {"ses:FromAddress": "digest@example.com"}
    }
    # No Slack: the only secret read is the unsubscribe key, and there is no directory grant.
    (secrets,) = _grants(
        mail_only, "BackendDigestSendRole", "secretsmanager:GetSecretValue"
    )
    assert "UnsubscribeKey" in json.dumps(secrets["Resource"])
    assert not _grants(mail_only, "BackendDigestSendRole", "sts:AssumeRole")
    assert "DigestSlackSecretArn" not in mail_only.template.to_json()["Outputs"]


def test_a_domain_without_a_zone_gets_no_dns_records(webhook_mail: Synth) -> None:
    (identity,) = webhook_mail.props("AWS::SES::EmailIdentity")
    assert identity["EmailIdentity"] == "mail.example.org"
    assert webhook_mail.resources("AWS::Route53::RecordSet") == {}


def test_a_domain_under_the_stacks_own_zone_uses_that_zone(bundles: None) -> None:
    s = _synth(
        {
            "emailMode": "ses",
            "emailSender": "digest@mail.radar.example.com",
            "sesDomain": "mail.radar.example.com",
            "sesDmarc": True,
        },
        domainName="radar.example.com",
        parentZoneId="Z0123456789ABCDEFGHIJ",
    )
    (zone_id,) = [
        k
        for k, v in s.resources("AWS::Route53::HostedZone").items()
        if v["Properties"]["Name"] == "radar.example.com."
    ]
    (dmarc,) = [r for r in s.props("AWS::Route53::RecordSet") if r["Type"] == "TXT"]
    assert dmarc["HostedZoneId"] == {"Ref": zone_id}
    assert dmarc["Name"] == "_dmarc.mail.radar.example.com"


def test_dmarc_without_a_zone_is_refused(bundles: None) -> None:
    """Scan cycle 4: sesDmarc on a domain with no known hosted zone fails the synth rather than dropping it."""
    with pytest.raises(ValueError, match="sesDmarc needs a hosted zone"):
        build(
            digestDelivery=json.dumps(
                {
                    "emailMode": "ses",
                    "emailSender": "a@mail.example.org",
                    "sesDomain": "mail.example.org",
                    "sesDmarc": True,
                }
            )
        )


def test_an_identity_in_another_region_is_refused(bundles: None) -> None:
    with pytest.raises(ValueError, match="must be there"):
        build(
            digestDelivery=json.dumps(
                {
                    "emailMode": "ses",
                    "emailSender": "a@example.com",
                    "sesIdentityArn": "arn:aws:ses:eu-west-1:111122223333:identity/example.com",
                }
            )
        )


# ---- The public unsubscribe route ----------------------------------------------------------------


def test_only_unsubscribe_is_added_without_sign_in_and_it_has_its_own_throttle(
    full: Synth,
) -> None:
    assert _routes(full) == {
        "GET /api/config": "NONE",
        "GET /api/digest/unsubscribe": "NONE",
        "POST /api/digest/unsubscribe": "NONE",
        "ANY /api/{proxy+}": "JWT",
        "POST /api/graph/search": "JWT",
        "POST /api/graph/ask": "JWT",
        "POST /api/products/{id}/ask": "JWT",
    }
    (stage,) = full.props("AWS::ApiGatewayV2::Stage")
    for method in ("GET", "POST"):
        assert stage["RouteSettings"][f"{method} /api/digest/unsubscribe"] == {
            "ThrottlingRateLimit": digest.UNSUBSCRIBE_RATE,
            "ThrottlingBurstLimit": digest.UNSUBSCRIBE_BURST,
        }
    unauthenticated = {
        p["RouteKey"]
        for p in full.props("AWS::ApiGatewayV2::Route")
        if not p.get("AuthorizerId")
    }
    assert unauthenticated == {
        "GET /api/config",
        "GET /api/digest/unsubscribe",
        "POST /api/digest/unsubscribe",
    }


def test_digest_suppressions_are_per_resource() -> None:
    for rule, path, reason in nag.DIGEST_BY_PATH:
        assert path.startswith("Backend/") and path.count("/") >= 2, (rule, path)
        assert len(reason) > 60


# ---- Config --------------------------------------------------------------------------------------


def _parse(settings: Any) -> DigestDelivery:
    return DeployConfig.from_context(
        {"adminEmail": "a@example.com", "digestDelivery": settings}.get
    ).digest


def test_config_defaults_to_all_off() -> None:
    off = _parse(None)
    assert off == DigestDelivery() and not off.enabled and off.options() == {}
    assert _parse("") == off
    assert _parse({}) == off


def test_config_reads_a_json_string_or_an_object() -> None:
    from_text = _parse(json.dumps(FULL))
    assert from_text == _parse(FULL)
    assert from_text.enabled and from_text.email
    assert from_text.options() == FULL
    assert (
        _parse(
            {
                "emailMode": "ses",
                "emailSender": "A@Example.com",
                "sesDomain": "Example.COM",
            }
        ).ses_domain
        == "example.com"
    )
    sub = _parse(
        {
            "emailMode": "ses",
            "emailSender": "a@eu.example.com",
            "sesIdentityArn": IDENTITY,
        }
    )
    assert sub.ses_identity_arn == IDENTITY
    address = "arn:aws:ses:us-east-1:111122223333:identity/a@example.com"
    assert _parse(
        {"emailMode": "ses", "emailSender": "a@example.com", "sesIdentityArn": address}
    ).email


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ("{not json", "must be a JSON object"),
        ("[1]", "must be a JSON object"),
        (7, "must be a JSON object"),
        ({"slack": "bot"}, "does not take slack"),
        ({"slackMode": True}, "slackMode must be a string"),
        ({"slackMode": "on"}, "slackMode must be one of off, bot, webhook"),
        ({"dmResolver": "phone"}, "dmResolver must be one of"),
        ({"emailMode": "smtp"}, "emailMode must be one of off, ses"),
        ({"dmResolver": "email"}, "dmResolver needs slackMode=bot"),
        (
            {"slackMode": "webhook", "dmResolver": "email"},
            "dmResolver needs slackMode=bot",
        ),
        (
            {"slackMode": "bot", "directoryEndpoint": "https://x.example.com"},
            "need dmResolver=directory",
        ),
        (
            {
                "slackMode": "bot",
                "dmResolver": "directory",
                "directoryRoleArn": "arn:aws:iam::1:role/x",
            },
            "directoryRoleArn must be an IAM role ARN",
        ),
        (
            {
                "slackMode": "bot",
                "dmResolver": "directory",
                "directoryEndpoint": "http://x.example.com",
            },
            "directoryEndpoint must be an https URL",
        ),
        (
            {
                "slackMode": "bot",
                "dmResolver": "directory",
                "directoryEndpoint": "https://x.example.com",
            },
            "directoryRoleArn and directoryEndpoint go together",
        ),
        (
            {
                "slackMode": "bot",
                "dmResolver": "directory",
                "directoryRoleArn": "arn:aws:iam::111122223333:role/x",
            },
            "directoryRoleArn and directoryEndpoint go together",
        ),
        ({"sesDmarc": "yes"}, "sesDmarc must be true or false"),
        ({"sesDmarc": True}, "sesDmarc needs sesDomain"),
        ({"emailSender": "a@example.com"}, "need emailMode=ses"),
        ({"sesDomain": "example.com"}, "need emailMode=ses"),
        ({"emailMode": "ses", "sesDomain": "example.com"}, "needs emailSender"),
        (
            {"emailMode": "ses", "emailSender": "nope", "sesDomain": "example.com"},
            "emailSender must be an email address",
        ),
        ({"emailMode": "ses", "emailSender": "a@example.com"}, "exactly one of"),
        (
            {
                "emailMode": "ses",
                "emailSender": "a@example.com",
                "sesDomain": "example.com",
                "sesIdentityArn": IDENTITY,
            },
            "exactly one of",
        ),
        (
            {
                "emailMode": "ses",
                "emailSender": "a@example.com",
                "sesIdentityArn": IDENTITY,
                "sesHostedZoneId": "Z0123456789ABCDEFGHIJ",
            },
            "sesHostedZoneId needs sesDomain",
        ),
        (
            {
                "emailMode": "ses",
                "emailSender": "a@example.com",
                "sesDomain": "example.com",
                "sesHostedZoneId": "zone",
            },
            "sesHostedZoneId must be a Route 53 hosted zone id",
        ),
        (
            {"emailMode": "ses", "emailSender": "a@example", "sesDomain": "localhost"},
            "emailSender must be an email address",
        ),
        (
            {
                "emailMode": "ses",
                "emailSender": "a@b.localhost",
                "sesDomain": "localhost",
            },
            "sesDomain must be a DNS name",
        ),
        (
            {
                "emailMode": "ses",
                "emailSender": "a@example.com",
                "sesIdentityArn": "arn:aws:ses:x",
            },
            "sesIdentityArn must be an SES identity ARN",
        ),
        (
            {
                "emailMode": "ses",
                "emailSender": "a@other.com",
                "sesDomain": "example.com",
            },
            "emailSender must be an address on the SES identity",
        ),
        (
            {
                "emailMode": "ses",
                "emailSender": "a@notexample.com",
                "sesIdentityArn": IDENTITY,
            },
            "emailSender must be an address on the SES identity",
        ),
        (
            {
                "emailMode": "ses",
                "emailSender": "b@example.com",
                "sesIdentityArn": "arn:aws:ses:us-east-1:111122223333:identity/a@example.com",
            },
            "emailSender must be an address on the SES identity",
        ),
    ],
)
def test_config_refuses_bad_digest_settings(settings: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _parse(settings)


def test_the_signed_out_unsubscribe_page_is_served_by_the_spa_rule(
    default: Synth,
) -> None:
    """The web app's ``/unsubscribe?t=...`` page needs no CDN change: the viewer-request function
    serves ``index.html`` for every path without a file extension (the query string is kept)."""
    (fn,) = default.props("AWS::CloudFront::Function")
    assert frontend.SPA_RULE in fn["FunctionCode"]
    assert "indexOf('.') === -1" in frontend.SPA_RULE
    path = "/unsubscribe"
    assert "." not in path and not path.startswith("/api/")
    # Only /api/* goes to the API; the page is the default (site) behaviour.
    (dist,) = default.props("AWS::CloudFront::Distribution")
    assert [b["PathPattern"] for b in dist["DistributionConfig"]["CacheBehaviors"]] == [
        "/api/*"
    ]


def test_a_deployment_fault_is_alarmed_from_the_senders_log(full: Synth) -> None:
    """Scan cycle 6: a permanent fault on the deployment's side returns normally, so neither the function's
    Errors alarm nor the DLQ sees it; the sender's own log line is counted instead."""
    flt, key = (
        sorted(  # the sender's line, and the API's unusable-key line (scan cycle 43)
            (
                f
                for f in full.props("AWS::Logs::MetricFilter")
                if f["MetricTransformations"][0]["MetricName"]
                == "DigestDeploymentFaults"
            ),
            key=lambda f: "unsubscribe" in f["FilterPattern"],
        )
    )
    assert "unsubscribe.key_unusable" in key["FilterPattern"] and "Api" in json.dumps(
        key["LogGroupName"]
    )
    assert "digest.deployment_fault" in flt["FilterPattern"]
    assert "BackendDigestSend" in json.dumps(flt["LogGroupName"])
    (
        faults,
    ) = (  # c50: the advice reads the line's reason, not a repeat sparse unsubscribes may never bring
        a["AlarmDescription"]
        for a in full.props("AWS::CloudWatch::Alarm")
        if "because of a deployment setting" in a["AlarmDescription"]
    )
    assert (
        "line's reason is" in faults and "repeats" not in faults and len(faults) <= 1024
    )
    # c53, c54: no live check and no causal claims (each one tried had exceptions): the guide says what each code
    # means, and the alarm says only what the line means and where to look.
    assert (
        "Send test now" not in faults
        and "IAM settling" not in faults
        and "restore" not in faults
    )
    assert (
        "unsubscribe_not_configured" in faults
        and "docs/implementation-guide.md" in faults
    )  # c55: the path
    assert any(
        "because of a deployment setting" in a["AlarmDescription"]
        for a in full.props("AWS::CloudWatch::Alarm")
    )


def test_no_digest_fault_alarm_without_digest_delivery(default: Synth) -> None:
    assert not [
        f
        for f in default.props("AWS::Logs::MetricFilter")
        if f["MetricTransformations"][0]["MetricName"] == "DigestDeploymentFaults"
    ]


def test_lookup_misses_and_queue_faults_are_alarmed(full: Synth) -> None:
    """Scan cycle 9: several Slack users not found in a day, and queue or cursor writes that fail, are alarmed."""
    filters = {
        f["MetricTransformations"][0]["MetricName"]: f
        for f in full.props("AWS::Logs::MetricFilter")
        if f["MetricTransformations"][0]["MetricName"].startswith("Digest")
    }
    assert "digest.lookup_miss" in filters["DigestLookupMisses"]["FilterPattern"]
    queue = [
        f
        for f in full.props("AWS::Logs::MetricFilter")
        if f["MetricTransformations"][0]["MetricName"] == "DigestQueueFaults"
    ]
    patterns = " ".join(f["FilterPattern"] for f in queue)
    assert (
        len(queue) == 2
        and "digest.enqueue_failed" in patterns
        and "digest.advance_failed" in patterns
    )
    assert any("BackendDigestSchedule" in json.dumps(f["LogGroupName"]) for f in queue)
    alarms = {a["AlarmDescription"]: a for a in full.props("AWS::CloudWatch::Alarm")}
    (misses,) = [
        a
        for d, a in alarms.items()
        if d.startswith("A Slack user could not be found today")
    ]
    # Scan cycle 11: the first miss of a day alarms, so a broken lookup alarms on a deployment of any size.
    assert misses["Threshold"] == 1 and misses["Period"] == 86400
    assert (
        "digest.claim_failed" in patterns and "digest.defer_failed" in patterns
    )  # scan cycle 10
    assert (
        "digest.schedule_invalid" not in patterns
    )  # one owner's dropped zone is shown to them, not alarmed
    (queue_alarm,) = [
        a
        for d, a in alarms.items()
        if d.startswith("Digests could not be claimed or queued")
    ]
    assert queue_alarm["MetricName"] == "DigestQueueFaults"


def test_c60_the_key_alarm_text_and_outputs_follow_email(
    slack_only: Synth, mail_only: Synth
) -> None:
    def faults(synth: Synth) -> str:
        return str(
            next(
                a["AlarmDescription"]
                for a in synth.props("AWS::CloudWatch::Alarm")
                if "because of a deployment setting" in a["AlarmDescription"]
            )
        )

    assert "unsubscribe" not in faults(
        slack_only
    ) and "DigestUnsubscribeKeyArn" in faults(mail_only)
    outputs = mail_only.template.to_json()["Outputs"]
    assert (
        "DigestSlackSecretArn" not in outputs and "DigestUnsubscribeKeyArn" in outputs
    )
    dlq = next(
        a["AlarmDescription"]
        for a in mail_only.props("AWS::CloudWatch::Alarm")
        if "dead-letter" in a["AlarmDescription"]
    )
    assert "docs/implementation-guide.md" in dlq


def test_c61_a_secret_generation_replaces_that_secret_only(
    full: Synth, bundles: None
) -> None:
    """A secret deleted for good comes back by raising its generation: only its logical ID changes, so the
    next deploy creates it again and leaves the other secret, the SES identity and the queues alone."""
    raised = _synth(FULL, slackSecretGeneration="1")
    before = set(full.resources("AWS::SecretsManager::Secret"))
    after = set(raised.resources("AWS::SecretsManager::Secret"))
    assert len(before ^ after) == 2  # one ID out, one in
    assert [lid for lid in after - before if "SlackSecretG1" in lid]
    assert {lid for lid in before & after if "UnsubscribeKey" in lid}
    for kind in ("AWS::SES::EmailIdentity", "AWS::SQS::Queue", "AWS::Lambda::Function"):
        assert set(full.resources(kind)) == set(raised.resources(kind)), kind
    recorded = json.loads(
        raised.template.to_json()["Outputs"]["DeployOptions"]["Value"]
    )
    assert (
        recorded["slackSecretGeneration"] == "1"
        and recorded["unsubscribeKeyGeneration"] == "0"
    )
    # A top-level option, so no digestDelivery value carries or changes it (scan cycle 63).
    assert "Generation" not in recorded["digestDelivery"]


@pytest.mark.parametrize("bad", ["-1", "1000", "1.5", "01", "one", "\uff11", 1000])
def test_c61_a_secret_generation_is_a_small_whole_number(bad: Any) -> None:
    with pytest.raises(
        ValueError, match="unsubscribeKeyGeneration must be a whole number"
    ):
        DeployConfig.from_context(
            {"adminEmail": "a@example.com", "unsubscribeKeyGeneration": bad}.get
        )


def test_c63_a_generation_inside_digest_delivery_is_refused() -> None:
    """The generations left digestDelivery for top-level options: a value still carrying one is an unknown key."""
    with pytest.raises(
        ValueError,
        match="does not take unsubscribeKeyGeneration: it is a top-level option now",
    ):
        DigestDelivery.parse({**FULL, "unsubscribeKeyGeneration": 1})
    with pytest.raises(
        ValueError, match="does not take emailSendr either"
    ):  # every unknown key at once
        DigestDelivery.parse(
            {**FULL, "unsubscribeKeyGeneration": 1, "emailSendr": "x@example.com"}
        )


def test_c64_the_synthesized_secret_suppressions_are_the_matrix_rows(
    full: Synth,
) -> None:
    """The SMG4 reasons digest.py attaches to the two secrets are the ones the security matrix is checked against."""
    # Each secret by what it is, so swapping the two reasons fails (scan cycle 65).
    attached = {
        next(kind for kind in ("SlackSecret", "UnsubscribeKey") if kind in logical): [
            (rule["id"], rule["reason"])
            for rule in resource.get("Metadata", {})
            .get("cdk_nag", {})
            .get("rules_to_suppress", [])
        ]
        for logical, resource in full.resources("AWS::SecretsManager::Secret").items()
    }
    assert attached == {
        path.split("/")[2]: [(rule, reason)]
        for rule, path, reason in nag.DIGEST_SECRETS
    }


def test_c71_synth_takes_only_printable_ascii_addresses_and_digits() -> None:
    """config.py refuses what deploy.sh would refuse on carry-over, so synth never records it (c70, c71)."""
    with pytest.raises(ValueError, match="must be an email address in printable ASCII"):
        DigestDelivery.parse({**FULL, "emailSender": "zo\u00eb@example.com"})
    for context in (
        {"adminEmail": "zo\u00eb@example.com"},
        {"adminEmail": "a@example.com", "alarmEmail": "o\u200bps@example.com"},
    ):
        with pytest.raises(ValueError):
            DeployConfig.from_context(context.get)
    assert not config._OIDC_ISSUER.fullmatch(
        "https://idp.example.com:\u0664\u0664\u0663/x"
    )
    cert = "arn:aws:acm:us-east-1:{}:certificate/" + "0" * 36
    assert config._CERT.fullmatch(cert.format("1" * 12))
    assert not config._CERT.fullmatch(cert.format("\u0661" * 12))


def test_c63_purge_knows_the_digest_secrets_by_their_synthesized_descriptions(
    full: Synth,
) -> None:
    """purge.py finds retired digest secrets by words in their descriptions: each must be in the Description of
    exactly one synthesized secret, so a reworded description cannot silently hide a retired secret."""
    import ast

    tree = ast.parse((ROOT / "deployment" / "purge.py").read_text())
    fragments = next(
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", "") == "DIGEST_SECRETS"
    )
    descriptions = [
        str(p["Description"])
        for p in full.props("AWS::SecretsManager::Secret")
        if "Description" in p
    ]
    assert len(fragments) == 2
    for fragment in fragments:
        assert sum(fragment in d for d in descriptions) == 1, (fragment, descriptions)


def test_c61_a_single_channel_stack_reads_only_its_own_secret(
    slack_only: Synth, mail_only: Synth
) -> None:
    """Both secrets exist whenever digest delivery is on, but each function may read only the one its channel
    uses: an email-only stack never reads the Slack secret (which may hold a live token), and the reverse."""

    def read(synth: Synth) -> str:
        return json.dumps(
            [
                st["Resource"]
                for pol in synth.props("AWS::IAM::Policy")
                for st in pol["PolicyDocument"]["Statement"]
                if "secretsmanager:GetSecretValue" in json.dumps(st["Action"])
            ]
        )

    assert "SlackSecret" in read(slack_only) and "UnsubscribeKey" not in read(
        slack_only
    )
    assert "UnsubscribeKey" in read(mail_only) and "SlackSecret" not in read(mail_only)
